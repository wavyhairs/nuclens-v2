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
        # 어느 가드가 잡는지는 규칙이 늘면서 바뀔 수 있다. 잠그는 것은 **결과**다:
        # 고리 2호기 묶음과 월성 1호기 묶음이 고리 하나로 이어지지 않는다.
        self.assertEqual(sorted(sorted(group) for group in groups),
                         [["a1", "a2"], ["b1", "b2"]])
        self.assertGreaterEqual(
            sum(value for key, value in stats.items() if key.startswith("blocked_")), 1)

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


class AnchorTests(unittest.TestCase):
    def test_anchor_is_the_earliest_event_even_when_inherited(self):
        """증거 목록의 첫 칸은 사전순이라 묶음이 바뀔 때마다 흔들린다."""
        events = {event.issue_id: event for event in [
            _event("zz", "이른 사건", "2026-03-01"),
            _event("aa", "늦은 사건", "2026-08-01"),
        ]}
        out = thread_identity.resolve([{"zz", "aa"}], events, {"aa": "thread-old"})
        self.assertEqual(out["threads"][0]["anchor_event_id"], "zz")


class TopicVersusStoryTests(unittest.TestCase):
    """주제가 스토리로 자라는 것을 막는 규칙들 — 전부 실측에서 나왔다."""

    def _events(self, rows):
        return {event.issue_id: event for event in rows}

    def test_a_multi_unit_article_cannot_bridge_two_plants(self):
        """실측 오병합의 절반이 이런 기사 하나를 다리로 삼았다.

        "고리 3·4호기 올해, 한빛 1·2호기 내년 계속운전 심사 상정 예정"
        """
        bridge = _event("br", "고리 3·4호기 올해, 한빛 1·2호기 내년 심사 상정",
                        "2026-08-06", units={"kori-3", "kori-4", "hanbit-1", "hanbit-2"})
        events = self._events([
            _event("h1", "한빛 1·2호기 계속운전 검토", "2026-07-14", units={"hanbit-1"}),
            _event("h2", "한빛 1·2호기 원안위 검토 가속", "2026-07-17", units={"hanbit-1"}),
            _event("k1", "고리 3·4호기 계속운전 심의 지연", "2026-07-20", units={"kori-3"}),
            _event("k2", "고리 3·4호기 연내 결론", "2026-08-05", units={"kori-3"}),
            bridge,
        ])
        accepted = [("h1", "h2"), ("k1", "k2"), ("h2", "br"), ("br", "k1"), ("h1", "br")]
        groups, _stats = thread_identity.cluster(events, accepted)
        plants_per_group = [
            {unit.rsplit("-", 1)[0] for event_id in group
             for unit in events[event_id].units if not thread_identity.is_broad(events[event_id])}
            for group in groups
        ]
        for plants in plants_per_group:
            self.assertLessEqual(len(plants), 1, plants)

    def test_an_anchorless_topic_cannot_swallow_an_anchored_story(self):
        """12차 전기본 정책 묶음이 한빛 1·2호기 계속운전을 흡수해 27건이 됐었다.

        둘 다 그 자체로는 멀쩡한 이야기인데 섞이면 둘 다 죽는다 — 독자는 한빛
        계속운전을 찾다가 전력수급계획 토론회를 읽게 된다.
        """
        events = self._events([
            _event("p1", "12차 전기본 원전 반영 혼선", "2026-07-31"),
            _event("p2", "12차 전기본 토론회 개최", "2026-08-16"),
            _event("p3", "12차 전기본 목표수요 상향", "2026-08-26"),
            _event("h1", "한빛 1·2호기 계속운전 검토", "2026-07-14", units={"hanbit-1"}),
            _event("h2", "한빛 2호기 설계수명 만료", "2026-09-09", units={"hanbit-2"}),
        ])
        accepted = [("p1", "p2"), ("p2", "p3"), ("h1", "h2"), ("p3", "h1"), ("p1", "h1")]
        groups, stats = thread_identity.cluster(events, accepted)
        for group in groups:
            anchored = any(events[event_id].units for event_id in group)
            unanchored = any(not events[event_id].units for event_id in group)
            self.assertFalse(anchored and unanchored, sorted(group))
        self.assertGreaterEqual(stats["blocked_anchor_mismatch"], 1)

    def test_a_cluster_without_an_asset_anchor_stops_early(self):
        """대상이 없으면 무엇으로 묶였는지 말할 수 없다."""
        rows = [_event(f"p{i}", f"정책 논의 {i}", "2026-08-01")
                for i in range(thread_identity.MAX_EVENTS_WITHOUT_UNIT + 6)]
        events = self._events(rows)
        accepted = [(rows[0].issue_id, row.issue_id) for row in rows[1:]]
        accepted += [(rows[1].issue_id, row.issue_id) for row in rows[2:]]
        groups, stats = thread_identity.cluster(events, accepted)
        self.assertTrue(all(len(group) <= thread_identity.MAX_EVENTS_WITHOUT_UNIT
                            for group in groups))
        self.assertGreaterEqual(stats["blocked_anchorless"], 1)
