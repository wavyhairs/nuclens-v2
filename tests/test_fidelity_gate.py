"""캡처 하나에서 profile 판정까지 — 그리고 판정이 관대해지지 않는지.

이 게이트가 통과시키는 순간 그 profile 의 live 평가가 열린다. 그러니 통과 조건이
느슨하면 P0 에서 막아 둔 문 뒤로 같은 사고가 그대로 들어온다.
"""

import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import gemini_client
import news_bot
from tools import fidelity_gate, recorded_replay

ARTICLES = [
    {"hash": "a1b2c3d4e5f60718", "title": "신한울 3호기 종합시운전 착수",
     "description": "한수원이 착수했다고 밝혔다.", "link": "https://example.gov.kr/1",
     "domain": "example.gov.kr", "publisher": "산업통상자원부"},
    {"hash": "e5f6a7b8c9d0e1f2", "title": "SMR 표준설계 심의 지연",
     "description": "심의가 미뤄졌다.", "link": "https://news.example.com/2",
     "domain": "news.example.com", "publisher": "에너지신문"},
]


class _Recording:
    def __init__(self, text):
        self.text = text

    def __call__(self, req, *args, **kwargs):
        payload = {"candidates": [{"content": {"parts": [{"text": self.text}]},
                                   "finishReason": "STOP"}],
                   "usageMetadata": {"totalTokenCount": 1}}
        return recorded_replay._RecordedResponse(json.dumps(payload).encode("utf-8"))


class GroupingTests(unittest.TestCase):
    def test_regeneration_attaches_to_the_preceding_chunk(self):
        records = [{"detail": {"task": "curation"}},
                   {"detail": {"task": "curation:재생성"}},
                   {"detail": {"task": "curation"}}]
        groups = fidelity_gate.group_curation(records)
        self.assertEqual([len(g) for g in groups], [2, 1])

    def test_a_leading_regeneration_does_not_crash_or_attach_backwards(self):
        # 캡처가 회차 중간부터 시작할 수 있다(보존 기간 경계).
        groups = fidelity_gate.group_curation([{"detail": {"task": "curation:재생성"}}])
        self.assertEqual([len(g) for g in groups], [1])


class GateTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self._key = gemini_client.API_KEY
        self._capture = gemini_client._CAPTURE_DIR
        gemini_client.API_KEY = "test-key"
        gemini_client.reset_call_log()
        self.tmp = Path(self.enterContext(TemporaryDirectory()))
        self.addCleanup(self._restore)
        self.records = self._record()

    def _restore(self):
        urllib.request.urlopen = self._urlopen
        gemini_client.API_KEY = self._key
        gemini_client._CAPTURE_DIR = self._capture
        gemini_client.reset_call_log()

    def _record(self):
        gemini_client._CAPTURE_DIR = str(self.tmp / "capture")
        urllib.request.urlopen = _Recording('{"items": []}')
        news_bot.curate_batch(ARTICLES, [], log_path=self.tmp / "log.jsonl")
        urllib.request.urlopen = self._urlopen
        gemini_client._CAPTURE_DIR = None
        return recorded_replay.load_capture(self.tmp / "capture" / "llm_capture.jsonl")

    def _curated(self, rows=None):
        path = self.tmp / "curated.json"
        path.write_text(json.dumps(
            {a["hash"]: a for a in (rows if rows is not None else ARTICLES)},
            ensure_ascii=False), encoding="utf-8")
        return path

    def _evaluate(self, curated):
        return fidelity_gate.evaluate(
            self.records, "curation", archive_dir=self.tmp / "missing",
            curated=curated, work_dir=self.tmp)

    def test_a_faithful_capture_is_proven_and_reports_its_own_limits(self):
        row = self._evaluate(self._curated())
        self.assertEqual(row["status"], recorded_replay.PROVEN, row.get("first_failures"))
        self.assertEqual(row["orchestrations"], 1)
        self.assertEqual(row["recorded_calls"], 2)  # 최초 + 재생성
        self.assertEqual(row["unresolved"], [])
        # 판정문이 스스로의 한계를 달고 다녀야 인용될 때 함께 간다.
        self.assertIn("429", row["capture_limits"])
        self.assertEqual(sorted(row["always_prompt_derived"]), ["body", "description"])

    def test_one_bad_orchestration_fails_the_whole_profile(self):
        # "대체로 맞는 replay" 는 없다.
        wrong = [dict(ARTICLES[0], title="오염된 제목"), dict(ARTICLES[1])]
        row = self._evaluate(self._curated(wrong))
        self.assertEqual(row["status"], recorded_replay.NOT_PROVEN)
        self.assertEqual(row["proven"], 0)
        self.assertTrue(row["first_failures"][0]["differences"])

    def test_missing_repo_rows_are_surfaced_not_silently_prompt_filled(self):
        # 저장소에 없으면 프롬프트로 채워져 요청은 맞는다. 그래도 독립 검증이
        # 아니므로 unresolved 로 드러나야 한다.
        empty = self.tmp / "empty.json"
        empty.write_text("{}", encoding="utf-8")
        row = self._evaluate(empty)
        self.assertEqual(row["status"], recorded_replay.PROVEN)
        self.assertEqual(len(row["unresolved"]), 2)
        self.assertEqual(row["articles_with_repo_identity"], 0)

    def test_a_callsite_without_a_reconstructor_is_refused_not_assumed(self):
        row = fidelity_gate.evaluate(
            self.records, "issue_review", archive_dir=self.tmp,
            curated=self._curated(), work_dir=self.tmp)
        self.assertEqual(row["status"], recorded_replay.NOT_PROVEN)

    def test_an_empty_capture_is_not_vacuously_proven(self):
        # 녹화가 없으면 "전부 통과"가 아니라 판정 불가다.
        row = fidelity_gate.evaluate(
            [], "curation", archive_dir=self.tmp, curated=self._curated(),
            work_dir=self.tmp)
        self.assertEqual(row["status"], recorded_replay.NOT_PROVEN)
        self.assertEqual(row["orchestrations"], 0)


if __name__ == "__main__":
    unittest.main()
