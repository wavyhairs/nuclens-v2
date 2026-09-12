"""오래 떨어진 사건을 후보로 **끌어올 수 있는가**.

이 모듈이 지키는 계약은 둘이다.

1. 원장(`issue_ledger.json`)이 장기 원본이고 색인은 언제든 다시 만드는 파생물이다.
2. 후보 생성은 recall 이고 최종 판정은 다른 단계다 — 여기서 합치지 않는다.
"""

import unittest
from datetime import date

import event_retrieval as er


def _event(issue_id, title, first, last, *, summary="", briefing=1):
    return {
        "issue_id": issue_id, "title": title, "summary": summary,
        "first_seen": first, "last_seen": last, "briefing_count": briefing,
        "topics": [], "hashes": [issue_id], "facts": {},
    }


class RetrievalTests(unittest.TestCase):
    def _index(self, rows):
        store = {"generated_at": "", "issues": {row["issue_id"]: row for row in rows}}
        import unittest.mock as mock
        with mock.patch.object(er.issue_ledger, "load_store", return_value=store):
            return er.build_index()

    def test_same_unit_across_months_is_retrieved(self):
        """창은 21일인데 후보는 **창 밖**에서 와야 한다 — 그것이 이 모듈의 전부다."""
        index = self._index([
            _event("a", "고리 2호기 계속운전 신청서 제출", "2026-03-02", "2026-03-04"),
            _event("b", "고리 2호기 계속운전 안전성 심사 착수", "2026-09-01", "2026-09-03"),
            _event("c", "전력수급기본계획 공청회 개최", "2026-08-20", "2026-08-21"),
        ])
        target = index.by_id["b"]
        rows = er.candidates(index, target, min_gap_days=30)
        self.assertEqual([row["issue_id"] for row in rows], ["a"])
        self.assertGreater(rows[0]["gap_days"], 150)
        self.assertEqual(rows[0]["signals"]["unit"], 1)

    def test_english_and_korean_name_the_same_unit(self):
        index = self._index([
            _event("a", "고리 2호기 계속운전 신청", "2026-03-02", "2026-03-04"),
            _event("b", "Kori Unit 2 continued operation review begins",
                   "2026-09-01", "2026-09-03"),
        ])
        rows = er.candidates(index, index.by_id["b"], min_gap_days=30)
        self.assertEqual([row["issue_id"] for row in rows], ["a"])

    def test_different_unit_is_pushed_down_not_deleted(self):
        """지우면 그런 쌍이 있었다는 사실이 평가에서 사라진다."""
        index = self._index([
            _event("a", "고리 3호기 계속운전 심사 착수", "2026-03-02", "2026-03-04"),
            _event("b", "고리 2호기 계속운전 심사 착수", "2026-09-01", "2026-09-03"),
        ])
        rows = er.candidates(index, index.by_id["b"], min_score=-99, min_gap_days=30)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["signals"]["unit_conflict"])
        self.assertLess(rows[0]["score"], 0)

    def test_topic_only_overlap_does_not_become_a_candidate(self):
        index = self._index([
            _event("a", "원전 산업 에너지 정책 동향 전망", "2026-03-02", "2026-03-04"),
            _event("b", "원자력 에너지 정책 산업 시장 안전", "2026-09-01", "2026-09-03"),
        ])
        rows = er.candidates(index, index.by_id["b"], min_gap_days=30)
        self.assertEqual(rows, [])

    def test_gap_is_zero_when_events_overlap_in_time(self):
        index = self._index([
            _event("a", "고리 2호기 계속운전 신청", "2026-09-01", "2026-09-10"),
            _event("b", "고리 2호기 계속운전 심사", "2026-09-05", "2026-09-12"),
        ])
        rows = er.candidates(index, index.by_id["b"])
        self.assertEqual(rows[0]["gap_days"], 0)

    def test_index_is_a_derived_artifact(self):
        """원본만 있으면 색인은 언제든 같은 모양으로 다시 선다."""
        rows = [_event("a", "고리 2호기 계속운전", "2026-03-02", "2026-03-04")]
        first, second = self._index(rows), self._index(rows)
        self.assertEqual(sorted(first.postings), sorted(second.postings))
        self.assertEqual(first.idf, second.idf)


if __name__ == "__main__":
    unittest.main()
