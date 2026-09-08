"""production 요청이 글자 단위로 바뀌지 않았음을 고정한다.

이 fixture 는 P0.5 이음매 리팩터링 **이전** 코드에서 뽑았다. 리팩터링이 중립임을
증명하는 것이 첫 용도이고, 이후로는 reasoning 활성화 커밋이 요청 본문을 함께
바꾸지 않았음을 증명하는 자리로 계속 쓴다.

여기서 검사하는 불변식 두 개가 이 파일의 핵심이다.

- **실제 API 호출 0회.** ``gemini_client._CALL_LOG`` 가 늘지 않아야 한다. 두 모듈이
  ``call_json`` 을 이름으로 복사해 들고 있어서, 주입이 조용히 안 먹으면 replay 라고
  믿는 코드가 진짜 네트워크를 탄다. "코드를 읽어 보니 같다"로는 못 잡는다.
- **repo 파일 변경 0개.** 실패·격리 기록이 예전엔 ``delivery_log.jsonl`` 로 곧장
  갔다. 주입한 경로로만 쓰여야 한다.
"""

import json
import unittest
from pathlib import Path

import dedup
import gemini_client
import news_bot

FIXTURE = Path("tests/fixtures/gemini_reasoning/frozen_requests.json")
REPO_FILES = ("delivery_log.jsonl", "curated.json", "issue_llm_reviews.json")


class Recorder:
    """요청을 기록하고 즉시 실패시킨다 — 응답 경로를 타지 않게."""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user, **kwargs):
        options = {key: value for key, value in kwargs.items() if key != "label"}
        self.calls.append({"system": system, "user": user,
                           "options": {key: options[key] for key in sorted(options)}})
        raise gemini_client.GeminiError("recorded — no network")


class FrozenProductionRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def setUp(self):
        self.calls_before = len(gemini_client._CALL_LOG)
        self.repo_state = {name: self._stat(name) for name in REPO_FILES}
        self._api_key = gemini_client.API_KEY
        gemini_client.API_KEY = "offline-replay"
        self.addCleanup(self._restore)

    def _restore(self):
        gemini_client.API_KEY = self._api_key

    @staticmethod
    def _stat(name: str):
        path = Path(name)
        return path.stat().st_size if path.exists() else None

    def tearDown(self):
        self.assertEqual(len(gemini_client._CALL_LOG), self.calls_before,
                         "offline replay reached the real Gemini client")
        for name in REPO_FILES:
            self.assertEqual(self._stat(name), self.repo_state[name],
                             f"offline replay wrote to {name}")

    def _assert_matches(self, kind: str, recorded: list[dict]):
        self.assertEqual(recorded, self.fixture["captured"][kind])

    def test_curation_request_is_unchanged_and_logs_are_redirected(self):
        recorder = Recorder()
        log_path = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        news_bot.curate_batch(
            self.fixture["articles"], [], bodies=self.fixture["bodies"],
            client=recorder, log_path=log_path / "delivery_log.jsonl")
        self._assert_matches("curation", recorder.calls)
        # 주입한 경로로 실제로 흘렀는지 — 리다이렉트가 형식뿐이면 의미가 없다.
        self.assertTrue((log_path / "delivery_log.jsonl").exists())

    def test_dedup_requests_are_unchanged(self):
        scores = {article["hash"]: 1.0 for article in self.fixture["articles"]}
        for kind, call in (("dedup", dedup.dedup_articles),
                           ("dedup_final", dedup.editorial_dedup_articles)):
            with self.subTest(kind=kind):
                recorder = Recorder()
                kept, dropped = call(list(self.fixture["articles"]), scores,
                                     client=recorder)
                self._assert_matches(kind, recorder.calls)
                # 실패는 fail-open 이다. 이 성질이 바뀌면 dedup 안전 지표의 의미가
                # 통째로 달라지므로 여기서 함께 붙잡아 둔다.
                self.assertEqual(len(kept), len(self.fixture["articles"]))
                self.assertEqual(dropped, [])

    def test_default_client_is_still_the_module_level_binding(self):
        # 이음매를 넣었다고 기본 경로가 달라지면 안 된다.
        self.assertIs(news_bot.gemini_call_json, gemini_client.call_json)
        self.assertIs(dedup.call_json, gemini_client.call_json)


if __name__ == "__main__":
    unittest.main()
