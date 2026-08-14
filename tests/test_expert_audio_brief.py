"""expert_audio_brief.py — NucBrief 알고리즘 이식 회귀 테스트."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import expert_audio_brief as expert
from gemini_client import GeminiError


def issue(i: int, score: float = 10) -> dict:
    return {
        "issue_id": f"i{i}",
        "title": f"이슈 {i}",
        "summary": f"이슈 {i} 사실 요약",
        "selection_score": score,
        "story_outlet_count": 1 + (i % 3),
        "story_tier1_count": 1 if i == 1 else 0,
        "related_articles": [{"hash": f"h{i}", "title_kr": f"기사 {i}", "summary": "사실"}],
    }


def briefing() -> dict:
    return {
        "date": "2026-08-14",
        "headline": "오늘의 핵심",
        "highlight_issues": [{"issue_id": "i1"}, {"issue_id": "i2"}],
        "issues": [{"issue_id": f"i{i}"} for i in range(1, 8)],
    }


class ExpertAudioAlgorithmTests(unittest.TestCase):
    def test_selects_highlights_then_rest_without_duplicates(self):
        by_id = {f"i{i}": issue(i) for i in range(1, 8)}
        rows = expert.selected_issues(briefing(), by_id)
        self.assertEqual([r["issue_id"] for r in rows], ["i1", "i2", "i3", "i4", "i5", "i6"])

    def test_time_allocation_is_bounded_and_near_target(self):
        rows = [issue(i, 36 if i == 1 else 8) for i in range(1, 7)]
        allocations = expert.allocate_seconds(briefing(), rows)
        total = sum(row["seconds"] for row in allocations)
        self.assertLessEqual(abs(total - expert.EXPERT_BODY_SECONDS), len(rows))
        self.assertTrue(all(30 <= row["seconds"] <= expert.EXPERT_MAX_ISSUE_SECONDS for row in allocations))
        self.assertGreater(allocations[0]["seconds"], allocations[-1]["seconds"])

    def test_single_speaker_normalizer_removes_dialogue_fillers(self):
        raw = "\n".join([
            "정책분석가: 네, 첫 번째 사실입니다.",
            "기술전문가: 그렇군요. 기술적으로는 제약이 있습니다.",
            "HOST: 맞습니다. 현재는 허가 단계입니다.",
            "ANALYST: 다음 확인점은 일정입니다.",
            "화자A: 수치는 입력자료 그대로입니다.",
            "HOST: 사업 단계는 착공과 다릅니다.",
            "HOST: 불확실성은 남아 있습니다.",
            "HOST: 후속 발표를 확인해야 합니다.",
        ])
        normalized, spoken = expert.normalize_script(raw)
        self.assertTrue(all(line.startswith("HOST: ") for line in normalized.splitlines()))
        self.assertNotIn("네,", normalized)
        self.assertNotIn("그렇군요", normalized)
        self.assertNotIn("맞습니다", normalized)
        self.assertGreater(spoken, 50)

    def test_verification_requires_all_thresholds_and_no_critical_claim(self):
        good = {
            "passed": True, "coverage_score": 95, "factual_support_score": 98,
            "stage_precision_score": 98, "expert_depth_score": 91,
            "single_speaker_score": 100, "unsupported_critical_claims": [],
        }
        self.assertTrue(expert.verification_passed(good))
        bad = dict(good, stage_precision_score=90)
        self.assertFalse(expert.verification_passed(bad))
        bad2 = dict(good, unsupported_critical_claims=[{"claim": "x"}])
        self.assertFalse(expert.verification_passed(bad2))

    def test_tts_early_model_switch_restarts_for_voice_consistency(self):
        original_models = expert._tts_models
        original_chunk = expert._tts_chunk_retry
        original_trim = expert.trim_silence
        calls = []
        try:
            expert._tts_models = lambda: ["m1", "m2"]
            def fake(index, chunk, model):
                calls.append((model, index))
                if model == "m1" and index == 2:
                    raise GeminiError("quota")
                return b"\x00\x40" * 100, 24000
            expert._tts_chunk_retry = fake
            expert.trim_silence = lambda pcm, rate: pcm
            script = "\n".join([f"HOST: {'가'*850}{i}" for i in range(4)])
            pcm, rate, models, warnings = expert.synthesize_expert(script)
            self.assertEqual(rate, 24000)
            self.assertEqual(models, ["m2", "m2", "m2", "m2"])
            self.assertIn(("m2", 1), calls)  # 처음부터 재생성
            self.assertTrue(any("전체 음색" in text for text in warnings))
            self.assertTrue(pcm)
        finally:
            expert._tts_models = original_models
            expert._tts_chunk_retry = original_chunk
            expert.trim_silence = original_trim

    def test_tts_late_model_switch_resumes_to_finish(self):
        original_models = expert._tts_models
        original_chunk = expert._tts_chunk_retry
        original_trim = expert.trim_silence
        calls = []
        try:
            expert._tts_models = lambda: ["m1", "m2"]
            def fake(index, chunk, model):
                calls.append((model, index))
                if model == "m1" and index == 4:
                    raise GeminiError("quota")
                return b"\x00\x40" * 100, 24000
            expert._tts_chunk_retry = fake
            expert.trim_silence = lambda pcm, rate: pcm
            script = "\n".join([f"HOST: {'가'*850}{i}" for i in range(5)])
            _, _, models, warnings = expert.synthesize_expert(script)
            self.assertEqual(models[:3], ["m1", "m1", "m1"])
            self.assertEqual(models[3:], ["m2", "m2"])
            self.assertNotIn(("m2", 1), calls)  # 후반은 정상 구간 보존
            self.assertTrue(any("후반" in text for text in warnings))
        finally:
            expert._tts_models = original_models
            expert._tts_chunk_retry = original_chunk
            expert.trim_silence = original_trim


if __name__ == "__main__":
    unittest.main()
