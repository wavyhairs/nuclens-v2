"""counterfactual 재생 — 보조 근거를 기존 판정 위에 얹으면 무엇이 새로 붙고, 그것이 맞는가.

두 자리를 각각 production 코드로 판정한 뒤, 판정이 None/미매칭인 쌍에만 보조 근거를 얹는다.
기존 매칭은 건드리지 않는다("추가 positive evidence").

    continuity   발송 카드(delivery_log) × 그 앞 14일 카드. `issue_continuity.same_issue` 를 그대로 호출.
    web          Gemini 회색지대 쌍 + gold 쌍. `web.build_data.issue_similarity` 를 임베딩 없이 호출 —
                 이 쌍들은 정의상 어휘 경로가 못 붙인 쌍이고 production 의 답은 Gemini 판정이었다.

정답: silver = Gemini `same_event` (production 자신의 신호), gold = 사람 22 + Claude blind 352.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import admin_overrides  # noqa: E402
import issue_continuity  # noqa: E402
import build_data  # noqa: E402

from experiments.evidence_replay import signals  # noqa: E402

OUT_DIR = ROOT / "eval_artifacts" / "evidence_replay"
SAME = {"SAME_EVENT", "FOLLOW_UP_NO_NEW_ACTION"}
DISTINCT = {"RELATED_DISTINCT_EVENT", "UNRELATED"}
MATERIAL = {"FOLLOW_UP_NEW_ACTION"}

def _strong_q(ev):
    """작은 개수(`8기`·`5개`)는 서로 다른 사건이 흔히 공유한다 — 금액·전력·전력량·%·10 이상 개수만."""
    return [q for q in ev["shared_quantities"] if not (q[0].startswith("n:") and q[1] < 10)]


VARIANTS = {
    "Q":      lambda ev, ctx: bool(ev["shared_quantities"]),
    "Qs":     lambda ev, ctx: bool(_strong_q(ev)),
    "Qs+ctx": lambda ev, ctx: bool(_strong_q(ev)) and ctx,
    "A1":     lambda ev, ctx: len(ev["shared_actors"]) >= 1,
    "A2":     lambda ev, ctx: len(ev["shared_actors"]) >= 2,
    "Q+A1":   lambda ev, ctx: bool(ev["shared_quantities"]) and len(ev["shared_actors"]) >= 1,
    "Q|A2":   lambda ev, ctx: bool(ev["shared_quantities"]) or len(ev["shared_actors"]) >= 2,
    "Q+ctx":  lambda ev, ctx: bool(ev["shared_quantities"]) and ctx,
    "A1+ctx": lambda ev, ctx: len(ev["shared_actors"]) >= 1 and ctx,
}


def pair_id(a, b):
    return "--".join(sorted((str(a), str(b))))


def load_archive():
    out = {}
    for path in sorted((ROOT / "archive").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("hash"):
                out[row["hash"]] = row
    return out


def load_labels(human_path: Path | None):
    silver = {}
    reviews = json.loads((ROOT / "issue_llm_reviews.json").read_text(encoding="utf-8")).get("reviews") or {}
    for cid, row in reviews.items():
        if isinstance(row, dict):
            silver[cid] = bool(row.get("same_event"))
    gold = {}
    for line in (ROOT / "eval_artifacts" / "event_judgments_claude.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            gold[row["candidate_id"]] = row["relation"]
    if human_path and human_path.exists():
        gold.update((json.loads(human_path.read_text(encoding="utf-8")).get("labels") or {}))
    return silver, gold


def fam(row):
    return (row.get("features") or {}).get("event_type") or "none"


def _rate(n, d):
    return round(n / d, 3) if d else None


class Tally:
    """variant 별 · 기준(silver/gold) 별 집계."""

    def __init__(self):
        self.c = defaultdict(Counter)

    def add(self, key, silver_same, gold_rel):
        c = self.c[key]
        c["n"] += 1
        if silver_same is not None:
            c["silver_labeled"] += 1
            c["silver_same"] += int(silver_same)
        if gold_rel is not None:
            c["gold_labeled"] += 1
            c["gold_same"] += int(gold_rel in SAME)
            c["gold_distinct"] += int(gold_rel in DISTINCT)
            c["gold_material"] += int(gold_rel in MATERIAL)

    def report(self, key):
        c = self.c[key]
        return {
            "pairs": c["n"],
            "silver": {"labeled": c["silver_labeled"], "same": c["silver_same"],
                       "precision": _rate(c["silver_same"], c["silver_labeled"])},
            "gold": {"labeled": c["gold_labeled"], "same": c["gold_same"], "distinct": c["gold_distinct"],
                     "material": c["gold_material"],
                     "precision_same_vs_distinct": _rate(c["gold_same"], c["gold_same"] + c["gold_distinct"])},
        }


# ---- continuity -----------------------------------------------------------------------

def continuity_replay(cards, archive, silver, gold, cfg, examples):
    by_day = defaultdict(list)
    for card in cards:
        by_day[card["date"][:10]].append(card)
    days = sorted(by_day)
    lookback = int(cfg.get("lookback_days", 14))
    tally = Tally()
    label_pool = Counter()          # 라벨 있는 창 안 쌍 — recall 분모
    recovered = defaultdict(Counter)
    per_family = defaultdict(lambda: defaultdict(Counter))
    total_pairs = 0
    for day in days:
        d = date.fromisoformat(day)
        recent = [c for k in days if (d - timedelta(days=lookback)) <= date.fromisoformat(k) < d for c in by_day[k]]
        if not recent:
            continue
        generic = issue_continuity.generic_anchors(recent)
        for card in by_day[day]:
            family = fam(archive.get(card["hash"], {}))
            for prior in recent:
                if prior["hash"] == card["hash"]:
                    continue
                total_pairs += 1
                cid = pair_id(card["hash"], prior["hash"])
                s_same = silver.get(cid)
                g_rel = gold.get(cid)
                prod = issue_continuity.same_issue(card, prior, cfg, generic)
                if s_same is not None or g_rel is not None:
                    label_pool["labeled"] += 1
                    if s_same:
                        label_pool["silver_same"] += 1
                    if g_rel in SAME:
                        label_pool["gold_same"] += 1
                if prod:
                    tally.add("production", s_same, g_rel)
                    per_family[family]["production"]["n"] += 1
                    if s_same:
                        recovered["production"]["silver_same"] += 1
                    if g_rel in SAME:
                        recovered["production"]["gold_same"] += 1
                    continue
                # 거부권은 그대로 — production 이 None 을 낸 이유가 거부권이면 얹지 않는다
                fa, fb = issue_continuity._facilities(card), issue_continuity._facilities(prior)
                if (fa and fb and not (fa & fb)) or issue_continuity._fingerprint_country_conflict(card, prior) \
                        or admin_overrides.merge_blocked(card, prior):
                    continue
                ev = signals.evidence(card, prior)
                if not ev["shared_quantities"] and not ev["shared_actors"]:
                    continue
                ctx = issue_continuity.title_similarity(card, prior) >= 0.3 or bool(
                    (issue_continuity.named_anchors(card) & issue_continuity.named_anchors(prior)) - generic)
                for name, rule in VARIANTS.items():
                    if rule(ev, ctx):
                        tally.add(name, s_same, g_rel)
                        per_family[family][name]["n"] += 1
                        if s_same is not None:
                            per_family[family][name]["labeled"] += 1
                            per_family[family][name]["silver_same"] += int(s_same)
                        if s_same:
                            recovered[name]["silver_same"] += 1
                        if g_rel in SAME:
                            recovered[name]["gold_same"] += 1
                        if name in ("Q", "A1", "Q+A1") and len(examples[("continuity", name, s_same)]) < 8:
                            examples[("continuity", name, s_same)].append({
                                "a": f"{card['date'][:10]} {card.get('title_kr','')[:60]}",
                                "b": f"{prior['date'][:10]} {prior.get('title_kr','')[:60]}",
                                "shared_q": ev["shared_quantities"], "shared_actors": ev["shared_actors"],
                                "silver": s_same, "gold": g_rel})
    out = {"cards": len(cards), "pairs_in_window": total_pairs, "label_pool": dict(label_pool),
           "production": tally.report("production"), "variants": {}}
    for name in VARIANTS:
        rep = tally.report(name)
        rep["recall_gain"] = {
            "silver_same_recovered": recovered[name]["silver_same"],
            "silver_same_missed_by_production": label_pool["silver_same"] - recovered["production"]["silver_same"],
            "gold_same_recovered": recovered[name]["gold_same"],
            "gold_same_missed_by_production": label_pool["gold_same"] - recovered["production"]["gold_same"],
        }
        out["variants"][name] = rep
    out["production"]["recall"] = {
        "silver_same_matched": recovered["production"]["silver_same"], "silver_same_total": label_pool["silver_same"],
        "gold_same_matched": recovered["production"]["gold_same"], "gold_same_total": label_pool["gold_same"],
    }
    out["by_family"] = {f: {k: dict(v) for k, v in d.items()} for f, d in per_family.items()}
    return out


# ---- web ------------------------------------------------------------------------------

def web_replay(archive, silver, gold, examples):
    pairs = {}
    for cid, same in silver.items():
        pairs[cid] = {"silver": same, "gold": gold.get(cid)}
    for cid, rel in gold.items():
        pairs.setdefault(cid, {"silver": silver.get(cid), "gold": rel})
    tally = Tally()
    pool = Counter()
    per_family = defaultdict(lambda: defaultdict(Counter))
    lexical_matched = Counter()
    for cid, labels in pairs.items():
        a, b = cid.split("--")
        if a not in archive or b not in archive:
            continue
        A, B = archive[a], archive[b]
        family = fam(A) if fam(A) == fam(B) else "mixed"
        pool["pairs"] += 1
        if labels["silver"]:
            pool["silver_same"] += 1
        if labels["gold"] in SAME:
            pool["gold_same"] += 1
        matched, _score, diag = build_data.issue_similarity(A, B)
        if matched:
            lexical_matched["n"] += 1
            lexical_matched["silver_same"] += int(bool(labels["silver"]))
            continue
        if diag.get("blocked_by"):
            continue
        ev = signals.evidence(A, B)
        if not ev["shared_quantities"] and not ev["shared_actors"]:
            continue
        ctx = build_data.has_review_context(diag)
        if ev["shared_quantities"] and labels["silver"] is not None:
            for kind, value in ev["shared_quantities"]:
                key = kind if not kind.startswith("n:") else (kind + ("<10" if value < 10 else ">=10"))
                per_family["_by_kind"][key]["n"] += 1
                per_family["_by_kind"][key]["silver_same"] += int(labels["silver"])
        for name, rule in VARIANTS.items():
            if rule(ev, ctx):
                tally.add(name, labels["silver"], labels["gold"])
                per_family[family][name]["n"] += 1
                if labels["silver"] is not None:
                    per_family[family][name]["labeled"] += 1
                    per_family[family][name]["silver_same"] += int(labels["silver"])
                if name in ("Q", "A1", "Q+A1", "Q+ctx") and len(examples[("web", name, labels["silver"])]) < 8:
                    examples[("web", name, labels["silver"])].append({
                        "a": A.get("title_kr", "")[:60], "b": B.get("title_kr", "")[:60],
                        "shared_q": ev["shared_quantities"], "shared_actors": ev["shared_actors"],
                        "silver": labels["silver"], "gold": labels["gold"]})
    out = {"pairs": pool["pairs"], "silver_same_total": pool["silver_same"], "gold_same_total": pool["gold_same"],
           "lexical_matched_by_production": dict(lexical_matched), "variants": {}}
    for name in VARIANTS:
        rep = tally.report(name)
        c = tally.c[name]
        rep["share_of_silver_same_covered"] = _rate(c["silver_same"], pool["silver_same"])
        rep["silver_diff_merged"] = c["silver_labeled"] - c["silver_same"]
        out["variants"][name] = rep
    out["by_family"] = {f: {k: dict(v) for k, v in d.items()} for f, d in per_family.items()}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--human-labels", default="")
    args = parser.parse_args(argv)
    archive = load_archive()
    silver, gold = load_labels(Path(args.human_labels) if args.human_labels else None)
    cfg = issue_continuity.resolve_config(json.loads((ROOT / "ranking_config.json").read_text(encoding="utf-8")))
    cards = []
    for line in (ROOT / "delivery_log.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("hash") and not row.get("record_type") and row.get("date"):
            cards.append(row)
    examples = defaultdict(list)
    report = {
        "continuity": continuity_replay(cards, archive, silver, gold, cfg, examples),
        "web": web_replay(archive, silver, gold, examples),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT_DIR / "examples.jsonl").open("w", encoding="utf-8") as fh:
        for key, rows in examples.items():
            for row in rows:
                fh.write(json.dumps({"where": key[0], "variant": key[1], **row}, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in report["continuity"].items() if k != "by_family"}, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report["web"].items() if k != "by_family"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
