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


if __name__ == "__main__":
    unittest.main()
