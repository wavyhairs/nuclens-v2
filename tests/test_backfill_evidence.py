"""backfill_evidence.py — 과거 데이터 점진 재검증·백필 계약.

이 스크립트가 지켜야 하는 것은 넷이다.
  ① 기본은 dry-run — 무엇이 왜 바뀌는지 먼저 보여 주고 아무것도 안 쓴다.
  ② 멱등 — 두 번째 실행은 0건이어야 한다(중간에 죽어도 이어서 돌릴 수 있다).
  ③ 근거 없이 올리지 않는다 — 발행시각을 지어내지 않고, 확인 안 되는 사건일을
     남기지 않고, reviewed/verified 로 승격하지 않는다.
  ④ 기록을 잃지 않는다 — 읽지 못한 줄까지 원문 그대로 살아남는다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import article_quality_gate as gate
import backfill_evidence as backfill


CURATED_ROW = {
    "importance": "must_read",
    "title": "KHNP wins Czech Dukovany reactor construction contract",
    "title_kr": "한국수력원자력, 체코 두코바니 원전 건설 계약 수주",
    "summary": "한국수력원자력이 체코 두코바니 신규 원전 2기 건설 계약을 따냈다.",
    "link": "https://example.com/a",
    "domain": "example.com",
    "cached_at": "2026-08-14T02:00:00+00:00",
    "features": {"event_type": "contract_award"},
}
ARCHIVE_ROW = {
    "v": 2, "hash": "aaaa1111bbbb2222",
    "archived_at": "2026-08-14T02:00:00+00:00",
    "pub": "2026-08-13T21:10:00+00:00",
    "url": "https://example.com/a",
    "title": CURATED_ROW["title"], "title_kr": CURATED_ROW["title_kr"],
    "summary": CURATED_ROW["summary"], "importance": "must_read",
}


class BackfillSandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self._orig = (backfill.CURATED_FILE, backfill.QUEUE_FILE,
                      backfill.ARCHIVE_DIR)
        self.addCleanup(self._restore)
        backfill.CURATED_FILE = base / "curated.json"
        backfill.QUEUE_FILE = base / "digest_queue.json"
        backfill.ARCHIVE_DIR = base / "archive"
        backfill.ARCHIVE_DIR.mkdir()
        self.write_curated({"aaaa1111bbbb2222": dict(CURATED_ROW)})
        self.write_queue([])
        self.write_archive([dict(ARCHIVE_ROW)])

    def _restore(self):
        (backfill.CURATED_FILE, backfill.QUEUE_FILE,
         backfill.ARCHIVE_DIR) = self._orig

    def write_curated(self, payload):
        backfill.CURATED_FILE.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def write_queue(self, rows):
        backfill.QUEUE_FILE.write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def write_archive(self, rows, name="2026-08.jsonl"):
        (backfill.ARCHIVE_DIR / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8")

    def curated(self):
        return json.loads(backfill.CURATED_FILE.read_text(encoding="utf-8"))

    def queue(self):
        return json.loads(backfill.QUEUE_FILE.read_text(encoding="utf-8"))

    def archive(self, name="2026-08.jsonl"):
        return (backfill.ARCHIVE_DIR / name).read_text(encoding="utf-8").splitlines()

    def run_backfill(self, *args):
        return backfill.main(list(args))


class DryRunTests(BackfillSandbox):
    def test_dry_run_reports_but_writes_nothing(self):
        before = backfill.CURATED_FILE.read_text(encoding="utf-8")
        self.assertEqual(self.run_backfill("--samples", "0"), 0)
        self.assertEqual(backfill.CURATED_FILE.read_text(encoding="utf-8"), before)

    def test_dry_run_counts_match_what_apply_does(self):
        rows = backfill.load_archive_lines()
        published = backfill.archive_publication_times(rows)
        preview = backfill.run_curated(published, apply=False, samples=0)
        applied = backfill.run_curated(published, apply=True, samples=0)
        self.assertEqual(preview.as_dict()["reasons"], applied.as_dict()["reasons"])

    def test_report_names_the_reason_for_every_change(self):
        rows = backfill.load_archive_lines()
        report = backfill.run_curated(
            backfill.archive_publication_times(rows), apply=False, samples=2)
        self.assertIn("published_at_recovered", report.reasons)
        self.assertIn("manifest_backfilled", report.reasons)
        self.assertEqual(report.changed, 1)
        self.assertTrue(report.samples["published_at_recovered"])


class IdempotencyTests(BackfillSandbox):
    def test_second_apply_changes_nothing(self):
        self.run_backfill("--apply", "--samples", "0")
        after_first = backfill.CURATED_FILE.read_text(encoding="utf-8")
        rows = backfill.load_archive_lines()
        report = backfill.run_curated(
            backfill.archive_publication_times(rows), apply=True, samples=0)
        self.assertEqual(report.changed, 0)
        self.assertEqual(backfill.CURATED_FILE.read_text(encoding="utf-8"),
                         after_first)

    def test_interrupted_run_resumes(self):
        """curated 만 반영되고 죽어도, 다음 실행이 나머지를 이어서 끝낸다."""
        self.run_backfill("--apply", "--targets", "curated", "--samples", "0")
        self.assertIn("verified_evidence", self.curated()["aaaa1111bbbb2222"])
        self.assertNotIn("verified_evidence", json.loads(self.archive()[0]))
        self.run_backfill("--apply", "--samples", "0")
        self.assertIn("verified_evidence", json.loads(self.archive()[0]))


class EvidenceRuleTests(BackfillSandbox):
    def test_published_at_comes_from_the_archive_not_from_now(self):
        self.run_backfill("--apply", "--samples", "0")
        row = self.curated()["aaaa1111bbbb2222"]
        self.assertEqual(row["published_at"], ARCHIVE_ROW["pub"])
        self.assertEqual(row["published_at_source"], "archive_pub")

    def test_missing_publication_time_is_marked_not_invented(self):
        """모르는 발행시각을 cached_at 으로 채우면 그건 위조다."""
        self.write_archive([{**ARCHIVE_ROW, "pub": ""}])
        self.run_backfill("--apply", "--samples", "0")
        row = self.curated()["aaaa1111bbbb2222"]
        self.assertNotIn("published_at", row)
        self.assertEqual(row["published_at_fallback"], "cached_at")

    def test_backfilled_manifest_validates_against_its_record(self):
        self.run_backfill("--apply", "--samples", "0")
        row = self.curated()["aaaa1111bbbb2222"]
        self.assertTrue(gate.evidence_manifest_is_valid(
            row["verified_evidence"], article=row))

    def test_manifest_does_not_claim_body_facts_it_never_saw(self):
        """본문을 저장하지 않았으므로 manifest 도 제목이 말한 것까지만 봉인한다."""
        self.run_backfill("--apply", "--samples", "0")
        manifest = self.curated()["aaaa1111bbbb2222"]["verified_evidence"]
        self.assertIn("khnp", manifest["entities"])
        self.assertNotIn("westinghouse", manifest["entities"])

    def test_unverifiable_event_date_is_cleared(self):
        """본문 근거가 남아 있지 않은데 '본문에서 봤다'는 날짜는 확인할 수 없다."""
        self.write_curated({"aaaa1111bbbb2222": {
            **CURATED_ROW, "event_date": "2026-08-10",
            "event_date_type": "announcement", "event_date_precision": "day",
            "event_date_source": "article_text"}})
        self.run_backfill("--apply", "--samples", "0")
        row = self.curated()["aaaa1111bbbb2222"]
        self.assertIsNone(row["event_date"])
        self.assertEqual(row["event_date_source"], "unknown")

    def test_event_date_confirmed_by_the_title_survives(self):
        self.write_curated({"aaaa1111bbbb2222": {
            **CURATED_ROW,
            "title": "KHNP signs Czech Dukovany contract on August 13, 2026",
            "event_date": "2026-08-13", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "title"}})
        self.run_backfill("--apply", "--samples", "0")
        self.assertEqual(self.curated()["aaaa1111bbbb2222"]["event_date"],
                         "2026-08-13")

    def test_legacy_record_is_not_promoted_to_reviewed_or_verified(self):
        """근거가 없는 옛 레코드를 '검증됨'으로 올리는 것이 가장 위험한 실패다."""
        self.write_curated({"aaaa1111bbbb2222": {
            k: v for k, v in CURATED_ROW.items() if k != "features"}})
        self.run_backfill("--apply", "--samples", "0")
        row = self.curated()["aaaa1111bbbb2222"]
        self.assertNotIn("curation_status", row)
        self.assertNotEqual(row.get("curation_status"), "reviewed")

    def test_source_mismatch_is_quarantined_and_gets_no_manifest(self):
        self.write_curated({"aaaa1111bbbb2222": {
            **CURATED_ROW,
            "title": "Cameco starts construction at a new Canadian uranium mine",
            "title_kr": "스페인 알마라즈 원전 수명 연장 결정",
            "summary": "스페인 정부가 알마라즈 원전의 가동 시한을 연장했다."}})
        self.run_backfill("--apply", "--samples", "0")
        row = self.curated()["aaaa1111bbbb2222"]
        self.assertEqual(row["curation_status"], "quarantined")
        self.assertIn("title_source_mismatch", row["quarantine_reason"])
        self.assertNotIn("verified_evidence", row)


class DataPreservationTests(BackfillSandbox):
    def test_unparsable_archive_line_is_preserved_verbatim(self):
        path = backfill.ARCHIVE_DIR / "2026-08.jsonl"
        path.write_text(json.dumps(ARCHIVE_ROW, ensure_ascii=False) + "\n"
                        + "{ 깨진 줄\n", encoding="utf-8")
        self.run_backfill("--apply", "--samples", "0")
        lines = self.archive()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], "{ 깨진 줄")

    def test_non_dict_values_survive_untouched(self):
        self.write_curated({"aaaa1111bbbb2222": dict(CURATED_ROW),
                            "broken": "그냥 문자열"})
        self.run_backfill("--apply", "--samples", "0")
        self.assertEqual(self.curated()["broken"], "그냥 문자열")

    def test_no_record_is_dropped(self):
        rows = [{**ARCHIVE_ROW, "hash": f"h{i:016d}"} for i in range(20)]
        self.write_archive(rows)
        self.run_backfill("--apply", "--samples", "0")
        self.assertEqual(len(self.archive()), 20)

    def test_delivery_eligibility_is_not_narrowed_by_the_backfill(self):
        """검증을 켰더니 브리핑이 비는 것이 이 작업에서 가장 비싼 실패다."""
        rows = [{**CURATED_ROW, "hash": f"h{i:016d}",
                 "event_date": "2026-08-10", "event_date_type": "announcement",
                 "event_date_precision": "day",
                 "event_date_source": "article_text"} for i in range(10)]
        impact = backfill.delivery_impact(rows)
        self.assertEqual(impact["before"], impact["after"])


if __name__ == "__main__":
    unittest.main()
