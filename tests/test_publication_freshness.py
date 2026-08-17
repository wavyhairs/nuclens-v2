"""기사 발행 시각 보존과 랭킹 신선도 회귀 테스트."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _key in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
    os.environ.setdefault(_key, "test-dummy")

import news_bot  # noqa: E402
import ranking  # noqa: E402


NOW = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
CFG = ranking.load_config()


def article(**overrides) -> dict:
    row = {
        "hash": "freshness",
        "title": "원전 후속 기사",
        "title_kr": "원전 후속 기사",
        "link": "https://example.com/freshness",
        "domain": "example.com",
        "importance": "nice_to_know",
        "section": "international",
        "features": {
            "event_type": "other",
            "korea_relevance": 0,
            "market_materiality": 0,
            "policy_materiality": 0,
            "novelty": 0,
            "evidence_strength": 0,
            "report_worthiness": 0,
        },
        "queued_at": NOW.isoformat(),
    }
    row.update(overrides)
    return row


class TestPublicationSerialization(unittest.TestCase):
    def test_datetime_is_preserved_as_utc_iso(self):
        kst = timezone(timedelta(hours=9))
        value = datetime(2026, 8, 17, 10, 30, tzinfo=kst)
        self.assertEqual(
            news_bot.normalize_publication_timestamp(value, now=NOW),
            "2026-08-17T01:30:00+00:00",
        )

    def test_rfc2822_is_accepted(self):
        self.assertEqual(
            news_bot.normalize_publication_timestamp(
                "Sun, 16 Aug 2026 21:00:00 +0000", now=NOW),
            "2026-08-16T21:00:00+00:00",
        )

    def test_future_clock_skew_uses_queue_time_instead(self):
        value = NOW + timedelta(hours=2)
        self.assertEqual(news_bot.normalize_publication_timestamp(value, now=NOW), "")

    def test_invalid_or_far_future_value_is_not_persisted(self):
        self.assertEqual(news_bot.normalize_publication_timestamp("not-a-date", now=NOW), "")
        self.assertEqual(
            news_bot.normalize_publication_timestamp(NOW + timedelta(days=2), now=NOW),
            "",
        )


class TestPublicationBasedDecay(unittest.TestCase):
    def test_actual_publication_time_takes_priority_over_queue_time(self):
        late_discovery = article(
            published_at=(NOW - timedelta(hours=36)).isoformat(),
            queued_at=NOW.isoformat(),
        )
        _, breakdown = ranking.score_item(late_discovery, CFG, now=NOW)
        self.assertEqual(breakdown["time_decay"], -1.5)

    def test_old_queue_without_publication_time_keeps_legacy_behavior(self):
        legacy = article(queued_at=(NOW - timedelta(hours=24)).isoformat())
        _, breakdown = ranking.score_item(legacy, CFG, now=NOW)
        self.assertEqual(breakdown["time_decay"], -1.0)

    def test_invalid_publication_time_falls_back_to_queued_at(self):
        invalid = article(
            published_at="broken",
            queued_at=(NOW - timedelta(hours=24)).isoformat(),
        )
        _, breakdown = ranking.score_item(invalid, CFG, now=NOW)
        self.assertEqual(breakdown["time_decay"], -1.0)

    def test_far_future_publication_time_cannot_bypass_queue_decay(self):
        future = article(
            published_at=(NOW + timedelta(days=365)).isoformat(),
            queued_at=(NOW - timedelta(hours=24)).isoformat(),
        )
        _, breakdown = ranking.score_item(future, CFG, now=NOW)
        self.assertEqual(breakdown["time_decay"], -1.0)

    def test_naive_and_zulu_timestamps_are_supported(self):
        naive = article(published_at="2026-08-16T15:00:00")
        zulu = article(published_at="2026-08-16T15:00:00Z")
        self.assertEqual(
            ranking._freshness_timestamp(naive, NOW),
            datetime(2026, 8, 16, 15, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            ranking._freshness_timestamp(zulu, NOW),
            datetime(2026, 8, 16, 15, 0, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
