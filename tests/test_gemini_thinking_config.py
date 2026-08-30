"""thinkingConfig 400 왕복 제거 — 지원 안 하는 모델엔 첫 요청부터 필드를 뺀다.

실측(2026-08-16): gemini-3.5-flash-lite 는 thinkingBudget=0 을 주면 400
INVALID_ARGUMENT 를 던진다. gemini-3.1-flash-lite 는 같은 값에 200 이다. 예전
구조는 매 호출마다 3.5 로 한 번 맞고 필드를 벗어 재시도했다 — 불필요한 API
왕복과 그만큼의 RPM 소모다. 이 테스트는 ① 알려진 미지원 모델은 첫 요청부터
필드가 없는지, ② 지원 모델은 기존대로 필드가 실리는지, ③ 목록에 없는 새
모델이 같은 증상을 내도 400 fallback 이 여전히 잡아주는지를 확인한다.
"""
import json
import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client as gc

SUCCESS_BODY = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

INVALID_ARGUMENT_BODY = (
    '{"error":{"code":400,"message":"Request contains an invalid argument.",'
    '"status":"INVALID_ARGUMENT"}}'
)


class _FakeResponse:
    """urllib.request.urlopen 이 반환하는 컨텍스트 매니저 흉내."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _capturing_success_urlopen(captured: list):
    """요청 본문을 captured 에 쌓고 성공 응답을 준다."""

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(SUCCESS_BODY)

    return fake_urlopen


class TestKnownUnsupportedModelOmitsThinkingConfigUpfront(unittest.TestCase):
    """gemini-3.5-flash-lite — 첫 요청부터 thinkingConfig 가 없어야 한다."""

    def test_first_request_body_has_no_thinking_config(self):
        captured: list = []
        with patch.object(gc, "API_KEY", "test-key"), \
                patch.object(gc.urllib.request, "urlopen",
                              _capturing_success_urlopen(captured)):
            result = gc.call_json("system", "user", thinking_budget=0,
                                   model="gemini-3.5-flash-lite")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(captured), 1,
                          "400→재거→재시도 왕복 없이 단 한 번의 요청으로 끝나야 한다")
        self.assertNotIn("thinkingConfig", captured[0]["generationConfig"])

    def test_no_400_retry_is_recorded_in_call_log(self):
        """계측(_CALL_LOG)에도 :retry 라벨이 남지 않아야 한다 — RPM 을 안 태웠다는 증거."""
        gc.reset_call_log()
        captured: list = []
        with patch.object(gc, "API_KEY", "test-key"), \
                patch.object(gc.urllib.request, "urlopen",
                              _capturing_success_urlopen(captured)):
            gc.call_json("system", "user", thinking_budget=0,
                         model="gemini-3.5-flash-lite", label="probe")
        labels = [label for _stamp, _model, label in gc._CALL_LOG]
        self.assertEqual(labels, ["probe"])
        gc.reset_call_log()


class TestSupportedModelKeepsThinkingConfig(unittest.TestCase):
    """gemini-3.1-flash-lite 등 지원 모델의 기존 동작은 바뀌면 안 된다."""

    def test_thinking_config_is_still_sent(self):
        captured: list = []
        with patch.object(gc, "API_KEY", "test-key"), \
                patch.object(gc.urllib.request, "urlopen",
                              _capturing_success_urlopen(captured)):
            gc.call_json("system", "user", thinking_budget=0,
                         model="gemini-3.1-flash-lite")
        self.assertEqual(captured[0]["generationConfig"]["thinkingConfig"],
                         {"thinkingBudget": 0})

    def test_default_model_without_thinking_budget_is_unaffected(self):
        """thinking_budget 을 아예 안 주는 기존 호출자는 여전히 필드가 없다."""
        captured: list = []
        with patch.object(gc, "API_KEY", "test-key"), \
                patch.object(gc.urllib.request, "urlopen",
                              _capturing_success_urlopen(captured)):
            gc.call_json("system", "user")
        self.assertNotIn("thinkingConfig", captured[0]["generationConfig"])


class TestUnknownModelStillFallsBackOn400(unittest.TestCase):
    """목록에 없는 새 모델이 같은 증상을 내면 기존 400 fallback 이 안전망이 된다.

    이 목록은 "알려진 모델의 첫 요청 최적화" 캐시일 뿐이라, 처음 보는 모델은
    여전히 한 번은 400 을 맞고 필드를 벗어 재시도한다 — 그 자체는 유지돼야
    한다(향후 호환성).
    """

    def test_first_400_then_retry_without_thinking_config_succeeds(self):
        calls: list = []

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body)
            if "thinkingConfig" in body["generationConfig"]:
                raise urllib.error.HTTPError(
                    "u", 400, "Bad Request", {},
                    BytesIO(INVALID_ARGUMENT_BODY.encode("utf-8")))
            return _FakeResponse(SUCCESS_BODY)

        with patch.object(gc, "API_KEY", "test-key"), \
                patch.object(gc.urllib.request, "urlopen", fake_urlopen):
            result = gc.call_json("system", "user", thinking_budget=0,
                                   model="gemini-9.9-future-lite")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2,
                          "미지 모델은 첫 400 이후 한 번 더(필드를 벗고) 시도해야 한다")
        self.assertIn("thinkingConfig", calls[0]["generationConfig"])
        self.assertNotIn("thinkingConfig", calls[1]["generationConfig"])

    def test_unrelated_400_is_not_treated_as_thinking_rejection(self):
        """thinkingConfig 와 무관한 400 까지 재시도로 태우면 안 된다."""
        calls: list = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(
                "u", 400, "Bad Request", {},
                BytesIO(b'{"error":{"message":"unrelated bad request"}}'))

        with patch.object(gc, "API_KEY", "test-key"), \
                patch.object(gc.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(gc.GeminiError):
                gc.call_json("system", "user", thinking_budget=0,
                             model="gemini-9.9-future-lite")
        self.assertEqual(len(calls), 1, "무관한 400 은 재시도하지 않고 바로 올라가야 한다")


if __name__ == "__main__":
    unittest.main()
