"""Gemini 임베딩 호출의 공통 속도 제어 · 429 분류 · 호출 통계.

왜 따로 있는가
--------------
텍스트 호출은 ``gemini_client.call_json`` 하나로 모여 거기서 페이싱(``_pace``)과
계측(``call_stats``)을 받는다. 임베딩은 그 길을 타지 않는다 — SDK 의
``client.models.embed_content`` 를 ``embedding_pipeline.embed_one`` 이 직접 부른다.
그래서 2026-10-01 수집 #526·#527 에서 ``semantic_dedup`` 이 기사마다 0.3초만 쉬고
임베딩을 연달아 불렀고, AI Studio 에 Gemini Embedding 2 의 **RPM 106 / 100** 이
찍혔다(TPM 15.57K / 30K, RPD 268 / 1,000 은 한도 안). 429 를 맞은 뒤에도
``embedding quota exceeded`` 한 줄만 찍고 다음 기사로 넘어가 같은 실패를
188건·32건 반복했다. 오류 본문을 버려서 188건 각각이 어느 한도였는지는 지금도
모른다 — 그래서 여기서는 **본문을 읽고 가른다.**

무엇을 하는가
-------------
- 모든 임베딩 API 요청(재시도 포함)이 ``EmbeddingLimiter.call`` 을 지난다.
  최근 60초 요청 수가 상한(기본 80, ``GEMINI_EMBEDDING_RPM_CAP``)에 닿으면
  가장 오래된 요청이 창 밖으로 나갈 때까지 기다린다.
- 캐시에서 벡터를 꺼낸 경우는 이 길을 지나지 않는다 — 세지도 기다리지도 않는다.
- 429 는 본문의 ``QuotaFailure.violations[].quotaId/quotaMetric/quotaValue``,
  ``RetryInfo.retryDelay``, ``Retry-After`` 헤더로 RPM · TPM · RPD 를 가른다.
    RPM/TPM  서버가 요구한 만큼 기다린 뒤 같은 요청을 최대 N회 재시도.
    RPD      이번 회차 신규 호출 중단. 오늘 안 풀리므로 재시도는 한도만 태운다.
    UNKNOWN  RPD 로 단정하지 않는다. 1회만 재시도하고, 그래도 실패하면 중단.
  재시도까지 실패한 429 는 종류와 상관없이 **이번 회차 신규 호출을 멈춘다** —
  다음 기사에 같은 실패를 이어 붙이지 않는다. 남은 기사는 캐시로 진행한다.
- 회차 전체의 재시도 횟수와 대기 시간(페이싱 + 429 대기)에도 상한이 있다.
- 같은 잡 안의 다음 스텝(수집 ``news_bot.py`` → 백필 ``embedding_pipeline.py``)은
  다른 프로세스라 메모리 창을 공유하지 못한다. 프로세스마다 80회를 허용하면
  60초 안에 합계 160회가 나갈 수 있으므로, 최근 요청 시각과 중단 표지를 작은
  원장 파일(기본: 임시 디렉터리)에 함께 적는다.

로그에는 모델 · HTTP 상태 · 한도 종류 · quotaId · quotaMetric · 한도값 · 서버
대기 안내 · 재시도 결과만 남긴다. 오류 원문(message)·요청 본문·API 키는
남기지 않는다.

환경 변수
---------
    GEMINI_EMBEDDING_RPM_CAP          최근 60초 요청 상한 (기본 80, 한도 100)
    GEMINI_EMBEDDING_MAX_RETRIES      한 요청의 429 재시도 상한 (기본 2)
    GEMINI_EMBEDDING_MAX_TOTAL_RETRIES 회차 전체 재시도 상한 (기본 6)
    GEMINI_EMBEDDING_MAX_WAIT_SECONDS 회차 전체 대기 상한, 초 (기본 360)
    GEMINI_EMBEDDING_CALL_LEDGER      프로세스 간 원장 경로. ``off`` 면 끈다.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

try:  # POSIX 만. 윈도에서는 잠금 없이 쓴다 — 동시 실행이 없는 로컬 전용 경로다.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


RPM_CAP_DEFAULT = 80
WINDOW_SECONDS = 60.0
MAX_RETRIES_DEFAULT = 2
UNKNOWN_MAX_RETRIES = 1
MAX_TOTAL_RETRIES_DEFAULT = 6
# 크롤 잡 상한은 60분이고 그 안에 큐레이션·아카이브가 다 들어간다. 80 RPM 으로
# 6분이면 신규 벡터 ~480건 — 평소 회차 신규량을 넉넉히 덮고, 넘치면 다음
# 회차가 캐시 위에서 이어 받는다.
MAX_WAIT_SECONDS_DEFAULT = 360.0
# gemini_client.RETRY_DELAY_MAX 와 같은 값. 서버가 터무니없는 값을 주거나 파싱이
# 어긋나도 한 번에 잡을 통째로 잡아먹지 않는다.
RETRY_DELAY_MAX = 75.0
RETRY_DELAY_BUFFER = 1.0
LEDGER_DISABLED = {"", "0", "off", "false", "none"}

KIND_RPM = "RPM"
KIND_TPM = "TPM"
KIND_RPD = "RPD"
KIND_UNKNOWN = "UNKNOWN"
PER_MINUTE_KINDS = frozenset({KIND_RPM, KIND_TPM})

# 중단 사유. 429 종류에 더해 회차 예산 소진이 있다.
STOP_RETRY_BUDGET = "RETRY_BUDGET"
STOP_WAIT_BUDGET = "WAIT_BUDGET"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


class EmbeddingCallsSuspended(RuntimeError):
    """이번 회차의 신규 임베딩 호출이 멈췄다. 호출자는 캐시로만 진행한다."""

    def __init__(self, reason: str):
        super().__init__(f"embedding calls suspended: {reason}")
        self.reason = reason


class EmbeddingQuotaError(RuntimeError):
    """재시도 상한까지 쓰고도 풀리지 않은 429. 원문 대신 분류 결과만 싣는다."""

    def __init__(self, info: "QuotaInfo"):
        super().__init__(f"embedding 429 {info.kind}")
        self.info = info


# ── 429 분류 ──────────────────────────────────────────────────────────────


class QuotaInfo:
    __slots__ = ("http_status", "status", "kind", "quota_id", "quota_metric",
                 "quota_value", "retry_delay", "retry_after")

    def __init__(self, *, http_status: int | None, status: str, kind: str,
                 quota_id: str = "", quota_metric: str = "", quota_value: str = "",
                 retry_delay: float | None = None, retry_after: float | None = None):
        self.http_status = http_status
        self.status = status
        self.kind = kind
        self.quota_id = quota_id
        self.quota_metric = quota_metric
        self.quota_value = quota_value
        self.retry_delay = retry_delay      # 본문 RetryInfo / "Please retry in Ns"
        self.retry_after = retry_after      # HTTP Retry-After 헤더

    def server_wait(self) -> float | None:
        values = [v for v in (self.retry_delay, self.retry_after) if v and v > 0]
        return max(values) if values else None

    def as_log(self) -> dict:
        return {
            "http_status": self.http_status,
            "status": self.status,
            "kind": self.kind,
            "quota_id": self.quota_id,
            "quota_metric": self.quota_metric,
            "quota_value": self.quota_value,
            "retry_delay_s": self.retry_delay,
            "retry_after_s": self.retry_after,
        }


_DURATION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*s?\s*$")
_RETRY_IN_RE = re.compile(r"retry in\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
# SDK 예외의 str() 은 파이썬 dict repr 이라 작은따옴표다. JSON 문자열(큰따옴표)도 받는다.
_FIELD_RE = {
    name: re.compile(r"""['"]%s['"]\s*:\s*['"]([^'"]+)['"]""" % name)
    for name in ("quotaId", "quotaMetric", "quotaValue", "retryDelay")
}


def _seconds(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    if isinstance(value, dict):  # protobuf Duration 의 JSON 표현 {"seconds": 41, "nanos": ...}
        try:
            total = float(value.get("seconds") or 0) + float(value.get("nanos") or 0) / 1e9
        except (TypeError, ValueError):
            return None
        return total if total > 0 else None
    found = _DURATION_RE.match(str(value or ""))
    if not found:
        return None
    seconds = float(found.group(1))
    return seconds if seconds > 0 else None


def _retry_after_header(exc: BaseException, now: float) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # noqa: BLE001 — 헤더 형식이 낯설면 없는 것으로 본다
        return None
    if not raw:
        return None
    seconds = _seconds(raw)
    if seconds is not None:
        return seconds
    try:
        when = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = when.timestamp() - now
    return delta if delta > 0 else None


def _http_status(exc: BaseException) -> int | None:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _error_body(exc: BaseException) -> dict:
    details = getattr(exc, "details", None)
    if isinstance(details, list) and len(details) == 1:
        details = details[0]
    if isinstance(details, dict):
        inner = details.get("error")
        return inner if isinstance(inner, dict) else details
    return {}


def _violation_kind(quota_id: str, metric: str) -> str:
    """quotaId 가 1순위, 없으면 quotaMetric. 어느 쪽도 단서가 없으면 UNKNOWN."""
    joined = f"{quota_id} {metric}"
    lowered = joined.lower()
    if "perday" in lowered or "per_day" in lowered or "daily" in lowered:
        return KIND_RPD
    per_minute = "perminute" in lowered or "per_minute" in lowered
    if "token" in lowered and (per_minute or not quota_id):
        return KIND_TPM
    if per_minute:
        return KIND_RPM
    return KIND_UNKNOWN


_KIND_RANK = {KIND_RPD: 3, KIND_RPM: 2, KIND_TPM: 1, KIND_UNKNOWN: 0}


def classify_quota_error(exc: BaseException, *, now: float | None = None) -> QuotaInfo | None:
    """429 면 분류 결과를, 아니면 None 을 돌려준다.

    한 응답에 위반이 여럿이면 RPD 가 이긴다 — 하나라도 일일 한도면 기다려도 오늘은
    안 풀린다. 단서가 없으면 UNKNOWN 이다. **일일 한도로 단정하지 않는다** — 그건
    2026-08-06 큐레이션 429 를 '일일 한도 소진'으로 잘못 짚었던 바로 그 오진이다.
    """
    now = time.time() if now is None else now
    text = str(exc)
    body = _error_body(exc)
    code = _http_status(exc)
    status = str(body.get("status") or getattr(exc, "status", "") or "")
    is_429 = (code == 429 or status == "RESOURCE_EXHAUSTED"
              or "RESOURCE_EXHAUSTED" in text or re.search(r"\b429\b", text) is not None)
    if not is_429:
        return None

    violations: list[dict] = []
    retry_delay: float | None = None
    for detail in body.get("details") or []:
        if not isinstance(detail, dict):
            continue
        kind_name = str(detail.get("@type") or "")
        if kind_name.endswith("QuotaFailure"):
            violations.extend(v for v in detail.get("violations") or [] if isinstance(v, dict))
        elif kind_name.endswith("RetryInfo"):
            retry_delay = _seconds(detail.get("retryDelay"))

    if not violations:
        # 구조가 없으면(다른 SDK·문자열 예외) 같은 필드를 문자열에서 찾는다.
        fields = {name: (pattern.search(text) or None) for name, pattern in _FIELD_RE.items()}
        if fields["quotaId"] or fields["quotaMetric"]:
            violations.append({
                "quotaId": fields["quotaId"].group(1) if fields["quotaId"] else "",
                "quotaMetric": fields["quotaMetric"].group(1) if fields["quotaMetric"] else "",
                "quotaValue": fields["quotaValue"].group(1) if fields["quotaValue"] else "",
            })
        if retry_delay is None and fields["retryDelay"]:
            retry_delay = _seconds(fields["retryDelay"].group(1))
    if retry_delay is None:
        message = str(body.get("message") or text)
        found = _RETRY_IN_RE.search(message)
        if found:
            retry_delay = _seconds(found.group(1))

    chosen: dict = {}
    kind = KIND_UNKNOWN
    for violation in violations:
        candidate = _violation_kind(str(violation.get("quotaId") or ""),
                                    str(violation.get("quotaMetric") or ""))
        if not chosen or _KIND_RANK[candidate] > _KIND_RANK[kind]:
            chosen, kind = violation, candidate

    return QuotaInfo(
        http_status=code if code is not None else 429,
        status=status or "RESOURCE_EXHAUSTED",
        kind=kind,
        quota_id=str(chosen.get("quotaId") or ""),
        quota_metric=str(chosen.get("quotaMetric") or ""),
        quota_value=str(chosen.get("quotaValue") or ""),
        retry_delay=retry_delay,
        retry_after=_retry_after_header(exc, now),
    )


def next_pacific_midnight(now: float) -> float:
    """Gemini 일일 한도가 풀리는 시각(태평양 자정)."""
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001 — tzdata 가 없으면 보수적으로 6시간
        return now + 6 * 3600
    local = datetime.fromtimestamp(now, tz=zone)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


# ── 프로세스 간 원장 ──────────────────────────────────────────────────────


class _Ledger:
    """최근 60초 요청 시각과 중단 표지를 같은 러너의 다음 프로세스와 나눈다.

    실패해도 본 작업을 막지 않는다 — 원장이 깨지면 이 프로세스의 메모리 창만으로
    제한한다(예전보다 나빠지지 않는다).
    """

    def __init__(self, path: Path | None):
        self.path = path

    def _locked(self, mutate: Callable[[dict], None] | None) -> dict:
        if self.path is None:
            return {}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a+", encoding="utf-8") as stream:
                if fcntl is not None:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                stream.seek(0)
                try:
                    state = json.loads(stream.read() or "{}")
                except json.JSONDecodeError:
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                if mutate is not None:
                    mutate(state)
                    stream.seek(0)
                    stream.truncate()
                    stream.write(json.dumps(state))
                    stream.flush()
                return state
        except OSError:
            return {}

    def read(self) -> dict:
        return self._locked(None)

    def record(self, stamp: float, owner: str, *, keep_after: float) -> None:
        def mutate(state: dict) -> None:
            calls = [row for row in _ledger_calls(state) if row[0] > keep_after]
            calls.append([stamp, owner])
            state["calls"] = calls[-1000:]
        self._locked(mutate)

    def block(self, until: float, reason: str) -> None:
        def mutate(state: dict) -> None:
            if float(state.get("blocked_until") or 0) < until:
                state["blocked_until"] = until
                state["blocked_reason"] = reason
        self._locked(mutate)


def _ledger_calls(state: dict) -> list[list]:
    """원장의 ``[시각, 소유자]`` 목록. 형식이 어긋난 줄은 버린다."""
    rows = []
    for row in state.get("calls") or []:
        if (isinstance(row, list) and len(row) == 2
                and isinstance(row[0], (int, float)) and not isinstance(row[0], bool)):
            rows.append([float(row[0]), str(row[1])])
    return rows


def default_ledger_path() -> Path | None:
    raw = os.environ.get("GEMINI_EMBEDDING_CALL_LEDGER")
    if raw is None:
        return Path(tempfile.gettempdir()) / "nuclens-embedding-calls.json"
    if raw.strip().lower() in LEDGER_DISABLED:
        return None
    return Path(raw)


# ── 제한기 ────────────────────────────────────────────────────────────────


class EmbeddingLimiter:
    def __init__(
        self,
        *,
        model: str,
        rpm_cap: int = RPM_CAP_DEFAULT,
        max_retries: int = MAX_RETRIES_DEFAULT,
        max_total_retries: int = MAX_TOTAL_RETRIES_DEFAULT,
        max_wait_seconds: float = MAX_WAIT_SECONDS_DEFAULT,
        ledger_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ):
        self.model = model
        self.rpm_cap = max(1, int(rpm_cap))
        self.max_retries = max(0, int(max_retries))
        self.max_total_retries = max(0, int(max_total_retries))
        self.max_wait_seconds = max(0.0, float(max_wait_seconds))
        self._ledger = _Ledger(ledger_path)
        self._owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._clock = clock
        self._sleep = sleep
        self._log = log
        self._own_calls: list[float] = []
        self.suspended = ""
        self.attempts = 0
        self.successes = 0
        self.failures = 0
        self.failures_by_kind: dict[str, int] = {}
        self.retries = 0
        self.recovered = 0
        self.cache_hits = 0
        self.skipped_after_stop = 0
        self.wait_pacing = 0.0
        self.wait_retry = 0.0
        self.events: list[dict] = []

    @classmethod
    def from_env(cls, model: str, **overrides) -> "EmbeddingLimiter":
        settings = {
            "rpm_cap": _env_int("GEMINI_EMBEDDING_RPM_CAP", RPM_CAP_DEFAULT, minimum=1),
            "max_retries": _env_int("GEMINI_EMBEDDING_MAX_RETRIES", MAX_RETRIES_DEFAULT),
            "max_total_retries": _env_int("GEMINI_EMBEDDING_MAX_TOTAL_RETRIES",
                                          MAX_TOTAL_RETRIES_DEFAULT),
            "max_wait_seconds": _env_float("GEMINI_EMBEDDING_MAX_WAIT_SECONDS",
                                           MAX_WAIT_SECONDS_DEFAULT),
            "ledger_path": default_ledger_path(),
        }
        settings.update(overrides)
        return cls(model=model, **settings)

    # -- 계측 --------------------------------------------------------------

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def _waited(self) -> float:
        return self.wait_pacing + self.wait_retry

    def peak_per_minute(self) -> int:
        """이 프로세스가 낸 요청의 슬라이딩 60초 최대치 (gemini_client.call_stats 와 같은 방식)."""
        stamps = self._own_calls
        best = 0
        start = 0
        for end, stamp in enumerate(stamps):
            while stamp - stamps[start] >= WINDOW_SECONDS:
                start += 1
            best = max(best, end - start + 1)
        return best

    def stats(self) -> dict:
        return {
            "model": self.model,
            "rpm_cap": self.rpm_cap,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "failures_by_kind": dict(self.failures_by_kind),
            "retries": self.retries,
            "recovered": self.recovered,
            "cache_hits": self.cache_hits,
            "skipped_after_stop": self.skipped_after_stop,
            "peak_per_minute": self.peak_per_minute(),
            "wait_pacing_s": round(self.wait_pacing, 1),
            "wait_retry_s": round(self.wait_retry, 1),
            "suspended": self.suspended,
            "events": [dict(event) for event in self.events],
        }

    def format_stats(self) -> str:
        """텍스트 모델의 ``[gemini] 호출 N회`` 와 섞이지 않게 ``[embedding]`` 으로 시작한다."""
        by_kind = ", ".join(f"{kind} {count}"
                            for kind, count in sorted(self.failures_by_kind.items()))
        stop = (f"신규 호출 중단({self.suspended}) · 중단 후 캐시 미스 {self.skipped_after_stop}건"
                if self.suspended else "신규 호출 중단 없음")
        return (
            f"[embedding] {self.model} API 요청 {self.attempts}회(재시도 {self.retries}, "
            f"재시도 복구 {self.recovered}) · 성공 {self.successes} · 실패 {self.failures}"
            + (f"[{by_kind}]" if by_kind else "")
            + f" · 캐시 재사용 {self.cache_hits} · 최대 분당 {self.peak_per_minute()}"
            f"/상한 {self.rpm_cap} · 대기 페이싱 {self.wait_pacing:.1f}s"
            f"/429 {self.wait_retry:.1f}s · {stop}"
        )

    # -- 제어 --------------------------------------------------------------

    def _stop(self, reason: str, *, until: float | None = None) -> None:
        if not self.suspended:
            self.suspended = reason
            self._log(f"[embedding] 이번 회차 신규 임베딩 호출 중단({reason}) — "
                      f"남은 기사는 캐시만 사용")
        if until is not None:
            self._ledger.block(until, reason)

    def _sleep_within_budget(self, seconds: float, *, bucket: str) -> bool:
        """회차 대기 예산 안이면 자고 True. 넘으면 자지 않고 False."""
        if seconds <= 0:
            return True
        if self._waited() + seconds > self.max_wait_seconds:
            return False
        self._sleep(seconds)
        if bucket == "pacing":
            self.wait_pacing += seconds
        else:
            self.wait_retry += seconds
        return True

    def _acquire(self) -> None:
        """다음 요청 직전에 최근 60초 요청 수를 상한 밑으로 맞춘다."""
        while True:
            now = self._clock()
            ledger = self._ledger.read()
            blocked_until = float(ledger.get("blocked_until") or 0)
            if blocked_until > now:
                reason = str(ledger.get("blocked_reason") or KIND_UNKNOWN)
                if reason == KIND_RPD:
                    self._stop(KIND_RPD)
                    raise EmbeddingCallsSuspended(KIND_RPD)
                # 앞 프로세스가 분당 한도로 멈췄다 — 그 대기만큼 기다린 뒤 시작한다.
                if not self._sleep_within_budget(blocked_until - now, bucket="retry"):
                    self._stop(STOP_WAIT_BUDGET)
                    raise EmbeddingCallsSuspended(STOP_WAIT_BUDGET)
                continue
            window_start = now - WINDOW_SECONDS
            # 내 요청은 메모리에서, 다른 프로세스의 요청은 원장에서 센다. 원장에도
            # 내 요청이 있으므로 소유자로 걸러 두 번 세지 않는다.
            recent = sorted(
                [stamp for stamp in self._own_calls if stamp > window_start]
                + [stamp for stamp, owner in _ledger_calls(ledger)
                   if owner != self._owner and stamp > window_start]
            )
            if len(recent) < self.rpm_cap:
                return
            wait = recent[len(recent) - self.rpm_cap] + WINDOW_SECONDS - now + 0.05
            if not self._sleep_within_budget(wait, bucket="pacing"):
                self._stop(STOP_WAIT_BUDGET)
                raise EmbeddingCallsSuspended(STOP_WAIT_BUDGET)

    def _record_attempt(self) -> None:
        stamp = self._clock()
        self.attempts += 1
        self._own_calls.append(stamp)
        self._ledger.record(stamp, self._owner, keep_after=stamp - WINDOW_SECONDS)

    def call(self, request: Callable[[], object], *, label: str = ""):
        """``request`` 를 상한·429 정책 아래에서 부른다.

        - 중단된 회차면 부르지 않고 ``EmbeddingCallsSuspended``.
        - 429 를 끝내 못 넘기면 ``EmbeddingQuotaError`` (이때 회차도 중단된다).
        - 429 가 아닌 오류는 그대로 올린다 — 호출자가 기존처럼 다룬다.
        """
        if self.suspended:
            self.skipped_after_stop += 1
            raise EmbeddingCallsSuspended(self.suspended)
        attempt = 0
        event: dict | None = None
        while True:
            try:
                self._acquire()
            except EmbeddingCallsSuspended:
                if event is not None:
                    event["outcome"] = f"stopped:{self.suspended}"
                raise
            self._record_attempt()
            try:
                result = request()
            except Exception as exc:  # noqa: BLE001 — 분류 뒤 그대로 다시 올린다
                self.failures += 1
                info = classify_quota_error(exc, now=self._clock())
                if info is None:
                    self.failures_by_kind["other"] = self.failures_by_kind.get("other", 0) + 1
                    if event is not None:
                        event["outcome"] = f"failed:{type(exc).__name__}"
                    raise
                self.failures_by_kind[info.kind] = self.failures_by_kind.get(info.kind, 0) + 1
                event = self._on_quota_error(info, attempt=attempt, label=label, event=event)
                if event["outcome"] != "retrying":
                    raise EmbeddingQuotaError(info) from None
                attempt += 1
                continue
            self.successes += 1
            if event is not None:
                event["outcome"] = "recovered"
                self.recovered += 1
                self._log(f"[embedding] 429 재시도 복구 — {attempt}회 재시도 후 성공"
                          + (f" ({label})" if label else ""))
            return result

    def _on_quota_error(self, info: QuotaInfo, *, attempt: int, label: str,
                        event: dict | None) -> dict:
        now = self._clock()
        if event is None:
            event = {"model": self.model, "label": label, **info.as_log(),
                     "retries": 0, "waited_s": 0.0, "outcome": ""}
            self.events.append(event)
        else:  # 재시도에서 다시 맞은 429 — 가장 최근 분류로 갱신한다
            event.update(info.as_log())
        limit = (self.max_retries if info.kind in PER_MINUTE_KINDS
                 else UNKNOWN_MAX_RETRIES if info.kind == KIND_UNKNOWN else 0)
        wait = min(info.server_wait() or WINDOW_SECONDS, RETRY_DELAY_MAX)
        if info.server_wait():
            wait = min(wait + RETRY_DELAY_BUFFER, RETRY_DELAY_MAX)

        if info.kind == KIND_RPD:
            outcome = f"stopped:{KIND_RPD}"
            self._stop(KIND_RPD, until=next_pacific_midnight(now))
        elif attempt >= limit:
            # 같은 요청을 상한만큼 다시 불러도 안 풀렸다. 다음 기사에 같은 실패를
            # 잇지 않도록 회차를 멈춘다. 분당이면 다음 프로세스는 이 대기만큼 쉬고 시작한다.
            outcome = f"stopped:{info.kind}"
            self._stop(info.kind, until=now + wait)
        elif self.retries >= self.max_total_retries:
            outcome = f"stopped:{STOP_RETRY_BUDGET}"
            self._stop(STOP_RETRY_BUDGET, until=now + wait)
        elif not self._sleep_within_budget(wait, bucket="retry"):
            outcome = f"stopped:{STOP_WAIT_BUDGET}"
            self._stop(STOP_WAIT_BUDGET, until=now + wait)
        else:
            outcome = "retrying"
            self.retries += 1
            event["retries"] = int(event.get("retries") or 0) + 1
            event["waited_s"] = round(float(event.get("waited_s") or 0) + wait, 1)
        event["outcome"] = outcome
        self._log(self._describe(info, outcome=outcome, wait=wait, attempt=attempt,
                                 limit=limit, label=label))
        return event

    def _describe(self, info: QuotaInfo, *, outcome: str, wait: float, attempt: int,
                  limit: int, label: str) -> str:
        server = []
        if info.retry_delay:
            server.append(f"RetryInfo {info.retry_delay:g}s")
        if info.retry_after:
            server.append(f"Retry-After {info.retry_after:g}s")
        head = (
            f"[embedding] 429 {info.kind} · model={self.model} · HTTP {info.http_status} "
            f"{info.status} · quotaId={info.quota_id or '-'} · "
            f"quotaMetric={info.quota_metric or '-'} · limit={info.quota_value or '-'} · "
            f"서버 대기 안내={', '.join(server) or '없음'}"
            + (f" · {label}" if label else "")
        )
        if outcome == "retrying":
            return head + f" → {wait:.1f}초 대기 후 재시도 {attempt + 1}/{limit}"
        return head + f" → 재시도 안 함({outcome})"


# ── 프로세스 기본 제한기 ──────────────────────────────────────────────────

_DEFAULT: EmbeddingLimiter | None = None


def get_limiter(model: str) -> EmbeddingLimiter:
    """프로세스당 하나. 수집 중복 판별과 백필이 같은 창·예산·통계를 쓴다."""
    global _DEFAULT
    if _DEFAULT is None or _DEFAULT.model != model:
        _DEFAULT = EmbeddingLimiter.from_env(model)
    return _DEFAULT


def set_limiter(limiter: EmbeddingLimiter | None) -> None:
    """테스트·도구용. None 이면 다음 ``get_limiter`` 가 환경에서 새로 만든다."""
    global _DEFAULT
    _DEFAULT = limiter


def current_limiter() -> EmbeddingLimiter | None:
    return _DEFAULT
