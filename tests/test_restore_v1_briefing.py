"""tools/restore_v1_briefing.py — v1 에만 남은 회차를 되살리는 도구의 계약.

delivery_log 는 이 저장소의 DB다. 여기에 잘못 덧붙이면 이미 나간 브리핑의
날짜가 바뀌거나 없는 기사에 발송 기록만 남는다. 그래서 이 도구는 쓰기 전에
세 가지를 확인한다 — 그 셋을 여기서 고정한다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import restore_v1_briefing as restore  # noqa: E402


def news_row(hash_: str, date: str, **extra) -> dict:
    return {"hash": hash_, "briefing_date": date, "title_kr": f"기사 {hash_}",
            "domain": "example.com", "section": "smr", "region": "해외",
            "selection_score": 20.0, "selection_reasons": ["정책 결정"], **extra}


class RestoreGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "archive").mkdir()
        self._saved = (restore.DELIVERY_LOG, restore.ARCHIVE_DIR)
        restore.DELIVERY_LOG = root / "delivery_log.jsonl"
        restore.ARCHIVE_DIR = root / "archive"
        self.root = root

    def tearDown(self):
        restore.DELIVERY_LOG, restore.ARCHIVE_DIR = self._saved
        self.tmp.cleanup()

    def write_archive(self, hashes):
        (self.root / "archive" / "2026-08.jsonl").write_text(
            "\n".join(json.dumps({"hash": h}) for h in hashes), encoding="utf-8")

    def write_log(self, rows):
        restore.DELIVERY_LOG.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    def test_articles_v2_already_sent_on_another_day_are_skipped(self):
        """🔴 이걸 안 막으면 오늘 나간 브리핑에서 기사가 사라진다.

        v2 에 08-15 가 없었으므로 그날 v1 이 보낸 기사 일부는 큐에 남아 08-16 에
        v2 로 실제 발송됐다. load_deliveries 는 hash 당 **마지막** 레코드를 쓰므로,
        뒤에 붙는 복원본이 그 기사들을 08-15 로 되돌려 놓는다 — 실측에서 17건 중
        9건이 오늘 브리핑에서 빠져 어제로 옮겨 갔다.

        두 타임라인 다 사실이지만 v2 에서 참인 것은 v2 가 보낸 날짜다.
        """
        self.write_log([{"hash": "dup", "date": "2026-08-16", "title_kr": "오늘 나간 기사"}])
        taken = restore.already_delivered("2026-08-15")
        self.assertEqual({"dup": "2026-08-16"}, taken)

        news = [news_row("dup", "2026-08-15"), news_row("fresh", "2026-08-15")]
        records = restore.build_records(news, "2026-08-15", "src")
        kept = [r for r in records if r["hash"] not in taken]
        self.assertEqual(["fresh"], [r["hash"] for r in kept])

    def test_same_date_records_do_not_block_themselves(self):
        """복원 대상 날짜의 레코드는 겹침 판정에서 빼야 한다 — 아니면 재실행이 꼬인다."""
        self.write_log([{"hash": "a", "date": "2026-08-15"}])
        self.assertEqual({}, restore.already_delivered("2026-08-15"))

    def test_refuses_to_write_twice(self):
        self.write_log([{"hash": "a", "date": "2026-08-15", "title_kr": "이미 있음"}])
        self.assertEqual({"2026-08-15": 1}, restore.delivered_dates())

    def test_archive_hashes_are_the_join_key(self):
        """archive 에 없는 기사에 발송 기록만 붙이면 build_data 조인이 빈다."""
        self.write_archive(["a", "b"])
        self.assertEqual({"a", "b"}, restore.archive_hashes())

    def test_record_carries_its_provenance_and_no_invented_breakdown(self):
        """breakdown 을 지어내지 않는다 — '왜 뽑혔나'를 설명하는 자리다."""
        records = restore.build_records([news_row("a", "2026-08-15")], "2026-08-15", "src")
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertNotIn("breakdown", record)
        self.assertNotIn("story_article_count", record)
        self.assertEqual("src", record["restored_from"])
        self.assertTrue(record["restored_at"])
        self.assertEqual(["정책 결정"], record["selection_reasons"])
        self.assertEqual(20.0, record["score"])

    def test_only_the_target_date_is_taken_from_the_source(self):
        news = [news_row("a", "2026-08-15"), news_row("b", "2026-08-14"),
                {"hash": "", "briefing_date": "2026-08-15"}]
        records = restore.build_records(news, "2026-08-15", "src")
        self.assertEqual(["a"], [r["hash"] for r in records])

    def test_stats_are_skipped_when_the_source_has_none(self):
        """v1 도 08-15 통계는 없었다. 없는 것을 0 으로 채우면 거짓 진술이 된다."""
        rows = [{"date": "2026-08-15", "candidate_count": None, "issue_count": 16}]
        self.assertIsNone(restore.build_stats(rows, "2026-08-15", "src"))

        rows = [{"date": "2026-08-15", "candidate_count": 300, "below_floor_count": 40,
                 "issue_count": 16, "pipeline_status": "ok"}]
        stats = restore.build_stats(rows, "2026-08-15", "src")
        self.assertEqual("selection_stats", stats["record_type"])
        # build_data.selection_view 는 합계만 읽는다 — 지역 분해는 추측이라 안 한다.
        total = (stats["domestic"]["candidate_count"] + stats["overseas"]["candidate_count"])
        self.assertEqual(300, total)


if __name__ == "__main__":
    unittest.main()
