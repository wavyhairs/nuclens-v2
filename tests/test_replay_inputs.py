"""복원한 입력으로 replay 가 성립하는가, 그리고 **무엇이 순환인지 밝히는가**.

입력을 캡처된 프롬프트에서 그대로 뜯어 오면 프롬프트를 다시 만들어도 당연히 같다.
그렇게 얻은 PROVEN 은 아무것도 증명하지 않는다. 그래서 여기서 두 가지를 함께 건다.

- 저장소에서 복원한 필드(title/publisher/domain)는 **정말로 대조된다** — 색인을
  틀리게 만들면 fidelity 가 깨져야 한다.
- 프롬프트에서만 올 수 있는 필드(description/body)는 순환임을 판정에 적어야 한다.
"""

import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import gemini_client
import news_bot
from tools import recorded_replay, replay_inputs

ARTICLES = [
    {"hash": "a1b2c3d4e5f60718", "title": "신한울 3호기 종합시운전 착수",
     "description": "한수원이 신한울 3호기 종합시운전에 착수했다고 밝혔다.",
     "link": "https://example.gov.kr/1", "domain": "example.gov.kr",
     "publisher": "산업통상자원부"},
    {"hash": "e5f6a7b8c9d0e1f2", "title": "SMR 표준설계 심의 지연",
     "description": "규제기관 심의가 다음 분기로 미뤄졌다.",
     "link": "https://news.example.com/2", "domain": "news.example.com",
     "publisher": "에너지신문"},
]
# 줄바꿈이 있는 본문. 블록 경계를 줄 단위로 자르면 여기서 깨진다.
BODIES = {ARTICLES[0]["hash"]: "종합시운전은 상업운전 직전 단계다.\n출력 상승 시험이 포함된다."}


class _Recording:
    def __init__(self, text):
        self.text = text

    def __call__(self, req, *args, **kwargs):
        payload = {"candidates": [{"content": {"parts": [{"text": self.text}]},
                                   "finishReason": "STOP"}],
                   "usageMetadata": {"totalTokenCount": 1}}
        return recorded_replay._RecordedResponse(
            json.dumps(payload).encode("utf-8"))


class PromptParsingTests(unittest.TestCase):
    def _blocks(self, **kwargs):
        _system, user = self._request(**kwargs)
        return replay_inputs.parse_curation_prompt(user)

    @staticmethod
    def _request(**kwargs):
        blocks = []
        for index, article in enumerate(ARTICLES):
            official = " (OFFICIAL)" if news_bot.is_tier1_source(article) else ""
            lines = [f"[{index}|{article['hash'][:8]}]{official} {article['title']}",
                     f"요약: {article['description']}",
                     f"출처: {article['publisher']}"]
            body = kwargs.get("bodies", {}).get(article["hash"])
            if body:
                lines.append(f"본문: {body}")
            if kwargs.get("reports"):
                lines.append("관련보고서: 보고서 제목")
            if kwargs.get("errors"):
                lines.append("이전 출력 오류: response:idx_missing")
            blocks.append("\n".join(lines))
        return "system", "\n\n---\n\n".join(blocks)

    def test_multiline_body_survives_block_parsing(self):
        blocks = self._blocks(bodies=BODIES)
        self.assertEqual(blocks[0]["body"], BODIES[ARTICLES[0]["hash"]])
        self.assertIn("\n", blocks[0]["body"])
        self.assertEqual(blocks[1]["body"], "")

    def test_trailing_single_lines_are_stripped_from_the_end_not_the_body(self):
        blocks = self._blocks(bodies=BODIES, reports=True, errors=True)
        self.assertEqual(blocks[0]["body"], BODIES[ARTICLES[0]["hash"]])
        self.assertEqual(blocks[0]["related_reports"], "보고서 제목")
        self.assertEqual(blocks[0]["error_notes"], "response:idx_missing")

    def test_official_flag_is_read_back(self):
        blocks = self._blocks()
        self.assertEqual([b["official"] for b in blocks],
                         [news_bot.is_tier1_source(a) for a in ARTICLES])

    def test_unreadable_header_is_refused(self):
        with self.assertRaises(ValueError):
            replay_inputs.parse_curation_prompt("헤더가 아닌 줄")


class ReconstructionTests(unittest.TestCase):
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
        news_bot.curate_batch(ARTICLES, [], bodies=BODIES,
                              log_path=self.tmp / "log.jsonl")
        urllib.request.urlopen = self._urlopen
        gemini_client._CAPTURE_DIR = None
        return recorded_replay.load_capture(self.tmp / "capture" / "llm_capture.jsonl")

    def _index(self, rows=None):
        curated = self.tmp / "curated.json"
        curated.write_text(json.dumps(
            {a["hash"]: a for a in (rows if rows is not None else ARTICLES)},
            ensure_ascii=False), encoding="utf-8")
        return replay_inputs.load_article_index(curated=curated)

    def _replay(self, index):
        first = self.records[0]
        built = replay_inputs.reconstruct_curation(first, index)
        driver = recorded_replay.curation_driver(
            built["articles"], [], built["bodies"], self.tmp)
        return built, recorded_replay.verify(self.records, driver)

    def test_reconstructed_inputs_reproduce_the_recorded_requests(self):
        built, report = self._replay(self._index())
        self.assertEqual(report["status"], recorded_replay.PROVEN, report["mismatches"])
        self.assertEqual(built["unresolved_tags"], [])
        self.assertEqual(built["bodies"], BODIES)
        # 재생성까지 재현됐는지 — 한 번짜리 재현은 orchestration 을 증명하지 않는다.
        self.assertEqual(report["replayed_calls"], 2)
        self.assertTrue(replay_inputs.reconstruct_curation(
            self.records[1], self._index())["is_regeneration"])

    def test_a_wrong_repo_title_breaks_fidelity(self):
        # 이것이 순환이 아님을 보이는 자리다. title 은 저장소에서 오므로
        # 색인이 틀리면 프롬프트가 달라지고 판정이 깨져야 한다.
        wrong = [dict(ARTICLES[0], title="저장소에 잘못 들어간 제목"), dict(ARTICLES[1])]
        _built, report = self._replay(self._index(wrong))
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        # 드라이버가 터져서 실패한 것이 아니라 **본문이 달라서** 실패해야 한다.
        self.assertNotIn("driver_error", report["production_result"])
        self.assertIn("contents", " ".join(report["mismatches"][0]["differences"]))

    def test_a_wrong_repo_publisher_flips_the_official_flag(self):
        # publisher/domain 은 (OFFICIAL) 표기를 좌우한다 — 조용히 틀리면 안 된다.
        # 원본은 정부 도메인이라 (OFFICIAL) 이 붙는다. 언론사로 바꾸면 떨어진다.
        self.assertTrue(news_bot.is_tier1_source(ARTICLES[0]))
        wrong = [dict(ARTICLES[0], publisher="에너지신문", domain="news.example.com",
                      link="https://news.example.com/1"), dict(ARTICLES[1])]
        self.assertFalse(news_bot.is_tier1_source(wrong[0]))
        _built, report = self._replay(self._index(wrong))
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        self.assertNotIn("driver_error", report["production_result"])
        differences = " ".join(report["mismatches"][0]["differences"])
        self.assertIn("OFFICIAL", differences)

    def test_colliding_hash_prefixes_are_dropped_rather_than_guessed(self):
        # 아무 쪽이나 고르면 복원이 조용히 틀린다. 못 찾는 편이 낫다.
        twin = dict(ARTICLES[0], hash=ARTICLES[0]["hash"][:8] + "ffffffff",
                    title="같은 머리표식 다른 기사")
        index = self._index([ARTICLES[0], twin, ARTICLES[1]])
        self.assertNotIn(ARTICLES[0]["hash"][:8], index)

    def test_independence_report_never_claims_more_than_it_proves(self):
        built, _report = self._replay(self._index())
        claim = replay_inputs.independence(built)
        self.assertEqual(claim["articles_with_repo_identity"], 2)
        self.assertEqual(sorted(claim["always_prompt_derived"]),
                         ["body", "description"])
        self.assertIn("순환", claim["claim"])

    def test_missing_articles_are_reported_as_prompt_only(self):
        built = replay_inputs.reconstruct_curation(self.records[0], {})
        self.assertEqual(len(built["unresolved_tags"]), 2)
        claim = replay_inputs.independence(built)
        self.assertEqual(claim["articles_with_repo_identity"], 0)
        self.assertEqual(claim["independently_reconstructed_fields"], 0)



DEDUP_ROWS = [
    {"hash": "aaaa1111bbbb2222", "title": "신한울 3호기 종합시운전 착수",
     "title_kr": "신한울 3호기 종합시운전 착수", "publisher": "산업통상자원부",
     "domain": "example.gov.kr", "feed": "gov", "source_tier": 1,
     "scope": "kr", "section": "정책", "features": {"event_type": "operation"},
     "event_date": "2026-09-01", "tags": ["신한울", "시운전"],
     "summary": "종합시운전에 착수했다.", "detail": "출력 상승 시험이 포함된다."},
    {"hash": "cccc3333dddd4444", "title": "SMR 표준설계 심의 지연",
     "title_kr": "SMR 표준설계 심의 지연", "publisher": "에너지신문",
     "domain": "news.example.com", "feed": "rss", "source_tier": 3,
     "scope": "kr", "section": "정책", "features": {"event_type": "regulation"},
     "event_date": "2026-09-02", "tags": ["SMR"],
     "summary": "심의가 미뤄졌다.", "detail": "다음 분기로 이월."},
]


class DedupReconstructionTests(unittest.TestCase):
    """dedup 은 15개 필드 중 13개가 저장소에 남아 복원 근거가 더 강하다."""

    def setUp(self):
        self._urlopen = urllib.request.urlopen
        self._key = gemini_client.API_KEY
        self._capture = gemini_client._CAPTURE_DIR
        gemini_client.API_KEY = "test-key"
        gemini_client.reset_call_log()
        self.tmp = Path(self.enterContext(TemporaryDirectory()))
        self.addCleanup(self._restore)

    def _restore(self):
        urllib.request.urlopen = self._urlopen
        gemini_client.API_KEY = self._key
        gemini_client._CAPTURE_DIR = self._capture
        gemini_client.reset_call_log()

    def _record(self, articles):
        import dedup

        gemini_client._CAPTURE_DIR = str(self.tmp / "capture")
        urllib.request.urlopen = _Recording(json.dumps(
            {"groups": [{"indices": [0, 1], "relation": "merge", "story_title": "s"}]}))
        dedup.dedup_articles(list(articles), {a["hash"]: 1.0 for a in articles})
        urllib.request.urlopen = self._urlopen
        gemini_client._CAPTURE_DIR = None
        return recorded_replay.load_capture(self.tmp / "capture" / "llm_capture.jsonl")

    def _index(self, rows):
        curated = self.tmp / "curated.json"
        curated.write_text(json.dumps({r["hash"]: r for r in rows}, ensure_ascii=False),
                           encoding="utf-8")
        return replay_inputs.load_title_index(curated=curated)

    def _replay(self, records, index):
        built = replay_inputs.reconstruct_dedup(records[0], index)
        driver = recorded_replay.dedup_driver(
            built["articles"], built["scores"], stage=built["stage"])
        return built, recorded_replay.verify(records, driver)

    def test_repo_rows_alone_reproduce_the_recorded_dedup_request(self):
        records = self._record(DEDUP_ROWS)
        built, report = self._replay(records, self._index(DEDUP_ROWS))
        self.assertEqual(report["status"], recorded_replay.PROVEN, report["mismatches"])
        self.assertEqual(built["unresolved_indices"], [])
        self.assertEqual(built["stage"], "dedup")

    def test_runtime_story_fields_round_trip_through_the_prompt(self):
        # story_context/story_fingerprint 는 브리핑 실행 중 계산되어 저장되지 않는다.
        rows = [dict(DEDUP_ROWS[0],
                     story_fingerprint="신한울 3호기 시운전 착수 · 2026-09",
                     story_context=[{"summary": "1차 맥락"}, {"summary": "2차 맥락"}]),
                dict(DEDUP_ROWS[1])]
        records = self._record(rows)
        # 색인에는 저장 가능한 필드만 있다 — 실제 저장소와 같은 조건.
        _built, report = self._replay(records, self._index(DEDUP_ROWS))
        self.assertEqual(report["status"], recorded_replay.PROVEN, report["mismatches"])

    def test_a_wrong_repo_summary_breaks_fidelity(self):
        records = self._record(DEDUP_ROWS)
        wrong = [dict(DEDUP_ROWS[0], summary="저장소에 잘못 들어간 요약"),
                 dict(DEDUP_ROWS[1])]
        _built, report = self._replay(records, self._index(wrong))
        self.assertEqual(report["status"], recorded_replay.NOT_PROVEN)
        self.assertNotIn("driver_error", report["production_result"])
        self.assertIn("contents", " ".join(report["mismatches"][0]["differences"]))

    def test_duplicate_titles_are_dropped_rather_than_guessed(self):
        # dedup 블록에는 hash 가 없어 제목이 유일한 join key 다. 겹치면 못 찾는 편이 낫다.
        twin = dict(DEDUP_ROWS[0], hash="ffff9999ffff9999", summary="다른 기사")
        index = self._index([DEDUP_ROWS[0], twin, DEDUP_ROWS[1]])
        self.assertNotIn(DEDUP_ROWS[0]["title"], index)

    def test_independence_reports_the_dedup_specific_boundary(self):
        records = self._record(DEDUP_ROWS)
        built, _report = self._replay(records, self._index(DEDUP_ROWS))
        claim = replay_inputs.independence(built)
        self.assertEqual(claim["articles_with_repo_identity"], 2)
        self.assertEqual(sorted(claim["always_prompt_derived"]),
                         ["story_context", "story_fingerprint"])

    def test_scores_are_flagged_as_placeholders(self):
        # 점수는 프롬프트에 실리지 않는다. 복원한 척하면 최종 산출물 대조가 거짓이 된다.
        records = self._record(DEDUP_ROWS)
        built, _report = self._replay(records, self._index(DEDUP_ROWS))
        self.assertTrue(built["scores_are_placeholders"])

if __name__ == "__main__":
    unittest.main()
