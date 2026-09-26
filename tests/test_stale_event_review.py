"""기사는 새것인데 사건이 묵은 후보 — freshness.stale_firsts 와 dedup.stale_event_review.

2026-09-26 1번 카드 '정부, 대미 전략투자 첫 사업으로 텍사스 가스복합발전소 건설 확정'은
9/22 국회 보고를 9/25 정리 기사가 다시 쓴 것이었다. 게재 시각만 보는 신선도 게이트는
이것을 못 본다 — 늦은 발송 45건 중 27건이 이 모양이었다.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dedup
import freshness as fr
from gemini_client import GeminiError

KST = timezone(timedelta(hours=9))
CUTOFF = datetime(2026, 9, 25, 4, 13, tzinfo=KST)
DATES = {
    "old": {"published_at": "2026-09-22T12:14:00+09:00",
            "title": "정부, 대미 전략투자 1호로 텍사스 6.3GW 가스복합발전소 건설 확정"},
    "new": {"published_at": "2026-09-25T09:28:00+09:00",
            "title": "국회 보고된 대미투자…'텍사스' 확정, 원전·LNG 협상 진행형"},
}


def _row(h, importance="nice_to_know", members=(), **kw):
    row = {"hash": h, "importance": importance, "title_kr": DATES.get(h, {}).get("title", h),
           "summary": "요약", "published_at": DATES.get(h, {}).get("published_at",
                                                                  "2026-09-25T09:00:00+09:00"),
           "story_article_hashes": list(members)}
    row.update(kw)
    return row


class FirstSeenTests(unittest.TestCase):
    def test_the_earliest_story_member_is_the_first_report(self):
        head = fr.first_seen(_row("new", members=["old"]), DATES)
        self.assertEqual(head["hash"], "old")

    def test_only_rows_whose_first_report_is_stale_are_asked(self):
        rows = [_row("new", members=["old"]), _row("solo"),
                _row("dated", importance="must_read", members=["old"], stale_since="2026-09-22")]
        got = fr.stale_firsts(rows, DATES, CUTOFF, 6)
        self.assertEqual(list(got), ["new"], "혼자인 기사·이미 날짜 단 기사까지 물었다")

    def test_archive_dates_reads_recent_months(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2026-09.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
                {"hash": "a", "pub": "2026-09-22T12:14:00+09:00", "title_kr": "확정"},
                {"hash": "z", "pub": "2026-08-01T00:00:00+09:00", "title_kr": "오래됨"},
            ]), encoding="utf-8")
            got = fr.archive_dates(14, root=root, now=datetime(2026, 9, 26, 6, tzinfo=KST))
        self.assertEqual(set(got), {"a"})


class _Client:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.payloads = response, error, []

    def __call__(self, prompt, payload, **kw):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.response


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self._avail = dedup.is_available
        dedup.is_available = lambda: True
        dedup.reset_failures()
        self.addCleanup(setattr, dedup, "is_available", self._avail)
        self.first = {"new": {"hash": "old", **DATES["old"]}}

    def test_no_new_action_drops_a_nice_to_know(self):
        row = _row("new", members=["old"])
        client = _Client({"items": [{"candidate": 0, "new_facts": [], "new_action": False}]})
        verdicts = dedup.stale_event_review([row], self.first, "2026-09-25", client=client)
        self.assertTrue(row["continuity"]["drop"])
        self.assertEqual(row["continuity"]["identity_method"], "stale_event")
        self.assertEqual(verdicts[0]["prior_date"], "2026-09-22")
        self.assertIn("CUTOFF=2026-09-25", client.payloads[0])
        self.assertIn("(FIRST) published=2026-09-22", client.payloads[0])

    def test_no_new_action_dates_a_must_read_instead_of_dropping(self):
        """결정 D1 — 중요한 결정은 영영 빠뜨리지 않고 첫 보도일을 달아 한 번 보낸다."""
        row = _row("new", importance="must_read", members=["old"])
        client = _Client({"items": [{"candidate": 0, "new_facts": [], "new_action": False}]})
        dedup.stale_event_review([row], self.first, "2026-09-25", client=client)
        self.assertEqual(row["stale_since"], "2026-09-22")
        self.assertFalse((row.get("continuity") or {}).get("drop"))

    def test_a_new_action_is_left_alone(self):
        row = _row("new", members=["old"])
        client = _Client({"items": [{"candidate": 0, "new_facts": ["하원 통과"], "new_action": True}]})
        dedup.stale_event_review([row], self.first, "2026-09-25", client=client)
        self.assertNotIn("continuity", row)
        self.assertNotIn("stale_since", row)

    def test_missing_answer_or_failure_touches_nothing(self):
        row = _row("new", members=["old"])
        dedup.stale_event_review([row], self.first, "2026-09-25", client=_Client({"items": []}))
        self.assertNotIn("continuity", row)
        verdicts = dedup.stale_event_review(
            [row], self.first, "2026-09-25", client=_Client(error=GeminiError("503")))
        self.assertEqual(verdicts, [])
        self.assertNotIn("continuity", row)
        self.assertTrue(dedup.LLM_FAILURES, "실패가 운영 알림 목록에 안 남았다")

    def test_rows_already_dropped_or_not_flagged_are_not_asked(self):
        client = _Client({"items": []})
        dropped = _row("new", members=["old"], continuity={"drop": True})
        self.assertEqual(dedup.stale_event_review([dropped], self.first, "2026-09-25",
                                                  client=client), [])
        self.assertEqual(dedup.stale_event_review([_row("solo")], self.first, "2026-09-25",
                                                  client=client), [])
        self.assertEqual(client.payloads, [], "물을 것이 없는데 호출했다")


if __name__ == "__main__":
    unittest.main()
