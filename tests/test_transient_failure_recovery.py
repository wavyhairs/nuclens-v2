# -*- coding: utf-8 -*-
"""일시 장애가 **옆 기능으로 번지지 않고, 숨지도 않는다** — 2026-09-30 사고의 계약.

그날 드러난 것은 한 건의 503 이 아니라 세 가지 구조였다.

**① 전파.** 업로드는 정상이었는데 배포 직후 manifest·shard 세대가 잠시 섞여
(`news/000.json declared=2142 actual=2161`) 라이브 스모크가 떨어졌고, 그 한 번이
Cards 호출까지 막았다. → 스모크는 전체 검사를 몇 번 다시 하고, Cards 는 업로드에만
건다(워크플로 쪽 계약은 test_cards_workflow).

**② 비대칭.** 통합 `card_writer` 가 503 으로 떨어지자 일일 카드는 단독 Writer 로
살아났지만 스토리는 그 경로가 없어 빠졌다. → 과부하 대기를 호출 계층에 두어
Narrator·Writer 가 일일 폴백과 같은 기회를 얻는다.

**③ 은폐.** 후보가 있었는데 카피가 안 나온 날도 `Make story cards` 는 "오늘
스토리 카피가 없다" 로 초록불이었다. → make_cards 가 없음/QA/실패를 가려 적고,
story_cards 는 실패일 때 빨간불이 된다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import card_editorial  # noqa: E402
import check_live_news  # noqa: E402
import gemini_client  # noqa: E402
import make_cards  # noqa: E402
import story_cards  # noqa: E402
from test_card_editorial import ITEMS, brief, copy  # noqa: E402


def overload(code=503):
    exc = gemini_client.GeminiError(f"HTTP {code}: UNAVAILABLE")
    exc.transient = code in gemini_client.TRANSIENT_HTTP_STATUSES
    return exc


class LiveNewsSmokeRetryTests(unittest.TestCase):
    """① 한 번의 세대 불일치로 배포를 실패로 판정하지 않는다."""

    def test_a_propagation_mismatch_that_heals_passes(self):
        probe = mock.Mock(side_effect=[
            ValueError("news shard count mismatch: news/000.json declared=2142 actual=2161"),
            2161])
        sleeps = []
        self.assertEqual(check_live_news.check_with_retry(
            "https://x.test", attempts=4, wait_sec=20, sleep=sleeps.append, probe=probe), 2161)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(sleeps, [20])

    def test_a_persistent_mismatch_still_fails(self):
        """재시도가 지속 오류를 가리면 안 된다 — 끝까지 안 맞으면 실패다."""
        probe = mock.Mock(side_effect=ValueError("news total mismatch"))
        with self.assertRaises(ValueError):
            check_live_news.check_with_retry(
                "https://x.test", attempts=3, wait_sec=1, sleep=lambda _: None, probe=probe)
        self.assertEqual(probe.call_count, 3)

    def test_the_whole_check_restarts_from_the_manifest(self):
        """shard 만 다시 받으면 옛 manifest 와 새 shard 를 또 섞는다."""
        pages = iter([
            {"schema": "nuclens-news-shards-v1", "count": 2,
             "shards": [{"file": "news/000.json", "count": 2}]},
            [{}],                          # 옛 세대 shard
            {"schema": "nuclens-news-shards-v1", "count": 1,
             "shards": [{"file": "news/000.json", "count": 1}]},
            [{}],
        ])

        def probe(_site, evidence):
            return check_live_news.validate_manifest(next(pages), lambda _: next(pages))

        self.assertEqual(check_live_news.check_with_retry(
            "https://x.test", attempts=2, wait_sec=0, sleep=lambda _: None, probe=probe), 1)


class OverloadWaitTests(unittest.TestCase):
    """② 과부하 대기는 호출 계층에 있다 — 스토리도 일일과 같은 기회를 얻는다."""

    def setUp(self):
        patches = [
            mock.patch.object(card_editorial, "_overload_waited_sec", 0.0),
            mock.patch.object(card_editorial, "CARD_OVERLOAD_WAITS_SEC", (45, 120)),
            mock.patch.object(card_editorial, "CARD_OVERLOAD_BUDGET_SEC", 240),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.slept = []
        sleeper = mock.patch.object(card_editorial, "_overload_sleep", self.slept.append)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_a_503_is_waited_out_and_logged(self):
        rows = []
        with mock.patch("gemini_client.call_json",
                        side_effect=[overload(), {"ok": 1}]) as called:
            out = card_editorial.call("card_writer", "sys", {}, log=rows)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(called.call_count, 2)
        self.assertEqual(self.slept, [45])
        self.assertEqual(rows[0]["overload_retries"], 1)
        self.assertEqual(rows[0]["max_http_attempts"], 2 * (card_editorial.CARD_LLM_RETRIES + 1))

    def test_non_transient_failures_are_not_waited(self):
        """429 일일 한도·400 은 기다려도 안 풀린다."""
        with mock.patch("gemini_client.call_json", side_effect=overload(429)):
            with self.assertRaises(gemini_client.GeminiError):
                card_editorial.call("card_writer", "sys", {})
        self.assertEqual(self.slept, [])

    def test_the_wait_budget_is_shared_across_calls(self):
        """과부하가 안 풀리는 날 호출마다 사다리를 새로 타서 잡 시간을 태우지 않는다."""
        with mock.patch("gemini_client.call_json", side_effect=overload()):
            with self.assertRaises(gemini_client.GeminiError):
                card_editorial.call("card_editorial_narrator", "sys", {})
            with self.assertRaises(gemini_client.GeminiError):
                card_editorial.call("card_daily_writer", "sys", {})
        # 첫 호출이 45+120, 둘째는 남은 예산(75초) 안에 드는 45 만 — 120 은 넘친다.
        self.assertEqual(self.slept, [45, 120, 45])
        self.assertLessEqual(sum(self.slept), card_editorial.CARD_OVERLOAD_BUDGET_SEC)


class StoryOutcomeTests(unittest.TestCase):
    """③ 스토리가 빠진 이유를 가른다 — 호출 실패(failed) vs 편집 판단(quality)."""

    PAYLOAD = {"thread_id": "thread-x", "events": [{"date": "2026-09-19"}]}

    def _run(self, responses, story_bad=()):
        outcome: dict = {}
        with mock.patch.object(make_cards, "story_problems",
                               side_effect=lambda *_, **__: list(story_bad)), \
                mock.patch.object(make_cards, "card_editorial") as fake:
            fake.validate_brief = card_editorial.validate_brief
            fake.normalize_brief = card_editorial.normalize_brief
            fake.daily_writer_system = lambda **_: "sys"
            fake.writer_system = lambda **_: "sys"
            fake.NARRATOR_SYSTEM = "sys"
            fake.call = mock.Mock(side_effect=responses)
            daily, story = make_cards.run_editorial(
                ITEMS, "2026-09-30", 10, dict(self.PAYLOAD), [], outcome)
        return daily, story, outcome

    def test_a_writer_503_is_recorded_as_a_failure_not_as_no_story(self):
        """그날 실사고: 일일은 단독 Writer 로 살고 스토리는 사라졌다 — 그 사실이 남는다."""
        daily, story, outcome = self._run(
            [brief(story_thread="thread-x"), overload(), copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual(outcome["status"], make_cards.STORY_FAILED)
        self.assertIn("503", outcome["reason"])

    def test_a_narrator_failure_is_a_failure(self):
        _daily, _story, outcome = self._run([overload(), copy()["daily"]])
        self.assertEqual(outcome["status"], make_cards.STORY_FAILED)

    def test_a_story_rejected_by_qa_is_quality_not_failure(self):
        _daily, story, outcome = self._run(
            [brief(story_thread="thread-x"), copy(with_story=True), copy(with_story=True)],
            story_bad=["cover.headline: 31자 > 26"])
        self.assertIsNone(story)
        self.assertEqual(outcome["status"], make_cards.STORY_QUALITY)

    def test_a_good_day_records_nothing(self):
        _daily, story, outcome = self._run(
            [brief(story_thread="thread-x"), copy(with_story=True)])
        self.assertIsNotNone(story)
        self.assertEqual(outcome, {})


class StoryStepExitCodeTests(unittest.TestCase):
    """③ `Make story cards` 의 초록불이 '정상'을 뜻하게 한다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, path in (("STORY_STATUS_FILE", root / "story_status.json"),
                           ("STORY_COPY_FILE", root / "story_copy.json")):
            p = mock.patch.object(make_cards, name, path)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(root / "summary.md")})
        env.start()
        self.addCleanup(env.stop)
        self.summary = root / "summary.md"

    def _main(self, date="2026-09-30"):
        with mock.patch.object(sys, "argv", ["story_cards.py", "--date", date]):
            return story_cards.main()

    def test_a_failed_story_turns_the_step_red(self):
        make_cards.write_story_status("2026-09-30", make_cards.STORY_FAILED,
                                      "Writer(card_writer) 호출 실패 — HTTP 503", "thread-x")
        self.assertEqual(self._main(), 1)
        text = self.summary.read_text(encoding="utf-8")
        self.assertIn("thread-x", text)
        self.assertIn("Long-term stories", text)

    def test_a_day_without_a_candidate_stays_green(self):
        make_cards.write_story_status("2026-09-30", make_cards.STORY_NONE, "후보 없음")
        self.assertEqual(self._main(), 0)

    def test_a_quality_drop_warns_but_stays_green(self):
        make_cards.write_story_status("2026-09-30", make_cards.STORY_QUALITY, "QA", "thread-x")
        self.assertEqual(self._main(), 0)
        self.assertIn("편집 QA", self.summary.read_text(encoding="utf-8"))

    def test_yesterdays_failure_does_not_colour_today(self):
        make_cards.write_story_status("2026-09-29", make_cards.STORY_FAILED, "503", "thread-x")
        self.assertEqual(self._main(), 0)

    def test_no_status_file_keeps_the_old_quiet_behaviour(self):
        """멱등 스킵(카드가 이미 있는 날)은 make_cards 가 판정을 안 남긴다."""
        self.assertEqual(self._main(), 0)


if __name__ == "__main__":
    unittest.main()
