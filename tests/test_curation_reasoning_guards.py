import unittest

import news_bot


class CurationReasoningGuardTests(unittest.TestCase):
    def test_real_saeul_title_merge_is_separated(self):
        merged = "상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장"
        self.assertEqual(news_bot.separate_curation_headline_events(merged),
                         "상업운전 앞둔 새울 3호기 시운전 중 자동정지")

    def test_single_incident_or_single_extension_is_untouched(self):
        for title in ("새울 3호기 시운전 중 자동정지",
                      "새울 3·4호기 건설사업 사업기간 10개월 연장"):
            with self.subTest(title=title):
                self.assertEqual(news_bot.separate_curation_headline_events(title), title)

    def test_source_supported_causality_is_retained(self):
        value = "폭염 때문에 전력수요가 늘었다."
        self.assertEqual(news_bot.drop_unsupported_causal_interpretation(
            value, "폭염 때문에 전력수요가 늘었다."), value)

    def test_generated_only_causality_is_removed_from_optional_analysis(self):
        self.assertEqual(news_bot.drop_unsupported_causal_interpretation(
            "자동정지 때문에 사업기간이 연장됐다.",
            "자동정지했다. 사업기간도 연장됐다.", "새울"), "")

    def test_normalizer_repairs_saeul_title_but_keeps_two_source_facts(self):
        article = {
            "title": "상업운전 앞둔 새울 3호기 자동정지…사업기간도 10개월 연장",
            "description": "새울 3호기가 자동정지했다. 사업기간도 연장됐다.",
        }
        item = {
            "title_kr": "상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장",
            "summary": "새울 3호기가 자동정지했으며 사업기간도 연장됐다.",
            "detail": "자동정지와 사업기간 변경은 별도 사안이다.",
            "implication": "자동정지 때문에 사업기간이 연장됐다.",
        }
        result = news_bot.normalize_curation_item(item, article, body=article["description"])
        self.assertEqual(result["title_kr"],
                         "상업운전 앞둔 새울 3호기 시운전 중 자동정지")
        self.assertIn("사업기간", result["summary"])
        self.assertEqual(result["implication"], "")


if __name__ == "__main__":
    unittest.main()
