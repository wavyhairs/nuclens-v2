"""장기 스토리의 신원과 오염 방지.

D 가 사건에서 한 일을 스토리에서 다시 한다. 다만 **기본 단위가 기사 해시가 아니라
사건 id** 다 — 사건은 D 가 이미 안정시켜 놓았으므로 그 위에 선다.
"""

import unittest
from datetime import date

import thread_identity
from event_retrieval import Event


def _event(issue_id, title, first, last=None, *, units=(), entities=()):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date.fromisoformat(first),
        last_seen=date.fromisoformat(last or first),
        units=frozenset(units), plants=frozenset(), entities=frozenset(entities),
        assets=frozenset(), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={},
    )


class MintTests(unittest.TestCase):
    def test_thread_ids_live_in_their_own_namespace(self):
        """`issue-abc` 와 `thread-abc` 가 나란히 있으면 사람도 코드도 헷갈린다."""
        thread_id = thread_identity.mint_id("issue-abcdef0123456789")
        self.assertTrue(thread_id.startswith("thread-"))
        self.assertNotIn("abcdef0123456789", thread_id)

    def test_minting_is_deterministic(self):
        self.assertEqual(thread_identity.mint_id("issue-a"),
                         thread_identity.mint_id("issue-a"))
        self.assertNotEqual(thread_identity.mint_id("issue-a"),
                            thread_identity.mint_id("issue-b"))


class ClusterGuardTests(unittest.TestCase):
    def _events(self, rows):
        return {event.issue_id: event for event in rows}

    def test_one_weak_link_does_not_join_two_clusters(self):
        """약한 고리 하나로 서로 다른 사안이 한 스토리로 연쇄 병합되면 안 된다."""
        events = self._events([
            _event("a1", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("a2", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("b1", "월성 1호기 해체 승인", "2026-05-01", units={"wolsong-1"}),
            _event("b2", "월성 1호기 해체 착수", "2026-06-01", units={"wolsong-1"}),
        ])
        accepted = [("a1", "a2"), ("b1", "b2"), ("a2", "b1")]
        groups, stats = thread_identity.cluster(events, accepted)
        self.assertEqual(stats["blocked_scope"], 1)
        self.assertEqual(sorted(sorted(group) for group in groups),
                         [["a1", "a2"], ["b1", "b2"]])

    def test_two_links_do_join_two_clusters(self):
        events = self._events([
            _event("a1", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("a2", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("b1", "고리 2호기 주민 의견수렴", "2026-05-01", units={"kori-2"}),
            _event("b2", "고리 2호기 원안위 심의", "2026-06-01", units={"kori-2"}),
        ])
        accepted = [("a1", "a2"), ("b1", "b2"), ("a2", "b1"), ("a1", "b2")]
        groups, stats = thread_identity.cluster(events, accepted)
        self.assertEqual(len(groups), 1)
        self.assertEqual(stats["blocked_weak_link"], 0)

    def test_unit_conflict_anywhere_in_the_cluster_blocks(self):
        """브리지 예외가 없다 — 묶음 전체를 다시 본다."""
        events = self._events([
            _event("a1", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("a2", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("b1", "고리 3호기 계속운전 심사", "2026-05-01", units={"kori-3"}),
            _event("b2", "고리 3호기 원안위 심의", "2026-06-01", units={"kori-3"}),
        ])
        accepted = [("a1", "a2"), ("b1", "b2"), ("a2", "b1"), ("a1", "b1")]
        groups, stats = thread_identity.cluster(events, accepted)
        self.assertGreaterEqual(stats["blocked_conflict"], 1)
        self.assertEqual(len(groups), 2)

    def test_size_cap_stops_a_thread_from_becoming_a_topic(self):
        rows = [_event(f"e{i}", f"고리 2호기 단계 {i}", "2026-03-01", units={"kori-2"})
                for i in range(thread_identity.MAX_EVENTS_PER_THREAD + 4)]
        events = self._events(rows)
        accepted = [(rows[0].issue_id, row.issue_id) for row in rows[1:]]
        accepted += [(rows[1].issue_id, row.issue_id) for row in rows[2:]]
        groups, stats = thread_identity.cluster(events, accepted)
        self.assertTrue(all(len(group) <= thread_identity.MAX_EVENTS_PER_THREAD
                            for group in groups))
        self.assertGreater(stats["blocked_size"], 0)


class ResolveTests(unittest.TestCase):
    def _events(self, rows):
        return {event.issue_id: event for event in rows}

    def test_single_owner_is_inherited(self):
        events = self._events([
            _event("a1", "고리 2호기 계속운전 신청", "2026-03-01"),
            _event("a2", "고리 2호기 계속운전 심사", "2026-04-01"),
        ])
        out = thread_identity.resolve([{"a1", "a2"}], events, {"a1": "thread-old"})
        self.assertEqual(out["threads"][0]["thread_id"], "thread-old")
        self.assertEqual(out["diagnostics"]["inherited"], 1)

    def test_merge_gives_the_address_to_the_earliest_thread(self):
        """묶음 크기는 늘고 줄지만 최초 관측일은 단조롭다 — D 의 규칙 3 그대로."""
        events = self._events([
            _event("a1", "이른 사건", "2026-03-01"),
            _event("b1", "늦은 사건", "2026-08-01"),
        ])
        out = thread_identity.resolve([{"a1", "b1"}], events,
                                      {"a1": "thread-early", "b1": "thread-late"})
        thread = out["threads"][0]
        self.assertEqual(thread["thread_id"], "thread-early")
        self.assertEqual(thread["merged_from"], ["thread-late"])
        self.assertEqual(out["diagnostics"]["merged_away_ids"], 1)

    def test_split_gives_the_id_to_the_side_holding_more_events(self):
        events = self._events([
            _event("a1", "사건 1", "2026-03-01"),
            _event("a2", "사건 2", "2026-03-05"),
            _event("b1", "사건 3", "2026-04-01"),
        ])
        owners = {"a1": "thread-x", "a2": "thread-x", "b1": "thread-x"}
        out = thread_identity.resolve([{"a1", "a2"}, {"b1", "b1"}], events, owners)
        keeper = [row for row in out["threads"] if row["thread_id"] == "thread-x"]
        self.assertEqual(len(keeper), 1)
        self.assertEqual(sorted(keeper[0]["event_ids"]), ["a1", "a2"])
        self.assertEqual(out["diagnostics"]["split"], 1)

    def test_no_owner_mints(self):
        events = self._events([
            _event("a1", "새 사건", "2026-03-01"),
            _event("a2", "새 후속", "2026-04-01"),
        ])
        out = thread_identity.resolve([{"a1", "a2"}], events, {})
        self.assertEqual(out["diagnostics"]["minted"], 1)
        self.assertTrue(out["threads"][0]["thread_id"].startswith("thread-"))
        # 앵커는 가장 이른 사건이다 — 주소가 흔들리지 않으려면 단조로운 값이어야 한다.
        self.assertEqual(out["threads"][0]["anchor_event_id"], "a1")

    def test_resolution_is_idempotent(self):
        events = self._events([
            _event("a1", "사건", "2026-03-01"),
            _event("a2", "후속", "2026-04-01"),
        ])
        first = thread_identity.resolve([{"a1", "a2"}], events, {})
        owners = {event_id: first["threads"][0]["thread_id"]
                  for event_id in first["threads"][0]["event_ids"]}
        second = thread_identity.resolve([{"a1", "a2"}], events, owners)
        self.assertEqual(first["threads"][0]["thread_id"],
                         second["threads"][0]["thread_id"])
        self.assertEqual(second["diagnostics"]["minted"], 0)


if __name__ == "__main__":
    unittest.main()


class ChainPoisoningTests(unittest.TestCase):
    """고리 하나로 묶음을 키우는 것은 정상이다. 막아야 하는 것은 **연쇄**다."""

    def _events(self, rows):
        return {event.issue_id: event for event in rows}

    def test_a_lone_link_cannot_bridge_two_grown_clusters(self):
        events = self._events([
            _event("a1", "A 사업 협상 개시", "2026-03-01", entities={"alpha"}),
            _event("a2", "A 사업 계약 체결", "2026-04-01", entities={"alpha"}),
            _event("b1", "B 사업 착공", "2026-05-01", entities={"beta"}),
            _event("b2", "B 사업 준공", "2026-06-01", entities={"beta"}),
        ])
        groups, stats = thread_identity.cluster(
            events, [("a1", "a2"), ("b1", "b2"), ("a2", "b1")])
        self.assertEqual(stats["blocked_weak_link"], 1)
        self.assertEqual(sorted(sorted(group) for group in groups),
                         [["a1", "a2"], ["b1", "b2"]])

    def test_shared_scope_lets_a_single_link_grow_the_cluster(self):
        """같은 호기를 말하는 후속 사건 하나가 붙는 것까지 막으면 스토리가 안 자란다."""
        events = self._events([
            _event("a1", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("a2", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("a3", "고리 2호기 원안위 심의", "2026-05-01", units={"kori-2"}),
        ])
        groups, stats = thread_identity.cluster(events, [("a1", "a2"), ("a2", "a3")])
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]), ["a1", "a2", "a3"])
        self.assertEqual(stats["blocked_weak_link"], 0)
