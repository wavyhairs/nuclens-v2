"""원문 검사기 '맞음' → 원문 대조 격리 해제 테스트. LLM·네트워크 0.

잠그는 것
---------
- '맞음'을 받은 바로 그 제목·요약만 풀린다. 글이 바뀌면 표식은 무효(다시 격리).
- '다름·없음', 제목만 보고 받은 '맞음', 규칙 검사에 걸린 글은 풀리지 않는다.
- 같은 글은 다시 묻지 않고 기록의 판정을 쓴다(상한 절약).
- 수집 게이트가 요약을 비우기 전 글을 검사기에 넘긴다.
- 표식은 브리핑(screen_auto_delivery · verify_final_cards)과 아카이브까지 따라간다.
- INTEGRITY_RELEASE=off 면 풀지 않는다(판정 기록은 그대로).
- 하루 점검에 '내보낸 기사' 줄이 따로 서고, 같은 기사를 '뺀 기사'로 다시 세지 않는다.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import article_quality_gate as gate  # noqa: E402
import daily_brief  # noqa: E402
import integrity_shadow  # noqa: E402
import news_archive  # noqa: E402
import news_bot as nb  # noqa: E402
import operational_digest  # noqa: E402

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)

# 게이트가 '3호기'를 원문(4호기)과 다르다고 보는 출력. 진짜로 틀린 글이지만, 여기서는
# 검사기 판정을 대역으로 정해 해제 경로만 본다.
ARTICLE = {"hash": "m4", "title": "Power start-up for Mochovce 4",
           "description": "Slovakia's Mochovce 4 began power generation at 15:33 on Monday.",
           "domain": "world-nuclear-news.org", "publisher": "WNN", "link": "https://wnn/m4",
           "pub": datetime(2026, 10, 24, 9, tzinfo=timezone.utc)}
CURATION = {"title_kr": "슬로바키아 모호체 원전 3호기 전력 생산 개시",
            "summary": "슬로바키아 모호체 원전 3호기가 전력 생산을 시작했다.",
            "detail": "", "importance": "nice_to_know"}


class FakeClient:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []

    def is_available(self):
        return True

    def call_json(self, system, user, **kwargs):
        self.calls.append(user)
        return {"items": [self.answers.pop(0) if self.answers else {"verdict": "ok"}]}


def _quarantine(curation=None):
    curation = dict(curation or CURATION)
    result = nb.audit_curation_integrity(ARTICLE, curation)
    assert not result.eligible, "고정 출력은 게이트에 걸려야 한다"
    return {"m4": {"curation": curation, "codes": [f.code for f in result.findings],
                   "concerns": gate.integrity_concerns(result.findings), "stage": "collect"}}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "integrity_shadow.jsonl"

    def release(self, client, quarantines=None, articles=None):
        return integrity_shadow.release(
            quarantines or _quarantine(), articles or {"m4": ARTICLE}, {},
            client=client, now=NOW, path=self.log)

    def test_ok_verdict_releases_exactly_the_checked_text(self):
        out = self.release(FakeClient([{"verdict": "ok"}]))
        cur = out["m4"]
        marker = cur[gate.INTEGRITY_RELEASE_KEY]
        self.assertEqual(marker["source_kind"], "description")
        self.assertIn("title_source_mismatch", marker["codes"])
        passed = nb.audit_curation_integrity(ARTICLE, cur)
        self.assertTrue(passed.eligible)
        self.assertEqual(passed.value["summary"], CURATION["summary"], "비운 요약을 되돌린다")
        self.assertEqual({f.severity for f in passed.findings
                          if f.code.endswith("_source_mismatch")}, {"released"})
        # 글이 바뀌면 표식은 그 글을 보증하지 않는다.
        edited = {**cur, "title_kr": "슬로바키아 모호체 원전 3호기 상업운전"}
        self.assertFalse(nb.audit_curation_integrity(ARTICLE, edited).eligible)

    def test_negative_verdicts_do_not_release(self):
        for answer in ({"verdict": "contradiction", "source_quote": "Mochovce 4",
                        "claim": "3호기", "reason": "호기가 다르다"},
                       {"verdict": "unsupported"}, {"verdict": "stale"}):
            with self.subTest(answer=answer["verdict"]):
                self.log.unlink(missing_ok=True)
                self.assertEqual(self.release(FakeClient([answer])), {})

    def test_title_only_source_is_not_asked_for_release(self):
        bare = {**ARTICLE, "description": ""}
        client = FakeClient([{"verdict": "ok"}])
        out = integrity_shadow.release(_quarantine(), {"m4": bare}, {},
                                       client=client, now=NOW, path=self.log)
        self.assertEqual((out, client.calls), ({}, []))

    def test_same_text_is_not_asked_again_but_still_released(self):
        self.release(FakeClient([{"verdict": "ok"}]))
        again = FakeClient([{"verdict": "contradiction"}])
        out = self.release(again)
        self.assertEqual(again.calls, [])
        self.assertIn("m4", out)

    def test_marker_must_match_version_and_source_kind(self):
        cur = self.release(FakeClient([{"verdict": "ok"}]))["m4"]
        for field, value in (("v", 0), ("source_kind", "title"), ("verdict", "unsupported")):
            with self.subTest(field=field):
                broken = {**cur, gate.INTEGRITY_RELEASE_KEY: {
                    **cur[gate.INTEGRITY_RELEASE_KEY], field: value}}
                self.assertFalse(nb.audit_curation_integrity(ARTICLE, broken).eligible)

    def test_switch_off_keeps_quarantine(self):
        with patch.dict(os.environ, {"INTEGRITY_RELEASE": "off"}):
            self.assertFalse(integrity_shadow.release_enabled())
            self.assertEqual(nb.release_integrity_quarantines(_quarantine(), {"m4": ARTICLE},
                                                              {}, []), {})
        with patch.dict(os.environ, {"INTEGRITY_RELEASE": ""}):
            self.assertTrue(integrity_shadow.release_enabled())


class DownstreamTests(unittest.TestCase):
    """표식이 브리핑·아카이브까지 따라가야 뒤 게이트에서 다시 격리되지 않는다."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cur = integrity_shadow.release(
            _quarantine(), {"m4": ARTICLE}, {}, client=FakeClient([{"verdict": "ok"}]),
            now=NOW, path=Path(tmp.name) / "log.jsonl")["m4"]

    def queue_item(self, **override):
        return {"hash": "m4", "title": ARTICLE["title"], "link": ARTICLE["link"],
                "domain": ARTICLE["domain"], "curation_status": "reviewed",
                "source_excerpt": ARTICLE["description"],
                "published_at": "2026-10-24T09:00:00+00:00", "section": "international",
                "title_kr": self.cur["title_kr"], "summary": self.cur["summary"],
                "integrity_release": self.cur["integrity_release"], **override}

    def test_brief_gates_honor_the_marker(self):
        result = gate.audit_article_integrity(
            self.queue_item(), source=daily_brief._quality_source(self.queue_item()))
        self.assertTrue(result.eligible)
        card = {"headline": self.cur["title_kr"], "what": self.cur["summary"]}
        final = gate.validate_final_card(card, self.queue_item(),
                                         source=daily_brief._quality_source(self.queue_item()))
        self.assertNotEqual(final.action, "quarantine")
        self.assertEqual(final.value["what"], self.cur["summary"])

    def test_card_with_other_headline_is_still_quarantined(self):
        card = {"headline": "슬로바키아 모호체 원전 3호기 100% 출력 도달",
                "what": self.cur["summary"]}
        final = gate.validate_final_card(card, self.queue_item(),
                                         source=daily_brief._quality_source(self.queue_item()))
        self.assertEqual(final.action, "quarantine")

    def test_without_marker_brief_quarantines_as_before(self):
        item = self.queue_item()
        item.pop("integrity_release")
        result = gate.audit_article_integrity(item, source=daily_brief._quality_source(item))
        self.assertFalse(result.eligible)

    def test_archive_record_keeps_the_marker(self):
        record = news_archive.make_record(ARTICLE, self.cur, NOW.isoformat())
        self.assertEqual(record["integrity_release"], self.cur["integrity_release"])
        self.assertNotIn("integrity_release", news_archive.make_record(ARTICLE, CURATION,
                                                                       NOW.isoformat()))


class CurateBatchHandsTheUnblankedText(unittest.TestCase):
    """요약 불일치면 게이트가 요약을 비운다. 비운 글로 받은 '맞음'은 요약을 보증하지 않는다."""

    def test_failure_keeps_the_summary_the_gate_blanked(self):
        response = {"items": [{
            "idx": 0, "id": "m4", "importance": "nice_to_know", "section": "international",
            "scope": "overseas", "category": "운영", "title_kr": "모호체 원전 4호기 전력 생산 개시",
            "summary": CURATION["summary"], "implication": "", "why_important": "",
            "tags": [], "topics": ["reactor_operation"], "countries": ["SK"],
            "article_type": "news", "event_date": None, "event_date_type": "unknown",
            "event_date_precision": "unknown", "event_date_source": "unknown",
            "related_reports": [], "features": {},
        }]}
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        nb.INTEGRITY_QUARANTINE_OUTPUTS.clear()
        with patch.object(nb, "gemini_rest_available", return_value=True), patch.object(
                nb, "gemini_call_json", side_effect=[response, response]):
            nb.curate_batch([dict(ARTICLE)], [], log_path=Path(tmp.name) / "d.jsonl")
        kept = nb.INTEGRITY_QUARANTINE_OUTPUTS.pop("m4")
        nb.INTEGRITY_QUARANTINE_OUTPUTS.clear()
        self.assertIn("summary_source_mismatch", kept["codes"])
        self.assertEqual(kept["curation"]["summary"], CURATION["summary"])
        self.assertEqual(kept["other_errors"], [], "요약을 비워 생긴 결손은 다른 결손이 아니다")


class DigestTests(unittest.TestCase):
    def test_released_articles_get_their_own_line_and_are_not_counted_as_dropped(self):
        by_key = {
            "article-integrity-quarantine": [{"items": [
                {"hash": "a", "title": "Power start-up for Mochovce 4"},
                {"hash": "b", "title": "JAIF estimates economic impact"}]}],
            "article-integrity-released": [{"items": [
                {"hash": "a", "title": "Power start-up for Mochovce 4",
                 "title_kr": "모호체 원전 4호기 전력 생산 개시",
                 "concerns": ["제목: 대상 원자력안전위원회"]}]}],
        }
        lines = operational_digest._article_lines(by_key)
        dropped = next(line for line in lines if "이번 수집에서 뺀 기사" in line)
        released = next(line for line in lines if "내보낸 기사" in line)
        self.assertIn("1건", dropped)
        self.assertNotIn("Mochovce", dropped)
        self.assertIn("모호체 원전 4호기 전력 생산 개시", released)
        self.assertIn("대상 원자력안전위원회", released)


class WiringTests(unittest.TestCase):
    source = (ROOT / "news_bot.py").read_text(encoding="utf-8")

    def test_release_runs_before_the_collect_loop_and_queue_carries_the_marker(self):
        taken = self.source.index("curation_quarantines = dict(INTEGRITY_QUARANTINE_OUTPUTS)")
        released = self.source.index("release_integrity_quarantines(", taken)
        loop = self.source.index("for article in final_articles:", taken)
        self.assertLess(released, loop)
        self.assertIn('"integrity_release": cur["integrity_release"]', self.source)

    def test_switch_is_passed_to_collect_steps(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            yml = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("INTEGRITY_RELEASE: ${{ vars.INTEGRITY_RELEASE }}", yml)


if __name__ == "__main__":
    unittest.main()
