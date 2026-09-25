"""주간 기간은 실행 시각이 아니라 금요일 17:05 KST 고정 경계로 정한다.

W35(2026-08)는 토요일 00:00 복구 실행에서 8/23(일)~8/29(토)가 되어 다음 주
시작일과 겹쳤다. 제때 돌아도 금요일 17시 이후 기사는 어느 주에도 안 들어갔다.
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import weekly_bot  # noqa: E402
import weekly_trigger_gate as gate  # noqa: E402

KST = weekly_bot.KST
CUTOFF = datetime(2026, 9, 25, 17, 5, tzinfo=KST)          # 금요일 경계


class FixedCutoffTests(unittest.TestCase):
    def test_on_time_late_and_weekend_runs_make_the_same_week(self):
        runs = [
            datetime(2026, 9, 25, 17, 7, tzinfo=KST),       # schedule 제때
            datetime(2026, 9, 25, 21, 40, tzinfo=KST),      # 4시간 반 밀림
            datetime(2026, 9, 26, 0, 0, tzinfo=KST),        # 자정 넘김 (W35 모양)
            datetime(2026, 9, 27, 11, 50, tzinfo=KST),      # 일요일 복구 마감 직전
        ]
        for now in runs:
            with self.subTest(now=now.isoformat()):
                self.assertEqual(weekly_bot.week_window(now),
                                 (CUTOFF - timedelta(days=7), CUTOFF))
                self.assertEqual([d.isoformat() for d in weekly_bot.week_label(now)],
                                 ["2026-09-19", "2026-09-25"])
                self.assertEqual(weekly_bot.report_week_id(now), "2026-W39")

    def test_before_the_cutoff_is_still_last_week(self):
        """금요일 경계 전 수동 실행이 이번 주 키에 지난주 내용을 덮어쓰지 않는다."""
        now = datetime(2026, 9, 25, 17, 4, tzinfo=KST)
        self.assertEqual(weekly_bot.week_cutoff(now), CUTOFF - timedelta(days=7))
        self.assertEqual(weekly_bot.report_week_id(now), "2026-W38")
        self.assertEqual(weekly_bot.week_label(now)[1].isoformat(), "2026-09-18")

    def test_consecutive_weeks_have_no_gap_and_no_overlap(self):
        this = weekly_bot.week_window(CUTOFF + timedelta(minutes=2))
        following = weekly_bot.week_window(CUTOFF + timedelta(days=7, minutes=2))
        self.assertEqual(this[1], following[0])        # (a, b] 이어 붙임

    def test_friday_evening_article_lands_in_the_next_week(self):
        """예전엔 금요일 17시 이후 기사가 어느 주에도 안 들어갔다."""
        def record(published):
            return {"importance": "must_read", "curation_status": "reviewed",
                    "title": "금요일 저녁 발표", "title_kr": "금요일 저녁 발표",
                    "link": "https://a.com/fri", "summary": "금요일 저녁에 나온 발표다.",
                    "published_at": published, "cached_at": published}
        curated = {"f" * 16: record("2026-09-25T19:00:00+09:00")}
        this_week = weekly_bot.get_week_articles(curated, now=CUTOFF + timedelta(minutes=2))
        next_week = weekly_bot.get_week_articles(
            curated, now=CUTOFF + timedelta(days=7, minutes=2))
        self.assertEqual(this_week, [])
        self.assertEqual(len(next_week), 1)


class GateAgreesWithWeeklyBotTests(unittest.TestCase):
    """게이트는 의존성 없이 돌아 weekly_bot 을 import 하지 않는다 — 값이 같아야 한다."""

    def test_same_cutoff_constants(self):
        self.assertEqual(gate.CUTOFF_WEEKDAY, weekly_bot.WEEK_CUTOFF_WEEKDAY)
        self.assertEqual((gate.CUTOFF_HOUR, gate.CUTOFF_MINUTE),
                         (weekly_bot.WEEK_CUTOFF_TIME.hour, weekly_bot.WEEK_CUTOFF_TIME.minute))

    def test_same_week_key_for_every_hour_of_a_week(self):
        now = datetime(2026, 9, 21, 0, 30, tzinfo=KST)
        while now < datetime(2026, 9, 28, 0, 0, tzinfo=KST):
            self.assertEqual(gate._week_id(now), weekly_bot.report_week_id(now), now)
            now += timedelta(minutes=55)

    def test_recovery_opens_at_the_cutoff_not_on_the_hour(self):
        self.assertFalse(gate._in_recovery_window(datetime(2026, 9, 25, 17, 0, tzinfo=KST)))
        self.assertFalse(gate._in_recovery_window(datetime(2026, 9, 25, 17, 4, tzinfo=KST)))
        self.assertTrue(gate._in_recovery_window(datetime(2026, 9, 25, 17, 5, tzinfo=KST)))
        self.assertTrue(gate._in_recovery_window(datetime(2026, 9, 27, 11, 59, tzinfo=KST)))
        self.assertFalse(gate._in_recovery_window(datetime(2026, 9, 27, 12, 0, tzinfo=KST)))

    def test_schedule_cron_runs_after_the_cutoff(self):
        """cron 이 경계 전에 돌면 방금 끝난 주가 아니라 지난주를 만든다."""
        workflow = (ROOT / ".github" / "workflows" / "weekly.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "7 8 * * 5"', workflow)   # UTC 08:07 = KST 17:07 > 17:05


if __name__ == "__main__":
    unittest.main()
