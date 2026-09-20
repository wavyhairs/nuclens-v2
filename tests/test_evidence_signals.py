"""`experiments/evidence_replay/signals` — 앞 감사(§4)에서 찾은 정규화 오류가 실제로 고쳐졌는가."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from experiments.evidence_replay import signals  # noqa: E402


def q(text):
    return signals.quantities(text)


def kinds(text):
    return {k for k, _ in q(text)}


class QuantityNormalizationTests(unittest.TestCase):
    def test_usd_is_not_double_parsed_as_krw(self):
        self.assertEqual(q("앤트로픽, 엔스케일과 450억 달러 규모 컴퓨팅 임대 계약"), {("usd", 45000.0)})
        self.assertEqual(q("미 에너지부, 디아블로 캐년 원전에 2.71억 달러 지원"), {("usd", 271.0)})

    def test_krw_compound_and_thousand(self):
        self.assertEqual(q("HD현대중공업, SMR 공장 신설에 1조 722억원 투자"), {("krw", 10722.0)})
        self.assertEqual(q("우리은행, 고창 해상풍력에 4천억 원 금융 지원"), {("krw", 4000.0)})
        self.assertEqual(q("전남광주, 국비 13조 9009억 확보"), {("krw", 139009.0)})

    def test_rounded_forms_match_within_tolerance(self):
        self.assertTrue(signals.shared_quantities(q("1.07조원 투자"), q("1조 722억원 투자")))

    def test_english_and_korean_dollar_forms_agree(self):
        a, b, c = q("홀텍, 최대 9억 달러 규모 IPO 추진"), q("Holtec plans $900 million IPO"), q("한미 3500억달러 대미투자 협상")
        self.assertEqual(a, b)
        self.assertEqual(c, q("$350 billion US investment"))

    def test_twh_is_energy_not_tonnes(self):
        self.assertEqual(kinds("정부, 2038년 국내 전력소비량 735.1TWh 전망"), {"mwh"})

    def test_yen_is_not_won(self):
        self.assertEqual(q("日 1400조엔 그린전환 시장 열린다"), {("jpy", 14000000.0)})

    def test_labels_are_not_quantities(self):
        self.assertEqual(q("정부, 3대 메가프로젝트 대응 제12차 전력수급기본계획 재정비"), set())
        self.assertEqual(q("이재명 대통령, SMR 등 7대 성장동력 강조"), set())
        self.assertEqual(q("제12차 전기본 7차 토론회 18일 개최"), set())
        self.assertEqual(q("정부, 대미 투자 1호 사업 낙점"), set())

    def test_years_dates_and_unit_ids_are_removed(self):
        self.assertEqual(q("한빛 2호기, 9월 11일 설계수명 만료로 가동 정지"), set())
        self.assertEqual(q("2028년 9월까지 SMR 설치 확정"), set())
        self.assertEqual(q("원안위, 고리 3·4호기 계속운전 심의 착수"), set())

    def test_counts_with_multipliers_and_new_units(self):
        self.assertEqual(q("골드만삭스, 휴머노이드 출하량 전망 650만 대로 상향"), {("n:대", 6500000.0)})
        self.assertIn(("n:개", 13.0), q("미국 DOE, NRIC 런치 패드 대상 13개 프로젝트 선정"))
        self.assertIn(("x", 3.4), q("IAEA, 2060년 세계 원전 설비 용량 3.4배 확대 전망"))
        self.assertIn(("n:기", 8.0), q("한수원-웨스팅하우스, 미국 내 원전 8기 건설 협력 잠정 합의"))

    def test_power_units_normalize_to_mw(self):
        self.assertEqual(q("제12차 전기본 2040년 최대전력 158.4GW 전망"), q("최대전력 158.4기가와트"))


class ActorTests(unittest.TestCase):
    def test_party_pair_is_order_and_script_independent(self):
        forms = ["한수원-웨스팅하우스, 미국 내 원전 8기 건설 협력 잠정 합의", "KHNP-Westinghouse agree on 8 US reactors",
                 "웨스팅하우스와 한수원, 8기 건설 협력", "한국수력원자력·美 웨스팅하우스 협력 프레임워크 준비"]
        self.assertEqual({signals.actors(t) for t in forms}, {frozenset({"khnp", "westinghouse"})})

    def test_kepco_subsidiaries_are_not_kepco(self):
        self.assertNotIn("kepco", signals.actors("한전KDN, 기후산업국제박람회서 에너지 ICT 솔루션 공개"))
        self.assertEqual(signals.actors("한전기술, 한국기계연구원과 해양 SMR 핵심기술 고도화 협약"), frozenset({"kepco-en"}))
        self.assertIn("kepco", signals.actors("한전, 배전선로별 정전 비용 산출 착수"))


if __name__ == "__main__":
    unittest.main()
