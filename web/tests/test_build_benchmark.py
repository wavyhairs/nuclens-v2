from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock


WEB_DIR = Path(__file__).resolve().parents[1]
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import build_data  # noqa: E402


class FrozenClockTests(unittest.TestCase):
    def test_default_clock_is_kst_aware(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(build_data.BUILD_AS_OF_ENV, None)
            current = build_data._build_now()
        self.assertEqual(current.utcoffset(), build_data.KST.utcoffset(None))

    def test_explicit_clock_is_normalized_to_kst(self):
        with mock.patch.dict(
            os.environ, {build_data.BUILD_AS_OF_ENV: "2026-09-06T00:00:00Z"}
        ):
            current = build_data._build_now()
        self.assertEqual(current.isoformat(), "2026-09-06T09:00:00+09:00")

    def test_invalid_clock_fails_loudly(self):
        with mock.patch.dict(os.environ, {build_data.BUILD_AS_OF_ENV: "not-a-date"}):
            with self.assertRaisesRegex(ValueError, build_data.BUILD_AS_OF_ENV):
                build_data._build_now()


class SemanticSignatureTests(unittest.TestCase):
    def _fixture(self):
        card = {
            "hash": "card-a",
            "briefing_date": "2026-09-06",
            "countries": ["KR"],
            "tags": ["#고리1호기"],
            "story_fingerprint": {"assets": ["고리1호기"]},
        }
        evidence = {"hash": "evidence-a", "countries": ["KR"]}
        issues = [{
            "issue_id": "issue-a",
            "representative": card,
            "members": [card],
            "evidence_members": [evidence],
            "match_diagnostics": [{
                "hash": "evidence-a",
                "member_role": "evidence",
                "reference_hash": "card-a",
            }],
        }]
        news = [card, evidence, {"hash": "unattached", "countries": ["US"]}]
        return news, issues

    def test_signature_captures_card_evidence_judgment_and_unattached_state(self):
        news, issues = self._fixture()
        signature = build_data.build_semantic_signature(
            news,
            issues,
            {"card-a--evidence-a": True},
            {"approved": {"manual-a--manual-b"}, "rejected": set()},
        )
        self.assertEqual(signature["cards"][0]["member_hashes"], ["card-a"])
        self.assertEqual(signature["evidence"][0]["reference_hash"], "card-a")
        self.assertTrue(any(row["unattached"] for row in signature["evidence"]))
        self.assertEqual(signature["counts"]["llm_verdicts"], 1)

    def test_volatile_fields_do_not_affect_signature(self):
        news, issues = self._fixture()
        before = build_data.build_semantic_signature(news, issues, {}, {})
        news[0]["generated_at"] = "later"
        issues[0]["generated_at"] = "later"
        after = build_data.build_semantic_signature(news, issues, {}, {})
        self.assertEqual(before["overall_sha256"], after["overall_sha256"])


class BuildLocalCacheTests(unittest.TestCase):
    def tearDown(self):
        build_data._ACTIVE_BUILD_CACHE = None

    def test_issue_similarity_cache_is_exact_and_does_not_mutate_articles(self):
        left = {
            "hash": "left",
            "title_kr": "고리 1호기 계속운전 심사 착수",
            "summary": "계속운전 심사를 시작했다.",
            "tags": ["#고리1호기", "#계속운전"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "고리 1호기 계속운전 심의 시작",
            "summary": "계속운전 심의가 시작됐다.",
            "tags": ["#고리1호기", "#계속운전"],
            "countries": ["KR"],
        }
        expected = build_data.issue_similarity(left, right)
        build_data._ACTIVE_BUILD_CACHE = build_data._BuildLocalCache()
        first = build_data.issue_similarity(left, right)
        second = build_data.issue_similarity(left, right)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(set(left), {"hash", "title_kr", "summary", "tags", "countries"})

    def test_approval_reverse_index_is_symmetric(self):
        reverse = build_data._approval_reverse_index({
            "approved": {"a--b"},
            "llm_approved": {"a--c"},
        })
        self.assertEqual(reverse["a"], {"b", "c"})
        self.assertEqual(reverse["b"], {"a"})
        self.assertEqual(reverse["c"], {"a"})


if __name__ == "__main__":
    unittest.main()
