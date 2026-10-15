"""신규원전 공론화 레인 → 웹 산출물. 회차는 아침 브리핑, 사건 묶기는 결정적 비교 셋."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ai_lane_web  # noqa: E402
import deliberation_web as web  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)


def row(digest, key, collected, *, title=None, publisher="연합뉴스", folded_into="", judge="gemini",
        significance=2, stage="agenda_design", actor="committee", article_type="report", stance="neutral",
        confidence="high"):
    return {"hash": digest, "event_key": key, "title_kr": title or f"{key} 보도 {digest}", "title": title or digest,
            "collected_at": collected, "pub": collected, "publisher": publisher, "folded_into": folded_into,
            "judge": judge, "significance": significance, "stage": stage, "actor_type": actor,
            "article_type": article_type, "stance": stance, "confidence": confidence, "origin": "shared",
            "summary": "요약", "url": f"https://x.test/{digest}"}


def at(day, hour=1):
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+09:00").astimezone(timezone.utc).isoformat()


class WebTests(unittest.TestCase):
    def setUp(self):
        # 아침 브리핑 마감 07:00 KST — 10/13·10/14·10/15.
        ai_lane_web.set_briefing_cutoffs([
            (datetime.fromisoformat(f"2026-10-{d}T07:00:00+09:00"), f"2026-10-{d}") for d in (13, 14, 15)])

    def tearDown(self):
        ai_lane_web.set_briefing_cutoffs(None)

    def test_same_event_key_groups_across_outlets_and_spacing(self):
        groups = web.group_rows([row("a", "원전 공론화 의제 확정", at("2026-10-14")),
                                 row("b", "원전공론화 의제확정", at("2026-10-14"), publisher="경북매일"),
                                 row("c", "공론화위 1차 회의", at("2026-10-14"))])
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])

    def test_folded_article_joins_its_representative_even_with_another_key(self):
        groups = web.group_rows([row("a", "원전 공론화 의제 확정", at("2026-10-14")),
                                 row("f", "다른 이름", at("2026-10-14"), folded_into="a", judge="inherited")])
        self.assertEqual(len(groups), 1)

    def test_same_key_after_a_week_is_a_new_event(self):
        groups = web.group_rows([row("a", "12차 전기본 확정 연기", at("2026-10-01")),
                                 row("b", "12차 전기본 확정 연기", at("2026-10-14"))])
        self.assertEqual(len(groups), 2)

    def test_gold_events_are_never_merged(self):
        gold = {}
        for line in (ROOT / "eval_artifacts/deliberation/gold.jsonl").read_text(encoding="utf-8").splitlines():
            g = json.loads(line)
            if g["in_scope"]:
                gold[g["hash"]] = g["event_key"]
        ai_lane_web.set_briefing_cutoffs(None)
        rows = [r for r in web.load_rows() if r["hash"] in gold]
        self.assertEqual(len(rows), len(gold))
        groups = web.group_rows(rows)
        keys_per_group = [{gold[r["hash"]] for r in group} for group in groups]
        self.assertTrue(all(len(keys) == 1 for keys in keys_per_group), "서로 다른 gold 사건이 한 이슈로 합쳐졌다")
        # 갈라지는 것은 첫날부터 7일 창을 넘긴 같은 이름뿐이다 — 9/23 연기 발표와 10/6 이후 연기 보도,
        # 9/29 국감 예고와 10/6 이후 국감, 10/2 출범과 10/12 뒤늦은 출범 기사(3곳).
        self.assertLessEqual(len(groups) - len(set(gold.values())), 3)

    def test_headline_comes_from_the_busiest_round(self):
        # 마지막 회차 대표가 아니라 기사가 가장 많이 나온 회차의 대표가 제목이 된다.
        rows = [row(f"a{i}", "공론화위 출범", at("2026-10-13", 8 + i), title=f"공론화위 출범 {i}",
                    publisher=f"매체{i}") for i in range(3)]
        rows.append(row("late", "공론화위 출범", at("2026-10-14", 9), title="[국감] 곁가지 기사"))
        issue = web.build_issue(rows, today="2026-10-15")
        self.assertTrue(issue["title"].startswith("공론화위 출범"), issue["title"])
        self.assertEqual(issue["last_seen"], "2026-10-15")

    def test_a_recurring_key_does_not_chain_past_the_window(self):
        # 백필 실측: 같은 사건 이름이 엿새마다 나오면 마지막 날 기준 창으로는 9/20~10/13 이 한 사건이 됐다.
        rows = [row(f"r{i}", "기후부 신규 원전 규모 공론화 거쳐 결정 방침", at(day))
                for i, day in enumerate(("2026-09-20", "2026-09-26", "2026-10-02", "2026-10-08"))]
        self.assertEqual([len(g) for g in web.group_rows(rows)], [2, 2])

    def test_payload_rounds_pending_and_scores(self):
        rows = [row("a", "공론화위 1차 회의", at("2026-10-14", 3), significance=3),
                row("b", "공론화위 1차 회의", at("2026-10-14", 4), publisher="경향신문"),
                row("c", "공론화위 1차 회의", at("2026-10-14", 5), publisher="경북매일"),
                row("d", "공론화위 1차 회의", at("2026-10-14", 6), publisher="한겨레"),
                row("e", "[사설] 숙의의 조건", at("2026-10-14", 6), actor="media", article_type="editorial",
                    stage="reaction", significance=0),
                # 마지막 브리핑(10/15 07:00) 뒤 — 다음 브리핑에 실릴 기사
                row("p", "원전 공론화 의제 확정", at("2026-10-15", 12), significance=3)]
        payload = web.build_payload(rows, now=NOW)
        self.assertEqual([r["date"] for r in payload["rounds"]], ["2026-10-14"])
        first = payload["rounds"][0]
        self.assertEqual((first["article_count"], first["outlet_count"]), (5, 4))
        top = web_issue(payload, first["issue_ids"][0])
        self.assertEqual(top["event_key"], "공론화위 1차 회의")
        self.assertIn("매체 4곳 +2", top["round_scores"]["2026-10-14"]["breakdown"])
        self.assertTrue(web_issue(payload, first["issue_ids"][1])["editorial"])
        self.assertEqual(payload["pending"]["date"], "2026-10-16")
        self.assertEqual([i["event_key"] for i in payload["pending"]["issues"]], ["원전 공론화 의제 확정"])
        self.assertEqual(payload["stats"]["articles"], 6)

    def test_current_stage_ignores_reactions(self):
        rows = [row("a", "공론화위원회 출범", at("2026-10-13"), significance=3, stage="committee_setup"),
                row("b", "[사설] 반발", at("2026-10-14"), significance=3, stage="reaction")]
        payload = web.build_payload(rows, now=NOW)
        self.assertEqual(payload["current_stage"]["stage"], "committee_setup")

    def test_representative_prefers_own_judgment_over_inherited(self):
        rows = [row("f", "의제 확정", at("2026-10-14", 2), judge="inherited", folded_into="a", publisher="통신"),
                row("a", "의제 확정", at("2026-10-14", 2), publisher="지역지")]
        issue = web.build_payload(rows, now=NOW)["issues"][0]
        cards = [a for a in issue["related_articles"] if a["member_role"] == "card"]
        self.assertEqual([a["hash"] for a in cards], ["a"])

    def test_review_flag_only_when_every_article_is_uncertain(self):
        low = [row("a", "영덕 MOU 보류", at("2026-10-14"), confidence="low"),
               row("b", "영덕 MOU 보류", at("2026-10-14"), confidence="low", publisher="경북매일")]
        self.assertTrue(web.build_payload(low, now=NOW)["issues"][0]["review"])
        mixed = low + [row("c", "영덕 MOU 보류", at("2026-10-14"), publisher="KBS")]
        self.assertFalse(web.build_payload(mixed, now=NOW)["issues"][0]["review"])


def web_issue(payload, issue_id):
    return next(i for i in payload["issues"] if i["issue_id"] == issue_id)


class WiringTests(unittest.TestCase):
    def test_build_data_ships_the_payload(self):
        src = (ROOT / "web/build_data.py").read_text(encoding="utf-8")
        self.assertIn('("deliberation_issues.json", build_deliberation_payload(now))', src)

    def test_app_loads_and_gates_the_tab(self):
        app = (ROOT / "web/public/app.js").read_text(encoding="utf-8")
        self.assertIn('loadJSON("deliberation_issues.json").catch(() => null)', app)
        self.assertIn('if (view === "deliberation" && !dlReady()) view = "news";', app)
        html = (ROOT / "web/public/index.html").read_text(encoding="utf-8")
        self.assertIn('data-view="deliberation" aria-pressed="false" hidden>신규원전 공론화', html)
        self.assertIn('data-scope="deliberation"', html)
        # 「오늘 들어온 기사」가 목록보다 먼저 온다 — 이 탭의 요청이 '3시간마다 계속 띄우기'다.
        self.assertLess(html.index('id="dlPending"'), html.index('id="dlTocList"'))


if __name__ == "__main__":
    unittest.main()
