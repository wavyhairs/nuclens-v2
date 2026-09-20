"""카드 재료에 체크아웃 원장을 다시 투영한다 — `tools/reproject_threads.py`.

2026-09-21 실사고: 그날 새 id 로 조폐된 1위 이슈가 라이브 today.json 에서는
thread_id 빈칸(배포가 판정보다 먼저), 체크아웃 원장에서는 사건 2건짜리
스레드의 사건이었다. 카드는 빈칸을 보고 스토리를 조용히 건너뛰었다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "tools", ROOT / "web"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import reproject_threads as rt  # noqa: E402

NEW_ID = "story-fc64f50e257b3a0a"      # 그날 조폐된 1위
OLD_ID = "story-b674a830b574b651"      # 같은 스레드의 앞 사건
THREAD = "thread-9218375ad64119a8"


def _flow(source_id, date, relation):
    return {"event_id": source_id, "source_event_id": source_id,
            "source_event_ids": [source_id], "evidence_hashes": ["h-" + source_id],
            "title": source_id, "date": date, "date_kind": "first_seen",
            "relation_to_next": relation, "relation_label": ""}


def _payload(*, visible=True, source_generated_at="2026-09-21T04:52:44+09:00"):
    thread = {"thread_id": THREAD, "title": "미국, 한국의 대미 투자 지연에 불만",
              "first_seen": "2026-09-11", "last_seen": "2026-09-21", "lifespan_days": 10,
              "event_count": 2, "briefing_count": 2,
              "events": [{"event_id": NEW_ID, "source_event_id": NEW_ID},
                         {"event_id": OLD_ID, "source_event_id": OLD_ID}],
              "flow": [_flow(OLD_ID, "2026-09-11", "stage_progress"),
                       _flow(NEW_ID, "2026-09-21", "")]}
    return {"version": "thread-web-v2", "date_kind": "first_seen",
            "generated_at": "2026-09-21T04:53:00+09:00",
            "source_generated_at": source_generated_at,
            "visible": visible, "status": "ok" if visible else "hidden", "reasons": [],
            "degraded": False, "build": {"status": "ok"},
            "stats": {"threads": 1}, "redirects": {}, "threads": [thread] if visible else []}


def _issue(issue_id, thread_id=""):
    return {"issue_id": issue_id, "title": issue_id, "thread_id": thread_id,
            "related_articles": [{"hash": "keep-" + issue_id}]}


class LedgerFreshnessTests(unittest.TestCase):
    def test_a_newer_checkout_ledger_wins(self):
        site = {"source_generated_at": "2026-09-20T06:22:59+09:00"}
        newer, why = rt.ledger_is_newer(site, {"generated_at": "2026-09-21T04:52:44+09:00"})
        self.assertTrue(newer, why)

    def test_an_equal_or_older_checkout_ledger_leaves_the_site_files_alone(self):
        """크롤 빌드가 이미 오늘 판정을 실은 뒤의 수동 실행 — 손대지 않는다."""
        site = {"source_generated_at": "2026-09-21T04:52:44+09:00"}
        self.assertFalse(rt.ledger_is_newer(site, {"generated_at": "2026-09-21T04:52:44+09:00"})[0])
        self.assertFalse(rt.ledger_is_newer(site, {"generated_at": "2026-09-20T06:22:59+09:00"})[0])

    def test_a_ledger_without_a_clock_never_overwrites(self):
        self.assertFalse(rt.ledger_is_newer({"source_generated_at": "2026-09-21T04:52:44+09:00"}, {})[0])
        self.assertFalse(rt.ledger_is_newer({}, {})[0])

    def test_a_site_without_a_clock_is_treated_as_stale(self):
        """v1 스냅샷 등 source_generated_at 이 없던 시절의 파일."""
        self.assertTrue(rt.ledger_is_newer({}, {"generated_at": "2026-09-21T04:52:44+09:00"})[0])


class ReprojectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        issues = [_issue(NEW_ID), _issue("story-5b2aa08eeeb78c3e"), _issue(OLD_ID, THREAD),
                  _issue("story-stale", "thread-gone")]
        today = {"date": "2026-09-21", "issues": [
            {"issue_id": NEW_ID, "title": "정부, 2000억 달러 규모 대미투자", "thread_id": ""},
            {"issue_id": "story-5b2aa08eeeb78c3e", "title": "북한, IAEA", "thread_id": ""},
            {"issue_id": "story-not-in-catalog", "title": "카탈로그에 없는 행", "thread_id": "thread-keep"},
        ]}
        (self.dir / "issues.json").write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
        (self.dir / "today.json").write_text(json.dumps(today, ensure_ascii=False), encoding="utf-8")
        (self.dir / "threads.json").write_text(json.dumps(
            _payload(source_generated_at="2026-09-20T06:22:59+09:00", visible=True) | {"threads": []},
            ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def test_the_minted_top_issue_gets_its_thread_from_the_checkout_ledger(self):
        summary = rt.reproject(self.dir, _payload())
        today = self._read("today.json")
        self.assertEqual(today["issues"][0]["thread_id"], THREAD)
        self.assertEqual(today["issues"][1]["thread_id"], "")
        self.assertEqual(summary["today_before"], 1)  # 카탈로그 밖의 행이 든 주소
        self.assertEqual(summary["today_after"], 2)
        self.assertEqual(summary["top"][0], (NEW_ID, THREAD))
        issues = {row["issue_id"]: row for row in self._read("issues.json")}
        self.assertEqual(issues[NEW_ID]["thread_id"], THREAD)
        self.assertEqual(issues[OLD_ID]["thread_id"], THREAD)
        # 원장에서 사라진 스레드 주소는 비운다 — 사이트 빌드와 같은 규칙
        self.assertEqual(issues["story-stale"]["thread_id"], "")
        self.assertEqual(self._read("threads.json")["threads"][0]["thread_id"], THREAD)

    def test_rows_missing_from_the_catalog_keep_their_slot(self):
        rt.reproject(self.dir, _payload())
        self.assertEqual(self._read("today.json")["issues"][2]["thread_id"], "thread-keep")

    def test_ranking_ids_and_articles_are_untouched(self):
        before_today = self._read("today.json")
        rt.reproject(self.dir, _payload())
        after_today = self._read("today.json")
        self.assertEqual([row["issue_id"] for row in before_today["issues"]],
                         [row["issue_id"] for row in after_today["issues"]])
        self.assertEqual([row["title"] for row in before_today["issues"]],
                         [row["title"] for row in after_today["issues"]])
        issues = {row["issue_id"]: row for row in self._read("issues.json")}
        self.assertEqual(issues[NEW_ID]["related_articles"], [{"hash": "keep-" + NEW_ID}])

    def test_dry_run_writes_nothing(self):
        before = {name: (self.dir / name).read_bytes() for name in ("issues.json", "today.json", "threads.json")}
        summary = rt.reproject(self.dir, _payload(), dry_run=True)
        self.assertEqual(summary["today_after"], 2)
        for name, blob in before.items():
            self.assertEqual((self.dir / name).read_bytes(), blob, name)


class MainTests(unittest.TestCase):
    def test_missing_material_is_a_no_op_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rt.main(["--data-dir", tmp]), 0)


if __name__ == "__main__":
    unittest.main()
