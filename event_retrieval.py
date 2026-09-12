"""몇 달 전 사건을 **다시 찾아온다** — 합치기 위해서가 아니라 후보로 올리기 위해.

무엇을 하는가
-------------
`issue_ledger.json` 은 지우지 않는다. 그래서 그 파일이 이미 장기 사건 원본이다
(2026-09-13 기준 798건 · 792 KB · git 추적). 여기서 하는 일은 그 위에 **매번 새로
만드는 색인**을 얹어, 오래 떨어진 두 사건을 같은 스토리 후보로 묶어 내는 것이다.

    영속 원본        issue_ledger.json — 사건과 그 근거 관계
    재생성 파생물    아래 색인 — 언제든 버리고 다시 만든다

무엇을 하지 않는가
------------------
외부 Vector DB 를 쓰지 않는다. 규모가 798건이고 연간 8천 건 남짓이라 색인을 통째로
메모리에 올리는 편이 단순하고 충분하다. 실측 빌드 시간은 이 파일 맨 아래 검사가
지킨다.

**합치지 않는다.** 여기서 나오는 것은 후보다. 후보 생성은 recall 중심이고 최종
판정은 precision 중심이라, 두 단계를 한 함수에 넣으면 둘 다 나빠진다.

왜 임베딩에만 기대지 않는가
---------------------------
기댈 수가 없다. 벡터는 `EMBEDDING_RETENTION_DAYS`(창 + 14일) 만큼만 살고, 로컬
폴백 벡터는 빌드 코퍼스로 IDF 를 계산하므로 **빌드가 다르면 같은 공간이 아니다.**
즉 의미 검색은 원리적으로 최근 구간만 덮는다. 장거리는 구조화 신호(호기·엔티티·
행동)와 어휘가 져야 한다. 임베딩은 있으면 더하는 신호지 기반이 아니다.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import asset_alias
import issue_ledger

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 분야만 같은 낱말. 이런 것이 겹쳤다고 후보로 올리면 후보 목록이 전부 원자력
# 기사가 된다 — `build_data._GENERIC_TAGS` 가 같은 이유로 같은 목록을 들고 있다.
_STOP = frozenset({
    "원전", "원자력", "에너지", "발전", "발전소", "정책", "산업", "시장", "기술",
    "안전", "국제", "협력", "사업", "계획", "추진", "확대", "관련", "위한", "통해",
    "nuclear", "energy", "power", "plant", "project", "npp", "the", "and", "for",
    "with", "from", "that", "this", "new", "will", "has", "its", "into",
})

# 신호별 가중치. 기준은 **판별력**이다 —
#   호기    가장 좁다. `kori-2` 는 한 대상만 가리킨다.
#   엔티티  설비·프로젝트는 좁고 기관·기업은 넓다(2026-08-05 실측: 기관까지 넣으면
#           같은 사건 3 대 다른 사건 40). 그래서 plant/project 만 강하게 본다.
#   어휘    표현이 흔들려도 남는 신호지만 혼자서는 분야만 같은 쌍을 끌어온다.
WEIGHTS = {
    "unit": 4.0,
    "entity": 2.2,
    "plant": 1.2,
    "assets": 1.6,
    "actors": 0.9,
    "lexical": 1.0,
    "action": 0.4,
}
# 호기가 어긋나면 같은 스토리일 수 없다. 후보 목록에서 **끌어내리되 지우지는**
# 않는다 — 지우면 그런 쌍이 있었다는 사실 자체가 평가에서 사라진다.
UNIT_CONFLICT_PENALTY = 6.0


def _tokens(text: object) -> set[str]:
    return {word for word in _TOKEN_RE.findall(str(text or "").lower())
            if len(word) > 1 and word not in _STOP}


def _event_text(event: dict) -> str:
    facts = event.get("facts") or {}
    return " ".join(str(part) for part in [
        event.get("title"), event.get("summary"),
        facts.get("assets"), facts.get("actors"), facts.get("action"),
        " ".join(event.get("topics") or []),
    ] if part)


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


@dataclass
class Event:
    issue_id: str
    title: str
    summary: str
    first_seen: date | None
    last_seen: date | None
    units: frozenset
    plants: frozenset
    entities: frozenset
    assets: frozenset
    actors: frozenset
    action: str
    tokens: frozenset
    briefing_count: int
    raw: dict = field(repr=False, default_factory=dict)


def load_events(path: Path | None = None) -> list[Event]:
    store = issue_ledger.load_store(path)
    out: list[Event] = []
    for issue_id, row in (store.get("issues") or {}).items():
        facts = row.get("facts") or {}
        text = _event_text(row)
        units = set(row.get("units") or ())
        plants = set(row.get("plants") or ())
        if not units:
            # 옛 원장 항목에는 이 칸이 없다. 제목에서 다시 읽는다 —
            # 원장을 통째로 다시 쓰지 않고도 옛 사건이 검색에 든다.
            units = asset_alias.unit_tokens(f"{row.get('title')} {row.get('summary')}")
            plants = plants or asset_alias.plant_tokens(row.get("title"))
        out.append(Event(
            issue_id=str(issue_id),
            title=str(row.get("title") or ""),
            summary=str(row.get("summary") or ""),
            first_seen=_day(row.get("first_seen")),
            last_seen=_day(row.get("last_seen")),
            units=frozenset(units),
            plants=frozenset(plants),
            entities=frozenset(row.get("entity_ids") or ()),
            assets=frozenset(_tokens(facts.get("assets"))),
            actors=frozenset(_tokens(facts.get("actors"))),
            action=str(facts.get("action") or ""),
            tokens=frozenset(_tokens(text)),
            briefing_count=int(row.get("briefing_count") or 0),
            raw=row,
        ))
    out.sort(key=lambda event: (event.first_seen or date.min, event.issue_id))
    return out


class Index:
    """역색인 + IDF. 원본에서 매번 다시 만든다 — 파생물이라 보존 대상이 아니다."""

    def __init__(self, events: list[Event]):
        self.events = events
        self.by_id = {event.issue_id: event for event in events}
        self.postings: dict[str, set[str]] = defaultdict(set)
        document_frequency: Counter = Counter()
        for event in events:
            for token in event.tokens:
                self.postings[f"t:{token}"].add(event.issue_id)
                document_frequency[token] += 1
            for unit in event.units:
                self.postings[f"u:{unit}"].add(event.issue_id)
            for plant in event.plants:
                self.postings[f"p:{plant}"].add(event.issue_id)
            for entity in event.entities:
                self.postings[f"e:{entity}"].add(event.issue_id)
        total = max(1, len(events))
        self.idf = {token: math.log(total / (1 + count)) + 1.0
                    for token, count in document_frequency.items()}

    def __len__(self) -> int:
        return len(self.events)

    @property
    def posting_count(self) -> int:
        return sum(len(value) for value in self.postings.values())


def _overlap_score(index: Index, left: Event, right: Event) -> tuple[float, dict]:
    shared_tokens = left.tokens & right.tokens
    lexical = sum(index.idf.get(token, 1.0) for token in shared_tokens)
    lexical = lexical / math.sqrt(max(1, len(left.tokens))) if lexical else 0.0

    parts = {
        "unit": len(left.units & right.units),
        "entity": len(left.entities & right.entities),
        "plant": len(left.plants & right.plants),
        "assets": len(left.assets & right.assets),
        "actors": len(left.actors & right.actors),
        "lexical": round(lexical, 3),
        "action": 1 if (left.action and left.action == right.action) else 0,
    }
    score = sum(WEIGHTS[name] * value for name, value in parts.items())
    conflict = asset_alias.conflict(
        f"{left.title} {left.summary}", f"{right.title} {right.summary}")
    if conflict:
        score -= UNIT_CONFLICT_PENALTY
    return score, {**parts, "unit_conflict": conflict,
                   "shared_tokens": sorted(shared_tokens)[:8]}


def candidates(index: Index, event: Event, *, limit: int = 12,
               min_score: float = 1.5, min_gap_days: int = 0,
               exclude_self: bool = True) -> list[dict]:
    """이 사건과 같은 스토리일 **가능성이 있는** 옛 사건들.

    `min_gap_days` 는 장거리만 보고 싶을 때 쓴다 — 최근 사건끼리는 이미 Event
    matcher 가 보고 있으므로, 여기서 재야 할 것은 **창 밖**이다.
    """
    pool: set[str] = set()
    for unit in event.units:
        pool |= index.postings.get(f"u:{unit}", set())
    for entity in event.entities:
        pool |= index.postings.get(f"e:{entity}", set())
    for plant in event.plants:
        pool |= index.postings.get(f"p:{plant}", set())
    # 어휘는 **판별력이 높은 낱말만** 역색인에서 꺼낸다. 흔한 낱말까지 펼치면
    # 후보 풀이 카탈로그 전체가 된다.
    ranked_tokens = sorted(event.tokens, key=lambda token: -index.idf.get(token, 0.0))
    for token in ranked_tokens[:12]:
        pool |= index.postings.get(f"t:{token}", set())
    if exclude_self:
        pool.discard(event.issue_id)

    rows: list[dict] = []
    for issue_id in pool:
        other = index.by_id.get(issue_id)
        if other is None:
            continue
        gap = _gap_days(event, other)
        if min_gap_days and (gap is None or gap < min_gap_days):
            continue
        score, parts = _overlap_score(index, event, other)
        if score < min_score:
            continue
        rows.append({"issue_id": issue_id, "title": other.title,
                     "score": round(score, 3), "gap_days": gap,
                     "first_seen": other.first_seen.isoformat() if other.first_seen else "",
                     "signals": parts})
    rows.sort(key=lambda row: (-row["score"], row["issue_id"]))
    return rows[:limit]


def _gap_days(left: Event, right: Event) -> int | None:
    """두 사건이 시간축에서 얼마나 떨어져 있는가(겹치면 0)."""
    if not (left.first_seen and left.last_seen and right.first_seen and right.last_seen):
        return None
    if left.last_seen < right.first_seen:
        return (right.first_seen - left.last_seen).days
    if right.last_seen < left.first_seen:
        return (left.first_seen - right.last_seen).days
    return 0


def build_index(path: Path | None = None) -> Index:
    return Index(load_events(path))


def index_size_bytes(index: Index) -> int:
    """색인이 얼마나 큰가 — 외부 저장소가 필요해지는 지점을 감시한다."""
    return len(json.dumps({key: sorted(value) for key, value in index.postings.items()},
                          ensure_ascii=False).encode("utf-8"))
