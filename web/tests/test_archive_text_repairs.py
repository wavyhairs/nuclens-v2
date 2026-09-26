"""아카이브 제목·요약 수선이 빌드에 얹힌다 — 원본 JSONL 은 다시 쓰지 않는다.

2026-09-26 발송분 1,044건 점검에서 제목이 틀린 28건(배경 사실을 새 사건처럼 17 등)과
사이트 21일 창의 미발송분 점검에서 27건을 archive_repairs.json 에 적었다.
국가 수선(country_repairs)은 빌드가 읽기 시점에 얹었지만 제목·요약 수선은
`--migrate-quality` 에서만 반영돼 사이트에 닿지 않았다.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_data as bd  # noqa: E402

REPAIRS = ROOT.parent / "archive_repairs.json"
ARCHIVE = ROOT.parent / "archive"


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._saved = bd._TEXT_REPAIRS
        bd._TEXT_REPAIRS = {"h1": {"title_kr": "고친 제목", "summary": "고친 요약"}}
        self.addCleanup(setattr, bd, "_TEXT_REPAIRS", self._saved)

    def test_a_repaired_record_gets_the_new_text(self):
        record = bd.apply_text_repairs({"hash": "h1", "title_kr": "옛 제목",
                                        "summary": "옛 요약", "detail": "상세"})
        self.assertEqual((record["title_kr"], record["summary"], record["detail"]),
                         ("고친 제목", "고친 요약", "상세"))

    def test_other_records_are_untouched(self):
        record = {"hash": "h2", "title_kr": "그대로"}
        self.assertEqual(bd.apply_text_repairs(dict(record)), record)


class RepairFileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repairs = json.loads(REPAIRS.read_text(encoding="utf-8"))
        cls.archive = {}
        for path in sorted(ARCHIVE.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                cls.archive[row.get("hash")] = row

    def _text_entries(self):
        return {key: entry for key, entry in self.repairs.items()
                if isinstance(entry, dict) and any(f in entry for f in bd.TEXT_REPAIR_FIELDS)}

    def test_every_text_repair_points_at_an_archived_article(self):
        missing = [key for key in self._text_entries() if key not in self.archive]
        self.assertEqual(missing, [], "수선이 없는 기사를 가리킨다 — 해시가 틀렸거나 아카이브에서 빠졌다")

    def test_text_repairs_are_non_empty_and_carry_a_reason(self):
        for key, entry in self._text_entries().items():
            with self.subTest(hash=key):
                for field in bd.TEXT_REPAIR_FIELDS:
                    if field in entry:
                        self.assertTrue(str(entry[field]).strip(), f"{field} 가 비었다")
                if str(entry.get("reason", "")).startswith("2026-09-26"):
                    self.assertTrue(set(entry) & set(bd.TEXT_REPAIR_FIELDS),
                                    "사유만 있고 고친 칸이 없다")

    def test_the_audit_batch_passes_the_curation_contract(self):
        """빌드의 데이터 품질 게이트와 같은 규칙 — 어기면 배포가 거기서 죽는다(요약 120자)."""
        sys.path.insert(0, str(ROOT.parent))
        from data_quality import curation_errors
        for key, entry in self._text_entries().items():
            if not str(entry.get("reason", "")).startswith("2026-09-26"):
                continue
            record = dict(self.archive[key])
            record.update({f: entry[f] for f in bd.TEXT_REPAIR_FIELDS if f in entry})
            with self.subTest(hash=key):
                self.assertEqual([e for e in curation_errors(record, summary_limit=120)
                                  if e.split(":")[0] in bd.TEXT_REPAIR_FIELDS], [])

    def test_the_audit_batches_are_all_there(self):
        reasons = [str(entry.get("reason", "")) for entry in self._text_entries().values()]
        self.assertEqual(sum(r.startswith("2026-09-26 발송분 점검") for r in reasons), 28)
        self.assertEqual(sum(r.startswith("2026-09-26 사이트 창 점검") for r in reasons), 27)


if __name__ == "__main__":
    unittest.main()
