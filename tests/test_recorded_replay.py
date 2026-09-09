"""capture → replay 왕복이 실제로 성립하는지, 그리고 **틀렸을 때 틀렸다고 하는지**.

두 번째가 더 중요하다. 항상 PROVEN 을 돌려주는 fidelity 검사기는 없는 것만 못하다 —
검증했다는 착각만 남기고 과거의 실패를 그대로 반복하게 한다. 그래서 이 파일은
긍정 경로마다 부정 경로를 함께 붙인다.
"""

import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import gemini_client
import llm_policy
import news_bot
from tools import recorded_replay

ARTICLES = [
    {"hash": "a1b2c3d4" + "0" * 56, "title": "신한울 3호기 종합시운전 착수",
     "description": "한수원이 종합시운전에 착수했다.", "link": "https://example.gov.kr/1",
     "domain": "example.gov.kr", "publisher": "산업통상자원부"},
    {"hash": "e5f6a7b8" + "1" * 56, "title": "SMR 표준설계 심의 지연",
     "description": "심의가 다음 분기로 미뤄졌다.", "link": "https://news.example.com/2",
     "domain": "news.example.com", "publisher": "에너지신문"},
]


def _payload(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5,
                              "thoughtsTokenCount": 0, "totalTokenCount": 15}}


class _Recording:
    """응답을 돌려주면서 production 의 capture 훅이 실제로 기록하게 둔다."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def __call__(self, req, *args, **kwargs):
        self.calls += 1
        return recorded_replay._RecordedResponse(
            json.dumps(_payload(self.text)).encode("utf-8"))


class RoundTripTests(unittest.TestCase):
    """녹화한 그대로 다시 돌리면 요청 열이 글자까지 같아야 한다."""

    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self._key = gemini_client.API_KEY
        self._capture_dir = gemini_client._CAPTURE_DIR
        gemini_client.API_KEY = "test-key"
        gemini_client.reset_call_log()
        self.tmp = Path(self.enterContext(TemporaryDirectory()))
        self.addCleanup(self._restore)

    def _restore(self):
        urllib.request.urlopen = self._urlopen
        gemini_client.API_KEY = self._key
        gemini_client._CAPTURE_DIR = self._capture_dir
        gemini_client.reset_call_log()

    def _record(self, response_text: str = '{"items": []}') -> list[dict]:
        """production 경로를 그대로 태워 실제 capture 파일을 만든다."""
        capture_dir = self.tmp / "capture"
        gemini_client._CAPTURE_DIR = str(capture_dir)
        urllib.request.urlopen = _Recording(response_text)
        news_bot.curate_batch(ARTICLES, [], log_path=self.tmp / "delivery_log.jsonl")
        urllib.request.urlopen = self._urlopen
        gemini_client._CAPTURE_DIR = None
        return recorded_replay.load_capture(capture_dir / "llm_capture.jsonl")

    def _driver(self, articles=None):
        return recorded_replay.curation_driver(
            articles if articles is not None else ARTICLES, [], None, self.tmp)

    # ── 긍정 ────────────────────────────────────────────────────────────────
    def test_recording_captures_the_regeneration_round_trip(self):
        # 빈 items 는 품질 게이트에서 걸려 재생성을 부른다. 즉 orchestration 이
        # 두 번 호출하는 경로가 녹화에 들어간다 — 한 번짜리로는 검증이 얕다.
        records = self._record()
        self.assertEqual(len(records), 2)
        labels = [row["detail"]["task"] for row in records]
        self.assertEqual(labels, ["curation", "curation:재생성"])
        regen = records[1]["request_body"]["system_instruction"]["parts"][0]["text"]
        self.assertIn("[재생성]", regen)

    def test_replay_reproduces_the_recorded_request_sequence(self):
        records = self._record()
        report = recorded_replay.verify(records, self._driver())
        self.assertEqual(report["status"], recorded_replay.PROVEN, report["mismatches"])
        self.assertEqual(report["replayed_calls"], 2)
        self.assertEqual(report["unreplayed_recorded_calls"], 0)
        self.assertEqual(report["extra_replay_calls"], 0)
        # transport 를 우회한 호출이 있었다면 여기서 어긋난다.
        self.assertEqual(report["call_log_delta"], report["replayed_calls"])

    # ── 부정 ────────────────────────────────────────────────────────────────
    def test_wrong_input_reconstruction_is_caught_with_a_field_path(self):
        # replay 를 돌리려면 그때의 입력을 재구성해야 한다. 재구성이 틀리면
        # 프롬프트가 달라지고, 그것이 곧 fidelity 실패여야 한다.
        records = self._record()
        wrong = [dict(ARTICLES[0], title="다른 제목"), dict(ARTICLES[1])]
        report = recorded_replay.verify(records, self._driver(wrong))
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        self.assertTrue(report["mismatches"])
        paths = " ".join(report["mismatches"][0]["differences"])
        self.assertIn("contents", paths)

    def test_a_shorter_recording_is_not_silently_accepted(self):
        records = self._record()[:1]
        report = recorded_replay.verify(records, self._driver())
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        self.assertEqual(report["extra_replay_calls"], 1)

    def test_unreplayed_recorded_calls_fail_the_gate(self):
        records = self._record()
        report = recorded_replay.verify(
            records, lambda: None)  # orchestration 을 아예 돌리지 않은 경우
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        self.assertEqual(report["unreplayed_recorded_calls"], 2)

    def test_reasoning_is_the_only_axis_ignore_thinking_may_hide(self):
        """reasoning arm 대조에서 thinkingConfig 만 예외로 둔다.

        P4 에서 baseline 녹화와 candidate arm 을 대조할 때 쓴다. 이 예외가 다른
        축까지 덮으면 "reasoning 만 바꿨다"는 주장이 검증 없이 통과한다.
        """
        records = self._record()
        profiles = llm_policy._PROFILES
        patched = dict(profiles)
        patched["curation"] = llm_policy.TaskProfile(
            task=profiles["curation"].task,
            model_resolver=profiles["curation"].model_resolver,
            thinking_level="medium")
        llm_policy._PROFILES = patched
        self.addCleanup(lambda: setattr(llm_policy, "_PROFILES", profiles))

        strict = recorded_replay.verify(records, self._driver())
        self.assertEqual(strict["status"], recorded_replay.NOT_PROVEN)
        self.assertIn("thinkingConfig",
                      " ".join(strict["mismatches"][0]["differences"]))

        lenient = recorded_replay.verify(records, self._driver(),
                                         ignore_thinking=True)
        self.assertEqual(lenient["status"], recorded_replay.PROVEN,
                         lenient["mismatches"])

        # 예외는 thinkingConfig 에만 걸린다 — 입력이 틀리면 여전히 잡혀야 한다.
        wrong = [dict(ARTICLES[0], title="다른 제목"), dict(ARTICLES[1])]
        still = recorded_replay.verify(records, self._driver(wrong),
                                       ignore_thinking=True)
        self.assertEqual(still["status"], recorded_replay.NOT_PROVEN)


class CaptureSelectionTests(unittest.TestCase):
    def test_regeneration_belongs_to_the_same_callsite(self):
        # 재생성을 다른 callsite 로 세면 "호출이 몇 번 늘었나"가 보이지 않는다.
        records = [{"seq": 0, "detail": {"task": "curation"}},
                   {"seq": 1, "detail": {"task": "curation:재생성"}},
                   {"seq": 2, "detail": {"task": "dedup"}}]
        self.assertEqual(len(recorded_replay.select(records, "curation")), 2)
        self.assertEqual(len(recorded_replay.select(records, "dedup")), 1)

    def test_unknown_callsite_is_refused(self):
        with self.assertRaises(ValueError):
            recorded_replay.select([], "not_a_callsite")


class DedupRoundTripTests(unittest.TestCase):
    """dedup 은 실패가 fail-open 이라 replay 가 조용히 통과하기 쉽다."""

    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self._key = gemini_client.API_KEY
        self._capture_dir = gemini_client._CAPTURE_DIR
        gemini_client.API_KEY = "test-key"
        gemini_client.reset_call_log()
        self.tmp = Path(self.enterContext(TemporaryDirectory()))
        self.addCleanup(self._restore)

    def _restore(self):
        urllib.request.urlopen = self._urlopen
        gemini_client.API_KEY = self._key
        gemini_client._CAPTURE_DIR = self._capture_dir
        gemini_client.reset_call_log()

    def _record(self, response_text: str) -> list[dict]:
        import dedup

        gemini_client._CAPTURE_DIR = str(self.tmp / "capture")
        urllib.request.urlopen = _Recording(response_text)
        dedup.dedup_articles(list(ARTICLES),
                             {a["hash"]: 1.0 for a in ARTICLES})
        urllib.request.urlopen = self._urlopen
        gemini_client._CAPTURE_DIR = None
        return recorded_replay.load_capture(self.tmp / "capture" / "llm_capture.jsonl")

    def test_dedup_grouping_round_trip_is_proven(self):
        grouped = json.dumps({"groups": [{"indices": [0, 1], "relation": "merge",
                                          "story_title": "s"}]}, ensure_ascii=False)
        records = self._record(grouped)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["detail"]["task"], "dedup")
        report = recorded_replay.verify(
            records,
            recorded_replay.dedup_driver(
                ARTICLES, {a["hash"]: 1.0 for a in ARTICLES}, stage="dedup"))
        self.assertEqual(report["status"], recorded_replay.PROVEN, report["mismatches"])
        # 병합이 실제로 일어났는지까지 본다. fail-open 으로 전량 유지된 것을
        # "재현 성공"이라 부르면 dedup 검증은 아무것도 검증하지 않는 것이 된다.
        kept, dropped = report["production_result"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_stage_mismatch_is_caught_by_the_system_prompt(self):
        # 같은 두 기사라도 dedup 과 dedup_final 은 다른 프롬프트다. stage 를 잘못
        # 재구성하면 fidelity 가 실패해야 한다.
        records = self._record(json.dumps({"groups": [{"indices": [0]},
                                                      {"indices": [1]}]}))
        report = recorded_replay.verify(
            records,
            recorded_replay.dedup_driver(
                ARTICLES, {a["hash"]: 1.0 for a in ARTICLES}, stage="dedup_final"))
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        self.assertIn("system_instruction",
                      " ".join(report["mismatches"][0]["differences"]))

if __name__ == "__main__":
    unittest.main()
