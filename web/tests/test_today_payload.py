"""첫 화면 한 벌(today.json)이 무엇을 담는가 — build_today_payload().

왜 잠그는가
-----------
이 페이로드는 **잘라 담기만** 한다. 여기서 값을 새로 계산하기 시작하면 같은 이슈를
today.json 과 issues.json 이 다르게 말하는 날이 오고, 그 증상은 "새로고침하면
고쳐진다"로만 나타난다.

특히 이슈 레코드는 카탈로그에서 와야 한다. briefings 안에 내장된 같은 이슈는 그
회차 시점의 related_articles 를 들고 있어(실측 734건 중 449건 불일치, 최대 1 vs 54)
상세의 타임라인이 조용히 잘린다. 화면도 최신 회차에서는 카탈로그를 쓴다
(app.js 의 briefingIssuesForDisplay).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_data as bd  # noqa: E402


def _briefing(date: str, issue_ids: list[str], **extra) -> dict:
    return {
        "date": date,
        "issue_count": len(issue_ids),
        "highlight_issues": [{"issue_id": key, "title": f"{key} 제목"} for key in issue_ids[:3]],
        "issues": [{"issue_id": key, "title": f"{key} 제목", "related_articles": [{"hash": "h1"}]}
                   for key in issue_ids],
        **extra,
    }


def _catalog(issue_ids: list[str]) -> list[dict]:
    return [{
        "issue_id": key,
        "title": f"{key} 제목",
        "card_why": f"{key} 한 줄",
        # 카탈로그 쪽이 누적본이라 근거가 더 많다 — 이 차이가 이 테스트의 핵심이다.
        "related_articles": [{"hash": "h1"}, {"hash": "h2"}, {"hash": "h3"}],
        "identity_diagnostics": {"버려질": "진단값"},
        "story_members": ["a", "b"],
    } for key in issue_ids]


class TodayPayloadTests(unittest.TestCase):
    def test_takes_the_latest_briefing(self):
        briefings = [_briefing("2026-09-13", ["i1", "i2"]), _briefing("2026-09-12", ["i9"])]
        payload = bd.build_today_payload(briefings, _catalog(["i1", "i2", "i9"]), {"x": 1})
        self.assertEqual(payload["date"], "2026-09-13")
        self.assertEqual([row["issue_id"] for row in payload["issues"]], ["i1", "i2"])

    def test_issue_records_come_from_the_catalog_not_the_briefing(self):
        """회차 스냅샷을 담으면 타임라인이 잘린 채로 나간다."""
        briefings = [_briefing("2026-09-13", ["i1"])]
        payload = bd.build_today_payload(briefings, _catalog(["i1"]), {})
        self.assertEqual(len(payload["issues"][0]["related_articles"]), 3,
                         "회차 스냅샷(1건)을 담았다 — 카탈로그 누적본(3건)이어야 한다")
        self.assertEqual(payload["issues"][0]["card_why"], "i1 한 줄")

    def test_issue_missing_from_catalog_falls_back_to_the_briefing_row(self):
        """빠뜨리면 그 이슈만 첫 화면에서 사라졌다가 본 데이터가 오면 나타난다."""
        briefings = [_briefing("2026-09-13", ["i1", "고아"])]
        payload = bd.build_today_payload(briefings, _catalog(["i1"]), {})
        self.assertEqual({row["issue_id"] for row in payload["issues"]}, {"i1", "고아"})

    def test_drops_only_fields_no_screen_reads(self):
        briefings = [_briefing("2026-09-13", ["i1"])]
        payload = bd.build_today_payload(briefings, _catalog(["i1"]), {})
        row = payload["issues"][0]
        for field in ("identity_diagnostics", "story_members"):
            self.assertNotIn(field, row, f"{field} 는 어느 화면도 읽지 않는다")
        # 화면이 읽는 필드는 남아 있어야 한다.
        for field in ("issue_id", "title", "card_why", "related_articles"):
            self.assertIn(field, row)

    def test_carries_every_briefing_date_for_the_date_picker(self):
        """날짜 이동 칸이 briefings.json 없이도 서야 한다."""
        briefings = [_briefing("2026-09-13", ["i1"]), _briefing("2026-09-12", ["i2"]),
                     _briefing("2026-09-11", ["i3"])]
        payload = bd.build_today_payload(briefings, _catalog(["i1", "i2", "i3"]), {})
        self.assertEqual(payload["dates"], ["2026-09-13", "2026-09-12", "2026-09-11"])

    def test_briefing_meta_travels_without_its_issue_array(self):
        briefings = [_briefing("2026-09-13", ["i1"], article_count=42)]
        payload = bd.build_today_payload(briefings, _catalog(["i1"]), {})
        self.assertEqual(payload["briefing"]["article_count"], 42)
        self.assertEqual(len(payload["briefing"]["highlight_issues"]), 1)
        self.assertNotIn("issues", payload["briefing"],
                         "이슈 배열이 두 번 실렸다 — 페이로드가 두 배가 된다")

    def test_empty_build_does_not_explode(self):
        payload = bd.build_today_payload([], [], {})
        self.assertEqual(payload["date"], "")
        self.assertEqual(payload["issues"], [])


if __name__ == "__main__":
    unittest.main()
