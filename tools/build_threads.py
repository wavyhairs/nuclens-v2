"""장기 스토리를 판정해 원장에 쌓는다. 화면은 원장을 읽지 이 실행을 읽지 않는다.

무엇이 어디로 가나
------------------
    thread_ledger.json        영속 — 스토리와 그 관계(되돌릴 수 있게). **커밋된다**
    web/_shadow/threads.json  파생 — 진단용. .gitignore 라 배포되지 않는다
    web/_shadow/report.json   이번 실행의 진단

판정은 비싸고(LLM) 투영은 싸다. 그래서 주기를 갈랐다 — 이 실행은 정기 빌드에서
하루 1회 돌아 원장을 갱신하고, 화면이 읽는 `data/threads.json` 은 빌드마다
`thread_web.py` 가 원장에서 다시 만든다. **기존 "스토리" 메뉴는 건드리지 않는다**
(`archiveScope === "stories"`) — 그쪽은 단위가 이슈고 이쪽은 그 이슈들을 묶은
상위 객체라 목록의 단위가 다르다. 장기 스토리는 별도 화면(Beta)으로 선다.

이 실행이 ok 로 끝나지 않으면 그 사실이 원장의 `build` 에 남고, 웹 투영이 그것을
보고 화면을 통째로 내린다. 반쪽 판정 그래프에서 나온 값을 내보내지 않는다.

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

import event_retrieval  # noqa: E402
import thread_identity  # noqa: E402
import thread_judge  # noqa: E402
import thread_ledger  # noqa: E402
import thread_web  # noqa: E402

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


# 다음 관전점은 그림자와 웹 투영이 **같은 것**을 써야 한다. 각자 짝을 지으면
# 같은 스토리에 다른 일정이 붙는다. 정의는 thread_web 에 있고 여기서는 이름만
# 빌려 온다 — tests/test_build_threads.py 가 계속 이 이름으로 부른다.
load_milestones = thread_web.load_milestones
next_milestone = thread_web.next_milestone


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
        # 이 회차가 어떻게 끝났는지를 원장에 같이 적는다. 그림자 진단은
        # .gitignore 라 다른 체크아웃에서 도는 웹 빌드가 볼 수 없다 —
        # 판정이 반쪽으로 끝난 회차를 화면이 알아보려면 커밋되는 파일에 남아야 한다.
        result = thread_ledger.run(threads, relations, build={
            "status": judge_stats["status"],
            "contract": judge_stats["contract"],
            "candidates": judge_stats["candidates"],
            "asked": judge_stats["asked"],
            "failed": judge_stats["failed"],
            "threads": len(threads),
            "events": sum(len(thread["event_ids"]) for thread in threads),
        })
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
