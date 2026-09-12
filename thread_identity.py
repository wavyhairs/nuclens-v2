"""장기 스토리의 신원 authority — 묶고, 이름을 정하고, 오염을 막는다.

D 에서 배운 것을 그대로 쓴다. 다만 **기본 단위가 다르다.**

    D (사건 신원)   증거 = 기사 해시        — 한 기사는 정확히 한 이슈에 속한다
    F (스토리 신원) 증거 = 사건 id          — 한 사건은 정확히 한 스토리에 속한다

기사 해시를 스토리의 직접 신원으로 삼지 않는다. 스토리는 사건 위의 계층이고,
사건은 이미 D 가 안정시켜 놓았다. 해시까지 내려가면 두 계층이 같은 흔들림을
두 번 겪는다.

신원 규칙 다섯 — D 와 같은 뼈대
--------------------------------
1. 소유자 없음        → 발급
2. 소유자 하나        → 상속
3. 소유자 둘 이상     → `first_seen` 이 가장 이른 쪽. 묶음 크기는 늘고 줄지만
                        최초 관측일은 단조롭다
4. 한 옛 id 를 둘이 주장 → 공유 사건이 많은 쪽. 동수면 가장 이른 사건을 가진 쪽
5. 발급도 충돌한다    → 상속이 우선권을 갖는다

오염 방지 (F6)
--------------
약한 고리 하나로 서로 다른 사안이 한 스토리로 연쇄 병합되는 것을 막는다.

    · 묶음끼리 합칠 때 **묶음 전체**를 다시 본다. 한 쌍만 보면 안 된다.
    · 이미 둘 이상인 묶음 둘을 잇는 데는 **고리 두 개**가 필요하다.
    · 호기 모순은 묶음 어느 자리에서든 거부다. 브리지 예외가 없다 —
      `build_data._cluster_facility_conflict` 가 같은 말을 한다.
    · 크기 상한. 하나가 계속 자라면 그것은 스토리가 아니라 주제다.

지문 모순은 **거부권이 아니라 감점**이다. 2026-08-19 에 `_cluster_fingerprint_conflict`
를 모든 합류에 걸었더니 테라파워 12건 묶음이 5개로 쪼개졌다 — 하나의 긴 사건
안에서 원인·대상 축은 원래 움직인다. 장기 스토리는 그 움직임이 더 크다.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date

import asset_alias

IDENTITY_VERSION = "thread-identity-v1"
THREAD_ID_PREFIX = "thread-"

# 한 스토리가 들 수 있는 사건 수. 넘으면 붙이지 않는다 — 계속 자라는 묶음은
# 스토리가 아니라 주제다(메모의 "Topic 과 Story 혼동").
MAX_EVENTS_PER_THREAD = 30
# 이미 여럿인 묶음 둘을 잇는 데 필요한 고리 수.
LINKS_TO_JOIN_CLUSTERS = 2


def mint_id(anchor_event_id: str) -> str:
    """앵커 사건 id 에서 만든다. **사건 id 를 그대로 쓰지 않는다** —

    `issue-abc` 와 `thread-abc` 가 나란히 있으면 사람도 코드도 헷갈린다. 이
    저장소는 이름 충돌로 이미 여러 번 사고가 났다.
    """
    digest = hashlib.sha256(str(anchor_event_id).encode("utf-8")).hexdigest()[:16]
    return f"{THREAD_ID_PREFIX}{digest}"


class _Union:
    def __init__(self, keys):
        self.parent = {key: key for key in keys}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, left, right) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        self.parent[right_root] = left_root
        return True

    def groups(self) -> dict:
        out = defaultdict(set)
        for key in self.parent:
            out[self.find(key)].add(key)
        return out


def _text(event) -> str:
    return f"{event.title} {event.summary}"


def cluster(events_by_id: dict, accepted: list[tuple[str, str]]) -> tuple[list[set], dict]:
    """승인된 고리로 사건을 묶는다. 오염 방지는 여기서 건다.

    Args:
        accepted: (event_id, event_id) — 판정이 같은 스토리라고 한 쌍.
    """
    stats = {"links": len(accepted), "joined": 0, "blocked_conflict": 0,
             "blocked_scope": 0, "blocked_weak_link": 0, "blocked_size": 0}
    link_count: dict[tuple[str, str], int] = defaultdict(int)
    for left, right in accepted:
        link_count[tuple(sorted((left, right)))] += 1

    union = _Union(events_by_id)
    members = {key: {key} for key in events_by_id}

    # 고리가 많은 쌍부터. 약한 고리가 먼저 묶음을 키우면 그 뒤의 검사가 이미
    # 오염된 묶음을 보게 된다.
    ordered = sorted(link_count, key=lambda pair: (-link_count[pair], pair))
    for left, right in ordered:
        left_root, right_root = union.find(left), union.find(right)
        if left_root == right_root:
            continue
        left_side, right_side = members[left_root], members[right_root]

        if len(left_side) + len(right_side) > MAX_EVENTS_PER_THREAD:
            stats["blocked_size"] += 1
            continue
        if _cluster_conflict(events_by_id, left_side, right_side):
            stats["blocked_conflict"] += 1
            continue
        shared_scope = _shared_scope(events_by_id, left_side, right_side)
        if _scope_disjoint(events_by_id, left_side, right_side):
            # 양쪽이 다 호기를 말하는데 겹치는 것이 하나도 없다. 같은 발전소면
            # 위의 모순 검사가 이미 잡았으므로, 여기 걸리는 것은 **아예 다른
            # 설비**다. 고리 2호기 묶음과 월성 1호기 묶음이 고리 하나로 이어지는
            # 자리가 정확히 여기다.
            stats["blocked_scope"] += 1
            continue
        crossing = sum(
            link_count.get(tuple(sorted((a, b))), 0)
            for a in left_side for b in right_side
        )
        # 고리 하나로 묶음을 키우는 것 자체는 정상 성장이다. 막아야 하는 것은
        # **연쇄** — 이미 여럿인 묶음에 약한 고리 하나로 새 묶음이 붙고, 그 묶음을
        # 발판 삼아 다음이 붙는 것. 그래서 상대가 이미 여럿이면 공유 범위(호기·
        # 엔티티)를 요구하고, 그것도 없으면 고리 두 개를 요구한다.
        if max(len(left_side), len(right_side)) > 1 and not shared_scope:
            if crossing < LINKS_TO_JOIN_CLUSTERS:
                stats["blocked_weak_link"] += 1
                continue

        union.union(left, right)
        new_root = union.find(left)
        merged = left_side | right_side
        members[new_root] = merged
        stats["joined"] += 1

    groups = [group for group in union.groups().values() if len(group) > 1]
    groups.sort(key=lambda group: (min(group), len(group)))
    return groups, stats


def _units_of(events_by_id: dict, side: set) -> set:
    out: set = set()
    for event_id in side:
        event = events_by_id.get(event_id)
        if event is not None:
            out |= set(event.units)
    return out


def _entities_of(events_by_id: dict, side: set) -> set:
    out: set = set()
    for event_id in side:
        event = events_by_id.get(event_id)
        if event is not None:
            out |= set(event.entities)
    return out


def _shared_scope(events_by_id: dict, left_side: set, right_side: set) -> bool:
    """두 묶음이 **같은 대상**을 말하는가. 호기가 우선, 없으면 엔티티."""
    left_units, right_units = _units_of(events_by_id, left_side), _units_of(events_by_id, right_side)
    if left_units and right_units:
        return bool(left_units & right_units)
    left_entities = _entities_of(events_by_id, left_side)
    right_entities = _entities_of(events_by_id, right_side)
    return bool(left_entities and right_entities and (left_entities & right_entities))


def _scope_disjoint(events_by_id: dict, left_side: set, right_side: set) -> bool:
    left_units, right_units = _units_of(events_by_id, left_side), _units_of(events_by_id, right_side)
    return bool(left_units and right_units and not (left_units & right_units))


def _cluster_conflict(events_by_id: dict, left_side: set, right_side: set) -> bool:
    """묶음 **전체** 사이에 호기 모순이 있는가. 한 자리라도 있으면 거부다."""
    for left in left_side:
        left_event = events_by_id.get(left)
        if left_event is None:
            continue
        for right in right_side:
            right_event = events_by_id.get(right)
            if right_event is None:
                continue
            if asset_alias.conflict(_text(left_event), _text(right_event)):
                return True
    return False


def _first_seen(event) -> date:
    return event.first_seen or date.max


def resolve(groups: list[set], events_by_id: dict, owners: dict[str, str]) -> dict:
    """묶음마다 thread_id 를 정한다. 상속이 발급을 이긴다.

    Returns:
        ``{"threads": [...], "diagnostics": {...}}``
    """
    diagnostics = {"inherited": 0, "merged": 0, "merged_away_ids": 0,
                   "split": 0, "minted": 0, "version": IDENTITY_VERSION}
    claims: dict[str, list[int]] = defaultdict(list)
    resolved: list[dict | None] = [None] * len(groups)

    # 1차 — 각 묶음이 어떤 옛 id 를 주장하는지 모은다.
    for index, group in enumerate(groups):
        owned = {owners[event_id] for event_id in group if event_id in owners}
        owned.discard("")
        for thread_id in owned:
            claims[thread_id].append(index)

    # 2차 — 한 옛 id 를 여럿이 주장하면(분열) 공유 사건이 많은 쪽이 가진다.
    winner_of: dict[str, int] = {}
    for thread_id, indexes in claims.items():
        if len(indexes) == 1:
            winner_of[thread_id] = indexes[0]
            continue
        def _weight(index: int) -> tuple:
            group = groups[index]
            shared = sum(1 for event_id in group if owners.get(event_id) == thread_id)
            earliest = min((_first_seen(events_by_id[e]) for e in group
                            if e in events_by_id), default=date.max)
            return (-shared, earliest, min(group))
        best = sorted(indexes, key=_weight)[0]
        winner_of[thread_id] = best
        diagnostics["split"] += len(indexes) - 1

    # 3차 — 묶음마다 id 를 정한다.
    for index, group in enumerate(groups):
        inherited = sorted(
            thread_id for thread_id, owner_index in winner_of.items()
            if owner_index == index
        )
        anchor = min(group, key=lambda event_id: (
            _first_seen(events_by_id[event_id]) if event_id in events_by_id else date.max,
            event_id))
        if not inherited:
            resolved[index] = {"thread_id": mint_id(anchor), "origin": "minted",
                               "evidence": [anchor], "merged_from": []}
            diagnostics["minted"] += 1
            continue
        if len(inherited) == 1:
            resolved[index] = {"thread_id": inherited[0], "origin": "inherited",
                               "evidence": sorted(group)[:8], "merged_from": []}
            diagnostics["inherited"] += 1
            continue
        # 병합 — `first_seen` 이 가장 이른 쪽의 id 가 이긴다. 오래된 주소일수록
        # 밖에 나가 있고, 최초 관측일은 묶음 크기와 달리 단조롭다.
        def _thread_age(thread_id: str) -> tuple:
            owned = [event_id for event_id, owner in owners.items()
                     if owner == thread_id and event_id in events_by_id]
            earliest = min((_first_seen(events_by_id[e]) for e in owned), default=date.max)
            return (earliest, thread_id)
        winner = sorted(inherited, key=_thread_age)[0]
        losers = [value for value in inherited if value != winner]
        resolved[index] = {"thread_id": winner, "origin": "merged",
                           "evidence": sorted(group)[:8], "merged_from": losers}
        diagnostics["merged"] += 1
        diagnostics["merged_away_ids"] += len(losers)

    # 4차 — 발급도 충돌한다. 상속이 우선권을 갖고 발급이 비켜선다.
    taken = {row["thread_id"] for row in resolved if row and row["origin"] != "minted"}
    for index, row in enumerate(resolved):
        if row is None or row["origin"] != "minted":
            continue
        if row["thread_id"] not in taken:
            taken.add(row["thread_id"])
            continue
        for event_id in sorted(groups[index]):
            candidate = mint_id(f"{event_id}#2")
            if candidate not in taken:
                row["thread_id"] = candidate
                row["evidence"] = [event_id]
                taken.add(candidate)
                break

    threads = []
    for index, group in enumerate(groups):
        row = resolved[index]
        members = [events_by_id[event_id] for event_id in group if event_id in events_by_id]
        if not members:
            continue
        members.sort(key=lambda event: (_first_seen(event), event.issue_id))
        units, entities = set(), set()
        for event in members:
            units |= set(event.units)
            entities |= set(event.entities)
        threads.append({
            "thread_id": row["thread_id"],
            "anchor_event_id": row["evidence"][0] if row["evidence"] else "",
            "event_ids": [event.issue_id for event in members],
            "title": members[0].title,
            "first_seen": members[0].first_seen.isoformat() if members[0].first_seen else "",
            "last_seen": max((event.last_seen for event in members
                              if event.last_seen), default=None),
            "units": sorted(units),
            "entity_ids": sorted(entities),
            "identity_origin": row["origin"],
            "identity_evidence": row["evidence"],
            "merged_from": row["merged_from"],
        })
    for thread in threads:
        last = thread["last_seen"]
        thread["last_seen"] = last.isoformat() if hasattr(last, "isoformat") else (last or "")
    return {"threads": threads, "diagnostics": diagnostics}
