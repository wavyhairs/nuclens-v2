"""스토리 연결도 **독립 판정**을 받는다 (F11).

E2 와 같은 규율이되 단위가 다르다 — 저쪽은 기사 쌍, 이쪽은 **사건 쌍**이다.

가리는 것: `thread_judge` 의 판정 · 그 이유 · 관계 · 규칙 거부 여부 ·
두 사건이 실제로 한 스토리에 들어갔는지 · 검색 점수.

내보내는 것: 두 사건의 제목·기간·회차·호기·엔티티·지문 사실.

`build` 는 정답을 함께 쓰지 않는다. 정답은 `join` 이 그때 가서 `thread_ledger.json`
과 판정 캐시에서 다시 읽는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import event_retrieval  # noqa: E402
import thread_judge  # noqa: E402

EVAL_DIR = ROOT / "eval_artifacts"
BLIND_FILE = EVAL_DIR / "thread_pairs_blind.jsonl"
JUDGMENT_FILE = EVAL_DIR / "thread_judgments_claude.jsonl"
AGREEMENT_FILE = EVAL_DIR / "thread_judge_agreement.json"
SHADOW_DIR = ROOT / "web" / "_shadow"

VERDICTS = ("same_thread", "different_thread", "uncertain")


def _view(event) -> dict:
    facts = event.raw.get("facts") or {}
    return {
        "title": event.title,
        "summary": str(event.summary or "")[:400],
        "first_seen": event.first_seen.isoformat() if event.first_seen else "",
        "last_seen": event.last_seen.isoformat() if event.last_seen else "",
        "briefing_count": event.briefing_count,
        "units": sorted(event.units),
        "entity_ids": sorted(event.entities),
        "countries": list(event.raw.get("countries") or [])[:4],
        "facts": {key: facts.get(key, "") for key in
                  ("actors", "assets", "event_family", "action")},
    }


def _order(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def build(args) -> int:
    index = event_retrieval.build_index()
    cache = thread_judge.load_cache()
    # 판정을 받은 쌍 전부가 모집단이다 — 승인만 보면 **놓친 연결**이 평가에서
    # 통째로 빠진다. 규칙이 거부한 쌍도 표본에 넣는다.
    keys = list(cache)
    rejected_pool = []
    if (SHADOW_DIR / "rule_rejected.json").exists():
        rejected_pool = json.loads(
            (SHADOW_DIR / "rule_rejected.json").read_text(encoding="utf-8"))
    rng = random.Random(args.seed)
    if args.cap and len(keys) > args.cap:
        keys = sorted(rng.sample(keys, args.cap))
    sample_rejected = rejected_pool
    if args.cap_rejected and len(rejected_pool) > args.cap_rejected:
        sample_rejected = sorted(rng.sample(rejected_pool, args.cap_rejected))

    rows: list[dict] = []
    missing = 0
    for key, stratum in ([(k, "judged") for k in keys]
                         + [(k, "rule_rejected") for k in sample_rejected]):
        left_id, _, right_id = key.partition("--")
        left, right = index.by_id.get(left_id), index.by_id.get(right_id)
        if left is None or right is None:
            missing += 1
            continue
        rows.append({"pair_id": key, "stratum": stratum,
                     "a": _view(left), "b": _view(right)})
    rows.sort(key=lambda row: _order(row["pair_id"]))

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    BLIND_FILE.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")
    print(f"[thread-blind] {len(rows)}쌍 → {BLIND_FILE.relative_to(ROOT)} "
          f"(사건 못 찾음 {missing})")
    print("    " + json.dumps(dict(Counter(row["stratum"] for row in rows)),
                              ensure_ascii=False))

    if args.shards > 1:
        shard_dir = EVAL_DIR / "thread_shards"
        shard_dir.mkdir(parents=True, exist_ok=True)
        for old in shard_dir.glob("*.jsonl"):
            old.unlink()
        for index_no in range(args.shards):
            part = rows[index_no::args.shards]
            if not part:
                continue
            path = shard_dir / f"blind_{index_no:02d}.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in part),
                encoding="utf-8")
            print(f"    조각 {path.name}: {len(part)}쌍")
    return 0


def join(args) -> int:
    judgments = {}
    with JUDGMENT_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                judgments[str(row["pair_id"])] = row
    blind = {}
    with BLIND_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                blind[row["pair_id"]] = row

    production = thread_judge.load_cache()
    rule_rejected: set[str] = set()
    path = SHADOW_DIR / "rule_rejected.json"
    if path.exists():
        rule_rejected = set(json.loads(path.read_text(encoding="utf-8")))

    buckets = Counter()
    by_stratum: dict[str, Counter] = {}
    disagreements: list[dict] = []
    for key, row in judgments.items():
        claude = str(row.get("verdict") or "")
        stratum = (blind.get(key) or {}).get("stratum", "?")
        counter = by_stratum.setdefault(stratum, Counter())
        entry = production.get(key)
        if entry is not None:
            produced = str(entry.get("verdict") or "")
        elif key in rule_rejected:
            produced = "different_thread"
        else:
            produced = ""

        if not produced:
            bucket = "production_absent"
        elif claude == "uncertain":
            bucket = "claude_uncertain"
        elif claude == produced:
            bucket = ("agreement_same" if claude == "same_thread"
                      else "agreement_different")
        else:
            bucket = "disagreement"
            disagreements.append({
                "pair_id": key, "stratum": stratum,
                "production": produced,
                "production_reason": str((entry or {}).get("reason") or
                                         ("규칙 거부" if key in rule_rejected else "")),
                "claude": claude,
                "claude_reason": str(row.get("reason") or ""),
                "a_title": (blind.get(key) or {}).get("a", {}).get("title", ""),
                "b_title": (blind.get(key) or {}).get("b", {}).get("title", ""),
            })
        buckets[bucket] += 1
        counter[bucket] += 1

    comparable = sum(buckets[name] for name in
                     ("agreement_same", "agreement_different", "disagreement"))
    report = {
        "judged": len(judgments),
        "comparable": comparable,
        "buckets": dict(buckets),
        "agreement_rate": round(
            (buckets["agreement_same"] + buckets["agreement_different"]) / comparable, 4
        ) if comparable else 0,
        "by_stratum": {name: dict(counter) for name, counter in sorted(by_stratum.items())},
        "claude_verdicts": dict(Counter(str(row.get("verdict")) for row in judgments.values())),
        "relationships": dict(Counter(str(row.get("relationship") or "")
                                      for row in judgments.values())),
        "disagreements": disagreements[:60],
        "disagreement_total": len(disagreements),
    }
    AGREEMENT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("judged", "comparable", "buckets", "agreement_rate",
                       "claude_verdicts")}, ensure_ascii=False, indent=1))
    print(f"[join] → {AGREEMENT_FILE.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="스토리 연결 독립 판정")
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--cap", type=int, default=200)
    builder.add_argument("--cap-rejected", type=int, default=60)
    builder.add_argument("--shards", type=int, default=1)
    builder.add_argument("--seed", type=int, default=20260913)
    builder.set_defaults(func=build)
    joiner = sub.add_parser("join")
    joiner.set_defaults(func=join)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
