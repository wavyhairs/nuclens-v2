import json
import unittest
from pathlib import Path

import dedup
import issue_review
import keei_match
from tools import identity_replay


class IdentityReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(Path(
            "tests/fixtures/gemini_reasoning/identity_candidates.json"
        ).read_text(encoding="utf-8"))
        cls.case = next(case for case in payload["cases"] if case.get("human_label"))

    def test_issue_review_reuses_production_prompt_builder_and_parser(self):
        system, user, options = identity_replay.request("issue_review", self.case)
        self.assertEqual(system, issue_review.SYSTEM_PROMPT)
        self.assertEqual(user, issue_review.build_user_message([identity_replay._pair(self.case)]))
        self.assertEqual(options["model_profile"], "issue_review")
        self.assertEqual(identity_replay.parse(
            "issue_review", {"items": [{"idx": 0, "same_event": True}]}), "MERGE")
        self.assertIsNone(identity_replay.parse("issue_review", {"items": []}))

    def test_keei_adapter_requires_role_specific_input(self):
        status = identity_replay.readiness("keei_match", self.case)
        self.assertFalse(status["ready_for_live_replay"])
        self.assertIn("KEEI_ROLE_SPECIFIC_INPUT_MISSING", status["reasons"])
        self.assertEqual(identity_replay.parse(
            "keei_match", {"items": [{"idx": 0, "same_event": False}]}), "SEPARATE")

    def test_dedup_adapters_reuse_production_prompt_and_parser(self):
        system, user, _ = identity_replay.request("dedup", self.case)
        self.assertEqual(system, dedup.ARTICLE_STORY_PROMPT)
        self.assertIn("TITLE_KR:", user)
        self.assertEqual(identity_replay.parse(
            "dedup", {"groups": [{"indices": [0, 1], "relation": "merge"}]}), "MERGE")
        self.assertEqual(identity_replay.parse(
            "dedup_final", {"groups": [{"indices": [0]}, {"indices": [1]}]}),
            "SEPARATE")

    def test_gold_and_prior_llm_cache_never_enter_requests(self):
        for profile in ("issue_review", "dedup", "dedup_final", "dedup_legacy"):
            _system, user, _options = identity_replay.request(profile, self.case)
            self.assertNotIn("human_label", user)
            self.assertNotIn("prior_llm_verdict_not_gold", user)
            self.assertNotIn("cached_review_not_gold", user)

    def test_current_fixture_is_not_claimed_as_profile_replay_ready(self):
        for profile in identity_replay.PROFILES:
            with self.subTest(profile=profile):
                self.assertFalse(identity_replay.readiness(
                    profile, self.case)["ready_for_live_replay"])


if __name__ == "__main__":
    unittest.main()
