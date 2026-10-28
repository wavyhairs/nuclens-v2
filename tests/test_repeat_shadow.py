"""재발송 그림자 판정(repeat_shadow) 테스트. 외부 호출 0 — call 을 주입한다.

잠그는 것
---------
- 처분은 모델이 아니라 코드가 정한다: 없는 사실 0(또는 배경뿐) → drop, 전부 직전 브리핑일 전 → late,
  그 뒤 사실이 하나라도 → new, 날짜 모름 → new_undated, 인용이 원문에 없음 → unverified.
- 원장에 같은 이야기가 없으면 묻지 않는다(new_issue, 호출 0).
- 응답에서 case 가 빠지거나 호출이 실패하면 그 묶음은 error — 예외는 밖으로 나가지 않는다.
- 호출 상한을 넘는 묶음은 skipped.
- REPEAT_SHADOW 가 꺼져 있으면 아무 호출도 없다. (date, region) 멱등 적재.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import repeat_shadow as rs  # noqa: E402
from gemini_client import GeminiError  # noqa: E402

TOLD = [
    {"date": "2026-10-17", "hash": "865faac86a1d5bd9", "region": "국내", "brief_rank": 1,
     "title_kr": "원안위, 고리 3·4호기 및 한빛 1·2호기 사고관리계획서 승인",
     "summary": "원자력안전위원회가 고리 3·4호기 및 한빛 1·2호기의 사고관리계획서를 승인했다."},
    {"date": "2026-10-24", "hash": "4d508e1f99a15166", "region": "국내", "brief_rank": 1,
     "title_kr": "원안위, 고리 3·4호기 계속운전 허가안 27일 재심의",
     "summary": "원자력안전위원회가 고리 3·4호기 계속운전 허가안을 오는 27일 재심의할 예정이다."},
]
REPEAT = {"hash": "274511da46688793", "published_at": "2026-10-23T21:03:16+00:00",
          "event_date": "2026-10-15", "importance": "must_read",
          "title_kr": "원안위, 고리 3·4호기 및 한빛 1·2호기 사고관리계획서 승인",
          "summary": "원안위는 제2026-16회 회의에서 고리 3·4호기 및 한빛 1·2호기의 사고관리계획서를 승인하고 "
                     "관련 운영변경허가 20건을 의결했다."}
FRESH = {"hash": "ffffffffffffffff", "published_at": "2026-10-24T22:00:00+00:00",
         "title_kr": "LS일렉트릭, 미국 데이터센터 배전반 공급 계약", "summary": "새 계약을 체결했다."}


def _answer(facts_by_case):
    """사실 목록을 돌려주는 대역. told_in 이 없는 사실은 TOLD 에 없는 사실이다."""
    def call(system, user, **kwargs):
        count = user.count("[CASE ")
        return {"cases": [{"case": i, "facts": facts_by_case(i)} for i in range(count)]}
    return call


def ALL_TOLD(_index):
    """핵심 사실을 적었고 전부 앞선 카드(T1)가 이미 전했다 — 재탕."""
    return [{"fact": "사고관리계획서 승인", "told_in": "T1", "kind": "development",
             "date": "2026-10-15", "quote": "사고관리계획서를 승인"}]


class DispositionTests(unittest.TestCase):
    def _facts(self, *rows):
        return [{"fact": "f", "date": d, "quote": "q", "quote_ok": ok} for d, ok in rows]

    def test_rules(self):
        last = "2026-10-24"
        self.assertEqual(rs.disposition([], REPEAT, last), "drop")
        self.assertEqual(rs.disposition(self._facts(("2026-10-15", True)), REPEAT, last), "late")
        self.assertEqual(rs.disposition(self._facts(("2026-10-15", True), ("2026-10-24", True)), REPEAT, last), "new")
        self.assertEqual(rs.disposition(self._facts(("2026-10-15", True), (None, True)), REPEAT, last), "new_undated")
        self.assertEqual(rs.disposition(self._facts(("2026-10-25", False)), REPEAT, last), "unverified")
        # 인용이 없는 사실은 처분에 쓰지 않는다 — 검증된 것이 늦은 사실뿐이면 late
        self.assertEqual(rs.disposition(self._facts(("2026-10-15", True), ("2026-10-25", False)), REPEAT, last), "late")

    def test_background_alone_is_not_news(self):
        """v2 (10/27 DL이앤씨): 새것이 '국내 최초'·'시장 전망' 같은 배경뿐이면 재탕이다."""
        last = "2026-10-24"
        background = [{"fact": "국내 건설사 최초", "kind": "background", "date": None,
                       "quote": "q", "quote_ok": True}]
        self.assertEqual(rs.disposition(background, REPEAT, last), "drop")
        # 원문에서 확인된 진전이 하나라도 있으면 배경은 상관없다
        development = {"fact": "계약 체결", "kind": "development", "date": "2026-10-25",
                       "quote": "q", "quote_ok": True}
        self.assertEqual(rs.disposition(background + [development], REPEAT, last), "new")
        # 인용을 못 맞춘 진전이 있으면 빼지 않는다
        self.assertEqual(rs.disposition(background + [{**development, "quote_ok": False}],
                                        REPEAT, last), "unverified")

    def test_facts_must_be_matched_to_a_real_told_card(self):
        parsed = rs.parse_cases({"cases": [{"case": 0, "facts": [
            {"fact": "a", "told_in": "T2"}, {"fact": "b", "told_in": "T9"},
            {"fact": "c", "told_in": "null"}, {"fact": "d", "told_in": " t1 "}]}]}, 1, [2])
        self.assertEqual([f["told_in"] for f in parsed[0]], ["T2", None, None, "T1"])

    def test_no_listed_fact_is_not_a_repeat(self):
        """사실을 하나도 못 적은 답은 비교를 안 한 것이다 — 빼지 않는다."""
        [record] = rs.run("2026-10-25", {"국내": [REPEAT]}, call=_answer(lambda i: []),
                          told=TOLD, issues={}, cfg={})
        self.assertEqual(record["rows"][0]["verdict"], "unverified")

    def test_untold_development_keeps_the_card(self):
        call = _answer(lambda i: [*ALL_TOLD(i), {
            "fact": "운영변경허가 20건 의결", "told_in": None, "kind": "development",
            "date": "2026-10-24", "quote": "운영변경허가 20건을 의결"}])
        [record] = rs.run("2026-10-25", {"국내": [REPEAT]}, call=call, told=TOLD, issues={}, cfg={})
        row = record["rows"][0]
        self.assertEqual(row["verdict"], "new")
        self.assertEqual(row["told_facts"], [{"fact": "사고관리계획서 승인", "told_in": "T1"}])

    def test_unknown_kind_counts_as_development(self):
        parsed = rs.parse_cases({"cases": [{"case": 0, "untold": [
            {"fact": "a", "kind": "background"}, {"fact": "b", "kind": "??"}, {"fact": "c"}]}]}, 1)
        self.assertEqual([f["kind"] for f in parsed[0]], ["background", "development", "development"])

    def test_drop_records_why(self):
        call = _answer(lambda i: [{"fact": "국내 최초", "kind": "background", "date": None,
                                   "quote": "고리 3·4호기 및 한빛 1·2호기"}])
        records = rs.run("2026-10-25", {"국내": [REPEAT]}, call=call, told=TOLD, issues={}, cfg={})
        row = records[0]["rows"][0]
        self.assertEqual((row["verdict"], row["drop_reason"]), ("drop", "background_only"))
        self.assertEqual(records[0]["prompt_version"], 2)


class QuoteTests(unittest.TestCase):
    def test_quote_must_appear_in_candidate_text(self):
        self.assertTrue(rs.quote_ok("운영변경허가 20건을 의결", REPEAT))
        self.assertTrue(rs.quote_ok("고리 3·4호기 및 한빛 1·2호기", REPEAT))
        # 공백·따옴표·가운뎃점·쉼표 차이는 흡수한다
        self.assertTrue(rs.quote_ok("“고리 3, 4호기”", {"summary": "고리 34호기"}))
        self.assertFalse(rs.quote_ok("방사능방재법 하위법령 개정", REPEAT))
        self.assertFalse(rs.quote_ok("짧", REPEAT))


class ParseTests(unittest.TestCase):
    def test_drops_malformed_and_out_of_range(self):
        parsed = rs.parse_cases({"cases": [
            {"case": 0, "untold": [{"fact": "a", "date": "2026-10-15", "quote": "q"},
                                   {"fact": "", "date": "x"}, "junk"]},
            {"case": 5, "untold": []},
            {"case": 0, "untold": []},
        ]}, 2)
        self.assertEqual(list(parsed), [0])
        self.assertEqual(parsed[0][0]["date"], "2026-10-15")
        self.assertEqual(len(parsed[0]), 1)

    def test_bad_date_becomes_none(self):
        parsed = rs.parse_cases({"cases": [{"case": 0, "untold": [{"fact": "a", "date": "10월 15일"}]}]}, 1)
        self.assertIsNone(parsed[0][0]["date"])


class RunTests(unittest.TestCase):
    def _run(self, call, cards=None, **kwargs):
        return rs.run("2026-10-25", {"국내": cards if cards is not None else [REPEAT, FRESH]},
                      call=call, told=TOLD, issues={}, cfg={}, **kwargs)

    def test_repeat_drops_and_unrelated_is_new_issue_without_call(self):
        calls = []

        def call(system, user, **kwargs):
            calls.append(user)
            return _answer(ALL_TOLD)(system, user)

        [record] = self._run(call)
        rows = {r["hash"]: r for r in record["rows"]}
        self.assertEqual(rows[REPEAT["hash"]]["verdict"], "drop")
        self.assertEqual(rows[FRESH["hash"]]["verdict"], "new_issue")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].count("[CASE "), 1)          # 관련 없는 카드는 묻지 않는다
        self.assertIn("2026-10-17", calls[0])                    # 10/17 카드가 TOLD 로 들어간다
        self.assertIn("event_date=2026-10-15", calls[0])         # 후보 사건일을 넘긴다
        self.assertEqual(record["lead_verdict"], "drop")
        self.assertEqual(record["summary"], {"drop": 1, "new_issue": 1})
        self.assertEqual(record["last_brief_day"], "2026-10-24")

    def test_late_fact(self):
        call = _answer(lambda i: [{"fact": "운영변경허가 20건 의결", "date": "2026-10-15",
                                   "quote": "운영변경허가 20건을 의결"}])
        [record] = self._run(call, cards=[REPEAT])
        self.assertEqual(record["rows"][0]["verdict"], "late")
        self.assertTrue(record["rows"][0]["untold"][0]["quote_ok"])

    def test_missing_case_marks_chunk_error_without_raising(self):
        [record] = self._run(lambda system, user, **kw: {"cases": []}, cards=[REPEAT])
        self.assertEqual(record["rows"][0]["verdict"], "error")
        self.assertIn("빠진 case", record["rows"][0]["error"])
        self.assertIsNone(record["error"])

    def test_gemini_error_marks_error(self):
        def call(system, user, **kwargs):
            raise GeminiError("429 quota")
        [record] = self._run(call, cards=[REPEAT])
        self.assertEqual(record["rows"][0]["verdict"], "error")

    def test_unexpected_exception_never_escapes(self):
        def call(system, user, **kwargs):
            raise RuntimeError("boom")
        records = self._run(call, cards=[REPEAT])
        self.assertIn("RuntimeError", records[0]["error"])

    def test_call_cap_skips_rest(self):
        cards = [{**REPEAT, "hash": f"{i:016x}"} for i in range(rs.CASES_PER_CALL + 1)]
        [record] = self._run(_answer(ALL_TOLD), cards=cards, max_calls=1)
        verdicts = [r["verdict"] for r in record["rows"]]
        self.assertEqual(verdicts.count("drop"), rs.CASES_PER_CALL)
        self.assertEqual(verdicts.count("skipped"), 1)
        self.assertEqual(record["calls"], 1)


    def test_time_budget_skips_rest(self):
        cards = [{**REPEAT, "hash": f"{i:016x}"} for i in range(rs.CASES_PER_CALL + 1)]
        ticks = iter([0.0, 0.0, 999.0])          # 시작 · 첫 묶음 직전 · 둘째 묶음 직전

        [record] = self._run(_answer(ALL_TOLD), cards=cards, clock=lambda: next(ticks))
        verdicts = [r["verdict"] for r in record["rows"]]
        self.assertEqual(verdicts.count("skipped"), 1)
        self.assertEqual(record["calls"], 1)

    def test_call_uses_short_timeout_and_one_retry(self):
        seen = {}

        def call(system, user, **kwargs):
            seen.update(kwargs)
            return _answer(ALL_TOLD)(system, user)
        self._run(call, cards=[REPEAT])
        self.assertEqual(seen["timeout"], rs.CALL_TIMEOUT_S)
        self.assertEqual(seen["retries"], rs.CALL_RETRIES)


class SwitchAndLogTests(unittest.TestCase):
    def test_disabled_makes_no_call(self):
        def call(*args, **kwargs):
            raise AssertionError("호출하면 안 된다")
        with patch.dict(os.environ, {"REPEAT_SHADOW": "off"}):
            self.assertEqual(rs.run_all("2026-10-25", {"국내": [REPEAT]}, call=call), [])

    def test_enabled_runs(self):
        with patch.dict(os.environ, {"REPEAT_SHADOW": "on"}):
            records = rs.run_all("2026-10-25", {"국내": [REPEAT]}, call=_answer(ALL_TOLD),
                                 told=TOLD, issues={}, cfg={})
        self.assertEqual(records[0]["rows"][0]["verdict"], "drop")

    def test_append_log_idempotent_and_skips_empty(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            rec = {"date": "2026-10-25", "region": "국내", "rows": [{"hash": "h"}]}
            empty = {"date": "2026-10-25", "region": "해외", "rows": []}
            self.assertEqual(rs.append_log([rec, empty], path), 1)
            self.assertEqual(rs.append_log([rec], path), 0)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
