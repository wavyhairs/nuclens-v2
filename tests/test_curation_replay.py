import json
import unittest
from pathlib import Path

import news_bot
from tools import curation_replay


FIXTURE = Path("tests/fixtures/gemini_reasoning/curation_gold.json")


class CurationReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = {case["human_label"]: case for case in payload["cases"]
                     if case.get("human_label") in {"PASS", "REPAIR", "BLOCK"}}

    def test_pass_repair_and_block_cases_use_production_request_without_gold_leakage(self):
        for label in ("PASS", "REPAIR", "BLOCK"):
            with self.subTest(label=label):
                case = self.cases[label]
                system, message = curation_replay.request_for_case(case)
                self.assertEqual(system, news_bot.CURATION_SYSTEM_PROMPT + news_bot.BATCH_SUFFIX)
                self.assertIn(case["source_input"]["title"], message)
                self.assertNotIn(json.dumps(case["current_output"], ensure_ascii=False), message)
                self.assertNotIn(label, message)

    def test_existing_gold_cannot_score_a_new_generation(self):
        for label in ("PASS", "REPAIR", "BLOCK"):
            row = curation_replay.readiness(self.cases[label])
            self.assertFalse(row["can_score"])
            self.assertEqual(row["existing_human_label_scope"], "current_output_only")
            self.assertIn("REPLAY_OUTPUT_HUMAN_LABEL_MISSING", row["reasons"])
            self.assertIn("SOURCE_DESCRIPTION_OR_BODY_MISSING", row["reasons"])

    def test_production_parser_and_normalizer_are_reused(self):
        case = self.cases["REPAIR"]
        article = curation_replay.article_from_case(case)
        raw_item = {**case["current_output"], "id": article["hash"][:8]}
        replay_value, replay_errors = curation_replay.parse_case_response(
            case, {"items": [raw_item]})
        valid, failures = news_bot.parse_curation_batch_response(
            {"items": [raw_item]}, [article])
        self.assertEqual(replay_value, valid.get(article["hash"], {}))
        self.assertEqual(replay_errors, failures.get(article["hash"], []))
        # The production normalizer repairs the known Saeul merged headline.
        normalized = news_bot.normalize_curation_item(raw_item, article)
        self.assertNotIn("사업기간 연장", normalized["title_kr"])

    def test_policy_fingerprint_changes_with_reasoning(self):
        case = self.cases["PASS"]
        self.assertNotEqual(
            curation_replay.policy_fingerprint(case, "unspecified"),
            curation_replay.policy_fingerprint(case, "level:high"))


if __name__ == "__main__":
    unittest.main()
