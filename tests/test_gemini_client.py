"""Gemini 호출 계측 — 429 의 범인을 세어서 가린다."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client


class TestSecretResolutionPrecedence(unittest.TestCase):
    """환경변수가 .env 를 이긴다 — 빈 문자열도 '값'이다.

    `os.environ.get(k) or _ENV_FILE.get(k)` 는 "" 이 falsy 라 .env 로 넘어갔다.
    그러면 키를 비워 LLM 을 끄려 해도 묵은 .env 가 되살린다 — 테스트에서는 실제
    TTS 호출로, 프로덕션에서는 의도치 않은 과금으로 나타난다(2026-08-15).
    """

    def test_env_var_wins_over_dotenv(self):
        with patch.dict(gemini_client._ENV_FILE, {"K_X": "from-dotenv"}, clear=False), \
                patch.dict(os.environ, {"K_X": "from-env"}):
            self.assertEqual(gemini_client._resolve("K_X"), "from-env")

    def test_explicitly_empty_env_var_does_not_fall_back_to_dotenv(self):
        with patch.dict(gemini_client._ENV_FILE, {"K_X": "from-dotenv"}, clear=False), \
                patch.dict(os.environ, {"K_X": ""}):
            self.assertIsNone(gemini_client._resolve("K_X"))
            # default 는 여전히 받는다 — '값 없음'과 '기본값 없음'은 다른 질문이다.
            self.assertEqual(gemini_client._resolve("K_X", "fallback"), "fallback")

    def test_dotenv_still_used_when_env_var_is_absent(self):
        with patch.dict(gemini_client._ENV_FILE, {"K_X": "from-dotenv"}, clear=False):
            os.environ.pop("K_X", None)
            self.assertEqual(gemini_client._resolve("K_X"), "from-dotenv")


class TestCallInstrumentation(unittest.TestCase):
    """429 는 분당 20회였는데 그 1분에 누가 몇 번 불렀는지가 로그에 없었다.

    원인을 두 번 잘못 짚은 뒤에 넣은 계측이다 — ①"일일 한도 소진"(틀림, RPM)
    ②"격리 항목 개별 재시도"(틀림, 품질 게이트 재생성은 배치 1회).
    """

    def setUp(self):
        gemini_client.reset_call_log()

    def tearDown(self):
        gemini_client.reset_call_log()

    def test_empty_log_reports_zero(self):
        self.assertEqual(gemini_client.call_stats()["total"], 0)
        self.assertIn("0회", gemini_client.format_call_stats())

    def test_counts_split_by_model_and_label(self):
        gemini_client._record_call("gemini-2.5-flash", "curation")
        gemini_client._record_call("gemini-2.5-flash", "curation:retry")
        gemini_client._record_call("gemini-2.5-flash-lite", "issue_review")
        stats = gemini_client.call_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["per_model"]["gemini-2.5-flash"], 2)
        self.assertEqual(stats["per_model"]["gemini-2.5-flash-lite"], 1)
        self.assertEqual(stats["per_label"]["curation:retry"], 1)

    def test_peak_uses_a_sliding_window_not_clock_minutes(self):
        """'시:분' 경계로 세면 59초와 61초에 걸친 폭주를 못 잡는다.

        한도는 슬라이딩 60초로 걸리므로 여기서도 그렇게 잰다.
        """
        base = 1000.0
        gemini_client._CALL_LOG.extend(
            (base + offset, "m", "x") for offset in (0.0, 30.0, 59.0, 61.0, 200.0))
        peak = gemini_client.call_stats()["peak_per_minute"]["m"]
        # 0·30·59 가 한 창에 들어간다. 61 은 0 에서 60 초를 넘겨 빠진다.
        self.assertEqual(peak, 3)

    def test_counter_is_bounded(self):
        for _ in range(gemini_client.CALL_LOG_LIMIT + 50):
            gemini_client._record_call("m", "x")
        self.assertEqual(len(gemini_client._CALL_LOG), gemini_client.CALL_LOG_LIMIT)

    def test_retry_is_counted_as_its_own_call(self):
        """재시도도 한도를 깎는다. attempt 를 안 세면 'chunk 4회'가 거짓말이 된다."""
        import inspect
        source = inspect.getsource(gemini_client.call_json)
        self.assertIn("_record_call", source)
        self.assertIn("retry", source)


class TestQuotaVerdictIsLogged(unittest.TestCase):
    """429 는 두 갈래고, 어느 쪽으로 갈렸는지가 로그에 남아야 한다.

    2026-08-12 오디오 브리핑이 19초 만에 죽었다. 재시도가 한 번이라도 돌았으면
    (최소 20초 대기) 불가능한 시간이라 '일일 한도로 판정해 즉시 포기' 말고는
    설명이 없는데, 로그의 429 본문이 [:600] 으로 잘려 quotaId 가 안 보여
    확증을 못 했다. 같은 `limit: 20` 메시지가 크롤에선 재시도로 풀린다 —
    본문 안의 표지가 유일한 판별자인데 그걸 안 찍고 있었다.
    """

    def test_daily_markers_are_detected_and_named(self):
        body = '{"error":{"details":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel"}]}}'
        self.assertTrue(gemini_client._is_daily_quota(body))
        self.assertEqual(gemini_client._daily_quota_marker(body), "PerDay")

    def test_rpm_body_is_not_daily_and_names_no_marker(self):
        """실제 429 본문(2026-08-12). 표지가 없으면 분당으로 보고 재시도한다."""
        body = ('{"error":{"code":429,"message":"Quota exceeded for metric: '
                'generativelanguage.googleapis.com/generate_content_free_tier_requests, '
                'limit: 20, model: gemini-2.5-flash\\nPlease retry in 48.943978808s.",'
                '"status":"RESOURCE_EXHAUSTED"}}')
        self.assertFalse(gemini_client._is_daily_quota(body))
        self.assertEqual(gemini_client._daily_quota_marker(body), "없음")
        # 이 본문이면 반드시 잔다 — 즉시 포기는 여기서 나올 수 없다.
        self.assertGreaterEqual(gemini_client._retry_delay_seconds(body), 20)

    def test_both_429_branches_print_their_verdict(self):
        """어느 갈래로 갔는지 로그로 남지 않으면 다음 사고도 로그 고고학이 된다."""
        import inspect
        source = inspect.getsource(gemini_client.call_json)
        self.assertIn("일일 한도 판정", source)
        self.assertIn("분당 한도", source)
