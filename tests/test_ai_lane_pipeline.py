"""해외 AI 레인 수집 회차 — 메인 상태를 건드리지 않고, 실패를 '제외'로 바꾸지 않는다."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ai_lane_pipeline as lane  # noqa: E402

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)


def main_record(digest, title, *, importance="noise", title_kr="", summary="", archived_at=None):
    return {"hash": digest, "title": title, "title_kr": title_kr, "summary": summary,
            "importance": importance, "scope": "overseas", "url": f"https://x.test/{digest}",
            "domain": "neimagazine.com", "publisher": "Nuclear Engineering International",
            "archived_at": archived_at or NOW.isoformat(), "pub": "2026-10-13T10:00:00+00:00"}


def judge_all(in_scope_titles):
    """제목에 키워드가 있으면 포함, 아니면 데이터센터 제외."""
    def call(system, user):
        items = []
        for block in user.split("\n\n"):
            head = block.split("\n", 1)[0]
            idx = int(head[1:head.index("]")])
            if any(t in head for t in in_scope_titles):
                items.append({"idx": idx, "in_scope": True, "exclude_reason": "none",
                              "application_area": "operations_maintenance", "ai_tech": "genai_llm",
                              "actor_type": "ai_startup", "actor_names": ["Atomic Canyon"],
                              "maturity": "deployed", "adoption_significance": 3,
                              "countries": ["US"], "reason": "r", "title_kr": "모델 제목",
                              "summary": "모델 요약", "why_important": "왜", "implication": "시사"})
            else:
                items.append({"idx": idx, "in_scope": False, "exclude_reason": "datacenter_power"})
        return {"items": items}
    return call


class LaneRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state = base / "ai_state.json"
        self.archive = base / "ai_archive"

    def tearDown(self):
        self.tmp.cleanup()

    def run_lane(self, records, call, **kw):
        return lane.run(records, judge_call=call, now=NOW, state_file=self.state,
                        archive_dir=self.archive, **kw)

    def archived(self):
        return lane.load_archive(self.archive)

    def test_includes_ai_use_even_when_main_called_it_noise(self):
        records = [main_record("a", "NIVA takes nuclear AI fleetwide", title_kr="NIVA 함대 적용"),
                   main_record("b", "Amazon PPA with nuclear plant for AI data centers"),
                   main_record("c", "Reactor outage extended")]
        stats = self.run_lane(records, judge_all(["NIVA"]))
        self.assertEqual(stats["prefilter_out"], 1)
        self.assertEqual((stats["included"], stats["excluded"]), (1, 1))
        rows = self.archived()
        self.assertEqual([r["hash"] for r in rows], ["a"])
        row = rows[0]
        # 메인 큐레이션 제목이 우선, 비어 있던 해석은 레인이 채운다.
        self.assertEqual(row["title_kr"], "NIVA 함대 적용")
        self.assertEqual(row["implication"], "시사")
        self.assertEqual(row["main_importance"], "noise")
        self.assertEqual(row["maturity"], "deployed")
        self.assertEqual(row["archived_at"][:7], "2026-10")
        self.assertTrue((self.archive / "2026-10.jsonl").exists())

    def test_second_run_skips_judged_and_archived(self):
        records = [main_record("a", "NIVA takes nuclear AI fleetwide"),
                   main_record("b", "Amazon PPA with nuclear plant for AI data centers")]
        self.run_lane(records, judge_all(["NIVA"]))
        calls = []

        def spy(system, user):
            calls.append(user)
            return {"items": []}
        stats = self.run_lane(records, spy)
        self.assertEqual(calls, [])
        self.assertEqual(stats["seen"], 2)
        self.assertEqual(len(self.archived()), 1)

    def test_failure_is_retried_not_excluded(self):
        records = [main_record("a", "NIVA takes nuclear AI fleetwide")]

        def boom(system, user):
            raise RuntimeError("quota")
        stats = self.run_lane(records, boom)
        self.assertEqual(stats["failed"], 1)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn("a", state["judged"])
        self.assertEqual(state["attempts"]["a"], 1)
        stats = self.run_lane(records, judge_all(["NIVA"]))
        self.assertEqual(stats["included"], 1)

    def test_gives_up_after_max_attempts(self):
        records = [main_record("a", "NIVA takes nuclear AI fleetwide")]

        def boom(system, user):
            raise RuntimeError("quota")
        for _ in range(lane.MAX_ATTEMPTS):
            self.run_lane(records, boom)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["judged"]["a"]["reason"], "judge_failed")

    def test_same_title_twice_is_judged_once(self):
        records = [main_record("a", "Tianwan nuclear plant puts AI to work"),
                   main_record("b", "Tianwan nuclear plant puts AI to work")]
        stats = self.run_lane(records, judge_all(["Tianwan"]))
        self.assertEqual(stats["duplicate"], 1)
        self.assertEqual(len(self.archived()), 1)

    def test_dedicated_feed_items_join_and_get_bodies(self):
        fresh = NOW - timedelta(hours=5)
        stale = NOW - timedelta(days=10)

        def fetch(url, name):
            return [{"link": "https://news.google.com/rss/articles/one", "title": "Atomic Canyon NIVA nuclear AI rollout",
                     "description": "", "pub": fresh, "publisher": "IEEE Spectrum",
                     "publisher_domain": "spectrum.ieee.org"},
                    {"link": "https://news.google.com/rss/articles/old", "title": "Old nuclear AI story",
                     "description": "", "pub": stale, "publisher": "X", "publisher_domain": "x.test"}]
        seen_bodies = []

        def bodies(articles):
            seen_bodies.extend(a["hash"] for a in articles)
            for a in articles:
                a["resolved_url"] = "https://spectrum.ieee.org/niva"
            return {a["hash"]: "본문 " * 10 for a in articles}, {}
        captured = []

        def call(system, user):
            captured.append(user)
            return judge_all(["NIVA"])(system, user)
        stats = lane.run([], fetch=fetch, hasher=lambda u: "h-" + u[-3:], body_fetcher=bodies,
                         judge_call=call, now=NOW, state_file=self.state, archive_dir=self.archive)
        self.assertEqual(stats["included"], 1)
        self.assertEqual(len(seen_bodies), 1)  # 오래된 항목은 버렸다(피드마다 1건 × 피드 수 중 중복 제목은 한 번)
        self.assertIn("본문 발췌:", captured[0])
        row = self.archived()[0]
        self.assertEqual(row["origin"], "dedicated")
        self.assertEqual(row["url"], "https://spectrum.ieee.org/niva")
        self.assertEqual(row["title_kr"], "모델 제목")
        self.assertEqual(row["source_tier"], lane.source_profile("spectrum.ieee.org", "IEEE Spectrum")["source_tier"])

    def test_broken_feed_does_not_stop_lane(self):
        def fetch(url, name):
            raise OSError("down")
        stats = lane.run([main_record("a", "NIVA takes nuclear AI fleetwide")], fetch=fetch,
                         hasher=str, judge_call=judge_all(["NIVA"]), now=NOW,
                         state_file=self.state, archive_dir=self.archive)
        self.assertEqual(stats["included"], 1)

    def test_shared_pool_is_judged_before_feeds_under_cap(self):
        pool = [{"hash": "d", "origin": "dedicated", "title": "nuclear AI d"},
                {"hash": "s", "origin": "shared", "title": "nuclear AI s"}]
        chosen, _ = lane.select_candidates(pool, lane.load_state(self.state),
                                           {"hashes": set(), "urls": set(), "titles": set()})
        self.assertEqual([c["hash"] for c in chosen], ["s", "d"])

    def test_recent_main_records_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rows = [main_record("new", "t", archived_at=(NOW - timedelta(days=1)).isoformat()),
                    main_record("old", "t", archived_at=(NOW - timedelta(days=5)).isoformat())]
            (d / "2026-10.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            got = lane.recent_main_records(now=NOW, archive_dir=d)
        self.assertEqual([r["hash"] for r in got], ["new"])

    def test_state_prunes_old_judgments(self):
        state = {"judged": {"old": {"at": (NOW - timedelta(days=90)).isoformat()},
                            "new": {"at": NOW.isoformat()}},
                 "attempts": {"new": 1, "x": 1}, "runs": [{}] * 100}
        lane.save_state(state, self.state, now=NOW)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(set(saved["judged"]), {"new"})
        self.assertEqual(saved["attempts"], {"x": 1})
        self.assertEqual(len(saved["runs"]), lane.RUN_LOG_KEEP)

    def test_sources_are_time_bounded_google_news(self):
        for src in lane.AI_SOURCES:
            with self.subTest(src=src["name"]):
                self.assertIn("when%3A", src["url"])

    def test_module_does_not_import_news_bot(self):
        self.assertNotIn("news_bot", lane.__dict__)


class NewsBotHookTests(unittest.TestCase):
    def test_news_bot_calls_lane_after_archive_and_guards_it(self):
        src = (ROOT / "news_bot.py").read_text(encoding="utf-8")
        archive_at = src.index("news_archive.append_records(records)")
        hook_at = src.index("ai_lane_pipeline.run(")
        save_at = src.index("    save_state(state)\n    save_curated(curated)")
        self.assertLess(archive_at, hook_at)
        self.assertLess(hook_at, save_at)
        block = src[src.index("# ---- 해외 AI 레인"):save_at]
        self.assertIn("except Exception", block)
        self.assertIn("QUOTA_EXHAUSTED", block)

    def test_workflows_commit_lane_state(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("ai_archive/", text)
                self.assertIn("ai_state.json", text)

    def test_lane_archive_is_union_merged(self):
        self.assertIn("ai_archive/*.jsonl merge=union",
                      (ROOT / ".gitattributes").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
