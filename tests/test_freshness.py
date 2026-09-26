"""브리핑 후보 신선도 — 직전 브리핑 이후의 기사만 오늘 소식이다(freshness.py).

2026-09-13~26 발송 243건 판정에서 이틀 이상 늦은 발송이 45건, 재발송이 33건이었다.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import freshness as fr

KST = timezone(timedelta(hours=9))
CUTOFF = datetime(2026, 9, 25, 4, 13, tzinfo=KST)


def _item(h, published, importance="nice_to_know", **kw):
    return {"hash": h, "importance": importance, "published_at": published,
            "title_kr": f"제목 {h}", **kw}


class LastBriefTests(unittest.TestCase):
    def _log(self, rows):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        for row in rows:
            tmp.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_earliest_run_of_the_latest_previous_day(self):
        path = self._log([
            {"record_type": "selection_stats", "date": "2026-09-24",
             "generated_at": "2026-09-24T04:18:23+09:00"},
            {"record_type": "selection_stats", "date": "2026-09-25",
             "generated_at": "2026-09-25T09:11:53+09:00"},
            {"record_type": "selection_stats", "date": "2026-09-25",
             "generated_at": "2026-09-25T04:13:09+09:00"},
            {"record_type": "selection_stats", "date": "2026-09-26",
             "generated_at": "2026-09-26T06:50:47+09:00"},
            {"hash": "x", "date": "2026-09-25", "title_kr": "selection_stats 라는 낱말만 든 기사"},
        ])
        got = fr.last_brief_at("2026-09-26", path)
        self.assertEqual(got, datetime(2026, 9, 25, 4, 13, 9, tzinfo=KST),
                         "재실행 줄이나 오늘 줄을 기준으로 삼았다")

    def test_no_log_means_no_cutoff(self):
        self.assertIsNone(fr.last_brief_at("2026-09-26", Path("없는파일.jsonl")))


class StaleTests(unittest.TestCase):
    def test_article_before_the_last_brief_minus_grace_is_stale(self):
        old = _item("a", "2026-09-24T12:00:00+09:00")
        self.assertEqual(str(fr.stale_since(old, CUTOFF, 6)), "2026-09-24")

    def test_article_just_before_the_last_brief_survives_the_grace(self):
        """새벽에 게재돼 브리핑 직후에 수집된 기사를 잃지 않는다."""
        near = _item("b", "2026-09-25T01:30:00+09:00")
        self.assertIsNone(fr.stale_since(near, CUTOFF, 6))

    def test_date_only_board_item_compares_by_date(self):
        """공식 게시판은 날짜만 준다 — 자정으로 박힌 어제 보도자료는 오늘 소식이다."""
        board = _item("c", "2026-09-25T00:00:00+09:00")
        self.assertIsNone(fr.stale_since(board, CUTOFF, 0))
        older = _item("d", "2026-09-24T00:00:00+09:00")
        self.assertEqual(str(fr.stale_since(older, CUTOFF, 0)), "2026-09-24")

    def test_unknown_time_or_cutoff_passes(self):
        self.assertIsNone(fr.stale_since({"hash": "e"}, CUTOFF, 6))
        self.assertIsNone(fr.stale_since(_item("f", "2026-09-01T00:00:00+09:00"), None, 6))


class SplitTests(unittest.TestCase):
    def test_stale_nice_is_dropped_and_stale_must_read_is_dated(self):
        rows = [_item("new", "2026-09-25T20:00:00+09:00"),
                _item("old", "2026-09-23T10:00:00+09:00"),
                _item("key", "2026-09-22T12:14:00+09:00", importance="must_read")]
        kept, dropped = fr.split(rows, CUTOFF, fr.resolve_config({}))
        self.assertEqual([a["hash"] for a in kept], ["new", "key"])
        self.assertEqual([d["hash"] for d in dropped], ["old"])
        self.assertEqual(kept[1]["stale_since"], "2026-09-22",
                         "묵은 must_read 에 날짜가 안 붙었다 — 제목에 날짜를 달 수 없다")
        self.assertNotIn("stale_since", kept[0])

    def test_disabled_config_keeps_everything(self):
        rows = [_item("old", "2026-09-01T10:00:00+09:00")]
        kept, dropped = fr.split(rows, CUTOFF, fr.resolve_config({"freshness": {"enabled": False}}))
        self.assertEqual((len(kept), len(dropped)), (1, 0))

    def test_a_stale_label_from_yesterday_does_not_stick(self):
        """큐에 남은 dict 는 다음 날 다시 들어온다 — 어제 단 표시를 오늘 새로 판정한다."""
        row = _item("x", "2026-09-25T20:00:00+09:00", stale_since="2026-09-20")
        kept, _ = fr.split([row], CUTOFF, fr.resolve_config({}))
        self.assertNotIn("stale_since", kept[0])


class OrderAndLabelTests(unittest.TestCase):
    def test_a_dated_must_read_never_takes_the_first_slot(self):
        rows = [{"hash": "key", "stale_since": "2026-09-22"}, {"hash": "a"}, {"hash": "b"}]
        self.assertEqual([r["hash"] for r in fr.fresh_first(rows)], ["a", "key", "b"])

    def test_all_dated_rows_keep_their_order(self):
        rows = [{"hash": "k1", "stale_since": "2026-09-22"},
                {"hash": "k2", "stale_since": "2026-09-23"}]
        self.assertEqual(fr.fresh_first(rows), rows)

    def test_date_label(self):
        self.assertEqual(fr.date_label({"stale_since": "2026-09-22"}), "(9/22)")
        self.assertEqual(fr.date_label({}), "")


if __name__ == "__main__":
    unittest.main()
