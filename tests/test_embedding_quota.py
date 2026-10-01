"""임베딩 공통 속도 제어·429 분류·호출 통계 회귀 검사.

실제 API 는 부르지 않는다. 가상 시계(``FakeClock``)가 ``sleep`` 을 받아 시간을
앞으로 돌리고, 가짜 클라이언트가 요청마다 시각을 남겨 '최근 60초 요청 수'를
그 자리에서 검사한다. 2026-10-01 수집 #526(429 188건)·#527(32건)과 AI Studio
RPM 106/100 이 출발점이다.
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import embedding_pipeline as ep
import embedding_quota as eq
import news_bot as nb

HAS_SDK = importlib.util.find_spec("google.genai") is not None

MODEL = ep.EMBEDDING_MODEL
SECRET_KEY = "AIzaSy-FAKE-KEY-must-not-leak"
ARTICLE_BODY = "기사 본문 문장 — 로그에 나오면 안 된다"


class FakeClock:
    def __init__(self, start: float = 1_790_000_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAPIError(Exception):
    """google.genai.errors.APIError 와 같은 모양(code·status·details·response)."""

    def __init__(self, code: int, body: dict, headers: dict | None = None):
        self.code = code
        self.details = body
        self.status = (body.get("error") or {}).get("status")
        self.response = SimpleNamespace(headers=headers or {}, status_code=code)
        super().__init__(f"{code} {self.status}. {body}")


def quota_429(quota_id: str = "", *, metric: str = "", value: str = "100",
              retry_delay: str | None = None, message: str = "",
              headers: dict | None = None) -> FakeAPIError:
    details: list[dict] = []
    if quota_id or metric:
        violation = {"quotaMetric": metric, "quotaValue": value,
                     "quotaDimensions": {"model": MODEL, "location": "global"}}
        if quota_id:
            violation["quotaId"] = quota_id
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [violation]})
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": retry_delay})
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": message or "You exceeded your current quota.",
                      "details": details}}
    return FakeAPIError(429, body, headers)


def rpm_429(delay: str | None = "41s") -> FakeAPIError:
    return quota_429("EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier",
                     metric="generativelanguage.googleapis.com/embed_content_free_tier_requests",
                     retry_delay=delay)


def rpd_429() -> FakeAPIError:
    return quota_429("EmbedContentRequestsPerDayPerProjectPerModel-FreeTier",
                     metric="generativelanguage.googleapis.com/embed_content_free_tier_requests",
                     value="1000", retry_delay="3s")


def tpm_429() -> FakeAPIError:
    return quota_429("EmbedContentInputTokensPerMinutePerProjectPerModel-FreeTier",
                     metric="generativelanguage.googleapis.com/embed_content_free_tier_input_token_count",
                     value="30000", retry_delay="12s")


def unknown_429() -> FakeAPIError:
    return quota_429(message=f"Resource has been exhausted. {SECRET_KEY} {ARTICLE_BODY}")


def vector() -> list[float]:
    values = [0.0] * ep.EMBEDDING_DIMENSION
    values[0] = 1.0
    return values


class FakeClient:
    """요청마다 시각을 남기고, 그 순간 최근 60초 요청 수가 상한 이하인지 본다."""

    def __init__(self, clock: FakeClock, script=None, *, latency: float = 0.2,
                 cap: int = eq.RPM_CAP_DEFAULT, shared: list[float] | None = None):
        self.clock = clock
        self.script = list(script or [])
        self.latency = latency
        self.cap = cap
        self.calls: list[float] = shared if shared is not None else []
        self.max_in_window = 0
        self.models = self

    def embed_content(self, *, model, contents, config):
        now = self.clock()
        self.calls.append(now)
        in_window = sum(1 for stamp in self.calls if now - stamp < eq.WINDOW_SECONDS)
        self.max_in_window = max(self.max_in_window, in_window)
        self.clock.now += self.latency
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        return SimpleNamespace(embeddings=[SimpleNamespace(values=vector())])


def make_limiter(clock: FakeClock, logs: list[str] | None = None, **overrides):
    settings = {"model": MODEL, "ledger_path": None, "clock": clock,
                "sleep": clock.sleep, "log": (logs.append if logs is not None else lambda _m: None)}
    settings.update(overrides)
    return eq.EmbeddingLimiter(**settings)


def article(index: int, **extra) -> dict:
    return {"hash": f"h{index:04d}", "title_kr": f"원전 기사 {index}",
            "summary": f"요약 {index}", "score": 100 - index, **extra}


def current_entry(art: dict) -> dict:
    return {"vec": vector(), "model": MODEL, "dimension": ep.EMBEDDING_DIMENSION,
            "text_fingerprint": ep.text_fingerprint(ep.embedding_text(art)),
            "cached_at": datetime.now(timezone.utc).isoformat()}


class LimiterTestCase(unittest.TestCase):
    def tearDown(self):
        eq.set_limiter(None)


# ── ① 최근 60초 상한 ───────────────────────────────────────────────────────


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class RollingWindowCapTests(LimiterTestCase):
    def test_never_exceeds_cap_in_any_60_second_window(self):
        clock = FakeClock()
        gate = make_limiter(clock, max_wait_seconds=10_000)
        client = FakeClient(clock)
        for index in range(250):
            ep.embed_one(client, f"text {index}", gate=gate)
        self.assertEqual(len(client.calls), 250)
        self.assertLessEqual(client.max_in_window, 80)
        self.assertEqual(gate.peak_per_minute(), 80)
        self.assertGreater(gate.wait_pacing, 0)
        stats = gate.stats()
        self.assertEqual((stats["attempts"], stats["successes"], stats["failures"]), (250, 250, 0))

    def test_cap_counts_retries_too(self):
        clock = FakeClock()
        gate = make_limiter(clock, rpm_cap=5, max_wait_seconds=10_000)
        client = FakeClient(clock, script=[None, None, None, None, rpm_429("1s"), None, None])
        for index in range(6):
            ep.embed_one(client, f"text {index}", gate=gate)
        self.assertEqual(gate.attempts, 7)          # 재시도 1회 포함
        self.assertLessEqual(client.max_in_window, 5)

    def test_cap_and_budgets_come_from_env(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            for name in ("GEMINI_EMBEDDING_RPM_CAP", "GEMINI_EMBEDDING_MAX_WAIT_SECONDS",
                         "GEMINI_EMBEDDING_MAX_RETRIES", "GEMINI_EMBEDDING_MAX_TOTAL_RETRIES"):
                env.pop(name, None)
            default = eq.EmbeddingLimiter.from_env(MODEL, ledger_path=None)
            self.assertEqual(default.rpm_cap, 80)
            self.assertEqual(default.max_retries, 2)
            self.assertEqual(default.max_total_retries, 6)
            self.assertEqual(default.max_wait_seconds, 360.0)
            env.update({"GEMINI_EMBEDDING_RPM_CAP": "50",
                        "GEMINI_EMBEDDING_MAX_WAIT_SECONDS": "120"})
            tuned = eq.EmbeddingLimiter.from_env(MODEL, ledger_path=None)
            self.assertEqual((tuned.rpm_cap, tuned.max_wait_seconds), (50, 120.0))
            env["GEMINI_EMBEDDING_RPM_CAP"] = "eighty"
            self.assertEqual(eq.EmbeddingLimiter.from_env(MODEL, ledger_path=None).rpm_cap, 80)
            env["GEMINI_EMBEDDING_RPM_CAP"] = "0"
            self.assertEqual(eq.EmbeddingLimiter.from_env(MODEL, ledger_path=None).rpm_cap, 80)


# ── ② 캐시 재사용은 호출·대기 0 ────────────────────────────────────────────


class CacheReuseTests(LimiterTestCase):
    def test_cached_vector_costs_no_request_and_no_wait(self):
        clock = FakeClock()
        gate = make_limiter(clock, rpm_cap=1)
        client = FakeClient(clock)
        articles = [article(i) for i in range(50)]
        cache = {a["hash"]: current_entry(a) for a in articles}
        for art in articles:
            vec, created = ep.get_or_compute_embedding(client, art, art["hash"], cache, gate=gate)
            self.assertEqual(vec, vector())
            self.assertFalse(created)
        self.assertEqual(client.calls, [])
        self.assertEqual(clock.sleeps, [])
        self.assertEqual((gate.attempts, gate.cache_hits), (0, 50))

    def test_semantic_dedup_on_warm_cache_never_sleeps(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        eq.set_limiter(gate)
        client = FakeClient(clock)
        articles = [article(i) for i in range(30)]
        cache = {a["hash"]: current_entry(a) for a in articles}
        with patch.object(nb, "get_gemini", return_value=client), \
                patch.object(nb.time, "sleep", side_effect=AssertionError("고정 대기 금지")):
            nb.semantic_dedup(articles, cache)
        self.assertEqual(client.calls, [])
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(gate.cache_hits, 30)


# ── ③ 분당 429 → 대기 후 복구 ─────────────────────────────────────────────


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class PerMinuteRecoveryTests(LimiterTestCase):
    def test_rpm_429_waits_server_delay_then_same_request_succeeds(self):
        clock = FakeClock()
        logs: list[str] = []
        gate = make_limiter(clock, logs)
        client = FakeClient(clock, script=[rpm_429("41s")])
        cache: dict = {}
        art = article(1)
        vec, created = ep.get_or_compute_embedding(client, art, art["hash"], cache, gate=gate)
        self.assertTrue(created)
        self.assertEqual(vec, vector())
        self.assertIn(art["hash"], cache)                 # 정상 벡터는 저장된다
        self.assertEqual(clock.sleeps, [42.0])            # 41s + 1s 여유
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(gate.suspended, "")
        stats = gate.stats()
        self.assertEqual((stats["retries"], stats["recovered"]), (1, 1))
        self.assertEqual(stats["failures_by_kind"], {"RPM": 1})
        self.assertEqual(stats["events"][0]["outcome"], "recovered")
        self.assertEqual(stats["events"][0]["waited_s"], 42.0)
        self.assertTrue(any("재시도 복구" in line for line in logs))

    def test_tpm_429_is_per_minute_too(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        client = FakeClient(clock, script=[tpm_429()])
        ep.embed_one(client, "text", gate=gate)
        self.assertEqual(gate.failures_by_kind, {"TPM": 1})
        self.assertEqual(clock.sleeps, [13.0])

    def test_retry_without_server_hint_waits_one_window(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        client = FakeClient(clock, script=[rpm_429(delay=None)])
        ep.embed_one(client, "text", gate=gate)
        self.assertEqual(clock.sleeps, [eq.WINDOW_SECONDS])

    def test_retry_after_header_is_honoured(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        error = rpm_429(delay=None)
        error.response.headers = {"Retry-After": "7"}
        client = FakeClient(clock, script=[error])
        ep.embed_one(client, "text", gate=gate)
        self.assertEqual(clock.sleeps, [8.0])


# ── ④ 일일 429 → 이후 신규 호출 중단 ──────────────────────────────────────


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class DailyQuotaStopTests(LimiterTestCase):
    def test_rpd_stops_new_calls_but_cache_keeps_working(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        eq.set_limiter(gate)
        client = FakeClient(clock, script=[rpd_429()])
        fresh = [article(i) for i in range(5)]
        cached = [article(100 + i) for i in range(3)]
        cache = {a["hash"]: current_entry(a) for a in cached}
        with patch.object(nb, "get_gemini", return_value=client):
            vectors = [nb.get_or_compute_embedding(a, a["hash"], cache) for a in fresh + cached]
        self.assertEqual(len(client.calls), 1)                 # 첫 429 뒤로는 안 부른다
        self.assertEqual(clock.sleeps, [])                     # 일일 한도는 기다리지 않는다
        self.assertEqual(vectors[:5], [None] * 5)
        self.assertEqual(vectors[5:], [vector()] * 3)          # 캐시는 그대로 쓴다
        self.assertEqual(gate.suspended, "RPD")
        self.assertEqual(gate.skipped_after_stop, 4)
        self.assertEqual(gate.cache_hits, 3)
        self.assertEqual(gate.events[0]["outcome"], "stopped:RPD")

    def test_rpd_carries_to_the_next_process_via_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            clock = FakeClock()
            first = make_limiter(clock, ledger_path=ledger)
            with self.assertRaises(eq.EmbeddingQuotaError):
                ep.embed_one(FakeClient(clock, script=[rpd_429()]), "text", gate=first)
            clock.now += 600   # 다음 스텝(백필)이 10분 뒤 시작
            second = make_limiter(clock, ledger_path=ledger)
            client = FakeClient(clock)
            with self.assertRaises(eq.EmbeddingCallsSuspended):
                ep.embed_one(client, "text", gate=second)
            self.assertEqual(client.calls, [])
            self.assertEqual(second.suspended, "RPD")
            state = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertGreater(state["blocked_until"], clock.now)

    def test_refresh_stops_after_rpd_and_keeps_earlier_vectors(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        articles = [article(i) for i in range(6)]
        client = FakeClient(clock, script=[None, None, rpd_429()])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "embeddings.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                stats = ep.refresh_embeddings(articles, {}, client, cache_path=path, gate=gate)
            self.assertEqual(len(client.calls), 3)
            self.assertEqual((stats["generated"], stats["failed"], stats["stopped"]), (2, 1, "RPD"))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(saved), {"h0000", "h0001"})
            self.assertEqual(saved["h0000"]["model"], MODEL)
            self.assertEqual(saved["h0000"]["dimension"], ep.EMBEDDING_DIMENSION)

            # 다음 실행: 저장된 벡터는 재사용(요청 0), 나머지만 새로 만든다.
            later = make_limiter(clock)
            client2 = FakeClient(clock)
            with redirect_stdout(io.StringIO()):
                stats2 = ep.refresh_embeddings(articles, ep.load_cache(path), client2,
                                               cache_path=path, gate=later)
            self.assertEqual(len(client2.calls), 4)
            self.assertEqual((later.cache_hits, stats2["current"]), (2, 6))


# ── ⑤ 유형 불명 429 · 반복 실패 · 예산 ────────────────────────────────────


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class BoundedRetryTests(LimiterTestCase):
    def test_unknown_429_is_not_called_daily_and_retries_once(self):
        clock = FakeClock()
        logs: list[str] = []
        gate = make_limiter(clock, logs)
        client = FakeClient(clock, script=[unknown_429(), unknown_429()])
        with self.assertRaises(eq.EmbeddingQuotaError) as caught:
            ep.embed_one(client, "text", gate=gate)
        self.assertEqual(caught.exception.info.kind, "UNKNOWN")
        self.assertEqual(len(client.calls), 2)               # 원 요청 + 재시도 1
        self.assertEqual(gate.suspended, "UNKNOWN")
        self.assertEqual(gate.failures_by_kind, {"UNKNOWN": 2})
        self.assertTrue(any("429 UNKNOWN" in line for line in logs))
        self.assertFalse(any("RPD" in line for line in logs))
        with self.assertRaises(eq.EmbeddingCallsSuspended):  # 다음 기사는 안 부른다
            ep.embed_one(client, "next", gate=gate)
        self.assertEqual(len(client.calls), 2)

    def test_persistent_rpm_429_is_bounded_per_request_and_stops_the_run(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        client = FakeClient(clock, script=[rpm_429("41s")] * 20)
        articles = [article(i) for i in range(10)]
        with patch.object(nb, "get_gemini", return_value=client):
            eq.set_limiter(gate)
            results = [nb.get_or_compute_embedding(a, a["hash"], {}) for a in articles]
        self.assertEqual(results, [None] * 10)
        self.assertEqual(len(client.calls), 3)                # 1 + 재시도 2, 그 뒤 중단
        self.assertEqual(clock.sleeps, [42.0, 42.0])
        self.assertEqual(gate.suspended, "RPM")
        self.assertEqual(gate.events[0]["outcome"], "stopped:RPM")
        self.assertEqual(gate.events[0]["retries"], 2)
        self.assertEqual(gate.skipped_after_stop, 9)

    def test_total_retry_budget(self):
        clock = FakeClock()
        gate = make_limiter(clock, max_total_retries=1)
        client = FakeClient(clock, script=[rpm_429("5s"), None, rpm_429("5s")])
        ep.embed_one(client, "a", gate=gate)
        with self.assertRaises(eq.EmbeddingQuotaError):
            ep.embed_one(client, "b", gate=gate)
        self.assertEqual(gate.retries, 1)
        self.assertEqual(gate.suspended, "RETRY_BUDGET")
        self.assertEqual(len(client.calls), 3)

    def test_total_wait_budget_covers_429_waits(self):
        clock = FakeClock()
        gate = make_limiter(clock, max_wait_seconds=30)
        client = FakeClient(clock, script=[rpm_429("41s")])
        with self.assertRaises(eq.EmbeddingQuotaError):
            ep.embed_one(client, "a", gate=gate)
        self.assertEqual(clock.sleeps, [])                    # 예산을 넘기는 잠은 안 잔다
        self.assertEqual(gate.suspended, "WAIT_BUDGET")

    def test_total_wait_budget_covers_pacing(self):
        clock = FakeClock()
        gate = make_limiter(clock, rpm_cap=10, max_wait_seconds=100)
        client = FakeClient(clock, latency=0.0)
        made = 0
        with self.assertRaises(eq.EmbeddingCallsSuspended):
            for index in range(1000):
                ep.embed_one(client, f"t{index}", gate=gate)
                made += 1
        self.assertEqual(gate.suspended, "WAIT_BUDGET")
        self.assertLessEqual(sum(clock.sleeps), 100)
        self.assertEqual(made, 20)                            # 첫 창 10 + 대기 60초 뒤 10

    def test_worst_case_wall_time_is_bounded(self):
        """매 요청이 429 여도 회차 전체 대기는 예산을 못 넘는다."""
        clock = FakeClock()
        gate = make_limiter(clock, max_retries=5, max_total_retries=50)
        client = FakeClient(clock, script=[rpm_429("75s")] * 500)
        for index in range(100):
            try:
                ep.embed_one(client, f"t{index}", gate=gate)
            except (eq.EmbeddingQuotaError, eq.EmbeddingCallsSuspended):
                pass
        self.assertLessEqual(sum(clock.sleeps), eq.MAX_WAIT_SECONDS_DEFAULT)
        self.assertLessEqual(len(client.calls), 1 + 5)

    def test_non_quota_error_is_not_retried_and_does_not_stop(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        boom = FakeAPIError(500, {"error": {"code": 500, "status": "INTERNAL", "message": "x"}})
        client = FakeClient(clock, script=[boom])
        with self.assertRaises(FakeAPIError):
            ep.embed_one(client, "a", gate=gate)
        ep.embed_one(client, "b", gate=gate)
        self.assertEqual((gate.failures_by_kind, gate.suspended, gate.retries), ({"other": 1}, "", 0))


# ── 동시 실행: 같은 러너의 다음 프로세스 ──────────────────────────────────


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class CrossProcessLedgerTests(LimiterTestCase):
    def test_second_process_shares_the_60_second_window(self):
        """수집(news_bot) 직후 백필(embedding_pipeline)이 떠도 합계가 상한을 넘지 않는다."""
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            clock = FakeClock()
            shared: list[float] = []
            collect = make_limiter(clock, ledger_path=ledger, max_wait_seconds=10_000)
            client = FakeClient(clock, shared=shared)
            for index in range(80):
                ep.embed_one(client, f"c{index}", gate=collect)
            backfill = make_limiter(clock, ledger_path=ledger, max_wait_seconds=10_000)
            client2 = FakeClient(clock, shared=shared)
            for index in range(80):
                ep.embed_one(client2, f"b{index}", gate=backfill)
            self.assertLessEqual(max(client.max_in_window, client2.max_in_window), 80)
            self.assertGreater(backfill.wait_pacing, 0)

    def test_per_minute_stop_makes_next_process_wait_first(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            clock = FakeClock()
            first = make_limiter(clock, ledger_path=ledger)
            with self.assertRaises(eq.EmbeddingQuotaError):
                ep.embed_one(FakeClient(clock, script=[unknown_429()] * 2), "a", gate=first)
            second = make_limiter(clock, ledger_path=ledger)
            ep.embed_one(FakeClient(clock), "b", gate=second)
            self.assertEqual(second.suspended, "")
            self.assertGreater(second.wait_retry, 0)

    def test_broken_ledger_falls_back_to_in_memory_window(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            ledger.write_text("{not json", encoding="utf-8")
            clock = FakeClock()
            gate = make_limiter(clock, ledger_path=ledger, rpm_cap=3, max_wait_seconds=1000)
            client = FakeClient(clock)
            for index in range(7):
                ep.embed_one(client, f"t{index}", gate=gate)
            self.assertLessEqual(client.max_in_window, 3)

    def test_default_ledger_can_be_switched_off(self):
        with patch.dict("os.environ", {"GEMINI_EMBEDDING_CALL_LEDGER": "off"}):
            self.assertIsNone(eq.default_ledger_path())
        with patch.dict("os.environ", {"GEMINI_EMBEDDING_CALL_LEDGER": "/x/y.json"}):
            self.assertEqual(eq.default_ledger_path(), Path("/x/y.json"))


# ── ⑥ 분류·오류 상세·통계 ─────────────────────────────────────────────────


class ClassificationTests(unittest.TestCase):
    def kind(self, error) -> str:
        return eq.classify_quota_error(error, now=0).kind

    def test_kinds_from_quota_id_and_metric(self):
        self.assertEqual(self.kind(rpm_429()), "RPM")
        self.assertEqual(self.kind(tpm_429()), "TPM")
        self.assertEqual(self.kind(rpd_429()), "RPD")
        self.assertEqual(self.kind(unknown_429()), "UNKNOWN")
        token_metric_only = quota_429(metric="generativelanguage.googleapis.com/x_input_token_count")
        self.assertEqual(self.kind(token_metric_only), "TPM")
        requests_metric_only = quota_429(
            metric="generativelanguage.googleapis.com/embed_content_free_tier_requests")
        self.assertEqual(self.kind(requests_metric_only), "UNKNOWN")  # 창을 모르면 단정 안 함

    def test_daily_wins_when_both_are_violated(self):
        error = rpm_429()
        error.details["error"]["details"][0]["violations"].append(
            {"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"})
        self.assertEqual(self.kind(error), "RPD")

    def test_fields_and_server_wait(self):
        info = eq.classify_quota_error(rpm_429("41.5s"), now=0)
        self.assertEqual(info.quota_id, "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier")
        self.assertEqual(info.quota_metric,
                         "generativelanguage.googleapis.com/embed_content_free_tier_requests")
        self.assertEqual(info.quota_value, "100")
        self.assertEqual(info.retry_delay, 41.5)
        self.assertEqual((info.http_status, info.status), (429, "RESOURCE_EXHAUSTED"))

    def test_message_retry_hint_and_http_date_header(self):
        error = quota_429(message="Please retry in 12.5s.")
        self.assertEqual(eq.classify_quota_error(error, now=0).retry_delay, 12.5)
        error = quota_429(headers={"Retry-After": "Thu, 01 Jan 1970 00:00:30 GMT"})
        self.assertEqual(eq.classify_quota_error(error, now=0).retry_after, 30.0)

    def test_plain_string_error_with_python_repr(self):
        """SDK str() 은 dict repr(작은따옴표)이다 — gemini_client 의 큰따옴표 정규식으로는 못 읽는다."""
        text = str(rpd_429())
        self.assertIn("'quotaId'", text)
        info = eq.classify_quota_error(RuntimeError(text), now=0)
        self.assertEqual((info.kind, info.retry_delay), ("RPD", 3.0))

    def test_non_quota_errors_are_ignored(self):
        self.assertIsNone(eq.classify_quota_error(RuntimeError("timeout"), now=0))
        self.assertIsNone(eq.classify_quota_error(
            FakeAPIError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}), now=0))

    @unittest.skipUnless(HAS_SDK, "google-genai 필요")
    def test_real_sdk_client_error_shape(self):
        from google.genai import errors
        body = rpm_429("41s").details
        error = errors.ClientError(429, body)
        info = eq.classify_quota_error(error, now=0)
        self.assertEqual((info.kind, info.retry_delay, info.quota_value), ("RPM", 41.0, "100"))

    def test_pacific_midnight(self):
        # 2026-10-01 12:00 UTC = 05:00 PDT → 다음 자정 2026-10-02 00:00 PDT = 07:00 UTC
        now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
        until = eq.next_pacific_midnight(now)
        self.assertEqual(datetime.fromtimestamp(until, tz=timezone.utc),
                         datetime(2026, 10, 2, 7, tzinfo=timezone.utc))


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class LogAndStatsTests(LimiterTestCase):
    def test_error_detail_has_fields_but_no_secrets(self):
        clock = FakeClock()
        logs: list[str] = []
        gate = make_limiter(clock, logs)
        client = FakeClient(clock, script=[rpm_429("41s"), unknown_429()])
        # RPM → 재시도 → UNKNOWN. 같은 요청이 이미 1회 재시도했으므로 불명 429 상한(1)에 걸려 멈춘다.
        with self.assertRaises(eq.EmbeddingQuotaError):
            ep.embed_one(client, ARTICLE_BODY, gate=gate)
        self.assertEqual(len(client.calls), 2)
        joined = "\n".join(logs)
        for expected in (MODEL, "HTTP 429", "RESOURCE_EXHAUSTED", "429 RPM",
                         "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier",
                         "limit=100", "RetryInfo 41s", "42.0초 대기 후 재시도 1/2"):
            self.assertIn(expected, joined)
        self.assertNotIn(SECRET_KEY, joined)
        self.assertNotIn(ARTICLE_BODY, joined)
        self.assertNotIn(SECRET_KEY, json.dumps(gate.stats(), ensure_ascii=False))
        event = gate.events[0]
        self.assertEqual(event["model"], MODEL)
        self.assertEqual(event["http_status"], 429)
        self.assertEqual(event["outcome"], "stopped:UNKNOWN")  # 같은 요청의 최종 결과로 갱신

    def test_stats_are_exact(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        arts = [article(i) for i in range(6)]
        cache = {arts[0]["hash"]: current_entry(arts[0]), arts[1]["hash"]: current_entry(arts[1])}
        client = FakeClient(clock, script=[None, rpm_429("2s"), None, None])
        for art in arts[:5]:
            ep.get_or_compute_embedding(client, art, art["hash"], cache, gate=gate)
        stats = gate.stats()
        self.assertEqual(stats["attempts"], 4)            # 신규 3 + 재시도 1
        self.assertEqual(stats["successes"], 3)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["cache_hits"], 2)
        self.assertEqual(stats["peak_per_minute"], 4)
        self.assertEqual(stats["wait_retry_s"], 3.0)
        line = gate.format_stats()
        self.assertTrue(line.startswith("[embedding] "))
        self.assertNotIn("[gemini]", line)
        for expected in ("API 요청 4회", "재시도 1", "성공 3", "실패 1[RPM 1]",
                         "캐시 재사용 2", "최대 분당 4/상한 80", "신규 호출 중단 없음"):
            self.assertIn(expected, line)

    def test_embedding_calls_do_not_touch_text_model_stats(self):
        import gemini_client
        gemini_client.reset_call_log()
        clock = FakeClock()
        gate = make_limiter(clock)
        ep.embed_one(FakeClient(clock), "a", gate=gate)
        self.assertEqual(gemini_client.call_stats()["total"], 0)

    def test_sdk_internal_retry_is_pinned_off(self):
        from google.genai import types
        config = ep._embed_config(types)
        self.assertEqual(config.http_options.retry_options.attempts, 1)
        self.assertEqual(config.output_dimensionality, ep.EMBEDDING_DIMENSION)


# ── ⑦ 기존 동작: 실패해도 기사는 남는다 ───────────────────────────────────


@unittest.skipUnless(HAS_SDK, "google-genai 필요 (requirements.txt)")
class DedupBehaviourTests(LimiterTestCase):
    def test_articles_without_vectors_are_kept_not_dropped(self):
        clock = FakeClock()
        gate = make_limiter(clock)
        eq.set_limiter(gate)
        arts = [article(i) for i in range(8)]
        client = FakeClient(clock, script=[rpd_429()])
        with patch.object(nb, "get_gemini", return_value=client):
            kept = nb.semantic_dedup(arts, {})
        self.assertEqual({a["hash"] for a in kept}, {a["hash"] for a in arts})

    def test_cached_duplicates_still_fold_after_stop(self):
        """중단 뒤에도 캐시 벡터끼리는 기존 임계값 그대로 접힌다."""
        clock = FakeClock()
        gate = make_limiter(clock)
        eq.set_limiter(gate)
        a, b = article(1, score=50), article(2, score=10)
        c = article(3, score=30)
        cache = {a["hash"]: current_entry(a), b["hash"]: current_entry(b)}
        client = FakeClient(clock, script=[rpd_429()])
        with patch.object(nb, "get_gemini", return_value=client):
            kept = nb.semantic_dedup([a, b, c], cache)
        self.assertEqual([x["hash"] for x in kept], [a["hash"], c["hash"]])
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
