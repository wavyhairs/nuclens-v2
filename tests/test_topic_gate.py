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


# ---- 직접 연결 — 막차 감점의 재료 ------------------------------------------------
#
# 그림자 하루치 순위(2026-10-10~18)가 내렸을 기사와 올렸을 기사에서 옮겼다.

def card(title_kr, summary="", **kw):
    return {"title_kr": title_kr, "title": kw.pop("title", title_kr), "summary": summary,
            "importance": "nice_to_know", **kw}


class DirectLinkTests(unittest.TestCase):
    def test_unrelated_industry_cards_have_no_link(self):
        for row in (
            card("美 a16z, 11억 달러 AI 인프라 펀드 조성…한국 반도체·로봇 투자 모색"),
            card("삼성SDI, 말레이시아 공장에 AI 서버용 원통형 LFP 배터리 생산설비 증설"),
            card("EU, 중국산 하이브리드카 수출 제한 합의…주력 산업 규제 확대 우려"),
            card("산업부, 11차 석유 최고가격 10차 수준으로 동결"),
            card("부산시, 35조 규모 민선9기 시정 청사진 확정"),
        ):
            with self.subTest(row["title_kr"]):
                self.assertEqual(topic_gate.direct_link(row), topic_gate.LINK_NONE)

    def test_nuclear_cards(self):
        for row in (
            card("한전기술, 한수원과 264억원 규모 공급계약 체결"),
            card("덴마크, 핵폐기물 심층처분장 건설을 위한 프레임워크 제안서 제출"),
            card("생활 방사선 결함 제품 10년간 35건 적발…회수율 18%대 저조"),
            card("말레이시아 야당, 261조원 규모 희토류 관리 기구 '리나스' 설립 제안",
                 "희토류 자원 관리 일원화와 원전 건설 로드맵 수립을 포함한 국가 전략 기구"),
        ):
            with self.subTest(row["title_kr"]):
                self.assertEqual(topic_gate.direct_link(row), topic_gate.LINK_NUCLEAR)

    def test_power_cards_survive_spacing_and_english(self):
        for row in (
            card("벤처 글로벌, 가스 발전 설비 최대 16기 공급 기본 계약 체결"),
            card("국회 국정감사 시작…에너지 믹스 및 대미 투자 사업 검증 예고"),
            card("백악관 대변인, 한국의 대미 에너지 투자 패키지 2000억 달러 공식 언급"),
            card("IEA, 아세안 전력망 확충에 2040년까지 3000억 달러 전망"),
            card("Texas regulators pause new grid connections", title="Texas regulators pause new grid connections"),
        ):
            with self.subTest(row["title_kr"]):
                self.assertEqual(topic_gate.direct_link(row), topic_gate.LINK_POWER)

    def test_industry_names_are_nuclear(self):
        """이름만 쓰는 기사 — `카자톰프롬` 은 세계 최대 우라늄 생산사다."""
        for title in ("카자톰프롬, 2027 생산 가이던스 하향", "X-energy, 텍사스 착공",
                      "Kairos, 건설허가 취득", "로사톰, 핀란드 중재 승소", "한울 4호기 재가동",
                      "황이주 울진군수, '울진행복 에너지연금' 정책 추진"):
            with self.subTest(title):
                self.assertEqual(topic_gate.direct_link(card(title)), topic_gate.LINK_NUCLEAR)

    def test_short_place_names_do_not_overmatch(self):
        for title in ("민간 보글보글 축제 개막", "한울타리 나눔 행사", "산업 연결고리 강화"):
            with self.subTest(title):
                self.assertEqual(topic_gate.direct_link(card(title)), topic_gate.LINK_NONE)

    def test_us_energy_permitting_reform_is_linked(self):
        """사용자 지목 2026-10-18 — 원전 인허가 기간을 함께 정하는 법안이다."""
        for title in ("미 상원, 에너지 프로젝트 인허가 간소화 법안 합의",
                      "미국 상원, 인프라 건설 인허가 개혁 법안 논의 진전",
                      "Senate strikes deal on permitting reform"):
            with self.subTest(title):
                self.assertEqual(topic_gate.direct_link(card(title)), topic_gate.LINK_POWER)

    def test_ai_datacenter_permit_relief_stays_unlinked(self):
        """맨 '인허가 간소화'는 AI 데이터센터 특별법 기사도 쓴다."""
        self.assertEqual(
            topic_gate.direct_link(card("정부, AI 데이터센터 특별법으로 인허가 지원 및 수용성 확보 추진",
                                        "데이터센터 인허가 간소화를 추진한다.")),
            topic_gate.LINK_NONE)

    def test_bare_baljeon_is_not_power(self):
        """'발전' 하나는 '경제 발전'에 걸린다."""
        self.assertEqual(
            topic_gate.direct_link(card("정부, 지역 균형 발전 방안 발표")), topic_gate.LINK_NONE)

    def test_curation_topics_are_not_used(self):
        """LLM 주제 태그는 두 번 물으면 59% 만 같다 — 낱말로만 판정한다."""
        row = card("미국 빅테크, 고금리에도 AI 인프라 투자 위해 회사채 발행 급증",
                   topics=["datacenter_ai", "power_market"])
        self.assertEqual(topic_gate.direct_link(row), topic_gate.LINK_NONE)

    def test_annotate_marks_every_item(self):
        rows = [card("한전기술, 한수원과 264억원 규모 공급계약 체결"),
                card("부산시, 35조 규모 민선9기 시정 청사진 확정")]
        topic_gate.annotate_links(rows)
        self.assertEqual([r["topic_link"] for r in rows], ["nuclear", "none"])


if __name__ == "__main__":
    unittest.main()
