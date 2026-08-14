"""Nuclens 웹과 Daily Brief story 계약/장기기간 집계가 같은 단위를 쓰는지 검증."""
import importlib.util
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("nuclens_build_data", ROOT / "web" / "build_data.py")
build = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(build)


class StoryTrendTests(unittest.TestCase):
    def row(self, day: date, tag="SMR", outlets=1):
        return {
            "hash": day.isoformat(),
            "briefing_date": day.isoformat(),
            "title_kr": f"뉴스 {day}",
            "canonical_tags": [tag],
            "topics": ["smr"],
            "countries": ["US"],
            "publisher": "WNN",
            "story_outlet_count": outlets,
            "story_tier1_count": 1 if outlets >= 2 else 0,
            "importance": "must_read" if day.day % 3 == 0 else "nice_to_know",
            "selection_score": 20,
            "source_type": "media",
        }

    def test_periods_count_selected_story_not_outlet_count(self):
        end = date(2026, 8, 14)
        rows = [self.row(end - timedelta(days=i), outlets=5) for i in range(30)]
        periods = build.build_period_trends(rows, end.isoformat())
        self.assertEqual(periods["30"]["story_count"], 30)
        self.assertEqual(periods["30"]["multi_source_story_count"], 30)
        self.assertEqual(periods["30"]["top_tags"][0]["count"], 30)

    def test_long_period_reports_actual_archive_coverage(self):
        end = date(2026, 8, 14)
        rows = [self.row(end - timedelta(days=i)) for i in range(30)]
        period = build.build_period_trends(rows, end.isoformat())["365"]
        self.assertFalse(period["complete_period"])
        self.assertEqual(period["available_days"], 30)
        self.assertEqual(period["start"], (end - timedelta(days=29)).isoformat())
        self.assertEqual(period["requested_start"], (end - timedelta(days=364)).isoformat())

    def test_long_period_becomes_complete_as_archive_accumulates(self):
        end = date(2026, 8, 14)
        rows = [self.row(end - timedelta(days=i)) for i in range(400)]
        period = build.build_period_trends(rows, end.isoformat())["365"]
        self.assertTrue(period["complete_period"])
        self.assertEqual(period["story_count"], 365)
        self.assertGreaterEqual(len(period["timeline"]), 12)

    def test_week_comparison_uses_story_counts(self):
        end = date(2026, 8, 14)
        rows = []
        for i in range(14):
            rows.append(self.row(end - timedelta(days=i), tag="계속운전" if i < 7 else "SMR"))
        week = build.build_period_trends(rows, end.isoformat())["7"]
        self.assertTrue(week["previous_period_complete"])
        comparison = {row["tag"]: row for row in week["tag_comparison"]}
        self.assertEqual(comparison["계속운전"]["count"], 7)
        self.assertEqual(comparison["계속운전"]["previous_count"], 0)
        self.assertTrue(comparison["계속운전"]["new"])

    def test_fingerprint_connects_same_underlying_event_despite_title_difference(self):
        left = {"story_fingerprint": {
            "country": "FR", "operator": "EDF", "asset": "French nuclear fleet",
            "event_family": "operational_constraint", "action": "shutdown",
            "cause": ["heat", "high water temperature"],
        }}
        right = {"story_fingerprint": {
            "country": "FR", "operator": "EDF", "asset": "French nuclear fleet",
            "event_family": "operational_constraint", "action": "shutdown",
            "cause": ["heat", "low river flow"],
        }}
        score, diag = build.story_fingerprint_similarity(left, right)
        self.assertGreaterEqual(score, 0.8)
        self.assertGreaterEqual(diag["compared"], 5)
        self.assertIn("actors", diag["shared"])
        self.assertIn("assets", diag["shared"])


if __name__ == "__main__":
    unittest.main()
