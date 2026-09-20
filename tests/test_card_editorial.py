# -*- coding: utf-8 -*-
"""카드 편집 계층의 **호출 계약** — `card_editorial` · `make_cards.run_editorial`.

여기서 지키는 것은 셋이다.

**① 호출 수.** 스토리 없는 날 1회, 있는 날 2회, QA 실패 시 +1. 스토리가 없는
날까지 편집 데스크를 따로 불러 2회로 늘리지 않는다.

**② 논리 호출 ≠ HTTP 요청.** 예전에는 바깥 `for attempt in (1, 2)` 루프와
`call_json(retries=3)` 이 곱해져 "1회" 가 최악 8번의 HTTP 요청이었고, 그 수가
로그 어디에도 없었다. 쿼터를 재려면 두 수가 다 필요하다.

**③ 실패 도메인 분리.** 스토리가 깨져도 일일 카드는 그대로 나간다. Narrator 가
죽으면 스토리만 빠지고 일일은 단독 호출로 살아난다.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import card_editorial  # noqa: E402
import llm_policy  # noqa: E402
import make_cards  # noqa: E402

ITEMS = [
    {"issue_id": "issue-a", "title": "기후부, SAR 시범사업 시행",
     "summary": "진안과 금산에서 1년간 시행한다", "detail": "", "why_important": "",
     "implication": "", "open_question": "", "sensitive": False, "body": "",
     "event_date": "", "source": "전기신문", "topic": "전력시장·요금", "hash": "h1",
     "link": "https://x.test/1", "importance": "must_know", "tag": ""},
]


def brief(issue_ids=("issue-a",), story_thread=None) -> dict:
    out = {
        "schema_version": card_editorial.BRIEF_SCHEMA_VERSION,
        "issues": [{
            "issue_id": issue_id,
            "core_change": "계획 단계였던 SAR 이 현장 실증으로 넘어갔다",
            "headline_angle": "계획 넘어 현장",
            "must_know_facts": ["진안·금산에서 착수", "실증기간 1년"],
            "why_it_matters": [{"type": "policy_effect", "text": "계절 기준으로 전환"}],
            "watchpoint": "실증 결과",
            "avoid_repeating": ["시범사업 착수"],
        } for issue_id in issue_ids],
        "story": None,
    }
    if story_thread:
        out["story"] = {"thread_id": story_thread, "story_subject": "SAR",
                        "key_change": "계획 → 시행"}
    return out


def copy(issue_ids=("issue-a",), with_story=False) -> dict:
    out = {"daily": {
        "hook": {"headline": "오늘 먼저 볼 현안"},
        "steps": [{"issue_id": issue_id, "headline": "SAR, 계획 넘어 현장",
                   "facts": ["진안·금산에서 착수", "실증기간 1년"],
                   "why": ["송전용량이 계절 기준으로 전환", "실증 결과로 확대 여부 판단"]}
                  for issue_id in issue_ids]}, "story": None}
    if with_story:
        out["story"] = {"cover": {"headline": "SAR"}}
    return out


class ModelPolicyTests(unittest.TestCase):
    """**프로필을 등록하는 데 그치지 않고 실제 호출에 연결한다.**

    예전 카드 호출은 `model=` 을 안 넘겨 `gemini_client.MODEL` 전역에 기댔고,
    스토리는 워크플로 env 로 `gemini-3-flash-preview` 를 박았다. "이 카드는
    어느 모델이 썼는가" 가 코드가 아니라 환경에 흩어져 있었다.
    """

    def test_all_four_card_profiles_are_registered(self):
        for task in ("card_editorial_narrator", "card_writer",
                     "card_daily_writer", "card_writer_repair"):
            with self.subTest(task=task):
                self.assertTrue(llm_policy.profile(task).model())

    def test_the_narrator_thinks_one_tier_above_the_writer(self):
        """무엇을 말할지 고르는 일과 그것을 적는 일은 다른 과제다."""
        narrator = llm_policy.profile("card_editorial_narrator").model()
        writer = llm_policy.profile("card_writer").model()
        self.assertEqual(narrator, "gemini-3.5-flash-lite")
        self.assertEqual(writer, "gemini-3.1-flash-lite")
        self.assertNotEqual(narrator, writer)

    def test_the_preview_model_default_is_gone(self):
        snapshot = llm_policy.production_policy_snapshot()
        for name, row in snapshot.items():
            if name.startswith("card_"):
                with self.subTest(task=name):
                    self.assertNotIn("preview", row["model"])

    def test_the_call_actually_passes_the_profile_model(self):
        with mock.patch("gemini_client.call_json", return_value={}) as called:
            card_editorial.call("card_editorial_narrator", "sys", {"a": 1})
        self.assertEqual(called.call_args.kwargs["model"], "gemini-3.5-flash-lite")
        self.assertEqual(called.call_args.kwargs["label"], "cards:card_editorial_narrator")

    def test_transport_retries_are_explicit_not_the_library_default(self):
        """논리 1회가 몇 번의 HTTP 요청이 되는지를 코드가 정한다."""
        with mock.patch("gemini_client.call_json", return_value={}) as called:
            card_editorial.call("card_writer", "sys", {})
        self.assertEqual(called.call_args.kwargs["retries"], card_editorial.CARD_LLM_RETRIES)
        self.assertLessEqual(card_editorial.CARD_LLM_RETRIES, 1)


class BriefContractTests(unittest.TestCase):
    """브리프는 문자열 모음이 아니라 계약이다."""

    def test_a_good_brief_passes(self):
        self.assertEqual(card_editorial.validate_brief(brief(), ITEMS), [])

    def test_a_missing_issue_is_refused(self):
        problems = card_editorial.validate_brief(brief(issue_ids=()), ITEMS)
        self.assertTrue(any("issue_id" in p for p in problems))

    def test_a_reordered_brief_is_refused(self):
        """순서가 틀리면 Writer 가 A 이슈의 판단으로 B 카드를 쓴다."""
        items = ITEMS + [dict(ITEMS[0], issue_id="issue-b")]
        problems = card_editorial.validate_brief(
            brief(issue_ids=("issue-b", "issue-a")), items)
        self.assertTrue(any("issue_id" in p for p in problems))

    def test_a_story_for_a_thread_we_did_not_ask_about_is_refused(self):
        problems = card_editorial.validate_brief(
            brief(story_thread="thread-다른것"), ITEMS, "thread-우리것")
        self.assertTrue(any("thread_id" in p for p in problems))

    def test_a_story_invented_without_a_candidate_is_refused(self):
        problems = card_editorial.validate_brief(brief(story_thread="thread-x"), ITEMS)
        self.assertTrue(any("후보가 없는데" in p for p in problems))

    def test_a_missing_story_when_we_had_a_candidate_is_refused(self):
        problems = card_editorial.validate_brief(brief(), ITEMS, "thread-우리것")
        self.assertTrue(any("story 가 없다" in p for p in problems))


class CallCountTests(unittest.TestCase):
    def _run(self, story_payload, responses):
        calls: list[dict] = []
        with mock.patch.object(make_cards, "card_editorial") as fake:
            fake.validate_brief = card_editorial.validate_brief
            fake.daily_writer_system = lambda **_: "sys"
            fake.writer_system = lambda **_: "sys"
            fake.NARRATOR_SYSTEM = "sys"
            fake.call = mock.Mock(side_effect=responses)
            daily, story = make_cards.run_editorial(ITEMS, "2026-09-20", 10,
                                                    story_payload, calls)
        return daily, story, fake.call

    def test_a_day_without_a_story_costs_one_logical_call(self):
        daily, story, call = self._run(None, [copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args_list[0].args[0], "card_daily_writer")

    def test_a_day_with_a_story_costs_two(self):
        payload = {"thread_id": "thread-x", "events": [{"date": "2026-09-19"}]}
        daily, story, call = self._run(payload, [brief(story_thread="thread-x"),
                                                 copy(with_story=True)])
        self.assertIsNotNone(daily)
        self.assertIsNotNone(story)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_editorial_narrator", "card_writer"])

    def test_a_narrator_failure_drops_the_story_and_keeps_the_daily_card(self):
        """**일일 카드는 핵심 산출물이다.** 스토리 때문에 같이 빠지지 않는다."""
        payload = {"thread_id": "thread-x", "events": []}
        daily, story, call = self._run(
            payload, [RuntimeError("no key"), copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_editorial_narrator", "card_daily_writer"])

    def test_a_brief_that_fails_its_contract_also_drops_only_the_story(self):
        payload = {"thread_id": "thread-x", "events": []}
        daily, story, call = self._run(
            payload, [brief(story_thread="thread-엉뚱한것"), copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual(call.call_count, 2)

    def test_a_qa_failure_costs_exactly_one_repair(self):
        """**Narrator 는 다시 부르지 않는다.** 판단은 그대로 두고 문구만 고친다."""
        bad = copy()["daily"]
        bad["steps"][0]["why"] = ["진안·금산에서 착수", "실증기간 1년"]  # facts 의 되풀이
        daily, story, call = self._run(None, [bad, copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_daily_writer", "card_writer_repair"])

    def test_a_failure_that_survives_the_repair_gives_up_instead_of_shipping_it(self):
        bad = copy()["daily"]
        bad["steps"][0]["why"] = ["진안·금산에서 착수", "실증기간 1년"]
        daily, story, call = self._run(None, [bad, bad])
        self.assertIsNone(daily)      # 호출자가 결정적 폴백으로 떨어진다
        self.assertEqual(call.call_count, 2)


class CallLogTests(unittest.TestCase):
    def test_the_log_separates_logical_calls_from_http_attempts(self):
        rows = []
        with mock.patch("gemini_client.call_json", return_value={}):
            card_editorial.call("card_writer", "sys", {}, log=rows)
            card_editorial.call("card_writer_repair", "sys", {},
                                fix_these=["x"], log=rows)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["repair"])
        for row in rows:
            self.assertEqual(row["max_http_attempts"], card_editorial.CARD_LLM_RETRIES + 1)
            self.assertEqual(row["model"], "gemini-3.1-flash-lite")

    def test_the_worst_case_is_far_below_the_old_one(self):
        """예전: 바깥 2회 × call_json 기본 4회 = 8. 지금: 논리 2회 × 2 = 4."""
        worst_story_day = 2 * (card_editorial.CARD_LLM_RETRIES + 1)
        self.assertLessEqual(worst_story_day, 4)


if __name__ == "__main__":
    unittest.main()
