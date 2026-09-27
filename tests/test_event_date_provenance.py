"""수집 때 본문으로 확인한 사건일이, 본문이 사라진 뒤에도 살아남는가.

큐레이션은 본문을 보고 `event_date_source=article_text` 인 사건일을 검사한다.
본문은 저장하지 않으므로 그 뒤 단계(발송·캐시 재사용·사이트)는 같은 검사를
다시 할 수 없었고, 사건일을 '확인 불가'로 지웠다. 실측 2026-08-17~09-27:
사이트 빌드에서만 하루 약 34건씩 1,412건(PR #29 가 남긴 TODO).

여기서는 그 판정을 manifest 안에 봉인한다. 새 근거 체계가 아니다 — #48 이
제목·요약에 쓰는 같은 manifest, 같은 결속·봉인 검사를 그대로 쓴다.

레코드는 전부 **수집 경로 그대로** 만든다:
    refresh_evidence_manifest(본문 있음) → 큐 항목 / make_record → 각 단계 게이트
"""
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import _fake_tg  # noqa: E402,F401 — telegram_send 는 토큰이 없으면 import 시 종료한다
import article_quality_gate as gate  # noqa: E402
import daily_brief  # noqa: E402
import news_archive  # noqa: E402
import news_bot as nb  # noqa: E402

spec = importlib.util.spec_from_file_location("nuclens_build_data", ROOT / "web" / "build_data.py")
build = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(build)

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)

# 본문에는 날짜가 둘 있다. 봉인은 "본문에 날짜가 있다"가 아니라 "이 사건일이
# 이 근거로 확인됐다"여야 한다.
BODY = ("원자력안전위원회는 2026년 9월 26일 회의를 열고 고리 2호기 계속운전을 "
        "심의했다. 정부는 2024년 8월 14일 관련 정책을 발표한 바 있다.")
ARTICLE = {
    "hash": "provenance-case",
    "title": "원안위, 고리 2호기 계속운전 심의",
    "description": "원안위가 고리 2호기 계속운전 안건을 심의했다.",
    "link": "https://example.com/kori2",
    "domain": "example.com",
    "feed": "test",
    "pub": datetime(2026, 9, 26, 18, 0, tzinfo=KST),
}
CURATION = {
    "title_kr": "원안위, 고리 2호기 계속운전 심의",
    "summary": "원자력안전위원회가 고리 2호기 계속운전 안건을 심의했다.",
    "curation_status": "reviewed",
    "importance": "nice_to_know",
    "features": {},
    "event_date": "2026-09-26",
    "event_date_type": "occurrence",
    "event_date_precision": "day",
    "event_date_source": "article_text",
}


def collect(article: dict | None = None, curation: dict | None = None,
            *, body: str = BODY) -> tuple[dict, dict]:
    """수집이 하는 일: 본문으로 검사한 큐레이션과 manifest 를 남긴다."""
    article = {**ARTICLE, **(article or {})}
    cur = {**CURATION, **(curation or {})}
    integrity = nb.audit_curation_integrity(article, cur, body)
    cur = integrity.value
    cur["verified_evidence"] = nb.refresh_evidence_manifest(
        article, cur, body=body, force=True, now=NOW)
    cur["verified_source_components"] = gate.evidence_manifest_source_components(
        cur["verified_evidence"])
    binding = nb.evidence_binding(article, now=NOW)
    cur["hash"] = binding["hash"]
    cur["published_at"] = binding["published_at"]
    return article, cur


def queued(article: dict, cur: dict) -> dict:
    """news_bot 이 큐에 적는 항목에서 게이트가 읽는 필드."""
    return {
        "hash": cur["hash"], "title": article["title"],
        "title_kr": cur["title_kr"], "summary": cur["summary"],
        "link": article["link"], "domain": article["domain"],
        "curation_status": cur["curation_status"], "importance": cur["importance"],
        "features": cur["features"],
        **{key: cur.get(key) for key in (
            "event_date", "event_date_type", "event_date_precision", "event_date_source")},
        "source_excerpt": nb.clean_text(article.get("description", ""))[:600],
        "verified_evidence": cur["verified_evidence"],
        "verified_source_components": cur["verified_source_components"],
        "published_at": cur["published_at"],
        "queued_at": "2026-09-26T10:00:00+00:00",
    }


def archived(article: dict, cur: dict) -> dict:
    record = news_archive.make_record(article, cur, "2026-09-26T10:00:00+00:00")
    return build._normalize_archive_record(record)


def site_date(record: dict):
    visible, _stats = build.apply_archive_integrity_gate([record])
    return visible[0]["event_date"] if visible else "hidden"


def reseal(manifest: dict, **changes) -> dict:
    """봉인까지 다시 계산한 manifest. 봉인 검사가 아니라 **그 뒤의 규칙**을 보려는 것."""
    updated = {key: value for key, value in manifest.items()
               if key != "manifest_fingerprint"}
    updated.update(changes)
    updated["manifest_fingerprint"] = gate._digest_payload(updated)
    return updated


class SealedDateSurvivesWithoutTheBodyTests(unittest.TestCase):
    def test_collection_seals_the_exact_date_it_verified(self):
        _article, cur = collect()
        self.assertEqual(cur["verified_evidence"]["verified_event_date"], {
            "value": "2026-09-26", "type": "occurrence",
            "precision": "day", "source": "article_text"})
        # 본문 자체는 남기지 않는다.
        self.assertNotIn("고리 2호기 계속운전을 심의했다",
                         str(cur["verified_evidence"]))

    def test_site_build_keeps_the_date(self):
        article, cur = collect()
        self.assertEqual(site_date(archived(article, cur)), "2026-09-26")

    def test_daily_brief_keeps_the_date(self):
        article, cur = collect()
        eligible, held = daily_brief.screen_auto_delivery([queued(article, cur)])
        self.assertEqual(held, [])
        self.assertEqual(eligible[0]["event_date"], "2026-09-26")

    def test_cache_hit_keeps_the_date(self):
        """다음 크롤이 같은 기사를 캐시에서 다시 쓸 때는 본문이 없다."""
        article, cur = collect()
        again = nb.audit_curation_integrity(article, cur, "")
        self.assertEqual(again.value["event_date"], "2026-09-26")
        self.assertEqual(again.action, "allow")

    def test_description_sourced_date_is_sealed_too(self):
        """RSS 설명문도 아카이브에 남지 않는다 — 같은 방식으로 봉인해야 산다."""
        article, cur = collect(
            {"description": "원안위는 2026년 9월 26일 고리 2호기 안건을 심의했다."},
            {"event_date_source": "description"}, body="")
        self.assertEqual(cur["verified_evidence"]["verified_event_date"]["source"],
                         "description")
        self.assertEqual(site_date(archived(article, cur)), "2026-09-26")

    def test_relative_date_is_sealed_against_the_curation_reference(self):
        """'어제'는 큐레이션 검사와 같은 기준일로 풀어야 같은 날이 된다.

        KST 08:00 발행은 UTC 로는 전날이다. 봉인이 UTC 발행시각을 기준으로 삼으면
        큐레이션이 확인한 9/25 대신 9/24 를 찾아 봉인을 거절한다.
        """
        article, cur = collect(
            {"pub": datetime(2026, 9, 26, 8, 0, tzinfo=KST)},
            {"event_date": "2026-09-25"},
            body="원자력안전위원회는 어제 회의를 열고 고리 2호기 계속운전을 심의했다.")
        self.assertEqual(cur["event_date"], "2026-09-25", "전제: 큐레이션 검사를 통과")
        self.assertEqual(cur["verified_evidence"]["verified_event_date"]["value"],
                         "2026-09-25")

    def test_gate_is_idempotent(self):
        article, cur = collect()
        once, first = build.apply_archive_integrity_gate([archived(article, cur)])
        twice, second = build.apply_archive_integrity_gate(once)
        self.assertEqual(once, twice)
        self.assertEqual((first["sanitized"], second["sanitized"]), (0, 0))


class FailClosedTests(unittest.TestCase):
    def test_manifest_without_a_sealed_date_still_clears_it(self):
        """봉인 이전에 만든 manifest(기존 아카이브 전부)는 지금처럼 지운다."""
        article, cur = collect()
        legacy = {key: value for key, value in cur["verified_evidence"].items()
                  if key not in ("verified_event_date", "manifest_fingerprint")}
        legacy["manifest_fingerprint"] = gate._digest_payload(legacy)
        record = archived(article, {**cur, "verified_evidence": legacy})
        self.assertTrue(gate.evidence_manifest_is_valid(legacy, article=record),
                        "전제: manifest 자체는 유효하다")
        self.assertIsNone(site_date(record))

    def test_broken_seal_clears_the_date(self):
        article, cur = collect()
        forged = dict(cur["verified_evidence"])
        forged["verified_event_date"] = {**forged["verified_event_date"],
                                         "value": "2026-10-03"}
        record = archived(article, {**cur, "verified_evidence": forged,
                                    "event_date": "2026-10-03"})
        self.assertIsNone(site_date(record))

    def test_another_articles_seal_does_not_vouch_for_this_one(self):
        """제목이 같은 다른 기사(전재·중복 보도)라도 hash 결속이 다르면 무효다."""
        _source_article, source_cur = collect()
        article, cur = collect({"hash": "other-article"}, body="")
        self.assertIsNone(cur["event_date"], "전제: 이 기사는 스스로 확인하지 못했다")
        cur = {**cur, **{key: CURATION[key] for key in (
            "event_date", "event_date_type", "event_date_precision", "event_date_source")}}
        record = archived(article, {
            **cur,
            "verified_evidence": source_cur["verified_evidence"],
            "verified_source_components": source_cur["verified_source_components"]})
        self.assertIsNone(site_date(record))

    def test_changed_value_type_or_precision_is_not_vouched_for(self):
        article, cur = collect()
        for field, value in (("event_date", "2026-10-03"),
                             ("event_date_type", "scheduled"),
                             ("event_date_precision", "month")):
            with self.subTest(field=field):
                self.assertIsNone(site_date(archived(article, {**cur, field: value})))

    def test_date_the_body_does_not_state_is_never_sealed(self):
        """2026년 기사에 연도를 잘못 단 사건일(PR #28 실측 오류 유형)."""
        article, cur = collect(curation={"event_date": "2024-09-26"})
        self.assertIsNone(cur["event_date"], "큐레이션 검사가 본문과의 충돌을 잡는다")
        self.assertNotIn("verified_event_date", cur["verified_evidence"])

        # 큐레이션 검사를 우회한 값이 들어와도 봉인은 스스로 판정한다.
        manifest = gate.build_evidence_manifest(
            {"article_hash": ARTICLE["hash"], "title": ARTICLE["title"],
             "description": ARTICLE["description"], "article_text": BODY,
             "published_at": "2026-09-26T09:00:00+00:00"},
            article={"hash": ARTICLE["hash"], "title": ARTICLE["title"]},
            curation={**CURATION, "event_date": "2024-09-26"},
            reference_date=ARTICLE["pub"])
        self.assertNotIn("verified_event_date", manifest)

    def test_impossible_dates_are_cleared_even_with_a_valid_seal(self):
        """연대 규칙은 봉인보다 먼저다."""
        article, cur = collect()
        for label, changes in (
            ("implausible_year", {"event_date": "2206-09-26"}),
            ("future_completed_event", {"event_date": "2026-12-01",
                                        "event_date_type": "announcement"}),
        ):
            with self.subTest(label=label):
                claim = {**cur["verified_evidence"]["verified_event_date"],
                         "value": changes["event_date"],
                         "type": changes.get("event_date_type", "occurrence")}
                forged = reseal(cur["verified_evidence"], verified_event_date=claim)
                record = archived(article, {**cur, **changes, "verified_evidence": forged})
                self.assertIsNone(site_date(record))

    def test_body_that_contradicts_the_seal_wins(self):
        article, cur = collect()
        result = gate.audit_article_integrity(
            cur, source={"title": article["title"],
                         "article_text": "회의는 2026년 10월 3일 열렸다."},
            reference_date=article["pub"])
        self.assertIsNone(result.value["event_date"])

    def test_rebuilding_without_the_body_drops_the_seal(self):
        """원문이 바뀌어 manifest 를 다시 지을 때 본문이 없으면 봉인도 없다."""
        article, cur = collect()
        changed = {**article, "title": "원안위, 고리 2호기 계속운전 심의 결과 공개"}
        rebuilt = nb.refresh_evidence_manifest(
            changed, cur, body="", force=False, now=NOW)
        self.assertNotIn("verified_event_date", rebuilt)

    def test_delivery_excerpt_is_not_promoted_to_article_text(self):
        """발송 때의 source_excerpt 는 짧고 제목을 되풀이한다 — 본문 근거가 아니다."""
        article, cur = collect(body="")
        self.assertIsNone(cur["event_date"])
        self.assertNotIn("verified_event_date", cur["verified_evidence"])


class ExistingManifestContractTests(unittest.TestCase):
    def test_sealed_manifests_keep_the_existing_version(self):
        """버전을 올리면 이미 아카이브된 manifest 가 전부 무효가 된다(#48 후퇴)."""
        _article, cur = collect()
        self.assertEqual(cur["verified_evidence"]["version"],
                         gate.EVIDENCE_MANIFEST_VERSION)

    def test_title_and_summary_support_is_unchanged(self):
        """#48: 본문에만 있는 수치를 쓴 한국어 제목은 봉인된 근거로 통과한다."""
        article, cur = collect(
            {"title": "두산퓨얼셀, 하이엑시엄과 누적 8236억원 수주"},
            {"title_kr": "두산퓨얼셀, 하이엑시엄에 3,222억원 규모 연료전지 공급",
             "summary": "두산퓨얼셀이 하이엑시엄과 3,222억원 규모 공급계약을 체결했다.",
             "event_date": None, "event_date_type": "unknown",
             "event_date_precision": "unknown", "event_date_source": "unknown"},
            body="두산퓨얼셀이 하이엑시엄과 3222억원 규모 공급계약을 맺었다. "
                 "누적 계약액은 8236억원이다.")
        visible, stats = build.apply_archive_integrity_gate([archived(article, cur)])
        self.assertEqual(stats["quarantined"], 0, stats["quarantine_samples"])
        self.assertEqual(len(visible), 1)


if __name__ == "__main__":
    unittest.main()
