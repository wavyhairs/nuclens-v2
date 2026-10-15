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
        # Google 뉴스 검색은 when: 창이 있어야 한다(관련도순 대비). 매체 직접 RSS(kind=rss)는 예외.
        for src in lane.AI_SOURCES:
            with self.subTest(src=src["name"]):
                if src.get("kind") == "rss":
                    self.assertNotIn("news.google.com", src["url"])
                else:
                    self.assertIn("when%3A", src["url"])
                self.assertIn(src.get("lang"), ("en", "ja", "fr", "ru", "zh"))

    def test_sources_cover_russian_chinese_and_foreign_regulators(self):
        langs = {src["lang"] for src in lane.AI_SOURCES}
        self.assertTrue({"ru", "zh"} <= langs)
        urls = " ".join(src["url"] for src in lane.AI_SOURCES)
        for name in ("FANR", "ONR", "CNSC", "iaea.org/feeds", "nrc.gov", "atomic-energy.ru"):
            self.assertIn(name, urls)

    def test_google_queries_stay_under_the_when_window_limit(self):
        # 실측(crawl #673): 278자·213자 질의는 when: 이 무시돼 전부 오래된 기사였다. 184자는 지켜졌다.
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
        for src in lane.AI_SOURCES:
            if src.get("kind") == "rss":
                continue
            query = parse_qs(urlparse(src["url"]).query).get("q", [""])[0]
            with self.subTest(src=src["name"], length=len(query)):
                self.assertLessEqual(len(query), 190)

    def test_url_date_guard(self):
        self.assertTrue(lane.stale_by_url("https://energynow.com/2026/06/canadas-atkinsrealis-seeks/", NOW))
        self.assertFalse(lane.stale_by_url("https://x.test/2026/10/12/story/", NOW))
        self.assertFalse(lane.stale_by_url("https://x.test/news/story-12345", NOW))
        self.assertIsNone(lane.url_date("https://x.test/2026/13/bad/"))

    def test_feed_items_with_old_url_dates_are_dropped_and_counted(self):
        fresh = NOW - timedelta(hours=2)

        def fetch(url, name):
            return [{"link": "https://news.google.com/rss/articles/a", "raw_link": "https://e.test/2026/06/old-story/",
                     "title": "Nuclear AI story", "pub": fresh, "publisher": "E", "publisher_domain": "e.test"},
                    {"link": "https://news.google.com/rss/articles/b", "raw_link": "https://e.test/2026/10/13/new-story/",
                     "title": "Nuclear AI story two", "pub": fresh, "publisher": "E", "publisher_domain": "e.test"}]
        items, counts = lane.collect_feeds(fetch, lambda u: u[-5:], now=NOW,
                                           sources=[{"name": "F", "lang": "en", "url": "u"}])
        self.assertEqual([i["title"] for i in items], ["Nuclear AI story two"])
        self.assertEqual(counts["F"]["stale_url"], 1)
        self.assertEqual(counts["F"]["fresh"], 1)
        self.assertEqual(items[0]["feed"], "F")

    def test_run_records_per_feed_stats_in_state(self):
        first = lane.AI_SOURCES[0]["name"]

        def fetch(url, name):
            if name != first:
                return []
            return [{"link": "https://news.google.com/rss/articles/d", "title": "NIVA nuclear AI rollout",
                     "pub": NOW - timedelta(hours=1), "publisher": "P", "publisher_domain": "p.test"}]
        stats = lane.run([main_record("s", "nuclear AI shared story")], fetch=fetch, hasher=lambda u: u[-1],
                         judge_call=judge_all(["NIVA"]), now=NOW, state_file=self.state, archive_dir=self.archive)
        feeds = stats["feeds"]
        self.assertEqual(feeds["공유 풀"]["candidates"], 1)
        dedicated = [k for k in feeds if k != "공유 풀"]
        self.assertTrue(dedicated)
        self.assertEqual(sum(feeds[k]["included"] for k in dedicated), 1)
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("feeds", saved["runs"][-1])
        self.assertIn("[AI 피드]", lane.format_stats(stats))

    def test_conflict_context_drops_digital_only_candidates_before_judge(self):
        pool = [{"hash": "w", "origin": "shared", "title": "IAEA, 자포리자 원전 부지 드론 피격 사건 확인"},
                {"hash": "r", "origin": "shared", "title": "Sellafield deploys immersive VR facility for nuclear decommissioning"}]
        chosen, stats = lane.select_candidates(pool, lane.load_state(self.state),
                                               {"hashes": set(), "urls": set(), "titles": set()})
        self.assertEqual([c["hash"] for c in chosen], ["r"])
        self.assertEqual(stats["conflict_drop"], 1)


class DatacenterGuardTests(unittest.TestCase):
    def test_included_verdict_with_datacenter_hint_is_excluded(self):
        article = {"_prefilter": {"hints": ["datacenter_power"]}}
        verdict = lane.datacenter_guard(article, {"in_scope": True, "countries": ["US"]})
        self.assertFalse(verdict["in_scope"])
        self.assertEqual(verdict["exclude_reason"], "datacenter_power")

    def test_no_hint_passes_through(self):
        verdict = {"in_scope": True, "countries": ["US"]}
        self.assertIs(lane.datacenter_guard({"_prefilter": {"hints": []}}, verdict), verdict)
        self.assertIsNone(lane.datacenter_guard({}, None))

    def test_run_applies_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stats = lane.run([main_record("a", "AtkinsRealis seeks US approval for nuclear tech to power AI boom data centers")],
                             judge_call=judge_all(["AtkinsRealis"]), now=NOW,
                             state_file=base / "s.json", archive_dir=base / "a")
            self.assertEqual(stats["included"], 0)
            self.assertEqual(stats["guarded"], 1)
            saved = json.loads((base / "s.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["judged"]["a"]["reason"], "datacenter_power")

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


class DomesticGuardTests(unittest.TestCase):
    IN = {"in_scope": True, "exclude_reason": "none", "countries": ["KR", "US"]}

    def test_main_scope_kr_is_excluded_even_if_judge_included(self):
        out = lane.domestic_guard({"scope": "kr"}, dict(self.IN))
        self.assertFalse(out["in_scope"])
        self.assertEqual(out["exclude_reason"], "domestic")

    def test_korean_outlet_on_overseas_case_stays(self):
        # 국내 매체가 쓴 도쿄전력 기사 — 메인 scope 는 overseas 다.
        out = lane.domestic_guard({"scope": "overseas"}, {"in_scope": True, "countries": ["JP"]})
        self.assertTrue(out["in_scope"])

    def test_dedicated_item_with_only_korea_is_excluded(self):
        out = lane.domestic_guard({"scope": ""}, {"in_scope": True, "countries": ["kr"]})
        self.assertFalse(out["in_scope"])

    def test_exclusions_and_failures_pass_through(self):
        self.assertIsNone(lane.domestic_guard({"scope": "kr"}, None))
        out = lane.domestic_guard({"scope": "kr"}, {"in_scope": False, "exclude_reason": "fusion"})
        self.assertEqual(out["exclude_reason"], "fusion")

    def test_run_applies_guard_and_records_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kr = main_record("k", "Hyundai AI ship with SMR nuclear propulsion")
            kr["scope"] = "kr"
            ov = main_record("o", "NIVA nuclear AI fleetwide")
            stats = lane.run([kr, ov], judge_call=judge_all(["Hyundai", "NIVA"]), now=NOW,
                             state_file=base / "s.json", archive_dir=base / "a")
            rows = lane.load_archive(base / "a")
        self.assertEqual(stats["included"], 1)
        self.assertEqual([r["hash"] for r in rows], ["o"])
        self.assertEqual(rows[0]["main_scope"], "overseas")


class ResearchBackfillTests(unittest.TestCase):
    """원천 재검색 기간 백필(tools/ai_lane_research_backfill.py, 2026-10-15)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import ai_lane_research_backfill as bf  # noqa: PLC0415
        self.bf = bf
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state = base / "ai_state.json"
        self.archive = base / "ai_archive"
        self.main = base / "archive"
        self.main.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_period_queries_stay_short_and_cover_every_day(self):
        from datetime import date  # noqa: PLC0415
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
        sources = lane.backfill_sources(date(2026, 7, 1), date(2026, 10, 15))
        for src in sources:
            q = parse_qs(urlparse(src["url"]).query)["q"][0]
            self.assertLessEqual(len(q), 190, src["name"])
            self.assertIn("after:", q)
            self.assertIn("before:", q)
            self.assertNotIn("when:", q)
        # 질의마다 7/1~10/15 를 빈틈없이 덮는다.
        for label, _, _, _ in lane.BACKFILL_QUERIES:
            rows = [parse_qs(urlparse(s["url"]).query)["q"][0] for s in sources if s["label"] == label]
            self.assertIn("after:2026-07-01", rows[0])
            self.assertIn("before:2026-10-16", rows[-1])
        self.assertEqual({lang for _, lang, _, _ in lane.BACKFILL_QUERIES}, {"en", "ja", "fr", "ru", "zh"})

    def _main(self, rows):
        (self.main / "2026-08.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_fetch_only_counts_without_judging_or_writing(self):
        from datetime import date  # noqa: PLC0415
        self._main([main_record("a", "NIVA nuclear AI rollout", archived_at="2026-08-10T01:00:00+00:00"),
                    dict(main_record("k", "한수원 원전 AI 도입", archived_at="2026-08-10T01:00:00+00:00"), scope="kr"),
                    main_record("d", "Nuclear to power AI data centers", archived_at="2026-08-10T01:00:00+00:00"),
                    main_record("old", "NIVA nuclear AI", archived_at="2026-06-01T01:00:00+00:00")])

        def boom(system, user):
            raise AssertionError("fetch_only 는 판정하지 않는다")
        stats = self.bf.run(date(2026, 7, 1), date(2026, 10, 15), judge_call=boom, fetch_only=True,
                            now=NOW, state_file=self.state, archive_dir=self.archive, main_archive_dir=self.main)
        self.assertEqual(stats["shared_pool"], 3)
        self.assertEqual(stats["pre_excluded"], {"domestic": 1, "datacenter_power": 1})
        self.assertEqual(stats["to_judge"], 1)
        self.assertFalse(self.state.exists())

    def test_judges_records_with_publication_time_and_skips_on_rerun(self):
        from datetime import date  # noqa: PLC0415
        self._main([main_record("a", "NIVA nuclear AI rollout", archived_at="2026-08-10T01:00:00+00:00"),
                    main_record("b", "Reactor AI inspection pilot", archived_at="2026-08-11T01:00:00+00:00")])
        pub = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)

        def fetch(url, name):
            if "EN 원전 AI 2026-07-19" not in name:
                return []
            return [{"link": "https://news.google.com/rss/articles/r1", "title": "Rosatom NIVA nuclear AI digital twin",
                     "pub": pub, "publisher": "TASS", "publisher_domain": "tass.com"}]
        calls = []

        def call(system, user):
            calls.append(user)
            return judge_all(["NIVA"])(system, user)
        stats = self.bf.run(date(2026, 7, 1), date(2026, 10, 15), fetch=fetch, hasher=lambda u: "h" + u[-2:],
                            judge_call=call, max_judge=10, now=NOW, state_file=self.state,
                            archive_dir=self.archive, main_archive_dir=self.main)
        self.assertEqual(stats["included"], 2)
        self.assertEqual(stats["excluded"], 1)
        rows = {r["hash"]: r for r in lane.load_archive(self.archive)}
        self.assertEqual(rows["hr1"]["collected_at"], pub.isoformat())
        self.assertEqual(rows["hr1"]["backfill"], "research")
        self.assertTrue((self.archive / "2026-07.jsonl").exists())  # 수집 달의 파일
        again = self.bf.run(date(2026, 7, 1), date(2026, 10, 15), fetch=fetch, hasher=lambda u: "h" + u[-2:],
                            judge_call=call, max_judge=10, now=NOW, state_file=self.state,
                            archive_dir=self.archive, main_archive_dir=self.main)
        self.assertEqual(again["to_judge"], 0)

    def test_cap_leaves_the_rest_for_the_next_run(self):
        from datetime import date  # noqa: PLC0415
        self._main([main_record(f"m{i}", f"NIVA nuclear AI story {i}",
                                archived_at=f"2026-08-{10 + i:02d}T01:00:00+00:00") for i in range(5)])
        first = self.bf.run(date(2026, 7, 1), date(2026, 10, 15), judge_call=judge_all(["NIVA"]), max_judge=2,
                            now=NOW, state_file=self.state, archive_dir=self.archive, main_archive_dir=self.main)
        self.assertEqual((first["judged"], first["deferred"]), (2, 3))
        second = self.bf.run(date(2026, 7, 1), date(2026, 10, 15), judge_call=judge_all(["NIVA"]), max_judge=10,
                             now=NOW, state_file=self.state, archive_dir=self.archive, main_archive_dir=self.main)
        self.assertEqual(second["judged"], 3)

    def test_workflow_is_manual_locked_and_redeploys(self):
        wf = (ROOT / ".github" / "workflows" / "ai-lane-backfill.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", wf)
        self.assertIn("group: nuclens-state", wf)
        self.assertIn("python tools/ai_lane_research_backfill.py", wf)
        self.assertIn("git add ai_archive/", wf)
        self.assertIn("deploy-web.yml", wf)
