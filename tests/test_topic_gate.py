"""topic_gate — 큐레이션 뒤 결정적 주제 게이트.

2026-10-03 아침 브리핑에 G7 원유 방출·오픈AI 조사·석유 회수가 이슈로 올랐다.
큐레이션 LLM 은 95% 를 noise 로 걸렀지만 5% 가 nice_to_know 로 샜고, 소급하면
발송 254건 중 14건이다. 여기서 그 14건이 빠지고, 한수원 사업환경 기사(전력수급
기본계획·AI 데이터센터·발전사 통합)는 **남는지**를 잠근다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import topic_gate  # noqa: E402

# 2026-10-03 실제 발송분·아카이브 모양.
G7_OIL = {
    "hash": "g7", "domain": "lemonde.fr",
    "title": "Sous pression de Donald Trump, le G7 annonce un déblocage de 100 millions de barils de pétrole",
    "title_kr": "G7, 트럼프 압박에 원유 1억 배럴 방출 발표",
    "summary": "G7 국가들이 유가 안정을 위해 전략 비축유 1억 배럴을 방출하기로 했다.",
    "importance": "nice_to_know", "section": "international",
    "topics": ["security_trade"], "tags": ["#에너지안보", "#유가"],
}
OPENAI_PROBE = {
    "hash": "oa", "domain": "lesechos.fr",
    "title": "La justice américaine enquête sur les agents IA d'OpenAI et d'Anthropic",
    "title_kr": "미국 사법당국, 오픈AI 및 앤스로픽의 AI 에이전트 사고 조사 착수",
    "summary": "미 법무부가 AI 에이전트 관련 사고를 조사한다.",
    "importance": "nice_to_know", "section": "international",
    # 실제 아카이브 행: topics 는 비었고 태그만 있다. regulation 태그가 붙었다면
    # khnp_relevance 대응표(regulation→core)로 required 가 된다 — 그 대응은 원자력
    # 규제 뜻으로 둔 것이라 여기서 바꾸지 않는다.
    "topics": [], "tags": ["#AI규제"],
}
POWER_PLAN = {
    "hash": "pp", "domain": "ebn.co.kr",
    "title_kr": "제12차 전력수급기본계획, AI 데이터센터 수요 반영해 목표 상향",
    "summary": "정부가 2040년 최대전력수요 전망을 165GW 로 올렸다.",
    "importance": "nice_to_know", "section": "domestic",
    "topics": ["power_market", "datacenter_ai"], "tags": ["#전력수급"],
}
TARIFF = {
    "hash": "tf", "domain": "news1.kr",
    "title_kr": "순천·구례·보성 제조업계, 산업용 전기료 차등요금제 조기 시행 촉구",
    "summary": "지역 제조업계가 지역별 차등 전기요금제의 조기 시행을 요구했다.",
    "importance": "nice_to_know", "section": "domestic",
    "topics": ["power_market"], "tags": ["#전기요금"],
}
NUCLEAR_FR = {
    "hash": "fr", "domain": "lesechos.fr",
    "title": "Nucléaire : pourquoi la facture des EPR2 d'EDF pourrait grimper",
    "title_kr": "EDF EPR2 건설비 1530억 유로로 상승 가능성",
    "summary": "감사원이 EPR2 6기 비용 재추산을 요구했다.",
    "importance": "nice_to_know", "section": "international", "topics": ["newbuild"],
}
TRUMP_INVEST = {
    "hash": "ti", "domain": "reuters.com",
    "title": "Trump to announce $200 billion of South Korean investment in US",
    "title_kr": "트럼프, 한국 기업의 미국 내 2,000억 달러 투자 계획 발표 예정",
    "summary": "트럼프 대통령이 한국 기업의 대미 투자 패키지를 발표한다.",
    "importance": "must_read", "section": "international", "topics": ["security_trade"],
}


class VerdictTests(unittest.TestCase):
    def test_nuclear_vocabulary_is_enough(self):
        self.assertEqual(topic_gate.assess(NUCLEAR_FR)["verdict"], topic_gate.ON_TOPIC)
        self.assertTrue(topic_gate.has_nuclear_term(
            {"title_kr": "원안위, 신한울 3호기 운영허가 심사 착수"}))
        self.assertTrue(topic_gate.has_nuclear_term(
            {"title": "TRISO-X Completes Vertical Construction of TX-1 Fuel Fabrication Facility"}))

    def test_short_korean_tokens_are_not_substrings(self):
        """'핵' 은 핵심, '고리' 는 연결고리에 걸린다 — 어휘에 넣지 않는다."""
        self.assertFalse(topic_gate.has_nuclear_term(
            {"title_kr": "반도체 핵심 인재 유출 우려, 산업계 연결고리 점검"}))

    def test_power_market_context_without_nuclear_word_stays(self):
        """원자력 낱말이 없어도 한수원 사업환경 축이면 싣는다 — 2,938건 중 1,155건이 이 부류."""
        self.assertEqual(topic_gate.assess(POWER_PLAN)["verdict"], topic_gate.ON_TOPIC)
        self.assertEqual(topic_gate.assess(TARIFF)["verdict"], topic_gate.ADJACENT)

    def test_oil_and_ai_regulation_are_off_topic(self):
        for article in (G7_OIL, OPENAI_PROBE):
            with self.subTest(article=article["hash"]):
                verdict = topic_gate.assess(article)
                self.assertEqual(verdict["verdict"], topic_gate.OFF_TOPIC)
                self.assertFalse(verdict["nuclear_term"])
                self.assertEqual(verdict["level"], "not_required")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        topic_gate.reset()

    def _normalized(self, article):
        return {k: v for k, v in article.items() if k not in ("hash", "domain", "title")}

    def test_off_topic_nice_to_know_becomes_noise_and_is_recorded(self):
        normalized = self._normalized(G7_OIL)
        row = topic_gate.apply(normalized, G7_OIL)
        self.assertEqual(normalized["importance"], "noise")
        self.assertEqual(normalized["importance_llm"], "nice_to_know")
        self.assertEqual(normalized["topic_verdict"], topic_gate.OFF_TOPIC)
        self.assertEqual(row["hash"], "g7")
        self.assertEqual(row["title"], G7_OIL["title_kr"])
        self.assertEqual(topic_gate.DEMOTED, [row])

    def test_must_read_is_never_touched(self):
        """LLM 이 가장 확신한 등급이고, 원전 8기와 한 이슈가 되는 기사가 실제로 있다."""
        normalized = self._normalized(TRUMP_INVEST)
        self.assertIsNone(topic_gate.apply(normalized, TRUMP_INVEST))
        self.assertEqual(normalized["importance"], "must_read")
        self.assertNotIn("importance_llm", normalized)
        self.assertEqual(normalized["topic_verdict"], topic_gate.OFF_TOPIC)

    def test_market_is_left_alone(self):
        article = {**G7_OIL, "importance": "market"}
        normalized = self._normalized(article)
        self.assertIsNone(topic_gate.apply(normalized, article))
        self.assertEqual(normalized["importance"], "market")

    def test_on_topic_only_gets_the_verdict(self):
        normalized = self._normalized(POWER_PLAN)
        self.assertIsNone(topic_gate.apply(normalized, POWER_PLAN))
        self.assertEqual(normalized["importance"], "nice_to_know")
        self.assertEqual(normalized["topic_verdict"], topic_gate.ON_TOPIC)
        self.assertEqual(topic_gate.DEMOTED, [])

    def test_reset_clears_the_run_ledger(self):
        topic_gate.apply(self._normalized(G7_OIL), G7_OIL)
        topic_gate.reset()
        self.assertEqual(topic_gate.DEMOTED, [])


if __name__ == "__main__":
    unittest.main()
