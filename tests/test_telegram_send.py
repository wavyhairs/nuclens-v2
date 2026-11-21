"""telegram_send 의 연결 오류 재시도. 네트워크는 쓰지 않는다 — send_text 를 바꿔 끼운다.

다른 테스트가 sys.modules 에 fake telegram_send 를 먼저 올려 두므로(_fake_tg),
`import telegram_send` 로는 진짜 모듈을 못 얻을 수 있다. 파일에서 다른 이름으로 읽는다.
"""

import importlib.util
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location("telegram_send_real", ROOT / "telegram_send.py")
tg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tg)


def unreachable():
    # 2026-11-21 실측 메시지 모양 그대로 — urlopen 이 소켓 오류를 URLError 로 감싼다.
    return urllib.error.URLError(OSError(101, "Network is unreachable"))


class ScriptedSender:
    """청크마다 차례로 결과를 낸다. 예외면 올리고, 아니면 응답으로 돌려준다."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, chunk, **kwargs):
        self.calls.append(chunk)
        outcome = self.outcomes.pop(0) if self.outcomes else {"ok": True}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class NetworkRetryTests(unittest.TestCase):
    def run_send(self, sender, text="본문", **kwargs):
        sleeps = []
        with patch.object(tg, "send_text", sender), \
                patch.object(tg.time, "sleep", sleeps.append):
            result = tg.send_long_text(text, **kwargs)
        return result, sleeps

    def test_a_connection_error_is_retried_once_after_the_wait(self):
        sender = ScriptedSender(unreachable(), {"ok": True})
        result, sleeps = self.run_send(sender, network_retries=1, retry_wait=20)
        self.assertEqual([{"ok": True}], result)
        self.assertEqual(2, len(sender.calls))
        self.assertEqual([20], sleeps)

    def test_a_reset_mid_request_is_also_retried(self):
        """Errno 104 도 다시 보낸다 — 다음 회차 재발송이 이미 같은 위험을 진다."""
        sender = ScriptedSender(ConnectionResetError(104, "Connection reset by peer"),
                                {"ok": True})
        result, _ = self.run_send(sender, network_retries=1)
        self.assertEqual([{"ok": True}], result)
        self.assertEqual(2, len(sender.calls))

    def test_a_server_answer_is_not_retried(self):
        """HTTP 오류는 서버가 답한 것이다. 곧바로 다시 보내도 같은 답이 온다."""
        sender = ScriptedSender(RuntimeError("Telegram API HTTP 400: Bad Request"))
        with self.assertRaises(RuntimeError):
            self.run_send(sender, network_retries=1)
        self.assertEqual(1, len(sender.calls))

    def test_default_keeps_the_old_behaviour(self):
        """주간·채널·연구 발송은 재시도를 청하지 않았다 — 예전처럼 바로 올린다."""
        sender = ScriptedSender(unreachable())
        with self.assertRaises(urllib.error.URLError):
            self.run_send(sender)
        self.assertEqual(1, len(sender.calls))

    def test_gives_up_after_the_allowed_retries(self):
        sender = ScriptedSender(unreachable(), unreachable(), {"ok": True})
        with self.assertRaises(urllib.error.URLError):
            self.run_send(sender, network_retries=1)
        self.assertEqual(2, len(sender.calls))

    def test_only_the_failed_chunk_is_resent(self):
        """긴 브리핑의 앞 청크가 구독자에게 두 번 가면 안 된다."""
        first = "가" * (tg.MAX_LEN - 10)
        second = "나" * 50
        sender = ScriptedSender({"ok": True}, unreachable(), {"ok": True})
        result, _ = self.run_send(sender, text=first + "\n\n" + second, network_retries=1)
        self.assertEqual(3, len(sender.calls))
        self.assertEqual(1, sum(1 for chunk in sender.calls if chunk.startswith("가")))
        self.assertEqual(2, sum(1 for chunk in sender.calls if chunk.startswith("나")))
        self.assertEqual(2, len(result))

    def test_network_error_classification(self):
        self.assertTrue(tg.is_network_error(unreachable()))
        self.assertTrue(tg.is_network_error(ConnectionResetError(104, "reset")))
        self.assertTrue(tg.is_network_error(TimeoutError("timed out")))
        http = urllib.error.HTTPError("https://api.telegram.org", 429, "Too Many", {}, None)
        self.assertFalse(tg.is_network_error(http))
        self.assertFalse(tg.is_network_error(RuntimeError("Telegram API HTTP 403")))


if __name__ == "__main__":
    unittest.main()
