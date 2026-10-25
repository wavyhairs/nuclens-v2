"""발송 원장(told_ledger) 테스트. LLM·네트워크 0.

잠그는 것
---------
- 원장은 delivery_log 의 기사 줄만, 오늘 **이전** WINDOW_DAYS 일만 읽는다(진단 줄·오늘 제외).
- 웹 이슈 원장은 moved_to 를 따라 최종 이슈로 접는다.
- 2026-10-25 사례: 같은 제목의 10/17 카드가 8일 전(현행 7일 창 밖)이고 더 최근의 덜 닮은
  10/24 카드가 있어도, 원장은 10/17 카드를 찾는다.
- 어휘가 한 장만 찾아도 웹 이슈가 같은 이야기의 다른 발송 카드를 데려온다.
- 상한에 걸리면 어휘로 찾은 카드가 이슈 확장 카드보다 먼저 자리를 받는다.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))

import issue_continuity  # noqa: E402
import told_ledger  # noqa: E402

CFG = issue_continuity.resolve_config({})


def _card(day: str, h: str, title: str, summary: str = "", **extra) -> dict:
    return {"date": day, "hash": h, "title_kr": title, "title": title,
            "summary": summary or title, "region": "국내", "brief_rank": 1, **extra}


# 실제 발송 제목(2026-10-13~25, delivery_log)
PREVIEW = _card("2026-10-13", "caef49e6fcaeb916",
                "원안위, 15일 고리 3·4호기 사고관리계획서 및 계속운전 허가안 심의")
APPROVED = _card("2026-10-17", "865faac86a1d5bd9",
                 "원안위, 고리 3·4호기 및 한빛 1·2호기 사고관리계획서 승인",
                 "원자력안전위원회가 고리 3·4호기 및 한빛 1·2호기의 사고관리계획서를 승인했다.")
APPROVED_AGAIN = _card("2026-10-18", "727d06348551a08a",
                       "원안위, 고리 3·4호기 사고관리계획서 승인…계속운전 심의는 보류",
                       "원자력안전위원회가 고리 3·4호기 사고관리계획서를 승인했으나 계속운전 허가는 재논의.")
RESCHEDULED = _card("2026-10-24", "4d508e1f99a15166",
                    "원안위, 고리 3·4호기 계속운전 허가안 27일 재심의")
UNRELATED = _card("2026-10-22", "aaaaaaaaaaaaaaaa", "LS일렉트릭, 미국 데이터센터 배전반 공급 계약")
TODAY = _card("2026-10-25", "274511da46688793",
              "원안위, 고리 3·4호기 및 한빛 1·2호기 사고관리계획서 승인",
              "원안위는 제2026-16회 회의에서 고리 3·4호기 및 한빛 1·2호기의 사고관리계획서를 승인했다.")
ISSUE = "story-caef49e6fcaeb916"


class LoadToldTests(unittest.TestCase):
    def test_reads_only_article_lines_before_today_within_window(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery_log.jsonl"
            rows = [APPROVED, RESCHEDULED, {**TODAY},
                    {"record_type": "selection_stats", "date": "2026-10-24"},
                    _card("2026-09-01", "bbbbbbbbbbbbbbbb", "오래된 카드"),
                    APPROVED]  # 재실행으로 같은 줄이 두 번
            path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                            encoding="utf-8")
            told = told_ledger.load_told("2026-10-25", path=path)
        self.assertEqual([r["hash"] for r in told], [APPROVED["hash"], RESCHEDULED["hash"]])

    def test_missing_file_is_empty(self):
        self.assertEqual(told_ledger.load_told("2026-10-25", path=Path("/nonexistent/x.jsonl")), [])


class IssueIndexTests(unittest.TestCase):
    def test_follows_moved_to(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue_ledger.json"
            path.write_text(json.dumps({"issues": {
                "old": {"moved_to": "new", "hashes": ["h1"]},
                "new": {"hashes": ["h2"]},
            }}), encoding="utf-8")
            index = told_ledger.issue_index(path)
        self.assertEqual(index, {"h1": "new", "h2": "new"})


class RelatedTests(unittest.TestCase):
    def test_finds_identical_card_outside_seven_days_behind_newer_match(self):
        told = [PREVIEW, APPROVED, APPROVED_AGAIN, UNRELATED, RESCHEDULED]
        rows = told_ledger.related(TODAY, told, {}, CFG)
        found = [r["hash"] for r in rows]
        self.assertIn(APPROVED["hash"], found)
        self.assertIn(APPROVED_AGAIN["hash"], found)
        self.assertNotIn(UNRELATED["hash"], found)
        self.assertEqual(found, sorted(found, key=lambda h: next(r["date"] for r in told if r["hash"] == h)))

    def test_issue_expansion_brings_dissimilar_cards_of_same_story(self):
        older = _card("2026-10-01", "cccccccccccccccc", "원전 4기 가동 중단으로 전력 생산 차질")
        told = [older, APPROVED_AGAIN, UNRELATED]
        issues = {APPROVED_AGAIN["hash"]: ISSUE, older["hash"]: ISSUE}
        without = {r["hash"] for r in told_ledger.related(TODAY, told, {}, CFG)}
        with_issue = told_ledger.related(TODAY, told, issues, CFG)
        self.assertNotIn(older["hash"], without)
        self.assertIn(older["hash"], {r["hash"] for r in with_issue})
        self.assertIn("issue", next(r for r in with_issue if r["hash"] == older["hash"])["via"])

    def test_lexical_seed_outranks_issue_expansion_at_limit(self):
        filler = [_card(f"2026-10-{d:02d}", f"{d:02d}" + "f" * 14, f"대미 투자 관련 다른 카드 {d}")
                  for d in range(10, 22)]
        told = filler + [APPROVED]
        issues = {r["hash"]: ISSUE for r in told}
        rows = told_ledger.related(TODAY, told, issues, CFG, limit=3)
        self.assertEqual(len(rows), 3)
        self.assertIn(APPROVED["hash"], {r["hash"] for r in rows})

    def test_shared_story_hash_links_without_lexical_overlap(self):
        cand = {**_card("2026-10-25", "dddddddddddddddd", "전혀 다른 표현의 제목"),
                "story_article_hashes": ["x1", "x2"]}
        prior = {**_card("2026-10-20", "eeeeeeeeeeeeeeee", "다른 매체 다른 말"),
                 "story_article_hashes": ["x2"]}
        rows = told_ledger.related(cand, [prior], {}, CFG)
        self.assertEqual([r["hash"] for r in rows], [prior["hash"]])
        self.assertIn("evidence", rows[0]["via"])

    def test_empty_told(self):
        self.assertEqual(told_ledger.related(TODAY, [], {}, CFG), [])


if __name__ == "__main__":
    unittest.main()
