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


def dossiers_of(volume: int, count: int = 6) -> list[dict]:
    """직렬화 길이가 `volume`자 안팎이 되는 dossier 목록.

    분량 목표가 재료 크기에서 나오므로, 테스트도 크기를 눈대중이 아니라
    실제 직렬화 길이로 맞춘다.
    """
    import json as _json
    rows = [{"issue_id": f"i{i}", "body": ""} for i in range(1, count + 1)]
    pad = max(1, (volume - len(_json.dumps(rows, ensure_ascii=False))) // count)
    for row in rows:
        row["body"] = "가" * pad
    return rows


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
        # 상한(EXPERT_MAX_ISSUES)은 뚜껑이지 목표가 아니다 — 그날 있는 만큼 간다.
        # 브리핑에 이슈가 7개뿐이면 7개 전부, 하이라이트가 앞에 온다.
        self.assertEqual([r["issue_id"] for r in rows],
                         ["i1", "i2", "i3", "i4", "i5", "i6", "i7"])

    def test_issue_cap_still_bounds_a_heavy_news_day(self):
        by_id = {f"i{i}": issue(i) for i in range(1, 21)}
        heavy = dict(briefing(), issues=[{"issue_id": f"i{i}"} for i in range(1, 21)])
        rows = expert.selected_issues(heavy, by_id)
        self.assertEqual(expert.EXPERT_MAX_ISSUES, len(rows))

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

    def test_paragraph_floor_is_reachable_for_the_issue_count_we_pick(self):
        """🔴 하한이 고정 8이면 이 파이프라인은 산술적으로 통과할 수 없다.

        selected_issues 는 EXPERT_MAX_ISSUES(6)까지만 뽑고 모델은 이슈당 한 문단 +
        종합 한 문단을 쓴다 — 자연스러운 최소가 7이다. 실측(2026-08-14, 2회 재현):
        이슈 6개 → 7문단 → 매번 '형식 미달'로 전문가 브리핑이 통째로 실패했다.
        이슈가 적은 날일수록 더 못 넘으므로 하한은 이슈 수를 따라가야 한다.
        """
        self.assertEqual(7, expert.min_paragraphs(6))
        self.assertEqual(4, expert.min_paragraphs(3))
        # 이슈가 1개뿐인 날에도 바닥이 3 밑으로 내려가지는 않는다.
        self.assertEqual(3, expert.min_paragraphs(1))

        seven = "\n".join(f"HOST: 문단 {i} 의 내용입니다." for i in range(1, 8))
        normalized, spoken = expert.normalize_script(seven, 6)
        self.assertEqual(7, len(normalized.splitlines()))
        self.assertGreater(spoken, 0)

    def test_short_draft_reaches_the_length_retry_instead_of_dying(self):
        """🔴 재시도가 필요한 바로 그 원고에서 재시도가 도달 불가였다.

        normalize_script 가 493행에서 raise 하면 494행의 길이 재시도는 영영 안
        돈다. 짧은 원고는 문단도 적어 두 실패가 항상 같이 오므로, '짧으면 다시
        쓰게 한다'는 장치가 필요한 순간에 정확히 죽어 있었다.
        """
        calls = []

        def fake_call(system, message, **kw):
            label = kw.get("label", "")
            calls.append(label)
            if label == "expert_dossiers":
                return {"dossiers": [{"issue_id": f"i{i}"} for i in range(1, 7)]}
            if label == "expert_plan":
                return {"segments": []}
            if label == "expert_script":
                # 1차: 문단 2개 — 하한(7)에도 분량에도 미달
                return {"script": "HOST: 짧은 원고.\nHOST: 두 번째."}
            if label == "expert_script_length_retry":
                body = "가" * 700
                return {"script": "\n".join(f"HOST: {body}" for _ in range(8))}
            return {}

        original = expert._call_structured
        expert._call_structured = fake_call
        try:
            with self.assertRaises(Exception):
                # 검증 단계까지는 안 간다 — 여기서 보는 것은 재시도 도달 여부다.
                expert.generate_expert_script(briefing(), [issue(i) for i in range(1, 7)])
        finally:
            expert._call_structured = original

        self.assertIn("expert_script_length_retry", calls,
                      "1차 원고가 형식 미달일 때 길이 재시도가 호출되지 않았다")

    def test_length_target_follows_the_material_not_a_fixed_ten_minutes(self):
        """🔴 고정 목표는 재료가 얇은 날 '지어내야만 통과'를 요구한다.

        실측(2026-08-14): dossiers 5,768자에 고정 목표 3,600자를 걸어 2회 연속
        미달로 전문가 브리핑이 아예 안 나왔다. 모델이 게으른 게 아니라
        confirmed_facts 밖을 못 쓰는 제약과 목표가 서로 모순이었다.

        기사가 적은 날은 짧게, 많은 날은 10분을 넘겨도 길게 — 길이를 정하는 것은
        시계가 아니라 그날 뉴스다.
        """
        thin = dossiers_of(2000, count=2)
        rich = dossiers_of(14000, count=9)

        thin_target = expert.spoken_target(thin)
        rich_target = expert.spoken_target(rich)
        self.assertLess(thin_target, rich_target)

        # 재료가 많은 날은 10분(약 4,050자)을 넘어설 수 있어야 한다.
        self.assertGreater(rich_target, 4050)
        # 절대 한계는 지킨다 — 너무 짧으면 전문가 브리핑이 아니고,
        # 너무 길면 TTS·생성 시간이 워크플로 예산을 넘는다.
        self.assertGreaterEqual(thin_target, expert.SPOKEN_ABS_MIN)
        self.assertLessEqual(rich_target, expert.SPOKEN_ABS_MAX)

        target, low, high = expert.spoken_bounds(rich)
        self.assertLess(low, target)
        self.assertGreater(high, target)
        # 시간 배분도 같은 목표에서 나온다 — 본문 초를 상수로 두면 plan 이
        # 채울 수 없는 시간을 요구하거나 10분에서 잘린다.
        self.assertGreater(expert.body_seconds(rich_target),
                           expert.body_seconds(thin_target))

    def test_under_target_script_is_kept_not_thrown_away(self):
        """🔴 목표 미달로 멀쩡한 대본을 버리면 안 된다.

        실측(2026-08-14): 재료를 5,768→8,070자로 늘려도 대본은 2,461→2,480자로
        사실상 그대로였다. 단일 호출에서 길이는 재료가 아니라 모델이 정하고
        재시도로도 안 움직인다. 그 상태에서 목표를 게이트로 쓰면 17문단짜리
        정상 대본이 18자 모자라다는 이유로 통째로 버려진다.

        게이트는 쓰레기를 막는 자리다 — 실패는 절대 한계에서만 난다.
        """
        calls = []

        def fake_call(system, message, **kw):
            label = kw.get("label", "")
            calls.append(label)
            if label == "expert_dossiers":
                return {"dossiers": [{"issue_id": f"i{i}", "body": "가" * 900}
                                     for i in range(1, 7)]}
            if label == "expert_plan":
                return {"segments": []}
            if label.startswith("expert_script") or label == "expert_repair":
                # 목표엔 못 미치지만 절대 한계는 넘는 정상 대본
                return {"script": "\n".join(f"HOST: {'가' * 150}" for _ in range(16))}
            if label.startswith("expert_verify"):
                return {"passed": True, "coverage_score": 99,
                        "factual_support_score": 99, "stage_precision_score": 99,
                        "expert_depth_score": 99, "single_speaker_score": 100,
                        "unsupported_critical_claims": []}
            return {}

        original = expert._call_structured
        expert._call_structured = fake_call
        try:
            script, dossiers, plan, report = expert.generate_expert_script(
                briefing(), [issue(i) for i in range(1, 7)])
        finally:
            expert._call_structured = original

        self.assertTrue(script, "목표 미달이라는 이유로 대본이 버려졌다")
        self.assertIn("HOST:", script)

    def test_absolute_floor_still_rejects_garbage(self):
        """반대로, 절대 한계 밑은 여전히 실패해야 한다 — 게이트를 없앤 게 아니다."""
        def fake_call(system, message, **kw):
            label = kw.get("label", "")
            if label == "expert_dossiers":
                return {"dossiers": [{"issue_id": f"i{i}"} for i in range(1, 7)]}
            if label == "expert_plan":
                return {"segments": []}
            if label.startswith("expert_script"):
                return {"script": "\n".join(f"HOST: 짧음 {i}." for i in range(9))}
            return {}

        original = expert._call_structured
        expert._call_structured = fake_call
        try:
            with self.assertRaises(ValueError) as ctx:
                expert.generate_expert_script(briefing(), [issue(i) for i in range(1, 7)])
        finally:
            expert._call_structured = original
        self.assertIn("절대한계", str(ctx.exception))

    def test_2026_08_14_material_now_passes(self):
        """그날 실제로 나온 2,461자가 통과하는지 — 실측을 상수로 박아 둔다."""
        dossiers = dossiers_of(5768, count=6)
        target, low, high = expert.spoken_bounds(dossiers)
        self.assertLessEqual(low, 2461, f"하한 {low} 이 실측 2,461자를 또 떨어뜨린다")
        self.assertGreaterEqual(high, 2461)

    def test_script_prompt_asks_for_multiple_paragraphs_per_segment(self):
        """프롬프트가 '2~5문장 문단'과 목표 분량을 동시에 요구하면서 세그먼트당
        문단 수를 안 알려 주면, 모델은 이슈당 한 문단으로 끝낸다."""
        dossiers = [{"issue_id": f"i{i}"} for i in range(1, 7)]
        text = expert.script_prompt(briefing(), dossiers, {"segments": []})
        self.assertIn("한 문단으로 끝내지 마십시오", text)
        self.assertRegex(text, r"세그먼트 6개")

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
