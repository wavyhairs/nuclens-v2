"""`why_short` 가 화면에 닿는 **유일한 길** — finalize_card_fields() 의 우선순위.

화면 1단은 `card_why` 하나만 읽는다. 무엇을 그 자리에 태울지는 여기서 정한다:
`why_short` → `implication` → `why_important`.

프론트에서 `A || B || C` 로 고르면 이 함수의 두 필터(제목 재진술·change_display
중복)를 통째로 우회한다. build_data.py 의 card_why 주석이 그 이유를 적어 뒀고,
아래 `test_a_title_restating_why_short_loses_to_implication` 이 그 자리를 잡는다.

이 검사가 root `tests/` 가 아니라 여기 있는 이유: 루트 수트는 `tests/` 만 sys.path
에 두고 도는데 거기서 `web/` 를 얹으면 그 뒤로 수트 전체의 모듈 해석이 바뀐다
(CI 에서 test_collect 의 수집원 목록 검사가 깨졌고 로컬에서는 재현되지 않았다).
`build_data` 를 정상적으로 import 하는 자리는 여기다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_data as bd  # noqa: E402


class CardWhyPriorityTests(unittest.TestCase):
    def finalize(self, **row):
        rows = [{"title": "어떤 이슈 제목", "latest_change": "", **row}]
        bd.finalize_card_fields(rows)
        return rows[0]["card_why"]

    def test_why_short_wins_when_present(self):
        self.assertEqual(
            self.finalize(why_short="수출 시 중복 검증 부담이 줄어듦",
                          implication="시사점 문장이다.",
                          why_important="왜 중요한지 긴 문장이다."),
            "수출 시 중복 검증 부담이 줄어듦")

    def test_falls_back_to_implication_then_why_important(self):
        self.assertEqual(
            self.finalize(why_short="", implication="시사점 문장이다.",
                          why_important="왜 중요한지 긴 문장이다."),
            "시사점 문장이다.")
        self.assertEqual(
            self.finalize(why_short="", implication="", why_important="왜 중요한지 긴 문장이다."),
            "왜 중요한지 긴 문장이다.")

    def test_a_title_restating_why_short_loses_to_implication(self):
        """프론트 폴백이었다면 이 필터를 우회했을 자리다."""
        self.assertEqual(
            self.finalize(title="한빛 2호기 가동 정지",
                          why_short="한빛 2호기 가동 정지됨",
                          implication="계속운전 심의 일정에 영향을 줌."),
            "계속운전 심의 일정에 영향을 줌.")

    def test_without_the_new_field_the_answer_is_unchanged(self):
        """구 화면 무회귀 — why_short 가 없는 데이터는 개정 전과 같은 값을 낸다."""
        legacy = self.finalize(implication="시사점 문장이다.", why_important="긴 문장이다.")
        with_field = self.finalize(why_short="", implication="시사점 문장이다.",
                                   why_important="긴 문장이다.")
        self.assertEqual(legacy, with_field)
        self.assertEqual(legacy, "시사점 문장이다.")


class FallbackWithholdTests(unittest.TestCase):
    def test_why_short_is_withheld_on_fallback_articles(self):
        """폴백 기사는 검토받지 않은 해석을 내보내지 않는다."""
        self.assertIn("why_short", bd.FALLBACK_WITHHELD_FIELDS)


if __name__ == "__main__":
    unittest.main()
