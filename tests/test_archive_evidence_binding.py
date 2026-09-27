"""사이트 빌드의 아카이브 무결성 게이트가 수집 때 봉인한 근거를 다시 읽는가.

발송 게이트(#48)는 본문에서 만든 manifest 로 제목·요약을 판정한다. 사이트
빌드도 같은 게이트를 부르는데, 아카이브의 `pub`(원래 시간대, +09:00)을
결속 검사에 넘겨 UTC 로 봉인된 발행시각(PR #31)과 어긋났다. 실측 2026-09-27:
manifest 18,481개 중 7,235개만 유효로 읽혔고, 텔레그램이 통과시킨 기사 25건이
사이트에서만 격리돼 있었다.

레코드는 전부 **수집 경로 그대로** 만든다 — refresh_evidence_manifest →
make_record → _normalize_archive_record. 손으로 짠 manifest 로는 이 어긋남이
재현되지 않는다.
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

spec = importlib.util.spec_from_file_location("nuclens_build_data", ROOT / "web" / "build_data.py")
build = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(build)

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)

# 실측 사례(94cf4142be76d3c3)의 모양. 원제목은 누적액만 말하고 번역 제목은 본문에
# 있는 이번 계약액을 앞세운다 — 본문 근거 없이는 수치 충돌로 보인다.
ARTICLE = {
    "hash": "binding-case",
    "title": "두산퓨얼셀, 2주 만에 또 대형 수주…하이엑시엄과 누적 8236억원",
    "description": "두산퓨얼셀이 하이엑시엄과 대형 공급 계약을 맺었다.",
    "link": "https://example.com/doosan",
    "domain": "example.com",
    "feed": "test",
    "pub": datetime(2026, 9, 15, 10, 30, tzinfo=KST),
}
CURATION = {
    "title_kr": "두산퓨얼셀, 미국 하이엑시엄에 3,222억원 규모 연료전지 추가 공급",
    "summary": "두산퓨얼셀이 하이엑시엄과 3,222억원 규모의 연료전지 공급계약을 체결했다.",
    "curation_status": "reviewed",
    "importance": "nice_to_know",
    "features": {},
}
BODY = ("두산퓨얼셀이 미국 하이엑시엄과 3222억원 규모의 인산형 연료전지 공급계약을 "
        "체결했다. 두 회사의 누적 계약액은 8236억원이다.")


def archived(article: dict | None = None, *, body: str = BODY) -> dict:
    """수집이 아카이브에 적는 레코드를 웹 빌드가 읽는 모양으로 돌려준다."""
    article = {**ARTICLE, **(article or {})}
    cur = dict(CURATION)
    cur["verified_evidence"] = nb.refresh_evidence_manifest(
        article, cur, body=body, force=True, now=NOW)
    cur["verified_source_components"] = gate.evidence_manifest_source_components(
        cur["verified_evidence"])
    binding = nb.evidence_binding(article, now=NOW)
    cur["hash"] = binding["hash"]
    cur["published_at"] = binding["published_at"]
    record = news_archive.make_record(article, cur, "2026-09-15T03:00:00+00:00")
    return build._normalize_archive_record(record)


class ArchiveGateReadsSealedEvidenceTests(unittest.TestCase):
    def assertVisible(self, record: dict) -> None:
        visible, stats = build.apply_archive_integrity_gate([record])
        self.assertEqual(stats["quarantined"], 0, stats["quarantine_samples"])
        self.assertEqual([row["hash"] for row in visible], [record["hash"]])

    def assertQuarantined(self, record: dict) -> None:
        visible, stats = build.apply_archive_integrity_gate([record])
        self.assertEqual(visible, [])
        self.assertEqual(stats["quarantined"], 1)

    def test_premise_body_evidence_is_what_supports_the_korean_title(self):
        """본문 근거가 없으면 격리되는 사례여야 아래 검사가 의미가 있다."""
        record = archived(body="")
        self.assertQuarantined(record)

    def test_kst_publication_time_keeps_the_manifest_valid(self):
        record = archived()
        self.assertTrue(record["pub"].endswith("+09:00"), record["pub"])
        self.assertVisible(record)

    def test_missing_publication_time_does_not_borrow_archived_at(self):
        """pub 이 비면 manifest 는 빈 값에 묶여 있다. archived_at 을 끌어오면 어긋난다."""
        record = archived({"pub": None})
        self.assertEqual(record["pub"], "")
        self.assertVisible(record)

    def test_manifest_from_another_article_is_still_rejected(self):
        other = archived({"hash": "other-article",
                          "title": "두산퓨얼셀, 국내 연료전지 공급 계약"})
        record = {**archived(),
                  "verified_evidence": other["verified_evidence"],
                  "verified_source_components": other["verified_source_components"]}
        self.assertQuarantined(record)

    def test_archived_component_drift_is_still_rejected(self):
        """아카이브가 보존한 결속 지문이 봉인과 다르면 manifest 를 믿지 않는다."""
        record = archived()
        drifted = dict(record["verified_source_components"])
        drifted["published_at"] = "0" * 64
        self.assertQuarantined({**record, "verified_source_components": drifted})


if __name__ == "__main__":
    unittest.main()
