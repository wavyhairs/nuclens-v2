"""스토리 카드 재방송 거르기와 후보 확장.

2026-09-18~24 스토리 카드 7장 중 세 쌍이 새 전개 없는 재방송이었다 — 대미 투자
(9/18·9/21), 원전 공론화(9/20·9/23), 한·미·일 SMR(9/22·9/24). 같은 날 오늘 순위
18건 중 스토리 자격이 있는 이슈는 5건이었는데 일일 3건 안에는 재방송 하나뿐이었다.

규칙 둘:
  · 같은 스토리는 지난 카드 이후 **새 사건이 진행 관계로** 붙었을 때만 다시 낸다.
    문장 변화로 재지 않는다 — 9/24 SMR 은 제목이 바뀌었지만 같은 사건이었다.
  · 3건 안에 없으면 나머지 오늘 이슈를 **사이트 순서대로** 내려간다.
"""
import json
import tempfile
import unittest
from pathlib import Path

import card_context as cc
import make_cards as mc
import publish_cards


def _row(source, date, relation="", folded=()):
    return {"source_event_id": source, "source_event_ids": [source, *folded],
            "event_id": source, "date": date, "title": source,
            "relation_to_next": relation, "evidence_hashes": ["h-" + source]}


def _thread(thread_id, *rows):
    return {"thread_id": thread_id, "flow": list(rows)}


SMR = _thread("thread-smr",
              _row("issue-1779", "2026-08-23", "stage_progress"),
              _row("story-831a", "2026-09-22"))


def _data(threads, date="2026-09-24"):
    return cc.SiteData(date=date, issues=[], generation_id="g", source="today",
                       threads={"version": cc.REQUIRED_THREAD_CONTRACT, "visible": True,
                                "threads": threads})


def _issue(issue_id, thread_id=""):
    return {"issue_id": issue_id, "thread_id": thread_id, "title": issue_id}


class RepeatVerdictTests(unittest.TestCase):
    def test_a_story_never_published_is_fresh(self):
        self.assertTrue(cc.repeat_verdict(SMR, [], "2026-09-24")[0])

    def test_the_0924_smr_rerun_is_a_repeat(self):
        """제목이 바뀌어도 사건 집합이 같으면 재방송이다."""
        history = [{"date": "2026-09-22", "thread_id": "thread-smr",
                    "event_ids": ["issue-1779", "story-831a"]}]
        ok, why = cc.repeat_verdict(SMR, history, "2026-09-24")
        self.assertFalse(ok)
        self.assertIn("2026-09-22", why)

    def test_a_new_event_joined_by_progress_allows_a_repeat(self):
        """대미 투자: 9/21 카드 뒤 9/08 사건이 stage_progress 로 합류했다."""
        thread = _thread("thread-inv",
                         _row("story-d521", "2026-08-26", "same_matter"),
                         _row("issue-40c5", "2026-08-28", "same_matter"),
                         _row("story-a3e5", "2026-09-08", "stage_progress"),
                         _row("story-7524", "2026-09-09"))
        history = [{"date": "2026-09-21", "thread_id": "thread-inv",
                    "event_ids": ["story-d521", "issue-40c5", "story-7524"]}]
        self.assertTrue(cc.repeat_verdict(thread, history, "2026-09-24")[0])

    def test_a_new_event_that_only_repeats_the_matter_is_not_enough(self):
        thread = _thread("thread-x",
                         _row("a", "2026-09-01", "stage_progress"),
                         _row("b", "2026-09-05", "same_matter"),
                         _row("c", "2026-09-20"))
        history = [{"date": "2026-09-10", "thread_id": "thread-x", "event_ids": ["a", "b"]}]
        ok, why = cc.repeat_verdict(thread, history, "2026-09-24")
        self.assertFalse(ok)
        self.assertIn("같은 사안", why)

    def test_a_folded_copy_is_not_a_new_event(self):
        """접힌 사본이 대표 id 를 바꿔도 이미 본 사건이다."""
        thread = _thread("thread-f", _row("a", "2026-09-01", "stage_progress"),
                         _row("b2", "2026-09-05", folded=("b",)))
        history = [{"date": "2026-09-10", "thread_id": "thread-f", "event_ids": ["a", "b"]}]
        self.assertFalse(cc.repeat_verdict(thread, history, "2026-09-24")[0])

    def test_the_same_day_is_a_replacement_not_a_repeat(self):
        history = [{"date": "2026-09-24", "thread_id": "thread-smr",
                    "event_ids": ["issue-1779", "story-831a"]}]
        self.assertTrue(cc.repeat_verdict(SMR, history, "2026-09-24")[0])

    def test_only_the_latest_card_counts(self):
        thread = _thread("thread-y", _row("a", "2026-09-01", "stage_progress"),
                         _row("b", "2026-09-15"))
        history = [{"date": "2026-09-05", "thread_id": "thread-y", "event_ids": ["a"]},
                   {"date": "2026-09-20", "thread_id": "thread-y", "event_ids": ["a", "b"]}]
        self.assertFalse(cc.repeat_verdict(thread, history, "2026-09-24")[0])


class PoolTests(unittest.TestCase):
    NEW = _thread("thread-eu", _row("e1", "2026-09-10", "stage_progress"),
                  _row("e2", "2026-09-23"))

    def test_the_pool_keeps_site_order_daily_three_first(self):
        items = [_issue("i1"), _issue("i2"), _issue("i3")]
        rows = [_issue("i1"), _issue("i2"), _issue("i3"), _issue("i4"), _issue("i5")]
        self.assertEqual([r["issue_id"] for r in mc.story_pool(items, rows)],
                         ["i1", "i2", "i3", "i4", "i5"])

    def test_a_repeat_in_the_top_three_falls_through_to_the_next_rank(self):
        """9/24 재현: 2위 재방송을 건너뛰고 3건 밖 5위를 고른다."""
        pool = [_issue("i1"), _issue("i2", "thread-smr"), _issue("i3"), _issue("i4"),
                _issue("i5", "thread-eu")]
        history = [{"date": "2026-09-22", "thread_id": "thread-smr",
                    "event_ids": ["issue-1779", "story-831a"]}]
        reasons = []
        found = cc.pick_story_candidate(_data([SMR, self.NEW]), pool, reasons, history=history)
        self.assertEqual((found.rank, found.thread_id), (5, "thread-eu"))
        self.assertTrue(any("재방송" in line for line in reasons), reasons)

    def test_the_top_three_still_win_when_they_are_fresh(self):
        pool = [_issue("i1"), _issue("i2", "thread-smr"), _issue("i3"),
                _issue("i5", "thread-eu")]
        found = cc.pick_story_candidate(_data([SMR, self.NEW]), pool, [], history=[])
        self.assertEqual(found.rank, 2)

    def test_no_history_keeps_the_old_behaviour(self):
        pool = [_issue("i2", "thread-smr")]
        self.assertIsNotNone(cc.pick_story_candidate(_data([SMR]), pool))


class HistoryFileTests(unittest.TestCase):
    def test_publishing_a_story_records_it_and_replaces_the_same_day(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "story_history.json"
            publish_cards.record_story({"date": "2026-09-24", "thread_id": "thread-smr",
                                        "event_ids": ["a", "b"]}, path)
            publish_cards.record_story({"date": "2026-09-24", "thread_id": "thread-eu",
                                        "issue_id": "i5", "event_ids": ["e1", "e2"]}, path)
            rows = cc.load_story_history(path)
        self.assertEqual([(r["date"], r["thread_id"]) for r in rows],
                         [("2026-09-24", "thread-eu")])

    def test_old_rows_expire(self):
        rows = cc.record_story_card([{"date": "2026-01-01", "thread_id": "t", "event_ids": []}],
                                    date="2026-09-24", thread_id="u")
        self.assertEqual([r["thread_id"] for r in rows], ["u"])

    def test_the_committed_history_is_readable(self):
        rows = cc.load_story_history()
        self.assertTrue(rows, "web/public/cards/story_history.json 이 비었다")
        for row in rows:
            self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(row["thread_id"].startswith("thread-"))

    def test_the_story_album_carries_event_ids(self):
        source = (Path(mc.__file__).parent / "story_cards.py").read_text(encoding="utf-8")
        self.assertIn('"event_ids": list(payload.get("event_ids")', source)
        self.assertIn('story_payload["event_ids"] = card_context.event_ids(',
                      Path(mc.__file__).read_text(encoding="utf-8"))

    def test_the_history_lives_where_the_cards_workflow_commits(self):
        """cards.yml 의 스토리 커밋 스텝은 web/public/cards 를 통째로 올린다."""
        yml = (Path(mc.__file__).parent / ".github" / "workflows" / "cards.yml").read_text(
            encoding="utf-8")
        self.assertIn("git add outbox.json web/public/cards", yml)
        self.assertEqual(cc.STORY_HISTORY_FILE.parent.name, "cards")


if __name__ == "__main__":
    unittest.main()
