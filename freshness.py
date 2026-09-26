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
DEFAULTS = {"enabled": True, "grace_hours": 6.0, "keep_must_read_dated": True,
            "event_review": True}


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


def _older(stamp: datetime, cutoff: datetime, grace_hours: float) -> bool:
    cutoff = cutoff.astimezone(KST)
    if _date_only(stamp):
        return stamp.date() < cutoff.date()
    return stamp < cutoff - timedelta(hours=grace_hours)


def stale_since(item: dict, cutoff: datetime | None, grace_hours: float) -> date | None:
    """묵은 기사면 그 게재일(KST)을, 아니면 None. 기준이나 시각을 모르면 None(통과)."""
    if cutoff is None:
        return None
    stamp = published_at(item)
    if stamp is None:
        return None
    return stamp.date() if _older(stamp, cutoff, grace_hours) else None


def stale_outlets(item: dict, cutoff: datetime | None, grace_hours: float) -> set[str]:
    """이 기사 묶음에서 **직전 브리핑 전에만** 보도한 매체(identity).

    커버리지 가점(`ranking._coverage_bonus`)이 여기서 센 매체를 뺀다. 9/26 국내
    1번은 9/22 국회 보고를 열 곳이 이미 쓴 사건의 9/25 정리 기사였는데, 그 열
    곳이 '여러 매체가 다룬 오늘 소식'으로 1.2점을 보탰다. 게재 시각은 수집
    단계에서 접힌 기사(raw_sources)만 들고 있으므로 거기서만 센다 — 한 매체의
    기사가 하나라도 새것이거나 시각을 모르면 그 매체는 남긴다.

    9/13~26 발송 243건 재현: 가점이 줄어드는 건 늦은 발송 8/45, 정상 1/135.
    """
    if cutoff is None:
        return set()
    fresh: dict[str, bool] = {}
    for raw in item.get("raw_sources") or []:
        if not isinstance(raw, dict):
            continue
        ident = str(raw.get("identity") or raw.get("publisher") or raw.get("domain") or "")
        ident = ident.strip().lower()
        if not ident:
            continue
        stamp = _parse(raw.get("pub"))
        old = stamp is not None and _older(stamp, cutoff, grace_hours)
        fresh[ident] = fresh.get(ident, False) or not old
    return {ident for ident, is_fresh in fresh.items() if not is_fresh}


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


def _can_lead(row: dict) -> bool:
    # 해설(brief_kind)도 1번은 받지 않는다 — 결정 D2.
    return not row.get("stale_since") and row.get("brief_kind") != "explainer"


def fresh_first(rows: list[dict]) -> list[dict]:
    """1번 자리는 그날 소식에만 준다. 묵은 must_read·해설이 1위면 첫 새 보도를 앞으로."""
    if not rows or _can_lead(rows[0]):
        return rows
    for index, row in enumerate(rows):
        if _can_lead(row):
            return [row] + rows[:index] + rows[index + 1:]
    return rows


def date_label(item: dict) -> str:
    """카드 제목 앞에 붙일 날짜 표시. 묵은 기사가 아니면 빈 문자열."""
    try:
        since = date.fromisoformat(str(item.get("stale_since") or ""))
    except ValueError:
        return ""
    return f"({since.month}/{since.day})"


# ── 사건 신선도(B5) — 기사는 새것인데 사건이 묵은 경우 ─────────────────────
#
# 늦은 발송 45건 가운데 27건은 기사 자체는 하루 안에 나온 새 기사였다 — 옛 사건을
# 다시 다룬 정리·후속 기사다(9/26 1번 카드 '대미투자 1호 확정'은 9/22 국회 보고를
# 9/25 정리 기사가 다시 쓴 것). 게재 시각만 보는 위 규칙은 이것을 못 본다.
# 같은 스토리로 묶인 기사 중 가장 이른 것이 직전 브리핑 전에 나왔으면 '옛 사건일
# 수 있다'는 신호로 삼고, 그 후보만 모델에게 "그 뒤 새로 일어난 일이 있나"를 묻는다
# (dedup.stale_event_review). 신호만으로 자르면 243건 판정에서 정상 기사 11/137 이
# 같이 잘렸다 — 하원 가결처럼 진짜 후속이 옛 스토리에 붙어 있기 때문이다.


def archive_dates(days: int = 14, *, root: Path | None = None,
                  now: datetime | None = None) -> dict[str, dict]:
    """최근 아카이브의 hash → {published_at, title}. 스토리 첫 보도일을 찾는 재료."""
    root = root or Path(__file__).resolve().parent / "archive"
    now = (now or datetime.now(KST)).astimezone(KST)
    since = now - timedelta(days=days)
    months = {now.strftime("%Y-%m"), since.strftime("%Y-%m")}
    found: dict[str, dict] = {}
    for month in sorted(months):
        path = root / f"{month}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            stamp = _parse(row.get("pub"))
            if not row.get("hash") or stamp is None or stamp < since:
                continue
            found[row["hash"]] = {"published_at": row.get("pub"),
                                  "title": row.get("title_kr") or row.get("title") or ""}
    return found


def first_seen(item: dict, dates: dict[str, dict]) -> dict | None:
    """자기와 스토리 멤버 가운데 가장 이른 기사 {hash, published_at, title}."""
    hashes = [item.get("hash")]
    hashes += list(item.get("story_article_hashes") or [])
    hashes += [m.get("hash") for m in item.get("story_members") or () if isinstance(m, dict)]
    best: tuple[datetime, dict] | None = None
    for h in dict.fromkeys(h for h in hashes if h):
        row = dates.get(h)
        if h == item.get("hash") and row is None:
            row = {"published_at": item.get("published_at") or item.get("queued_at"),
                   "title": item.get("title_kr") or item.get("title") or ""}
        stamp = _parse((row or {}).get("published_at"))
        if stamp is None:
            continue
        if best is None or stamp < best[0]:
            best = (stamp, {"hash": h, "published_at": row["published_at"],
                            "title": row.get("title") or ""})
    return best[1] if best else None


def stale_firsts(rows: list[dict], dates: dict[str, dict], cutoff: datetime | None,
                 grace_hours: float) -> dict[str, dict]:
    """hash → 그 후보 스토리의 가장 이른 기사 — 그 기사가 직전 브리핑보다 묵은 후보만.

    기사 자체가 묵은 must_read(`stale_since`)는 이미 날짜를 달았으므로 건너뛴다.
    가장 이른 기사가 자기 자신이면(스토리에 더 이른 기사가 없음) 묻지 않는다.
    """
    if cutoff is None:
        return {}
    firsts: dict[str, dict] = {}
    for row in rows:
        if row.get("stale_since") or not row.get("hash"):
            continue
        head = first_seen(row, dates)
        if head and head["hash"] != row["hash"] and stale_since(
                {"published_at": head["published_at"]}, cutoff, grace_hours):
            firsts[row["hash"]] = head
    return firsts
