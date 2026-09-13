"""장기 스토리를 **그림자로** 만든다 — production UI 는 아직 이것을 읽지 않는다.

왜 그림자인가
-------------
기존 "스토리" 메뉴는 이미 있고, 이슈 중 추적 가치가 높은 것을 고르는 화면이다
(`archiveScope === "stories"`). 그 화면을 검증되지 않은 새 계층으로 바꾸면,
품질 문제가 곧바로 사용자에게 간다. 그래서 먼저 따로 만들어 재고 나서 잇는다.

산출물
------
    thread_ledger.json        영속 — 스토리와 그 관계(되돌릴 수 있게)
    web/_shadow/threads.json  파생 — 언제든 다시 만든다. 배포되지 않는다
    web/_shadow/report.json   이번 실행의 진단

입력은 `issue_ledger.json` 하나다. 카탈로그가 아니라 원장에서 읽는 것이 요점이다 —
카탈로그는 60일이면 사라지지만 원장은 지우지 않으므로, 몇 달 전 사건이 후보로
올라올 수 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asset_alias  # noqa: E402
import event_retrieval  # noqa: E402
import thread_identity  # noqa: E402
import thread_judge  # noqa: E402
import thread_ledger  # noqa: E402

SHADOW_DIR = ROOT / "web" / "_shadow"


def gather_pairs(index: event_retrieval.Index, *, per_event: int,
                 min_score: float, cap: int | None) -> list[dict]:
    """후보를 모은다. **recall 중심** — 거르는 것은 판정의 일이다."""
    seen: set[str] = set()
    rows: list[dict] = []
    for event in index.events:
        for candidate in event_retrieval.candidates(
                index, event, limit=per_event, min_score=min_score):
            other = index.by_id.get(candidate["issue_id"])
            if other is None:
                continue
            key = thread_judge.pair_id(event.issue_id, other.issue_id)
            if key in seen:
                continue
            seen.add(key)
            left, right = (event, other) if event.issue_id < other.issue_id else (other, event)
            rows.append({"key": key, "left": left, "right": right,
                         "score": candidate["score"], "gap_days": candidate["gap_days"],
                         "signals": candidate["signals"]})
    # 점수가 높은 쌍부터 묻는다. 예산이 걸리면 약한 쌍이 다음 회차로 밀린다.
    rows.sort(key=lambda row: (-row["score"], row["key"]))
    return rows[:cap] if cap else rows


def derive_scope(thread: dict, index: event_retrieval.Index) -> dict:
    """`story_scope` 를 **기계가** 만든다. 사람이 스토리마다 적는 구조를 만들지 않는다.

    범위는 두 쪽이 다 있어야 쓸모가 있다 — 무엇이 들어오는가와 **무엇이 닮았지만
    들어오지 않는가**. 뒤쪽이 오병합을 막는 실제 재료다.
    """
    members = [index.by_id[event_id] for event_id in thread["event_ids"]
               if event_id in index.by_id]
    units = sorted({unit for event in members for unit in event.units})
    entities = sorted({entity for event in members for entity in event.entities})
    plants = sorted({unit.rsplit("-", 1)[0] for unit in units})
    families = Counter()
    actions = Counter()
    for event in members:
        facts = event.raw.get("facts") or {}
        if facts.get("event_family"):
            families[str(facts["event_family"])] += 1
        if facts.get("action"):
            actions[str(facts["action"])] += 1

    # 같은 발전소의 **다른 호기**는 명시적으로 범위 밖이다. 자동으로 적을 수 있는
    # 가장 값어치 있는 제외 조항이라 이것부터 남긴다.
    excluded_units = sorted({
        unit for event in index.events for unit in event.units
        if unit.rsplit("-", 1)[0] in plants and unit not in set(units)
    })
    return {
        "includes": {"units": units, "entity_ids": entities,
                     "event_families": [name for name, _ in families.most_common(3)],
                     "actions": [name for name, _ in actions.most_common(4)]},
        "excludes": {"units": excluded_units[:12]},
        "derived_by": thread_identity.IDENTITY_VERSION,
    }


def load_milestones() -> list[dict]:
    """다음 관전점의 재료. **새 일정 시스템을 만들지 않는다** —

    `event_ledger.json` 이 이미 달력 창 밖의 확정 일정을 쌓고 있다(월성 2호기
    설계수명 만료 2026-11-01, ICRS15 2026-10-25 …). 추론으로 읽은 날짜는 애초에
    담기지 않으므로(`date_basis` 가 inferred 인 것은 제외) 이 목록은 그대로 쓸 수
    있다. `latest_change` 를 진실의 출처로 삼지 않는 이유와 같은 판단이다 —
    저쪽은 요약 차이를 사건 변화처럼 말한 적이 있다.
    """
    try:
        payload = json.loads((ROOT / "event_ledger.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = []
    for row in payload.get("events") or []:
        text = f"{row.get('title') or ''} {row.get('clause') or ''}"
        rows.append({**row, "units": asset_alias.unit_tokens(text),
                     "plants": asset_alias.plant_tokens(text)})
    rows.sort(key=lambda row: str(row.get("date") or ""))
    return rows


def next_milestone(thread: dict, milestones: list[dict]) -> dict | None:
    """이 스토리가 다음에 볼 공식 일정. 호기가 먼저, 없으면 발전소."""
    units = set(thread.get("units") or ())
    plants = {unit.rsplit("-", 1)[0] for unit in units}
    for row in milestones:
        if units and (units & row["units"]):
            return {"date": row.get("date"), "title": row.get("title"),
                    "label": row.get("label"), "matched_by": "unit"}
    for row in milestones:
        if plants and (plants & row["plants"]):
            return {"date": row.get("date"), "title": row.get("title"),
                    "label": row.get("label"), "matched_by": "plant"}
    return None


def build(args) -> int:
    started = time.time()
    index = event_retrieval.build_index()
    index_seconds = time.time() - started
    print(f"[threads] 사건 {len(index)}건 · 색인 {index_seconds:.2f}s · "
          f"{event_retrieval.index_size_bytes(index) / 1024:.0f} KB")

    pairs = gather_pairs(index, per_event=args.per_event,
                         min_score=args.min_score, cap=args.cap)
    print(f"[threads] 후보 {len(pairs)}쌍")

    verdicts, judge_stats = thread_judge.judge(
        pairs, client=None if args.live_llm else _OfflineClient(),
        max_new_pairs=args.max_new_pairs)
    print(f"[threads] 판정: 규칙거부 {judge_stats['rule_rejected']} · "
          f"캐시 {judge_stats['from_cache']} · 신규 {judge_stats['asked']} "
          f"(호출 {judge_stats['calls']}) · 실패 {judge_stats['failed']} "
          f"[{judge_stats['status']}]")

    accepted = [(row["left"].issue_id, row["right"].issue_id) for row in pairs
                if (verdicts.get(row["key"]) or {}).get("verdict") == "same_thread"]
    groups, cluster_stats = thread_identity.cluster(index.by_id, accepted)
    print(f"[threads] 묶음 {len(groups)}개 · 고리 {cluster_stats['links']} → "
          f"결합 {cluster_stats['joined']} · 거부(호기 {cluster_stats['blocked_conflict']} / "
          f"약한고리 {cluster_stats['blocked_weak_link']} / 크기 {cluster_stats['blocked_size']})")

    store = thread_ledger.load_store()
    owners = thread_ledger.owner_index(store)
    resolved = thread_identity.resolve(groups, index.by_id, owners)
    threads = resolved["threads"]
    identity = resolved["diagnostics"]
    print(f"[threads] 신원: 상속 {identity['inherited']} · 병합 {identity['merged']} "
          f"(흡수 {identity['merged_away_ids']}) · 분열 {identity['split']} · "
          f"신규 {identity['minted']}")

    relations: list[dict] = []
    for thread in threads:
        thread["scope"] = derive_scope(thread, index)
        for event_id in thread["event_ids"]:
            previous = owners.get(event_id)
            if previous == thread["thread_id"]:
                continue
            relations.append(thread_ledger.relation(
                event_id, thread["thread_id"], "attach",
                reason="판정이 같은 스토리로 보았다" if previous is None else "스토리가 합쳐졌다",
                evidence=thread["identity_evidence"],
                method="thread_judge", version=thread_judge.CONTRACT_VERSION))
        for loser in thread.get("merged_from") or []:
            relations.append(thread_ledger.relation(
                loser, thread["thread_id"], "merge",
                reason="first_seen 이 이른 쪽이 주소를 가진다",
                evidence=thread["identity_evidence"],
                method="thread_identity", version=thread_identity.IDENTITY_VERSION))
            entry = (store.get("threads") or {}).get(loser)
            if entry is not None:
                entry["moved_to"] = thread["thread_id"]

    live_ids = {thread["thread_id"] for thread in threads}
    for event_id, thread_id in owners.items():
        still = any(event_id in thread["event_ids"] for thread in threads)
        if not still:
            relations.append(thread_ledger.relation(
                event_id, thread_id, "detach",
                reason="이번 판정에서 같은 스토리로 보지 않았다",
                method="thread_judge", version=thread_judge.CONTRACT_VERSION))

    if not args.dry_run:
        result = thread_ledger.run(threads, relations)
        print(f"[threads] 원장: 신규 {result['counts']['added']} · "
              f"갱신 {result['counts']['updated']} · 관계 {result['counts']['relations']} · "
              f"누적 {result['counts']['total']}")

    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    # 규칙이 거부한 쌍도 남긴다. 승인만 남기면 **놓친 연결**이 평가에서 통째로
    # 빠지고, 그러면 일치율이 recall 에 대해 아무 말도 하지 않는다.
    (SHADOW_DIR / "rule_rejected.json").write_text(
        json.dumps(sorted(key for key, value in verdicts.items()
                          if value.get("method") == "rule"),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    milestones = load_milestones()
    payload = [_thread_view(thread, index) for thread in threads]
    for row in payload:
        row["next_milestone"] = next_milestone(row, milestones)
    payload.sort(key=lambda row: (-row["event_count"], row["thread_id"]))
    (SHADOW_DIR / "threads.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    sizes = Counter(len(thread["event_ids"]) for thread in threads)
    lifespans = [row["lifespan_days"] for row in payload if row["lifespan_days"] is not None]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "events": len(index),
        "index_seconds": round(index_seconds, 3),
        "index_bytes": event_retrieval.index_size_bytes(index),
        "candidate_pairs": len(pairs),
        "judge": judge_stats,
        "cluster": cluster_stats,
        "identity": identity,
        "threads": len(threads),
        "events_in_threads": sum(len(thread["event_ids"]) for thread in threads),
        "size_histogram": {str(key): value for key, value in sorted(sizes.items())},
        "lifespan_days": {
            "n": len(lifespans),
            "median": sorted(lifespans)[len(lifespans) // 2] if lifespans else 0,
            "max": max(lifespans) if lifespans else 0,
            "over_30": sum(1 for value in lifespans if value > 30),
        },
        "relations": Counter(row["decision"] for row in relations),
        "redirects": len(thread_ledger.redirects(store, live_ids)),
        "with_next_milestone": sum(1 for row in payload if row.get("next_milestone")),
        "wall_seconds": round(time.time() - started, 1),
    }
    report["relations"] = dict(report["relations"])
    (SHADOW_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[threads] 스토리 {len(threads)}개 · 사건 {report['events_in_threads']}건 · "
          f"수명 중앙 {report['lifespan_days']['median']}일 · "
          f"{report['wall_seconds']}s → {SHADOW_DIR.relative_to(ROOT)}")
    return 0


def _thread_view(thread: dict, index: event_retrieval.Index) -> dict:
    members = [index.by_id[event_id] for event_id in thread["event_ids"]
               if event_id in index.by_id]
    members.sort(key=lambda event: (event.first_seen or date.min, event.issue_id))
    first = members[0].first_seen if members else None
    last = max((event.last_seen for event in members if event.last_seen), default=None)
    return {
        "thread_id": thread["thread_id"],
        "title": thread["title"],
        "event_count": len(members),
        "first_seen": first.isoformat() if first else "",
        "last_seen": last.isoformat() if last else "",
        "lifespan_days": (last - first).days if (first and last) else None,
        "units": thread["units"],
        "entity_ids": thread["entity_ids"],
        "identity_origin": thread["identity_origin"],
        "scope": thread["scope"],
        "events": [{
            "event_id": event.issue_id,
            "title": event.title,
            "first_seen": event.first_seen.isoformat() if event.first_seen else "",
            "briefing_count": event.briefing_count,
        } for event in members],
    }


class _OfflineClient:
    """키를 쓰지 않겠다는 뜻을 명시한다. `is_available()` 이 False 면 판정은 규칙만."""

    @staticmethod
    def is_available() -> bool:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="장기 스토리 그림자 빌드")
    parser.add_argument("--per-event", type=int, default=8)
    parser.add_argument("--min-score", type=float, default=3.0)
    parser.add_argument("--cap", type=int, default=0, help="0 이면 전수")
    parser.add_argument("--max-new-pairs", type=int, default=None,
                        help="이번 실행에 새로 물을 쌍의 상한")
    parser.add_argument("--live-llm", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="원장을 쓰지 않는다")
    args = parser.parse_args()
    args.cap = args.cap or None
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())
