"""운영 콘솔 '그날 화면에서 뺀 것' — 연속일 자동 제외와 사람 숨김을 한 칸에.

빠진 후보는 발송되지 않아 결과물에 흔적이 없다. 2026-09-25 이탈리아 반복은 사람이
selection_overrides 로 먼저 내렸고, 다음 날부터는 알고리즘(cross_day_audit)이 뺀다.
콘솔이 둘을 갈라 보여 줘야 알고리즘이 무엇을 놓치는지 보인다.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import build_data  # noqa: E402

KST = timezone(timedelta(hours=9))


def audit(day, *verdicts, generated_at="2026-09-26T04:10:00+09:00"):
    return {"record_type": "cross_day_audit", "date": day, "generated_at": generated_at,
            "verdicts": list(verdicts)}


DROP = {"hash": "c2d8c771daf24eb2", "title": "이탈리아 상원, 원자력 발전 복귀 법안 가결",
        "prior_title": "이탈리아, 40년 만에 원전 부활법 통과", "prior_date": "2026-09-24",
        "relation": "same_detail", "confirm": "no_new_action",
        "new_facts": ["찬성 81표"], "reason": "같은 상원 표결", "drop": True}
KEPT = {**DROP, "hash": "k1", "title": "이탈리아 하원, 원전 법안 통과",
        "relation": "next_step", "drop": False, "confirm": ""}


class RepeatRemovalsTests(unittest.TestCase):
    def test_auto_and_manual_are_told_apart(self):
        rows = build_data.build_repeat_removals(
            [audit("2026-09-26", DROP, KEPT)],
            [{"hash8": "2122da70", "date": "2026-09-25", "action": "hide_from_today",
              "reason": "사람이 소급"}],
            [{"hash": "2122da7027f6180d", "title_kr": "웨스팅하우스 지분율 7%대 전망"}])
        kinds = [(row["date"], row["kind"]) for row in rows]
        self.assertEqual(kinds, [("2026-09-26", "auto_drop"), ("2026-09-26", "auto_kept"),
                                 ("2026-09-25", "manual_hide")])
        self.assertEqual(rows[0]["prior_title"], "이탈리아, 40년 만에 원전 부활법 통과")
        self.assertEqual(rows[2]["title"], "웨스팅하우스 지분율 7%대 전망")
        self.assertEqual(rows[2]["diagnosis_rounds"], ["2026-09-25"])

    def test_merges_payload_carries_rows_and_round_counts(self):
        rows = build_data.build_repeat_removals([audit("2026-09-26", DROP, KEPT)], [], [])
        now = datetime(2026, 9, 26, 7, 0, tzinfo=KST)
        merges = build_data.build_admin_merges(
            [], [], {"clusters": [], "review_candidates": []}, now, [], rows)
        self.assertEqual(len(merges["story"]["repeat_removals"]), 2)
        index = {row["date"]: row for row in merges["rounds"]["dates"]}
        self.assertEqual(index["2026-09-26"]["repeat"], 2)

    def test_default_payload_is_empty_not_missing(self):
        now = datetime(2026, 9, 26, 7, 0, tzinfo=KST)
        merges = build_data.build_admin_merges(
            [], [], {"clusters": [], "review_candidates": []}, now)
        self.assertEqual(merges["story"]["repeat_removals"], [])

    def test_loader_keeps_latest_round_per_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "delivery_log.jsonl"
            lines = [
                audit("2026-09-26", KEPT, generated_at="2026-09-26T04:10:00+09:00"),
                audit("2026-09-26", DROP, generated_at="2026-09-26T06:30:00+09:00"),
                {"record_type": "story_audit", "date": "2026-09-26"},
                audit("2026-09-25", DROP),
            ]
            log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in lines)
                           + "\nnot json\n", encoding="utf-8")
            original = build_data.BOT_DIR
            build_data.BOT_DIR = Path(tmp)
            try:
                got = build_data.load_cross_day_audits()
            finally:
                build_data.BOT_DIR = original
        self.assertEqual([row["date"] for row in got], ["2026-09-26", "2026-09-25"])
        self.assertTrue(got[0]["verdicts"][0]["drop"])

    def test_manual_loader_skips_undated_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "selection_overrides.json"
            path.write_text(json.dumps({"demote": [
                {"hash8": "c2d8c771", "date": "2026-09-25", "action": "hide_from_today"},
                {"hash8": "deadbeef"},
                {"hash8": "abcd1234", "date": "2026-09-25", "action": "demote_only"},
            ]}), encoding="utf-8")
            rows = build_data.load_manual_hides(path)
        self.assertEqual([(r["hash8"], r["action"]) for r in rows],
                         [("c2d8c771", "hide_from_today"), ("abcd1234", "demote_only")])

    def test_console_renders_the_new_fold(self):
        """화면 계약 — 칸·본문 id·렌더 함수가 서로를 가리킨다."""
        html = (ROOT / "public" / "admin" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "public" / "admin" / "admin.js").read_text(encoding="utf-8")
        self.assertIn('data-fold="repeat"', html)
        self.assertIn('id="repeatRemovals"', html)
        self.assertIn('name: "repeat"', js)
        self.assertIn("function renderRepeatRemovals()", js)
        self.assertIn("repeat_removals", js)


if __name__ == "__main__":
    unittest.main()
