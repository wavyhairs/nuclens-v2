"""아침 브리핑의 LLM 없는 일일 품질 신호 — 늦은 발송·재발송이 줄었는지 매일 잰다.

왜 있는가
---------
2026-09-26 발송분 점검(`.eval/delivery-audit-2026-09-26`)은 Claude 가 243건을 한 건씩
판정해서야 늦은 발송 45건·재발송 42건을 셌다. 그 판정을 매일 할 수는 없다. 대신
판정 라벨과 맞춰 본 **값싼 신호 셋**을 selection_stats 에 남겨, 신선도 게이트(#195)·
연속일 대조(#197)·사건 신선도(#199)·글 종류(#200)가 효과를 냈는지 추세로 본다.
게이트가 아니다 — 아무것도 빼지 않는다.

신호 (날짜는 모두 KST, D = 브리핑 날짜)
--------------------------------------
- ``article_older_2d``: 기사 게재일 ≤ D−2.
- ``story_older_2d``: 같은 스토리(자기+멤버) 가운데 가장 이른 기사의 게재일 ≤ D−2
  (freshness.first_seen). 기사는 새것인데 사건이 묵은 경우까지 잡는다.
- ``similar_7d``: D−7 ~ D−1 발송분 가운데 제목 유사도(공백·기호를 뺀 SequenceMatcher)
  가 ``SIMILAR_RATIO`` 이상인 것이 있다.

기준선 (9/13~26 발송 243건, 이 모듈 그대로): 아래 ``BASELINE`` — 게재 2일+ 13.2%,
스토리 2일+ 28.8%, 7일 내 닮은 제목 11.5%. 유사도 0.5 는 판정
라벨과 맞춘 값이다 — 걸린 28건 중 재발송·각도만 바꾼 재발송 19, 정상 7(정밀도 68%,
재발송 42건 중 45%). 0.4 로 내리면 67건에 정상 26건이 섞여 추세가 흐려진다.
"""
from __future__ import annotations

import difflib
import re
from datetime import date, timedelta

import freshness

SIMILAR_RATIO = 0.5
WINDOW_DAYS = 7
# 판정 라벨별로 보면 — story_older_2d: 늦음 32/45·재발송 16/33·정상 12/135,
# article_older_2d: 늦음 17/45·정상 1/135, similar_7d: 재발송·각도만 19/42·정상 7/135.
BASELINE = {"period": "2026-09-13~26", "sent": 243,
            "article_older_2d": 32, "story_older_2d": 70, "similar_7d": 28}

_NOISE = re.compile(r"[\s\W_]+")


def _norm(title: str) -> str:
    return _NOISE.sub("", title or "").lower()


def _kst_date(value: object) -> date | None:
    stamp = freshness._parse(value)
    return stamp.astimezone(freshness.KST).date() if stamp else None


def _title(item: dict) -> str:
    return str(item.get("title_kr") or item.get("title") or "")


def most_similar(title: str, sent: list[dict]) -> tuple[float, dict | None]:
    """발송분 가운데 제목이 가장 닮은 것과 그 비율."""
    norm = _norm(title)
    best: tuple[float, dict | None] = (0.0, None)
    if not norm:
        return best
    for row in sent:
        other = _norm(_title(row))
        if not other:
            continue
        ratio = difflib.SequenceMatcher(None, norm, other).ratio()
        if ratio > best[0]:
            best = (ratio, row)
    return best


def compute(selected: list[dict], today: str, dates: dict[str, dict],
            recent_sent: list[dict]) -> dict:
    """오늘 나가는 기사들의 신호. ``recent_sent`` 는 delivery_log 의 기사 레코드."""
    day = date.fromisoformat(today)
    old_line = day - timedelta(days=2)
    window = [row for row in recent_sent
              if (day - timedelta(days=WINDOW_DAYS)).isoformat()
              <= str(row.get("date") or "") < today]
    counts = {"article_older_2d": 0, "story_older_2d": 0, "similar_7d": 0}
    samples: list[dict] = []
    for item in selected:
        flags: dict = {}
        published = _kst_date(item.get("published_at") or item.get("queued_at"))
        if published and published <= old_line:
            counts["article_older_2d"] += 1
            flags["published"] = published.isoformat()
        first = freshness.first_seen(item, dates)
        first_day = _kst_date((first or {}).get("published_at"))
        if first_day and first_day <= old_line:
            counts["story_older_2d"] += 1
            flags["first_seen"] = first_day.isoformat()
        ratio, match = most_similar(_title(item), window)
        if match is not None and ratio >= SIMILAR_RATIO:
            counts["similar_7d"] += 1
            flags["similar_to"] = {"date": match.get("date", ""), "title": _title(match)[:80],
                                   "ratio": round(ratio, 2)}
        if flags:
            samples.append({"hash": item.get("hash", ""), "title": _title(item)[:80], **flags})
    return {"sent": len(selected), **counts, "similar_ratio": SIMILAR_RATIO,
            "samples": samples[:12]}
