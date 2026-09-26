"""아침 브리핑 후보의 **신선도** — 직전 브리핑 이후에 나온 기사만 오늘 소식이다.

왜 있는가
---------
2026-09-13~26 발송 243건을 한 건씩 판정한 결과(`.eval/delivery-audit-2026-09-26`),
사건 기사 229건 가운데 45건이 이틀 이상 늦게 나갔고 33건은 이미 보낸 사실의
재발송이었다. 큐는 queued_at 3일에서 자르고 점수는 12시간당 0.5씩(최대 3.0)
깎이지만, 그날 새 기사가 적으면 이틀 전 기사가 감점을 안고도 하한(14점)을 넘는다.
주말·추석에 늦은 발송이 몰린 것이 그 모양이다.

규칙
----
- 기준 시각은 **직전 브리핑이 실제로 돈 시각**이다(delivery_log 의 selection_stats).
  달력으로 "이틀"을 세면 발송이 하루 빠진 날 그 사이 소식이 통째로 사라진다.
- 게재 시각이 기준보다 `grace_hours` 이상 앞서면 묵은 기사다. 여유를 두는 이유는
  수집 지연이다 — 새벽에 게재돼 브리핑 직후에 수집된 기사를 잃지 않으려고.
- 게재 시각이 정확히 자정(KST)이면 **날짜만 준 것**으로 본다(공식기관 게시판은
  날짜만 주고 `_board_datetime` 이 자정으로 박는다). 날짜로만 비교한다.
- 묵은 nice_to_know 는 뺀다. 묵은 must_read 는 **빼지 않는다** — 날짜를 달아
  한 번은 보낸다(`stale_since`). 이미 보낸 사건이면 연속일 게이트(#186)가 뺀다.
  2026-09-26 결정 D1: 중요한 결정을 영영 빠뜨리는 것이 늦는 것보다 나쁘다. 다만
  1번 자리는 그날 소식에만 준다(`fresh_first`).

243건 재현(grace 6h): 문제 기사 29건(늦음 15·재발송 10·각도만 바꾼 재발송 3·옛
사실 1)이 걸리고 정상 기사는 1건(정상회담 전날의 예고 기사)만 걸린다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEFAULTS = {"enabled": True, "grace_hours": 6.0, "keep_must_read_dated": True}


def resolve_config(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    section = (cfg or {}).get("freshness")
    if isinstance(section, dict):
        merged.update({k: v for k, v in section.items() if not str(k).startswith("_")})
    return merged


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST)


def last_brief_at(today: str, path: Path | None = None) -> datetime | None:
    """오늘보다 앞선 가장 최근 브리핑 날짜의 **첫** 선정 시각(KST).

    재실행으로 같은 날 selection_stats 가 여러 줄일 수 있다. 가장 이른 줄을 쓴다 —
    기준이 이를수록 창이 넓어져, 잘못 판단해도 기사를 잃는 쪽으로 틀리지 않는다.
    """
    path = path or Path(__file__).resolve().parent / "delivery_log.jsonl"
    latest_day = ""
    times: dict[str, datetime] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if '"selection_stats"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        day = str(row.get("date") or "")
        at = _parse(row.get("generated_at"))
        if row.get("record_type") != "selection_stats" or not day or day >= today or at is None:
            continue
        if day not in times or at < times[day]:
            times[day] = at
        latest_day = max(latest_day, day)
    return times.get(latest_day)


def published_at(item: dict) -> datetime | None:
    """게재 시각. 없으면 큐 등록 시각(랭킹의 `_freshness_timestamp` 와 같은 순서)."""
    return _parse(item.get("published_at")) or _parse(item.get("queued_at"))


def _date_only(stamp: datetime) -> bool:
    return stamp.hour == 0 and stamp.minute == 0 and stamp.second == 0 and stamp.microsecond == 0


def stale_since(item: dict, cutoff: datetime | None, grace_hours: float) -> date | None:
    """묵은 기사면 그 게재일(KST)을, 아니면 None. 기준이나 시각을 모르면 None(통과)."""
    if cutoff is None:
        return None
    stamp = published_at(item)
    if stamp is None:
        return None
    cutoff = cutoff.astimezone(KST)
    if _date_only(stamp):
        return stamp.date() if stamp.date() < cutoff.date() else None
    return stamp.date() if stamp < cutoff - timedelta(hours=grace_hours) else None


def split(items: list[dict], cutoff: datetime | None, cfg: dict) -> tuple[list[dict], list[dict]]:
    """(남길 것, 뺀 것). 남긴 묵은 must_read 에는 `stale_since` 를 단다(제자리).

    뺀 것은 진단용 요약이다 — 무엇을 왜 뺐는지 발송 로그에 남는다.
    """
    if not cfg.get("enabled", True) or cutoff is None:
        return list(items), []
    grace = float(cfg.get("grace_hours", DEFAULTS["grace_hours"]))
    keep_must = bool(cfg.get("keep_must_read_dated", True))
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        item.pop("stale_since", None)
        since = stale_since(item, cutoff, grace)
        if since is None:
            kept.append(item)
            continue
        if keep_must and str(item.get("importance") or "") == "must_read":
            item["stale_since"] = since.isoformat()
            kept.append(item)
            continue
        dropped.append({
            "hash": item.get("hash", ""),
            "title": (item.get("title_kr") or item.get("title") or "")[:80],
            "importance": item.get("importance", ""),
            "published_at": str(item.get("published_at") or item.get("queued_at") or ""),
            "reason": "stale",
        })
    return kept, dropped


def fresh_first(rows: list[dict]) -> list[dict]:
    """1번 자리는 그날 소식에만 준다. 묵은 must_read 가 1위면 첫 새 기사를 앞으로."""
    if not rows or not rows[0].get("stale_since"):
        return rows
    for index, row in enumerate(rows):
        if not row.get("stale_since"):
            return [row] + rows[:index] + rows[index + 1:]
    return rows


def date_label(item: dict) -> str:
    """카드 제목 앞에 붙일 날짜 표시. 묵은 기사가 아니면 빈 문자열."""
    try:
        since = date.fromisoformat(str(item.get("stale_since") or ""))
    except ValueError:
        return ""
    return f"({since.month}/{since.day})"
