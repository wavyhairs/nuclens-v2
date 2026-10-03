"""TTS 출력 검증 — 만들어진 음성이 대본을 한 번씩 읽었는가 (audio_verify).

2026-10-03 전문가 브리핑: TTS 가 청크 하나를 두 번 읽어 해외 구간 1분 48초가
되풀이된 채 나갔다. 잘림 검사는 '너무 짧음'만 봐서 두 배 길이는 통과했다.
이 스위트는 그 종류의 사고를 (1) 유사도가 잡고 (2) 받아쓰기 대조가 잡고
(3) 잡힌 뒤 재생성→분할→잘라내기로 **오디오가 빠지지 않고** 끝나는지를 고정한다.
"""
import io
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

import audio_brief
import audio_verify
import expert_audio_brief as expert
import gemini_client
from gemini_client import GeminiError

RATE = 16000


def speech_like(seconds: float, seed: int, rate: int = RATE) -> bytes:
    """말소리를 흉내 낸 신호 — 0.25초마다 스펙트럼이 바뀌는 유성 구간.

    진짜 음성은 아니지만 유사도 검사가 보는 것(블록마다 다른 대역 분포)을 갖는다.
    같은 seed 는 같은 신호다 — '같은 문장을 다시 읽음'의 대역이다.
    """
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * rate), dtype=np.float32)
    t = np.arange(int(0.25 * rate)) / rate
    pos = 0
    while pos < len(out):
        f0 = rng.uniform(90, 220)
        amps = rng.uniform(0, 1, 12)
        seg = sum(a * np.sin(2 * np.pi * f0 * (k + 1) * t + rng.uniform(0, 6.28))
                  for k, a in enumerate(amps))
        seg += rng.normal(0, 0.3, len(t)) * rng.uniform(0.2, 1.0)
        seg = seg / (np.abs(seg).max() + 1e-9) * 0.4
        n = min(len(seg), len(out) - pos)
        out[pos:pos + n] = seg[:n]
        pos += n
    return (out * 32767).astype("<i2").tobytes()


def silence(seconds: float, rate: int = RATE) -> bytes:
    return b"\x00" * (int(seconds * rate) * 2)


class SelfSimilarityTests(unittest.TestCase):
    def test_whole_passage_read_twice_is_detected_at_the_right_lag(self):
        """10-03 모양: 20초를 읽고 처음부터 다시 읽음 → 시차 20초의 되풀이."""
        once = speech_like(20, seed=1)
        report = audio_verify.self_similarity(once + once, RATE)
        self.assertTrue(report["checked"])
        self.assertTrue(report["repeat"], report)
        self.assertGreaterEqual(report["max_score"], audio_verify.REPEAT_SCORE)
        seg = max(report["segments"], key=lambda s: s["end"] - s["start"])
        self.assertAlmostEqual(seg["lag"], 20.0, delta=0.6)
        self.assertLessEqual(seg["start"], 1.0)

    def test_distinct_passages_do_not_trigger(self):
        """서로 다른 40초: 정상 음성의 실측 최고 0.47 과 같은 쪽에 있어야 한다."""
        report = audio_verify.self_similarity(speech_like(40, seed=2), RATE)
        self.assertTrue(report["checked"])
        self.assertFalse(report["repeat"], report)
        self.assertLess(report["max_score"], audio_verify.REPEAT_SCORE)

    def test_repeat_far_from_its_first_reading_is_still_found(self):
        """'바로 앞'이 아니라 **모든 시차**를 본다 — 3꼭지 전과 겹쳐도 잡는다."""
        a = speech_like(12, seed=3)
        middle = speech_like(45, seed=4)
        report = audio_verify.self_similarity(a + middle + a, RATE)
        self.assertTrue(report["repeat"], report)
        seg = max(report["segments"], key=lambda s: s["end"] - s["start"])
        self.assertAlmostEqual(seg["lag"], 57.0, delta=0.6)

    def test_silence_against_silence_is_not_a_repeat(self):
        """시연 음성에서 몇 초짜리 완전 무음끼리 1.0 이 나왔다 — 무음은 비교에서 뺀다."""
        audio = (speech_like(8, seed=5) + silence(6) + speech_like(8, seed=6)
                 + silence(6) + speech_like(8, seed=7))
        report = audio_verify.self_similarity(audio, RATE)
        self.assertTrue(report["checked"])
        self.assertFalse(report["repeat"], report)

    def test_all_silent_or_too_short_input_is_checked_but_clean(self):
        self.assertFalse(audio_verify.self_similarity(silence(30), RATE)["repeat"])
        short = audio_verify.self_similarity(speech_like(3, seed=8), RATE)
        self.assertTrue(short["checked"])
        self.assertFalse(short["repeat"])

    def test_without_numpy_the_check_reports_unchecked_not_failure(self):
        with patch.object(audio_verify, "np", None):
            report = audio_verify.self_similarity(speech_like(5, seed=9), RATE)
        self.assertFalse(report["checked"])
        self.assertFalse(report["repeat"])

    def test_adjacent_segments_with_the_same_lag_merge(self):
        merged = audio_verify._merge_segments([
            {"start": 0.0, "end": 57.0, "lag": 108.4, "score": 0.96},
            {"start": 53.2, "end": 85.8, "lag": 108.4, "score": 0.99},
            {"start": 200.0, "end": 206.0, "lag": 30.0, "score": 0.9},
        ])
        self.assertEqual(2, len(merged))
        self.assertEqual((0.0, 85.8), (merged[0]["start"], merged[0]["end"]))


class TrimRepeatTests(unittest.TestCase):
    def test_whole_reread_keeps_only_the_first_pass(self):
        once = speech_like(20, seed=11)
        audio = once + once
        report = audio_verify.self_similarity(audio, RATE)
        trimmed = audio_verify.trim_repeat(audio, RATE, report)
        self.assertIsNotNone(trimmed)
        self.assertAlmostEqual(audio_verify.duration_sec(trimmed, RATE), 20.0, delta=0.6)

    def test_partial_repeat_removes_only_the_second_copy(self):
        a, b, c = speech_like(12, seed=12), speech_like(30, seed=13), speech_like(10, seed=14)
        audio = a + b + a + c          # a 가 42초 뒤에 다시 나온다
        report = audio_verify.self_similarity(audio, RATE)
        self.assertTrue(report["repeat"], report)
        trimmed = audio_verify.trim_repeat(audio, RATE, report)
        self.assertAlmostEqual(audio_verify.duration_sec(trimmed, RATE), 52.0, delta=1.5)
        # 첫 번째 a 와 c 의 꼬리는 그대로 남는다 (경계는 블록 단위라 ±0.5초 흔들린다)
        self.assertEqual(trimmed[: len(a)], a)
        tail = 5 * RATE * 2
        self.assertEqual(trimmed[-tail:], c[-tail:])

    def test_nothing_to_trim_returns_none(self):
        self.assertIsNone(audio_verify.trim_repeat(speech_like(5, seed=15), RATE,
                                                   {"segments": []}))


SCRIPT = "\n".join([
    "HOST: 한미 양국은 223억 달러 규모의 텍사스 가스복합발전소 건설을 확정했습니다.",
    "HOST: 세부 기술 운영 측면에서는 미국형 AP1000 과 한국형 APR1400 노형의 혼합 건설이 검토되고 있습니다.",
    "HOST: 다음 소식은 전략수출금융기금 법안 통과를 위한 정부의 움직임입니다. 상생기여금 요율을 낮추는 방안을 검토 중입니다.",
])


class CompareTranscriptTests(unittest.TestCase):
    def _flat(self, text: str) -> str:
        return " ".join(line.split(":", 1)[1].strip() for line in text.splitlines())

    def test_verbatim_transcript_is_clean(self):
        diff = audio_verify.compare_transcript(SCRIPT, self._flat(SCRIPT))
        self.assertEqual([], diff["repeated"])
        self.assertEqual([], diff["missing"])
        self.assertAlmostEqual(diff["ratio"], 1.0, delta=0.05)

    def test_a_sentence_heard_twice_is_a_repeat_wherever_it_sits(self):
        lines = SCRIPT.splitlines()
        transcript = self._flat("\n".join(lines + [lines[0]]))   # 첫 문장이 끝에서 또
        diff = audio_verify.compare_transcript(SCRIPT, transcript)
        self.assertEqual(1, len(diff["repeated"]))
        self.assertIn("텍사스", diff["repeated"][0])

    def test_a_sentence_never_heard_is_missing(self):
        transcript = self._flat("\n".join(SCRIPT.splitlines()[1:]))
        diff = audio_verify.compare_transcript(SCRIPT, transcript)
        self.assertEqual(1, len(diff["missing"]))
        self.assertIn("텍사스", diff["missing"][0])

    def test_spacing_punctuation_and_small_wording_drift_are_tolerated(self):
        """받아쓰기는 띄어쓰기·문장부호가 흔들린다 — 그걸로 누락이라 하면 매일 재생성이다."""
        drift = ("한미양국은 223억달러 규모의 텍사스 가스 복합 발전소 건설을 확정했습니다 "
                 "세부 기술운영 측면에서는 미국형 AP1000과 한국형 APR1400 노형의 혼합건설이 검토되고있습니다 "
                 "다음소식은 전략수출금융기금 법안통과를 위한 정부의 움직임입니다 상생기여금 요율을 낮추는 방안을 검토중입니다")
        diff = audio_verify.compare_transcript(SCRIPT, drift)
        self.assertEqual([], diff["missing"], diff)
        self.assertEqual([], diff["repeated"], diff)

    def test_latin_and_digits_read_in_hangul_are_not_missing(self):
        """TTS 는 'Nuclens' 를 '뉴클렌스' 로 읽고 받아쓰기는 들은 대로 적는다.

        10-04: 이 첫 문장이 빠른·전문가 모두 매 시도 '누락' 으로 찍혀 멀쩡한 첫 청크를
        재생성·분할·모델 전환까지 몰았고, TTS 요청이 필요분의 세 배가 됐다.
        """
        script = "HOST: 10월 4일 일요일 Nuclens 전문가 브리핑입니다.\n" + SCRIPT
        for heard in ("10월 4일 일요일 뉴클렌스 전문가 브리핑입니다.",
                      "시월 사일 일요일 누클렌즈 전문가 브리핑입니다."):
            diff = audio_verify.compare_transcript(script, heard + " " + self._flat(SCRIPT))
            self.assertEqual([], diff["missing"], heard)
            self.assertEqual([], diff["repeated"], heard)

    def test_a_latin_heavy_sentence_is_still_missing_when_never_heard(self):
        """영문을 빼고 보더라도 한글 부분이 안 들리면 여전히 누락이다."""
        script = "HOST: 10월 4일 일요일 Nuclens 전문가 브리핑입니다.\n" + SCRIPT
        diff = audio_verify.compare_transcript(script, self._flat(SCRIPT))
        self.assertEqual(1, len(diff["missing"]))
        self.assertIn("Nuclens", diff["missing"][0])

    def test_short_transition_sentences_are_not_judged(self):
        script = "HOST: 다음 소식입니다.\nHOST: 이어서 보겠습니다.\n" + SCRIPT
        diff = audio_verify.compare_transcript(script, self._flat(SCRIPT))
        self.assertEqual([], diff["missing"])


class TranscriptCheckTests(unittest.TestCase):
    def test_without_api_key_the_check_is_skipped_not_failed(self):
        with patch.object(gemini_client, "API_KEY", None):
            result = audio_verify.transcript_check(SCRIPT, speech_like(30, seed=20), RATE)
        self.assertFalse(result["checked"])
        self.assertTrue(result["ok"])
        self.assertEqual("no_api_key", result["error"])

    def test_call_failure_is_skipped_not_failed(self):
        def boom(*_a, **_kw):
            raise GeminiError("HTTP 503")
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "call_json", boom):
            result = audio_verify.transcript_check(SCRIPT, speech_like(30, seed=21), RATE)
        self.assertFalse(result["checked"])
        self.assertTrue(result["ok"])
        self.assertIn("503", result["error"])

    def test_transcriber_that_silently_deduplicates_is_distrusted(self):
        """10-03 청크: 음성 217초, 대본 860자. 받아쓰기가 되풀이를 한 번만 적으면
        글은 대본과 같고 길이만 두 배다 — 그 결과를 통과로 보면 안 된다."""
        flat = " ".join(l.split(":", 1)[1] for l in SCRIPT.splitlines())
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "call_json", lambda *a, **k: {"transcript": flat}):
            # 대본 ~130자에 음성 60초 → 2자/초. 평소 7자/초.
            result = audio_verify.transcript_check(SCRIPT, speech_like(60, seed=22), RATE)
        self.assertFalse(result["ok"])
        self.assertEqual("untrusted", result["error"])

    def test_a_normal_speaking_rate_is_trusted(self):
        """10-04 정상 청크는 받아쓴 글 4.7~5.0자/초였다(141초 678자 등). 5.0 문턱은
        그 청크들을 '불신' 으로 찍어 매일 재생성으로 몰았다."""
        flat = " ".join(l.split(":", 1)[1] for l in SCRIPT.splitlines())
        seconds = len(audio_verify.normalize(flat)) / 4.7
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "call_json", lambda *a, **k: {"transcript": flat}):
            result = audio_verify.transcript_check(SCRIPT, speech_like(seconds, seed=24), RATE)
        self.assertNotEqual("untrusted", result["error"], result)
        self.assertTrue(result["ok"], result)

    def test_transcribe_sends_audio_inline_without_thinking(self):
        seen = {}

        def fake(system, user, **kw):
            seen.update(kw)
            return {"transcript": "들린 대로"}
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "call_json", fake):
            text = audio_verify.transcribe(speech_like(2, seed=23), RATE)
        self.assertEqual("들린 대로", text)
        self.assertEqual("audio/wav", seen["inline_data"][0])
        self.assertTrue(seen["inline_data"][1].startswith(b"RIFF"))
        self.assertEqual(0, seen["thinking_budget"])
        self.assertEqual(audio_verify.TRANSCRIBE_LABEL, seen["label"])


class VerifyChunkTests(unittest.TestCase):
    def test_either_detector_alone_fails_the_chunk(self):
        once = speech_like(15, seed=30)
        with patch.object(gemini_client, "API_KEY", None):
            by_similarity = audio_verify.verify_chunk(1, SCRIPT, once + once, RATE)
        self.assertFalse(by_similarity["ok"])
        self.assertIn("유사도", by_similarity["reason"])

        flat = " ".join(l.split(":", 1)[1] for l in SCRIPT.splitlines())
        twice = flat + " " + flat.split(".")[0] + "."
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "call_json", lambda *a, **k: {"transcript": twice}):
            by_transcript = audio_verify.verify_chunk(1, SCRIPT, speech_like(18, seed=31), RATE)
        self.assertFalse(by_transcript["ok"])
        self.assertIn("되풀이", by_transcript["reason"])

    def test_clean_chunk_passes_and_records_both_detectors(self):
        flat = " ".join(l.split(":", 1)[1] for l in SCRIPT.splitlines())
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "call_json", lambda *a, **k: {"transcript": flat}):
            report = audio_verify.verify_chunk(2, SCRIPT, speech_like(18, seed=32), RATE)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["similarity"]["checked"])
        self.assertTrue(report["transcript"]["checked"])
        summary = audio_verify.summarize([report])
        self.assertEqual(0, summary["rejected"])
        self.assertEqual(2, summary["chunks"][0]["index"])


class VerifiedChunkFlowTests(unittest.TestCase):
    """검증 실패 뒤의 순서: 같은 모델 재생성 → 분할 → 잘라내기. 오디오는 안 빠진다."""

    CHUNK = "\n".join([f"HOST: {'가' * 120} {i}." for i in range(4)])

    def setUp(self):
        self._orig = audio_brief.verify_tts_chunk
        self.addCleanup(setattr, audio_brief, "verify_tts_chunk", self._orig)
        self.synth_calls: list[str] = []

    def _synth(self, text):
        self.synth_calls.append(text)
        return b"\x00\x40" * 2000, 24000

    def _verdicts(self, outcomes):
        """outcomes: 검증 호출 순서대로 True/False (또는 dict 로 유사도 구간 지정)."""
        queue = list(outcomes)

        def fake(index, chunk, pcm, rate):
            verdict = queue.pop(0) if queue else True
            if isinstance(verdict, dict):
                return {"index": index, "ok": False, "reason": "유사도 되풀이",
                        "similarity": verdict, "transcript": {"checked": False}}
            return {"index": index, "ok": bool(verdict),
                    "reason": "" if verdict else "되풀이 1문장",
                    "similarity": {"checked": True, "segments": []},
                    "transcript": {"checked": False}}
        audio_brief.verify_tts_chunk = fake

    def test_first_pass_ok_costs_one_request(self):
        self._verdicts([True])
        reports: list[dict] = []
        audio_brief.synthesize_verified_chunk(1, self.CHUNK, self._synth, reports=reports)
        self.assertEqual(1, len(self.synth_calls))
        self.assertEqual([1], [r["attempt"] for r in reports])

    def test_failure_regenerates_once_with_the_same_input(self):
        self._verdicts([False, True])
        audio_brief.synthesize_verified_chunk(1, self.CHUNK, self._synth)
        self.assertEqual([self.CHUNK, self.CHUNK], self.synth_calls)

    def test_two_failures_split_the_chunk_in_halves(self):
        self._verdicts([False, False, True, True])
        reports: list[dict] = []
        pcm, rate = audio_brief.synthesize_verified_chunk(
            1, self.CHUNK, self._synth, reports=reports)
        self.assertEqual(4, len(self.synth_calls))
        halves = self.synth_calls[2:]
        self.assertEqual(self.CHUNK.splitlines(), (halves[0] + "\n" + halves[1]).splitlines())
        self.assertEqual(["split1", "split2"], [r["attempt"] for r in reports[2:]])
        # 반쪽 사이 간격이 들어간다
        self.assertGreater(len(pcm), 2 * len(audio_brief.trim_silence(b"\x00\x40" * 2000, 24000)))

    def test_unsplittable_chunk_falls_back_to_trimming_the_repeat(self):
        once = speech_like(10, seed=40)
        audio = once + once

        def synth(text):
            self.synth_calls.append(text)
            return audio, RATE
        single_line = "HOST: " + "가" * 300
        report = audio_verify.self_similarity(audio, RATE)
        self.assertTrue(report["repeat"])
        self._verdicts([report, report])
        reports: list[dict] = []
        pcm, rate = audio_brief.synthesize_verified_chunk(
            1, single_line, synth, reports=reports)
        self.assertEqual(2, len(self.synth_calls))      # 분할 불가 → 재생성 2회뿐
        self.assertAlmostEqual(audio_verify.duration_sec(pcm, rate), 10.0, delta=0.6)
        self.assertEqual("trimmed", reports[-1]["attempt"])

    def test_nothing_left_to_try_raises_so_the_next_model_runs(self):
        self._verdicts([False] * 6)
        with self.assertRaises(GeminiError):
            audio_brief.synthesize_verified_chunk(1, "HOST: " + "가" * 300, self._synth)

    def test_verification_can_be_switched_off_by_env(self):
        with patch.dict("os.environ", {"AUDIO_OUTPUT_VERIFY": "off"}):
            report = audio_brief.verify_tts_chunk(1, self.CHUNK, b"\x00\x40" * 10, 24000)
        self.assertTrue(report["ok"])
        self.assertTrue(report.get("disabled"))


class ExpertIntegrationTests(unittest.TestCase):
    def test_expert_synthesis_regenerates_a_rejected_chunk_and_records_it(self):
        original = (expert._tts_models, expert._tts_chunk_retry, expert.trim_silence,
                    audio_brief.verify_tts_chunk, audio_brief._tts_models)
        calls: list[int] = []
        verdicts = {2: [False, True]}
        try:
            expert._tts_models = lambda: ["m1"]
            audio_brief._tts_models = lambda: ["m1"]

            def fake_chunk(index, chunk, model):
                calls.append(index)
                return b"\x00\x40" * 100, 24000

            def fake_verify(index, chunk, pcm, rate):
                ok = verdicts.get(index, [True]).pop(0) if verdicts.get(index) else True
                return {"index": index, "ok": ok, "reason": "" if ok else "되풀이",
                        "similarity": {"checked": True, "segments": []},
                        "transcript": {"checked": False}}
            expert._tts_chunk_retry = fake_chunk
            expert.trim_silence = lambda pcm, rate: pcm
            audio_brief.verify_tts_chunk = fake_verify
            script = "\n".join([f"HOST: {'가' * 850}{i}" for i in range(3)])
            pcm, rate, models, warnings = expert.synthesize_expert(script)
            self.assertEqual([1, 2, 2, 3], calls)
            summary = audio_brief.LAST_OUTPUT_VERIFICATION
            self.assertEqual(1, summary["regenerated"])
            self.assertFalse(summary["fallback_model_used"])
            self.assertTrue(any("출력 검증" in w for w in warnings))
        finally:
            (expert._tts_models, expert._tts_chunk_retry, expert.trim_silence,
             audio_brief.verify_tts_chunk, audio_brief._tts_models) = original

    def test_fallback_model_is_recorded_in_the_summary(self):
        summary = audio_brief.output_verification_summary(
            [], {"checked": True, "repeat": False}, models=[audio_brief.TTS_MODELS[1]])
        self.assertTrue(summary["fallback_model_used"])


class InlineDataTests(unittest.TestCase):
    """call_json 이 음성을 inlineData 로 싣고, capture 에는 바이트를 남기지 않는다."""

    def _ok(self):
        payload = {"candidates": [{"content": {"parts": [{"text": '{"transcript": "x"}'}]}}],
                   "usageMetadata": {}}
        stream = io.BytesIO(json.dumps(payload).encode("utf-8"))
        stream.__enter__ = lambda self=stream: self
        stream.__exit__ = lambda *a: False
        return stream

    def test_inline_data_goes_first_in_the_user_parts(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["body"] = json.loads(request.data)
            return self._ok()
        with patch.object(gemini_client, "API_KEY", "k"), \
                patch.object(gemini_client, "_pace", lambda _m: None), \
                patch.object(gemini_client.urllib.request, "urlopen", fake_urlopen):
            gemini_client.call_json("sys", "msg", model="m", label="t",
                                    inline_data=("audio/wav", b"RIFF1234"))
        parts = seen["body"]["contents"][0]["parts"]
        self.assertEqual("audio/wav", parts[0]["inlineData"]["mimeType"])
        self.assertEqual("UklGRjEyMzQ=", parts[0]["inlineData"]["data"])
        self.assertEqual({"text": "msg"}, parts[1])

    def test_capture_keeps_only_the_size_of_inline_bytes(self):
        body = {"contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": "audio/wav", "data": "A" * 1000}},
            {"text": "msg"}]}]}
        cleaned = gemini_client._without_inline_bytes(body)
        self.assertIn("1000", cleaned["contents"][0]["parts"][0]["inlineData"]["data"])
        self.assertEqual({"text": "msg"}, cleaned["contents"][0]["parts"][1])
        self.assertEqual("A" * 1000, body["contents"][0]["parts"][0]["inlineData"]["data"])


class PrimaryModelRetryTests(unittest.TestCase):
    """2.5 폴백은 10-03 되풀이가 난 경로 — 기본 모델의 과부하에는 한 단 더 기다린다."""

    def setUp(self):
        self._orig = (audio_brief.urllib.request.urlopen, audio_brief.time.sleep,
                      audio_brief._tts_backoff_spent, audio_brief._tts_failures,
                      audio_brief._tts_quota_exhausted, audio_brief.TTS_FAILURE_BUDGET)
        self.slept = []
        audio_brief.time.sleep = self.slept.append
        audio_brief._tts_backoff_spent = 0.0
        audio_brief._tts_failures = {}
        audio_brief._tts_quota_exhausted = False
        audio_brief.TTS_FAILURE_BUDGET = 10
        self.addCleanup(self._restore)

    def _restore(self):
        (audio_brief.urllib.request.urlopen, audio_brief.time.sleep,
         audio_brief._tts_backoff_spent, audio_brief._tts_failures,
         audio_brief._tts_quota_exhausted, audio_brief.TTS_FAILURE_BUDGET) = self._orig

    def _install(self, outcomes):
        self.seen = []

        def fake_urlopen(request, timeout=None):
            self.seen.append(request.full_url)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            payload = {"candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "audio/L16;rate=24000",
                "data": audio_brief.base64.b64encode(b"\x00\x40" * 100).decode()}}]}}]}
            stream = io.BytesIO(json.dumps(payload).encode("utf-8"))
            stream.__enter__ = lambda self=stream: self
            stream.__exit__ = lambda *a: False
            return stream
        audio_brief.urllib.request.urlopen = fake_urlopen

    def _503(self):
        return audio_brief.urllib.error.HTTPError(
            "https://x", 503, "err", {}, io.BytesIO(b"high demand"))

    def test_primary_model_gets_three_attempts_before_fallback(self):
        primary = audio_brief._tts_models()[0]
        self._install([self._503(), self._503(), "ok"])
        audio_brief.call_tts("HOST: 안녕하세요", models=[primary, "m2"])
        self.assertEqual(3, len(self.seen))
        self.assertTrue(all(primary in url for url in self.seen))
        self.assertEqual(list(audio_brief.TTS_BACKOFF_LADDER[:2]), self.slept)

    def test_fallback_model_keeps_two_attempts(self):
        self._install([self._503(), self._503(), "ok"])
        audio_brief.call_tts("HOST: 안녕하세요", models=["m2", "m3"])
        self.assertIn("m3", self.seen[2])
        self.assertEqual(2, sum(1 for url in self.seen if "m2" in url))


if __name__ == "__main__":
    unittest.main()
