"""그림자 하루치 순위(shadow_rank) 테스트. 외부 호출 0 — call 을 주입한다.

잠그는 것
---------
- 후보는 현재 점수 상위 N 건, 순서를 섞어 두 번 묻고, 응답의 id 가 하나라도 빠지면 실패로 남는다.
- 경계 판정: 두 순위 차이가 BOUNDARY_GAP 이상이거나 선정 경계 k 를 사이에 두고 갈림.
- 어떤 예외도 밖으로 나가지 않는다 — 레코드의 error 에 남는다(브리핑은 그대로).
- (date, region) 멱등 적재. SHADOW_RANK=off 면 아무 호출도 없다.
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

import shadow_rank as sr  # noqa: E402
from tools import shadow_rank_report as report  # noqa: E402
from gemini_client import GeminiError  # noqa: E402


def _article(i: int, importance="nice_to_know"):
    return {"hash": f"{i:02d}" + "a" * 14, "title_kr": f"기사 {i}", "summary": f"요약 {i}",
            "detail": "", "publisher": "매체", "published_at": "2026-10-09T00:00:00+00:00",
            "importance": importance}


def _ranking_by_tag_order(system, user, **kwargs):
    """입력에 나온 순서대로 1,2,3… — 순서 민감한 모델을 흉내낸다."""
    tags = [line.split("]")[0][1:] for line in user.splitlines() if line.startswith("[")]
    return {"ranking": [{"id": t, "rank": i, "grade": "nice_to_know"} for i, t in enumerate(tags, 1)]}


def _ranking_by_hash(system, user, **kwargs):
    """입력 순서와 무관하게 hash 오름차순 — 안정적인 모델."""
    tags = sorted(line.split("]")[0][1:] for line in user.splitlines() if line.startswith("["))
    return {"ranking": [{"id": t, "rank": i, "grade": "must_read" if i == 1 else "nice_to_know"}
                        for i, t in enumerate(tags, 1)]}


class CandidatesTests(unittest.TestCase):
    def test_top_n_by_score_then_hash(self):
        pool = [_article(i) for i in range(5)]
        scores = {a["hash"]: 10.0 for a in pool}
        scores[pool[3]["hash"]] = 20.0
        top = sr.candidates(pool, scores, top_n=3)
        self.assertEqual([a["hash"][:2] for a in top], ["03", "00", "01"])


class ParseRankingTests(unittest.TestCase):
    def test_missing_id_is_an_error(self):
        with self.assertRaises(ValueError):
            sr.parse_ranking({"ranking": [{"id": "aa", "rank": 1}]}, {"aa", "bb"})

    def test_non_contiguous_ranks_are_renumbered(self):
        out = sr.parse_ranking({"ranking": [{"id": "aa", "rank": 5, "grade": "noise"},
                                            {"id": "bb", "rank": 2, "grade": "x"}]}, {"aa", "bb"})
        self.assertEqual(out["bb"]["rank"], 1)
        self.assertEqual(out["aa"]["rank"], 2)
        self.assertEqual(out["bb"]["grade"], "")      # 모르는 등급은 비운다


class RunTests(unittest.TestCase):
    def _pool(self, n=8):
        pool = [_article(i) for i in range(n)]
        scores = {a["hash"]: float(n - i) for i, a in enumerate(pool)}
        return pool, scores

    def test_stable_model_has_no_boundary_and_matches_selection(self):
        pool, scores = self._pool()
        selected = pool[:3]
        record = sr.run("국내", pool, selected, scores, "2026-10-10", call=_ranking_by_hash)
        self.assertIsNone(record["error"])
        self.assertEqual(record["calls"], 2)                  # 재질문 없음
        self.assertEqual(record["boundary"], [])
        self.assertEqual([r["gap"] for r in record["rows"]], [0] * 8)
        top = [r["hash"][:2] for r in record["rows"] if r["shadow_mean"] <= 3]
        self.assertEqual(top, ["00", "01", "02"])
        self.assertTrue(all(r["selected"] for r in record["rows"][:3]))

    def test_order_sensitive_model_produces_boundary_and_reasks(self):
        pool, scores = self._pool()
        selected = pool[:3]
        calls = {"reask": 0}

        def call(system, user, **kwargs):
            if system is sr.REASK_SYSTEM_PROMPT:
                calls["reask"] += 1
                return {"grade": "must_read" if calls["reask"] % 2 else "nice_to_know"}
            return _ranking_by_tag_order(system, user, **kwargs)

        record = sr.run("해외", pool, selected, scores, "2026-10-10", call=call)
        self.assertIsNone(record["error"])
        self.assertTrue(record["boundary"])
        self.assertLessEqual(len(record["reask"]), sr.REASK_LIMIT)
        self.assertEqual(record["calls"], 2 + len(record["reask"]) * sr.REASK_TIMES)
        self.assertEqual(record["reask"][0]["grades"], ["must_read", "nice_to_know"])

    def test_gemini_error_is_recorded_not_raised(self):
        pool, scores = self._pool()

        def boom(*a, **k):
            raise GeminiError("429")

        record = sr.run("국내", pool, pool[:2], scores, "2026-10-10", call=boom)
        self.assertTrue(record["error"].startswith("GeminiError"))
        self.assertEqual(record["rows"], [])

    def test_bad_payload_is_recorded_not_raised(self):
        pool, scores = self._pool()
        record = sr.run("국내", pool, pool[:2], scores, "2026-10-10",
                        call=lambda *a, **k: {"ranking": []})
        self.assertIn("ValueError", record["error"])

    def test_prompt_never_leaks_current_grade_or_score(self):
        pool, scores = self._pool(3)
        seen = []

        def call(system, user, **kwargs):
            seen.append(user)
            return _ranking_by_hash(system, user)

        sr.run("국내", pool, pool[:1], scores, "2026-10-10", call=call)
        self.assertNotIn("nice_to_know", seen[0])
        self.assertNotIn("점수", seen[0])


class AppendLogTests(unittest.TestCase):
    def test_idempotent_per_date_region(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            rec = {"record_type": "shadow_rank", "date": "2026-10-10", "region": "국내"}
            self.assertEqual(sr.append_log([rec, {**rec, "region": "해외"}], path), 2)
            self.assertEqual(sr.append_log([rec], path), 0)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)


class RunAllTests(unittest.TestCase):
    def test_off_switch_makes_no_calls(self):
        pool = [_article(i) for i in range(3)]
        with patch.dict(os.environ, {"SHADOW_RANK": "off"}):
            out = sr.run_all("2026-10-10", {"국내": (pool, pool[:1], {})},
                             call=lambda *a, **k: self.fail("호출돼서는 안 된다"))
        self.assertEqual(out, [])

    def test_injected_call_runs_without_api_key(self):
        pool = [_article(i) for i in range(3)]
        scores = {a["hash"]: 1.0 for a in pool}
        with patch.dict(os.environ, {"SHADOW_RANK": "on"}):
            out = sr.run_all("2026-10-10", {"국내": (pool, pool[:1], scores)}, call=_ranking_by_hash)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["region"], "국내")
        self.assertIsNone(out[0]["error"])
        json.dumps(out[0], ensure_ascii=False)   # 직렬화 가능


class ActualOutcomeTests(unittest.TestCase):
    """실제 선별이 후보마다 무엇을 했나 — 보고서가 같은 게이트·같은 사건으로 비교하게."""

    DIAG = {
        "dropped_duplicates": [
            {"hash": "dup", "dup_of": "sel"},
            # 사건을 합칠 때 대표가 자기 자신을 짝으로 남긴다 — 중복이 아니다(10/10 비스트라).
            {"hash": "rep", "dup_of": "rep"},
        ],
        "dropped_repeat": [{"hash": "rep"}],
        "dropped_below_floor": [{"hash": "low"}],
        "breakdowns": {"follow": {"continuity:minor": -2.5, "grade": 10.0},
                       "sel": {"continuity:material": 1.0}},
    }

    def test_outcomes_from_diag(self):
        out = sr.actual_outcomes([{"hash": "sel"}], self.DIAG)
        self.assertEqual(out["sel"], {"actual": "selected"})
        self.assertEqual(out["dup"], {"actual": "duplicate", "dup_of": "sel"})
        self.assertEqual(out["rep"], {"actual": "repeat"})
        self.assertEqual(out["low"], {"actual": "below_floor"})
        self.assertEqual(out["follow"], {"follow_up": -2.5})

    def test_run_annotates_rows_only_when_diag_is_given(self):
        pool = [_article(i) for i in range(4)]
        scores = {a["hash"]: float(4 - i) for i, a in enumerate(pool)}
        diag = {"dropped_duplicates": [{"hash": pool[2]["hash"], "dup_of": pool[0]["hash"]}]}
        record = sr.run("국내", pool, pool[:1], scores, "2026-10-11", call=_ranking_by_hash, diag=diag)
        by_hash = {r["hash"]: r for r in record["rows"]}
        self.assertEqual(by_hash[pool[0]["hash"]]["actual"], "selected")
        self.assertEqual(by_hash[pool[2]["hash"]]["dup_of"], pool[0]["hash"])
        self.assertEqual(by_hash[pool[3]["hash"]]["actual"], "ranked_out")
        self.assertEqual(record["selected_hashes"], [pool[0]["hash"]])
        self.assertTrue(record["actual_recorded"])
        bare = sr.run("국내", pool, pool[:1], scores, "2026-10-11", call=_ranking_by_hash)
        self.assertFalse(bare["actual_recorded"])
        self.assertNotIn("actual", bare["rows"][0])

    def test_run_all_passes_the_diag_through(self):
        pool = [_article(i) for i in range(3)]
        scores = {a["hash"]: 1.0 for a in pool}
        with patch.dict(os.environ, {"SHADOW_RANK": "on"}):
            out = sr.run_all("2026-10-11", {"국내": (pool, pool[:1], scores, {})},
                             call=_ranking_by_hash)
        self.assertTrue(out[0]["actual_recorded"])

    def test_daily_brief_hands_over_the_diag(self):
        source = (Path(__file__).parent.parent / "daily_brief.py").read_text(encoding="utf-8")
        start = source.index("shadow_records = shadow_rank.run_all(")
        call = source[start:source.index("})", start)]
        self.assertIn("dom_diag)", call)
        self.assertIn("forn_diag)", call)


class FairCompareTests(unittest.TestCase):
    """공정 비교: 게이트로 빠진 후보는 건너뛰고, 중복은 짝의 사건으로 접는다."""

    @staticmethod
    def _row(h, shadow, actual, **extra):
        return {"hash": h, "title": h, "importance": "nice_to_know", "score_rank": 1,
                "shadow_mean": shadow, "selected": actual == "selected", "actual": actual, **extra}

    def record(self):
        return {"record_type": "shadow_rank", "date": "2026-10-11", "region": "해외", "k": 2,
                "selected_hashes": ["A", "E"], "error": None, "rows": [
                    self._row("B", 1.0, "duplicate", dup_of="A"),   # 실제가 고른 사건의 다른 기사
                    self._row("F", 1.5, "duplicate", dup_of="G"),   # 대표 G 는 어제 보낸 사건
                    self._row("C", 2.0, "repeat"),
                    self._row("A", 3.0, "selected"),
                    self._row("D", 4.0, "ranked_out", follow_up=-2.0),
                    self._row("E", 5.0, "selected"),
                    self._row("G", 9.0, "repeat"),
                ]}

    def test_same_event_and_gates_are_respected(self):
        fair = report.fair_compare(self.record())
        self.assertEqual(fair["overlap"], 1)                 # A (B 로 골랐어도 같은 사건)
        self.assertEqual(fair["same_event"], 1)
        self.assertEqual([r["hash"] for r in fair["added"]], ["D"])
        self.assertEqual(fair["added_follow_up"], 1)
        self.assertEqual([r["hash"] for r in fair["dropped"]], ["E"])
        self.assertEqual(fair["gated"], {"duplicate": 2, "repeat": 0, "below_floor": 0})

    def test_naive_comparison_is_kept_for_continuity(self):
        summary = report.summarize(self.record())
        self.assertEqual(summary["overlap"], 0)              # 원래 비교는 B·F 를 넣음으로 셌다
        self.assertEqual(len(summary["added"]), 2)

    def test_record_without_outcomes_says_so(self):
        record = self.record()
        for row in record["rows"]:
            row.pop("actual")
        self.assertIsNone(report.fair_compare(record))
        text = report.render([report.summarize(record)])
        self.assertIn("실제 결과 미기록", text)


if __name__ == "__main__":
    unittest.main()
