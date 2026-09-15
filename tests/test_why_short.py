"""목록 한 줄용 문장 `why_short` — 검사 규칙과 그 값이 화면에 닿는 경로.

왜 이 필드가 생겼나
-------------------
`why_important` 는 150자 이내로 생성되므로 목록 한 줄에 넣으면 중간에 잘린다
(라이브 실측 530건: 채움률 17.7%, 중앙값 87자, 45자 이내는 1.1%). 화면 v3 의
1단은 한 줄짜리 문장을 요구한다.

무엇을 잠그는가
---------------
① 규칙을 어긴 값은 **빈 문자열이 된다.** 기사를 격리하거나 다시 부르지 않는다 —
   격리하면 영문 제목 폴백으로 떨어져 지금보다 나쁘다(implication 게이트와 같은 판단).
② 값이 화면에 닿는 길은 `finalize_card_fields()` **하나뿐**이다. 화면이 제 폴백을
   들면 제목 재진술 필터를 우회한다(build_data.py 의 card_why 주석).
③ 필드가 없는 구버전 응답에서도 아무 일이 없어야 한다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import news_bot  # noqa: E402
import build_data as bd  # noqa: E402


class WhyShortGateTests(unittest.TestCase):
    def setUp(self):
        news_bot.WHY_SHORT_DROPS.clear()

    def keep(self, text, title="어떤 제목", why="근거가 되는 긴 문장이 여기 있다."):
        return news_bot.drop_invalid_why_short(text, title, why)

    def test_a_good_one_liner_survives(self):
        text = "국산 기자재 수출 시 성능검증 중복 부담이 줄어듦"
        self.assertEqual(self.keep(text), text)

    def test_too_long_is_dropped(self):
        text = "국산 기자재를 미국으로 수출할 때 성능검증을 중복으로 받아야 했던 부담이 크게 줄어들게 됨"
        self.assertGreater(len(text), news_bot.WHY_SHORT_MAX_CHARS)
        self.assertEqual(self.keep(text), "")
        self.assertIn("자 초과", news_bot.WHY_SHORT_DROPS[0])

    def test_exactly_at_the_limit_survives(self):
        text = "가" * (news_bot.WHY_SHORT_MAX_CHARS - 1) + "함"
        self.assertEqual(len(text), news_bot.WHY_SHORT_MAX_CHARS)
        self.assertEqual(self.keep(text), text)

    def test_sentence_ending_is_rejected(self):
        """목록 한 줄은 문장이 아니라 라벨에 가깝다 — 문체가 섞이면 줄끼리 어긋난다."""
        self.assertEqual(self.keep("성능검증 중복 부담이 줄어든다"), "")
        self.assertEqual(self.keep("성능검증 중복 부담이 줄어들어요"), "")
        self.assertIn("명사형 종결 아님", news_bot.WHY_SHORT_DROPS[0])

    def test_the_mra_case_is_rejected(self):
        """9/15 MRA 건. 목적 설명은 시사점이 아니다."""
        self.assertEqual(self.keep("국내 성능검증기관의 신뢰도를 높이기 위해서임"), "")
        self.assertIn("금지 어미", news_bot.WHY_SHORT_DROPS[0])

    def test_banned_endings_are_rejected(self):
        for text in ("국내 원전 수출 확대가 기대됨", "정책 방향 전환을 시사함",
                     "추가 논의가 필요함", "시장 확대가 전망됨"):
            news_bot.WHY_SHORT_DROPS.clear()
            self.assertEqual(self.keep(text), "", text)

    def test_restating_the_title_is_rejected(self):
        title = "원자력안전재단, 미국 IEEE와 성능검증 상호인정협약 체결함"
        self.assertEqual(self.keep(title, title=title), "")
        self.assertIn("제목 재진술", news_bot.WHY_SHORT_DROPS[0])

    def test_without_why_important_the_line_is_dropped(self):
        """근거 없는 해석이 화면 맨 앞에 서지 않게 한다."""
        self.assertEqual(self.keep("성능검증 중복 부담이 줄어듦", why=""), "")
        self.assertIn("why_important 없음", news_bot.WHY_SHORT_DROPS[0])

    def test_missing_field_is_not_an_error(self):
        """구버전 응답에는 이 필드가 없다."""
        self.assertEqual(news_bot.drop_invalid_why_short(None, "제목", "근거"), "")
        self.assertEqual(news_bot.drop_invalid_why_short("", "제목", "근거"), "")

    def test_drops_are_recorded_with_a_reason(self):
        self.keep("이것은 문장으로 끝난다")
        self.assertEqual(len(news_bot.WHY_SHORT_DROPS), 1)
        self.assertIn("|", news_bot.WHY_SHORT_DROPS[0])


class CardWhyPriorityTests(unittest.TestCase):
    """화면이 읽는 유일한 '왜 중요해요' 필드를 빌드가 어떻게 고르는가."""

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
