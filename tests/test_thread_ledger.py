"""스토리 관계는 **되돌릴 수 있어야** 한다 — 원본 사건을 건드리지 않은 채로."""

import tempfile
import unittest
from pathlib import Path

import thread_ledger


def _thread(thread_id, event_ids, *, first="2026-03-01", last="2026-04-01"):
    return {"thread_id": thread_id, "title": "스토리", "event_ids": list(event_ids),
            "anchor_event_id": event_ids[0], "first_seen": first, "last_seen": last,
            "units": [], "entity_ids": [], "scope": {},
            "identity_origin": "minted", "identity_evidence": [event_ids[0]]}


class StoreTests(unittest.TestCase):
    def test_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "threads.json"
            rows = [_thread("thread-a", ["e1", "e2"])]
            first = thread_ledger.run(rows, [], path=path)
            second = thread_ledger.run(rows, [], path=path)
            self.assertEqual(first["counts"]["added"], 1)
            self.assertEqual(second["counts"]["added"], 0)
            self.assertEqual(second["counts"]["total"], 1)

    def test_first_seen_only_moves_earlier(self):
        """단조로운 값이라야 주소가 왕복하지 않는다 — D 의 규칙 3 과 같은 이유."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "threads.json"
            thread_ledger.run([_thread("thread-a", ["e1"], first="2026-03-01")], [], path=path)
            result = thread_ledger.run(
                [_thread("thread-a", ["e1"], first="2026-08-01")], [], path=path)
            self.assertEqual(result["store"]["threads"]["thread-a"]["first_seen"],
                             "2026-03-01")

    def test_owner_index_maps_each_event_to_one_thread(self):
        store = {"threads": {
            "thread-a": {"thread_id": "thread-a", "event_ids": ["e1", "e2"], "moved_to": ""},
            "thread-b": {"thread_id": "thread-b", "event_ids": ["e3"], "moved_to": "thread-a"},
        }}
        owners = thread_ledger.owner_index(store)
        self.assertEqual(owners, {"e1": "thread-a", "e2": "thread-a"})


class RelationTests(unittest.TestCase):
    def test_every_relation_carries_who_decided_and_why(self):
        record = thread_ledger.relation(
            "issue-1", "thread-a", "attach", reason="판정이 같은 스토리로 보았다",
            evidence=["issue-1"], method="thread_judge", version="thread-judge-v1")
        for field in ("event_id", "thread_id", "decision", "reason", "evidence",
                      "decided_at", "method", "version"):
            self.assertIn(field, record)

    def test_unknown_decisions_are_refused(self):
        with self.assertRaises(ValueError):
            thread_ledger.relation("e", "t", "obliterate", reason="",
                                   method="m", version="v")

    def test_history_is_capped_but_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "threads.json"
            records = [thread_ledger.relation(f"e{i}", "thread-a", "attach", reason="",
                                              method="m", version="v")
                       for i in range(thread_ledger.MAX_RELATIONS_PER_THREAD + 10)]
            result = thread_ledger.run([_thread("thread-a", ["e1"])], records, path=path)
            kept = result["store"]["threads"]["thread-a"]["relations"]
            self.assertEqual(len(kept), thread_ledger.MAX_RELATIONS_PER_THREAD)
            self.assertEqual(kept[-1]["event_id"],
                             f"e{thread_ledger.MAX_RELATIONS_PER_THREAD + 9}")


class AliasTests(unittest.TestCase):
    def test_chains_are_followed_and_loops_stop(self):
        store = {"threads": {
            "thread-a": {"thread_id": "thread-a", "moved_to": "thread-b"},
            "thread-b": {"thread_id": "thread-b", "moved_to": "thread-c"},
            "thread-c": {"thread_id": "thread-c", "moved_to": ""},
            "thread-x": {"thread_id": "thread-x", "moved_to": "thread-y"},
            "thread-y": {"thread_id": "thread-y", "moved_to": "thread-x"},
        }}
        self.assertEqual(thread_ledger.alias_target(store, "thread-a"), "thread-c")
        self.assertIn(thread_ledger.alias_target(store, "thread-x"),
                      {"thread-x", "thread-y"})

    def test_redirects_only_point_at_live_threads(self):
        store = {"threads": {
            "thread-a": {"thread_id": "thread-a", "moved_to": "thread-b"},
            "thread-b": {"thread_id": "thread-b", "moved_to": ""},
            "thread-z": {"thread_id": "thread-z", "moved_to": "thread-gone"},
        }}
        self.assertEqual(thread_ledger.redirects(store, {"thread-b"}),
                         {"thread-a": "thread-b"})


if __name__ == "__main__":
    unittest.main()
