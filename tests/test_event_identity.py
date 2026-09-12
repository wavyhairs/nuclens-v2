"""`event_identity` — 원장에서 신원을 물려받는 규칙의 계약."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import event_identity  # noqa: E402
import issue_ledger  # noqa: E402


def article(article_hash, day="2026-09-01"):
    return {"hash": article_hash, "article_date": day, "briefing_date": day}


def cluster(issue_id, members, evidence=(), representative=None):
    return {
        "issue_id": issue_id,
        "members": list(members),
        "evidence_members": list(evidence),
        "representative": representative or (members[0] if members else {}),
    }


def store(entries):
    return {"issues": {row["issue_id"]: row for row in entries}}


def entry(issue_id, hashes, first_seen="2026-08-01", last_seen="2026-09-01"):
    return {
        "issue_id": issue_id,
        "hashes": list(hashes),
        "first_seen": first_seen,
        "last_seen": last_seen,
    }


class OwnerIndexTests(unittest.TestCase):
    def test_maps_each_hash_to_its_issue(self):
        index = event_identity.owner_index(store([
            entry("issue-a", ["h1", "h2"]),
            entry("issue-b", ["h3"]),
        ]))
        self.assertEqual(index, {"h1": "issue-a", "h2": "issue-a", "h3": "issue-b"})

    def test_most_recent_holder_wins_when_an_article_moved(self):
        """재클러스터링으로 기사가 옮겨 가면 원장 두 항목에 같은 해시가 남는다."""
        index = event_identity.owner_index(store([
            entry("issue-old", ["h1"], last_seen="2026-08-20"),
            entry("issue-new", ["h1"], last_seen="2026-09-01"),
        ]))
        self.assertEqual(index["h1"], "issue-new")

    def test_equal_timestamps_are_dropped_rather_than_guessed(self):
        index = event_identity.owner_index(store([
            entry("issue-a", ["h1"], last_seen="2026-09-01"),
            entry("issue-b", ["h1"], last_seen="2026-09-01"),
        ]))
        self.assertNotIn("h1", index)

    def test_ignores_malformed_entries_without_raising(self):
        index = event_identity.owner_index({"issues": {"issue-a": "not a dict", "": {}}})
        self.assertEqual(index, {})


class ClusterHashTests(unittest.TestCase):
    def test_story_members_are_not_identity_evidence(self):
        """수집 dedup 의 원본 묶음은 여러 이슈가 공유한다(실측 120건)."""
        issue = cluster("issue-a", [article("h1")])
        issue["members"][0]["story_members"] = [{"hash": "shared-1"}]
        issue["story_members"] = [{"hash": "shared-2"}]
        self.assertEqual(event_identity.cluster_hashes(issue), ["h1"])

    def test_includes_card_and_evidence_members(self):
        issue = cluster("issue-a", [article("h1")], evidence=[article("h2")])
        self.assertEqual(set(event_identity.cluster_hashes(issue)), {"h1", "h2"})


class ResolveTests(unittest.TestCase):
    def test_mints_when_the_ledger_knows_nothing(self):
        issues = [cluster("issue-fresh", [article("h9")])]
        diagnostics = event_identity.resolve(issues, store([entry("issue-a", ["h1"])]))
        self.assertEqual(issues[0]["issue_id"], "issue-fresh")
        self.assertEqual(issues[0]["identity_origin"], event_identity.ORIGIN_MINTED)
        self.assertEqual(diagnostics["minted"], 1)

    def test_inherits_the_prior_id_even_when_the_cluster_reformed(self):
        """묶음의 첫 멤버가 바뀌어 임시 id 가 달라져도 주소는 유지된다."""
        issues = [cluster("issue-h2", [article("h2"), article("h1")])]
        diagnostics = event_identity.resolve(issues, store([entry("issue-h1", ["h1"])]))
        self.assertEqual(issues[0]["issue_id"], "issue-h1")
        self.assertEqual(issues[0]["identity_origin"], event_identity.ORIGIN_INHERITED)
        self.assertEqual(issues[0]["identity_evidence"]["shared_hashes"], 1)
        self.assertEqual(diagnostics["inherited"], 1)

    def test_merge_keeps_the_oldest_id_and_records_the_absorbed_one(self):
        issues = [cluster("issue-tmp", [article("h1"), article("h2")])]
        event_identity.resolve(issues, store([
            entry("issue-young", ["h1"], first_seen="2026-08-20"),
            entry("issue-old", ["h2"], first_seen="2026-08-02"),
        ]))
        self.assertEqual(issues[0]["issue_id"], "issue-old")
        self.assertEqual(issues[0]["identity_origin"], event_identity.ORIGIN_MERGED)
        self.assertEqual(issues[0]["identity_merged_from"], ["issue-young"])

    def test_merge_ignores_cluster_size_and_uses_first_seen(self):
        """큰 쪽이 이기면 묶음이 늘고 줄 때 id 가 왕복한다."""
        issues = [cluster("issue-tmp", [article("h1"), article("h2"), article("h3")])]
        event_identity.resolve(issues, store([
            entry("issue-big", ["h2", "h3"], first_seen="2026-08-20"),
            entry("issue-old", ["h1"], first_seen="2026-08-02"),
        ]))
        self.assertEqual(issues[0]["issue_id"], "issue-old")

    def test_split_gives_the_id_to_the_larger_claim(self):
        issues = [
            cluster("issue-x", [article("h1"), article("h2")]),
            cluster("issue-y", [article("h3")]),
        ]
        diagnostics = event_identity.resolve(
            issues, store([entry("issue-prior", ["h1", "h2", "h3"])])
        )
        self.assertEqual(issues[0]["issue_id"], "issue-prior")
        self.assertEqual(issues[1]["issue_id"], "issue-y")
        self.assertEqual(issues[1]["identity_origin"], event_identity.ORIGIN_SPLIT)
        self.assertEqual(issues[1]["identity_split_from"], "issue-prior")
        self.assertEqual(diagnostics["split"], 1)

    def test_split_tie_is_broken_by_the_earliest_article(self):
        issues = [
            cluster("issue-late", [article("h2", "2026-09-05")]),
            cluster("issue-early", [article("h1", "2026-08-10")]),
        ]
        event_identity.resolve(issues, store([entry("issue-prior", ["h1", "h2"])]))
        self.assertEqual(issues[1]["issue_id"], "issue-prior")
        self.assertEqual(issues[0]["identity_origin"], event_identity.ORIGIN_SPLIT)

    def test_never_assigns_one_id_to_two_clusters(self):
        issues = [
            cluster("issue-x", [article("h1")]),
            cluster("issue-y", [article("h2")]),
            cluster("issue-z", [article("h3")]),
        ]
        event_identity.resolve(issues, store([entry("issue-prior", ["h1", "h2", "h3"])]))
        ids = [issue["issue_id"] for issue in issues]
        self.assertEqual(len(set(ids)), len(ids))

    def test_is_deterministic(self):
        def run():
            issues = [
                cluster("issue-x", [article("h1"), article("h4")]),
                cluster("issue-y", [article("h2"), article("h3")]),
            ]
            event_identity.resolve(issues, store([
                entry("issue-p", ["h1", "h2"], first_seen="2026-08-03"),
                entry("issue-q", ["h3", "h4"], first_seen="2026-08-03"),
            ]))
            return [issue["issue_id"] for issue in issues]

        self.assertEqual(run(), run())

    def test_does_not_change_cluster_shape(self):
        members = [article("h1"), article("h2")]
        issues = [cluster("issue-tmp", members)]
        before = [dict(member) for member in issues[0]["members"]]
        representative = issues[0]["representative"]
        event_identity.resolve(issues, store([entry("issue-prior", ["h1"])]))
        self.assertEqual(issues[0]["members"], before)
        self.assertIs(issues[0]["representative"], representative)

    def test_broken_store_falls_back_without_raising(self):
        issues = [cluster("issue-x", [article("h1")])]
        diagnostics = event_identity.resolve(issues, {"issues": None})
        self.assertEqual(issues[0]["issue_id"], "issue-x")
        self.assertEqual(diagnostics["clusters"], 1)

    def test_empty_input(self):
        self.assertEqual(event_identity.resolve([], store([]))["clusters"], 0)


class LedgerHashAccumulationTests(unittest.TestCase):
    """원장이 해시를 덮으면 다음 빌드가 같은 사건을 못 알아본다."""

    def test_merge_accumulates_hashes_across_builds(self):
        state = issue_ledger.load_store(Path("does-not-exist.json"))
        rows_day1 = issue_ledger.catalog_rows([{
            "issue_id": "issue-a",
            "title": "t", "summary": "s", "region": "국내",
            "first_seen": "2026-09-01", "last_seen": "2026-09-01",
            "related_articles": [{"hash": "h1"}, {"hash": "h2"}],
        }])
        issue_ledger.merge(state, rows_day1, "2026-09-01")
        # 다음 빌드에서 h2 가 안 붙었다 — 부착은 빌드마다 흔들린다.
        rows_day2 = issue_ledger.catalog_rows([{
            "issue_id": "issue-a",
            "title": "t", "summary": "s", "region": "국내",
            "first_seen": "2026-09-01", "last_seen": "2026-09-02",
            "related_articles": [{"hash": "h1"}, {"hash": "h3"}],
        }])
        issue_ledger.merge(state, rows_day2, "2026-09-02")
        self.assertEqual(state["issues"]["issue-a"]["hashes"], ["h1", "h2", "h3"])

    def test_accumulation_respects_the_cap(self):
        state = {"issues": {}}
        first = [f"h{n}" for n in range(issue_ledger.MAX_HASHES)]
        issue_ledger.merge(state, [{
            "issue_id": "issue-a", "title": "t", "summary": "s", "region": "",
            "first_seen": "2026-09-01", "last_seen": "2026-09-01",
            "article_count": 0, "briefing_count": 0, "topics": [],
            "hashes": first,
        }], "2026-09-01")
        issue_ledger.merge(state, [{
            "issue_id": "issue-a", "title": "t", "summary": "s", "region": "",
            "first_seen": "2026-09-01", "last_seen": "2026-09-02",
            "article_count": 0, "briefing_count": 0, "topics": [],
            "hashes": ["brand-new"],
        }], "2026-09-02")
        kept = state["issues"]["issue-a"]["hashes"]
        self.assertEqual(len(kept), issue_ledger.MAX_HASHES)
        self.assertEqual(kept[0], "h0")  # 최초 기사가 신원 앵커로 남는다

    def test_merge_stamps_the_write_day(self):
        """`last_seen` 은 여러 이슈가 같은 값을 가져 소유권을 못 가른다."""
        state = {"issues": {}}
        row = {
            "issue_id": "issue-a", "title": "t", "summary": "s", "region": "",
            "first_seen": "2026-09-01", "last_seen": "2026-09-01",
            "article_count": 0, "briefing_count": 0, "topics": [], "hashes": ["h1"],
        }
        issue_ledger.merge(state, [row], "2026-09-01")
        self.assertEqual(state["issues"]["issue-a"]["last_written"], "2026-09-01")
        issue_ledger.merge(state, [row], "2026-09-05")
        self.assertEqual(state["issues"]["issue-a"]["last_written"], "2026-09-05")

    def test_owner_index_prefers_the_more_recent_write(self):
        state = {"issues": {
            "issue-old": {"issue_id": "issue-old", "hashes": ["h1"],
                          "last_seen": "2026-09-09", "last_written": "2026-09-01"},
            "issue-new": {"issue_id": "issue-new", "hashes": ["h1"],
                          "last_seen": "2026-09-02", "last_written": "2026-09-12"},
        }}
        # last_seen 으로 보면 issue-old 가 이긴다 — 그것이 옛 기준의 오답이었다.
        self.assertEqual(event_identity.owner_index(state)["h1"], "issue-new")


if __name__ == "__main__":
    unittest.main()


class FingerprintEventDateTests(unittest.TestCase):
    """지문의 사건일은 게이트를 안 지난다 — 접근자가 연도 오기를 막는다."""

    def setUp(self):
        import story_fingerprint
        self.sf = story_fingerprint

    def test_event_date_is_not_an_identity_axis(self):
        """축이 되면 26.6% 가 틀린 값이 병합 판정에 들어간다."""
        self.assertNotIn("event_date", self.sf.AXES)
        for keys, _weight in self.sf.AXES.values():
            self.assertNotIn("event_date", keys)

    def test_two_year_slip_is_rejected(self):
        self.assertEqual(
            self.sf.reported_event_date(
                {"event_date": "2024-08-14"}, {"article_date": "2026-08-16"}),
            "")

    def test_same_year_passes(self):
        self.assertEqual(
            self.sf.reported_event_date(
                {"event_date": "2026-08-12"}, {"article_date": "2026-08-16"}),
            "2026-08-12")

    def test_adjacent_year_passes_for_announced_schedules(self):
        self.assertEqual(
            self.sf.reported_event_date(
                {"event_date": "2027-03-01"}, {"article_date": "2026-12-20"}),
            "2027-03-01")

    def test_month_precision_survives(self):
        self.assertEqual(
            self.sf.reported_event_date(
                {"event_date": "2026-08"}, {"article_date": "2026-08-18"}),
            "2026-08")

    def test_missing_or_malformed_values_are_empty(self):
        for fingerprint, article in (
            ({}, {"article_date": "2026-08-16"}),
            ({"event_date": ""}, {"article_date": "2026-08-16"}),
            ({"event_date": "언젠가"}, {"article_date": "2026-08-16"}),
            ({"event_date": "2026-08-12"}, {}),
            ("not a dict", {"article_date": "2026-08-16"}),
        ):
            self.assertEqual(self.sf.reported_event_date(fingerprint, article), "")
