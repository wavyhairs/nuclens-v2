"""사람이 나눈 사건의 계보는 원장에 남고, 옛 주소에서 갈라진 쪽으로 가는 길이 선다.

event_identity 는 `identity_split_from` 을 **나뉜 그 빌드에서만** 적는다. 다음 빌드부터
갈라진 쪽은 제 id 를 물려받은 평범한 상속이라, 원장이 기억하지 않으면 옛 주소(이긴
쪽에 그대로 산다)에서 갈라진 쪽을 찾을 길이 하루 만에 사라진다.
"""
import unittest

import issue_ledger
from web import build_data


def _row(issue_id, hashes, split_from="", origin=""):
    return {"issue_id": issue_id, "title": issue_id, "summary": "", "first_seen": "2026-09-24",
            "last_seen": "2026-09-24", "identity_split_from": split_from,
            "identity_origin": origin or ("split" if split_from else "inherited"),
            "related_articles": [{"hash": h, "article_date": "2026-09-24", "member_role": "card"}
                                 for h in hashes]}


class LedgerKeepsLineage(unittest.TestCase):
    def test_split_from_survives_the_next_build(self):
        store = {"issues": {}}
        day1 = [_row("issue-6260", ["a"]), _row("issue-c99b", ["c"], split_from="issue-6260")]
        issue_ledger.merge(store, issue_ledger.catalog_rows(day1), "2026-09-24")
        day2 = [_row("issue-6260", ["a"]), _row("issue-c99b", ["c"])]  # 평범한 상속
        issue_ledger.merge(store, issue_ledger.catalog_rows(day2), "2026-09-25")
        self.assertEqual(store["issues"]["issue-c99b"]["split_from"], "issue-6260")
        self.assertEqual(issue_ledger.split_lineage(store, day2), {"issue-c99b": "issue-6260"})


    def test_inherited_issue_that_lost_articles_is_not_lineage(self):
        """물려받은 이슈는 제 옛 주소가 살아 있다 — 재묶음 잡음을 계보로 쌓지 않는다."""
        store = {"issues": {}}
        rows = [_row("issue-a", ["a"]), _row("issue-b", ["b"], split_from="issue-a", origin="inherited")]
        issue_ledger.merge(store, issue_ledger.catalog_rows(rows), "2026-09-24")
        self.assertEqual(store["issues"]["issue-b"]["split_from"], "")
        self.assertEqual(issue_ledger.split_lineage(store, rows), {})


class CatalogPointsBothWays(unittest.TestCase):
    def test_parent_lists_children_and_child_names_parent(self):
        store = {"issues": {"issue-c99b": {"split_from": "issue-6260"}}}
        catalog = [_row("issue-6260", ["a"]), _row("issue-c99b", ["c"])]
        self.assertEqual(build_data.stamp_split_lineage(catalog, store), 1)
        parent, child = catalog
        self.assertEqual([r["issue_id"] for r in parent["split_children"]], ["issue-c99b"])
        self.assertEqual(child["split_parent"]["issue_id"], "issue-6260")
        self.assertEqual(parent["split_parent"], {})

    def test_dead_parent_is_not_linked(self):
        store = {"issues": {"issue-c99b": {"split_from": "issue-gone"}}}
        catalog = [_row("issue-c99b", ["c"])]
        self.assertEqual(build_data.stamp_split_lineage(catalog, store), 0)
        self.assertEqual(catalog[0]["split_parent"], {})


class AdminCorrectionIndex(unittest.TestCase):
    def test_index_carries_evidence_and_conflicts(self):
        catalog = [{"issue_id": "issue-x", "related_articles": [
            {"hash": "a", "member_role": "card"}, {"hash": "e", "member_role": "evidence"}]}]
        audit = {"overrides": {"approved": ["a--b", "c--d"], "rejected": ["a--b"]}}
        out = build_data.admin_correction_index(catalog, audit)
        self.assertEqual(out["hash_index"]["e"], ["issue-x", "evidence"])
        self.assertEqual(out["override_conflicts"], ["a--b"])
        self.assertEqual([row["pair"] for row in out["manual_approved"]], ["a--b", "c--d"])


if __name__ == "__main__":
    unittest.main()
