"""전문가 오디오 — 기사 설명 순서가 텔레그램 발송 순서와 같은가.

사용자 요구 (2026-08-17): "텔레그램 기사 목록을 화면으로 보면서 오디오를 듣는
경우가 많으므로, 오디오 본문에서 기사를 설명하는 순서는 텔레그램에 실제 발송된
기사 번호와 일치해야 한다."
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import expert_audio_brief as expert


def issue(issue_id, title, region, brief_rank=None, *, score=10.0, tags=None,
          fingerprint=None):
    return {
        "issue_id": issue_id,
        "title": title,
        "summary": f"{title} 관련 사실 요약",
        "region": region,
        "brief_region": region if brief_rank else "",
        "brief_rank": brief_rank,
        "selection_score": score,
        "tags": tags or [],
        "story_fingerprint": fingerprint or {},
        "related_articles": [{"hash": issue_id, "title_kr": title}],
    }


def briefing(issues, highlights=()):
    return {
        "date": "2026-08-17",
        "headline": "오늘의 핵심",
        "highlight_issues": [{"issue_id": i} for i in highlights],
        "issues": [{"issue_id": i["issue_id"]} for i in issues],
    }


class BriefOrderTests(unittest.TestCase):
    """웹 정렬이 아니라 텔레그램 번호가 순서를 정한다."""

    def setUp(self):
        # 웹은 국내·해외를 맞물려 늘어놓고 점수로 다시 줄 세운다. 텔레그램은
        # 국내 목록 → 해외 목록이고 번호는 랭킹(다양성 반영)이 정했다.
        self.rows = [
            issue("d1", "신규 원전 2기 영덕 선정", "국내", 1, score=27.7),
            issue("o1", "X-에너지 텍사스 SMR 자금 확보", "해외", 1, score=26.1),
            issue("d2", "테라파워-한국 기업 SMR 협력", "국내", 2, score=29.3),
            issue("o2", "미 에너지부 디아블로 캐년 지원", "해외", 2, score=25.1),
            issue("d3", "제12차 전력수급기본계획 토론회", "국내", 3, score=25.0),
        ]
        self.by_id = {r["issue_id"]: r for r in self.rows}

    def test_orders_by_telegram_rank_not_web_order(self):
        rows = expert.selected_issues(
            briefing(self.rows, highlights=["d1", "o1", "d2"]), self.by_id)
        self.assertEqual([r["issue_id"] for r in rows],
                         ["d1", "d2", "d3", "o1", "o2"])

    def test_highlights_no_longer_jump_the_queue(self):
        """하이라이트는 웹 정렬의 상위 3건이지 텔레그램 1·2·3번이 아니다."""
        rows = expert.selected_issues(
            briefing(self.rows, highlights=["o2", "d3"]), self.by_id)
        self.assertEqual([r["issue_id"] for r in rows],
                         ["d1", "d2", "d3", "o1", "o2"])

    def test_domestic_block_comes_first(self):
        rows = expert.selected_issues(briefing(self.rows), self.by_id)
        regions = [expert.region_of(r) for r in rows]
        self.assertEqual(regions, sorted(regions, key=expert.REGION_ORDER.index))

    def test_brief_region_overrides_web_region(self):
        """웹은 국내·해외 기사가 한 이슈에 접히면 '국내·해외'로 적는다.

        듣는 사람이 보고 있는 것은 텔레그램 목록이므로, 그 카드가 국내 목록에
        있었으면 국내 블록에서 말해야 한다.
        """
        row = issue("m1", "한미 원자력 협정 개정 논의", "국내·해외", 2)
        row["brief_region"] = "국내"
        self.assertEqual(expert.region_of(row), "국내")

    def test_missing_rank_keeps_previous_web_order(self):
        """옛 회차에는 brief_rank 가 없다 — 그때는 들어온 순서를 그대로 쓴다."""
        rows = [issue("a", "기사 A", "해외"), issue("b", "기사 B", "해외"),
                issue("c", "기사 C", "해외")]
        by_id = {r["issue_id"]: r for r in rows}
        got = expert.selected_issues(briefing(rows), by_id)
        self.assertEqual([r["issue_id"] for r in got], ["a", "b", "c"])

    def test_ranked_and_unranked_mix_puts_unranked_last(self):
        rows = [issue("x", "번호 없는 기사", "국내"),
                issue("d1", "국내 1번", "국내", 1),
                issue("d2", "국내 2번", "국내", 2)]
        by_id = {r["issue_id"]: r for r in rows}
        got = expert.selected_issues(briefing(rows), by_id)
        self.assertEqual([r["issue_id"] for r in got], ["d1", "d2", "x"])

    def test_script_blocks_follow_the_same_order(self):
        rows = expert.selected_issues(briefing(self.rows), self.by_id)
        blocks = expert.script_blocks(rows)
        self.assertEqual([name for name, _ in blocks], ["국내", "해외"])
        self.assertEqual([r["issue_id"] for r in blocks[0][1]], ["d1", "d2", "d3"])
        self.assertEqual([r["issue_id"] for r in blocks[1][1]], ["o1", "o2"])

    def test_batching_preserves_order_within_a_block(self):
        rows = [issue(f"d{i}", f"국내 {i}번 기사", "국내", i) for i in range(1, 12)]
        batches = expert.even_batches(rows, expert.SCRIPT_BATCH_ISSUES)
        flat = [r["issue_id"] for batch in batches for r in batch]
        self.assertEqual(flat, [r["issue_id"] for r in rows])


class OrderReportTests(unittest.TestCase):
    """최종 대본에서 순서 뒤바뀜·누락·중복을 검출한다."""

    def setUp(self):
        self.issues = [
            issue("d1", "영덕 신규 원전 부지 선정", "국내", 1,
                  fingerprint={"assets": ["영덕"], "actors": ["한수원"]}),
            issue("d2", "테라파워 두산에너빌리티 공급계약", "국내", 2,
                  fingerprint={"actors": ["테라파워", "두산에너빌리티"]}),
            issue("o1", "디아블로캐년 원전 연방자금 지원", "해외", 1,
                  fingerprint={"assets": ["디아블로캐년"]}),
        ]

    def script(self, *paragraphs):
        return "\n".join(f"HOST: {p}" for p in paragraphs)

    def test_correct_order_passes(self):
        script = self.script(
            "먼저 영덕 신규 원전 부지 선정부터 보겠습니다.",
            "영덕 부지는 절차상 다음 단계가 남아 있습니다.",
            "다음은 두산에너빌리티가 테라파워와 맺은 공급계약입니다.",
            "테라파워 계약의 규모와 일정을 정리하겠습니다.",
            "해외로 넘어가 디아블로캐년 지원을 보겠습니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["observed"], ["d1", "d2", "o1"])

    def test_swapped_order_is_detected(self):
        script = self.script(
            "먼저 두산에너빌리티와 테라파워의 공급계약을 보겠습니다.",
            "테라파워 계약의 일정입니다.",
            "이어서 영덕 신규 원전 부지 선정입니다.",
            "영덕 부지 절차가 남았습니다.",
            "마지막으로 디아블로캐년 지원입니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertFalse(report["ok"])
        self.assertTrue(report["out_of_order"])
        self.assertEqual(report["observed"], ["d2", "d1", "o1"])

    def test_missing_issue_is_detected(self):
        script = self.script(
            "영덕 신규 원전 부지 선정을 보겠습니다.",
            "영덕 부지 절차가 남았습니다.",
            "디아블로캐년 지원으로 넘어가겠습니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing"], ["d2"])

    def test_duplicate_explanation_is_detected(self):
        script = self.script(
            "영덕 신규 원전 부지 선정입니다.",
            "두산에너빌리티와 테라파워 공급계약입니다.",
            "디아블로캐년 지원입니다.",
            "다시 영덕 부지 이야기로 돌아오겠습니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertFalse(report["ok"])
        self.assertIn("d1", report["duplicated"])

    def test_adjacent_paragraphs_are_one_explanation(self):
        script = self.script(
            "영덕 신규 원전 부지 선정입니다.",
            "영덕 부지 이야기를 조금 더 하겠습니다.",
            "영덕 관련 다음 절차를 짚겠습니다.",
            "두산에너빌리티와 테라파워 공급계약입니다.",
            "디아블로캐년 지원입니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertEqual(report["duplicated"], [])
        self.assertTrue(report["ok"], report)

    def test_cross_reference_does_not_break_order(self):
        """'앞서 본 ○번과 이어집니다' 는 순서 위반이 아니다."""
        script = self.script(
            "영덕 신규 원전 부지 선정입니다.",
            "두산에너빌리티와 테라파워 공급계약입니다.",
            "이 계약은 앞서 본 영덕 부지 선정과 테라파워 공급망 논의가 겹칩니다.",
            "디아블로캐년 지원입니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertTrue(report["ok"], report)

    def test_unanchored_issue_is_not_judged(self):
        """고유 앵커가 없는 이슈로는 경보를 울리지 않는다 (없는 근거로 만든 경보는 무시된다)."""
        twins = [issue("a", "원전 정책 동향", "국내", 1),
                 issue("b", "원전 정책 동향", "국내", 2)]
        report = expert.script_order_report(
            self.script("원전 정책 동향을 보겠습니다."), twins)
        self.assertEqual(sorted(report["unanchored"]), ["a", "b"])
        self.assertTrue(report["ok"])

    def test_anchors_exclude_terms_shared_across_issues(self):
        anchors = expert.issue_anchors(self.issues)
        self.assertIn("영덕", anchors["d1"])
        self.assertNotIn("원전", anchors["d1"])   # 상투어
        self.assertNotIn("테라파워", anchors["d1"])


class OrderRepairPromptTests(unittest.TestCase):
    def test_prompt_lists_the_required_order_and_the_problem(self):
        issues = [issue("d1", "영덕 부지 선정", "국내", 1),
                  issue("d2", "테라파워 공급계약", "국내", 2)]
        report = {"expected": ["d1", "d2"], "observed": ["d2", "d1"],
                  "out_of_order": True, "missing": [], "duplicated": []}
        titles = {"d1": "영덕 부지 선정", "d2": "테라파워 공급계약"}
        prompt = expert.order_repair_prompt([], "HOST: ...", report, titles)
        self.assertIn("1. [d1] 영덕 부지 선정", prompt)
        self.assertIn("2. [d2] 테라파워 공급계약", prompt)
        self.assertIn("설명 순서가", prompt)
        self.assertIn("재배치", prompt)


class ScriptPromptTests(unittest.TestCase):
    def test_prompt_states_the_running_order(self):
        dossiers = [{"issue_id": "d1", "title": "영덕 부지 선정"},
                    {"issue_id": "d2", "title": "테라파워 공급계약"}]
        prompt = expert.script_prompt({"date": "2026-08-17"}, dossiers, {}, "국내", (1, 1))
        self.assertIn("설명 순서", prompt)
        self.assertIn("1. 영덕 부지 선정", prompt)
        self.assertIn("2. 테라파워 공급계약", prompt)


if __name__ == "__main__":
    unittest.main()
