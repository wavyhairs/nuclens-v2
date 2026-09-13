"""장기 스토리(thread)와 그 관계를 **되돌릴 수 있게** 기억한다 → thread_ledger.json.

이름에 대하여
-------------
`event_ledger.py` 는 이미 있고 **미래 일정 원장**이다. `story_id` 도 이미 있고
기사 중복묶음의 신원이다. 그래서 이 계층은 thread 라고 부른다.

    Article  →  briefing story  →  issue / event  →  thread
                (story_id)         (issue_id)        (thread_id)

무엇을 남기는가
---------------
관계를 **덧쓰지 않고 쌓는다.** 어떤 사건이 어느 스토리에 언제 왜 붙었는지 남지
않으면 되돌릴 수가 없다 — 되돌리려면 무엇을 되돌리는지 읽을 수 있어야 한다.

    attach     사건이 스토리에 붙었다
    detach     사건이 떨어졌다
    merge      두 스토리가 하나가 됐다 (진 쪽 id 는 moved_to 로 남는다)
    split      한 스토리가 갈라졌다
    revoke     앞선 판정을 취소했다 (사람 또는 규칙)

원본 사건은 **건드리지 않는다.** thread 는 `issue_ledger` 위에 얹는 덧지(overlay)
이고, 이 파일을 통째로 지워도 사건과 그 근거는 그대로다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT_FILE = Path(os.environ.get("THREAD_LEDGER_FILE") or (BASE / "thread_ledger.json"))
KST = timezone(timedelta(hours=9))

LEDGER_VERSION = "thread-ledger-v1"
# 스토리당 관계 기록 상한. 되돌리기에 필요한 것은 **최근 이력**이고, 무한히 쌓으면
# 매 빌드 재작성되는 파일이 부푼다(`issue_ledger.MAX_REVISIONS` 와 같은 판단).
MAX_RELATIONS_PER_THREAD = 40
DECISIONS = ("attach", "detach", "merge", "split", "revoke", "supersede")


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def load_store(path: Path | None = None) -> dict:
    try:
        raw = json.loads((path or OUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    threads = raw.get("threads")
    if not isinstance(threads, dict):
        threads = {}
    live = raw.get("live_thread_ids")
    build = raw.get("build")
    return {
        "version": raw.get("version") or LEDGER_VERSION,
        "generated_at": raw.get("generated_at") or "",
        # 명단과 빌드 결과는 다음 회차가 덮어쓰기 전까지 그대로 살아 있어야 한다 —
        # 여기서 떨어뜨리면 판정을 돌리지 않는 웹 빌드가 매번 '알 수 없음'을 본다.
        "live_thread_ids": [str(value) for value in live] if isinstance(live, list) else [],
        "build": build if isinstance(build, dict) else {},
        "threads": {key: value for key, value in threads.items()
                    if isinstance(value, dict) and value.get("thread_id")},
    }


def save_store(store: dict, path: Path | None = None) -> None:
    target = path or OUT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(target)


def owner_index(store: dict) -> dict[str, str]:
    """사건 id → 그 사건을 들고 있던 스토리. 신원 상속의 유일한 재료다.

    `issue_ledger` 와 달리 동값 문제가 없다 — 한 사건은 한 스토리에만 붙는다.
    """
    out: dict[str, str] = {}
    for thread_id, entry in (store.get("threads") or {}).items():
        if entry.get("moved_to"):
            continue
        for event_id in entry.get("event_ids") or ():
            out[str(event_id)] = str(thread_id)
    return out


def relation(event_id: str, thread_id: str, decision: str, *,
             reason: str, evidence: object = None, method: str,
             version: str) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"알 수 없는 결정: {decision}")
    return {
        "event_id": str(event_id),
        "thread_id": str(thread_id),
        "decision": decision,
        "reason": str(reason)[:160],
        "evidence": evidence if evidence is not None else [],
        "decided_at": _now(),
        "method": str(method),
        "version": str(version),
    }


def merge(store: dict, threads: list[dict], relations: list[dict]) -> dict:
    """이번 빌드의 스토리를 원장에 얹는다. 같은 입력이면 같은 결과다(멱등)."""
    entries = store["threads"]
    counts = {"added": 0, "updated": 0, "relations": 0}
    for row in threads:
        thread_id = str(row.get("thread_id") or "")
        if not thread_id:
            continue
        existing = entries.get(thread_id)
        payload = {
            "thread_id": thread_id,
            "title": str(row.get("title") or ""),
            "event_ids": list(dict.fromkeys(str(value) for value in
                                            (row.get("event_ids") or []) if value)),
            "anchor_event_id": str(row.get("anchor_event_id") or ""),
            "first_seen": str(row.get("first_seen") or ""),
            "last_seen": str(row.get("last_seen") or ""),
            "units": list(row.get("units") or []),
            "entity_ids": list(row.get("entity_ids") or []),
            "scope": row.get("scope") or {},
            "identity_origin": str(row.get("identity_origin") or ""),
            "identity_evidence": list(row.get("identity_evidence") or []),
        }
        if existing is None:
            entries[thread_id] = {**payload, "moved_to": "", "relations": []}
            counts["added"] += 1
            continue
        # 되살아난 스토리는 이동 표시를 지운다 — `issue_ledger.merge` 와 같은 규칙.
        existing["moved_to"] = ""
        # 최초 확인일은 낮은 쪽이 이긴다. 단조로운 값이라야 id 가 왕복하지 않는다.
        first_candidates = [value for value in
                            (existing.get("first_seen"), payload["first_seen"]) if value]
        existing.update(payload)
        if first_candidates:
            existing["first_seen"] = min(first_candidates)
        counts["updated"] += 1

    by_thread: dict[str, list[dict]] = {}
    for record in relations:
        by_thread.setdefault(str(record.get("thread_id")), []).append(record)
    for thread_id, records in by_thread.items():
        entry = entries.get(thread_id)
        if entry is None:
            continue
        history = list(entry.get("relations") or []) + records
        entry["relations"] = history[-MAX_RELATIONS_PER_THREAD:]
        counts["relations"] += len(records)
    return counts


def alias_target(store: dict, thread_id: str) -> str:
    """이동 사슬을 끝까지 따라간다. 고리를 만나면 멈춘다."""
    seen: set[str] = set()
    current = str(thread_id)
    while current and current not in seen:
        seen.add(current)
        entry = (store.get("threads") or {}).get(current) or {}
        target = str(entry.get("moved_to") or "")
        if not target:
            return current
        current = target
    return current


def redirects(store: dict, live_ids: set[str]) -> dict[str, str]:
    """살아 있는 스토리로 가는 별칭만. 죽은 주소로 보내면 그릴 것이 없다."""
    out: dict[str, str] = {}
    for thread_id, entry in (store.get("threads") or {}).items():
        if not entry.get("moved_to"):
            continue
        target = alias_target(store, thread_id)
        if target and target != thread_id and target in live_ids:
            out[thread_id] = target
    return out


def run(threads: list[dict], relations: list[dict], *,
        build: dict | None = None, path: Path | None = None,
        save: bool = True) -> dict:
    """이번 판정의 결과를 원장에 얹는다.

    `build` 는 이 회차가 어떻게 끝났는가다. 원장에 같이 적는 이유는 **읽는 쪽이
    다른 체크아웃에 있기 때문**이다 — 그림자 진단(`web/_shadow/report.json`)은
    .gitignore 라 웹 빌드가 볼 수 없다. 판정이 반쪽으로 끝난 회차를 화면이
    알아보려면 그 사실이 커밋되는 파일에 남아야 한다.

    `live_thread_ids` 는 **이번에 실제로 선 스토리**의 명단이다. 원장은 지우지
    않으므로 구성원이 전부 떨어져 나간 옛 스토리도 옛 event_ids 를 들고 파일에
    남는다 — 명단이 없으면 그것을 살아 있는 것과 구분할 방법이 없다.
    """
    store = load_store(path)
    counts = merge(store, threads, relations)
    store["version"] = LEDGER_VERSION
    store["generated_at"] = _now()
    store["live_thread_ids"] = sorted(
        {str(row.get("thread_id")) for row in threads if row.get("thread_id")})
    if build is not None:
        store["build"] = {**build, "at": store["generated_at"]}
    counts["total"] = len(store["threads"])
    if save:
        save_store(store, path)
    return {"store": store, "counts": counts}
