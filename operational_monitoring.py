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

운영자에게 나가는 문장은 네 조각으로 나뉜다 — 무슨 일이 있었나(``title`` ·
``detail``), 서비스 영향(``impact``), 내가 할 일(``action``), 그리고 마지막에
기술 상세(``technical``).  표시 등급(``level``)은 심각도(``severity``)와 다른
축이다: severity 는 얼마나 급한가를, level 은 **사람이 손을 대야 하는가**를
말한다.  자동으로 걸러진 품질 관리가 ``critical`` 로 찍혀 수집이 죽은 것과 같은
모양으로 나가던 것이 이 분리의 이유다.

같은 상태를 회차마다 다시 알리지 않도록 ``fingerprint`` 를 둔다.  값이 있으면
쿨다운이 지나도 **지문이 달라졌을 때만** 다시 부른다.  지문은 반복을 줄이기만
하며, 지문이 달라졌다고 쿨다운을 건너뛰지는 않는다.
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


# ── 운영자 등급 ─────────────────────────────────────────────────────────────
#
# ``severity`` 는 그대로 둔다 — 중복 억제·에스컬레이션이 쓰는 **내부** 순위이고
# 상태 파일에 이미 쌓여 있다. 문제는 그 값이 운영자 화면에도 그대로 나왔다는
# 것이다. 자동으로 잘 걸러진 품질 관리(아카이브 격리)가 ``critical`` 로 찍혀
# 수집이 죽은 것과 같은 모양이 됐다(실측 2026-08-22 sent.json).
#
# 운영자가 5초 안에 얻어야 하는 답은 심각도가 아니라 **내가 할 일이 있나** 다.
# 그래서 표시 등급을 따로 둔다. severity 는 얼마나 급한가, level 은 누가 손을
# 대야 하는가를 말한다 — 둘은 같은 축이 아니다.
LEVEL_ACTION = "action"        # 🚨 조치 필요 — 자동 복구되지 않은 실제 실패
LEVEL_ATTENTION = "attention"  # ⚠️ 확인 필요 — 자동 처리됨, 서비스는 정상
LEVEL_INFO = "info"            # ℹ️ 정보
LEVEL_RESOLVED = "resolved"    # ✅ 해결됨 — 스스로 정상으로 돌아왔다

_LEVEL_LABELS = {
    LEVEL_ACTION: ("🚨", "조치 필요"),
    LEVEL_ATTENTION: ("⚠️", "확인 필요"),
    LEVEL_INFO: ("ℹ️", "정보"),
    LEVEL_RESOLVED: ("✅", "해결됨"),
}
# 읽는 순서 = 급한 순서. 조치할 것이 첫 화면에 있어야 스크롤이 필요 없다.
_LEVEL_ORDER = (LEVEL_ACTION, LEVEL_ATTENTION, LEVEL_INFO, LEVEL_RESOLVED)

# 같은 뜻을 열 군데에서 다르게 쓰면 운영자는 매번 다시 읽는다. 자주 쓰는 문장만
# 여기 모은다 — 상황별 문장은 그 상황을 아는 곳에서 쓰는 편이 정확하다.
IMPACT_NONE = "없음 — 뉴스 수집과 서비스는 정상입니다."
ACTION_NONE = "필요 없음 — 자동으로 처리됐습니다."
ACTION_WATCH = "필요 없음 — 같은 알림이 계속 늘어나면 확인해 주세요."


def default_level(severity: str) -> str:
    """등급을 적지 않은 알림의 기본값. 옛 호출부는 이 규칙으로 그대로 산다."""
    if severity == "critical":
        return LEVEL_ACTION
    if severity == "info":
        return LEVEL_INFO
    return LEVEL_ATTENTION


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
    """운영자 한 명이 읽는 한 건의 알림.

    필드 순서는 **의미 순서**다 — 무슨 일이(title/detail), 서비스에 영향이
    있는지(impact), 내가 할 일이 있는지(action), 그리고 마지막에 기술
    상세(technical). 새 필드는 전부 뒤에 붙였으므로 위치 인자로 만드는 기존
    호출부는 그대로 동작한다.
    """

    key: str
    scope: str
    title: str
    detail: str
    severity: str = "warning"
    observation_id: str = ""
    min_occurrences: int = 2
    # 아래 넷이 운영자 문장이다. 비워 두면 옛 동작(제목+상세만)으로 렌더링된다.
    impact: str = ""
    action: str = ""
    technical: str = ""
    level: str = ""
    # 상태 동일성 지문. 이 값이 있으면 **내용이 달라졌을 때만** 다시 알린다.
    # 비어 있으면 예전처럼 쿨다운마다 다시 알린다(진행 중인 장애의 기본값).
    fingerprint: str = ""

    def normalized(self) -> "AlertSignal":
        severity = self.severity if self.severity in _SEVERITY_RANK else "warning"
        level = self.level if self.level in _LEVEL_LABELS else default_level(severity)
        return AlertSignal(
            key=str(self.key).strip(),
            scope=str(self.scope or "quality").strip(),
            title=str(self.title).strip()[:120],
            detail=str(self.detail).strip()[:700],
            severity=severity,
            observation_id=str(self.observation_id).strip(),
            min_occurrences=max(1, int(self.min_occurrences or 1)),
            impact=str(self.impact or "").strip()[:300],
            action=str(self.action or "").strip()[:300],
            technical=str(self.technical or "").strip()[:700],
            level=level,
            fingerprint=str(self.fingerprint or "").strip()[:200],
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
                    severity="warning", level=LEVEL_ATTENTION,
                    title=f"{name} 기사 목록을 일부만 읽었습니다",
                    detail=(f"{kind_label} 목록을 읽는 중 오류가 나서 항목 일부만 "
                            f"받았습니다. 연속 {bozo}회째입니다."),
                    impact="이 출처의 일부 기사가 빠질 수 있습니다. 다른 출처와 서비스는 정상입니다.",
                    action="반복되면 해당 사이트 형식이 바뀐 것입니다 — 수집 설정을 확인해 주세요.",
                    technical=(f"consecutive_bozo={bozo} "
                               f"usable={raw.get('last_usable')}/{raw.get('last_entries')} "
                               f"exception={raw.get('last_bozo_exception') or 'n/a'}"),
                    observation_id=observation_id, min_occurrences=1,
                ))
            elif unusable >= unusable_threshold:
                out.append(AlertSignal(
                    key=f"source:{name}:unusable", scope="source", severity="warning",
                    level=LEVEL_ATTENTION,
                    title=f"{name} 기사 목록을 읽지 못했습니다",
                    detail=(f"{kind_label} 목록은 받았는데 쓸 수 있는 기사가 하나도 "
                            f"없습니다. 연속 {unusable}회째로, 사이트 형식이 바뀌었을 "
                            "가능성이 큽니다."),
                    impact="이 출처의 기사가 수집되지 않습니다. 다른 출처와 서비스는 정상입니다.",
                    action="해당 사이트 형식이 바뀌었는지 확인해 주세요.",
                    technical=(f"consecutive_unusable={unusable} "
                               f"usable=0/{raw.get('last_entries')}"),
                    observation_id=observation_id, min_occurrences=1,
                ))
            quiet = _days_since(raw.get("last_newest_pub"), now)
            if quiet is not None and quiet >= stale_days:
                out.append(AlertSignal(
                    key=f"source:{name}:stale", scope="source", severity="warning",
                    level=LEVEL_ATTENTION,
                    title=f"{name}에 새 기사가 {quiet}일째 없습니다",
                    detail=("수집 자체는 성공하고 있습니다. 그 사이트에 새 글이 "
                            "올라오지 않았거나, 새 글을 목록에서 못 찾고 있습니다."),
                    impact=IMPACT_NONE + " 이 출처의 새 기사만 없습니다.",
                    action="해당 사이트에 실제로 새 글이 있는지 한 번만 확인해 주세요.",
                    technical=(f"newest_pub={raw.get('last_newest_pub')} quiet_days={quiet} "
                               f"threshold={stale_days} observed_normal_gap_max=5"),
                    # 하루가 지나도 같은 '무소식'이다. 날짜가 아니라 **마지막 기사**를
                    # 지문으로 쓴다 — 새 글이 올라오면 알림 자체가 사라지고, 그때까지는
                    # 같은 사실을 매일 다시 말하지 않는다.
                    fingerprint=f"newest={raw.get('last_newest_pub')}",
                    observation_id=observation_id, min_occurrences=1,
                ))

        if failures >= failure_threshold:
            severity = "critical" if raw.get("kind") == "official" and failures >= 3 else "warning"
            out.append(AlertSignal(
                key=f"source:{name}:failure", scope="source", severity=severity,
                # 이것은 자동으로 낫지 않는다 — 한 출처의 기사가 실제로 빠지고 있다.
                level=LEVEL_ACTION if severity == "critical" else LEVEL_ATTENTION,
                title=f"{name} 기사를 가져오지 못하고 있습니다",
                detail=f"{kind_label} 접속이 연속 {failures}회 실패했습니다.",
                impact="이 출처의 기사만 브리핑·사이트에서 빠집니다. 다른 출처는 정상 수집됩니다.",
                action=("해당 사이트가 열리는지 확인해 주세요. 사이트 쪽 장애라면 "
                        "복구되는 대로 자동으로 다시 수집합니다."
                        if severity == "critical" else
                        "다음 회차에 자동 재시도합니다. 계속되면 확인해 주세요."),
                technical=(f"consecutive_failures={failures} "
                           f"last_error={raw.get('last_error') or 'n/a'}"),
                observation_id=observation_id, min_occurrences=1,
            ))
        elif empties >= empty_threshold:
            out.append(AlertSignal(
                key=f"source:{name}:empty", scope="source", severity="warning",
                level=LEVEL_ATTENTION,
                title=f"{name} 수집 결과가 계속 0건입니다",
                detail=(f"접속은 되는데 연속 {empties}회 기사가 하나도 없었습니다. "
                        "실제 무소식일 수도, 목록 형식이 바뀐 것일 수도 있습니다."),
                impact=IMPACT_NONE + " 이 출처의 새 기사만 없습니다.",
                action="해당 사이트에 새 글이 있는지 확인해 주세요.",
                technical=f"consecutive_empty={empties} last_count=0",
                observation_id=observation_id, min_occurrences=1,
            ))
    return out


def _sample_hashes(rows: object) -> set[str]:
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes)):
        return set()
    return {str(row.get("hash"))[:12] for row in rows
            if isinstance(row, Mapping) and row.get("hash")}


def count_fingerprint(label: str, count: object) -> str:
    """건수가 흔들리는 알림의 지문. **자릿수**만 본다.

    자동 처리되는 품질 이벤트는 회차마다 건수가 조금씩 다르다(실측 2026-08-22
    하루: 2 · 4 · 5건). 건수를 그대로 지문에 넣으면 사실상 회차마다 다시 알리는
    것과 같아진다. 반대로 건수를 아예 빼면 2건이 500건이 돼도 조용하다.

    그래서 크기의 **자릿수**를 쓴다. 임계값을 새로 정하지 않으면서 "평소와 같은
    규모"와 "규모가 달라졌다"를 가른다. 심각도가 오르는 경우는 지문과 무관하게
    에스컬레이션이 이미 즉시 알린다.
    """
    try:
        value = max(0, int(count))
    except (TypeError, ValueError):
        value = 0
    return f"{label}~{len(str(value))}"


def _digest(values: Iterable[str]) -> str:
    """지문에 넣을 짧은 요약. **해시 목록을 그대로 이어 붙이면 안 된다** —
    지문은 200자에서 잘리므로, 잘린 뒤쪽에서 바뀐 항목이 조용히 묻힌다."""
    payload = "|".join(sorted(values))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def archive_quarantine_split(current: Mapping | None,
                             previous: Mapping | None) -> tuple[int, int]:
    """격리 건수를 **이번에 새로 생긴 것**과 **이전부터 유지되는 것**으로 나눈다.

    무결성 게이트는 매 빌드마다 아카이브 **전체**를 다시 훑는다
    (``build_data.apply_archive_integrity_gate``). 그래서 "격리 21건"은 오늘 21건이
    새로 생겼다는 뜻이 아니라 같은 21건이 계속 걸러지고 있다는 뜻인데, 알림은 그
    둘을 구분하지 못해 매번 새 사고처럼 보였다(실측 2026-08-20~22: 같은 21건에
    5회 통지).

    표본 해시는 20건까지만 남으므로 해시 비교만으로는 모자란다. 증가분과 표본에서
    새로 보인 해시 수 중 **큰 쪽**을 새로 생긴 것으로 본다 — 새 문제를 조용히
    넘기는 쪽으로는 틀리지 않는다.
    """
    total = _nonnegative_int((current or {}).get("quarantined"))
    if not isinstance(previous, Mapping):
        return total, 0
    before = _nonnegative_int(previous.get("quarantined"))
    fresh_hashes = _sample_hashes((current or {}).get("quarantine_samples"))
    old_hashes = _sample_hashes(previous.get("quarantine_samples"))
    # 앞 회차에 표본이 아예 없으면 해시 비교는 **전부 새것**이라고 말한다 —
    # 그건 비교가 아니라 정보 부재다. 그때는 건수 차이만 믿는다.
    by_hash = len(fresh_hashes - old_hashes) if old_hashes else 0
    new_count = max(total - before, by_hash)
    new_count = max(0, min(total, new_count))
    return new_count, total - new_count


def data_gate_signals(record: Mapping | None,
                      previous: Mapping | None = None) -> list[AlertSignal]:
    """Translate one ``data_quality_gate`` record into actionable signals.

    ``previous`` is the preceding gate record when one exists.  It only tells a
    *new* problem from one that is already known and still handled
    automatically; judgement thresholds stay in the modules that own them.
    """
    if not isinstance(record, Mapping):
        return []
    observation_id = str(record.get("observation_id") or
                         record.get("generated_at") or record.get("date") or "")
    out = []
    archive_quality = record.get("archive_quality")
    archive_quality = archive_quality if isinstance(archive_quality, Mapping) else {}
    quarantined = _nonnegative_int(archive_quality.get("quarantined"))
    sanitized = _nonnegative_int(archive_quality.get("sanitized"))
    if quarantined or sanitized:
        previous_archive = (previous.get("archive_quality")
                            if isinstance(previous, Mapping) else None)
        previous_archive = (previous_archive
                            if isinstance(previous_archive, Mapping) else None)
        new_quarantine, kept_quarantine = archive_quarantine_split(
            archive_quality, previous_archive)
        samples = (list(archive_quality.get("quarantine_samples") or []) +
                   list(archive_quality.get("sanitize_samples") or []))
        hashes = ", ".join(sorted(_sample_hashes(samples))[:5])
        if quarantined:
            title = "신뢰하기 어려운 아카이브 기사를 자동 제외했습니다"
            counted = (f"제외 {quarantined}건" if previous_archive is None else
                       f"새로 제외 {new_quarantine}건 · 기존 제외 유지 {kept_quarantine}건")
            detail = f"원문과 내용이 다른 기사를 사이트 출력에서 뺐습니다 — {counted}."
            if sanitized:
                detail += f" 사건일이 잘못된 기사 {sanitized}건은 날짜만 자동 정정했습니다."
        else:
            title = "아카이브 기사 날짜를 자동으로 정정했습니다"
            detail = (f"사건일이 잘못 적힌 기사 {sanitized}건의 날짜만 비우고 "
                      "나머지 내용은 그대로 내보냈습니다.")
        out.append(AlertSignal(
            # 격리와 정제는 같은 무결성 사고의 강도 차이다. 키를 하나로
            # 유지하면 정제 경고 뒤 격리가 생겼을 때 severity escalation은
            # 즉시 알리면서, 워크플로 재시도는 같은 사고로 중복 발송하지 않는다.
            key="quality:archive-integrity", scope="data_gate",
            severity="critical" if quarantined else "warning",
            # 사고가 아니라 **품질 관리가 제대로 돈 결과**다. severity 는 내부
            # 에스컬레이션 계약이라 그대로 두고, 운영자에게 보이는 등급만 나눈다.
            level=LEVEL_ATTENTION,
            title=title, detail=detail,
            impact="없음 — 제외된 기사만 사이트에서 빠지고, 나머지 뉴스와 서비스는 정상입니다.",
            action=ACTION_NONE + " 매 빌드에서 다시 검사합니다.",
            technical=(f"checked={_nonnegative_int(archive_quality.get('checked'))} "
                       f"quarantined={quarantined} sanitized={sanitized} "
                       f"samples={hashes or 'see build log'}"),
            # **정제 건수는 지문에 넣지 않는다.** 아카이브가 커지면 날짜 정정은
            # 매일 늘어난다(실측 6→40→72→100→142). 그것까지 지문에 넣으면 새로
            # 생긴 것이 없는 날에도 같은 알림이 매일 다시 나간다.
            fingerprint=(f"q={quarantined}:"
                         + _digest(_sample_hashes(
                             archive_quality.get("quarantine_samples")))),
            observation_id=observation_id, min_occurrences=1,
        ))
    tracking = record.get("tracking")
    if isinstance(tracking, Mapping) and tracking.get("applicable") and tracking.get("below_target"):
        out.append(AlertSignal(
            key="quality:tracking-rate", scope="data_gate", level=LEVEL_ATTENTION,
            title="이슈 추적률이 기준을 밑돕니다",
            detail=("같은 이슈로 이어 붙는 기사 비율이 목표보다 낮습니다. "
                    "뉴스가 한산한 기간에도 나타나는 값입니다."),
            impact=IMPACT_NONE + " 사이트의 이슈 묶음이 평소보다 잘게 나뉠 수 있습니다.",
            action=ACTION_WATCH,
            technical=(f"rate={tracking.get('rate')} target={tracking.get('target')} "
                       f"window_briefings={tracking.get('window_briefings')}"),
            # 값은 회차마다 흔들리지만 '기준 미달'이라는 사실은 그대로다.
            fingerprint="below-target",
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
    #
    # 문구만 여기서 감싼다. guardrails 의 제목·상세는 컷 값과 보존율로 쓰인
    # **개발자 문장**이라("컷을 올려야 한다") 운영자가 그것으로 할 수 있는 일이
    # 없다. 조치할 수 없는 알림이 섞이면 결국 전체가 안 읽힌다.
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
                key=key, scope="data_gate", severity=severity, level=LEVEL_ATTENTION,
                title="이슈 묶음 정확도 점검 항목이 있습니다",
                detail=str(guard.get("title") or "이슈 병합 후보 감시"),
                impact=IMPACT_NONE + " 이슈를 묶는 정확도만 영향을 받습니다.",
                action=("개발자 확인이 필요합니다 — 서비스 중단은 아닙니다."
                        if severity == "critical" else
                        "운영 조치는 필요 없습니다 — 개발자 확인 항목입니다."),
                technical=f"[{key}] {guard.get('detail') or ''}",
                # 보존율·컷 여유는 회차마다 소수점이 움직인다. 같은 항목이 같은
                # 심각도로 계속 걸려 있는 것은 새 소식이 아니다.
                fingerprint=f"guard:{severity}",
                observation_id=observation_id,
                min_occurrences=1 if severity == "critical" else 2,
            ))

    weeks = record.get("topic_weeks")
    if isinstance(weeks, Mapping):
        hidden = []
        # A missing ratio means there is not enough data to judge; it is not an
        # operational defect and should not page an administrator.
        if weeks.get("flow_ratio") is not None and not weeks.get("flow_visible", True):
            hidden.append("주제 흐름 표")
        if weeks.get("slope_ratio") is not None and not weeks.get("slope_visible", True):
            hidden.append("슬로프 그래프")
        if hidden:
            out.append(AlertSignal(
                key="quality:topic-weeks", scope="data_gate", level=LEVEL_ATTENTION,
                title="신뢰하기 어려운 추세 지표를 자동으로 숨겼습니다",
                detail=(f"주별 수집량 차이가 커서 {' · '.join(hidden)}를 화면에서 "
                        "자동으로 내렸습니다. 잘못된 추세를 보여 주지 않으려는 "
                        "안전장치가 작동한 것입니다."),
                impact=IMPACT_NONE + " 사이트의 다른 화면은 그대로 보입니다.",
                action="필요 없음 — 주별 수집량이 고르게 쌓이면 자동으로 다시 표시됩니다.",
                technical=(f"flow_ratio={weeks.get('flow_ratio')} "
                           f"slope_ratio={weeks.get('slope_ratio')} "
                           f"threshold={weeks.get('limit')} totals={weeks.get('totals')}"),
                # 비율은 매일 조금씩 움직인다(실측 2.5098→2.549→2.8163→2.5536). 그
                # 숫자를 지문에 넣으면 **숨은 지표가 그대로인데도** 매일 다시 알린다.
                # 실제로 달라지는 것은 '무엇이 숨었나' 뿐이다.
                fingerprint="hidden=" + ",".join(hidden),
                observation_id=observation_id, min_occurrences=2,
            ))
    return out


# 실패한 step 하나가 서비스에 무엇을 하는지는 step 마다 다르다. 셋을 한 문장으로
# 묶으면("웹 품질 파이프라인 실행 실패") 운영자는 사이트가 멈춘 것인지 지표만
# 빈 것인지 알 수 없다 — 그 판단이 이 알림의 존재 이유다.
_WEB_PIPELINE_LABELS = {
    "web_build": "웹 데이터 빌드",
    "data_gate": "데이터 품질 기록",
    "web_deploy": "Cloudflare 배포·스모크",
}
_WEB_PIPELINE_STAGES = {
    "web_build": {
        "title": "사이트에 올릴 데이터를 만들지 못했습니다",
        "impact": ("사이트가 이전 데이터 그대로 남습니다. 텔레그램 브리핑 발송은 "
                   "영향받지 않습니다."),
        "action": "워크플로 로그를 확인해 주세요. 다음 예약 실행에서 자동 재시도합니다.",
        "serving": True,
    },
    "web_deploy": {
        "title": "사이트 배포가 실패했습니다",
        "impact": ("새 데이터가 사이트에 반영되지 않았습니다. 텔레그램 브리핑은 "
                   "정상 발송됩니다."),
        "action": "배포 로그와 사이트 접속을 확인해 주세요.",
        "serving": True,
    },
    "data_gate": {
        "title": "오늘치 품질 기록을 남기지 못했습니다",
        "impact": "없음 — 수집·발송·사이트는 정상입니다. 품질 지표만 오늘치가 비어 있습니다.",
        "action": ACTION_WATCH,
        "serving": False,
    },
}


# 물어본 적 없는 단계는 실패가 아니다.
#
# `data_gate` 는 `data_gate_metrics.py` 를 부르는 **daily-brief 전용** 스텝이다.
# crawl.yml 에는 그 스텝 자체가 없어 `--data-gate-outcome` 을 넘기지 않는데,
# 안 넘어온 값이 `missing` 으로 채워지고 `!= "success"` 에 걸렸다. 그래서 크롤이
# 보내는 웹 알림에는 실재하지 않는 단계가 매번 실패로 끼어 있었다 — 실측
# 2026-09-04 run 33833880969: 배포가 잡 제한에 잘린 진짜 원인 옆에
# `데이터 품질 기록=missing` 이 나란히 붙어 나갔다.
#
# 그 문구는 두 번 해롭다. 없는 단계를 찾아보게 만들고, 진짜 원인 하나를 둘로
# 흐린다 — 이 모듈이 세 단계를 굳이 따로 부르는 이유가 그 반대였다.
#
# 반대로 `web_build`·`web_deploy` 는 **필수**다. 그 값이 안 넘어오면 그것 자체가
# 배선 사고이고, 조용해지는 쪽이 훨씬 나쁘다 — 이 알림이 생긴 이유가 웹이
# 깨졌는데 알림이 0건이던 2026-09-01 회차다.
_WEB_PIPELINE_OPTIONAL_STAGES = frozenset({"data_gate"})
# `skipped` 는 조건이 걸러 냈다는 뜻이고 `missing` 은 물어본 적이 없다는 뜻이다.
# 선택 단계에서는 둘 다 '이 회차엔 해당 없음'이다 (tools/failure_domains.py 와
# 같은 구분을 쓴다).
_WEB_PIPELINE_NOT_RUN = frozenset({"skipped", "missing"})


def _stage_failed(stage: str, normalized: Mapping[str, str]) -> bool:
    outcome = normalized[stage]
    if outcome == "success":
        return False
    if stage in _WEB_PIPELINE_OPTIONAL_STAGES and outcome in _WEB_PIPELINE_NOT_RUN:
        return False
    return True


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
    if _stage_failed("web_build", normalized):
        failed.append("web_build")
    else:
        if _stage_failed("data_gate", normalized):
            failed.append("data_gate")
        if _stage_failed("web_deploy", normalized):
            failed.append("web_deploy")
    if not failed:
        return []

    # 제목·영향은 **서비스에 닿는** step 을 먼저 말한다. 지표 기록만 실패한 날에
    # "배포 실패"라고 부르면 다음에 진짜 배포가 죽었을 때 안 읽힌다.
    lead = next((stage for stage in ("web_build", "web_deploy", "data_gate")
                 if stage in failed), failed[0])
    spec = _WEB_PIPELINE_STAGES[lead]
    serving = any(_WEB_PIPELINE_STAGES[stage]["serving"] for stage in failed)
    detail = " · ".join(
        f"{_WEB_PIPELINE_LABELS[stage]}={normalized[stage]}" for stage in failed)
    return [AlertSignal(
        key="quality:web-pipeline-failure", scope="web_pipeline",
        severity="critical" if serving else "warning",
        level=LEVEL_ACTION if serving else LEVEL_ATTENTION,
        title=spec["title"],
        detail=("자동 실행이 도중에 멈췄습니다."
                if serving else "자동 실행 중 한 단계가 끝나지 못했습니다."),
        impact=spec["impact"],
        action=spec["action"],
        # data_quality_gate 안내는 **그 단계가 실제로 실패한 회차에만** 붙인다.
        # 늘 붙이면 그 기록이 아예 없는 crawl 알림에까지 따라와, 운영자가 없는
        # 파일을 찾게 된다.
        technical=(f"{detail}. "
                   + ("data_quality_gate 기록이 없을 수 있으므로 "
                      if "data_gate" in failed else "")
                   + "워크플로 로그와 배포 상태를 확인해 주세요."),
        observation_id=str(observation_id).strip(), min_occurrences=1,
    )]


# 빌드가 **끝까지 돌았는데** 결과가 온전하지 않은 상태다. 실패도 정상도 아니다.
#
# `web/build_data.py` 는 서로 다른 두 클러스터가 같은 issue_id 를 들고 나오면
# (실측 2026-09-01: 팰리세이즈 vs 자포리자) 국소 건수에 한해 기사 해시 기반
# fallback ID 로 갈라 놓고 빌드를 계속한다 — 그 결과가 `build_mode=degraded` 다.
# 사이트는 서고 브리핑도 나가지만 그 이슈들은 검토를 못 받은 카드로 떠 있다.
#
# 이 신호가 없으면 degraded 는 워크플로 어디에도 안 나타난다. meta.json/status.json
# 안에만 있고 그 파일을 여는 사람은 없다 — 즉 `ok` 와 구별되지 않는다. 그래서
# **격리 건수를 지문으로** 둔다: 같은 오염이 이어지는 동안은 3시간마다 다시
# 울리지 않고, 건수가 움직일 때만 다시 알린다.
_BUILD_MODE_DEGRADED = "degraded"


def web_identity_signals(build_mode: str | None, *, quarantined_count: int = 0,
                         observation_id: str = "") -> list[AlertSignal]:
    """Surface a build that completed with quarantined identities.

    A degraded build is not a failure: the deploy proceeds and the briefing is
    unaffected.  It is also not ``ok``: some clusters are shown under a fallback
    ID that no reviewer has confirmed.  Reporting it as either one loses the
    distinction the identity architecture exists to make.
    """
    if build_mode is None:
        return []
    normalized = str(build_mode).strip().lower()
    if normalized != _BUILD_MODE_DEGRADED:
        return []
    count = max(0, int(quarantined_count or 0))
    return [AlertSignal(
        key="quality:web-identity-degraded", scope="web_identity",
        severity="warning", level=LEVEL_ATTENTION,
        title="같은 ID 를 쓰던 이슈를 자동으로 갈라 놓았습니다",
        detail=(f"서로 다른 사건 {count}건이 같은 이슈 ID 를 물고 있어 "
                "기사별 임시 ID 로 분리했습니다. 잘못 합쳐진 이슈를 그대로 "
                "내보내지 않으려는 안전장치가 작동한 것입니다."),
        impact=("사이트와 브리핑은 정상입니다. 해당 이슈만 이어짐 기록 없이 "
                "새 카드로 떠 있습니다."),
        action=("필요 없음 — 원인이 사라지면 자동으로 원래 ID 로 돌아옵니다. "
                "같은 건수가 계속 남으면 확인해 주세요."),
        technical=(f"build_mode=degraded quarantined_cluster_count={count}. "
                   "meta.json/status.json 의 identity.events 에 legacy_issue_id 가 있습니다."),
        # 오염이 이어지는 동안 건수는 그대로다. 그 상태가 새 소식은 아니다 —
        # 건수가 움직일 때만 다시 말한다.
        fingerprint=f"quarantined={count}",
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
        severity="critical", level=LEVEL_ACTION,
        title="뉴스 수집 작업이 끝까지 실행되지 못했습니다",
        detail="이번 회차 수집이 도중에 멈췄습니다.",
        impact=("이번 회차에 들어왔어야 할 새 기사가 빠집니다. 이미 발행된 브리핑과 "
                "사이트는 그대로 유지됩니다."),
        action="워크플로 로그를 확인해 주세요. 다음 예약 회차에 자동으로 다시 수집합니다.",
        technical=(f"news_bot step outcome={normalized}. source_yield가 갱신되지 않았을 수 있어 "
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


def previous_data_gate_record(records: Iterable[Mapping],
                              current: Mapping | None) -> Mapping | None:
    """``current`` 바로 앞 회차의 게이트 기록. 없으면 ``None``.

    "새로 생긴 문제"와 "이전부터 자동 처리 중인 문제"를 가르려면 어제의 나가
    필요하다. delivery_log 는 이미 회차마다 이 줄을 쌓고 있으므로 상태 파일을
    새로 만들지 않는다 (data_gate_metrics.previous_candidate_records 와 같은 이유).
    """
    if not isinstance(current, Mapping):
        return None
    current_key = str(current.get("generated_at") or current.get("date") or "")
    current_observation = str(current.get("observation_id") or "")
    best = None
    best_key = ""
    for row in records:
        if not isinstance(row, Mapping) or row.get("record_type") != "data_quality_gate":
            continue
        key = str(row.get("generated_at") or row.get("date") or "")
        if key >= current_key:
            continue
        # 같은 workflow run 의 재시도는 '앞 회차'가 아니다 — 같은 회차다.
        if current_observation and str(row.get("observation_id") or "") == current_observation:
            continue
        if best is None or key > best_key:
            best, best_key = row, key
    return best


def daily_quality_signals(records: Iterable[Mapping], date: str) -> tuple[list[AlertSignal], set[str]]:
    """Aggregate the day's persisted quality events into alert signals.

    The returned scopes tell :func:`evaluate_alerts` which older alerts may be
    resolved.  A daily data-gate record also closes the curation-failure window:
    if no failures were logged before that point, yesterday's failure alert can
    safely resolve.
    """
    records = list(records)
    rows = [row for row in records
            if isinstance(row, Mapping) and str(row.get("date") or "") == date]
    signals: list[AlertSignal] = []
    scopes: set[str] = set()

    gates = [row for row in rows if row.get("record_type") == "data_quality_gate"]
    if gates:
        latest = latest_data_gate_record(gates)
        signals.extend(data_gate_signals(
            latest, previous_data_gate_record(records, latest)))
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
        heavy = lost >= 10
        signals.append(AlertSignal(
            key="quality:curation-failure", scope="curation",
            severity="critical" if heavy else "warning",
            level=LEVEL_ACTION if heavy else LEVEL_ATTENTION,
            title="일부 기사를 정리하지 못하고 넘겼습니다",
            detail=(f"오늘 {lost}건이 요약·분류 단계에서 처리되지 못했습니다."),
            impact=("해당 기사는 오늘 브리핑과 사이트에 나오지 않습니다. 나머지 "
                    "기사와 서비스는 정상입니다."),
            action=("외부 API 한도·키 상태를 확인해 주세요."
                    if heavy else ACTION_WATCH),
            technical=f"records={len(failures)} lost={lost} reasons={reason_text}",
            observation_id=f"{date}:{latest_id}:{lost}",
            # A large loss is actionable immediately; a small transient loss
            # must recur on another day before paging an administrator.
            min_occurrences=1 if heavy else 2,
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
        status = latest.get("pipeline_status")
        if status not in (None, "ok"):
            # ``partial`` 은 daily_brief 가 '브리핑 중 하나 이상이 발송에 실패했다'는
            # 뜻으로만 쓴다. 그 사실을 그대로 말해야 운영자가 무엇을 볼지 안다.
            partial_send = str(status) == "partial"
            signals.append(AlertSignal(
                key="quality:brief-partial", scope="selection", severity="critical",
                level=LEVEL_ACTION,
                title=("일부 브리핑이 발송되지 못했습니다" if partial_send else
                       "일일 브리핑이 끝까지 처리되지 않았습니다"),
                detail=("오늘 준비된 브리핑 중 일부가 텔레그램으로 나가지 못했습니다."
                        if partial_send else "브리핑 처리가 도중에 멈췄습니다."),
                impact=("그 브리핑은 구독자에게 가지 않았습니다. 사이트와 나머지 "
                        "브리핑은 정상입니다."),
                action=("발송 결과를 확인하고 필요하면 워크플로를 다시 실행해 주세요 "
                        "— 재발송 창은 제한돼 있습니다."),
                technical=(f"pipeline_status={status} "
                           f"candidates={candidates} selected={selected}"),
                observation_id=observation_id, min_occurrences=1,
            ))
        if missing:
            signals.append(AlertSignal(
                key="quality:features-missing", scope="selection",
                level=LEVEL_ATTENTION,
                title="일부 기사에서 랭킹 분석값이 비었습니다",
                detail=f"기사 {missing}건이 분석값 없이 순위 계산에 들어갔습니다.",
                impact=IMPACT_NONE + " 순위 정확도만 조금 낮아집니다.",
                action=ACTION_WATCH,
                technical=f"features_missing={missing}",
                fingerprint=count_fingerprint("features-missing", missing),
                observation_id=observation_id, min_occurrences=2,
            ))
        if candidates and not selected:
            signals.append(AlertSignal(
                key="quality:empty-selection", scope="selection", severity="critical",
                level=LEVEL_ACTION,
                title="후보 기사는 있는데 브리핑 선정이 0건입니다",
                detail=f"후보 {candidates}건이 있었지만 최종 선정이 비었습니다.",
                impact="오늘 브리핑이 비어 나갔을 수 있습니다.",
                action="즉시 확인이 필요합니다 — 선정 단계 로그를 봐 주세요.",
                technical=f"candidates={candidates} selected=0",
                observation_id=observation_id, min_occurrences=1,
            ))

    # Extension contract for quality gates owned by other modules.  They can
    # append a record without coupling this monitor to their internal schema.
    # 운영자 문장(impact/action/technical/level)도 같은 계약으로 실어 보낸다 —
    # 무슨 일인지 아는 곳이 그 문장을 쓰는 것이 맞다.
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
            impact=str(row.get("impact") or ""), action=str(row.get("action") or ""),
            technical=str(row.get("technical") or ""), level=str(row.get("level") or ""),
            fingerprint=str(row.get("fingerprint") or ""),
            observation_id=str(row.get("generated_at") or date),
            min_occurrences=max(1, _nonnegative_int(row.get("min_occurrences")) or 2),
        ))

    return signals, scopes


def _empty_alert_state() -> dict:
    return {"version": STATE_VERSION, "items": {}}


def _signal_from_row(key: str, row: Mapping) -> AlertSignal:
    """저장된 항목을 다시 알림으로 되살린다(미발송 재시도·해결 통지 공용)."""
    return AlertSignal(
        key=key,
        scope=str(row.get("scope") or "quality"),
        title=str(row.get("title") or key),
        detail=str(row.get("detail") or ""),
        severity=str(row.get("severity") or "warning"),
        observation_id=str(row.get("last_observation_id") or ""),
        min_occurrences=max(1, _nonnegative_int(row.get("min_occurrences")) or 1),
        impact=str(row.get("impact") or ""),
        action=str(row.get("action") or ""),
        technical=str(row.get("technical") or ""),
        level=str(row.get("level") or ""),
        fingerprint=str(row.get("fingerprint") or ""),
    ).normalized()


def _resolution_signal(key: str, row: Mapping) -> AlertSignal:
    """이미 알린 문제가 스스로 사라졌다는 통지.

    없으면 운영자는 어제 받은 🚨 가 아직 살아 있는지 알 수 없다. 상태가 닫혔다는
    말을 듣지 못하면 알림 하나가 끝없이 열린 채로 남는다.
    """
    base = _signal_from_row(key, row)
    return AlertSignal(
        key=key, scope=base.scope, title=base.title,
        detail="이전에 알린 문제가 사라졌습니다. 자동으로 정상으로 돌아왔습니다.",
        severity="info", level=LEVEL_RESOLVED,
        impact=IMPACT_NONE, action="필요 없음.",
        technical=f"resolved_at={row.get('resolved_at') or ''} was={base.severity}",
        observation_id=str(row.get("resolved_at") or base.observation_id),
        min_occurrences=1,
    ).normalized()


def evaluate_alerts(signals: Sequence[AlertSignal], previous: Mapping | None,
                    *, evaluated_scopes: set[str] | None = None,
                    now: datetime | None = None,
                    cooldown: timedelta = DEFAULT_ALERT_COOLDOWN) -> tuple[dict, list[AlertSignal]]:
    """Update alert streaks and return alerts due for notification.

    The same ``observation_id`` never advances a streak twice, which matters
    when a workflow is retried against the same metrics record.

    반복 통지 규칙은 둘로 갈린다.  ``fingerprint`` 가 없는 알림(진행 중인 장애)은
    예전처럼 쿨다운마다 다시 부른다 — 아직 살아 있다는 사실 자체가 소식이다.
    ``fingerprint`` 가 있는 알림은 **그 값이 달라졌을 때만** 다시 부른다.  매
    빌드마다 아카이브 전체를 다시 검사하는 무결성 게이트처럼, 같은 상태가 계속
    관측되는 것이 정상인 알림이 있기 때문이다(실측: 같은 격리 21건이 회차마다
    새 critical 로 5회 통지됐다).
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
            # 알린 적 없는 문제의 '해결'은 소식이 아니다. 미발송분이 남아 있으면
            # 그 원본이 먼저 나가야 하므로 해결 통지를 만들지 않는다.
            if row.get("last_notified_at") and not row.get("pending_notification"):
                row["pending_resolution"] = True

    due: list[AlertSignal] = []
    for key, signal in by_key.items():
        row = dict(items.get(key) or {})
        same_observation = bool(signal.observation_id) and signal.observation_id == row.get("last_observation_id")
        was_active = bool(row.get("active"))
        # 해결됐던 문제가 다시 났다면 그것은 새 사고다 — 쿨다운을 기다리지 않는다.
        recurred = bool(row.get("resolved_at")) and not was_active
        if not same_observation:
            row["consecutive"] = (_nonnegative_int(row.get("consecutive")) + 1) if was_active else 1
        row.update({
            "scope": signal.scope,
            "active": True,
            "title": signal.title,
            "detail": signal.detail,
            "severity": signal.severity,
            "level": signal.level,
            "impact": signal.impact,
            "action": signal.action,
            "technical": signal.technical,
            "fingerprint": signal.fingerprint,
            "last_seen_at": now_iso,
            "last_observation_id": signal.observation_id,
            "min_occurrences": signal.min_occurrences,
        })
        # 다시 살아난 문제는 새 사고다 — 아직 못 보낸 해결 통지는 의미를 잃는다.
        row.pop("pending_resolution", None)
        if not was_active:
            row["first_seen_at"] = now_iso
            row.pop("resolved_at", None)
            # 재발이면 지문이 같아도 다시 알린다. 그러지 않으면 한 번 해결된
            # 문제는 두 번 다시 통지되지 않는다.
            row.pop("last_notified_fingerprint", None)

        last_notified = _parse_time(row.get("last_notified_at"))
        escalated = (_SEVERITY_RANK.get(signal.severity, 1) >
                     _SEVERITY_RANK.get(str(row.get("last_notified_severity") or "info"), 0))
        if last_notified is None or escalated or recurred:
            repeatable = True
        else:
            cooldown_elapsed = now_dt - last_notified >= cooldown
            # 지문은 **줄이기만 한다.** 쿨다운이 지났어도 상태가 그대로면 같은 말을
            # 다시 하지 않는다. 반대로 지문이 달라졌다고 쿨다운을 건너뛰지도 않는다
            # — 그러면 회차마다 건수만 흔들리는 알림이 3시간마다 울린다.
            repeatable = cooldown_elapsed and (
                not signal.fingerprint or
                signal.fingerprint != str(row.get("last_notified_fingerprint") or ""))
        if _nonnegative_int(row.get("consecutive")) >= signal.min_occurrences and repeatable:
            due.append(signal)
            # Keep the payload retryable even if the underlying condition
            # recovers before an administrator channel becomes available.
            row["pending_notification"] = True
        items[key] = row

    due_keys = {signal.key for signal in due}
    for key, row in items.items():
        if key in due_keys or not row.get("pending_notification"):
            continue
        due.append(_signal_from_row(key, row))
        due_keys.add(key)

    for key, row in items.items():
        if key in due_keys or not row.get("pending_resolution"):
            continue
        due.append(_resolution_signal(key, row))

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
        if signal.level == LEVEL_RESOLVED:
            # 해결 통지는 '알림을 보냈다'가 아니라 '닫혔다'는 기록이다.
            # last_notified_* 를 건드리면 다음 재발의 에스컬레이션 판정이 흐려진다.
            row.pop("pending_resolution", None)
            row["resolution_notified_at"] = sent_at
            continue
        row["last_notified_at"] = sent_at
        row["last_notified_severity"] = signal.severity
        row["last_notified_fingerprint"] = signal.fingerprint
        row["notifications"] = _nonnegative_int(row.get("notifications")) + 1
        row["pending_notification"] = False
    return result


def _level_of(signal: AlertSignal) -> str:
    return signal.level if signal.level in _LEVEL_LABELS else default_level(signal.severity)


def format_admin_alerts(alerts: Sequence[AlertSignal], *, max_chars: int = 3500) -> str:
    """운영자 한 명이 5초 안에 읽는 한 통의 메시지.

    순서는 의미 순서다 — ① 무슨 일이 있었나 ② 서비스 영향 ③ 내가 할 일
    ④ 기술 상세.  조치가 필요한 것이 항상 맨 위에 오고, 자동 처리된 것과
    해결된 것이 그 아래에 붙는다.  해시·비율·예외 이름은 마지막 줄에만 나온다.
    """
    grouped: dict[str, list[AlertSignal]] = {level: [] for level in _LEVEL_ORDER}
    for signal in alerts:
        grouped[_level_of(signal)].append(signal)

    counts = [f"{_LEVEL_LABELS[level][1]} {len(grouped[level])}건"
              for level in _LEVEL_ORDER if grouped[level]]
    lead = next((level for level in _LEVEL_ORDER if grouped[level]), LEVEL_INFO)
    lines = [f"{_LEVEL_LABELS[lead][0]} Nuclens+ 운영 알림 · " + " · ".join(counts)]

    for level in _LEVEL_ORDER:
        icon, label = _LEVEL_LABELS[level]
        for signal in sorted(grouped[level],
                             key=lambda row: -_SEVERITY_RANK.get(row.severity, 1)):
            block = [f"\n{icon} {label} · {signal.title}"]
            if signal.detail:
                block.append(f"  {signal.detail}")
            if signal.impact:
                block.append(f"  서비스 영향: {signal.impact}")
            if signal.action:
                block.append(f"  조치: {signal.action}")
            if signal.technical:
                block.append(f"  상세: {signal.technical}")
            text = "\n".join(block)
            if len("\n".join(lines)) + len(text) > max_chars:
                lines.append("\n• 나머지 알림은 실행 로그에서 확인해 주세요.")
                return "\n".join(lines)[:max_chars]
            lines.append(text)
    return "\n".join(lines)[:max_chars]


def format_technical_log(alerts: Sequence[AlertSignal]) -> str:
    """실행 로그용 한 줄씩. 운영자 문장에서 뺀 값은 여기서 전부 보존한다."""
    return "\n".join(
        f"[ops-monitor] alert key={signal.key} level={_level_of(signal)} "
        f"severity={signal.severity} scope={signal.scope} "
        f"observation={signal.observation_id or 'n/a'} "
        f"fingerprint={signal.fingerprint or 'n/a'} :: {signal.technical or signal.detail}"
        for signal in alerts)


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
