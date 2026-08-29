"""expert_audio_brief.py — NucBrief 알고리즘 이식 회귀 테스트."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import os

import article_quality_gate
import audio_brief
import channel_queue
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
        # 지역이 없으면 전부 해외 블록이라 순서가 그대로다. 하이라이트가 앞에 온다.
        self.assertEqual([r["issue_id"] for r in rows],
                         ["i1", "i2", "i3", "i4", "i5", "i6", "i7"])

    def test_heavy_news_day_keeps_every_issue(self):
        """🔴 상한 9 는 매일 국내 운영 이슈를 밖으로 밀어냈다.

        실측(2026-08-16): 브리핑 17개 중 9개만 오디오에 들어가 '새울 3호기
        시운전 중 자동정지' 같은 국내 운영 이슈가 탈락했다. 텔레그램으로 나간
        기사는 전부 오디오에도 들어간다 — 분량은 그만큼 늘어나면 된다.
        """
        by_id = {f"i{i}": issue(i) for i in range(1, 21)}
        heavy = dict(briefing(), issues=[{"issue_id": f"i{i}"} for i in range(1, 21)])
        rows = expert.selected_issues(heavy, by_id)
        self.assertEqual(20, len(rows))

    def test_domestic_runs_before_overseas_without_reordering_priority(self):
        """국내를 다 말한 뒤 해외로 넘어간다. 묶음 안 우선순위는 안 흔든다."""
        regions = {1: "해외", 2: "국내", 3: "해외", 4: "국내·해외", 5: "국내"}
        by_id = {f"i{i}": dict(issue(i), region=regions.get(i, "해외")) for i in range(1, 6)}
        rows = expert.selected_issues(
            dict(briefing(), highlight_issues=[{"issue_id": "i1"}],
                 issues=[{"issue_id": f"i{i}"} for i in range(1, 6)]), by_id)
        # 국내(i2,i5) → 국내·해외(i4) → 해외(i1,i3). i1 은 하이라이트라 해외 안에서 앞.
        self.assertEqual([r["issue_id"] for r in rows], ["i2", "i5", "i4", "i1", "i3"])

        blocks = expert.script_blocks(rows)
        self.assertEqual([name for name, _ in blocks], ["국내", "해외"])
        # '국내·해외' 는 국내 블록 끝 — 해외로 넘어가는 전환 지점이다.
        self.assertEqual([r["issue_id"] for r in blocks[0][1]], ["i2", "i5", "i4"])
        self.assertEqual([r["issue_id"] for r in blocks[1][1]], ["i1", "i3"])

    def test_script_batches_are_even_and_never_cross_regions(self):
        """5·5·1 로 끊으면 마지막 호출이 이슈 하나짜리 대본을 쓴다."""
        rows = [issue(i) for i in range(1, 12)]
        batches = expert.even_batches(rows, expert.SCRIPT_BATCH_ISSUES)
        self.assertEqual([len(b) for b in batches], [4, 4, 3])
        self.assertEqual(11, sum(len(b) for b in batches))
        # 순서 보존 — 배치가 랭킹을 흔들면 안 된다.
        self.assertEqual([r["issue_id"] for b in batches for r in b],
                         [f"i{i}" for i in range(1, 12)])

    def test_material_keeps_every_article_sent_today(self):
        """🔴 related[:5] 는 오늘 나간 기사를 빠뜨렸다.

        related 는 이슈의 이력이라 최신순이 아니다. 오늘 발송분이 6번째에 있으면
        오디오 재료에서 통째로 빠졌다 — '보낸 기사는 전부 넣는다'가 깨진다.
        """
        rows = [{"hash": f"old{i}", "briefing_date": "2026-08-10"} for i in range(6)]
        rows.append({"hash": "today1", "briefing_date": "2026-08-14"})
        rows.append({"hash": "today2", "briefing_date": "2026-08-14"})
        material = expert.issue_material(dict(issue(1), related_articles=rows), "2026-08-14")
        hashes = [a["hash"] for a in material["articles"]]
        self.assertIn("today1", hashes)
        self.assertIn("today2", hashes)
        # 과거 기사는 맥락용이라 상한이 있다.
        self.assertEqual(expert.MATERIAL_CONTEXT_ARTICLES,
                         sum(1 for h in hashes if h.startswith("old")))
        # 지역을 재료에 적어 준다 — 없으면 모델이 본문에서 짐작한다.
        self.assertIn("region", material)

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
            if label.startswith("expert_dossiers"):
                return {"dossiers": [{"issue_id": f"i{i}"} for i in range(1, 7)]}
            if label == "expert_plan":
                return {"segments": []}
            if label.startswith("expert_script_retry"):
                body = "가" * 700
                return {"script": "\n".join(f"HOST: {body}" for _ in range(8))}
            if label.startswith("expert_script"):
                # 1차: 문단 2개 — 하한에도 분량에도 미달
                return {"script": "HOST: 짧은 원고.\nHOST: 두 번째."}
            return {}

        original = expert._call_structured
        expert._call_structured = fake_call
        try:
            with self.assertRaises(Exception):
                # 검증 단계까지는 안 간다 — 여기서 보는 것은 재시도 도달 여부다.
                expert.generate_expert_script(briefing(), [issue(i) for i in range(1, 7)])
        finally:
            expert._call_structured = original

        self.assertTrue(any(label.startswith("expert_script_retry") for label in calls),
                        f"1차 원고가 형식 미달일 때 길이 재시도가 호출되지 않았다: {calls}")

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

    def _record_calls(self, issues: list[dict]) -> list[tuple[str, str]]:
        """generate_expert_script 를 돌리고 (label, message) 를 전부 모은다."""
        seen: list[tuple[str, str]] = []

        def fake_call(system, message, **kw):
            label = kw.get("label", "")
            seen.append((label, message))
            if label.startswith("expert_dossiers"):
                import re as _re
                ids = _re.findall(r'"issue_id": "(i\d+)"', message)
                return {"dossiers": [{"issue_id": i, "title": f"이슈 {i[1:]}",
                                      "body": "가" * 400} for i in dict.fromkeys(ids)]}
            if label == "expert_plan":
                return {"segments": []}
            if label.startswith("expert_script"):
                return {"script": "\n".join(f"HOST: {'가' * 150}" for _ in range(12))}
            if label.startswith("expert_verify"):
                return {"passed": True, "coverage_score": 99, "factual_support_score": 99,
                        "stage_precision_score": 99, "expert_depth_score": 99,
                        "single_speaker_score": 100, "unsupported_critical_claims": []}
            return {}

        original = expert._call_structured
        expert._call_structured = fake_call
        try:
            expert.generate_expert_script(briefing(), issues)
        finally:
            expert._call_structured = original
        return seen

    def test_more_issues_produce_more_script_calls(self):
        """🔴 뚜껑만 열면 분량이 안 따라온다 — 호출이 늘어야 분량이 는다.

        실측(2026-08-14): 재료를 5,768→8,070자로 늘려도 단일 호출 대본은
        2,461→2,480자로 그대로였다. 이슈 17개를 그 한 호출에 넣으면 같은
        2,500자에 눌려 담겨 이슈당 깊이는 오히려 얕아진다.
        """
        def script_calls(count: int) -> int:
            issues = [dict(issue(i), region="해외") for i in range(1, count + 1)]
            labels = [label for label, _ in self._record_calls(issues)]
            # 길이 재시도는 같은 배치의 두 번째 호출이라 세지 않는다.
            return sum(1 for label in labels
                       if label.startswith("expert_script")
                       and not label.startswith("expert_script_retry"))

        few, many = script_calls(4), script_calls(17)
        self.assertEqual(1, few)          # 4개는 한 호출로 충분하다
        self.assertGreater(many, few)
        self.assertEqual(4, many)         # 17 → 5·4·4·4

    def test_a_script_call_never_sees_the_other_region(self):
        """🔴 국내 설명에 해외 사실이 섞이던 경로를 구조로 막는다.

        재료에 region 이 없어서 모델이 기사 본문으로 지역을 짐작했다. 이제 배치가
        지역 경계를 넘지 않으므로, 한 호출의 프롬프트에는 다른 지역 story 가
        애초에 들어 있지 않다.
        """
        issues = ([dict(issue(i), region="국내", title=f"국내이슈{i}") for i in (1, 2)]
                  + [dict(issue(i), region="해외", title=f"해외이슈{i}") for i in (3, 4)])
        calls = self._record_calls(expert.selected_issues(
            dict(briefing(), highlight_issues=[],
                 issues=[{"issue_id": f"i{i}"} for i in range(1, 5)]),
            {f"i{i}": row for i, row in zip(range(1, 5), issues)}))
        script_calls = [(label, message) for label, message in calls
                        if label.startswith("expert_script")
                        and not label.startswith("expert_script_retry")]
        self.assertEqual(2, len(script_calls))          # 국내 1 + 해외 1
        domestic = next(m for label, m in script_calls if "국내" in label)
        overseas = next(m for label, m in script_calls if "해외" in label)
        self.assertIn("i1", domestic)
        self.assertNotIn("i3", domestic)
        self.assertIn("i3", overseas)
        self.assertNotIn("i1", overseas)

    def test_bridge_line_marks_the_domestic_to_overseas_handover(self):
        issues = ([dict(issue(1), region="국내")] + [dict(issue(2), region="해외")])
        calls = self._record_calls(issues)
        self.assertTrue(calls)
        # 전환 문장은 시스템이 붙인다 — plan 프롬프트가 그렇게 지시한다.
        plan_message = next(m for label, m in calls if label == "expert_plan")
        self.assertIn("전환 문장은", plan_message)
        self.assertIn("해외", expert._block_bridge("해외"))
        self.assertEqual("", expert._block_bridge("국내"))

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
        """후반 전환은 정상 구간을 보존하고 이어 붙인다 — 음색보다 완주가 위다.

        전 구간 한 모델로 통일해 봤는데(2026-08-16), 마지막 청크에서 실패할 때마다
        앞 전부를 다시 만들어야 했다. TTS 한계는 RPD 라 재생성분이 그대로 그날
        예산에서 빠지고, 대본이 길어진 뒤로는 청크가 6~8개라 재생성 한 번이
        예산을 두 배로 먹는다. 음색 차이는 warnings 로 남긴다.
        """
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

    def test_tts_retry_does_not_sleep_because_the_limit_is_daily(self):
        """RPD 는 기다린다고 회복되지 않는다 — 재시도에 백오프를 두지 않는다."""
        original_call = expert.call_tts
        original_check = expert._check_not_truncated
        attempts: list[str] = []
        try:
            def fake_call(chunk, models=None):
                attempts.append(models[0])
                if len(attempts) < 2:
                    raise GeminiError("HTTP 429")
                return b"\x00\x40" * 100, 24000
            expert.call_tts = fake_call
            expert._check_not_truncated = lambda *a, **k: None
            _pcm, rate = expert._tts_chunk_retry(1, "가" * 100, "m1")
            self.assertEqual(24000, rate)
            self.assertEqual(["m1", "m1"], attempts)  # 같은 모델로 한 번 더
            self.assertEqual(2, expert.EXPERT_TTS_RETRIES)
            self.assertFalse(hasattr(expert, "EXPERT_TTS_BACKOFF_SEC"))
        finally:
            expert.call_tts = original_call
            expert._check_not_truncated = original_check


class ExpertTelegramDeliveryTests(unittest.TestCase):
    """전문가 브리핑의 텔레그램 발송 계약.

    이 경로는 통째로 없었다 — 전문가 브리핑은 mp3 를 만들어 웹에만 올리고
    끝났고, 구독자가 받는 건 3분짜리 빠른 브리핑 하나뿐이었다. 빠른 브리핑과
    같은 계약(생성 후 발송 / 발송 실패 시 다음 실행이 회수 / --no-send 는
    재발송 안 함)을 전문가 쪽에도 건다.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        audio_dir = base / "data" / "audio"
        audio_dir.mkdir(parents=True)
        # _audio_manifest·_write_audio_variant 는 audio_brief 의 전역 AUDIO_DIR 을
        # 본다. expert 는 같은 이름을 자기 네임스페이스로 import 했으므로 두 곳을
        # 함께 갈지 않으면 매니페스트만 진짜 web/public 으로 새어 나간다.
        self._orig_dirs = (expert.AUDIO_DIR, expert.WEB_DATA,
                           audio_brief.AUDIO_DIR, audio_brief.WEB_DATA)
        expert.AUDIO_DIR = audio_brief.AUDIO_DIR = audio_dir
        expert.WEB_DATA = audio_brief.WEB_DATA = base / "data"
        # 발송 성공은 채널 배치 적재로 이어진다 — 저장소 루트의 진짜 큐 파일에
        # 테스트 픽스처가 쌓이지 않게 여기도 함께 돌린다.
        self._orig_queue = channel_queue.QUEUE_FILE
        channel_queue.QUEUE_FILE = base / "channel_outbox.json"
        self._orig_fns = (expert.is_available, expert.load_briefing,
                          expert.selected_issues, expert.generate_expert_script,
                          expert.synthesize_expert, expert.to_mp3,
                          expert.send_telegram_audio)
        self.addCleanup(self._restore)

        self.sent = []
        self.send_ok = True
        self.script_calls = 0
        self.tts_calls = 0

        expert.is_available = lambda: True
        expert.load_briefing = lambda web_data: (briefing(), {})
        expert.selected_issues = lambda brief, by_id: [issue(i) for i in range(1, 4)]
        expert.generate_expert_script = self._fake_script
        expert.synthesize_expert = self._fake_tts
        expert.to_mp3 = self._fake_mp3
        # 빠른 브리핑 스위트와 같은 이유 — 호스트의 진짜 ffmpeg 에 기대지 않는다.
        self._orig_ffmpeg = expert.ffmpeg_available
        self.addCleanup(setattr, expert, "ffmpeg_available", self._orig_ffmpeg)
        expert.ffmpeg_available = lambda: True
        expert.send_telegram_audio = self._fake_send

    def _restore(self):
        (expert.AUDIO_DIR, expert.WEB_DATA,
         audio_brief.AUDIO_DIR, audio_brief.WEB_DATA) = self._orig_dirs
        channel_queue.QUEUE_FILE = self._orig_queue
        (expert.is_available, expert.load_briefing,
         expert.selected_issues, expert.generate_expert_script,
         expert.synthesize_expert, expert.to_mp3,
         expert.send_telegram_audio) = self._orig_fns

    def _fake_script(self, brief, issues, contracts=None):
        self.script_calls += 1
        script = "\n".join(f"HOST: 문단 {i} 의 해설입니다." for i in range(1, 8))
        return script, dossiers_of(4000, 3), {"segments": []}, {"passed": True}

    def _fake_tts(self, script):
        self.tts_calls += 1
        return b"\x00\x40" * 24000 * 10, 24000, ["tts-a"], []

    def _fake_mp3(self, pcm, rate, path, bitrate="128k"):
        path.write_bytes(b"mp3")

    def _fake_send(self, mp3_path, meta):
        # 빠른 브리핑과 같은 계약 — 성공은 file_id dict, 실패는 None.
        self.sent.append((mp3_path.name, dict(meta)))
        return {"file_id": "tg-expert-id", "message_id": 2} if self.send_ok else None

    def _manifest(self):
        return json.loads((expert.AUDIO_DIR / "audio.json").read_text(encoding="utf-8"))

    def _cache_digest(self):
        rows = expert.selected_issues(briefing(), {})
        return expert.audio_evidence_digest(
            briefing(), expert.evidence_contracts(briefing(), rows),
            expert.EXPERT_VARIANT)

    def _seed(self, *, trusted=True, script="HOST: 저장된 대본입니다.", **extra):
        """이미 오늘치 전문가 mp3 가 있는 상태. trusted=False 면 지문 없는 옛 캐시."""
        (expert.AUDIO_DIR / "briefing-expert-2026-08-14.mp3").write_bytes(b"mp3")
        meta = {
            "date": "2026-08-14", "key": expert.EXPERT_VARIANT,
            "label": "전문가 브리핑", "file": "briefing-expert-2026-08-14.mp3",
            "duration_sec": 600, **extra,
        }
        if trusted:
            (expert.AUDIO_DIR / "script-expert-2026-08-14.txt").write_text(
                script, encoding="utf-8")
            meta.update({
                "evidence_digest": self._cache_digest(),
                "script_digest": article_quality_gate.script_digest(script),
                "gate_version": article_quality_gate.NARRATIVE_GATE_VERSION,
            })
        audio_brief._write_audio_variant("2026-08-14", expert.EXPERT_VARIANT, meta)

    def test_generate_sends_expert_audio_and_marks_meta(self):
        self.assertTrue(expert.generate())
        self.assertEqual(1, len(self.sent))
        self.assertEqual("briefing-expert-2026-08-14.mp3", self.sent[0][0])
        # 캡션이 두 브리핑을 구분할 수 있게 label 이 meta 에 실려 나간다.
        self.assertEqual("전문가 브리핑", self.sent[0][1]["label"])
        variant = self._manifest()["variants"][expert.EXPERT_VARIANT]
        self.assertIn("telegram_sent_at", variant)

    def test_no_send_generates_without_delivering(self):
        """품질 재생성(--force --no-send)은 채널에 같은 회차를 두 번 올리지 않는다."""
        self.assertTrue(expert.generate(send=False))
        self.assertEqual([], self.sent)
        self.assertNotIn("telegram_sent_at",
                         self._manifest()["variants"][expert.EXPERT_VARIANT])

    def test_send_failure_leaves_meta_unmarked_so_next_run_retries(self):
        self.send_ok = False
        self.assertTrue(expert.generate())  # 발송 실패가 생성 성공을 뒤집지는 않는다
        self.assertNotIn("telegram_sent_at",
                         self._manifest()["variants"][expert.EXPERT_VARIANT])

    def test_skip_path_recovers_generated_but_unsent_audio(self):
        """생성 뒤 발송 전에 죽은 실행을 다음 실행이 10분 TTS 없이 회수한다."""
        self._seed()
        self.assertTrue(expert.generate())
        self.assertEqual(1, len(self.sent))
        self.assertEqual(0, self.tts_calls)     # TTS 재호출 0
        self.assertEqual(0, self.script_calls)  # 대본 재생성 0
        self.assertIn("telegram_sent_at",
                      self._manifest()["variants"][expert.EXPERT_VARIANT])

    def test_already_sent_is_not_resent(self):
        self._seed(telegram_sent_at="2026-08-14T07:30:00+09:00")
        self.assertTrue(expert.generate())
        self.assertEqual([], self.sent)
        self.assertEqual(0, self.tts_calls)

    def test_legacy_cache_without_digest_is_regenerated(self):
        """지문 없는 캐시는 그 음원이 오늘 재료로 만들어졌다는 증거가 없다."""
        self._seed(trusted=False)
        self.assertTrue(expert.generate())
        self.assertEqual(1, self.script_calls)   # 다시 만들었다
        variant = self._manifest()["variants"][expert.EXPERT_VARIANT]
        self.assertEqual(self._cache_digest(), variant["evidence_digest"])

    def test_stale_cache_already_sent_is_not_resent(self):
        """이미 나간 회차는 재검증 때문에 두 번 나가지 않는다."""
        self._seed(trusted=False, telegram_sent_at="2026-08-14T07:30:00+09:00")
        self.assertTrue(expert.generate())
        self.assertEqual([], self.sent)
        self.assertEqual(0, self.script_calls)
        self.assertEqual(0, self.tts_calls)
        variant = self._manifest()["variants"][expert.EXPERT_VARIANT]
        self.assertEqual("stale_after_send", variant["cache_state"])

    def test_different_issue_set_invalidates_cache(self):
        """다른 기사 조합으로 만든 MP3 를 오늘 회차로 재사용하지 않는다."""
        self._seed()
        expert.selected_issues = lambda brief, by_id: [issue(i) for i in range(1, 6)]
        self.assertTrue(expert.generate())
        self.assertEqual(1, self.script_calls)


class TelegramCaptionTests(unittest.TestCase):
    """두 브리핑이 같은 채널에 연달아 도착한다 — 캡션만으로 구분돼야 한다."""

    def _capture(self, meta):
        """send_telegram_audio 가 텔레그램에 실제로 싣는 payload."""
        import requests
        captured = {}

        class _Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}

        def fake_post(url, data=None, files=None, timeout=None):
            captured.update(data)
            return _Response()

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp3 = Path(tmp.name) / "briefing.mp3"
        mp3.write_bytes(b"mp3")
        orig_post, orig_resolve = requests.post, audio_brief.gemini_client._resolve
        requests.post = fake_post
        audio_brief.gemini_client._resolve = lambda key, default=None: {
            "TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat",
            "SITE_URL": "https://nuclens-v2.pages.dev",
        }.get(key, default)
        try:
            self.assertTrue(audio_brief.send_telegram_audio(mp3, meta))
        finally:
            requests.post = orig_post
            audio_brief.gemini_client._resolve = orig_resolve
        return captured

    def test_caption_and_title_name_the_variant(self):
        fast = self._capture({"date": "2026-08-14", "label": "빠른 브리핑",
                              "description": "3분 요약", "duration_sec": 170})
        expert_payload = self._capture({"date": "2026-08-14", "label": "전문가 브리핑",
                                        "description": "10분 해설", "duration_sec": 600})
        self.assertIn("빠른 브리핑", fast["caption"])
        self.assertIn("2분 50초", fast["caption"])
        self.assertIn("전문가 브리핑", expert_payload["caption"])
        self.assertIn("10분 00초", expert_payload["caption"])
        self.assertNotEqual(fast["title"], expert_payload["title"])

    def test_caption_links_to_the_deployed_site_not_the_v1_domain(self):
        payload = self._capture({"date": "2026-08-14", "label": "빠른 브리핑",
                                 "duration_sec": 170})
        self.assertIn("nuclens-v2.pages.dev", payload["caption"])

    def test_legacy_meta_without_label_still_produces_a_clean_caption(self):
        """v1 매니페스트로 재발송이 돌아도 캡션에 'None' 이 찍히면 안 된다."""
        payload = self._capture({"date": "2026-08-14", "duration_sec": 165})
        self.assertNotIn("None", payload["caption"])
        self.assertIn("오디오 브리핑", payload["caption"])


class ModelBucketRoutingTests(unittest.TestCase):
    """단계별 모델 배치 회귀 방지 (2026-08 재배치).

    dossier·verify는 호출 수가 많은 대조·추출이라 'curation'(기본 3.1) 을
    우선하고, plan·script·repair·reorder는 문장 품질이 청취자에게 그대로
    들리는 소수 호출이라 'synthesis'(기본 3.5) 를 우선한다. 재배치 전에는
    `_call_structured` 전체가 curation 을 우선했다 — 폴백 사다리가 있어서
    기능은 그대로 돌아가지만, 전문가 오디오 대본까지 3.1 로 나가면서
    issue_review·issue_insight 만 3.5 였다. 이 테스트가 없으면 다음 리팩터가
    `primary=` 인자를 빼먹어도 조용히 통과한다.
    """

    def setUp(self):
        self._orig_model = expert.gemini_client.MODEL
        expert.gemini_client.MODEL = "curation-model"
        self._orig_env = os.environ.get("GEMINI_SYNTHESIS_MODEL")
        os.environ.pop("GEMINI_SYNTHESIS_MODEL", None)

    def tearDown(self):
        expert.gemini_client.MODEL = self._orig_model
        if self._orig_env is not None:
            os.environ["GEMINI_SYNTHESIS_MODEL"] = self._orig_env
        else:
            os.environ.pop("GEMINI_SYNTHESIS_MODEL", None)

    def test_model_ladder_orders_by_primary_bucket(self):
        self.assertEqual(expert._model_ladder("curation"),
                         ["curation-model", "gemini-3.5-flash-lite"])
        self.assertEqual(expert._model_ladder("synthesis"),
                         ["gemini-3.5-flash-lite", "curation-model"])

    def test_generate_expert_script_sends_each_stage_to_its_bucket(self):
        seen: dict[str, str] = {}
        prefixes = ("expert_dossiers", "expert_plan", "expert_script_retry",
                    "expert_script", "expert_verify_after_repair",
                    "expert_verify", "expert_repair", "expert_reorder")

        def fake_call_json(system, message, **kw):
            label = kw["label"]
            prefix = next((p for p in prefixes if label.startswith(p)), label)
            seen[prefix] = kw["model"]
            if prefix == "expert_dossiers":
                import re as _re
                ids = _re.findall(r'"issue_id": "(i\d+)"', message)
                return {"dossiers": [{"issue_id": i, "title": f"이슈 {i[1:]}",
                                      "body": "가" * 400} for i in dict.fromkeys(ids)]}
            if prefix == "expert_plan":
                return {"segments": []}
            if prefix.startswith("expert_script"):
                return {"script": "\n".join(f"HOST: {'가' * 150}" for _ in range(12))}
            if prefix.startswith("expert_verify"):
                return {"passed": True, "coverage_score": 99, "factual_support_score": 99,
                        "stage_precision_score": 99, "expert_depth_score": 99,
                        "single_speaker_score": 100, "unsupported_critical_claims": []}
            return {}

        original = expert.call_json
        expert.call_json = fake_call_json
        try:
            expert.generate_expert_script(briefing(), [issue(i) for i in range(1, 7)])
        finally:
            expert.call_json = original

        # 항상 도는 스테이지 — curation 대량 버킷
        self.assertEqual(seen["expert_dossiers"], "curation-model")
        self.assertEqual(seen["expert_verify"], "curation-model")
        # 항상 도는 스테이지 — synthesis 품질 버킷
        self.assertEqual(seen["expert_plan"], "gemini-3.5-flash-lite")
        self.assertEqual(seen["expert_script"], "gemini-3.5-flash-lite")
        # 조건부 스테이지 — 이번 실행에서 돌았다면 마찬가지로 검증
        for optional in ("expert_script_retry", "expert_repair", "expert_reorder"):
            if optional in seen:
                self.assertEqual(seen[optional], "gemini-3.5-flash-lite", optional)
        if "expert_verify_after_repair" in seen:
            self.assertEqual(seen["expert_verify_after_repair"], "curation-model")


if __name__ == "__main__":
    unittest.main()
