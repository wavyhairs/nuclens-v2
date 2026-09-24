"""흐름 행에 사건 안의 단계를 싣는다 — 한 행의 날짜와 제목이 같은 기사에서 오게.

2026-09-24 라이브: 사건 6260 이 7/30~9/24 를 품었고, 흐름은 날짜를 첫 기사(7/30),
제목을 마지막 기사(9/24 공론화)에서 가져와 9월 뉴스가 7월 자리에 섰다.
"""
import unittest
from datetime import date

import event_retrieval
import thread_web
from event_retrieval import Event


def _event(issue_id, title, first):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date.fromisoformat(first), last_seen=date.fromisoformat(first),
        units=frozenset(), plants=frozenset(), entities=frozenset(),
        assets=frozenset(), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={},
    )


def _article(hash_, day, title, role="card", rank=None):
    return {"hash": hash_, "briefing_date": day, "title_kr": title, "member_role": role, "brief_rank": rank}


class CatalogStages(unittest.TestCase):
    def setUp(self):
        self.catalog = [{"issue_id": "issue-6260", "related_articles": [
            _article("c99b", "2026-09-24", "정부, 12차 전기본 수립 위해 3개월간 원전 공론화 추진", rank=1),
            _article("44a9", "2026-09-23", "원전 공론화 추진으로 12차 전기본 확정 연기", rank=4),
            _article("zz01", "2026-09-23", "같은 날 순위 낮은 카드", rank=9),
            _article("5976", "2026-07-30", "24.7GW 전력 더 필요한데 신규 원전 몇 기", rank=2),
            _article("ev01", None, "추가 근거", role="evidence"),
        ]}]
        events = [_event("issue-6260", "정부, 12차 전기본 수립 위해 3개월간 원전 공론화 추진", "2026-07-30"),
                  _event("issue-other", "카탈로그 밖 사건", "2026-08-01")]
        self.events = event_retrieval.with_catalog_stages(events, self.catalog)

    def test_one_stage_per_briefing_day_from_card_articles(self):
        stages = self.events[0].stages
        self.assertEqual([s["date"] for s in stages], ["2026-07-30", "2026-09-23", "2026-09-24"])
        self.assertEqual(stages[1]["hash"], "44a9", "같은 날은 브리핑 순위가 높은 카드")
        self.assertNotIn("ev01", [s["hash"] for s in stages], "근거 기사가 단계가 됐다")

    def test_each_stage_pairs_a_date_with_its_own_title(self):
        by_day = {s["date"]: s["title"] for s in self.events[0].stages}
        self.assertIn("공론화 추진", by_day["2026-09-24"])
        self.assertNotIn("공론화", by_day["2026-07-30"])

    def test_flow_row_carries_the_stages_and_keeps_its_old_fields(self):
        rows = thread_web._flow([self.events[0]], [])
        self.assertEqual(rows[0]["date"], "2026-07-30", "기존 칸(판정·카드가 읽는다)은 그대로")
        self.assertEqual([s["date"] for s in rows[0]["stages"]], ["2026-07-30", "2026-09-23", "2026-09-24"])

    def test_event_outside_the_catalog_has_no_stages(self):
        self.assertEqual(self.events[1].stages, ())
        self.assertEqual(thread_web._flow([self.events[1]], [])[0]["stages"], [])


if __name__ == "__main__":
    unittest.main()
