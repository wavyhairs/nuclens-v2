"""Daily 카드 제목(title_kr) — 정보밀도는 올리되 사실성은 그대로.

왜 이 파일이 있는가
-------------------
제목 규칙을 '번역'에서 '헤드라인'으로 바꿨다(news_bot.CURATION_SYSTEM_PROMPT).
실측 2026-08-22 curated.json 의 must_read·nice_to_know 2,008건에서:

  원문 `진짜 AI 병목은 천연가스? …2028년 '가스 대란' 경고`
    → `AI 데이터센터 전력 수요 급증과 천연가스 공급난 우려`   (2028 이 사라졌다)
  원문 `러, 30GW 원전 증설·신흥국 17기 공세`
    → 같은 계열에서 수치를 통째로 버린 제목들
  `전기화 시대, 전력안보와 계통 운영 능력의 중요성 부상` (주체도 사건도 없다)

즉 **원문에 이미 있는 사실을 제목이 버리는** 쪽의 손실이었다. 그래서 프롬프트에
'행위 주체 + 핵심 행동'과 '수치 우선 보존'을 넣었다.

그런데 같은 프롬프트가 "정보를 더 담아라"라고 읽히면 없는 사실을 지어내는 쪽으로도
샌다. 이 파일이 지키는 것이 그 경계다 — **생성력은 프롬프트, 검증은 기존 게이트
그대로.** 아래 검사는 하나도 새 게이트를 만들지 않는다. 기존
article_quality_gate·news_bot·event_stage 가 지금 무엇을 막고 무엇을 못 막는지를
고정한다(못 막는 것도 명시한다 — 나중에 '막힌다고 믿는' 것이 제일 위험하다).

LLM 호출은 없다. 전부 픽스처다.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")

import article_quality_gate as gate  # noqa: E402
import event_stage  # noqa: E402
import news_bot  # noqa: E402

REF = "2026-08-20"


def _title_rule() -> str:
    """프롬프트에서 title_kr 항목 블록만."""
    prompt = news_bot.CURATION_SYSTEM_PROMPT
    start = prompt.index("- title_kr:")
    return prompt[start:prompt.index("\n- summary:", start)]


class TitlePromptContractTests(unittest.TestCase):
    """프롬프트는 코드가 아니라서 조용히 되돌려진다 — 규칙의 존재를 못 박는다.

    문장을 통째로 비교하지 않고 **규칙의 핵심 낱말**만 본다. 표현을 다듬는 것은
    자유지만 규칙을 빼는 것은 아니다.
    """

    def setUp(self):
        self.rule = _title_rule()

    def test_headline_framing(self):
        self.assertIn("헤드라인", self.rule)
        self.assertIn("행위 주체", self.rule)

    def test_meaningful_numbers_are_preserved(self):
        self.assertIn("수치는 우선 보존", self.rule)
        for unit in ("GW", "금액", "기수", "증감률", "시행시점"):
            self.assertIn(unit, self.rule, f"보존 대상에서 {unit} 이 빠졌다")

    def test_vague_noun_phrases_are_named_and_banned(self):
        # 실측에서 가장 많이 나온 꼴들. 이름을 적어 두지 않으면 모델이 계속 쓴다.
        for phrase in ("관련 동향", "관련 논의", "중요성 부상", "필요성 제기"):
            self.assertIn(phrase, self.rule, f"모호 표현 {phrase} 가 금지 목록에 없다")

    def test_event_stage_must_not_be_escalated(self):
        """계획을 확정으로, 전망을 결정으로 올려 쓰지 말 것.

        `검토`→`결정` 은 event_stage 어휘로는 안 잡힌다(아래
        EventStageEscalationTests 참조) — 이 자리가 유일한 방어선이라 규칙
        문장에 그 짝이 이름으로 박혀 있어야 한다.
        """
        self.assertIn("사건 단계를 정확히 유지", self.rule)
        for word in ("계획", "검토", "추진", "협의", "합의", "계약", "승인", "착공", "준공"):
            self.assertIn(word, self.rule, f"단계 목록에서 {word} 가 빠졌다")
        self.assertIn("`검토`면 `결정`으로", self.rule)

    def test_invention_is_still_forbidden(self):
        """정보밀도를 올리라는 지시가 '아는 사실로 채우라'로 읽히면 안 된다."""
        self.assertIn("원문에 없는 것은 넣지 않는다", self.rule)
        for word in ("인물명", "기업명", "기관명", "수치"):
            self.assertIn(word, self.rule)
        # 본문 없는 기사는 제목 이상을 쓰지 않는다 — summary 규칙과 같은 문.
        self.assertIn("`본문:` 이 없는 기사", self.rule)

    def test_the_summary_side_of_the_contract_is_untouched(self):
        """제목 규칙을 늘리며 요약 쪽 안전장치를 건드리지 않았는가."""
        prompt = news_bot.CURATION_SYSTEM_PROMPT
        self.assertIn("**`본문:` 이 없는 기사는 제목에 적힌 것 이상을 쓰지 말 것.**", prompt)
        self.assertIn("**인명은 원문에 적힌 대로만 쓴다.**", prompt)


class TitleNumberTests(unittest.TestCase):
    """수치는 '보존'과 '날조'가 종이 한 장 차이다. 양쪽을 같이 고정한다."""

    def test_source_numbers_survive_in_the_headline(self):
        """원문에 있는 수치를 제목으로 끌어올리는 것은 게이트가 막지 않는다.

        이것이 이번 프롬프트 변경이 노리는 방향이다 — 막히면 개선 자체가 불가능.
        """
        article = {
            "title": "Rosatom to add 30 GW of nuclear capacity by 2042",
            "description": "Russia plans 38 new reactors at home and 17 abroad.",
            "title_kr": "러시아, 2042년까지 원전 30GW 추가 건설 추진",
            "summary": "러시아가 2042년까지 원전 30GW를 추가로 짓겠다는 계획을 내놨다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date=REF)
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.findings, ())

    def test_a_reactor_count_from_the_body_is_not_a_conflict(self):
        """제목에 없고 본문에만 있는 수치를 제목으로 올려도 통과해야 한다."""
        article = {
            "title": "체코 두코바니 원전 사업 본계약 체결",
            "description": "한국수력원자력이 체코 두코바니 원전 2기 건설 본계약을 체결했다.",
            "title_kr": "한수원, 체코 두코바니 원전 2기 본계약 체결",
            "summary": "한국수력원자력이 체코 두코바니 원전 2기 본계약을 체결했다.",
            "features": {},
        }
        self.assertEqual(gate.audit_article_integrity(article, reference_date=REF).action, "allow")

    def test_numbers_absent_from_the_source_are_quarantined(self):
        """원문 어디에도 없는 규모·기수를 제목이 만들어 내면 격리한다."""
        article = {
            "title": "한수원, 체코 두코바니 신규 원전 본계약 체결",
            "description": "한국수력원자력이 체코 두코바니 신규 원전 건설 사업의 본계약을 체결했다.",
            "title_kr": "한수원, 체코 두코바니 원전 4기 26조원 본계약 체결",
            "summary": "한국수력원자력이 체코 두코바니 원전 본계약을 체결했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date=REF)
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        conflicts = finding.details["quantity_conflicts"]
        self.assertEqual(conflicts["기"]["unsupported_output"], ["4"])
        self.assertEqual(conflicts["조원"]["unsupported_output"], ["26"])

    def test_a_changed_capacity_is_quarantined(self):
        """수치를 '보존'하랬더니 다른 수로 바꿔 쓰는 경우."""
        article = {
            "title": "Doosan wins order for 345 MW Natrium reactor equipment",
            "description": "The 345 MW unit is scheduled for 2030.",
            "title_kr": "두산에너빌리티, 545MW 나트륨 원자로 기자재 수주",
            "summary": "두산에너빌리티가 545MW 나트륨 원자로 기자재를 수주했다.",
            "features": {},
        }
        self.assertEqual(gate.audit_article_integrity(article, reference_date=REF).action,
                         "quarantine")


class TitleNameTests(unittest.TestCase):
    """제목은 카드에서 가장 크게 보이는 줄이다 — 여기 틀린 사람이 오면 반쪽 수정으로
    끝나지 않는다(news_bot.strip_unsourced_person_names 의 존재 이유)."""

    ARTICLE = {
        "title": '李 대통령 "해남 청정에너지, 반도체 클러스터 움직이는 힘"',
        "description": "",
        "domain": "namdonews.com",
    }

    def test_a_full_name_the_source_never_used_is_dropped_from_the_title(self):
        item = {
            "importance": "nice_to_know",
            "title_kr": "윤석열 대통령, 해남 청정에너지 단지 조성 강조",
            "summary": "윤석열 대통령이 해남 청정에너지 단지 조성을 강조했다.",
        }
        out = news_bot.normalize_curation_item(dict(item), dict(self.ARTICLE))
        self.assertNotIn("윤석열", out["title_kr"])
        self.assertNotIn("윤석열", out["summary"])
        self.assertIn("해남", out["title_kr"], "이름만 떼고 나머지 사실은 살아야 한다")

    def test_a_matching_surname_is_left_alone(self):
        """원문과 성이 맞으면 깎지 않는다 — 제목 정보밀도를 깎는 쪽이 더 나쁘다."""
        item = {
            "importance": "nice_to_know",
            "title_kr": "이재명 대통령, 해남 청정에너지 단지 조성 강조",
            "summary": "이재명 대통령이 해남 청정에너지 단지 조성을 강조했다.",
        }
        out = news_bot.normalize_curation_item(dict(item), dict(self.ARTICLE))
        self.assertIn("이재명", out["title_kr"])


class TitleWithoutBodyTests(unittest.TestCase):
    """본문을 못 받아온 기사(실측 900건 중 597건)에서 제목 이상을 쓰지 않는가."""

    ARTICLE = {
        "title": "[외신 헤드라인] 애플, 中 창신메모리 칩 테스트",
        "description": "",
        "domain": "polinews.co.kr",
    }
    ITEM = {
        "importance": "must_read",
        "title_kr": "엔비디아, 전력 인프라에 2조원 투자 결정",
        "summary": "엔비디아가 전력 인프라에 2조원을 투자하기로 했다.",
        "detail": "엔비디아는 데이터센터 전력 확보를 위해 대규모 투자를 결정했다.",
        # 상투적 어미(…시사한다)는 본문 유무와 무관하게 drop_hollow_implication
        # 이 지운다. 여기서 보려는 것은 그 규칙이 아니라 본문 유무이므로 구체적
        # 사실을 담은 문장을 쓴다.
        "implication": "전력 인프라 투자 규모는 2조원으로 2027년까지 집행된다.",
        "why_important": "AI 전력 수요 대응의 분수령이다.",
    }

    def test_interpretation_fields_are_emptied(self):
        out = news_bot.normalize_curation_item(dict(self.ITEM), dict(self.ARTICLE))
        self.assertEqual(out["detail"], "")
        self.assertEqual(out["implication"], "")
        self.assertEqual(out["why_important"], "")

    def test_a_body_makes_the_same_fields_survive(self):
        """반대 방향도 고정한다 — 본문이 있으면 깎지 않는다."""
        body = "엔비디아는 데이터센터 전력 확보를 위해 전력 인프라 투자를 결정했다고 밝혔다."
        out = news_bot.normalize_curation_item(dict(self.ITEM), dict(self.ARTICLE), body)
        self.assertTrue(out["implication"])
        self.assertTrue(out["why_important"])

    def test_a_fabricated_headline_with_body_evidence_is_quarantined(self):
        """본문·요약이 남아 있을 때는 제목이 만들어 낸 주체를 게이트가 잡는다.

        본문 없는 기사(위)에서는 못 잡는다 — 대조할 재료가 제목뿐이라 '못 읽었다'와
        '틀렸다'를 가를 수 없기 때문이다. 그 구간의 방어선은 프롬프트와, 해석
        필드를 통째로 비우는 위 두 검사다.
        """
        article = {
            "title": "해외건설 500억 달러 시대 겨냥…K건설, 중동 플랜트서 원전·전력 선회",
            "description": "국내 건설사들이 중동 플랜트 수주에서 원전·전력 사업으로 방향을 틀고 있다.",
            "title_kr": "한수원, 신규 원전 후보지로 경북 영덕 선정",
            "summary": "한국수력원자력이 신규 원전 2기 후보지로 경북 영덕군을 선정했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date=REF)
        self.assertEqual(result.action, "quarantine")
        self.assertIn("title_source_mismatch", [f.code for f in result.findings])


class EventStageEscalationTests(unittest.TestCase):
    """검토를 결정으로 올려 쓰지 않는가.

    **덮이는 범위를 정직하게 적는다.** event_stage 의 어휘는 심사·심의·타당성조사
    ↔ 승인·인가·확정·의결처럼 매체가 실제로 쓰는 표현을 잡는다. 반면 맨낱말
    `검토`·`결정` 은 어느 목록에도 없어서 **탐지되지 않는다** — 둘 다 일반어라
    목록에 넣으면 story clustering 의 단계 거부권이 통째로 흔들린다(event_stage
    docstring: 표식이 늘면 사건이 갈린다). 이번 작업은 그 임계값을 건드리지 않기로
    했으므로, `검토`→`결정` 짝의 방어선은 프롬프트 규칙
    (TitlePromptContractTests.test_event_stage_must_not_be_escalated) 하나다.
    """

    def test_review_to_approval_is_a_stage_conflict(self):
        before = event_stage.detect_stages("원안위, 신한울 3호기 안전성 심사 착수")
        after = event_stage.detect_stages("원안위, 신한울 3호기 건설 승인 의결")
        self.assertEqual(set(before), {"review"})
        self.assertEqual(set(after), {"approval"})
        self.assertTrue(event_stage.stage_conflict(before, after))

    def test_feasibility_study_to_confirmed_is_a_stage_conflict(self):
        before = event_stage.detect_stages("정부, 제12차 전기본 원전 확대 타당성조사 착수")
        after = event_stage.detect_stages("정부, 제12차 전기본 원전 확대 확정")
        self.assertIn("review", before)
        self.assertEqual(set(after), {"approval"})
        self.assertTrue(event_stage.stage_conflict(before, after))

    def test_plain_review_and_decision_words_are_deliberately_not_detected(self):
        """지금 상태를 못 박는다. 여기가 바뀌면 dedup·story 판정이 함께 움직이므로
        이 파일만 고쳐 통과시키지 말 것."""
        self.assertEqual(set(event_stage.detect_stages("정부, 신규 원전 부지 검토")), set())
        self.assertEqual(set(event_stage.detect_stages("정부, 신규 원전 부지 결정")), set())

    def test_the_gate_records_the_stage_conflict_it_can_see(self):
        article = {
            "title": "한수원, 체코 두코바니 원전 안전성 심사 착수",
            "description": "한국수력원자력이 체코 두코바니 원전 안전성 심사에 착수했다.",
            "title_kr": "웨스팅하우스, 체코 두코바니 원전 건설 승인",
            "summary": "웨스팅하우스가 체코 두코바니 원전 건설을 승인받았다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date=REF)
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        self.assertEqual(finding.details["source_stages"], ["review"])
        self.assertEqual(finding.details["output_stages"], ["approval"])
        self.assertTrue(finding.details["stage_conflict"])


class TitleQuarantineRoutingTests(unittest.TestCase):
    """title_source_mismatch 가 붙으면 실제로 발송에서 빠지는가 —
    finding 만 남고 통과하면 게이트가 있는 척만 하는 것이다."""

    BROKEN = {
        "title": "Cameco starts construction at a new Canadian uranium mine",
        "description": "The mine will supply 20% of global uranium output.",
        "title_kr": "스페인 알마라즈 원전 수명 2030년까지 연장",
        "summary": "스페인 정부가 알마라즈 원전 가동 시한을 연장하기로 결정했다.",
        "features": {},
    }

    def test_quarantine_is_the_action_and_the_finding_is_a_quarantine(self):
        result = gate.audit_article_integrity(dict(self.BROKEN), reference_date=REF)
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        self.assertEqual(finding.severity, "quarantine")
        self.assertEqual(finding.field, "title_kr")
        self.assertFalse(result.eligible)

    def test_the_broken_title_is_not_silently_rewritten(self):
        """게이트는 제목을 고쳐 쓰지 않는다 — 고쳐 쓰면 무엇이 틀렸는지 사라진다."""
        result = gate.audit_article_integrity(dict(self.BROKEN), reference_date=REF)
        self.assertEqual(result.value["title_kr"], self.BROKEN["title_kr"])
        self.assertNotIn("title_kr", result.removed_fields)

    def test_a_healthy_translation_is_not_swept_up(self):
        """오탐이 늘면 영문 제목 폴백으로 떨어져 지금보다 나쁘다."""
        article = {
            "title": "Korean consortium prepares bid for Czech nuclear project",
            "description": "The consortium is preparing its bid documents.",
            "title_kr": "한국 컨소시엄, 체코 원전 사업 입찰 준비",
            "summary": "한국 컨소시엄이 체코 원전 사업 입찰을 준비하고 있다.",
            "features": {},
        }
        self.assertEqual(gate.audit_article_integrity(article, reference_date=REF).action,
                         "allow")


if __name__ == "__main__":
    unittest.main()
