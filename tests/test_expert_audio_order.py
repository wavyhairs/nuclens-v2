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
          fingerprint=None, entity_ids=None):
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
        "entity_ids": entity_ids or [],
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

    def test_duplication_only_leaves_expected_and_observed_identical(self):
        """중복만 어긋난 경우 기대/관측 두 목록은 **완전히 같다**.

        observed 는 각 이슈의 첫 등장만 담는 중복 제거 목록이기 때문이다. 그래서
        최종 경고가 이 두 목록만 찍으면, 글자까지 같은 두 줄 뒤에 "미통과"만
        붙어 나온다 — 2026-08-20 회차가 정확히 그 모습이었고(13개 항목 동일),
        매일 뜨는 그 경고는 늑대소년으로 읽혀 무시된다.

        이 테스트는 그 함정을 못으로 박아 둔다. 경고문이 무엇을 찍어야 하는지는
        아래 test_final_order_warning_names_the_actual_fault 가 지킨다.
        """
        script = self.script(
            "영덕 신규 원전 부지 선정입니다.",
            "두산에너빌리티와 테라파워 공급계약입니다.",
            "디아블로캐년 지원입니다.",
            "다시 영덕 부지 이야기로 돌아오겠습니다.",
        )
        report = expert.script_order_report(script, self.issues)
        self.assertFalse(report["ok"])
        self.assertEqual(report["duplicated"], ["d1"])
        self.assertFalse(report["out_of_order"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["expected"], report["observed"],
                         "이 함정이 사라졌다면 경고문 규칙을 다시 볼 때다")

    def test_final_order_warning_names_the_actual_fault(self):
        """최종 경고는 무엇이 틀렸는지(뒤바뀜·누락·중복)를 말해야 한다.

        기대/관측 목록은 순서가 실제로 뒤바뀐 때만 쓸모가 있다 — 그때만 두
        목록이 서로 다르다.
        """
        source = (ROOT / "expert_audio_brief.py").read_text(encoding="utf-8")
        head, _, tail = source.partition("최종 순서 점검 미통과")
        self.assertTrue(tail, "최종 순서 경고문이 사라졌다")
        warning = tail[:400]
        for field in ("out_of_order", "missing", "duplicated"):
            self.assertIn(field, warning,
                          f"경고가 {field} 을 말하지 않는다 — 원인을 못 읽는다")
        self.assertIn('if final["out_of_order"]', warning,
                      "기대/관측 목록은 순서가 뒤바뀐 때만 찍어야 한다")

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

    def test_prompt_lists_identity_names_when_dossier_has_them(self):
        dossiers = [{"issue_id": "z1", "title": "자포리자 원전 외부 전력 차단",
                     "identity_names": ["자포리자 원전", "자포리자"]}]
        prompt = expert.script_prompt({"date": "2026-08-30"}, dossiers, {})
        self.assertIn("처음 소개 규칙", prompt)
        self.assertIn("자포리자 원전", prompt)

    def test_prompt_omits_the_rule_when_no_identity_name_resolved(self):
        """이름을 못 정했으면 규칙도 안 낸다 — 지어내라고 요구하지 않는다."""
        dossiers = [{"issue_id": "z1", "title": "정체불명 이슈"}]
        prompt = expert.script_prompt({"date": "2026-08-30"}, dossiers, {})
        self.assertNotIn("처음 소개 규칙", prompt)


class IntroIdentificationTests(unittest.TestCase):
    """'기사 주체·대상 미소개' 회귀 방지 (2026-08-30 자포리자 원전 브리핑 사례).

    해외 첫 소식이 외부전력 차단·비상 디젤발전기부터 설명하면서 정작 그 상황의
    대상인 '자포리자 원전'을 첫 문장에서 밝히지 않았다. 8/28경 다른 기사에서도
    같은 계열 문제가 났다 — 대본 생성 구조 전체의 일반 문제로 본다.
    """

    REGISTRY = {
        "zaporizhzhia": {"id": "zaporizhzhia", "name_kr": "자포리자 원전",
                          "aliases": ["자포리자", "자포리아"]},
    }

    def test_missing_subject_in_opening_paragraph_is_detected(self):
        issues = [issue("z1", "IAEA, 자포리자 원전 외부 전력 차단 장기화에 블랙아웃 경고",
                        "해외", 1, entity_ids=["zaporizhzhia"])]
        script = "\n".join([
            "HOST: 외부 전력 공급이 일주일 이상 끊기면서 비상 디젤발전기에 의존하고 있습니다.",
            "HOST: 자포리자 원전은 연료 재고도 빠듯한 상황입니다.",
        ])
        report = expert.intro_identification_report(script, issues, self.REGISTRY)
        self.assertFalse(report["ok"])
        self.assertIn("z1", report["missing"])

    def test_subject_named_in_first_sentence_passes(self):
        issues = [issue("z1", "IAEA, 자포리자 원전 외부 전력 차단 장기화에 블랙아웃 경고",
                        "해외", 1, entity_ids=["zaporizhzhia"])]
        script = "\n".join([
            "HOST: 해외 다음 소식은 자포리자 원전입니다. IAEA에 따르면 외부 전력 공급이 "
            "일주일 이상 끊기면서 비상 디젤발전기에 의존하고 있습니다.",
            "HOST: 연료 재고도 빠듯한 상황입니다.",
        ])
        report = expert.intro_identification_report(script, issues, self.REGISTRY)
        self.assertTrue(report["ok"], report)

    def test_registered_alias_in_leading_sentence_also_passes(self):
        """정식 명칭('자포리자 원전')이 아니라 등재된 별칭만 있어도 통과한다."""
        issues = [issue("z1", "IAEA, 자포리자 원전 외부 전력 차단 장기화에 블랙아웃 경고",
                        "해외", 1, entity_ids=["zaporizhzhia"])]
        script = "HOST: 자포리자에서는 외부 전력 공급이 끊긴 지 일주일이 넘었습니다."
        report = expert.intro_identification_report(script, issues, self.REGISTRY)
        self.assertTrue(report["ok"], report)

    def test_only_the_opening_paragraph_is_checked_not_every_paragraph(self):
        """뒤 문단은 이름을 반복하지 않아도 된다 — 매 문단 반복은 역효과다."""
        issues = [issue("z1", "IAEA, 자포리자 원전 외부 전력 차단 장기화에 블랙아웃 경고",
                        "해외", 1, entity_ids=["zaporizhzhia"])]
        script = "\n".join([
            "HOST: 해외 소식은 자포리자 원전입니다. 외부 전력 공급이 끊겼습니다.",
            "HOST: 비상 디젤발전기로 버티고 있습니다.",
            "HOST: 연료 재고도 빠듯합니다.",
        ])
        report = expert.intro_identification_report(script, issues, self.REGISTRY)
        self.assertTrue(report["ok"], report)

    def test_unregistered_issue_falls_back_to_title_anchor(self):
        """entity_registry 에 없는 이슈(법안·기술 등)는 제목 고유 앵커로 대신한다.

        두 문단 모두 '통과'라는 앵커가 있어 b1 소유 문단으로는 판정되지만,
        어떤 법안인지(제12차 전력수급기본계획)는 no_subject 쪽만 안 밝힌다.
        """
        issues = [issue("b1", "제12차 전력수급기본계획 국회 통과", "국내", 1)]
        no_subject = "HOST: 관련 법안이 어제 저녁 통과됐습니다."
        with_subject = "HOST: 제12차 전력수급기본계획이 국회를 통과했습니다."
        self.assertFalse(expert.intro_identification_report(no_subject, issues, {})["ok"])
        self.assertTrue(expert.intro_identification_report(with_subject, issues, {})["ok"])

    def test_issue_without_any_unique_anchor_is_not_judged(self):
        """고유 앵커도 entity_ids 도 없으면 없는 근거로 경보를 울리지 않는다."""
        twins = [issue("a", "원전 정책 동향", "국내", 1),
                 issue("b", "원전 정책 동향", "국내", 2)]
        report = expert.intro_identification_report(
            "HOST: 원전 정책 동향을 보겠습니다.", twins, {})
        self.assertTrue(report["ok"])
        self.assertEqual(report["missing"], [])

    def test_does_not_conflict_with_order_report(self):
        """도입부 위반이 있어도 순서 판정(script_order_report)은 별도로 정상 작동한다."""
        issues = [
            issue("d1", "영덕 신규 원전 부지 선정", "국내", 1, entity_ids=["yeongdeok"]),
            issue("d2", "테라파워 두산에너빌리티 공급계약", "국내", 2),
        ]
        registry = {"yeongdeok": {"id": "yeongdeok", "name_kr": "영덕 원전", "aliases": ["영덕"]}}
        script = "\n".join([
            "HOST: 부지 선정 절차가 이번 주 마무리됐습니다.",  # d1인데 '영덕'을 밝히지 않음
            "HOST: 두산에너빌리티가 테라파워와 계약을 맺었습니다.",
        ])
        order = expert.script_order_report(script, issues)
        intro = expert.intro_identification_report(script, issues, registry)
        self.assertTrue(order["ok"], order)
        self.assertFalse(intro["ok"])
        self.assertIn("d1", intro["missing"])


class IntroRepairPromptTests(unittest.TestCase):
    def test_prompt_names_the_missing_subject_and_the_paragraph_rule(self):
        dossiers = [{"issue_id": "z1", "identity_names": ["자포리자 원전", "자포리자"]}]
        report = {"missing": ["z1"]}
        titles = {"z1": "자포리자 원전 외부 전력 차단"}
        prompt = expert.intro_repair_prompt(dossiers, "HOST: ...", report, titles)
        self.assertIn("자포리자 원전", prompt)
        self.assertIn("처음 설명하는", prompt)
        self.assertIn("z1", prompt)


if __name__ == "__main__":
    unittest.main()
