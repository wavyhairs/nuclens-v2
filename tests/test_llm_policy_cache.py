import json
import tempfile
import unittest
from pathlib import Path

import llm_cache
import issue_review
import keei_match
import issue_insight
from tests.test_issue_review import FakeClient, candidate, verdict_response
from tests.test_keei import FakeClient as KeeiClient, candidate as keei_candidate
from tests.test_issue_insight import FakeClient as InsightClient, row as insight_row


class FreshnessContractTests(unittest.TestCase):
    def test_prompt_mismatch_is_hard_and_policy_mismatch_is_soft(self):
        entry = {"prompt_version": 2, "generation_policy_fingerprint": "old"}
        self.assertEqual(llm_cache.freshness(entry, 3, "new"), llm_cache.HARD_STALE)
        self.assertEqual(llm_cache.freshness(entry, 2, "new"), llm_cache.SOFT_STALE)
        self.assertEqual(llm_cache.freshness(entry, 2, "old"), llm_cache.FRESH)

    def test_legacy_entry_is_soft_stale_not_hard_invalid(self):
        self.assertEqual(llm_cache.freshness({"prompt_version": 2}, 2, "new"),
                         llm_cache.SOFT_STALE)


class IssueReviewSoftMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "reviews.json"
        issue_review.review_pairs([candidate("p1")], cache_path=self.cache,
                                  client=FakeClient([verdict_response(1, True)]))

    def _make_soft_stale(self):
        raw = json.loads(self.cache.read_text(encoding="utf-8"))
        raw["reviews"]["p1"]["generation_policy_fingerprint"] = "old-policy"
        self.cache.write_text(json.dumps(raw), encoding="utf-8")

    def test_budget_zero_uses_old_verdict_with_zero_calls(self):
        self._make_soft_stale()
        client = FakeClient(raises=True)
        verdicts, stats = issue_review.review_pairs(
            [candidate("p1")], cache_path=self.cache, client=client,
            policy_refresh_budget=0)
        self.assertEqual(verdicts, {"p1": True})
        self.assertEqual(client.calls, [])
        self.assertEqual(stats["policy_refresh_deferred"], 1)

    def test_failed_refresh_keeps_old_verdict(self):
        self._make_soft_stale()
        verdicts, stats = issue_review.review_pairs(
            [candidate("p1")], cache_path=self.cache, client=FakeClient(raises=True),
            policy_refresh_budget=1)
        self.assertEqual(verdicts, {"p1": True})
        self.assertEqual(stats["policy_refreshed"], 0)
        stored = json.loads(self.cache.read_text(encoding="utf-8"))["reviews"]["p1"]
        self.assertTrue(stored["same_event"])
        self.assertEqual(stored["generation_policy_fingerprint"], "old-policy")

    def test_successful_refresh_replaces_old_verdict_and_fingerprint(self):
        self._make_soft_stale()
        verdicts, stats = issue_review.review_pairs(
            [candidate("p1")], cache_path=self.cache,
            client=FakeClient([verdict_response(1, False)]), policy_refresh_budget=1)
        self.assertEqual(verdicts, {"p1": False})
        self.assertEqual(stats["policy_refreshed"], 1)
        stored = json.loads(self.cache.read_text(encoding="utf-8"))["reviews"]["p1"]
        self.assertFalse(stored["same_event"])
        self.assertEqual(stored["generation_policy_fingerprint"],
                         stats["generation_policy_fingerprint"])


class OtherDomainSoftMigrationTests(unittest.TestCase):
    def test_keei_failed_refresh_keeps_old_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keei.json"
            initial = KeeiClient([{"items": [
                {"idx": 0, "same_event": True, "reason": "same"}]}])
            keei_match.match_pairs([keei_candidate(0)], cache_path=path, client=initial)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["matches"]["issue0--abc0"]["generation_policy_fingerprint"] = "old"
            path.write_text(json.dumps(raw), encoding="utf-8")
            verdicts, stats = keei_match.match_pairs(
                [keei_candidate(0)], cache_path=path, client=KeeiClient(raises=True),
                policy_refresh_budget=1)
            self.assertTrue(verdicts["issue0--abc0"])
            self.assertEqual(stats["policy_refreshed"], 0)

    def test_insight_failed_refresh_keeps_old_sentence(self):
        sentence = "다뉴브강 수위 저하로 냉각수 취수가 막혀 예고됐던 전면 정지를 피한 것이다."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "insight.json"
            initial = InsightClient([{"items": [{"idx": 0, "insight": sentence}]}])
            issue_insight.generate([insight_row()], cache_path=path, client=initial)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["insights"]["issue-paks"]["generation_policy_fingerprint"] = "old"
            path.write_text(json.dumps(raw), encoding="utf-8")
            insights, stats = issue_insight.generate(
                [insight_row()], cache_path=path,
                client=InsightClient(raises=RuntimeError("HTTP 429")),
                policy_refresh_budget=1)
            self.assertEqual(insights["issue-paks"], sentence)
            self.assertEqual(stats["policy_refreshed"], 0)


if __name__ == "__main__":
    unittest.main()
