"""게이트 기억 — 한 번 통과한 고리는 흔들리는 입력 때문에 다시 끊기지 않는다.

2026-09-24 실측이 이 파일의 존재 이유다. 한·미·일 SMR 스토리(사건 2건)의 판정은
`same_thread/stage_progress` 그대로였는데, 기사 3건이 붙으며 대표 기사가 바뀌어
facts.assets 'SMR' → '' 가 됐고, 공유 좁은 대상이 사라진 채 어휘 점수 1.618 < 3.0
이라 `thread_evidence.gate` 가 `generic_scope_only` 로 고리를 끊었다. 스토리가
live 명단에서 빠지고(97→96) 카드 재투영이 사이트에 있던 thread_id 를 빈칸으로
바꿔 그날 스토리 카드가 없었다. 같은 날 live 스토리 105개 중 39개가 같은 조건이었다.

원칙은 `sticky_pairs` 와 같다 — 이미 답을 아는 것은 다시 계산하지 않는다. 기억은
살리는 방향으로만 작용하므로 정책 지문을 같이 적어, 문턱을 바꾸는 날 전량 재심사가
되게 한다.
"""
import importlib.util
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import thread_evidence
import thread_judge
from event_retrieval import Event

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_threads", ROOT / "tools" / "build_threads.py")
build_threads = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_threads)


def _event(issue_id, title, *, assets=()):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date(2026, 8, 23), last_seen=date(2026, 9, 22),
        units=frozenset(), plants=frozenset(), entities=frozenset(),
        assets=frozenset(assets), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={},
    )


A = _event("issue-1779", "SMR 시장, 한미일 분업 모델", assets={"smr"})
B_YESTERDAY = _event("story-831a", "한미일, 제3국 SMR 배치 합의", assets={"smr"})
B_TODAY = _event("story-831a", "미국, 한·일과 제3국 SMR 이행 계획", assets=())
KEY = thread_judge.pair_id(A.issue_id, B_TODAY.issue_id)
SAME = {"verdict": "same_thread", "relationship": "stage_progress",
        "prompt_version": thread_judge.PROMPT_VERSION,
        "reviewed_at": "2026-09-21T19:56:41+00:00"}


def _pair(left, right, lexical):
    return {"key": KEY, "left": left, "right": right,
            "signals": {"lexical": lexical}}


class GateMemoryTests(unittest.TestCase):
    def test_the_incident_is_reproduced_without_memory(self):
        """어제는 자산 공유로 통과, 오늘은 자산이 비어 어휘 1.618 로 끊긴다."""
        ok, _ = thread_evidence.gate(SAME, A, B_YESTERDAY, {"lexical": 1.618})
        self.assertTrue(ok)
        ok, reason = thread_evidence.gate(SAME, A, B_TODAY, {"lexical": 1.618})
        self.assertFalse(ok)
        self.assertEqual(reason, thread_evidence.REJECT_GENERIC_SCOPE)

    def test_a_pass_is_recorded_and_survives_the_input_drift(self):
        """어제 통과가 캐시 항목에 적히면, 오늘 자산이 비어도 고리가 선다."""
        cache = {KEY: dict(SAME)}
        accepted, _, _, stats = build_threads.build_edges(
            [_pair(A, B_YESTERDAY, 1.618)], {KEY: SAME}, {}, cache=cache)
        self.assertEqual(accepted, [(A.issue_id, B_TODAY.issue_id)])
        self.assertEqual(stats["gate_recorded"], 1, "통과가 캐시 항목에 적힌다")
        self.assertEqual(cache[KEY]["gate"]["policy"], thread_evidence.policy_fingerprint())

        accepted, _, _, stats = build_threads.build_edges(
            [_pair(A, B_TODAY, 1.618)], {KEY: SAME}, {}, cache=cache)
        self.assertEqual(accepted, [(A.issue_id, B_TODAY.issue_id)], "오늘 값으로 다시 끊지 않는다")
        self.assertEqual(stats["links_remembered"], 1)
        self.assertEqual(stats.get("gate_recorded", 0), 0, "이미 적힌 기록은 다시 쓰지 않는다")

    def test_without_memory_the_link_is_still_gated(self):
        """기억이 없으면 게이트는 예전 그대로 오늘 값으로 심사한다 — 오병합 방어 유지."""
        accepted, _, _, stats = build_threads.build_edges(
            [_pair(A, B_TODAY, 1.618)], {KEY: SAME}, {}, cache={KEY: dict(SAME)})
        self.assertEqual(accepted, [])
        self.assertEqual(stats["gated_generic_scope_only"], 1)

    def test_memory_only_counts_for_a_same_thread_verdict(self):
        """판정이 바뀌면(캐시 폐기 뒤 different_thread) 기록은 아무 힘이 없다."""
        entry = {**SAME, "verdict": "different_thread", "method": "cache",
                 "gate": {"passed": True, "policy": thread_evidence.policy_fingerprint()}}
        accepted, negative, _, _ = build_threads.build_edges(
            [_pair(A, B_TODAY, 1.618)], {KEY: entry}, {}, cache={KEY: entry})
        self.assertEqual(accepted, [])
        self.assertIn(A.issue_id, negative, "거부권으로만 작용한다")

    def test_changing_a_threshold_reexamines_every_remembered_link(self):
        """정책 지문이 다르면 기억을 무시한다 — 문턱을 올린 날 옛 고리가 남지 않는다."""
        cache = {KEY: {**SAME, "gate": {"passed": True,
                                        "policy": thread_evidence.policy_fingerprint()}}}
        with mock.patch.object(thread_evidence, "MIN_LEXICAL_WITHOUT_SCOPE", 4.0):
            self.assertFalse(thread_evidence.remembered_pass(cache[KEY]))
            accepted, _, _, stats = build_threads.build_edges(
                [_pair(A, B_TODAY, 1.618)], {KEY: SAME}, {}, cache=cache)
        self.assertEqual(accepted, [])
        self.assertEqual(stats["gated_generic_scope_only"], 1)

    def test_policy_fingerprint_follows_every_threshold(self):
        base = thread_evidence.policy_fingerprint()
        with mock.patch.object(thread_evidence, "MIN_LEXICAL_WITHOUT_SCOPE", 3.5):
            self.assertNotEqual(base, thread_evidence.policy_fingerprint())
        with mock.patch.object(thread_evidence, "MIN_LEXICAL_FOR_CAUSE", 9.0):
            self.assertNotEqual(base, thread_evidence.policy_fingerprint())
        with mock.patch.object(thread_evidence, "DISCRIMINATIVE_TYPES", frozenset({"plant"})):
            self.assertNotEqual(base, thread_evidence.policy_fingerprint())
        self.assertEqual(base, thread_evidence.policy_fingerprint())

    def test_record_pass_is_idempotent_and_stamped(self):
        entry = dict(SAME)
        now = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(thread_evidence.record_pass(entry, "scope", now=now))
        self.assertEqual(entry["gate"]["at"], "2026-09-24T00:00:00+00:00")
        self.assertFalse(thread_evidence.record_pass(entry, "scope", now=now))


class GrandfatherTests(unittest.TestCase):
    """기억이 없던 첫 회차: 원장이 이미 잇고 있던 고리를 승계한다."""

    def _store(self, live):
        link = {"from": A.issue_id, "to": B_TODAY.issue_id, "relationship": "stage_progress"}
        return {"live_thread_ids": live,
                "threads": {"thread-203c": {"thread_id": "thread-203c", "moved_to": "",
                                            "event_ids": [A.issue_id, B_TODAY.issue_id],
                                            "links": [link]},
                            "thread-gone": {"thread_id": "thread-gone", "moved_to": "thread-x",
                                            "links": [link]}}}

    def test_a_live_link_is_grandfathered_whatever_its_verdict_age(self):
        old = {**SAME, "reviewed_at": "2026-09-01T00:00:00+00:00"}
        self.assertEqual(build_threads.grandfathered_links(self._store(["thread-203c"]), {KEY: old}),
                         {KEY})

    def test_a_dropped_story_is_grandfathered_only_with_a_post_gate_verdict(self):
        """SMR 사례: live 에서 빠졌지만 판정이 9/21(게이트 이후)이라 승계된다.
        게이트 이전 판정으로 실렸다가 빠진 고리는 게이트가 의도적으로 끊은 것일 수 있다."""
        self.assertEqual(build_threads.grandfathered_links(self._store([]), {KEY: dict(SAME)}),
                         {KEY})
        old = {**SAME, "reviewed_at": "2026-09-01T00:00:00+00:00"}
        self.assertEqual(build_threads.grandfathered_links(self._store([]), {KEY: old}), set())

    def test_grandfathering_needs_a_usable_same_thread_verdict(self):
        stale = {**SAME, "prompt_version": thread_judge.PROMPT_VERSION + 1}
        self.assertEqual(build_threads.grandfathered_links(self._store(["thread-203c"]), {KEY: stale}),
                         set())
        different = {**SAME, "verdict": "different_thread"}
        self.assertEqual(build_threads.grandfathered_links(self._store(["thread-203c"]), {KEY: different}),
                         set())

    def test_a_grandfathered_link_passes_and_gets_recorded(self):
        """둘째 회차부터는 승계가 아니라 기억으로 선다."""
        cache = {KEY: dict(SAME)}
        accepted, _, _, stats = build_threads.build_edges(
            [_pair(A, B_TODAY, 1.618)], {KEY: SAME}, {}, cache=cache, grandfathered={KEY})
        self.assertEqual(accepted, [(A.issue_id, B_TODAY.issue_id)])
        self.assertEqual(stats["links_grandfathered"], 1)
        self.assertEqual(stats["gate_recorded"], 1)
        self.assertEqual(cache[KEY]["gate"]["basis"], "ledger_link")


class StoriesWorkflowTests(unittest.TestCase):
    """수동 재판정 워크플로 — Daily Brief 의 같은 스텝과 계약이 같다."""

    @classmethod
    def setUpClass(cls):
        folder = ROOT / ".github" / "workflows"
        cls.stories = (folder / "stories.yml").read_text(encoding="utf-8")
        cls.brief = (folder / "daily-brief.yml").read_text(encoding="utf-8")

    def test_same_command_and_budget_as_the_brief(self):
        command = "python -u tools/build_threads.py --live-llm --max-new-pairs 400"
        self.assertIn(command, self.brief)
        self.assertIn(command, self.stories)

    def test_both_ledger_files_are_committed(self):
        for name in ("thread_ledger.json", "thread_llm_reviews.json"):
            self.assertIn(name, self.stories)

    def test_serialised_with_the_other_ledger_writers(self):
        self.assertIn("group: nuclens-state", self.stories)
        self.assertIn("cancel-in-progress: false", self.stories)

    def test_a_missing_key_stops_instead_of_writing_no_api_key(self):
        """키 없이 돌면 원장 build.status 가 no_api_key 로 덮인다(thread-ledger 함정)."""
        step = self.stories.split("- name: Build long-term stories")[1].split("- name:")[0]
        self.assertIn("exit 1", step)

    def test_cards_are_woken_with_force(self):
        self.assertIn("-f force=true", self.stories)
        self.assertIn("cards.yml", self.stories)


if __name__ == "__main__":
    unittest.main()
