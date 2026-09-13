"""장기 스토리 원장 → 화면 계약. **안전장치가 이 파일의 본론이다.**

이 화면은 Beta 고 판정이 자동이라, 잘못 뜨는 것이 안 뜨는 것보다 나쁘다. 그래서
검사의 무게도 "무엇을 그리는가"보다 "언제 안 그리는가"에 있다.
"""

import unittest
from datetime import date, datetime, timedelta, timezone

import thread_web
from event_retrieval import Event

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=KST)


def _event(issue_id, title, first, last, *, briefing_count=1, moved_to=""):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date.fromisoformat(first), last_seen=date.fromisoformat(last),
        units=frozenset(), plants=frozenset(), entities=frozenset(),
        assets=frozenset(), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=briefing_count, raw={"moved_to": moved_to},
    )


EVENTS = [
    _event("issue-a", "한빛 1·2호기 계속운전 청신호", "2026-07-14", "2026-07-16", briefing_count=3),
    _event("issue-b", "한빛 1·2호기 계속운전, 원안위 검토 가속", "2026-07-17", "2026-07-18"),
    _event("issue-c", "한빛 2호기 운영허가 만료로 가동 정지", "2026-09-09", "2026-09-12"),
    _event("issue-d", "홀텍 SMR-300 도입 검토", "2026-08-07", "2026-08-09"),
    _event("issue-e", "홀텍, INL 에 실증 시설 구축", "2026-08-28", "2026-09-01"),
]


def _thread(thread_id, event_ids, *, title="스토리", first="2026-07-14",
            last="2026-09-12", units=(), entities=(), excludes=(), moved_to=""):
    return {
        "thread_id": thread_id, "title": title, "event_ids": list(event_ids),
        "first_seen": first, "last_seen": last, "moved_to": moved_to,
        "units": list(units), "entity_ids": list(entities),
        "identity_origin": "inherited",
        "scope": {"includes": {}, "excludes": {"units": list(excludes)}},
    }


def _store(threads, *, live=None, build=None, generated_at=None):
    entries = {row["thread_id"]: row for row in threads}
    return {
        "version": "thread-ledger-v1",
        "generated_at": (generated_at or NOW.isoformat(timespec="seconds")),
        "live_thread_ids": list(live) if live is not None
                           else sorted(row["thread_id"] for row in threads),
        "build": build if build is not None
                 else {"status": "ok", "candidates": 3000, "failed": 0},
        "threads": entries,
    }


def _payload(store, **kwargs):
    return thread_web.build_payload(store=store, events=EVENTS, milestones=[],
                                    now=NOW, **kwargs)


def _many(count, *, prefix="t"):
    """게이트의 MIN_THREADS 를 넘기기 위한 채움용 스토리."""
    return [_thread(f"{prefix}-{index}", ["issue-a", "issue-b"])
            for index in range(count)]


class LiveMembershipTests(unittest.TestCase):
    def test_dead_threads_keep_their_old_members_so_the_roster_decides(self):
        """원장은 지우지 않는다 — 구성원이 전부 떨어진 스토리도 옛 event_ids 를
        들고 파일에 남아 겉보기에 멀쩡하다. 판정 빌드가 남긴 명단이 가른다."""
        store = _store([_thread("alive", ["issue-a", "issue-b"]),
                        _thread("dead", ["issue-d", "issue-e"])],
                       live=["alive"])
        self.assertEqual([row["thread_id"] for row in thread_web.live_entries(store)],
                         ["alive"])

    def test_absorbed_threads_are_never_live_even_if_the_roster_names_them(self):
        store = _store([_thread("winner", ["issue-a", "issue-b"]),
                        _thread("loser", ["issue-a"], moved_to="winner")],
                       live=["winner", "loser"])
        self.assertEqual([row["thread_id"] for row in thread_web.live_entries(store)],
                         ["winner"])

    def test_an_old_ledger_without_a_roster_falls_back_to_not_absorbed(self):
        """명단이 없는 파일도 읽을 수 있어야 한다 — 판정 빌드를 한 번 더 돌리기
        전까지 화면이 통째로 비면 배포 순서가 강제된다."""
        store = _store([_thread("a", ["issue-a", "issue-b"]),
                        _thread("b", ["issue-d"], moved_to="a")])
        store["live_thread_ids"] = []
        self.assertEqual([row["thread_id"] for row in thread_web.live_entries(store)], ["a"])


class ProjectionTests(unittest.TestCase):
    def test_period_is_measured_from_current_members_not_the_ledger_field(self):
        """원장의 first_seen 은 단조 감소만 하는 값이라(신원이 왕복하지 않게)
        현재 구성원보다 이를 수 있다. 화면의 기간은 지금 들고 있는 사건의 것이다."""
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("story", ["issue-d", "issue-e"], first="2026-01-01")])
        row = next(row for row in _payload(store)["threads"]
                   if row["thread_id"] == "story")
        self.assertEqual(row["first_seen"], "2026-08-07")
        self.assertEqual(row["last_seen"], "2026-09-01")
        self.assertEqual(row["lifespan_days"], 25)

    def test_sub_events_are_ordered_newest_first(self):
        """목록이 `last_seen` 정렬이고 카드 제목도 최신 사건을 거는 화면이다.
        제목 바로 아래 첫 행이 다른 날을 가리키면 어긋난 것으로 읽힌다. 이슈
        상세의 타임라인(`byTimelineOrder`)과도 방향이 같아진다.

        값은 치른다 — '착수 → 보류 → 결정' 이 거꾸로 읽힌다. 훑는 화면에서는
        '지금 어디까지 왔나' 가 먼저라고 보고 고른 쪽이다."""
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("story", ["issue-c", "issue-a", "issue-b"])])
        row = next(row for row in _payload(store)["threads"]
                   if row["thread_id"] == "story")
        self.assertEqual([event["date"] for event in row["events"]],
                         ["2026-09-09", "2026-07-17", "2026-07-14"])
        self.assertEqual(row["briefing_count"], 5)

    def test_the_card_is_named_by_where_the_story_is_now(self):
        """원장의 title 은 앵커(가장 이른 사건)의 제목이고, 앵커가 이른 사건인
        이유는 **id 가 왕복하지 않게** 하려는 것이지 그 제목이 이야기를 잘
        부르기 때문이 아니다. 화면은 지금 상태를 건다 — 원장은 건드리지 않는다."""
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("story", ["issue-a", "issue-b", "issue-c"],
                                  title="한빛 1·2호기 계속운전 청신호")])
        row = next(row for row in _payload(store)["threads"]
                   if row["thread_id"] == "story")
        self.assertEqual(row["title"], "한빛 2호기 운영허가 만료로 가동 정지")
        self.assertEqual(row["origin_title"], "한빛 1·2호기 계속운전 청신호")
        # 원장은 신원 기록이라 그대로다. 이름이 움직여도 주소는 안 움직인다.
        self.assertEqual(store["threads"]["story"]["title"], "한빛 1·2호기 계속운전 청신호")

    def test_a_one_day_cluster_is_not_a_long_term_story(self):
        """이 화면의 이름이 장기 스토리다. 하루 안에 끝난 묶음은 같은 일을
        이틀에 걸쳐 보도한 것이고, 그것은 사건 계층의 중복이지 긴 이야기가 아니다.
        판정을 되돌리지는 않는다 — 목록의 자격만 정한다."""
        same_day = [_event("issue-x", "같은 일 1보", "2026-09-10", "2026-09-10"),
                    _event("issue-y", "같은 일 2보", "2026-09-10", "2026-09-11")]
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("blip", ["issue-x", "issue-y"])])
        payload = thread_web.build_payload(store=store, events=EVENTS + same_day,
                                           milestones=[], now=NOW)
        self.assertNotIn("blip", {row["thread_id"] for row in payload["threads"]})
        # 조용히 사라지지는 않는다 — 몇 개를 걸렀는지 진단에 남는다.
        self.assertEqual(payload["stats"]["short_excluded"], 1)

    def test_absorbed_events_link_to_the_address_that_still_opens(self):
        """원장은 prune 하지 않으므로 `event_retrieval` 이 흡수된 이슈까지 사건으로
        낸다(실측 2026-09-13: 811건 중 219건). 그 id 를 링크로 내보내면 화면에서
        '이 이슈를 찾을 수 없습니다' 로 끝난다 — 흡수된 이슈는 보관 스냅샷을
        받지 못하기 때문이다. 행은 남기고 주소만 현재 이슈로 옮긴다."""
        moved = [_event("issue-m", "한빛 2호기 심사 지연으로 가동 중단",
                        "2026-09-11", "2026-09-12", moved_to="issue-c")]
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("story", ["issue-a", "issue-c", "issue-m"])])
        payload = thread_web.build_payload(store=store, events=EVENTS + moved,
                                           milestones=[], now=NOW)
        row = next(row for row in payload["threads"] if row["thread_id"] == "story")
        # 제목·날짜는 그날의 기록이라 남고, id 만 살아 있는 주소를 가리킨다.
        self.assertEqual([event["date"] for event in row["events"]],
                         ["2026-09-11", "2026-09-09", "2026-07-14"])
        self.assertEqual([event["event_id"] for event in row["events"]],
                         ["issue-c", "issue-c", "issue-a"])
        self.assertEqual(row["events"][0]["title"], "한빛 2호기 심사 지연으로 가동 중단")

    def test_a_chain_of_absorptions_is_followed_to_the_end(self):
        chain = [_event("issue-m", "1보", "2026-09-10", "2026-09-10", moved_to="issue-n"),
                 _event("issue-n", "2보", "2026-09-11", "2026-09-11", moved_to="issue-c")]
        by_id = {event.issue_id: event for event in EVENTS + chain}
        self.assertEqual(thread_web.surviving_id("issue-m", by_id), "issue-c")

    def test_an_absorption_loop_stops_instead_of_hanging(self):
        loop = [_event("issue-m", "1보", "2026-09-10", "2026-09-10", moved_to="issue-n"),
                _event("issue-n", "2보", "2026-09-11", "2026-09-11", moved_to="issue-m")]
        by_id = {event.issue_id: event for event in loop}
        self.assertIn(thread_web.surviving_id("issue-m", by_id), {"issue-m", "issue-n"})

    def test_the_same_headline_under_two_ids_is_one_row_not_two(self):
        """흡수 전후의 항목이 제목까지 같으면 이야기의 두 장면이 아니라 한 장면이
        id 두 개를 입은 것이다. 제목이 다르면 (위 검사처럼) 남긴다."""
        twin = [_event("issue-m", "한빛 2호기 운영허가 만료로 가동 정지",
                       "2026-09-12", "2026-09-12", moved_to="issue-c")]
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("story", ["issue-a", "issue-c", "issue-m"])])
        payload = thread_web.build_payload(store=store, events=EVENTS + twin,
                                           milestones=[], now=NOW)
        row = next(row for row in payload["threads"] if row["thread_id"] == "story")
        self.assertEqual([event["date"] for event in row["events"]],
                         ["2026-09-09", "2026-07-14"])
        self.assertEqual(row["event_count"], 2)

    def test_a_thread_whose_events_vanished_is_dropped(self):
        """원장이 사건보다 앞서간 경우. 그릴 것이 없는 항목을 목록에 남기면
        제목만 있는 빈 카드가 선다."""
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("ghost", ["issue-zzz", "issue-yyy"])])
        ids = {row["thread_id"] for row in _payload(store)["threads"]}
        self.assertNotIn("ghost", ids)

    def test_ids_are_given_names_because_the_reader_has_no_dictionary(self):
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("story", ["issue-a", "issue-b"],
                                  units=["hanbit-1"], entities=["khnp"],
                                  excludes=["hanbit-3"])])
        row = next(row for row in _payload(store)["threads"]
                   if row["thread_id"] == "story")
        self.assertEqual(row["unit_labels"], ["한빛 1호기"])
        self.assertEqual(row["entity_labels"], ["한국수력원자력"])
        self.assertEqual(row["excluded_unit_labels"], ["한빛 3호기"])

    def test_unknown_ids_pass_through_instead_of_becoming_blank(self):
        plants = {"hanbit": "한빛"}
        self.assertEqual(thread_web.unit_label("hanbit-3", plants), "한빛 3호기")
        self.assertEqual(thread_web.unit_label("mystery-2", plants), "mystery-2")
        self.assertEqual(thread_web.unit_label("nounit", plants), "nounit")


class GateTests(unittest.TestCase):
    """언제 화면이 안 뜨는가. 이유는 **전부** 남는다 — 하나만 남기면 다음 사람이
    원인 하나를 고치고 여전히 안 뜨는 화면을 다시 조사한다."""

    def test_a_healthy_ledger_is_shown(self):
        payload = _payload(_store(_many(thread_web.MIN_THREADS)))
        self.assertTrue(payload["visible"])
        self.assertEqual(payload["reasons"], [])
        self.assertEqual(payload["stats"]["threads"], thread_web.MIN_THREADS)

    def test_a_stale_ledger_hides_the_screen(self):
        old = (NOW - timedelta(hours=thread_web.STALE_HOURS + 1)).isoformat()
        payload = _payload(_store(_many(thread_web.MIN_THREADS), generated_at=old))
        self.assertFalse(payload["visible"])
        self.assertIn("stale", payload["reasons"])

    def test_a_ledger_that_never_recorded_a_build_hides_the_screen(self):
        payload = _payload(_store(_many(thread_web.MIN_THREADS), build={}))
        self.assertFalse(payload["visible"])
        self.assertIn("build_unknown", payload["reasons"])

    def test_the_rpd_exhaustion_incident_would_have_been_hidden(self):
        """2026-09-13 실측: 후보 3,068쌍 중 60쌍이 미판정으로 남은 회차. 그 수치는
        겉보기에 최종 결과와 같았지만 정본에 쓰지 않기로 한 회차다."""
        build = {"status": "partial_failure", "candidates": 3068, "failed": 60}
        payload = _payload(_store(_many(thread_web.MIN_THREADS), build=build))
        self.assertFalse(payload["visible"])
        self.assertIn("build_incomplete", payload["reasons"])

    def test_boundary_churn_does_not_trip_the_gate(self):
        """후보 생성은 점수 동률 경계에서 회차마다 몇 쌍씩 흔들린다. 그걸로 화면을
        내리면 안전장치가 상시 켜져 있는 소음이 된다."""
        build = {"status": "no_api_key", "candidates": 3069, "failed": 4}
        payload = _payload(_store(_many(thread_web.MIN_THREADS), build=build))
        self.assertTrue(payload["visible"])
        # 숨길 만큼은 아니어도 온전하지 않았다는 사실은 남는다.
        self.assertTrue(payload["degraded"])

    def test_too_few_stories_is_a_broken_pipeline_not_a_quiet_week(self):
        payload = _payload(_store(_many(thread_web.MIN_THREADS - 1)))
        self.assertFalse(payload["visible"])
        self.assertIn("too_few", payload["reasons"])

    def test_an_empty_ledger_hides_the_screen(self):
        payload = _payload(_store([]))
        self.assertFalse(payload["visible"])
        self.assertIn("no_ledger", payload["reasons"])

    def test_hidden_payloads_carry_no_stories(self):
        """'숨김'이 표시 문제로 내려앉지 않게 한다 — 내보내지 않을 것은 싣지도
        않는다. 파일에 남아 있으면 클라이언트 한 줄이 그것을 그릴 수 있다."""
        payload = _payload(_store(_many(2)))
        self.assertFalse(payload["visible"])
        self.assertEqual(payload["threads"], [])
        self.assertEqual(payload["redirects"], {})

    def test_every_failing_reason_is_reported_not_just_the_first(self):
        old = (NOW - timedelta(hours=thread_web.STALE_HOURS + 1)).isoformat()
        payload = _payload(_store(_many(2), build={}, generated_at=old))
        self.assertEqual(set(payload["reasons"]),
                         {"build_unknown", "stale", "too_few"})


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self._previous = thread_web.os.environ.get(thread_web.SWITCH_ENV)

    def tearDown(self):
        if self._previous is None:
            thread_web.os.environ.pop(thread_web.SWITCH_ENV, None)
        else:
            thread_web.os.environ[thread_web.SWITCH_ENV] = self._previous

    def test_the_kill_switch_wins_over_a_healthy_ledger(self):
        thread_web.os.environ[thread_web.SWITCH_ENV] = "off"
        payload = _payload(_store(_many(thread_web.MIN_THREADS)))
        self.assertFalse(payload["visible"])
        self.assertEqual(payload["reasons"], ["switched_off"])

    def test_forcing_it_on_still_records_why_it_would_have_been_hidden(self):
        """로컬에서 키 없이 화면을 볼 때 쓴다. 이유를 지우면 켜 둔 사람이
        무엇을 무시하고 있는지 모른다."""
        thread_web.os.environ[thread_web.SWITCH_ENV] = "on"
        payload = _payload(_store(_many(2)))
        self.assertTrue(payload["visible"])
        self.assertIn("too_few", payload["reasons"])
        self.assertTrue(payload["threads"])


class ClientHandoffTests(unittest.TestCase):
    def test_hide_after_is_the_second_line_of_defence(self):
        """배포가 멈춰 낡은 파일이 CDN 에 남으면 빌드 시점 판정은 잡을 수 없다.
        화면이 스스로 다시 재도록 유효기한을 같이 싣는다."""
        source = NOW.isoformat(timespec="seconds")
        payload = _payload(_store(_many(thread_web.MIN_THREADS), generated_at=source))
        expected = (NOW + timedelta(hours=thread_web.STALE_HOURS)).isoformat(timespec="seconds")
        self.assertEqual(payload["hide_after"], expected)
        self.assertEqual(payload["source_generated_at"], source)

    def test_absorbed_addresses_redirect_to_the_living_story(self):
        """공유된 링크가 죽지 않게 한다. 이슈 별칭과 같은 계약이다."""
        store = _store(_many(thread_web.MIN_THREADS)
                       + [_thread("winner", ["issue-a", "issue-b"]),
                          _thread("loser", ["issue-a"], moved_to="winner")],
                       live=[f"t-{index}" for index in range(thread_web.MIN_THREADS)]
                            + ["winner"])
        self.assertEqual(_payload(store)["redirects"], {"loser": "winner"})

    def test_the_contract_version_is_declared(self):
        payload = _payload(_store(_many(thread_web.MIN_THREADS)))
        self.assertEqual(payload["version"], thread_web.CONTRACT_VERSION)


if __name__ == "__main__":
    unittest.main()
