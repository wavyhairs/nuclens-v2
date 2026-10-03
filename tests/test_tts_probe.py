"""tools/tts_probe.py — 안내문 시험 도구가 운영과 같은 요청 모양을 쓰고 실패를 바로 가르는지."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import audio_brief  # noqa: E402
import tts_probe  # noqa: E402


def _http_error(code, body):
    return audio_brief.urllib.error.HTTPError(
        "https://x", code, "err", {}, io.BytesIO(body.encode("utf-8")))


def _ok_stream():
    payload = {"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": "audio/L16;codec=pcm;rate=24000",
        "data": audio_brief.base64.b64encode(b"\x00\x01" * 50).decode()}}]}}]}
    stream = io.BytesIO(json.dumps(payload).encode("utf-8"))
    stream.__enter__ = lambda self=stream: self
    stream.__exit__ = lambda *a: False
    return stream


class PayloadTests(unittest.TestCase):
    def test_default_payload_is_unchanged(self):
        text = audio_brief.tts_payload("HOST: 안녕하세요")["contents"][0]["parts"][0]["text"]
        self.assertEqual(audio_brief.STYLE_INSTRUCTION + "안녕하세요", text)

    def test_instruction_can_be_swapped_or_dropped(self):
        for instruction in ("읽으세요:\n\n", ""):
            text = audio_brief.tts_payload("HOST: 안녕하세요", instruction=instruction)
            self.assertEqual(instruction + "안녕하세요",
                             text["contents"][0]["parts"][0]["text"])

    def test_current_variant_is_the_production_instruction(self):
        self.assertEqual(audio_brief.STYLE_INSTRUCTION, tts_probe.VARIANTS["current"])


class CallOnceTests(unittest.TestCase):
    def setUp(self):
        self._key = patch.object(tts_probe.gemini_client, "API_KEY", "k")
        self._key.start()
        self.addCleanup(self._key.stop)

    def _run(self, outcome):
        def fake(request, timeout=None):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch.object(tts_probe.urllib.request, "urlopen", fake):
            return tts_probe.call_once("m", "HOST: 안녕하세요", "")

    def test_text_sampling_400_is_named(self):
        body = '{"error":{"message":"Model tried to generate text, but it should only be used for TTS."}}'
        self.assertEqual("text_sampling_400", self._run(_http_error(400, body))["status"])

    def test_other_errors_keep_their_code(self):
        self.assertEqual("http_400", self._run(_http_error(400, "invalid"))["status"])
        self.assertEqual("http_503", self._run(_http_error(503, "busy"))["status"])

    def test_dropped_connection_does_not_stop_the_probe(self):
        res = self._run(audio_brief.http.client.RemoteDisconnected("closed"))
        self.assertEqual("conn_error", res["status"])

    def test_success_returns_pcm_and_rate(self):
        res = self._run(_ok_stream())
        self.assertEqual("ok", res["status"])
        self.assertEqual(24000, res["rate"])
        self.assertTrue(res["pcm"])


class SummaryTests(unittest.TestCase):
    def test_counts_per_variant(self):
        results = [
            {"variant": "a", "status": "ok", "quality": {"truncated": False, "repeat": False,
                                                         "transcript": {"ok": True, "ratio": 1.02}}},
            {"variant": "a", "status": "text_sampling_400"},
            {"variant": "b", "status": "ok", "quality": {"truncated": True, "repeat": False}},
        ]
        table = tts_probe.summarize(results)
        self.assertEqual({"calls": 2, "ok": 1, "clean": 1}, {k: table["a"][k] for k in ("calls", "ok", "clean")})
        self.assertEqual(1, table["a"]["statuses"]["text_sampling_400"])
        self.assertEqual(0, table["b"]["clean"])
        md = tts_probe.render_markdown("m", "src", table)
        self.assertIn("| a | 2 | 1 | 1 | 0 | 1/1 | 1.02~1.02 |", md)


if __name__ == "__main__":
    unittest.main()
