"""Non-blocking source-health tracking and operational quality alerts.

The crawler and data-quality jobs must keep running when monitoring itself fails.
This module therefore contains no network calls and does not own any repository
files.  Callers keep the returned dictionaries in their existing persistent
state (currently ``sent.json``), and inject a sender only after an alert batch
has been planned.

Two distinctions are deliberate:

* A successful fetch with zero entries is ``empty``, not a network failure.
  Repeated empty responses are still worth inspecting because a parser can
  silently stop matching after a publisher changes its markup.
* Detecting an alert and recording a successful notification are separate.
  If Telegram fails, ``last_notified_at`` is not advanced and the next run can
  retry instead of silently losing the warning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable, Iterable, Mapping, Sequence


STATE_VERSION = 1
DEFAULT_ALERT_COOLDOWN = timedelta(hours=24)
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(now: datetime | None = None) -> str:
    return _utc_now(now).isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# 피드가 한 페이지를 통째로 주고도 쓸 수 있는 항목이 0건이면 형식이 바뀐 것이다.
# 항목 수가 적을 때는 정상적으로도 전건이 걸러질 수 있으므로 바닥을 둔다.
UNUSABLE_ENTRY_FLOOR = 5
# 실측 2026-08-02~17, 감시 대상 18개 출처의 발행 간격: p50 3일 · p95 5일 · 최대 5일.
# 관측 상한의 약 3배로 잡아 현재 0/18 이 걸리게 한다. 관측 창이 15일이라 그보다
# 긴 정상 공백은 확인할 수 없었다 — 낮추기 전에 더 긴 창으로 다시 재야 한다.
STALE_FEED_DAYS = 14


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _days_since(value: object, now: datetime) -> int | None:
    """Whole days between an ISO timestamp and ``now``; ``None`` if unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, (now - parsed).days)


def _source_specs(expected_sources: object) -> dict[str, str]:
    """Normalize source definitions to ``{name: kind}``.

    Accepted forms intentionally match the crawler's existing constants:
    mappings, strings, or dictionaries containing ``name`` and optionally
    ``kind``/``source_kind``.  A direct-board source is ``official``; other
    feeds default to ``feed``.
    """
    if isinstance(expected_sources, Mapping):
        out: dict[str, str] = {}
        for name, value in expected_sources.items():
            if not str(name).strip():
                continue
            if isinstance(value, Mapping):
                kind = value.get("source_kind") or (
                    "official" if value.get("kind") else "feed")
            else:
                kind = value
            out[str(name)] = "official" if str(kind) == "official" else "feed"
        return out

    out = {}
    if not isinstance(expected_sources, Iterable) or isinstance(expected_sources, (str, bytes)):
        return out
    for item in expected_sources:
        if isinstance(item, str):
            out[item] = "feed"
        elif isinstance(item, Mapping) and item.get("name"):
            kind = item.get("source_kind") or ("official" if item.get("kind") else "feed")
            out[str(item["name"])] = "official" if kind == "official" else "feed"
    return out


def source_observations(snapshot: Mapping | None,
                        expected_sources: object = None) -> list[dict]:
    """Turn the crawler's ``source_yield`` snapshot into explicit observations.

    ``snapshot`` supports the existing ``counts``, ``kept`` and ``errors``
    fields.  A future/precise collector may additionally supply ``success`` as
    a source-name-to-bool mapping.  An expected source missing from every field
    is recorded as a failure rather than disappearing from monitoring.
    """
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    kept = snapshot.get("kept") if isinstance(snapshot.get("kept"), Mapping) else {}
    errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), Mapping) else {}
    success = snapshot.get("success") if isinstance(snapshot.get("success"), Mapping) else {}
    diagnostics = (snapshot.get("diagnostics")
                   if isinstance(snapshot.get("diagnostics"), Mapping) else {})
    specs = _source_specs(expected_sources)
    names = set(specs) | {str(k) for field in (counts, kept, errors, success, diagnostics)
                          for k in field}

    observations = []
    for name in sorted(names):
        present = any(name in field
                      for field in (counts, kept, errors, success, diagnostics))
        error = str(errors.get(name) or "").strip()
        explicitly_failed = success.get(name) is False
        ok = present and not error and not explicitly_failed
        count = _nonnegative_int(counts.get(name))
        status = "failed" if not ok else ("empty" if count == 0 else "ok")
        if not present:
            error = "source was not observed in this collection run"
        elif explicitly_failed and not error:
            error = "collector reported failure"
        row = diagnostics.get(name)
        row = row if isinstance(row, Mapping) else {}
        observations.append({
            "name": name,
            "kind": specs.get(name, "feed"),
            "status": status,
            "count": count,
            "kept": _nonnegative_int(kept.get(name)),
            "error": error[:240],
            # 부분 장애 계기. 옛 스냅샷에는 없으므로 전부 선택 항목이다.
            "bozo": bool(row.get("bozo")),
            "bozo_exception": str(row.get("bozo_exception") or "")[:200],
            "entries": _nonnegative_int(row.get("entries")),
            "usable": _nonnegative_int(row.get("usable")),
            "newest_pub": str(row.get("newest_pub") or "").strip(),
            "has_diagnostics": bool(row),
        })
    return observations


def update_source_health(previous: Mapping | None,
                         observations: Iterable[Mapping],
                         now: datetime | None = None) -> dict:
    """Apply one collection run to persistent source health.

    Malformed observations are ignored.  Sources not included in this run are
    preserved unchanged because some collectors are intentionally scheduled at
    different times.
    """
    checked_at = _iso(now)
    old_sources = previous.get("sources") if isinstance(previous, Mapping) else {}
    old_sources = old_sources if isinstance(old_sources, Mapping) else {}
    sources = {str(name): dict(row) for name, row in old_sources.items()
               if isinstance(row, Mapping)}

    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        name = str(observation.get("name") or "").strip()
        if not name:
            continue
        status = str(observation.get("status") or "failed")
        if status not in {"ok", "empty", "failed"}:
            status = "failed"
        count = _nonnegative_int(observation.get("count"))
        kept = _nonnegative_int(observation.get("kept"))
        row = dict(sources.get(name) or {})
        row.setdefault("first_checked_at", checked_at)
        row.update({
            "name": name,
            "kind": "official" if observation.get("kind") == "official" else "feed",
            "last_checked_at": checked_at,
            "last_status": status,
            "last_count": count,
            "last_kept_count": kept,
            "checks": _nonnegative_int(row.get("checks")) + 1,
        })

        if observation.get("has_diagnostics"):
            entries = _nonnegative_int(observation.get("entries"))
            usable = _nonnegative_int(observation.get("usable"))
            row.update({"last_entries": entries, "last_usable": usable})
            newest = str(observation.get("newest_pub") or "").strip()
            if newest:
                # 뒤로 가지 않는다 — 어떤 실행이 일부만 읽어 와도 그 피드가
                # 갑자기 오래된 것으로 보이면 안 된다.
                row["last_newest_pub"] = max(newest, str(row.get("last_newest_pub") or ""))
            if observation.get("bozo"):
                row["consecutive_bozo"] = _nonnegative_int(row.get("consecutive_bozo")) + 1
                row["last_bozo_exception"] = str(
                    observation.get("bozo_exception") or "")[:200]
            else:
                row["consecutive_bozo"] = 0
                row["last_bozo_exception"] = ""
            if entries >= UNUSABLE_ENTRY_FLOOR and usable == 0 and status != "failed":
                row["consecutive_unusable"] = _nonnegative_int(
                    row.get("consecutive_unusable")) + 1
            else:
                row["consecutive_unusable"] = 0

        if status == "failed":
            row["consecutive_failures"] = _nonnegative_int(row.get("consecutive_failures")) + 1
            row["consecutive_empty"] = 0
            row["failures"] = _nonnegative_int(row.get("failures")) + 1
            row["last_failure_at"] = checked_at
            row["last_error"] = str(observation.get("error") or "unknown error")[:240]
        else:
            row["consecutive_failures"] = 0
            row["successes"] = _nonnegative_int(row.get("successes")) + 1
            row["last_success_at"] = checked_at
            row["last_error"] = ""
            if status == "empty":
                row["consecutive_empty"] = _nonnegative_int(row.get("consecutive_empty")) + 1
            else:
                row["consecutive_empty"] = 0
                row["last_nonempty_at"] = checked_at
        sources[name] = row

    return {"version": STATE_VERSION, "updated_at": checked_at, "sources": sources}


def ingest_source_snapshot(previous: Mapping | None, snapshot: Mapping | None,
                           expected_sources: object = None,
                           now: datetime | None = None) -> tuple[dict, bool]:
    """Idempotently ingest ``sent.json[source_yield]``.

    GitHub Actions retries can execute a monitoring hook twice without running
    the crawler again.  ``source_yield.at`` is the run identity; old snapshots
    without that field get a deterministic content hash instead.
    """
    previous_dict = dict(previous) if isinstance(previous, Mapping) else {
        "version": STATE_VERSION, "sources": {}}
    if not isinstance(snapshot, Mapping) or not snapshot:
        return previous_dict, False
    snapshot_id = str(snapshot.get("at") or "").strip()
    if not snapshot_id:
        try:
            payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload = repr(snapshot)
        snapshot_id = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if previous_dict.get("last_snapshot_id") == snapshot_id:
        return previous_dict, False
    result = update_source_health(
        previous_dict, source_observations(snapshot, expected_sources), now=now)
    result["last_snapshot_id"] = snapshot_id
    return result, True


@dataclass(frozen=True)
class AlertSignal:
    key: str
    scope: str
    title: str
    detail: str
    severity: str = "warning"
    observation_id: str = ""
    min_occurrences: int = 2

    def normalized(self) -> "AlertSignal":
        return AlertSignal(
            key=str(self.key).strip(),
            scope=str(self.scope or "quality").strip(),
            title=str(self.title).strip()[:120],
            detail=str(self.detail).strip()[:700],
            severity=self.severity if self.severity in _SEVERITY_RANK else "warning",
            observation_id=str(self.observation_id).strip(),
            min_occurrences=max(1, int(self.min_occurrences or 1)),
        )


def source_health_signals(health: Mapping | None, *,
                          failure_threshold: int = 2,
                          empty_threshold: int = 3,
                          bozo_threshold: int = 2,
                          unusable_threshold: int = 2,
                          stale_days: int = STALE_FEED_DAYS,
                          now: datetime | None = None) -> list[AlertSignal]:
    """Create distinct alerts for hard failures, empty runs and partial faults.

    The partial-fault rules deliberately avoid the ``counts>0 / kept==0`` trap:
    that combination is an ordinary quiet day (measured 2026-08-08 — four boards
    returned 10/15/10/10 items and every one fell out at the freshness cutoff).
    Each rule below keys on something a healthy feed never does:

    * ``bozo`` — the parser reported a fault *and* we still took entries, so the
      item list is silently partial;
    * ``entries >= floor and usable == 0`` — the feed handed us a full page and
      not one item had a usable link/title/date, which is a format change; and
    * the feed's newest item being older than ``stale_days``.  Over the 18
      monitored sources the largest normal gap between publications was 5 days
      (p50 3, p95 5), so 14 sits about three times above the observed ceiling
      and fires on none of them today.
    """
    sources = health.get("sources") if isinstance(health, Mapping) else {}
    if not isinstance(sources, Mapping):
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out = []
    for name, raw in sorted(sources.items()):
        if not isinstance(raw, Mapping):
            continue
        failures = _nonnegative_int(raw.get("consecutive_failures"))
        empties = _nonnegative_int(raw.get("consecutive_empty"))
        observation_id = str(raw.get("last_checked_at") or "")
        kind_label = "공식기관" if raw.get("kind") == "official" else "RSS/피드"

        # 부분 장애는 하드 실패와 별개로 본다 — 실패 중인 출처는 이미 위에서
        # 알리므로 중복 경보를 만들지 않되, 정상으로 보이는 출처의 조용한
        # 고장은 여기서만 잡힌다.
        if failures < failure_threshold:
            bozo = _nonnegative_int(raw.get("consecutive_bozo"))
            unusable = _nonnegative_int(raw.get("consecutive_unusable"))
            if bozo >= bozo_threshold:
                out.append(AlertSignal(
                    key=f"source:{name}:partial-parse", scope="source",
                    severity="warning",
                    title=f"{kind_label} 부분 파싱 실패: {name}",
                    detail=(f"연속 {bozo}회 파서 경고와 함께 항목 일부만 받았습니다 "
                            f"(최근 {raw.get('last_usable')}/{raw.get('last_entries')}건). "
                            f"오류: {raw.get('last_bozo_exception') or '기록 없음'}"),
                    observation_id=observation_id, min_occurrences=1,
                ))
            elif unusable >= unusable_threshold:
                out.append(AlertSignal(
                    key=f"source:{name}:unusable", scope="source", severity="warning",
                    title=f"{kind_label} 항목을 하나도 읽지 못함: {name}",
                    detail=(f"연속 {unusable}회 원문 {raw.get('last_entries')}건을 받고도 "
                            "링크·제목·게시일을 갖춘 항목이 0건입니다. "
                            "무소식이 아니라 피드 형식 변경일 가능성이 큽니다."),
                    observation_id=observation_id, min_occurrences=1,
                ))
            quiet = _days_since(raw.get("last_newest_pub"), now)
            if quiet is not None and quiet >= stale_days:
                out.append(AlertSignal(
                    key=f"source:{name}:stale", scope="source", severity="warning",
                    title=f"{kind_label} 최신 항목이 오래됨: {name}",
                    detail=(f"가장 최근 항목이 {quiet}일 전({raw.get('last_newest_pub')})입니다. "
                            f"수집은 성공하고 있어 접속 문제는 아니며, 관측된 정상 "
                            f"공백의 상한은 5일이었습니다."),
                    observation_id=observation_id, min_occurrences=1,
                ))

        if failures >= failure_threshold:
            severity = "critical" if raw.get("kind") == "official" and failures >= 3 else "warning"
            out.append(AlertSignal(
                key=f"source:{name}:failure", scope="source", severity=severity,
                title=f"{kind_label} 수집 실패: {name}",
                detail=(f"연속 {failures}회 실패 · 마지막 오류: "
                        f"{raw.get('last_error') or '원인 기록 없음'}"),
                observation_id=observation_id, min_occurrences=1,
            ))
        elif empties >= empty_threshold:
            out.append(AlertSignal(
                key=f"source:{name}:empty", scope="source", severity="warning",
                title=f"{kind_label} 수집 결과 0건: {name}",
                detail=(f"접속 오류는 없지만 연속 {empties}회 원문 항목이 0건입니다. "
                        "실제 무소식인지 피드·파서 변경인지 확인이 필요합니다."),
                observation_id=observation_id, min_occurrences=1,
            ))
    return out


def data_gate_signals(record: Mapping | None) -> list[AlertSignal]:
    """Translate one ``data_quality_gate`` record into actionable signals."""
    if not isinstance(record, Mapping):
        return []
    observation_id = str(record.get("observation_id") or
                         record.get("generated_at") or record.get("date") or "")
    out = []
    archive_quality = record.get("archive_quality")
    quarantined = (_nonnegative_int(archive_quality.get("quarantined"))
                   if isinstance(archive_quality, Mapping) else 0)
    sanitized = (_nonnegative_int(archive_quality.get("sanitized"))
                 if isinstance(archive_quality, Mapping) else 0)
    if quarantined or sanitized:
        quarantine_samples = archive_quality.get("quarantine_samples") or []
        sanitize_samples = archive_quality.get("sanitize_samples") or []
        samples = list(quarantine_samples) + list(sanitize_samples)
        hashes = ", ".join(
            str(row.get("hash") or "")[:12] for row in samples[:5]
            if isinstance(row, Mapping) and row.get("hash"))
        if quarantined and sanitized:
            title = "아카이브 기사 무결성 격리·정제"
        elif quarantined:
            title = "원문과 다른 아카이브 기사 격리"
        else:
            title = "아카이브 기사 날짜 무결성 정제"
        out.append(AlertSignal(
            # 격리와 정제는 같은 무결성 사고의 강도 차이다. 키를 하나로
            # 유지하면 정제 경고 뒤 격리가 생겼을 때 severity escalation은
            # 즉시 알리면서, 워크플로 재시도는 같은 사고로 중복 발송하지 않는다.
            key="quality:archive-integrity", scope="data_gate",
            severity="critical" if quarantined else "warning",
            title=title,
            detail=(f"웹 빌드 무결성 검사 결과 격리 {quarantined}건 · "
                    f"사건일 등 정제 {sanitized}건. 대상: {hashes or '로그 참조'}"),
            observation_id=observation_id, min_occurrences=1,
        ))
    tracking = record.get("tracking")
    if isinstance(tracking, Mapping) and tracking.get("applicable") and tracking.get("below_target"):
        out.append(AlertSignal(
            key="quality:tracking-rate", scope="data_gate",
            title="이슈 추적률 기준 미달",
            detail=(f"최근 {tracking.get('window_briefings') or '?'}회 브리핑 추적률 "
                    f"{tracking.get('rate')} (기준 {tracking.get('target')})"),
            observation_id=observation_id, min_occurrences=2,
        ))

    # 이슈 병합 후보 감시. 판정은 issue_candidate_stats.guardrails 가 이미 했고
    # 여기서는 **전달만** 한다 — 임계값이 두 곳에 있으면 반드시 어긋난다.
    #
    # 부르는 속도를 심각도로 가른다 — 이 기록은 **하루 한 번**만 생긴다
    # (data_gate_metrics 는 daily-brief 에서만 돈다). 그래서 min_occurrences=2 는
    # '내일 다시 보고'가 아니라 **이틀 뒤**라는 뜻이다.
    #
    #   warning  — 아직 잃은 것은 없고 여유가 줄었다는 신호다. 하루치 후보 분포는
    #              그날 수집량을 따라 흔들리므로 한 회차만 보고 부르면 한산한 날마다
    #              울린다(추적률 게이트에서 이미 치른 대가다). 이틀 연속일 때 부른다.
    #   critical — 계획한 컷이 **지금 실제로 병합을 놓치고 있다**. 표본 하한(50건)과
    #              비율 판정을 이미 통과한 값이라 그날 수집량 문제가 아니다.
    #              하루를 더 기다리면 그 하루치 병합을 그냥 잃는다.
    #
    # 같은 원리가 quality:curation-failure 에 이미 있다(유실 10건 이상이면 즉시).
    candidates = record.get("issue_candidates")
    if isinstance(candidates, Mapping) and candidates.get("applicable"):
        for guard in candidates.get("guards") or []:
            if not isinstance(guard, Mapping):
                continue
            key = str(guard.get("id") or "").strip()
            if not key:
                continue
            severity = str(guard.get("severity") or "warning")
            out.append(AlertSignal(
                key=key, scope="data_gate", severity=severity,
                title=str(guard.get("title") or "이슈 병합 후보 감시"),
                detail=str(guard.get("detail") or ""),
                observation_id=observation_id,
                min_occurrences=1 if severity == "critical" else 2,
            ))

    weeks = record.get("topic_weeks")
    if isinstance(weeks, Mapping):
        hidden = []
        # A missing ratio means there is not enough data to judge; it is not an
        # operational defect and should not page an administrator.
        if weeks.get("flow_ratio") is not None and not weeks.get("flow_visible", True):
            hidden.append(f"주제 흐름({weeks.get('flow_ratio')})")
        if weeks.get("slope_ratio") is not None and not weeks.get("slope_visible", True):
            hidden.append(f"슬로프({weeks.get('slope_ratio')})")
        if hidden:
            out.append(AlertSignal(
                key="quality:topic-weeks", scope="data_gate",
                title="웹 주제 흐름 지표 비노출",
                detail=(f"화면에서 {', '.join(hidden)}가 숨겨집니다. "
                        f"주별 표본 {weeks.get('totals')}; 기준 {weeks.get('limit')}"),
                observation_id=observation_id, min_occurrences=2,
            ))
    return out


_WEB_PIPELINE_LABELS = {
    "web_build": "웹 데이터 빌드",
    "data_gate": "데이터 품질 기록",
    "web_deploy": "Cloudflare 배포·스모크",
}


def web_pipeline_signals(outcomes: Mapping | None, *,
                         observation_id: str = "") -> list[AlertSignal]:
    """Translate explicit GitHub step outcomes into one pipeline incident.

    This path deliberately does not depend on ``delivery_log.jsonl``: a failed
    build or metrics step may be unable to create that log record at all.  The
    stable key keeps retries idempotent, while ``observation_id`` (normally the
    GitHub run id) lets a genuinely new failed run count as a new observation.
    """
    if not isinstance(outcomes, Mapping):
        return []
    normalized = {
        stage: str(outcomes.get(stage) or "missing").strip().lower()
        for stage in _WEB_PIPELINE_LABELS
    }

    failed: list[str] = []
    # Downstream steps are expected to be skipped after a build failure, so
    # report the root failure instead of paging three times for one incident.
    if normalized["web_build"] != "success":
        failed.append("web_build")
    else:
        if normalized["data_gate"] != "success":
            failed.append("data_gate")
        if normalized["web_deploy"] != "success":
            failed.append("web_deploy")
    if not failed:
        return []

    detail = " · ".join(
        f"{_WEB_PIPELINE_LABELS[stage]}={normalized[stage]}" for stage in failed)
    return [AlertSignal(
        key="quality:web-pipeline-failure", scope="web_pipeline",
        severity="critical", title="웹 품질 파이프라인 실행 실패",
        detail=(f"{detail}. data_quality_gate 기록이 없을 수 있으므로 "
                "워크플로 로그와 배포 상태를 확인해 주세요."),
        observation_id=str(observation_id).strip(), min_occurrences=1,
    )]


def collection_pipeline_signals(outcome: str | None, *,
                                observation_id: str = "") -> list[AlertSignal]:
    """Expose a collector crash even when no new source snapshot was written."""
    if outcome is None or str(outcome).strip().lower() == "success":
        return []
    normalized = str(outcome).strip().lower() or "missing"
    return [AlertSignal(
        key="source:collection-pipeline-failure", scope="collection_pipeline",
        severity="critical", title="뉴스 수집 파이프라인 실행 실패",
        detail=(f"news_bot step outcome={normalized}. source_yield가 갱신되지 않았을 수 있어 "
                "이전 수집 상태를 정상 관측으로 사용하지 않습니다."),
        observation_id=str(observation_id).strip(), min_occurrences=1,
    )]


def latest_data_gate_record(records: Iterable[Mapping]) -> Mapping | None:
    """Return the newest usable quality record from an append-only log."""
    latest = None
    latest_key = ""
    for row in records:
        if not isinstance(row, Mapping) or row.get("record_type") != "data_quality_gate":
            continue
        key = str(row.get("generated_at") or row.get("date") or "")
        if latest is None or key >= latest_key:
            latest, latest_key = row, key
    return latest


def daily_quality_signals(records: Iterable[Mapping], date: str) -> tuple[list[AlertSignal], set[str]]:
    """Aggregate the day's persisted quality events into alert signals.

    The returned scopes tell :func:`evaluate_alerts` which older alerts may be
    resolved.  A daily data-gate record also closes the curation-failure window:
    if no failures were logged before that point, yesterday's failure alert can
    safely resolve.
    """
    rows = [row for row in records
            if isinstance(row, Mapping) and str(row.get("date") or "") == date]
    signals: list[AlertSignal] = []
    scopes: set[str] = set()

    gates = [row for row in rows if row.get("record_type") == "data_quality_gate"]
    if gates:
        latest = latest_data_gate_record(gates)
        signals.extend(data_gate_signals(latest))
        scopes.update({"data_gate", "curation"})

    failures = [row for row in rows if row.get("record_type") == "curation_failure"]
    if failures:
        scopes.add("curation")
        lost = sum(_nonnegative_int(row.get("lost")) for row in failures)
        reasons: dict[str, int] = {}
        for row in failures:
            raw_reasons = row.get("reasons")
            if not isinstance(raw_reasons, Mapping):
                continue
            for reason, count in raw_reasons.items():
                reasons[str(reason)] = reasons.get(str(reason), 0) + _nonnegative_int(count)
        reason_text = ", ".join(f"{key} {value}건" for key, value in sorted(reasons.items())) or "원인 미분류"
        latest_id = max(str(row.get("generated_at") or "") for row in failures)
        signals.append(AlertSignal(
            key="quality:curation-failure", scope="curation",
            severity="critical" if lost >= 10 else "warning",
            title="기사 큐레이션 유실 발생",
            detail=f"오늘 {len(failures)}회 기록에서 총 {lost}건 유실 · {reason_text}",
            observation_id=f"{date}:{latest_id}:{lost}",
            # A large loss is actionable immediately; a small transient loss
            # must recur on another day before paging an administrator.
            min_occurrences=1 if lost >= 10 else 2,
        ))

    selection_rows = [row for row in rows if row.get("record_type") == "selection_stats"]
    if selection_rows:
        scopes.add("selection")
        latest = max(selection_rows, key=lambda row: str(row.get("generated_at") or ""))
        observation_id = str(latest.get("generated_at") or date)
        regions = [latest.get("domestic"), latest.get("overseas")]
        regions = [row for row in regions if isinstance(row, Mapping)]
        missing = sum(_nonnegative_int(row.get("features_missing")) for row in regions)
        candidates = sum(_nonnegative_int(row.get("candidate_count")) for row in regions)
        selected = sum(_nonnegative_int(row.get("selected_count")) for row in regions)
        if latest.get("pipeline_status") not in (None, "ok"):
            signals.append(AlertSignal(
                key="quality:brief-partial", scope="selection", severity="critical",
                title="일일 브리핑 일부 처리 실패",
                detail=(f"상태 {latest.get('pipeline_status')} · 후보 {candidates}건 중 "
                        f"{selected}건 선정"),
                observation_id=observation_id, min_occurrences=1,
            ))
        if missing:
            signals.append(AlertSignal(
                key="quality:features-missing", scope="selection",
                title="랭킹 분석값 누락",
                detail=f"선정 통계에서 features 누락 {missing}건이 확인됐습니다.",
                observation_id=observation_id, min_occurrences=2,
            ))
        if candidates and not selected:
            signals.append(AlertSignal(
                key="quality:empty-selection", scope="selection", severity="critical",
                title="후보는 있으나 브리핑 선정 0건",
                detail=f"후보 {candidates}건이 있었지만 최종 선정이 비었습니다.",
                observation_id=observation_id, min_occurrences=1,
            ))

    # Extension contract for quality gates owned by other modules.  They can
    # append a record without coupling this monitor to their internal schema.
    explicit = [row for row in rows if row.get("record_type") == "quality_event"]
    if explicit:
        scopes.add("quality_event")
    for row in explicit:
        key = str(row.get("alert_key") or "").strip()
        title = str(row.get("title") or "").strip()
        if not key or not title or row.get("active") is False:
            continue
        signals.append(AlertSignal(
            key=f"quality-event:{key}", scope="quality_event", title=title,
            detail=str(row.get("detail") or ""), severity=str(row.get("severity") or "warning"),
            observation_id=str(row.get("generated_at") or date),
            min_occurrences=max(1, _nonnegative_int(row.get("min_occurrences")) or 2),
        ))

    return signals, scopes


def _empty_alert_state() -> dict:
    return {"version": STATE_VERSION, "items": {}}


def evaluate_alerts(signals: Sequence[AlertSignal], previous: Mapping | None,
                    *, evaluated_scopes: set[str] | None = None,
                    now: datetime | None = None,
                    cooldown: timedelta = DEFAULT_ALERT_COOLDOWN) -> tuple[dict, list[AlertSignal]]:
    """Update alert streaks and return alerts due for notification.

    The same ``observation_id`` never advances a streak twice, which matters
    when a workflow is retried against the same metrics record.
    """
    now_dt = _utc_now(now)
    now_iso = _iso(now_dt)
    previous_items = previous.get("items") if isinstance(previous, Mapping) else {}
    previous_items = previous_items if isinstance(previous_items, Mapping) else {}
    items = {str(key): dict(value) for key, value in previous_items.items()
             if isinstance(value, Mapping)}

    normalized = [signal.normalized() for signal in signals if signal.key]
    by_key = {signal.key: signal for signal in normalized}
    scopes = set(evaluated_scopes or {signal.scope for signal in normalized})

    # Absence resolves only scopes the caller says it evaluated.  This prevents
    # a source-only crawler run from accidentally resolving data-quality alerts.
    for key, row in items.items():
        if row.get("scope") in scopes and key not in by_key and row.get("active"):
            row["active"] = False
            row["consecutive"] = 0
            row["resolved_at"] = now_iso

    due: list[AlertSignal] = []
    for key, signal in by_key.items():
        row = dict(items.get(key) or {})
        same_observation = bool(signal.observation_id) and signal.observation_id == row.get("last_observation_id")
        was_active = bool(row.get("active"))
        if not same_observation:
            row["consecutive"] = (_nonnegative_int(row.get("consecutive")) + 1) if was_active else 1
        row.update({
            "scope": signal.scope,
            "active": True,
            "title": signal.title,
            "detail": signal.detail,
            "severity": signal.severity,
            "last_seen_at": now_iso,
            "last_observation_id": signal.observation_id,
            "min_occurrences": signal.min_occurrences,
        })
        if not was_active:
            row["first_seen_at"] = now_iso
            row.pop("resolved_at", None)

        last_notified = _parse_time(row.get("last_notified_at"))
        cooldown_elapsed = last_notified is None or now_dt - last_notified >= cooldown
        escalated = (_SEVERITY_RANK.get(signal.severity, 1) >
                     _SEVERITY_RANK.get(str(row.get("last_notified_severity") or "info"), 0))
        if _nonnegative_int(row.get("consecutive")) >= signal.min_occurrences and (
                last_notified is None or cooldown_elapsed or escalated):
            due.append(signal)
            # Keep the payload retryable even if the underlying condition
            # recovers before an administrator channel becomes available.
            row["pending_notification"] = True
        items[key] = row

    due_keys = {signal.key for signal in due}
    for key, row in items.items():
        if key in due_keys or not row.get("pending_notification"):
            continue
        due.append(AlertSignal(
            key=key,
            scope=str(row.get("scope") or "quality"),
            title=str(row.get("title") or key),
            detail=str(row.get("detail") or ""),
            severity=str(row.get("severity") or "warning"),
            observation_id=str(row.get("last_observation_id") or ""),
            min_occurrences=max(1, _nonnegative_int(row.get("min_occurrences")) or 1),
        ).normalized())

    return {"version": STATE_VERSION, "updated_at": now_iso, "items": items}, due


def mark_notified(state: Mapping, alerts: Sequence[AlertSignal],
                  now: datetime | None = None) -> dict:
    """Record successful delivery.  Call only after the sender succeeds."""
    result = {"version": STATE_VERSION, "updated_at": _iso(now), "items": {}}
    raw_items = state.get("items") if isinstance(state, Mapping) else {}
    if isinstance(raw_items, Mapping):
        result["items"] = {str(key): dict(value) for key, value in raw_items.items()
                           if isinstance(value, Mapping)}
    sent_at = _iso(now)
    for signal in alerts:
        row = result["items"].get(signal.key)
        if row is None:
            continue
        row["last_notified_at"] = sent_at
        row["last_notified_severity"] = signal.severity
        row["notifications"] = _nonnegative_int(row.get("notifications")) + 1
        row["pending_notification"] = False
    return result


def format_admin_alerts(alerts: Sequence[AlertSignal], *, max_chars: int = 3500) -> str:
    """Build one plain-text Telegram-ready message for all due alerts."""
    lines = [f"⚠️ Nuclens+ 운영 품질 알림 ({len(alerts)}건)"]
    for signal in sorted(alerts, key=lambda row: -_SEVERITY_RANK.get(row.severity, 1)):
        icon = "🚨" if signal.severity == "critical" else "•"
        block = f"\n{icon} {signal.title}\n  {signal.detail}"
        if len("\n".join(lines)) + len(block) > max_chars:
            lines.append("\n• 나머지 경고는 실행 로그에서 확인해 주세요.")
            break
        lines.append(block)
    return "\n".join(lines)[:max_chars]


def notify_alerts(state: Mapping, alerts: Sequence[AlertSignal],
                  sender: Callable[[str], object], *,
                  now: datetime | None = None) -> tuple[dict, dict]:
    """Send one aggregate message without allowing notification failure to raise.

    This function deliberately accepts an injected callable.  Production can
    pass a thin Telegram adapter; tests and local analysis never need secrets or
    network access.
    """
    if not alerts:
        return dict(state), {"sent": False, "count": 0, "error": ""}
    message = format_admin_alerts(alerts)
    try:
        response = sender(message)
        if isinstance(response, Mapping) and response.get("ok") is False:
            raise RuntimeError(str(response.get("description") or "sender returned ok=false"))
    except Exception as exc:  # monitoring must never break the main job
        return dict(state), {
            "sent": False, "count": len(alerts),
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "message": message,
        }
    return mark_notified(state, alerts, now=now), {
        "sent": True, "count": len(alerts), "error": "", "message": message,
    }


def alert_debug_rows(alerts: Sequence[AlertSignal]) -> list[dict]:
    """JSON-friendly representation for logs and dry runs."""
    return [asdict(alert.normalized()) for alert in alerts]
