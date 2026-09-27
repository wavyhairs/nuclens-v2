"""봉인 도입 전 기사의 사건일 복원 — tools/restore_event_date_seals.py 와 빌드의 사이드카.

레코드는 **봉인 도입 전 수집 경로 그대로** 만든다: 본문으로 manifest 를 짓되
큐레이션을 넘기지 않아 봉인이 없다(PR #205 이전 모양). 사이트 게이트는 그 사건일을
'원문 근거를 다시 확인할 수 없음'으로 지운다.
"""
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import article_quality_gate as gate  # noqa: E402
import news_archive  # noqa: E402
import news_bot as nb  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


build = _load("nuclens_build_data", ROOT / "web" / "build_data.py")
restore = _load("restore_event_date_seals", ROOT / "tools" / "restore_event_date_seals.py")

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
BODY = ("원자력안전위원회는 2026년 8월 28일 회의를 열고 고리 2호기 계속운전을 "
        "심의했다. 정부는 2024년 8월 14일 관련 정책을 발표한 바 있다.")
ARTICLE = {
    "hash": "legacy-case",
    "title": "원안위, 고리 2호기 계속운전 심의",
    "description": "원안위가 고리 2호기 계속운전 안건을 심의했다.",
    "link": "https://example.com/kori2", "domain": "example.com", "feed": "test",
    "pub": datetime(2026, 8, 28, 18, 0, tzinfo=KST),
}
CURATION = {
    "title_kr": "원안위, 고리 2호기 계속운전 심의",
    "summary": "원자력안전위원회가 고리 2호기 계속운전 안건을 심의했다.",
    "curation_status": "reviewed", "importance": "nice_to_know", "features": {},
    "event_date": "2026-08-28", "event_date_type": "occurrence",
    "event_date_precision": "day", "event_date_source": "article_text",
}


def legacy_record(**curation) -> dict:
    """봉인 도입 전 수집이 아카이브에 남긴 레코드."""
    bound = nb.evidence_binding(ARTICLE, now=NOW)
    manifest = gate.build_evidence_manifest(
        {"article_hash": bound["hash"], "title": bound["title"],
         "description": bound["source_excerpt"], "article_text": BODY,
         "published_at": bound["published_at"]},
        article=bound)
    cur = {**CURATION, **curation, "verified_evidence": manifest,
           "verified_source_components": gate.evidence_manifest_source_components(manifest),
           "hash": bound["hash"], "published_at": bound["published_at"]}
    record = news_archive.make_record(ARTICLE, cur, "2026-08-28T10:00:00+00:00")
    return build._normalize_archive_record(record)


def site_date(record: dict):
    visible, _stats = build.apply_archive_integrity_gate([record])
    return visible[0]["event_date"] if visible else "hidden"


class RestoreEventDateSealTests(unittest.TestCase):
    def setUp(self):
        build._EVENT_DATE_SEALS = {}
        self.addCleanup(setattr, build, "_EVENT_DATE_SEALS", None)

    def with_sidecar(self, record: dict, entry: dict) -> dict:
        build._EVENT_DATE_SEALS = {record["hash"]: entry}
        return build.apply_event_date_seal(dict(record))

    def test_premise_a_legacy_record_loses_its_date_on_the_site(self):
        record = legacy_record()
        self.assertNotIn("verified_event_date", record["verified_evidence"])
        self.assertIsNone(site_date(record))
        self.assertEqual([record], restore.targets(build, [record]))

    def test_refetched_body_that_states_the_date_restores_it(self):
        record = legacy_record()
        entry, problem = restore.judge(build, record, BODY, today="2026-09-27")
        self.assertEqual(problem, "")
        self.assertEqual(entry["verified_event_date"], {
            "value": "2026-08-28", "type": "occurrence",
            "precision": "day", "source": "article_text"})
        self.assertEqual(entry["base_fingerprint"],
                         record["verified_evidence"]["manifest_fingerprint"])
        self.assertNotIn("고리 2호기", str(entry), "본문 자체는 남기지 않는다")

        restored = self.with_sidecar(record, entry)
        self.assertEqual(site_date(restored), "2026-08-28")
        self.assertEqual([], restore.targets(build, [restored]), "복원된 기사는 다시 대상이 아니다")

    def test_body_that_no_longer_states_the_date_is_not_restored(self):
        record = legacy_record()
        entry, problem = restore.judge(
            build, record, "원안위는 2026년 9월 4일 고리 2호기 안건을 다시 심의했다.",
            today="2026-09-27")
        self.assertEqual(entry, {})
        self.assertEqual(problem, "source_conflict")

    def test_sidecar_for_a_different_manifest_is_ignored(self):
        """확인할 때 본 manifest 가 아니면 얹지 않는다 — 다른 근거에 옛 판정을 붙이지 않는다."""
        record = legacy_record()
        entry, _problem = restore.judge(build, record, BODY, today="2026-09-27")
        stale = self.with_sidecar(record, {**entry, "base_fingerprint": "0" * 64})
        self.assertIsNone(site_date(stale))

    def test_sidecar_does_not_vouch_for_changed_fields(self):
        record = legacy_record()
        entry, _problem = restore.judge(build, record, BODY, today="2026-09-27")
        for field, value in (("event_date", "2026-09-04"),
                             ("event_date_type", "scheduled")):
            with self.subTest(field=field):
                changed = self.with_sidecar({**record, field: value}, entry)
                self.assertIsNone(site_date(changed))

    def test_sealing_keeps_the_binding_and_version(self):
        record = legacy_record()
        manifest = record["verified_evidence"]
        claim = gate.verify_event_date_claim(
            record, {"title": record["title"], "article_text": BODY}, record["pub"])
        sealed = gate.with_sealed_event_date(manifest, claim)
        for key in ("version", "article_hash", "source_components", "source_fingerprint",
                    "entities", "claims", "quantities"):
            self.assertEqual(sealed[key], manifest[key], key)
        self.assertTrue(gate.evidence_manifest_is_valid(sealed, article=record))
        self.assertNotEqual(sealed["manifest_fingerprint"], manifest["manifest_fingerprint"])

    def test_description_sourced_dates_are_not_refetch_targets_for_bodies(self):
        """설명문은 아카이브에 남지 않고 다시 받을 수도 없다 — 본문으로 대신 확인하지 않는다."""
        record = legacy_record(event_date_source="description")
        entry, problem = restore.judge(build, record, BODY, today="2026-09-27")
        self.assertEqual(entry, {})
        self.assertEqual(problem, "source_unavailable")


if __name__ == "__main__":
    unittest.main()
