"""passive capture 가 production 에 아무 영향도 주지 않음을 고정한다.

capture 는 reasoning 검증에만 쓰는 곁가지다. 그런데 곁가지가 hot path 에 있으면
그 자체가 production 동작이 된다 — 이 훅은 모든 LLM 호출을 지나므로, 여기서
예외가 나면 크롤·브리핑·오디오가 통째로 선다.

그래서 세 가지를 테스트로 못 박는다.

1. 환경변수가 없으면 아무 일도 하지 않는다 (파일도, 예외도 없다).
2. capture 가 실패해도 호출은 정상으로 끝난다.
3. API 키가 기록에 실리면 그 줄을 통째로 버린다.
"""

import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import gemini_client

PAYLOAD = {
    "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]},
                    "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3,
                      "thoughtsTokenCount": 0, "totalTokenCount": 8},
}


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CaptureSafetyTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self._key = gemini_client.API_KEY
        self._dir = gemini_client._CAPTURE_DIR
        urllib.request.urlopen = lambda *a, **k: _Response(PAYLOAD)
        gemini_client.API_KEY = "test-key-not-real"
        gemini_client.reset_call_log()
        self.addCleanup(self._restore)

    def _restore(self):
        urllib.request.urlopen = self._urlopen
        gemini_client.API_KEY = self._key
        gemini_client._CAPTURE_DIR = self._dir
        gemini_client.reset_call_log()

    def _call(self):
        return gemini_client.call_json("s", "u", retries=0, label="test")

    def test_disabled_capture_writes_nothing_and_stays_out_of_the_way(self):
        with TemporaryDirectory() as tmp:
            gemini_client._CAPTURE_DIR = None
            self.assertEqual(self._call(), {"ok": True})
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_enabled_capture_records_the_serialized_request_and_response(self):
        with TemporaryDirectory() as tmp:
            gemini_client._CAPTURE_DIR = tmp
            self.assertEqual(self._call(), {"ok": True})
            lines = (Path(tmp) / "llm_capture.jsonl").read_text(
                encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            # 요청 본문 그대로여야 replay 가 성립한다 — 요약본으로는 못 한다.
            self.assertEqual(record["request_body"]["generationConfig"]
                             ["responseMimeType"], "application/json")
            self.assertEqual(record["response"], PAYLOAD)
            self.assertEqual(record["detail"]["requested_thinking"], "unspecified")

    def test_capture_failure_never_fails_the_production_call(self):
        # 쓸 수 없는 경로. 훅이 예외를 흘리면 여기서 호출이 죽는다.
        gemini_client._CAPTURE_DIR = "\0invalid"
        self.assertEqual(self._call(), {"ok": True})

    def test_a_record_carrying_the_api_key_is_dropped_whole(self):
        with TemporaryDirectory() as tmp:
            gemini_client._CAPTURE_DIR = tmp
            # 키가 본문에 실리는 상황을 강제로 만든다.
            gemini_client.API_KEY = "s"
            self.assertEqual(self._call(), {"ok": True})
            self.assertFalse((Path(tmp) / "llm_capture.jsonl").exists())

    def test_capture_is_off_by_default_in_this_repo(self):
        # 기본값이 켜져 있으면 모든 실행이 프롬프트를 디스크에 남긴다.
        self.assertIsNone(self._dir)


if __name__ == "__main__":
    unittest.main()
