"""해외 AI 탭 데이터 — 회차·같은 사건 묶기·회차 안 정렬·상세가 읽는 필드."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ai_lane_web as web  # noqa: E402

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


def row(digest, collected, *, title, actors=("Atomic Canyon",), area="operations_maintenance",
        maturity="deployed", sig=2, actor_type="ai_startup", role="independent", tier=1,
        pub=None, detail="", publisher="WNN"):
    return {"hash": digest, "collected_at": collected, "archived_at": collected,
            "pub": pub or collected, "title": title, "title_kr": title, "summary": f"{title} 요약",
            "detail": detail, "why_important": "왜", "implication": "시사", "actor_names": list(actors),
            "actor_type": actor_type, "application_area": area, "maturity": maturity,
            "adoption_significance": sig, "ai_tech": "genai_llm", "countries": ["US"],
            "evidence_role": role, "source_type": "trade_media", "source_tier": tier,
            "publisher": publisher, "domain": "x.test", "url": f"https://x.test/{digest}",
            "judge": "gemini"}


class RoundTests(unittest.TestCase):
    def test_round_is_kst_collection_day(self):
        # 2026-10-09 18:20 UTC = 10-10 03:20 KST
        self.assertEqual(web.round_date({"collected_at": "2026-10-09T18:20:00+00:00"}), "2026-10-10")

    def test_falls_back_to_archived_at(self):
        self.assertEqual(web.round_date({"archived_at": "2026-10-01T01:00:00+00:00"}), "2026-10-01")


class GroupingTests(unittest.TestCase):
    def test_same_actor_and_area_within_window_is_one_event(self):
        # 실제 NIVA 보도 간격(8/25 → 9/23 → 10/9). 이어지는 보도가 30일 창 안에 있으면 사슬로 묶인다.
        rows = [row("a", "2026-08-25T03:00:00+00:00", title="NIVA 공개"),
                row("b", "2026-09-23T03:00:00+00:00", title="NIVA 함대 적용"),
                row("c", "2026-10-09T03:00:00+00:00", title="미국 원전 AI 확산")]
        groups = web.group_rows(rows)
        self.assertEqual([len(g) for g in groups], [3])

    def test_different_area_splits(self):
        rows = [row("a", "2026-08-18T03:00:00+00:00", title="NIVA 공개"),
                row("b", "2026-08-19T03:00:00+00:00", title="인허가 문서 AI", area="licensing_regulation")]
        self.assertEqual(len(web.group_rows(rows)), 2)

    def test_gap_over_window_splits(self):
        rows = [row("a", "2026-07-01T03:00:00+00:00", title="첫 발표"),
                row("b", "2026-09-30T03:00:00+00:00", title="석 달 뒤 다른 발표")]
        self.assertEqual(len(web.group_rows(rows)), 2)

    def test_same_title_joins_even_without_actor(self):
        rows = [row("a", "2026-09-01T03:00:00+00:00", title="같은 제목", actors=()),
                row("b", "2026-09-02T03:00:00+00:00", title="같은 제목", actors=(), area="fuel_cycle")]
        self.assertEqual(len(web.group_rows(rows)), 1)


class IssueTests(unittest.TestCase):
    def build(self, rows):
        return web.build_issue(rows, today="2026-10-14")

    def test_round_representatives_are_cards_and_rest_evidence(self):
        rows = [row("a", "2026-09-23T03:00:00+00:00", title="벤더 PR", role="distributed_claim", tier=3),
                row("b", "2026-09-23T05:00:00+00:00", title="전문지 보도", role="independent", tier=1),
                row("c", "2026-10-09T03:00:00+00:00", title="후속 보도")]
        issue = self.build(rows)
        roles = {a["hash"]: a["member_role"] for a in issue["related_articles"]}
        self.assertEqual(roles, {"a": "evidence", "b": "card", "c": "card"})
        self.assertEqual(issue["rounds"], ["2026-10-09", "2026-09-23"])
        self.assertEqual(issue["first_seen"], "2026-09-23")
        self.assertEqual(issue["last_seen"], "2026-10-09")
        self.assertEqual(issue["tracked_briefings"], 2)
        self.assertEqual(issue["status"], "ongoing")
        self.assertTrue(issue["issue_id"].startswith("ai-"))

    def test_fields_the_dialog_reads(self):
        issue = self.build([row("a", "2026-10-09T03:00:00+00:00", title="t", detail="본문")])
        for key in ("issue_id", "title", "headline_display", "summary", "detail", "why_important",
                    "implication", "topics", "tags", "regions", "first_seen", "last_seen",
                    "article_count", "related_articles", "verification", "round_scores",
                    "application_area", "maturity", "actor_names"):
            with self.subTest(key=key):
                self.assertIn(key, issue)
        self.assertEqual(issue["topics"], ["operations_maintenance"])
        self.assertEqual(issue["regions"], ["해외"])
        article = issue["related_articles"][0]
        for key in ("url", "title_kr", "publisher", "article_date", "briefing_date", "member_role",
                    "evidence_role", "source_tier"):
            with self.subTest(article_key=key):
                self.assertIn(key, article)

    def test_vendor_announcement_is_flagged(self):
        issue = self.build([row("a", "2026-10-09T03:00:00+00:00", title="출시",
                                actor_type="ai_startup", maturity="announced")])
        self.assertTrue(issue["vendor_claim"])
        issue = self.build([row("a", "2026-10-09T03:00:00+00:00", title="운영사 적용",
                                actor_type="utility", maturity="deployed")])
        self.assertFalse(issue["vendor_claim"])

    def test_verification_is_injected(self):
        seen = []

        def verify(articles):
            seen.append(len(articles))
            return {"status": "official", "official_source_count": 1, "independent_source_count": 2}
        issue = web.build_issue([row("a", "2026-10-09T03:00:00+00:00", title="t")],
                                today="2026-10-14", verify=verify)
        self.assertEqual(seen, [1])
        self.assertEqual(issue["verification"]["status"], "official")
        self.assertIn("근거 구성 +2", issue["round_scores"]["2026-10-09"]["breakdown"])

    def test_quiet_after_a_month(self):
        issue = web.build_issue([row("a", "2026-08-01T03:00:00+00:00", title="t")], today="2026-10-14")
        self.assertEqual(issue["lifecycle"], "quiet")


class ScoreTests(unittest.TestCase):
    def test_formula_and_breakdown(self):
        total, text = web.score({"adoption_significance": 3, "maturity": "deployed", "actor_type": "ai_startup"})
        self.assertEqual(total, 3 * 3 + 3 - 1)
        self.assertIn("도입 중대성 3×3", text)
        self.assertIn("실적용 +3", text)
        self.assertIn("AI 스타트업 -1", text)

    def test_discussion_ranks_below_deployment(self):
        low, _ = web.score({"adoption_significance": 0, "maturity": "discussion", "actor_type": "other"})
        high, _ = web.score({"adoption_significance": 2, "maturity": "deployed", "actor_type": "utility"})
        self.assertLess(low, high)


class PayloadTests(unittest.TestCase):
    def test_rounds_newest_first_and_ordered_by_score(self):
        rows = [row("a", "2026-10-09T03:00:00+00:00", title="해설", maturity="discussion", sig=0,
                    actors=("ANS",), area="workforce_governance", actor_type="other"),
                row("b", "2026-10-09T04:00:00+00:00", title="함대 적용", sig=3, actor_type="utility",
                    actors=("Constellation",)),
                row("c", "2026-10-01T04:00:00+00:00", title="옛 회차", actors=("WANO",),
                    area="safeguards_security")]
        payload = web.build_payload(rows, now=NOW)
        self.assertEqual([r["date"] for r in payload["rounds"]], ["2026-10-09", "2026-10-01"])
        first = payload["rounds"][0]["issue_ids"]
        by_id = {i["issue_id"]: i for i in payload["issues"]}
        self.assertEqual([by_id[i]["title"] for i in first], ["함대 적용", "해설"])
        self.assertEqual(payload["latest_round"], "2026-10-09")
        self.assertEqual(payload["today"], "2026-10-14")
        self.assertEqual(payload["stats"]["articles"], 3)
        self.assertEqual(payload["labels"]["deployed"], "실적용")

    def test_empty_archive_gives_empty_rounds(self):
        payload = web.build_payload([], now=NOW)
        self.assertEqual(payload["rounds"], [])
        self.assertEqual(payload["issues"], [])
        self.assertEqual(payload["latest_round"], "")


class WiringTests(unittest.TestCase):
    def test_build_data_writes_ai_payload(self):
        src = (ROOT / "web" / "build_data.py").read_text(encoding="utf-8")
        self.assertIn('("ai_issues.json", build_ai_payload(now))', src)
        self.assertIn("verification_state(articles", src)

    def test_front_end_loads_and_gates_ai_tab(self):
        app = (ROOT / "web" / "public" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn('loadJSON("ai_issues.json").catch(() => null)', app)
        self.assertIn('if (view === "ai" && !aiReady()) view = "news";', app)
        # 상단·하단 탭 둘 다, 기본은 숨김 — 데이터가 오면 syncAiChrome 이 연다.
        self.assertEqual(html.count('data-view="ai" aria-pressed="false" hidden'), 2)
        self.assertIn('id="view-ai"', html)
        self.assertIn('data-scope="ai"', html)

    def test_ai_issue_urls_avoid_static_issue_route(self):
        app = (ROOT / "web" / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("!isAiIssueId(state.issueId)", app)
        self.assertIn('params.set("issue", state.issueId)', app)


if __name__ == "__main__":
    unittest.main()


class CommittedArchiveTests(unittest.TestCase):
    """커밋된 ai_archive/ 레코드가 화면이 기대하는 통제 값만 쓰는지(수동 백필 포함)."""

    def test_rows_use_controlled_values(self):
        import ai_lane  # noqa: PLC0415
        rows = web.load_rows()
        if not rows:
            self.skipTest("ai_archive 없음")
        for r in rows:
            with self.subTest(hash=r.get("hash")):
                self.assertEqual(r.get("lane"), "ai")
                self.assertIn(r.get("application_area"), ai_lane.APPLICATION_AREAS)
                self.assertIn(r.get("maturity"), ai_lane.MATURITY)
                self.assertIn(r.get("actor_type"), ai_lane.ACTOR_TYPES)
                self.assertTrue(str(r.get("title_kr") or "").strip())
                self.assertTrue(r.get("collected_at"))
                self.assertIn(r.get("judge"), ("gemini", "manual-claude"))


class WeeklyDigestTests(unittest.TestCase):
    ROWS = [row("a", "2026-10-06T03:00:00+00:00", title="도쿄전력 서류 AI", actors=("TEPCO",),
                area="licensing_regulation", maturity="announced", sig=2, actor_type="utility"),
            row("b", "2026-10-09T03:00:00+00:00", title="미국 원전 AI 확산", sig=3, actor_type="utility"),
            row("c", "2026-10-08T03:00:00+00:00", title="AI 진단 웨비나", actors=("ANS",),
                area="inspection_diagnostics", maturity="discussion", sig=0, actor_type="other"),
            row("d", "2026-09-01T03:00:00+00:00", title="지난 달", actors=("X",))]

    def test_picks_this_week_by_score(self):
        out = web.weekly_digest("2026-10-05", "2026-10-11", self.ROWS, limit=2)
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["articles"], 3)
        self.assertEqual([i["title"] for i in out["items"]], ["미국 원전 AI 확산", "도쿄전력 서류 AI"])
        self.assertEqual(out["items"][0]["country_kr"], "미국")
        self.assertEqual(out["items"][1]["area_kr"], "인허가·규제")

    def test_empty_week_is_empty(self):
        self.assertEqual(web.weekly_digest("2026-11-02", "2026-11-08", self.ROWS), {})


class WeeklyMessageTests(unittest.TestCase):
    EMPTY = {"weekly_intro": "", "policy_shifts": [], "theme_moves": [], "khnp_direct": "",
             "watchpoints": [], "report_candidates": [], "key_events": []}

    def test_section_renders_only_when_present(self):
        import weekly_bot  # noqa: PLC0415
        now = datetime(2026, 10, 9, 17, 0, tzinfo=weekly_bot.KST)
        without = weekly_bot.format_weekly([], dict(self.EMPTY), {}, now=now)
        self.assertNotIn("해외 원전 AI", without)
        section = {"count": 2, "articles": 3, "items": [
            {"title": "미국 원전 AI 확산", "country_kr": "미국", "area_kr": "운영·정비",
             "maturity_kr": "실적용", "vendor_claim": False},
            {"title": "AI 제품 출시 <b>", "country_kr": "", "area_kr": "운영·정비",
             "maturity_kr": "발표", "vendor_claim": True}]}
        text = weekly_bot.format_weekly([], dict(self.EMPTY), {"overseas_ai": section}, now=now)
        self.assertIn("🤖 해외 원전 AI·디지털</b> (이번 주 사건 2건)", text)
        self.assertIn("미국 · 운영·정비 · 실적용", text)
        self.assertIn("운영·정비 · 발표 · 벤더 발표", text)
        self.assertIn("AI 제품 출시 &lt;b&gt;", text)  # 제목은 이스케이프한다

    def test_attached_only_in_plan_not_in_deterministic_sections(self):
        src = (ROOT / "weekly_bot.py").read_text(encoding="utf-8")
        plan = src.split("def cmd_plan(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('sections["overseas_ai"] = overseas_ai', plan)
        sections_fn = src.split("def build_week_sections(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("ai_lane", sections_fn)


class TrendSectionTests(unittest.TestCase):
    def test_flow_tab_has_ai_section_wired(self):
        app = (ROOT / "web" / "public" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="trendAi"', html)
        self.assertIn('data-go-view="ai"', html)
        self.assertIn("renderTrendTopicFlow();\n  renderTrendAi();", app)
class BuildGuardTests(unittest.TestCase):
    def test_load_rows_skips_main_scope_kr(self):
        import json as _json  # noqa: PLC0415
        import tempfile  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rows = [dict(row("a", "2026-10-09T03:00:00+00:00", title="t"), main_scope="kr"),
                    dict(row("b", "2026-10-09T03:00:00+00:00", title="u"), main_scope="overseas")]
            (d / "2026-10.jsonl").write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            got = web.load_rows(d)
        self.assertEqual([r["hash"] for r in got], ["b"])
