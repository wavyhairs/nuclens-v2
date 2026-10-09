"""원문 대조 격리의 뒤처리 — 그림자 재검(integrity_shadow)과 보류(integrity_hold).

배경 (2026-10-10): 9/26~10/10 수집 캡처를 #242 게이트로 재생하니 끝내 격리된 107건
중 100건이 게이트가 원문 표기를 잘못 읽은 것이었다. 격리된 기사는 수집 창 동안
3시간마다 다시 요약되다가 기록 없이 사라졌다. 판정은 원문을 읽는 검사기에 그림자로
묻고, 재시도에는 끝을 둔다.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import article_quality_gate as gate  # noqa: E402
import integrity_hold  # noqa: E402
import integrity_shadow  # noqa: E402
import news_bot as nb  # noqa: E402
import operational_digest  # noqa: E402
import summary_verify  # noqa: E402

NOW = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []

    def is_available(self):
        return True

    def call_json(self, system, user, **kwargs):
        self.calls.append(user)
        return {"items": [self.answers.pop(0) if self.answers else {"verdict": "ok"}]}


class HoldTests(unittest.TestCase):
    def quarantined(self, h="h1"):
        return {h: {"title": "Slovakia buys out Czech stake", "link": "https://wnn/x",
                    "reason": "summary_source_mismatch", "concerns": ["요약: 수치 89,000,000유로"]}}

    def test_held_after_the_threshold_of_consecutive_runs(self):
        state = {"sent": {}}
        for run in range(integrity_hold.HOLD_AFTER_RUNS - 1):
            self.assertEqual(integrity_hold.record_run(
                state, self.quarantined(), set(), now=NOW + timedelta(hours=3 * run)), [])
        self.assertEqual(integrity_hold.held_hashes(state), set())
        newly = integrity_hold.record_run(state, self.quarantined(), set(), now=NOW + timedelta(days=1))
        self.assertEqual([row["hash"] for row in newly], ["h1"])
        self.assertEqual(newly[0]["concerns"], ["요약: 수치 89,000,000유로"])
        self.assertEqual(integrity_hold.held_hashes(state), {"h1"})
        # 이미 보류된 기사를 다시 '새로 보류'로 알리지 않는다.
        self.assertEqual(integrity_hold.record_run(state, self.quarantined(), set(), now=NOW), [])

    def test_a_pass_breaks_the_streak(self):
        state = {"sent": {}}
        for _ in range(integrity_hold.HOLD_AFTER_RUNS - 1):
            integrity_hold.record_run(state, self.quarantined(), set(), now=NOW)
        integrity_hold.record_run(state, {}, {"h1"}, now=NOW)
        self.assertNotIn("h1", state[integrity_hold.STATE_KEY])
        self.assertEqual(integrity_hold.record_run(state, self.quarantined(), set(), now=NOW), [])

    def test_a_run_without_the_article_neither_counts_nor_resets(self):
        state = {"sent": {}}
        integrity_hold.record_run(state, self.quarantined(), set(), now=NOW)
        integrity_hold.record_run(state, {}, {"other"}, now=NOW)
        self.assertEqual(state[integrity_hold.STATE_KEY]["h1"]["runs"], 1)

    def test_old_entries_are_pruned_but_not_inside_the_official_window(self):
        state = {"sent": {}}
        integrity_hold.record_run(state, self.quarantined("old"), set(), now=NOW - timedelta(days=15))
        integrity_hold.record_run(state, self.quarantined("week"), set(), now=NOW - timedelta(days=7))
        self.assertEqual(integrity_hold.prune(state, now=NOW), 1)
        self.assertEqual(set(state[integrity_hold.STATE_KEY]), {"week"})

    def test_threshold_is_where_recovery_ran_out(self):
        """실측: 6회 연속 격리 뒤에 통과한 기사는 0/12(2회 뒤엔 7/25)."""
        self.assertEqual(integrity_hold.HOLD_AFTER_RUNS, 6)
        self.assertGreater(integrity_hold.RETENTION_DAYS, nb.OFFICIAL_LOOKBACK_DAYS)


class ShadowTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "integrity_shadow.jsonl"
        self.articles = {
            "b1": {"hash": "b1", "title": "Slovakia buys out Czech stake",
                   "description": "ČEZ sold its stake for EUR189 million.",
                   "pub": datetime(2026, 10, 8, 9, tzinfo=timezone.utc)},
            "d1": {"hash": "d1", "title": "원전 1기가 36조", "description": "JAIF 분석 요약"},
            "t1": {"hash": "t1", "title": "ZNPP faces mounting safety risks"},
        }
        self.quarantines = {
            h: {"curation": {"title_kr": f"제목 {h}", "summary": f"요약 {h}.", "detail": ""},
                "codes": ["summary_source_mismatch"], "concerns": [f"요약: 대상 {h}"],
                "stage": "curation-regen"}
            for h in self.articles}

    def test_targets_fall_back_from_body_to_description_to_title(self):
        items = {item["hash"]: item for item in integrity_shadow.targets(
            self.quarantines, self.articles, {"b1": "Full body text."})}
        self.assertEqual(items["b1"]["body"], "Full body text.")
        self.assertEqual(items["b1"]["log_extra"]["source_kind"], "body")
        self.assertEqual(items["b1"]["published"], "2026-10-08")
        self.assertEqual(items["d1"]["body"], "JAIF 분석 요약")
        self.assertEqual(items["d1"]["log_extra"]["source_kind"], "description")
        self.assertEqual(items["t1"]["log_extra"]["source_kind"], "title")
        self.assertEqual(items["t1"]["log_extra"]["concerns"], ["요약: 대상 t1"])

    def test_run_logs_the_gate_reason_next_to_the_verdict_and_does_not_reask(self):
        items = integrity_shadow.targets(self.quarantines, self.articles, {"b1": "Full body text."})
        client = FakeClient([
            {"id": "b1", "verdict": "ok"},
            {"id": "d1", "verdict": "contradiction", "claim": "36조", "source_quote": "JAIF 분석 요약",
             "reason": "다른 값"},
            {"id": "t1", "verdict": "unsupported"},
        ])
        rows, stats = integrity_shadow.run(items, client=client, now=NOW, path=self.log)
        self.assertEqual(stats["checked"], 3)
        logged = summary_verify.load_log(self.log)
        self.assertEqual({row["stage"] for row in logged}, {"integrity_shadow"})
        by_hash = {row["hash"]: row for row in logged}
        self.assertEqual(by_hash["b1"]["concerns"], ["요약: 대상 b1"])
        self.assertEqual(by_hash["b1"]["gate_codes"], ["summary_source_mismatch"])
        self.assertEqual({h: integrity_shadow.decision(row) for h, row in by_hash.items()},
                         {"b1": "would_release", "d1": "would_repair", "t1": "would_strip"})
        self.assertEqual(integrity_shadow.latest_decisions(["b1", "d1", "zz"], path=self.log),
                         {"b1": "would_release", "d1": "would_repair"})
        # 같은 출력은 다시 묻지 않는다 — 기사가 회차마다 다시 격리돼도.
        again = FakeClient()
        _rows, stats = integrity_shadow.run(items, client=again, now=NOW, path=self.log)
        self.assertEqual((len(again.calls), stats["cached"]), (0, 3))

    def test_run_respects_its_own_caps(self):
        items = integrity_shadow.targets(self.quarantines, self.articles, {})
        client = FakeClient()
        with patch.dict("os.environ", {"INTEGRITY_SHADOW_RUN_CAP": "1"}):
            _rows, stats = integrity_shadow.run(items, client=client, now=NOW, path=self.log)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(stats["skipped_cap"], 2)

    def test_uncalled_rows_have_no_verdict(self):
        self.assertEqual(integrity_shadow.decision({"called": False, "verdict": "failed"}),
                         "no_verdict")
        self.assertEqual(integrity_shadow.decision({"called": True, "verdict": "stale"}),
                         "uncertain")


class ConcernTests(unittest.TestCase):
    def test_the_value_read_from_the_source_is_shown(self):
        """`1천570억` 을 570억으로 읽은 게이트 — 운영자가 바로 알아볼 수 있어야 한다."""
        result = gate.audit_article_integrity(
            {"title": "x", "title_kr": "한수원, 1,570억 원 계약", "summary": ""},
            source={"title": "한수원 1천570억 계약", "description": "한수원이 1천570억 원 계약을 맺었다.",
                    "article_text": "한수원이 1천570억 원 규모 계약을 체결했다."},
            reference_date="2026-10-09")
        self.assertFalse(result.eligible)
        self.assertEqual(gate.integrity_concerns(result.findings),
                         ["제목: 수치 1,570억원(원문에서 읽은 값 570억원)"])

    def test_introduced_entity_is_named(self):
        finding = gate.Finding("summary_source_mismatch", "quarantine", "summary",
                               details={"introduced_entities": ["kepco"]})
        self.assertEqual(gate.integrity_concerns([finding]), ["요약: 대상 한국전력"])

    def test_sanitize_findings_are_not_concerns(self):
        finding = gate.Finding("event_date_source_unavailable", "sanitize", "event_date")
        self.assertEqual(gate.integrity_concerns([finding]), [])


class CurateBatchKeepsTheQuarantinedOutput(unittest.TestCase):
    """재생성에도 실패한 출력을 버리면 재검도, 걸린 구절 안내도 할 수 없다."""

    ARTICLE = {"hash": "m4", "title": "Power start-up for Mochovce 4",
               "description": "Slovakia's Mochovce 4 began power generation at 15:33 on Monday.",
               "domain": "world-nuclear-news.org", "publisher": "WNN", "link": "https://wnn/m4"}

    @staticmethod
    def response():
        return {"items": [{
            "idx": 0, "id": "m4", "importance": "nice_to_know", "section": "international",
            "scope": "overseas", "category": "운영", "title_kr": "슬로바키아 모호체 원전 3호기 전력 생산 개시",
            "summary": "슬로바키아 모호체 원전 3호기가 전력 생산을 시작했다.",
            "implication": "", "why_important": "", "tags": [], "topics": ["reactor_operation"],
            "countries": ["SK"], "article_type": "news", "event_date": None,
            "event_date_type": "unknown", "event_date_precision": "unknown",
            "event_date_source": "unknown", "related_reports": [], "features": {},
        }]}

    def test_final_quarantine_keeps_output_and_concerns(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "delivery_log.jsonl"
        nb.INTEGRITY_QUARANTINE_OUTPUTS.clear()
        with patch.object(nb, "gemini_rest_available", return_value=True), patch.object(
                nb, "gemini_call_json", side_effect=[self.response(), self.response()]):
            result = nb.curate_batch([dict(self.ARTICLE)], [], log_path=log)
        self.assertEqual(result, {})
        kept = nb.INTEGRITY_QUARANTINE_OUTPUTS["m4"]
        self.assertEqual(kept["curation"]["title_kr"], "슬로바키아 모호체 원전 3호기 전력 생산 개시")
        self.assertEqual(kept["stage"], "curation-regen")
        self.assertTrue(any("3호기" in concern for concern in kept["concerns"]), kept["concerns"])
        event = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()][-1]
        self.assertEqual(event["alert_key"], "article-integrity-quarantine")
        self.assertEqual(event["items"][0]["concerns"], kept["concerns"])
        self.assertIn("6번 연속이면 보류", event["detail"])
        nb.INTEGRITY_QUARANTINE_OUTPUTS.clear()


class DigestTests(unittest.TestCase):
    def test_held_articles_get_their_own_line_with_reason_and_shadow_verdict(self):
        by_key = {
            "article-integrity-quarantine": [{"items": [
                {"hash": "a", "title": "Slovakia buys out Czech stake"},
                {"hash": "b", "title": "JAIF estimates economic impact"}]}],
            "article-integrity-held": [{"items": [
                {"hash": "a", "title": "Slovakia buys out Czech stake",
                 "concerns": ["요약: 수치 89,000,000유로"], "shadow": "would_release"}]}],
        }
        lines = operational_digest._article_lines(by_key)
        quarantine, held = lines[0], lines[1]
        self.assertIn("1건", quarantine)
        self.assertNotIn("Slovakia", quarantine, "보류된 기사는 격리 줄에 다시 세지 않는다")
        self.assertIn("보류한 기사 1건", held)
        self.assertIn("요약: 수치 89,000,000유로", held)
        self.assertIn("원문과 맞다고 봄", held)


class WiringTests(unittest.TestCase):
    source = (ROOT / "news_bot.py").read_text(encoding="utf-8")

    def test_held_articles_are_not_curated_again(self):
        start = self.source.index("held = integrity_hold.held_hashes(state)")
        self.assertLess(start, self.source.index("if len(new_articles) > MAX_CURATION_PER_RUN"))
        self.assertLess(start, self.source.index("curation_attempted_hashes = {"))

    def test_shadow_block_is_warning_only(self):
        start = self.source.index("# ---- 원문 대조 격리의 뒤처리")
        block = self.source[start:self.source.index("# ---- 요약 사실검증", start)]
        self.assertIn("not (QUOTA_EXHAUSTED or CONFIG_ERROR)", block)
        self.assertEqual(block.count("except Exception"), 2)
        self.assertNotIn("curated[", block, "그림자 모드는 요약을 바꾸지 않는다")
        self.assertNotIn('state["sent"]', block, "보류는 sent 마킹이 아니다")

    def test_outputs_are_taken_before_the_summary_repair_reuses_curate_batch(self):
        taken = self.source.index("curation_quarantines = dict(INTEGRITY_QUARANTINE_OUTPUTS)")
        self.assertLess(taken, self.source.index("repair = repair_contradicted_summaries("))

    def test_log_is_committed_and_union_merged(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            yml = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("integrity_shadow.jsonl", yml, f"{name} 에 커밋이 빠졌다")
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("integrity_shadow.jsonl merge=union", attributes)


if __name__ == "__main__":
    unittest.main()
