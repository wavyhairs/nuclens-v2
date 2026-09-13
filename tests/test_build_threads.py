"""그림자 빌드의 두 파생물 — 범위(scope)와 다음 관전점."""

import importlib.util
import unittest
from datetime import date
from pathlib import Path

from event_retrieval import Event, Index

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_threads", ROOT / "tools" / "build_threads.py")
build_threads = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_threads)


def _event(issue_id, title, *, units=(), entities=(), facts=None):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date(2026, 3, 1), last_seen=date(2026, 3, 2),
        units=frozenset(units), plants=frozenset(), entities=frozenset(entities),
        assets=frozenset(), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={"facts": facts or {}},
    )


class ScopeTests(unittest.TestCase):
    def test_scope_names_what_is_excluded_not_only_what_is_included(self):
        """제외 조항이 오병합을 막는 실제 재료다 — 포함만 적으면 범위가 아니다."""
        index = Index([
            _event("a1", "고리 2호기 계속운전 신청", units={"kori-2"}),
            _event("a2", "고리 2호기 계속운전 심사", units={"kori-2"}),
            _event("b1", "고리 3호기 계속운전 심사", units={"kori-3"}),
            _event("b2", "고리 4호기 계속운전 심사", units={"kori-4"}),
            _event("c1", "한빛 1호기 정비", units={"hanbit-1"}),
        ])
        scope = build_threads.derive_scope(
            {"event_ids": ["a1", "a2"], "units": ["kori-2"]}, index)
        self.assertEqual(scope["includes"]["units"], ["kori-2"])
        self.assertEqual(scope["excludes"]["units"], ["kori-3", "kori-4"])
        # 다른 발전소는 제외 조항에 넣지 않는다 — 목록이 카탈로그 전체가 된다.
        self.assertNotIn("hanbit-1", scope["excludes"]["units"])

    def test_scope_is_machine_derived(self):
        """사람이 스토리마다 범위를 적는 구조를 만들지 않는다."""
        index = Index([_event("a1", "고리 2호기 계속운전", units={"kori-2"},
                              facts={"event_family": "license_renewal",
                                     "action": "심사 착수"})])
        scope = build_threads.derive_scope(
            {"event_ids": ["a1"], "units": ["kori-2"]}, index)
        self.assertEqual(scope["includes"]["event_families"], ["license_renewal"])
        self.assertEqual(scope["includes"]["actions"], ["심사 착수"])
        self.assertTrue(scope["derived_by"])


class MilestoneTests(unittest.TestCase):
    MILESTONES = [
        {"date": "2026-11-01", "title": "월성 2·3·4호기 계속운전", "label": "설계수명이 만료",
         "units": {"wolsong-2"}, "plants": {"wolsong"}},
        {"date": "2026-12-01", "title": "고리 원전 지역 설명회", "label": "설명회",
         "units": set(), "plants": {"kori"}},
    ]

    def test_unit_match_wins_over_plant_match(self):
        found = build_threads.next_milestone({"units": ["wolsong-2"]}, self.MILESTONES)
        self.assertEqual(found["matched_by"], "unit")
        self.assertEqual(found["date"], "2026-11-01")

    def test_plant_match_is_the_fallback(self):
        found = build_threads.next_milestone({"units": ["kori-2"]}, self.MILESTONES)
        self.assertEqual(found["matched_by"], "plant")
        self.assertEqual(found["date"], "2026-12-01")

    def test_no_match_returns_nothing_instead_of_guessing(self):
        self.assertIsNone(build_threads.next_milestone({"units": []}, self.MILESTONES))
        self.assertIsNone(build_threads.next_milestone({"units": ["hanul-1"]},
                                                       self.MILESTONES))


if __name__ == "__main__":
    unittest.main()
