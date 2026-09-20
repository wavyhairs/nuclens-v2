"""과거 데이터 전체 재생 — Object 기반 resolver 를 production 분할과 같은 쌍 위에서 대조한다.

무엇을 재생하나
    모집단 = 원장(issue_ledger.json) 해시 ∪ 라이브 issues.json 해시 ∪ 발송 카드(delivery_log) 해시.
    기사 본문·태그·큐레이션 게이트 산출물(verified_evidence)은 archive/*.jsonl 에서 읽는다.
    LLM 0회 · 네트워크 0회 · 저장소 파일 변경 0개 (산출물은 eval_artifacts/object_identity 와
    gitignore 된 web/_shadow/object_identity 에만 쓴다).

production 기준선
    해시 → 묶음은 `event_identity.owner_index`(production 의 소유권 규칙 그대로) 로 풀고 `moved_to`
    를 끝까지 따라간다. 동값으로 버려진 해시는 라이브 issues.json 이 말해 주는 묶음으로 채운다.

지표 (같은 쌍 위에서 두 시스템을 비교)
    false merge          gold 가 다른 사건(RELATED_DISTINCT_EVENT·UNRELATED)이라 했는데 같은 Event 로 둔 비율
    false split          gold 가 같은 사건(SAME_EVENT·FOLLOW_UP_NO_NEW_ACTION)이라 했는데 갈라 둔 비율
    false split recovery production 이 갈라 둔 같은-사건 쌍 중 resolver 가 붙인 비율
    material 보존        gold 가 새 행동(FOLLOW_UP_NEW_ACTION)이라 한 쌍을 **다른 Event** 로 둔 비율.
                         전수에서는 production 자신의 progression() 이 material 이라 한 인접 쌍이
                         한 Event 안에 접힌 건수(material collapse)로도 잰다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import event_identity  # noqa: E402  (production 의 소유권 규칙을 읽기 전용으로 재사용)
import issue_continuity  # noqa: E402

from experiments.object_identity import objects as objmod  # noqa: E402
from experiments.object_identity.resolver import Item, Resolver, Resolution  # noqa: E402

OUT_DIR = ROOT / "eval_artifacts" / "object_identity"
DUMP_DIR = ROOT / "web" / "_shadow" / "object_identity"

DISTINCT = {"RELATED_DISTINCT_EVENT", "UNRELATED"}
SAME = {"SAME_EVENT", "FOLLOW_UP_NO_NEW_ACTION"}
MATERIAL = {"FOLLOW_UP_NEW_ACTION"}


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _gap(left: str, right: str) -> int:
    a, b = _day(left), _day(right)
    return abs((a - b).days) if a and b else -1


def pair_id(a: str, b: str) -> str:
    return "--".join(sorted((a, b)))


# ---- 입력 -------------------------------------------------------------------------

def load_archive(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "archive").glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("hash"):
                    out[row["hash"]] = row
    return out


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def production_partition(ledger: dict, live_rows: list[dict]) -> tuple[dict[str, str], dict]:
    """해시 → production 묶음 id (원장 소유권 → moved_to 추적 → 라이브 보정)."""
    issues = ledger.get("issues") or {}
    owners = event_identity.owner_index(ledger)

    def surviving(issue_id: str) -> str:
        seen = set()
        while issue_id in issues and issues[issue_id].get("moved_to") and issue_id not in seen:
            seen.add(issue_id)
            issue_id = str(issues[issue_id]["moved_to"])
        return issue_id

    live_owner: dict[str, str] = {}
    for row in live_rows:
        for article in row.get("related_articles") or []:
            h = str(article.get("hash") or "")
            if h:
                live_owner.setdefault(h, str(row.get("issue_id") or ""))

    out: dict[str, str] = {}
    dropped_filled = 0
    for h, issue_id in owners.items():
        out[h] = surviving(issue_id)
    all_hashes = {h for entry in issues.values() for h in entry.get("hashes") or []}
    for h in all_hashes - set(out):
        if h in live_owner:
            out[h] = live_owner[h]
            dropped_filled += 1
    for h, issue_id in live_owner.items():
        out.setdefault(h, issue_id)
    disagreements = sum(1 for h, issue_id in live_owner.items() if h in owners and out[h] != issue_id)
    return out, {
        "ledger_issues": len(issues), "ledger_hashes": len(all_hashes),
        "owner_index_hashes": len(owners), "tie_dropped_filled_from_live": dropped_filled,
        "live_hashes": len(live_owner), "ledger_vs_live_disagreements": disagreements,
    }


def build_items(population: set[str], archive: dict[str, dict], partition: dict[str, str],
                *, gate_entities: bool = False) -> list[Item]:
    items: list[Item] = []
    for h in sorted(population):
        row = archive.get(h)
        if not row:
            continue
        verified = row.get("verified_evidence") or {}
        items.append(Item(
            hash=h,
            # 116건은 `pub` 이 비어 있다(7월 수집분). 수집 시각이 그 다음으로 가까운 날짜다.
            pub_date=str(row.get("pub") or row.get("published_at") or row.get("archived_at") or "")[:10],
            event_date=str(row.get("event_date") or "")[:10],
            title_kr=str(row.get("title_kr") or ""),
            title=str(row.get("title") or ""),
            summary=str(row.get("summary") or ""),
            tags=[str(t) for t in (row.get("tags") or [])],
            topics=[str(t) for t in (row.get("topics") or [])],
            countries=[str(c) for c in (row.get("countries") or [])],
            gate_stages=frozenset(str(s) for s in (verified.get("stages") or [])),
            production_issue=partition.get(h, ""),
            signals=objmod.extract(row, gate_entities=gate_entities),
        ))
    return items


def human_pairs(overrides: dict) -> tuple[set[frozenset[str]], set[frozenset[str]]]:
    def pairs(rows) -> set[frozenset[str]]:
        return {frozenset((str(r["left_hash"]), str(r["right_hash"]))) for r in rows or [] if isinstance(r, dict)}
    return pairs(overrides.get("approved")), pairs(overrides.get("rejected"))


# ---- 정답 ---------------------------------------------------------------------------

def load_gold(root: Path, human_path: Path | None) -> dict[str, dict]:
    """candidate_id → {relation, source, axis, ...}. 사람 판정이 Claude 판정을 덮는다."""
    gold: dict[str, dict] = {}
    blind = {}
    blind_file = root / "eval_artifacts" / "event_pairs_blind.jsonl"
    if blind_file.exists():
        for line in blind_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                blind[row["candidate_id"]] = row
    judged = root / "eval_artifacts" / "event_judgments_claude.jsonl"
    if judged.exists():
        for line in judged.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            gold[row["candidate_id"]] = {
                "relation": row.get("relation"), "source": "claude_blind",
                "axis": row.get("difference_axis"), "confidence": row.get("confidence"),
                "stratum": (blind.get(row["candidate_id"]) or {}).get("stratum", ""),
                "same_thread": row.get("same_thread"),
            }
    if human_path and human_path.exists():
        labels = (load_json(human_path) or {}).get("labels") or {}
        for cid, relation in labels.items():
            gold[cid] = {**gold.get(cid, {}), "relation": relation, "source": "human",
                         "stratum": (blind.get(cid) or {}).get("stratum", gold.get(cid, {}).get("stratum", "human"))}
    return gold


def load_silver(root: Path) -> dict[str, bool]:
    path = root / "issue_llm_reviews.json"
    if not path.exists():
        return {}
    reviews = (load_json(path) or {}).get("reviews") or {}
    return {cid: bool(row.get("same_event")) for cid, row in reviews.items() if isinstance(row, dict)}


# ---- 지표 ---------------------------------------------------------------------------

def pair_metrics(gold: dict[str, dict], same_fn, items_by_hash: dict[str, Item]) -> dict:
    counts = Counter()
    rows = []
    for cid, label in gold.items():
        a, b = cid.split("--")
        if a not in items_by_hash or b not in items_by_hash:
            counts["outside_population"] += 1
            continue
        relation = label.get("relation")
        same = same_fn(a, b)
        if relation in DISTINCT:
            counts["distinct"] += 1
            counts["false_merge"] += int(same)
        elif relation in SAME:
            counts["same"] += 1
            counts["false_split"] += int(not same)
        elif relation in MATERIAL:
            counts["material"] += 1
            counts["material_collapsed"] += int(same)
        else:
            counts["insufficient"] += 1
        rows.append((cid, relation, same))

    def rate(n, d):
        return round(n / d, 4) if d else None

    return {
        "pairs_in_population": len(rows),
        "outside_population": counts["outside_population"],
        "distinct_pairs": counts["distinct"], "false_merge": counts["false_merge"],
        "false_merge_rate": rate(counts["false_merge"], counts["distinct"]),
        "same_pairs": counts["same"], "false_split": counts["false_split"],
        "false_split_rate": rate(counts["false_split"], counts["same"]),
        "material_pairs": counts["material"], "material_collapsed": counts["material_collapsed"],
        "material_preserved_rate": rate(counts["material"] - counts["material_collapsed"], counts["material"]),
        "_rows": rows,
    }


def recovery(prod: dict, res: dict) -> dict:
    prod_rows = {cid: same for cid, rel, same in prod["_rows"]}
    out = Counter()
    for cid, rel, same in res["_rows"]:
        p = prod_rows.get(cid)
        if rel in SAME and p is False:
            out["prod_false_split"] += 1
            out["recovered"] += int(same)
        if rel in DISTINCT and p is False and same:
            out["new_false_merge"] += 1
        if rel in DISTINCT and p is True and not same:
            out["false_merge_fixed"] += 1
        if rel in MATERIAL and p is True and not same:
            out["material_recovered"] += 1
        if rel in MATERIAL and p is False and same:
            out["material_newly_collapsed"] += 1
    return {
        "production_false_splits": out["prod_false_split"],
        "recovered_by_resolver": out["recovered"],
        "recovery_rate": round(out["recovered"] / out["prod_false_split"], 4) if out["prod_false_split"] else None,
        "new_false_merges_introduced": out["new_false_merge"],
        "production_false_merges_fixed": out["false_merge_fixed"],
        "material_recovered_from_production_collapse": out["material_recovered"],
        "material_newly_collapsed": out["material_newly_collapsed"],
    }


def silver_metrics(silver: dict[str, bool], same_fn, items_by_hash: dict[str, Item]) -> dict:
    c = Counter()
    for cid, same_event in silver.items():
        a, b = cid.split("--")
        if a not in items_by_hash or b not in items_by_hash:
            continue
        same = same_fn(a, b)
        c["n"] += 1
        c["agree"] += int(same == same_event)
        if same_event and not same:
            c["silver_same_but_split"] += 1
        if not same_event and same:
            c["silver_diff_but_merged"] += 1
    return {"pairs": c["n"], "agreement": round(c["agree"] / c["n"], 4) if c["n"] else None,
            "silver_same_but_split": c["silver_same_but_split"],
            "silver_diff_but_merged": c["silver_diff_but_merged"]}


def material_pairs_within(groups: dict[str, list[Item]]) -> list[tuple[str, str, dict]]:
    """묶음 안에서 날짜순 인접 쌍 가운데 production 의 progression() 이 material 이라 한 쌍."""
    out = []
    for gid, members in groups.items():
        ordered = sorted(members, key=lambda i: (i.effective_date, i.hash))
        for prior, cand in zip(ordered, ordered[1:]):
            verdict = issue_continuity.progression(prior.as_row(), cand.as_row())
            if verdict.get("verdict") == "material":
                out.append((prior.hash, cand.hash, verdict))
    return out


def group_by(assignment: dict[str, str], items_by_hash: dict[str, Item]) -> dict[str, list[Item]]:
    groups: dict[str, list[Item]] = defaultdict(list)
    for h, gid in assignment.items():
        if h in items_by_hash:
            groups[gid].append(items_by_hash[h])
    return groups


def structural(assignment: dict[str, str], items_by_hash: dict[str, Item], *, label: str) -> dict:
    groups = group_by(assignment, items_by_hash)
    sizes = Counter(len(v) for v in groups.values())
    unit_conflicts = 0
    long_gap_groups = 0
    for members in groups.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if objmod.unit_conflict(a.signals, b.signals):
                    unit_conflicts += 1
        days = [d for d in (_day(m.effective_date) for m in members) if d]
        if days and (max(days) - min(days)).days > 21:
            long_gap_groups += 1
    material = material_pairs_within(groups)
    return {
        "label": label, "groups": len(groups), "singletons": sizes[1],
        "singleton_rate": round(sizes[1] / len(groups), 4) if groups else None,
        "largest": max(sizes) if sizes else 0,
        "unit_conflict_pairs_inside_groups": unit_conflicts,
        "groups_spanning_over_21_days": long_gap_groups,
        "material_pairs_collapsed_inside_groups": len(material),
        "_material": material,
    }


def material_preservation_against_production(prod_material, res_assignment: dict[str, str],
                                             grade_of: dict[str, str] | None = None) -> dict:
    """production 이 한 묶음 안에 접어 둔 material 인접 쌍을 resolver 는 갈라 두는가.

    lexical grade 는 정의상 production 을 따르므로 접힌 채 남는다. 그래서 두 기사가 다
    object grade 인 부분집합을 따로 낸다 — 가설이 실제로 손을 댄 자리는 거기뿐이다.
    """
    c = Counter()
    for prior, cand, _ in prod_material:
        c["n"] += 1
        kept = res_assignment.get(prior) != res_assignment.get(cand)
        c["preserved"] += int(kept)
        if grade_of and grade_of.get(prior) == "object" and grade_of.get(cand) == "object":
            c["object_n"] += 1
            c["object_preserved"] += int(kept)
    return {"production_material_pairs": c["n"], "kept_as_distinct_events": c["preserved"],
            "rate": round(c["preserved"] / c["n"], 4) if c["n"] else None,
            "object_grade_pairs": c["object_n"], "object_grade_kept": c["object_preserved"],
            "object_grade_rate": round(c["object_preserved"] / c["object_n"], 4) if c["object_n"] else None}


def structure_by_kind(res: Resolution, items_by_hash: dict[str, Item], *, use_lexicon: bool) -> dict:
    """Object kind 별 Event 통계. 호기 Event 가 몇 개고 어떻게 생겼는지가 가설의 유일한 직접 증거다."""
    out: dict[str, dict] = {}
    for event in res.events:
        if event.object_key is None:
            continue
        kind = event.object_key[0]
        if kind == "project":
            kind = "project:" + "+".join(sorted(event.object_key[1]))
        row = out.setdefault(kind, Counter())
        row["events"] += 1
        row["articles"] += len(event.members)
        row["singletons"] += int(len(event.members) == 1)
        row["production_issues_joined"] += max(0, len(event.production_issues) - 1)
        days = [d for d in (_day(m.effective_date) for m in event.members) if d]
        span = (max(days) - min(days)).days if days else 0
        row["span_over_21"] += int(span > 21)
        row["max_span"] = max(row["max_span"], span)
        row["largest"] = max(row["largest"], len(event.members))
        # 같은 Object 위에서 단계가 갈려 새 Event 가 선 횟수 (material 보존의 직접 증거)
        row["stage_splits"] += sum(1 for d in event.decisions if d["reason"] == "new:stage_split")
    return {k: dict(v) for k, v in sorted(out.items())}


# ---- 검토표 -------------------------------------------------------------------------

def _obj_label(item: Item, use_lexicon: bool) -> str:
    key = item.signals.key(use_lexicon=use_lexicon)
    return f"{key[0]}:{'+'.join(sorted(key[1]))}" if key else "-"


def review_row(cid: str, a: Item, b: Item, *, prod_same: bool, res_same: bool, res: Resolution,
               gold: dict | None, category: str, use_lexicon: bool, loose_same: bool | None = None) -> dict:
    return {
        "id": cid, "category": category,
        "a_date": a.effective_date, "a_title": a.title_kr or a.title,
        "b_date": b.effective_date, "b_title": b.title_kr or b.title,
        "gap_days": _gap(a.effective_date, b.effective_date),
        "a_object": _obj_label(a, use_lexicon), "b_object": _obj_label(b, use_lexicon),
        "a_stages": "+".join(sorted(a.stages())) or "-", "b_stages": "+".join(sorted(b.stages())) or "-",
        "production": "same" if prod_same else "split",
        "resolver": "same" if res_same else "split",
        "resolver_loose": "" if loose_same is None else ("same" if loose_same else "split"),
        "grade": f"{res.grade_of.get(a.hash, '?')}/{res.grade_of.get(b.hash, '?')}",
        "reason": f"{res.reason_of.get(a.hash, '?')} | {res.reason_of.get(b.hash, '?')}",
        "gold": (gold or {}).get("relation", ""), "gold_source": (gold or {}).get("source", ""),
        "gold_axis": (gold or {}).get("axis", "") or "",
        "human_verdict": "", "human_note": "",
    }


def build_review_table(items_by_hash, gold, prod_part, res: Resolution, *, use_lexicon: bool,
                       target: int = 160, loose: Resolution | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    def prod_same(a, b):
        return prod_part.get(a) == prod_part.get(b) and bool(prod_part.get(a))

    def res_same(a, b):
        return res.assignment.get(a) == res.assignment.get(b)

    def loose_same(a, b):
        if loose is None:
            return None
        return loose.assignment.get(a) == loose.assignment.get(b)

    def add(cid, category, gold_row=None):
        if cid in seen:
            return
        a, b = cid.split("--")
        if a not in items_by_hash or b not in items_by_hash:
            return
        seen.add(cid)
        rows.append(review_row(cid, items_by_hash[a], items_by_hash[b], prod_same=prod_same(a, b),
                               res_same=res_same(a, b), res=res, gold=gold_row, category=category,
                               use_lexicon=use_lexicon, loose_same=loose_same(a, b)))

    # A. 사람이 이미 판정한 쌍 전부.
    for cid, g in gold.items():
        if g.get("source") == "human":
            add(cid, "A.human_labeled", g)
    # B. gold 위에서 두 시스템이 갈린 쌍 전부 (결정적 증거).
    for cid, g in gold.items():
        a, b = cid.split("--")
        if a in items_by_hash and b in items_by_hash and prod_same(a, b) != res_same(a, b):
            add(cid, "B.gold_systems_disagree", g)
    # C. gold 와 resolver 가 어긋난 쌍 (위험).
    for cid, g in gold.items():
        a, b = cid.split("--")
        if a not in items_by_hash or b not in items_by_hash:
            continue
        rel = g.get("relation")
        s = res_same(a, b)
        if (rel in DISTINCT and s) or (rel in SAME and not s) or (rel in MATERIAL and s):
            add(cid, "C.resolver_vs_gold", g)

    # D~H. 전수에서 두 시스템이 갈린 쌍을 종류별로 표본.
    groups = group_by(res.assignment, items_by_hash)
    joined_prod_split: list[tuple[int, str]] = []       # resolver 가 붙였는데 production 은 갈라 둠
    for gid, members in groups.items():
        by_prod = defaultdict(list)
        for m in members:
            by_prod[m.production_issue].append(m)
        if len(by_prod) < 2:
            continue
        buckets = list(by_prod.values())
        for i, left in enumerate(buckets):
            for right in buckets[i + 1:]:
                a = max(left, key=lambda m: m.effective_date)
                b = min(right, key=lambda m: m.effective_date)
                joined_prod_split.append((_gap(a.effective_date, b.effective_date), pair_id(a.hash, b.hash)))
    joined_prod_split.sort(reverse=True)
    split_prod_joined: list[tuple[str, str]] = []       # production 은 한 묶음인데 resolver 가 갈라 둠
    prod_groups = group_by(prod_part, items_by_hash)
    for pid, members in prod_groups.items():
        by_res = defaultdict(list)
        for m in members:
            by_res[res.assignment.get(m.hash)].append(m)
        if len(by_res) < 2:
            continue
        buckets = sorted(by_res.values(), key=lambda ms: min(m.effective_date for m in ms))
        for left, right in zip(buckets, buckets[1:]):
            a = max(left, key=lambda m: m.effective_date)
            b = min(right, key=lambda m: m.effective_date)
            reason = res.reason_of.get(b.hash, "")
            split_prod_joined.append((reason, pair_id(a.hash, b.hash)))

    def take(pool, category, n, pick=lambda row: row[1], filt=lambda row: True):
        k = 0
        for row in pool:
            if k >= n:
                break
            if not filt(row):
                continue
            before = len(seen)
            add(pick(row), category)
            k += len(seen) - before

    take(joined_prod_split, "D.resolver_joined_production_split_long_gap", 30, filt=lambda r: r[0] > 21)
    take(joined_prod_split, "E.resolver_joined_production_split_short_gap", 15, filt=lambda r: 0 <= r[0] <= 21)
    take(split_prod_joined, "F.stage_split_inside_production_issue", 25, filt=lambda r: r[0] == "new:stage_split")
    take(split_prod_joined, "G.other_split_inside_production_issue", 20, filt=lambda r: r[0] != "new:stage_split")
    if use_lexicon:
        lex = [(0, pid) for gap, pid in joined_prod_split
               if any(items_by_hash[h].signals.key(use_lexicon=True) and
                      items_by_hash[h].signals.key(use_lexicon=True)[0] == "lexicon" for h in pid.split("--"))]
        take(lex, "H.lexicon_object_join", 15)
    # I. 대표 사례 — 두 시스템이 같은 답을 낸 object-grade 결합 (정상 동작 확인용).
    agree = []
    for gid, members in groups.items():
        if len(members) < 2:
            continue
        if not all(res.grade_of.get(m.hash) == "object" for m in members):
            continue
        if len({m.production_issue for m in members}) != 1:
            continue
        ordered = sorted(members, key=lambda m: m.effective_date)
        agree.append((0, pair_id(ordered[0].hash, ordered[-1].hash)))
    take(agree, "I.representative_agreement", max(0, target - len(rows)))
    return rows


def write_review_table(rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with (out_dir / "review_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Object 기반 Event Identity — 검토표", "",
             f"쌍 {len(rows)}건. 같은 표가 `review_table.csv` 에 있다(사람 판정 칸 `human_verdict`·`human_note` 포함).", "",
             "- `production` 원장 소유권 기준의 production 묶음 (같은 묶음이면 same)",
             "- `resolver` 이 실험의 명세 arm(`v0-spec`: transition 값 동일 · 키 셋을 다 알면 키 아니면 새 Event)",
             "- `loose` 느슨한 arm(`v0-loose`: transition 교집합 · production 다리 항상 허용) — 두 극단 사이에서 사람이 판단한다",
             "- `gold` 사람(human) 또는 Claude blind 판정. 없으면 빈 칸",
             "- 범주: A 사람 판정 · B gold 위에서 두 시스템이 갈림 · C resolver 가 gold 와 어긋남(위험) · D/E 전수에서 resolver 만 붙임(장/단기 간격) · F 같은 production 묶음을 단계로 가름 · G 그 밖의 가름 · I 두 시스템이 같은 답을 낸 object 결합(대표)", ""]
    by_cat = defaultdict(list)
    for row in rows:
        by_cat[row["category"]].append(row)
    for category in sorted(by_cat):
        lines.append(f"## {category} ({len(by_cat[category])}건)")
        lines.append("")
        lines.append("| # | A (날짜 · 제목) | B (날짜 · 제목) | 간격 | Object A / B | 단계 A / B | production | resolver | loose | grade | 이유 | gold |")
        lines.append("|---|---|---|---:|---|---|---|---|---|---|---|---|")
        for i, row in enumerate(by_cat[category], 1):
            gold_txt = f"{row['gold']} ({row['gold_source']})" if row["gold"] else ""
            lines.append(
                f"| {i} | {row['a_date']} · {row['a_title'][:60]} | {row['b_date']} · {row['b_title'][:60]} | {row['gap_days']} "
                f"| {row['a_object']} / {row['b_object']} | {row['a_stages']} / {row['b_stages']} | {row['production']} | {row['resolver']} "
                f"| {row['resolver_loose']} | {row['grade']} | {row['reason']} | {gold_txt} |")
        lines.append("")
    (out_dir / "review_table.md").write_text("\n".join(lines), encoding="utf-8")


# ---- 실행 ---------------------------------------------------------------------------

def run(args) -> dict:
    root = ROOT
    archive = load_archive(root)
    ledger = load_json(root / "issue_ledger.json")
    live_rows = load_json(Path(args.live_issues)) if args.live_issues and Path(args.live_issues).exists() else []
    partition, partition_stats = production_partition(ledger, live_rows)

    population = set(partition)
    delivery = root / "delivery_log.jsonl"
    if delivery.exists():
        for line in delivery.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("hash") and not row.get("record_type"):
                population.add(str(row["hash"]))
    items = build_items(population, archive, partition)
    items_by_hash = {i.hash: i for i in items}
    # production 묶음이 없는 발송 카드는 자기 해시로 단독 묶음이 된다(production 도 그렇다).
    for item in items:
        if not item.production_issue:
            item.production_issue = f"issue-{item.hash}"
            partition[item.hash] = item.production_issue

    approved, rejected = human_pairs(load_json(root / "issue_match_overrides.json"))
    gold = load_gold(root, Path(args.human_labels) if args.human_labels else None)
    silver = load_silver(root)

    def prod_same(a, b):
        return partition.get(a) == partition.get(b)

    prod_pairs = pair_metrics(gold, prod_same, items_by_hash)
    prod_struct = structural(partition, items_by_hash, label="production")
    prod_silver = silver_metrics(silver, prod_same, items_by_hash)

    signal_coverage = Counter()
    for item in items:
        k0 = item.signals.key(use_lexicon=False)
        k1 = item.signals.key(use_lexicon=True)
        signal_coverage["items"] += 1
        signal_coverage["v0_" + (k0[0] if k0 else "none")] += 1
        signal_coverage["v0x_" + (k1[0] if k1 else "none")] += 1
        signal_coverage["stage_known"] += int(bool(item.stages()))
        signal_coverage["event_date_gated"] += int(bool(item.event_date))
        signal_coverage["full_key_v0"] += int(bool(k0 and k0[0] != "plant" and item.stages()))
        signal_coverage["full_key_v0x"] += int(bool(k1 and k1[0] != "plant" and item.stages()))

    report = {
        "generated_for": "docs/2026-09-20-object-event-identity-experiment.md",
        "population": {"hashes": len(population), "items_in_archive": len(items),
                       "date_range": [min(i.pub_date for i in items), max(i.pub_date for i in items)],
                       **partition_stats},
        "signal_coverage": dict(signal_coverage),
        "gold": {"pairs": len(gold), "by_relation": dict(Counter(g.get("relation") for g in gold.values())),
                 "by_source": dict(Counter(g.get("source") for g in gold.values())),
                 "by_stratum": dict(Counter(g.get("stratum") for g in gold.values()))},
        "silver_pairs": len(silver),
        "production": {"pairs": {k: v for k, v in prod_pairs.items() if k != "_rows"},
                       "structure": {k: v for k, v in prod_struct.items() if k != "_material"},
                       "silver": prod_silver},
        "arms": {},
    }

    # arm 이름: v0(결정적 Object) · v0e(게이트 엔티티 포함) · v0x(어휘표) / b=버킷 / strict=단계 집합
    # 동일 요구 / title=제목 단계만 / nobridge=production 다리 없음 / process=topics 절차 힌트
    base = dict(use_lexicon=False, gate_entities=False, bucket_days=30, process_hint=False,
                strict_transition=False, title_only_stages=False, production_bridge=True)
    # "spec" = 리뷰 §10 명세 그대로: transition 은 값 하나(집합 동일) · 키 셋을 다 알면 키 아니면 새
    # Event · 하나라도 모르면 현재 경로(production 묶음)로 후퇴.
    spec = {"strict_transition": True, "production_bridge": "partial"}
    arms = {
        "v0-spec": {**spec},
        "v0-spec-b14": {**spec, "bucket_days": 14},
        "v0-spec-b60": {**spec, "bucket_days": 60},
        "v0-loose": {},
        "v0-loose-nobridge": {"production_bridge": False},
        "v0-strict-bridge": {"strict_transition": True},
        "v0-strict-nobridge": {"strict_transition": True, "production_bridge": False},
        "v0-loose-title": {"title_only_stages": True},
        "v0e-spec": {**spec, "gate_entities": True},
        "v0e-loose": {"gate_entities": True},
        "v0x-spec": {**spec, "use_lexicon": True},
        "v0x-loose": {"use_lexicon": True},
        "v0x-loose-nobridge": {"use_lexicon": True, "production_bridge": False},
        "v0x-spec-process": {**spec, "use_lexicon": True, "process_hint": True},
    }
    items_variants = {False: items, True: build_items(population, archive, partition, gate_entities=True)}
    for variant in items_variants.values():
        for item in variant:
            if not item.production_issue:
                item.production_issue = partition.get(item.hash) or f"issue-{item.hash}"
    main_res: Resolution | None = None
    loose_res: Resolution | None = None
    for label, overrides in arms.items():
        cfg = {**base, **overrides}
        arm_items = items_variants[cfg["gate_entities"]]
        arm_by_hash = {i.hash: i for i in arm_items}
        kwargs = dict(bucket_days=cfg["bucket_days"], use_lexicon=cfg["use_lexicon"], rejected_pairs=rejected,
                      approved_pairs=approved, process_hint=cfg["process_hint"],
                      strict_transition=cfg["strict_transition"], title_only_stages=cfg["title_only_stages"],
                      production_bridge=cfg["production_bridge"])
        res = Resolver(**kwargs).resolve(arm_items)
        # 멱등성: 같은 입력을 다시 돌려 같은 배정이 나오는가.
        again = Resolver(**kwargs).resolve(arm_items)
        idempotent = sum(1 for h in res.assignment if res.assignment[h] == again.assignment[h]) / len(res.assignment)

        def res_same(a, b, _res=res):
            return _res.assignment.get(a) == _res.assignment.get(b)

        pairs = pair_metrics(gold, res_same, arm_by_hash)
        struct = structural(res.assignment, arm_by_hash, label=label)
        object_only = {h: gid for h, gid in res.assignment.items() if res.grade_of.get(h) == "object"}
        report["arms"][label] = {
            "config": cfg,
            "stats": res.stats, "idempotence": round(idempotent, 4),
            "pairs": {k: v for k, v in pairs.items() if k != "_rows"},
            "vs_production": recovery(prod_pairs, pairs),
            "structure": {k: v for k, v in struct.items() if k != "_material"},
            "material_vs_production": material_preservation_against_production(prod_struct["_material"], res.assignment, res.grade_of),
            "object_grade_structure": {k: v for k, v in structural(object_only, arm_by_hash, label=label + ":object").items() if k != "_material"},
            "structure_by_kind": structure_by_kind(res, arm_by_hash, use_lexicon=cfg["use_lexicon"]),
            "silver": silver_metrics(silver, res_same, arm_by_hash),
            "silver_object_grade": silver_metrics(
                {cid: v for cid, v in silver.items()
                 if all(res.grade_of.get(h) == "object" for h in cid.split("--"))}, res_same, arm_by_hash),
        }
        # grade 별 · Object kind 별 gold 성적 — 가설은 object grade 에서만 다르게 행동하므로
        # 거기서 따로 본다. production 의 같은 쌍 성적을 옆에 둔다.
        by_grade = defaultdict(Counter)
        for cid, rel, same in pairs["_rows"]:
            a, b = cid.split("--")
            ga, gb = res.grade_of.get(a), res.grade_of.get(b)
            if ga == "object" and gb == "object":
                ka = arm_by_hash[a].signals.key(use_lexicon=cfg["use_lexicon"])
                kb = arm_by_hash[b].signals.key(use_lexicon=cfg["use_lexicon"])
                kind = ka[0] if ka and kb and ka[0] == kb[0] else "object_mixed_kind"
                if kind == "project" and ka and kb and ka[1] == kb[1]:
                    kind = "project:" + "+".join(sorted(ka[1]))
                buckets = ("object", "object:" + kind)
            else:
                buckets = ("mixed_or_lexical",)
            p_same = prod_same(a, b)
            for g in buckets:
                by_grade[g]["pairs"] += 1
                if rel in DISTINCT:
                    by_grade[g]["distinct"] += 1
                    by_grade[g]["false_merge"] += int(same)
                    by_grade[g]["prod_false_merge"] += int(p_same)
                elif rel in SAME:
                    by_grade[g]["same"] += 1
                    by_grade[g]["false_split"] += int(not same)
                    by_grade[g]["prod_false_split"] += int(not p_same)
                elif rel in MATERIAL:
                    by_grade[g]["material"] += 1
                    by_grade[g]["material_collapsed"] += int(same)
                    by_grade[g]["prod_material_collapsed"] += int(p_same)
        report["arms"][label]["pairs_by_grade"] = {k: dict(v) for k, v in sorted(by_grade.items())}
        if label == args.main_arm:
            main_res = res
            main_items_by_hash = arm_by_hash
        if label == args.loose_arm:
            loose_res = res

    if main_res is None:
        main_res = res
        main_items_by_hash = arm_by_hash
    items_by_hash = main_items_by_hash
    use_lexicon = report["arms"][args.main_arm]["config"]["use_lexicon"]
    rows = build_review_table(items_by_hash, gold, partition, main_res, use_lexicon=use_lexicon, loose=loose_res)
    write_review_table(rows, OUT_DIR)
    report["review_table"] = {"rows": len(rows), "by_category": dict(Counter(r["category"] for r in rows)),
                              "main_arm": args.main_arm}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    with (DUMP_DIR / "assignments.jsonl").open("w", encoding="utf-8") as handle:
        for item in sorted(items, key=lambda i: (i.effective_date, i.hash)):
            handle.write(json.dumps({
                "hash": item.hash, "date": item.effective_date, "date_kind": item.date_kind,
                "title": item.title_kr or item.title, "object": _obj_label(item, use_lexicon),
                "stages": sorted(item.stages()), "production_issue": item.production_issue,
                "event": main_res.assignment.get(item.hash), "grade": main_res.grade_of.get(item.hash),
                "reason": main_res.reason_of.get(item.hash),
            }, ensure_ascii=False) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live-issues", default="", help="라이브 issues.json 사본 (production 분할 보정용)")
    parser.add_argument("--human-labels", default="", help=".eval/identity-review.json (로컬 사이드카)")
    parser.add_argument("--main-arm", default="v0-spec", help="검토표·덤프를 만들 arm")
    parser.add_argument("--loose-arm", default="v0-loose", help="검토표에 나란히 실을 느슨한 arm")
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps({k: report[k] for k in ("population", "signal_coverage", "gold")}, ensure_ascii=False, indent=1))
    for label, arm in report["arms"].items():
        p = arm["pairs"]
        print(f"{label:24s} events={arm['stats']['events']:5d} idem={arm['idempotence']} "
              f"FM={p['false_merge']}/{p['distinct_pairs']} FS={p['false_split']}/{p['same_pairs']} "
              f"MATkeep={p['material_preserved_rate']} rec={arm['vs_production']['recovery_rate']} "
              f"newFM={arm['vs_production']['new_false_merges_introduced']} "
              f"matVsProd={arm['material_vs_production']['rate']} unitConf={arm['structure']['unit_conflict_pairs_inside_groups']}")
    pp = report["production"]["pairs"]
    print(f"{'production':24s} groups={report['production']['structure']['groups']:5d} "
          f"FM={pp['false_merge']}/{pp['distinct_pairs']} FS={pp['false_split']}/{pp['same_pairs']} MATkeep={pp['material_preserved_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
