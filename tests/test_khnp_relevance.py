"""khnp_relevance.py + daily_brief 의 조건부 필수 항목 보완 회귀 테스트.

사용자가 지목한 사례 (2026-08-17):
  · ESS/전력 인프라 정책 기사        → 관련성이 충분하면 `한수원 시사점` 생성
  · 한수원과 관련성이 거의 없는 기사 → 억지로 생성하지 않는다
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import daily_brief
import khnp_relevance

# 실측 2026-08-16 발송분 — 무슨 일 한 줄만 나가고 한수원 시사점이 비어 있었다.
ESS_ARTICLE = {
    "hash": "ess1",
    "title_kr": "정부, AI 시대 대비 ESS 및 무탄소 전력 인프라 확대 전략 추진",
    "importance": "nice_to_know",
    "section": "domestic",
    "summary": "정부가 AI 시대 국가 경쟁력 확보를 위해 2030년까지 재생에너지 비중을 "
               "20% 이상으로 확대하고, 전력 안정성을 위한 ESS 인프라를 선제 구축한다.",
    "detail": "기후에너지환경부는 '전기국가' 전략을 통해 재생에너지와 원전을 기반으로 "
              "무탄소 전력 공급을 확대할 방침이다. 재생에너지의 간헐성을 보완하기 위해 "
              "ESS의 역할이 강조되며, 국내 배터리 업계는 전기차 의존도를 낮추고 "
              "전력망·산업용 ESS 시장으로 사업 영역을 확장할 기회를 맞이했다. "
              "정부는 2030년까지 재생에너지 설비 용량을 100GW 이상으로 늘릴 계획이다.",
    "implication": "",
    "topics": ["policy"],
    "tags": ["#ESS", "#무탄소전원"],
}

UNRELATED_ARTICLE = {
    "hash": "misc1",
    "title_kr": "프로야구 흥행 속 구단 유니폼 판매 사상 최대",
    "importance": "nice_to_know",
    "section": "domestic",
    "summary": "올해 프로야구 관중 증가로 구단 유니폼 판매가 사상 최대를 기록했다.",
    "detail": "구단별 굿즈 매출이 전년 대비 40% 늘었다. 온라인 판매 비중이 절반을 "
              "넘어섰고, 지방 구단의 성장률이 특히 높았다. 관계자는 관중 증가가 "
              "굿즈 매출을 견인했다고 설명했다.",
    "implication": "",
}

EV_BATTERY_ARTICLE = {
    "hash": "ev1",
    "title_kr": "국내 배터리 3사, 전기차 판매 둔화에 2분기 영업이익 급감",
    "importance": "nice_to_know",
    "section": "domestic",
    "summary": "전기차 수요 둔화로 국내 배터리 3사의 2분기 영업이익이 크게 줄었다.",
    "detail": "LG에너지솔루션·삼성SDI·SK온의 2분기 합산 영업이익이 전년 대비 60% "
              "감소했다. 북미 전기차 판매 둔화와 재고 조정이 원인으로 지목된다. "
              "증권가는 하반기 실적 반등 폭을 두고 전망이 갈린다고 본다.",
    "implication": "",
}


class RelevanceTests(unittest.TestCase):
    def test_ess_power_policy_is_required(self):
        verdict = khnp_relevance.relevance(ESS_ARTICLE)
        self.assertEqual(verdict["level"], "required")
        # 한 축이 스친 것이 아니라 여러 축이 겹쳐서 올라간 것이어야 한다.
        self.assertGreaterEqual(len(verdict["domains"]), 3)

    def test_unrelated_article_is_not_required(self):
        self.assertEqual(khnp_relevance.relevance(UNRELATED_ARTICLE)["level"],
                         "not_required")

    def test_ev_battery_earnings_is_not_required(self):
        """'배터리'가 어휘에 있다고 전기차 실적 기사까지 걸리면 안 된다."""
        self.assertNotEqual(khnp_relevance.relevance(EV_BATTERY_ARTICLE)["level"],
                            "required")

    def test_core_nuclear_article_is_required(self):
        verdict = khnp_relevance.relevance({
            "title_kr": "원안위, 신한울 3호기 운영허가 심사 착수",
            "summary": "원자력안전위원회가 신한울 3호기 운영허가 심사에 들어갔다.",
            "detail": "한수원이 제출한 운영허가 신청서에 대한 심사가 시작됐다." * 3,
        })
        self.assertEqual(verdict["level"], "required")

    def test_reasons_name_the_axes(self):
        verdict = khnp_relevance.relevance(ESS_ARTICLE)
        self.assertTrue(any(r.startswith("policy") for r in verdict["reasons"]))


class RequirementTests(unittest.TestCase):
    def test_required_and_empty_triggers_regeneration(self):
        req = khnp_relevance.implication_requirement(ESS_ARTICLE)
        self.assertEqual(req["level"], "required")
        self.assertTrue(req["regenerate"])

    def test_existing_implication_is_left_alone(self):
        article = {**ESS_ARTICLE, "implication": "이미 있는 해석이다."}
        self.assertFalse(khnp_relevance.implication_requirement(article)["regenerate"])

    def test_no_body_is_never_regenerated(self):
        """본문 없는 기사에 해석을 요구하면 모델이 제목을 늘려 쓴다 (기존 계약)."""
        article = {**ESS_ARTICLE, "detail": ""}
        req = khnp_relevance.implication_requirement(article)
        self.assertFalse(req["regenerate"])
        self.assertIn("no_body", req["reasons"])

    def test_unrelated_article_is_never_regenerated(self):
        self.assertFalse(
            khnp_relevance.implication_requirement(UNRELATED_ARTICLE)["regenerate"])


class BackfillTests(unittest.TestCase):
    """daily_brief.complete_required_fields — 생성·검증·빈칸 허용."""

    def run_backfill(self, items, response):
        with mock.patch.object(daily_brief, "is_available", return_value=True), \
             mock.patch.object(daily_brief, "call_json", return_value=response) as call:
            diag = daily_brief.complete_required_fields(items)
        return diag, call

    def test_fills_ess_article_and_marks_source(self):
        items = [dict(ESS_ARTICLE)]
        diag, call = self.run_backfill(items, {"items": [{
            "idx": 0,
            "implication": "재생에너지 100GW 확대의 간헐성을 ESS로 메우는 구도라 "
                           "무탄소 기저 전원인 원전의 역할 규정이 쟁점이 된다.",
        }]})
        self.assertEqual(diag["filled"], 1)
        self.assertTrue(items[0]["implication"])
        self.assertEqual(items[0]["implication_source"], "khnp_backfill")
        self.assertEqual(items[0]["implication_requirement"], "required")
        self.assertEqual(call.call_count, 1)

    def test_unrelated_article_is_not_sent_to_the_model(self):
        items = [dict(UNRELATED_ARTICLE)]
        diag, call = self.run_backfill(items, {"items": []})
        self.assertEqual(diag["candidates"], 0)
        call.assert_not_called()
        self.assertEqual(items[0]["implication"], "")

    def test_empty_response_is_accepted_as_normal(self):
        """근거가 없으면 빈칸이 정답이다 — 빈 문자열은 실패가 아니다."""
        items = [dict(ESS_ARTICLE)]
        diag, _ = self.run_backfill(items, {"items": [{"idx": 0, "implication": ""}]})
        self.assertEqual(diag["filled"], 0)
        self.assertEqual(items[0]["implication"], "")
        self.assertEqual(diag["rejected"], [])

    def test_hollow_sentence_is_rejected(self):
        items = [dict(ESS_ARTICLE)]
        diag, _ = self.run_backfill(items, {"items": [{
            "idx": 0,
            "implication": "정부의 에너지 정책 변화가 원자력 산업에 미칠 영향을 시사한다.",
        }]})
        self.assertEqual(diag["filled"], 0)
        self.assertEqual(diag["rejected"][0]["reason"], "hollow")
        self.assertEqual(items[0]["implication"], "")

    def test_title_restatement_is_rejected(self):
        items = [dict(ESS_ARTICLE)]
        diag, _ = self.run_backfill(items, {"items": [{
            "idx": 0,
            "implication": "정부가 AI 시대에 대비해 ESS와 무탄소 전력 인프라 확대 "
                           "전략을 추진한다.",
        }]})
        self.assertEqual(diag["filled"], 0)
        self.assertEqual(diag["rejected"][0]["reason"], "restates_title")

    def test_gemini_failure_keeps_the_blank(self):
        from gemini_client import GeminiError
        items = [dict(ESS_ARTICLE)]
        with mock.patch.object(daily_brief, "is_available", return_value=True), \
             mock.patch.object(daily_brief, "call_json", side_effect=GeminiError("죽음")):
            diag = daily_brief.complete_required_fields(items)
        self.assertEqual(diag["skipped"], "gemini_error")
        self.assertEqual(items[0]["implication"], "")

    def test_no_api_key_is_not_fatal(self):
        items = [dict(ESS_ARTICLE)]
        with mock.patch.object(daily_brief, "is_available", return_value=False):
            diag = daily_brief.complete_required_fields(items)
        self.assertEqual(diag["skipped"], "no_api_key")
        self.assertEqual(items[0]["implication"], "")


class CardRenderTests(unittest.TestCase):
    def test_backfilled_implication_reaches_the_telegram_card(self):
        from synthesize import format_cards_message

        article = {**ESS_ARTICLE,
                   "implication": "재생에너지 100GW 확대로 무탄소 기저 전원인 "
                                  "원전의 역할 규정이 12차 전기본의 쟁점이 된다."}
        card = daily_brief.item_to_card(article, None)
        message = format_cards_message([card], header="국내")
        self.assertIn("🇰🇷 한수원 시사점", message)
        self.assertIn("12차 전기본", message)

    def test_blank_implication_omits_the_line(self):
        from synthesize import format_cards_message

        card = daily_brief.item_to_card(dict(UNRELATED_ARTICLE), None)
        self.assertNotIn("한수원 시사점", format_cards_message([card], header="국내"))


if __name__ == "__main__":
    unittest.main()
