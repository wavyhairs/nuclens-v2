"""재생 결과를 사람이 읽을 형태로 뽑는다 — 보고서용 표와 사례. 판정을 바꾸지 않는다."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.object_identity import replay as R  # noqa: E402
from experiments.object_identity.resolver import Resolver  # noqa: E402


def main(live_issues: str, human_labels: str, arm_kwargs: dict, *, gate_entities: bool = False) -> None:
    archive = R.load_archive(ROOT)
    ledger = R.load_json(ROOT / "issue_ledger.json")
    live = R.load_json(Path(live_issues)) if Path(live_issues).exists() else []
    part, _ = R.production_partition(ledger, live)
    pop = set(part)
    for line in (ROOT / "delivery_log.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("hash") and not row.get("record_type"):
                pop.add(row["hash"])
    items = R.build_items(pop, archive, part, gate_entities=gate_entities)
    for item in items:
        if not item.production_issue:
            item.production_issue = f"issue-{item.hash}"
            part[item.hash] = item.production_issue
    by = {i.hash: i for i in items}
    approved, rejected = R.human_pairs(R.load_json(ROOT / "issue_match_overrides.json"))
    gold = R.load_gold(ROOT, Path(human_labels) if human_labels else None)
    res = Resolver(rejected_pairs=rejected, approved_pairs=approved, **arm_kwargs).resolve(items)
    use_lexicon = arm_kwargs.get("use_lexicon", False)

    def same_p(a, b):
        return part[a] == part[b]

    def same_r(a, b):
        return res.assignment[a] == res.assignment[b]

    print("=== 사람 판정 22쌍: production / resolver / 사람")
    c = Counter()
    for cid, g in gold.items():
        if g.get("source") != "human":
            continue
        a, b = cid.split("--")
        if a not in by or b not in by:
            print("  (모집단 밖)", cid)
            continue
        A, B = by[a], by[b]
        want = "same" if g["relation"] in R.SAME else ("split" if g["relation"] in R.DISTINCT else "split(material)")
        p = "same" if same_p(a, b) else "split"
        r = "same" if same_r(a, b) else "split"
        c[("prod_ok", p == want.split("(")[0])] += 1
        c[("res_ok", r == want.split("(")[0])] += 1
        print(f"  {g['relation'][:22]:22s} want={want:15s} prod={p:5s} res={r:5s} {res.grade_of[a]}/{res.grade_of[b]} gap={R._gap(A.effective_date, B.effective_date)}d")
        print(f"      A {A.effective_date} {R._obj_label(A, use_lexicon)} {A.title_kr[:60]}")
        print(f"      B {B.effective_date} {R._obj_label(B, use_lexicon)} {B.title_kr[:60]}")
    print("  ", dict(c))

    print("\n=== gold 쌍 간격 분포 (일)")
    gaps = Counter()
    for cid in gold:
        a, b = cid.split("--")
        if a in by and b in by:
            g = R._gap(by[a].effective_date, by[b].effective_date)
            gaps["0-7" if g <= 7 else "8-21" if g <= 21 else "22-35" if g <= 35 else "36+"] += 1
    print("  ", dict(gaps))

    print("\n=== 호기(unit) Event 전수")
    for event in sorted(res.events, key=lambda e: (e.first_date, e.event_id)):
        if event.object_key is None or event.object_key[0] != "unit":
            continue
        print(f"  [{event.event_id}] {'+'.join(sorted(event.object_key[1]))} stages={sorted(event.stages)} "
              f"{event.first_date}~{event.last_date} n={len(event.members)} prod_issues={len(event.production_issues)}")
        for m in sorted(event.members, key=lambda m: m.effective_date):
            print(f"      {m.effective_date} [{res.reason_of[m.hash]:18s}] {m.title_kr[:70]}")

    print("\n=== 같은 Object 위의 stage_split (material 보존이 실제로 일어난 자리)")
    by_event = {e.event_id: e for e in res.events}
    for h, reason in res.reason_of.items():
        if reason != "new:stage_split":
            continue
        item = by[h]
        key = item.signals.key(use_lexicon=use_lexicon)
        others = [e for e in res.events if e.object_key and e.event_id != res.assignment[h]
                  and e.object_key[0] == key[0] and (e.object_key[1] & key[1])]
        print(f"  {item.effective_date} {R._obj_label(item, use_lexicon)} stages={sorted(item.stages())} prod={part[h][-8:]} :: {item.title_kr[:60]}")
        for e in others[:3]:
            m = e.members[-1]
            print(f"      ↔ {m.effective_date} stages={sorted(e.stages)} prod={m.production_issue[-8:]} :: {m.title_kr[:60]}")

    print("\n=== production 묶음을 넘어 붙인 object-grade 결합 (kind 별 건수, 간격)")
    joins = defaultdict(list)
    for event in res.events:
        if event.object_key is None or len(event.production_issues) < 2:
            continue
        kind = event.object_key[0]
        by_prod = defaultdict(list)
        for m in event.members:
            by_prod[m.production_issue].append(m)
        dates = sorted(min(m.effective_date for m in ms) for ms in by_prod.values())
        joins[kind].append((len(by_prod), R._gap(dates[0], dates[-1])))
    for kind, rows in joins.items():
        print(f"  {kind}: events_with_joins={len(rows)} issues_joined={sum(r[0] for r in rows)} max_gap={max(r[1] for r in rows)}")


if __name__ == "__main__":
    live, human = sys.argv[1], sys.argv[2]
    kwargs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    main(live, human, kwargs)
