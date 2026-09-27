"""하루 한 번 운영 요약 — 자동으로 처리된 일과 할 일을 한 통으로.

왜 이 파일이 있나
-----------------
2026-09-13~27 운영 알림은 58통이었고 그중 사람이 손댈 일은 한 건이었다. 나머지는
"자동으로 처리했습니다"와 그 "해결됨"이 새벽에도 따로따로 울린 것이었다. 운영자는
"기사 목록을 읽는데 오류가 난다는데 정말 문제인가", "근거 없는 문단이 뭔지 설명이
없다", "숫자가 많은데 각각이 뭔지 모르겠다"고 물었다 — 알림이 **무엇이 있었는지는
말하고 그래서 어떤지는 말하지 않았다.**

그래서 급한 것만 즉시 보내고(:mod:`operational_monitoring` 의 ``immediate`` 갈래),
나머지는 여기서 하루 한 번 모은다. 이 요약은

* 첫 줄에 결론(할 일이 있나)을 말하고,
* 기록에 이미 있는 구체 정보 — 기사 제목, 대본에서 뺀 문장과 확인 안 된 사실 — 를
  그대로 보여 주고,
* 각 줄 끝에 '그래서 어떤지'를 붙인다.

판정은 하지 않는다. 판정은 각 모듈이 이미 했고(``delivery_log.jsonl`` ·
``sent.json`` 의 알림 상태 · ``crawl_runs.json``), 여기서는 읽어서 사람 말로 옮긴다.
네트워크를 쓰지 않는 순수 함수라 테스트와 로컬 미리보기(``--digest-preview``)에서
그대로 돈다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

import operational_monitoring as monitor


KST = timezone(timedelta(hours=9))
# 아침 브리핑 워크플로가 끝나면서 보낸다(`--digest`). 그 실행이 빠진 날은 이 시각
# 이후 첫 실행(3시간마다 도는 수집)이 대신 보낸다.
FALLBACK_HOUR_KST = 10
# 개발 점검 항목은 월요일 요약에만 싣는다.
DEV_WEEKDAY = 0
MAX_CHARS = 3800
_WEEKDAYS = "월화수목금토일"

_VARIANT_LABEL = {"expert": "전문가", "fast": "빠른"}
_KIND_PHRASE = {
    "billing": "Gemini 선불 크레딧 소진",
    "spending_cap": "Gemini 월 지출 한도 초과",
    "quota": "Gemini 사용 한도 초과",
    "config": "Gemini 설정 오류",
    "overloaded": "구글 AI 서버 혼잡",
    "timeout": "AI 응답 지연",
    "other": "원인 미확인",
}
# 요약의 '할 일'에 올리는 요약 갈래 항목. 사람이 한 번 봐야 하는 것만.
_TODO_KEYS = frozenset({
    "quality:audio-brief-missing", "quality:audio-brief-undelivered",
    "quality-event:audio-script-unverified",
})
# 품질 이벤트 중 아래에서 따로 풀어 쓰는 것. 나머지는 제목 한 줄로 싣는다.
_DETAILED_EVENTS = frozenset({
    "article-integrity-quarantine", "unverified-fallback-held",
    "audio-script-claim-removed", "audio-script-unverified",
})


# ── 언제 보내나 ────────────────────────────────────────────────────────────

def _kst(value: datetime) -> datetime:
    return monitor._utc_now(value).astimezone(KST)


def digest_due(digest_state: Mapping | None, now: datetime, *, requested: bool,
               fallback: bool = True) -> bool:
    """오늘 아직 안 보냈고, 요청받았거나(대체 경로를 켠 호출에서) 대체 시각이 지났으면."""
    local = _kst(now)
    state = digest_state if isinstance(digest_state, Mapping) else {}
    if str(state.get("last_sent_date") or "") == local.date().isoformat():
        return False
    return requested or (fallback and local.hour >= FALLBACK_HOUR_KST)


def window_start(digest_state: Mapping | None, now: datetime) -> datetime:
    """직전 요약 이후. 기록이 없거나 너무 오래됐으면 최근 24시간."""
    now_utc = monitor._utc_now(now)
    state = digest_state if isinstance(digest_state, Mapping) else {}
    last = monitor._parse_time(state.get("last_sent_at"))
    if last and timedelta(hours=6) <= now_utc - last <= timedelta(hours=48):
        return last
    return now_utc - timedelta(hours=24)


# ── 읽기 도우미 ─────────────────────────────────────────────────────────────

def _in_window(value: object, since: datetime, now: datetime) -> bool:
    at = monitor._parse_time(value)
    return bool(at and since <= at <= monitor._utc_now(now) + timedelta(minutes=10))


def _clock(value: object) -> str:
    at = monitor._parse_time(value)
    return at.astimezone(KST).strftime("%H:%M") if at else ""


def _short(text: object, limit: int = 40) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _quote_titles(titles: Sequence[str], limit: int = 2) -> str:
    shown = [f"「{_short(title)}」" for title in titles[:limit] if title]
    if not shown:
        return ""
    more = f" 외 {len(titles) - len(shown)}건" if len(titles) > len(shown) else ""
    return ", ".join(shown) + more


def _first_sentence(text: object) -> str:
    value = " ".join(str(text or "").split())
    for mark in (". ", "다. "):
        if mark in value:
            return value[:value.index(mark) + len(mark)].strip()
    return value


def _entity_names() -> dict[str, str]:
    try:
        import entity_match
        return {str(row.get("id")): str(row.get("name_kr") or row.get("id"))
                for row in entity_match.load_entity_registry()}
    except Exception:  # noqa: BLE001 — 이름을 못 읽으면 id 로 쓴다
        return {}


def _unverified_facts(item: Mapping, names: Mapping[str, str]) -> str:
    """대본 검사가 기사에서 찾지 못한 사실을 사람 말로."""
    parts: list[str] = []
    for day in item.get("dates") or ():
        text = str(day)
        try:
            parsed = datetime.fromisoformat(text)
            text = f"{parsed.month}월 {parsed.day}일"
        except ValueError:
            pass
        parts.append(f"날짜 {text}")
    parts.extend(f"수치 {claim}" for claim in item.get("claims") or ())
    parts.extend(f"이름 {names.get(str(entity), str(entity))}"
                 for entity in item.get("entities") or ())
    parts.extend(f"나라 {country}" for country in item.get("countries") or ())
    if item.get("attributed_to"):
        parts.append("다른 기사의 내용을 섞음")
    return ", ".join(parts[:3])


# ── 구역별 문장 ─────────────────────────────────────────────────────────────

def _brief_line(rows: Sequence[Mapping]) -> tuple[str, bool]:
    """(아침 브리핑 한 줄, 문제가 있나)."""
    stats = [row for row in rows if row.get("record_type") == "selection_stats"]
    if not stats:
        return ("아침 브리핑: 이번 요약 기간에 발송 기록이 없습니다", True)
    latest = max(stats, key=lambda row: str(row.get("generated_at") or ""))
    selected = sum(monitor._nonnegative_int((latest.get(region) or {}).get("selected_count"))
                   for region in ("domestic", "overseas")
                   if isinstance(latest.get(region), Mapping))
    status = str(latest.get("pipeline_status") or "ok")
    at = _clock(latest.get("generated_at"))
    if status == "ok":
        return (f"아침 브리핑: 정상 발송({at}) · 기사 {selected}건", False)
    if status == "partial":
        return (f"아침 브리핑: 일부가 발송되지 못했습니다({at})", True)
    return (f"아침 브리핑: 처리가 끝까지 되지 않았습니다({at})", True)


def _crawl_line(slots: Mapping | None, since: datetime, now: datetime) -> str:
    rows = [row for row in (slots or {}).values()
            if isinstance(row, Mapping) and _in_window(row.get("slot"), since, now)]
    if not rows:
        return "수집: 이번 요약 기간의 실행 기록이 없습니다"
    ok = [row for row in rows if str(row.get("status") or "").startswith("success")]
    new = sum(monitor._nonnegative_int(row.get("new_article_count")) for row in rows)
    if len(ok) == len(rows):
        return f"수집: {len(rows)}회 모두 정상 · 새 기사 {new}건"
    return f"수집: {len(rows)}회 중 {len(rows) - len(ok)}회 실패 · 새 기사 {new}건"


def _curation_lines(rows: Sequence[Mapping]) -> list[str]:
    failures = [row for row in rows if row.get("record_type") == "curation_failure"]
    if not failures:
        return []
    lost = sum(monitor._nonnegative_int(row.get("lost")) for row in failures)
    kinds = Counter(monitor.curation_failure_kind([row]) for row in failures)
    causes = " · ".join(f"{_KIND_PHRASE.get(kind, kind)} {count}회"
                        for kind, count in kinds.most_common())
    return [f"AI 요약이 실패해 늦춰진 기사 {lost}건 ({causes}) — 다음 수집에서 다시 "
            "요약합니다. 두 번 연속 실패한 기사만 빠지고, 아래에 적습니다."]


def _event_items(events: Sequence[Mapping]) -> list[Mapping]:
    return [item for row in events for item in (row.get("items") or ())
            if isinstance(item, Mapping)]


def _article_lines(by_key: Mapping[str, list[Mapping]]) -> list[str]:
    lines: list[str] = []
    integrity = _event_items(by_key.get("article-integrity-quarantine", ()))
    integrity_hashes = {str(item.get("hash")) for item in integrity if item.get("hash")}
    if integrity:
        repeats = Counter(str(item.get("hash")) for item in integrity if item.get("hash"))
        titles: dict[str, str] = {}
        for item in integrity:
            titles.setdefault(str(item.get("hash")), str(item.get("title") or ""))
        ordered = [titles[h] for h, _ in repeats.most_common()]
        line = (f"AI가 만든 제목·요약이 원문과 맞지 않아 이번 수집에서 뺀 기사 "
                f"{len(repeats)}건 — 다음 수집에서 다시 만듭니다. {_quote_titles(ordered)}")
        stuck = [h for h, count in repeats.items() if count >= 3]
        if stuck:
            line += (f". 이 중 {len(stuck)}건은 {max(repeats.values())}회째 같은 이유로 빠지고 "
                     "있어 한 번 볼 만합니다")
        lines.append(line.rstrip(" .") + ".")

    held = [item for item in _event_items(by_key.get("unverified-fallback-held", ()))
            if str(item.get("hash")) not in integrity_hashes]
    if held:
        held_hashes = {str(item.get("hash")) for item in held}
        final = {str(item.get("hash")): str(item.get("title") or "")
                 for item in held if item.get("final")}
        line = (f"AI 요약이 안 돼 브리핑 후보에서 잠시 뺀 기사 {len(held_hashes)}건 — "
                "다음 수집에서 다시 요약합니다.")
        if final:
            line += (f" 두 번 모두 실패해 최종 제외 {len(final)}건: "
                     f"{_quote_titles(list(final.values()))}.")
        lines.append(line)
    return lines


def _script_lines(by_key: Mapping[str, list[Mapping]], names: Mapping[str, str]) -> list[str]:
    lines: list[str] = []
    for row in by_key.get("audio-script-claim-removed", ()):
        if "만들지 못했습니다" in str(row.get("title") or ""):
            continue  # 할 일 쪽에서 말한다
        items = [item for item in row.get("items") or () if isinstance(item, Mapping)]
        if not items:
            continue
        variant = _VARIANT_LABEL.get(str(items[0].get("variant") or ""), "")
        head = (f"{variant} 오디오 대본에서 기사로 확인되지 않는 문장 {len(items)}개를 빼고 "
                "녹음했습니다")
        details = []
        for item in items[:2]:
            facts = _unverified_facts(item, names)
            details.append(f"\"{_short(item.get('line'), 60)}\""
                           + (f" (기사에 없던 것: {facts})" if facts else ""))
        lines.append(head + " — " + " / ".join(details) + ".")
    return lines


def _other_event_lines(by_key: Mapping[str, list[Mapping]]) -> list[str]:
    lines = []
    for key, rows in sorted(by_key.items()):
        if key in _DETAILED_EVENTS or not rows:
            continue
        latest = max(rows, key=lambda row: str(row.get("generated_at") or ""))
        detail = _first_sentence(latest.get("detail"))
        times = f" ({len(rows)}회)" if len(rows) > 1 else ""
        lines.append(f"{latest.get('title')}{times}" + (f" — {detail}" if detail else ""))
    return lines


def _gate_lines(rows: Sequence[Mapping], records: Sequence[Mapping]) -> list[str]:
    gates = [row for row in rows if row.get("record_type") == "data_quality_gate"]
    if not gates:
        return []
    latest = monitor.latest_data_gate_record(gates)
    previous = monitor.previous_data_gate_record(records, latest)
    lines: list[str] = []
    archive = latest.get("archive_quality") if isinstance(latest, Mapping) else None
    if isinstance(archive, Mapping) and monitor._nonnegative_int(archive.get("quarantined")):
        previous_archive = (previous.get("archive_quality")
                            if isinstance(previous, Mapping) else None)
        new, _kept = monitor.archive_quarantine_split(
            archive, previous_archive if isinstance(previous_archive, Mapping) else None)
        if new:
            lines.append(f"원문과 내용이 달라 사이트에서 새로 뺀 옛 기사 {new}건 "
                         f"(지금 빼 둔 것 모두 {monitor._nonnegative_int(archive.get('quarantined'))}건).")
    weeks = latest.get("topic_weeks") if isinstance(latest, Mapping) else None
    if isinstance(weeks, Mapping):
        hidden = [label for key, label in (("flow", "주제 흐름 표"), ("slope", "슬로프 그래프"))
                  if weeks.get(f"{key}_ratio") is not None
                  and not weeks.get(f"{key}_visible", True)]
        if hidden:
            lines.append(f"{' · '.join(hidden)}를 사이트에서 잠시 숨겼습니다 — 주별 수집량 "
                         "차이가 커서 추세가 왜곡될 수 있어서입니다. 고르게 쌓이면 다시 보입니다.")
    return lines


def _state_rows(alert_items: Mapping | None, since: datetime, now: datetime):
    for key, row in sorted((alert_items or {}).items()):
        if not isinstance(row, Mapping):
            continue
        seen = _in_window(row.get("last_seen_at"), since, now)
        yield str(key), row, seen


def _dev_line(key: str, row: Mapping) -> str:
    since = monitor._parse_time(row.get("first_seen_at"))
    since_text = (f" ({since.astimezone(KST).month}/{since.astimezone(KST).day}부터)"
                  if since else "")
    return f"{_short(row.get('detail') or row.get('title'), 70)}{since_text}"


# ── 조립 ────────────────────────────────────────────────────────────────────

def build_digest(*, records: Iterable[Mapping], alert_state: Mapping | None,
                 crawl_slots: Mapping | None, since: datetime, now: datetime,
                 include_dev: bool | None = None) -> str:
    """요약 한 통. 판정은 하지 않고 기록을 사람 말로 옮긴다."""
    records = [row for row in records if isinstance(row, Mapping)]
    now_utc = monitor._utc_now(now)
    rows = [row for row in records if _in_window(row.get("generated_at"), since, now_utc)]
    local = _kst(now_utc)
    if include_dev is None:
        include_dev = local.weekday() == DEV_WEEKDAY
    names = _entity_names()
    alert_items = (alert_state or {}).get("items") if isinstance(alert_state, Mapping) else {}

    events_by_key: dict[str, list[Mapping]] = defaultdict(list)
    for row in rows:
        if row.get("record_type") == "quality_event" and row.get("alert_key"):
            events_by_key[str(row["alert_key"])].append(row)

    todo: list[str] = []
    handled: list[str] = []
    sources: list[str] = []
    dev: list[str] = []
    immediate_sent = immediate_resolved = 0

    brief, brief_problem = _brief_line(rows)
    if brief_problem:
        todo.append(brief.replace("아침 브리핑: ", "아침 브리핑 — ")
                    + ". GitHub 의 Daily Brief 워크플로를 확인해 주세요.")

    for key, row, seen in _state_rows(alert_items, since, now_utc):
        delivery = monitor.row_delivery(key, row)
        active = bool(row.get("active"))
        if _in_window(row.get("last_notified_at"), since, now_utc) and \
                delivery == monitor.DELIVERY_IMMEDIATE:
            immediate_sent += 1
        if _in_window(row.get("resolution_notified_at"), since, now_utc):
            immediate_resolved += 1
        if delivery == monitor.DELIVERY_DEV:
            if include_dev and active:
                dev.append(_dev_line(key, row))
            continue
        if not seen or row.get("scope") in ("quality_event", "curation", "data_gate"):
            continue  # 기록(delivery_log)에서 더 자세히 쓴다
        title = str(row.get("title") or key)
        if row.get("scope") == "source":
            if active:
                action = str(row.get("action") or "")
                sources.append(f"{title}" + (f" — {action}" if action else ""))
            continue
        needs_look = active and (row.get("level") == monitor.LEVEL_ACTION or key in _TODO_KEYS)
        if needs_look:
            todo.append(f"{title} — {row.get('action') or ''}".rstrip(" —"))
        elif active:
            handled.append(f"{title} — {_first_sentence(row.get('detail'))}")
        else:
            handled.append(f"{title} — 지금은 정상입니다.")

    for row in events_by_key.get("audio-script-unverified", ())[-1:]:
        todo.append(f"{row.get('title')} — {row.get('action') or '이 회차 음원을 한 번 들어봐 주세요.'}")
    for row in events_by_key.get("audio-script-claim-removed", ()):
        if "만들지 못했습니다" in str(row.get("title") or ""):
            todo.append(f"{row.get('title')} — {row.get('action') or ''}".rstrip(" —"))

    handled[:0] = (_curation_lines(rows) + _article_lines(events_by_key)
                   + _script_lines(events_by_key, names) + _other_event_lines(events_by_key)
                   + _gate_lines(rows, records))

    since_text = f"{_kst(since).month}/{_kst(since).day} {_kst(since).strftime('%H:%M')}"
    lines = [f"📋 뉴클렌스 하루 점검 · {local.month}월 {local.day}일"
             f"({_WEEKDAYS[local.weekday()]}) {local.strftime('%H:%M')}"]
    lines.append(f"🔧 할 일 {len(todo)}건" if todo else "✅ 할 일 없음")
    lines.append(f"• {brief}")
    lines.append(f"• {_crawl_line(crawl_slots, since, now_utc)}")
    if todo:
        lines.append("\n할 일")
        lines.extend(f"• {line}" for line in todo)
    lines.append(f"\n자동으로 처리된 일 ({since_text} 이후)")
    if handled:
        lines.extend(f"• {line}" for line in handled)
    else:
        lines.append("• 없음")
    if sources:
        lines.append("\n출처")
        lines.extend(f"• {line}" for line in sources)
    if dev:
        lines.append("\n개발 점검 (월요일에만 싣습니다 — 서비스에는 영향 없음)")
        lines.extend(f"• {line}" for line in dev)
    # 이 요약 기간에 따로 울린 🚨 과 그 '풀렸습니다'. 요약을 읽는 사람이 밤사이
    # 받은 알림이 아직 살아 있는지 여기서 맞춰 볼 수 있다.
    recap = f"지난 요약 이후 즉시 알림 {immediate_sent}건"
    if immediate_resolved:
        recap += f" · 풀렸다는 알림 {immediate_resolved}건"
    lines.append(f"\n{recap}")

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS - 40].rsplit("\n", 1)[0] + "\n• (길어서 줄였습니다 — 실행 기록 참고)"
    return text
