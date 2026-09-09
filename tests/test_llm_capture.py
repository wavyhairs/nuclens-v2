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


class CaptureWiringTests(unittest.TestCase):
    """평가 대상 callsite 가 실제로 capture 되도록 배선돼 있는가.

    배선이 조용히 빠지면 capture 를 켜 뒀다고 믿으면서 며칠을 흘려보내게 된다.
    그때 잃는 것은 코드가 아니라 **다시 만들 수 없는 자연 production 데이터**다.

    (PyYAML 은 런타임 의존성이 아니므로 문자열로 확인한다.)
    """

    ENV = "NUCLENS_LLM_CAPTURE_DIR:"
    SCOPE = {
        ".github/workflows/crawl.yml": ("curation / issue_review / keei_match",),
        ".github/workflows/daily-brief.yml": (
            "curation", "dedup / dedup_final (plan)", "expert_verify (audio)"),
    }

    def test_every_scope_workflow_step_passes_the_capture_variable(self):
        for path, callsites in self.SCOPE.items():
            with self.subTest(workflow=path):
                text = Path(path).read_text(encoding="utf-8")
                self.assertEqual(text.count(self.ENV), len(callsites),
                                 f"capture wiring drifted for {callsites}")

    def test_capture_is_opt_in_and_uploads_with_bounded_retention(self):
        for path in self.SCOPE:
            with self.subTest(workflow=path):
                text = Path(path).read_text(encoding="utf-8")
                # repo variable 로만 켜진다 — 기본값은 빈 문자열 = 꺼짐.
                self.assertIn("vars.NUCLENS_LLM_CAPTURE == 'on'", text)
                self.assertIn("retention-days: 14", text)
                # 업로드 실패가 파이프라인을 실패시키지 않아야 한다.
                self.assertIn("continue-on-error: true", text)

    def test_capture_path_is_ignored_by_git(self):
        # 봇 상태 커밋에 프롬프트가 딸려 들어가면 되돌릴 수 없다.
        self.assertIn(".eval/", Path(".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
