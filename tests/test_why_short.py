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

import unittest

import news_bot

# `build_data` 는 여기서 import 하지 않는다. 루트 수트는 `tests/` 만 sys.path 에
# 두고 도는데, 여기서 `web/` 를 얹으면 그 뒤로 **수트 전체**의 모듈 해석이 바뀐다
# (실측: CI 에서 test_collect 의 수집원 목록 검사가 깨졌다. 로컬에서는 재현되지
# 않아 더 나쁘다). card_why 우선순위 검사는 build_data 의 것이므로 그 모듈을
# 정상적으로 import 하는 web/tests/ 로 옮겼다 — web/tests/test_why_short_card.py.


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


if __name__ == "__main__":
    unittest.main()
