"""지난 브리핑 화면을 **발송 당시 모양**으로 붙잡아 두는 원장.

왜 있는가
---------
`build_data.build_briefings` 는 빌드마다 날짜별 카드를 **지금의 이슈 묶음**으로
다시 그린다. 그래서 사람이 콘솔에서 사건을 나누거나(6260 전기본 비대 이슈),
재클러스터링이 두 카드를 합치는 순간 8월의 브리핑 화면이 소리 없이 바뀐다 —
그날 독자가 본 카드는 사라지고, 그날 없던 카드 구성이 그 날짜 이름으로 선다.

2026-09-24 결정(사용자): **A — 발송 당시 기록을 보존하고 '이후 분류 정정됨'을
알린다.** 이 모듈이 그 기록이다.

무엇을 붙잡는가
---------------
날짜마다 카드 한 장당 한 줄 — 그날 그 카드에 실린 기사 해시 묶음과, 화면이
읽는 **표시 문장**(제목·요약·해석·변화)뿐이다. 근거 기사 목록·검증 상태 같은
무거운 칸은 얼리지 않는다(라이브 briefings.json 의 이슈 행 851건이 10.8 MB 다).
그런 칸은 지금 그 기사를 들고 있는 이슈에서 빌려 온다 — 정정 안내가 가리키는
곳이 바로 거기라 서로 다른 말을 하지 않는다.

언제 얼리는가
-------------
그 날짜를 **처음 본 빌드**에서 한 번. 한 번 적힌 날짜는 다시 쓰지 않는다
(write-once). 발송은 daily-brief 가 하고 그 직후 같은 잡이 빌드하므로, 처음
본 모양이 곧 발송 직후의 웹 화면이다. 원장이 없던 시절의 날짜는 이 모듈이
처음 도는 빌드의 묶음으로 얼린다 — 그래서 **사건 나누기(콘솔 정정)보다 먼저**
배포돼야 한다.

무엇을 하지 않는가
------------------
- 기사 해시 묶음이 그대로면 아무것도 바꾸지 않는다. 이슈 주소만 바뀐 경우
  (재발급·흡수)는 분류가 바뀐 게 아니다 — 지금 행을 그대로 쓴다.
- 편집 숨김·원문 불일치 격리로 그날 기사가 전부 빠진 카드는 되살리지 않는다.
  빠진 것은 정정이 아니라 철회다.
- 얼린 뒤에 그 날짜에 새로 붙은 기사(늦은 복원 등)는 지금 행 그대로 둔다.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# v2 (2026-09-26): 소급으로 얼린 날짜의 표시 제목을 그날 제목으로 되돌린다(_migrate_v2).
VERSION = 2
_KST = timezone(timedelta(hours=9))

# 화면이 카드·상세에서 읽는 **문장** 칸. 여기에 없는 칸은 지금 이슈에서 빌린다.
FROZEN_FIELDS = (
    "title", "headline_display", "summary", "detail", "implication",
    "why_important", "why_short", "card_why", "card_prior", "open_question",
    "latest_change", "change_display", "change_kind",
    "region", "importance", "status", "selection_reasons",
)


def load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "dates": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("dates"), dict):
        return {"version": VERSION, "dates": {}}
    return payload


def save(path: Path, payload: dict) -> None:
    """날짜 한 줄씩 쓴다 — write-once 라 diff 가 늘 '새 날짜 한 줄 추가'로 읽힌다."""
    dates = payload.get("dates") or {}
    lines = [f'{{"version": {VERSION}, "dates": {{']
    keys = sorted(dates)
    for index, key in enumerate(keys):
        comma = "," if index + 1 < len(keys) else ""
        lines.append(f"{json.dumps(key)}: "
                     f"{json.dumps(dates[key], ensure_ascii=False, sort_keys=True)}{comma}")
    lines.append("}}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def card_hashes(row: dict, briefing_date: str) -> list[str]:
    """그 날짜 카드에 실린 기사. 근거(evidence)와 다른 날의 기사는 뺀다."""
    return sorted({
        str(article.get("hash") or "")
        for article in row.get("related_articles") or ()
        if article.get("briefing_date") == briefing_date
        and (article.get("member_role") or "card") != "evidence"
        and article.get("hash")
    })


def freeze_rows(rows: list[dict], briefing_date: str, frozen_at: str) -> dict:
    cards = []
    for row in rows:
        hashes = card_hashes(row, briefing_date)
        if not hashes:
            continue
        cards.append({
            "issue_id": str(row.get("issue_id") or ""),
            "hashes": hashes,
            "representative_hash": str((row.get("representative_article") or {}).get("hash") or ""),
            "fields": {key: row[key] for key in FROZEN_FIELDS if key in row},
        })
    return {"frozen_at": frozen_at, "cards": cards}


def _now_label(row: dict) -> dict:
    return {"issue_id": str(row.get("issue_id") or ""),
            "title": str(row.get("headline_display") or row.get("title") or "")}


def reconcile(rows: list[dict], briefing_date: str, snapshot: dict) -> tuple[list[dict], int]:
    """지금 행과 얼린 카드를 맞대 그날 화면에 설 행을 돌려준다. (행, 정정 카드 수).

    - 해시 묶음이 똑같은 지금 행이 있으면 그 행을 쓴다(분류 그대로).
    - 없으면 얼린 문장으로 카드를 세우고 `classification_note` 로 지금 어디에
      있는지 알린다. 무거운 칸은 대표 기사를 지금 들고 있는 행에서 빌린다.
    - 얼린 카드들이 이미 대신 말한 지금 행(해시가 전부 얼린 쪽에 있는 행)은 뺀다.
    - 순서는 얼린 순서를 따른다. 얼린 뒤 새로 생긴 행은 뒤에 지금 순서대로.
    """
    cards = snapshot.get("cards") or []
    if not cards:
        return rows, 0
    current = [(row, set(card_hashes(row, briefing_date))) for row in rows]
    frozen_all: set[str] = set()
    for card in cards:
        frozen_all.update(card.get("hashes") or ())

    placed: list[tuple[int, int, dict]] = []   # (얼린 순서, 지금 순서, 행)
    used: set[int] = set()
    corrected = 0
    for order, card in enumerate(cards):
        hashes = set(card.get("hashes") or ())
        exact = next((index for index, (_, held) in enumerate(current)
                      if held == hashes and index not in used), None)
        if exact is not None:
            used.add(exact)
            placed.append((order, exact, current[exact][0]))
            continue
        holders = [row for row, held in current if held & hashes]
        if not holders:
            continue   # 그날 기사가 전부 빠졌다 — 철회지 정정이 아니다
        rep = card.get("representative_hash") or ""
        base = next((row for row, held in current if rep in held), holders[0])
        row = copy.deepcopy(base)
        row.update(copy.deepcopy(card.get("fields") or {}))
        row["issue_id"] = card.get("issue_id") or row.get("issue_id")
        row["current_article_count"] = len(hashes)
        row["classification_note"] = {
            "status": "corrected",
            "frozen_at": snapshot.get("frozen_at", ""),
            "now": [_now_label(holder) for holder in holders],
        }
        placed.append((order, -1, row))
        corrected += 1

    tail = len(cards)
    for index, (row, held) in enumerate(current):
        if index in used:
            continue
        if held and held <= frozen_all:
            continue   # 얼린 카드가 이미 이 기사들을 말했다
        placed.append((tail, index, row))

    # 한 날짜에 같은 주소가 두 번 서면 상세·딥링크가 어느 쪽인지 모른다.
    # 지금 행을 남긴다 — 얼린 카드의 주소는 옛 주소라 목록 밖에서도 풀린다.
    live_ids = {row.get("issue_id") for _, index, row in placed if index >= 0}
    placed = [entry for entry in placed
              if entry[1] >= 0 or entry[2].get("issue_id") not in live_ids]
    placed.sort(key=lambda entry: (entry[0], entry[1]))
    return [row for _, _, row in placed], sum(1 for _, index, _ in placed if index < 0)


def _frozen_late(day: str, frozen_at: object) -> bool:
    """그 날짜보다 하루 넘게 늦게 얼렸나 — 원장이 생기기 전 날짜를 소급으로 얼린 경우."""
    try:
        frozen = datetime.fromisoformat(str(frozen_at))
        briefing_day = date.fromisoformat(day)
    except (TypeError, ValueError):
        return False
    if frozen.tzinfo is not None:
        frozen = frozen.astimezone(_KST)
    return (frozen.date() - briefing_day).days > 1


def _migrate_v2(payload: dict) -> int:
    """v1 원장의 소급 날짜에서 표시 제목만 그날 제목으로 되돌린다. 고친 카드 수.

    write-once 의 유일한 예외다. 2026-09-24 에 원장이 처음 돌면서 그 전 날짜를
    전부 그 빌드의 묶음으로 얼렸는데, 그때 브리핑 행의 `headline_display` 는
    이슈의 **그 시점** 표시 제목이었다(`apply_headline_display` 가 카탈로그
    제목을 모든 날짜에 덮었다). 그날 화면에 실제로 선 표시 제목은 남아 있지
    않으므로, 그날 카드 자신의 제목이 가장 가까운 값이다. 다른 칸은 건드리지 않는다.
    """
    if int(payload.get("version") or 1) >= 2:
        return 0
    fixed = 0
    for day, snapshot in (payload.get("dates") or {}).items():
        if not isinstance(snapshot, dict) or not _frozen_late(day, snapshot.get("frozen_at")):
            continue
        for card in snapshot.get("cards") or ():
            fields = card.get("fields") if isinstance(card, dict) else None
            if not isinstance(fields, dict):
                continue
            title = fields.get("title")
            if title and fields.get("headline_display") != title:
                fields["headline_display"] = title
                fixed += 1
    return fixed


def apply(briefings: list[dict], payload: dict, frozen_at: str) -> dict:
    """모든 날짜에 대해 얼리거나(처음 본 날) 맞댄다(이미 얼린 날). 통계를 돌려준다."""
    dates = payload.setdefault("dates", {})
    migrated = _migrate_v2(payload)
    payload["version"] = VERSION
    stats = {"frozen_new": 0, "dates_corrected": 0, "cards_corrected": 0,
             "migrated_headlines": migrated}
    for briefing in briefings:
        day = str(briefing.get("date") or "")
        rows = briefing.get("issues") or []
        if not day or not rows:
            continue
        if day not in dates:
            dates[day] = freeze_rows(rows, day, frozen_at)
            stats["frozen_new"] += 1
            continue
        new_rows, corrected = reconcile(rows, day, dates[day])
        if corrected:
            stats["dates_corrected"] += 1
            stats["cards_corrected"] += corrected
        briefing["issues"] = new_rows
        briefing["issue_count"] = len(new_rows)
        briefing["highlights"] = [row.get("title", "") for row in new_rows[:3]]
        briefing["highlight_issues"] = [
            {"issue_id": row.get("issue_id"), "title": row.get("title", "")}
            for row in new_rows[:3]
        ]
    return stats
