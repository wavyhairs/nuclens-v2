"""같은 스토리인가 — `same_event` 와 **다른 계약**인지 검사가 지킨다."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import issue_review
import thread_judge
from event_retrieval import Event


def _event(issue_id, title, *, units=(), entities=(), assets=(), plants=()):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date(2026, 3, 1), last_seen=date(2026, 3, 2),
        units=frozenset(units), plants=frozenset(plants), entities=frozenset(entities),
        assets=frozenset(assets), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={},
    )


class ContractSeparationTests(unittest.TestCase):
    def test_cache_file_and_version_are_not_shared_with_issue_review(self):
        """`issue_review.PROMPT_VERSION` 을 올리면 13,419건이 한 번에 죽는다."""
        self.assertNotEqual(thread_judge.CACHE_FILE, issue_review.CACHE_FILE)
        self.assertNotEqual(thread_judge.CACHE_KEY, issue_review.CACHE_KEY)
        self.assertEqual(thread_judge.CONTRACT_VERSION, "thread-judge-v1")

    def test_prompt_asks_a_different_question(self):
        """같은 프롬프트를 재사용하면 장기 스토리는 정의상 만들어지지 않는다."""
        self.assertIn("같은 스토리다", thread_judge.SYSTEM_PROMPT)
        self.assertNotIn("same_event", thread_judge.SYSTEM_PROMPT)
        self.assertIn("uncertain", thread_judge.SYSTEM_PROMPT)

    def test_pair_id_is_order_free(self):
        self.assertEqual(thread_judge.pair_id("issue-b", "issue-a"),
                         thread_judge.pair_id("issue-a", "issue-b"))


class RuleTests(unittest.TestCase):
    def test_unit_conflict_is_rejected_without_asking(self):
        left = _event("a", "고리 2호기 계속운전 심사", units={"kori-2"})
        right = _event("b", "Kori Unit 3 outage extended", units={"kori-3"})
        self.assertEqual(thread_judge.rule_verdict(left, right),
                         ("different_thread", thread_judge.REJECT_UNIT_CONFLICT))

    def test_topic_only_pairs_never_reach_the_model(self):
        left = _event("a", "SMR 시장 전망")
        right = _event("b", "SMR 산업 동향")
        verdict, reason = thread_judge.rule_verdict(left, right, {"lexical": 3.0})
        self.assertEqual(verdict, "different_thread")
        self.assertEqual(reason, thread_judge.REJECT_NO_SHARED_IDENTITY)

    def test_a_strongly_specific_lexical_overlap_is_rescued(self):
        """구조화 칸이 빈 옛 사건을 규칙 하나로 통째로 잘라내면 안 된다."""
        left = _event("a", "국회 본회의 전력망 특별법 의결")
        right = _event("b", "국회 본회의 전력망 특별법 통과")
        self.assertIsNone(thread_judge.rule_verdict(
            left, right, {"lexical": thread_judge.LEXICAL_RESCUE}))

    def test_shared_identity_signal_goes_to_the_model(self):
        left = _event("a", "고리 2호기 계속운전 신청", units={"kori-2"})
        right = _event("b", "고리 2호기 계획예방정비 착수", units={"kori-2"})
        self.assertIsNone(thread_judge.rule_verdict(left, right))


class JudgeTests(unittest.TestCase):
    def _pairs(self):
        return [
            {"key": "a--b",
             "left": _event("a", "고리 2호기 계속운전 신청", units={"kori-2"}),
             "right": _event("b", "고리 3호기 계속운전 신청", units={"kori-3"}),
             "signals": {}},
            {"key": "c--d",
             "left": _event("c", "고리 2호기 계속운전 신청", units={"kori-2"}),
             "right": _event("d", "고리 2호기 계속운전 심사", units={"kori-2"}),
             "signals": {}},
        ]

    def test_without_a_key_nothing_is_linked(self):
        """판정 실패는 '잇지 않음' 으로 흡수한다 — 잘못 잇는 것이 더 해롭다."""
        class _Offline:
            @staticmethod
            def is_available():
                return False

        with tempfile.TemporaryDirectory() as tmp:
            verdicts, stats = thread_judge.judge(
                self._pairs(), cache_path=Path(tmp) / "cache.json", client=_Offline())
        self.assertEqual(verdicts["a--b"]["verdict"], "different_thread")
        self.assertNotIn("c--d", verdicts)
        self.assertEqual(stats["status"], "no_api_key")
        self.assertEqual(stats["rule_rejected"], 1)

    def test_cache_hits_do_not_call_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            path.write_text(json.dumps({
                "_comment": "", "prompt_version": thread_judge.PROMPT_VERSION,
                "threads": {"c--d": {"verdict": "same_thread",
                                     "relationship": "stage_progress",
                                     "reason": "같은 절차의 다음 단계",
                                     "prompt_version": thread_judge.PROMPT_VERSION}},
            }, ensure_ascii=False), encoding="utf-8")

            class _Boom:
                @staticmethod
                def is_available():
                    raise AssertionError("캐시가 있으면 모델을 부르지 않는다")

            verdicts, stats = thread_judge.judge(
                self._pairs(), cache_path=path, client=_Boom())
        self.assertEqual(verdicts["c--d"]["verdict"], "same_thread")
        self.assertEqual(verdicts["c--d"]["method"], "cache")
        self.assertEqual(stats["from_cache"], 1)

    def test_stale_prompt_version_invalidates_the_entry(self):
        cache = {"x--y": {"verdict": "same_thread",
                          "prompt_version": thread_judge.PROMPT_VERSION + 1}}
        self.assertIsNone(thread_judge.cached_verdict(cache, "x--y"))


if __name__ == "__main__":
    unittest.main()
