"""해외 AI 레인 0단계 — 판정 기준을 gold 로 고정하고 판정기를 잰다.

무엇을 하나
-----------
  candidates  아카이브 전체에 ``ai_lane.prefilter`` 를 돌려 후보를 뽑고 층(stratum)을 매긴다.
  sample      층별로 고정 시드 표본을 뽑아 라벨 시트를 만든다. 층별 표집률을 같이 남긴다 —
              그래야 표본 결과로 **3개월 전체의 진짜 건수**를 추정할 수 있다.
  baseline    LLM 없이 규칙만으로 판정한 기준선. "LLM 판정이 얼마나 필요한가"의 잣대다.
  judge       production 판정기(``ai_lane.judge``)를 표본에 돌린다. **Gemini 키가 필요하다.**
              ``--dry-run`` 이면 호출하지 않고 production 이 보낼 요청 문자열만 남긴다.
  evaluate    gold 대비 정밀도·재현율·제외 사유 혼동·분류 일치율, 층 가중 추정치.

평가기는 판정 프롬프트를 **가지지 않는다.** ``ai_lane.build_request`` 를 그대로 쓴다.
tools/llm_eval.py 머리 주석이 그 이유다 — 평가용 프롬프트를 따로 두면 production 과
다른 숫자가 나오고, 그 숫자를 며칠 쌓은 뒤에야 평가기가 틀렸다는 것을 알게 된다.

사용
----
    python tools/ai_lane_stage0.py candidates
    python tools/ai_lane_stage0.py sample
    python tools/ai_lane_stage0.py baseline
    python tools/ai_lane_stage0.py judge --dry-run        # 요청 미리보기
    GEMINI_API_KEY=... python tools/ai_lane_stage0.py judge
    python tools/ai_lane_stage0.py evaluate --pred eval_artifacts/ai_lane/pred_baseline.jsonl
    python tools/ai_lane_stage0.py evaluate --pred eval_artifacts/ai_lane/pred_judge.jsonl

산출물은 전부 ``eval_artifacts/ai_lane/`` 아래에 있다. production 상태 파일은 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_lane  # noqa: E402
from data_quality import title_key  # noqa: E402

ARCHIVE_DIR = ROOT / "archive"
OUT_DIR = ROOT / "eval_artifacts" / "ai_lane"
SEED = 20261014

# 층별 표집. None = 전수. 진짜 사례가 몰린 층(해외·데이터센터 힌트 없음)은 전수로 본다 —
# 희귀한 양성을 표본으로 깎으면 정밀도·재현율이 몇 건에 흔들린다.
SAMPLE_PLAN: dict[str, int | None] = {
    "overseas_core": None,
    "overseas_datacenter": 20,
    "domestic_core": 25,
    "domestic_hinted": 10,
    "unscoped": None,
}
STRATUM_ORDER = tuple(SAMPLE_PLAN)


def stratum_of(scope: str, hints: list[str]) -> str:
    if scope == "overseas":
        return "overseas_datacenter" if "datacenter_power" in hints else "overseas_core"
    if scope == "kr":
        return "domestic_hinted" if hints else "domestic_core"
    return "unscoped"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── candidates ───────────────────────────────────────────────────────────────
def extract_candidates(archive_dir: Path = ARCHIVE_DIR) -> tuple[list[dict], dict]:
    """아카이브 → 프리필터 통과 후보. hash·제목 완전일치로 한 번씩만 센다."""
    seen_hash: set[str] = set()
    seen_title: set[str] = set()
    rows: list[dict] = []
    total = 0
    days: set[str] = set()
    for path in sorted(archive_dir.glob("*.jsonl")):
        for article in _read_jsonl(path):
            total += 1
            stamp = str(article.get("archived_at") or article.get("pub") or "")[:10]
            if stamp:
                days.add(stamp)
            digest = str(article.get("hash") or "")
            if not digest or digest in seen_hash:
                continue
            seen_hash.add(digest)
            verdict = ai_lane.prefilter(article)
            if not verdict["passed"]:
                continue
            key = title_key(article.get("title"))
            if key in seen_title:
                continue
            seen_title.add(key)
            scope = str(article.get("scope") or "")
            rows.append({
                "id": digest,
                "stratum": stratum_of(scope, verdict["hints"]),
                "pub": str(article.get("pub") or "")[:10],
                "archived_at": str(article.get("archived_at") or "")[:10],
                "publisher": article.get("publisher") or article.get("domain") or "",
                "domain": article.get("domain") or "",
                "url": article.get("resolved_url") or article.get("url") or "",
                "scope": scope,
                "countries": article.get("countries") if isinstance(article.get("countries"), list) else [],
                "main_importance": article.get("importance") or "",
                "title": article.get("title") or "",
                "title_kr": article.get("title_kr") or "",
                "summary": _clip(article.get("summary"), 400),
                "detail": _clip(article.get("detail"), 600),
                "ai_terms": verdict["ai_terms"],
                "hints": verdict["hints"],
            })
    span = sorted(days)
    meta = {
        "archive_records": total,
        "archive_days": len(span),
        "archive_first_day": span[0] if span else "",
        "archive_last_day": span[-1] if span else "",
        "candidates": len(rows),
        "strata": dict(Counter(r["stratum"] for r in rows)),
    }
    return rows, meta


# ── sample ───────────────────────────────────────────────────────────────────
def _rank(seed: int, row_id: str) -> str:
    return hashlib.sha256(f"{seed}:{row_id}".encode("utf-8")).hexdigest()


def draw_sample(candidates: list[dict], plan: dict[str, int | None] = SAMPLE_PLAN,
                seed: int = SEED) -> tuple[list[dict], dict]:
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for row in candidates:
        by_stratum[row["stratum"]].append(row)
    picked: list[dict] = []
    weights: dict[str, dict] = {}
    for name in STRATUM_ORDER:
        # 해시 순위 표집: (seed, id) 해시가 작은 순으로 k 건. random.sample 과 달리
        # 풀에 항목이 **더해져도 기존 선택이 거의 유지**된다 — 프리필터 어휘를 고칠
        # 때마다 표본이 통째로 바뀌어 gold 를 다시 매기는 일을 막는다.
        pool = sorted(by_stratum.get(name, []), key=lambda r: _rank(seed, r["id"]))
        size = plan.get(name)
        chosen = pool if size is None or size >= len(pool) else pool[:size]
        chosen = sorted(chosen, key=lambda r: (r["pub"], r["id"]))
        picked.extend(chosen)
        weights[name] = {"population": len(pool), "sampled": len(chosen),
                         "weight": (len(pool) / len(chosen)) if chosen else 0.0}
    return picked, weights


# ── baseline ─────────────────────────────────────────────────────────────────
_HARD_EXCLUDE = ("datacenter_power", "fusion", "defense", "diplomatic", "ai_industry", "academic")
_HINT_TO_REASON = {
    "datacenter_power": "datacenter_power", "fusion": "fusion", "defense": "defense_weapons",
    "diplomatic": "diplomatic_mention", "ai_industry": "ai_industry_general",
    "academic": "academic_only", "robotics": "robotics_only",
}


def baseline_predict(row: dict) -> dict:
    """규칙 기준선. 국내 → 제외, 제외 힌트 → 그 사유, 로봇 힌트 + 약한 AI 신호 → 로봇 제외.

    LLM 없이 어디까지 가는지를 재는 잣대일 뿐 production 후보가 아니다.
    """
    if row.get("scope") == "kr":
        return {"in_scope": False, "exclude_reason": "domestic"}
    for hint in _HARD_EXCLUDE:
        if hint in row.get("hints", []):
            return {"in_scope": False, "exclude_reason": _HINT_TO_REASON[hint]}
    strong = [t for t in row.get("ai_terms", []) if t not in ("AI", "A.I.")]
    if "robotics" in row.get("hints", []) and not strong:
        return {"in_scope": False, "exclude_reason": "robotics_only"}
    return {"in_scope": True, "exclude_reason": "none"}


# ── evaluate ─────────────────────────────────────────────────────────────────
def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def evaluate(gold: list[dict], pred: list[dict], weights: dict | None = None,
             strata: dict[str, str] | None = None) -> dict:
    """gold·pred 를 id 로 맞춰 잰다. pred 에 없는 gold 는 '판정 없음'으로 따로 센다."""
    pred_by = {p["id"]: p for p in pred if p.get("id")}
    strata = strata or {}
    tp = fp = fn = tn = missing = 0
    per_stratum: dict[str, Counter] = defaultdict(Counter)
    reason_confusion: Counter = Counter()
    field_agree: dict[str, Counter] = defaultdict(Counter)
    disagreements = []
    for g in gold:
        p = pred_by.get(g["id"])
        stratum = strata.get(g["id"], g.get("stratum", ""))
        if p is None:
            missing += 1
            per_stratum[stratum]["missing"] += 1
            continue
        gi, pi = bool(g.get("in_scope")), bool(p.get("in_scope"))
        cell = "tp" if gi and pi else "fp" if pi else "fn" if gi else "tn"
        per_stratum[stratum][cell] += 1
        if cell == "tp":
            tp += 1
            for field in ("application_area", "maturity", "ai_tech", "actor_type"):
                field_agree[field]["agree" if g.get(field) == p.get(field) else "differ"] += 1
        elif cell == "fp":
            fp += 1
        elif cell == "fn":
            fn += 1
        else:
            tn += 1
            reason_confusion[(g.get("exclude_reason"), p.get("exclude_reason"))] += 1
        if cell in ("fp", "fn"):
            disagreements.append({"id": g["id"], "cell": cell, "stratum": stratum,
                                  "gold_reason": g.get("exclude_reason"),
                                  "pred_reason": p.get("exclude_reason"),
                                  "note": g.get("note", "")})
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (round(2 * precision * recall / (precision + recall), 4)
          if precision and recall else None)
    out = {
        "gold": len(gold), "judged": len(gold) - missing, "missing": missing,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": precision, "recall": recall, "f1": f1,
        "per_stratum": {k: dict(v) for k, v in sorted(per_stratum.items())},
        "negative_reason_agreement": _ratio(
            sum(n for (a, b), n in reason_confusion.items() if a == b),
            sum(reason_confusion.values())),
        "negative_reason_confusion": [
            {"gold": a, "pred": b, "count": n}
            for (a, b), n in reason_confusion.most_common() if a != b],
        "field_agreement_on_tp": {
            k: _ratio(v["agree"], v["agree"] + v["differ"]) for k, v in field_agree.items()},
        "disagreements": disagreements,
    }
    return out


def population_estimate(gold: list[dict], weights: dict, days: int) -> dict:
    """층 가중으로 3개월 전체의 진짜 건수를 추정한다 (gold 기준, 판정기와 무관)."""
    pos_by = Counter(g.get("stratum") for g in gold if g.get("in_scope"))
    estimate = 0.0
    rows = {}
    for name, w in weights.items():
        n = pos_by.get(name, 0)
        est = n * w["weight"]
        estimate += est
        rows[name] = {"gold_positive": n, "sampled": w["sampled"],
                      "population": w["population"], "estimated_positive": round(est, 1)}
    return {"by_stratum": rows, "estimated_positive_total": round(estimate, 1),
            "days": days, "estimated_per_day": round(estimate / days, 2) if days else None,
            "estimated_per_week": round(estimate / days * 7, 1) if days else None}


def gold_summary(gold: list[dict]) -> dict:
    pos = [g for g in gold if g.get("in_scope")]
    neg = [g for g in gold if not g.get("in_scope")]
    return {
        "positives": len(pos), "negatives": len(neg),
        "low_confidence": sum(1 for g in gold if g.get("confidence") == "low"),
        "exclude_reasons": dict(Counter(g.get("exclude_reason") for g in neg).most_common()),
        "application_area": dict(Counter(g.get("application_area") for g in pos).most_common()),
        "maturity": dict(Counter(g.get("maturity") for g in pos).most_common()),
        "ai_tech": dict(Counter(g.get("ai_tech") for g in pos).most_common()),
        "actor_type": dict(Counter(g.get("actor_type") for g in pos).most_common()),
        "countries": dict(Counter(c for g in pos for c in (g.get("countries") or [])).most_common()),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cmd_candidates(args) -> None:
    rows, meta = extract_candidates(Path(args.archive))
    _write_jsonl(OUT_DIR / "candidates.jsonl", rows)
    (OUT_DIR / "candidates_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def _cmd_sample(args) -> None:
    rows = _read_jsonl(OUT_DIR / "candidates.jsonl")
    picked, weights = draw_sample(rows, seed=args.seed)
    _write_jsonl(OUT_DIR / "sample.jsonl", picked)
    (OUT_DIR / "sample_weights.json").write_text(
        json.dumps({"seed": args.seed, "plan": SAMPLE_PLAN, "strata": weights},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"sample {len(picked)}건 → {OUT_DIR / 'sample.jsonl'}")
    for name, w in weights.items():
        print(f"  {name:22s} {w['sampled']:4d} / {w['population']:4d}")


def _cmd_baseline(args) -> None:
    rows = _read_jsonl(OUT_DIR / "sample.jsonl")
    preds = [{"id": r["id"], **baseline_predict(r)} for r in rows]
    _write_jsonl(OUT_DIR / "pred_baseline.jsonl", preds)
    print(f"baseline {len(preds)}건 → pred_baseline.jsonl")


def _cmd_judge(args) -> None:
    rows = _read_jsonl(OUT_DIR / "sample.jsonl")
    if args.limit:
        rows = rows[: args.limit]
    if args.dry_run:
        requests = []
        for start in range(0, len(rows), ai_lane.BATCH_SIZE):
            chunk = rows[start:start + ai_lane.BATCH_SIZE]
            system, user = ai_lane.build_request(chunk)
            requests.append({"ids": [r["id"] for r in chunk], "user": user})
        _write_jsonl(OUT_DIR / "judge_requests_preview.jsonl", requests)
        print(f"dry-run: 요청 {len(requests)}개 (기사 {len(rows)}건) → judge_requests_preview.jsonl")
        print(f"system prompt {len(ai_lane.SYSTEM_PROMPT)}자, prompt_version {ai_lane.PROMPT_VERSION}")
        return
    import gemini_client  # noqa: PLC0415
    if not gemini_client.is_available():
        sys.exit("GEMINI_API_KEY 가 없다. --dry-run 으로 요청만 확인하거나 키가 있는 곳에서 돌릴 것.")
    verdicts = ai_lane.judge(rows)
    preds = []
    for row, verdict in zip(rows, verdicts):
        if verdict is None:
            continue
        preds.append({"id": row["id"], **verdict})
    meta = {"judged_at": datetime.now().isoformat(timespec="seconds"),
            "prompt_version": ai_lane.PROMPT_VERSION, "model": gemini_client.MODEL,
            "requested": len(rows), "returned": len(preds)}
    _write_jsonl(OUT_DIR / "pred_judge.jsonl", preds)
    (OUT_DIR / "pred_judge_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


def _cmd_evaluate(args) -> None:
    gold = _read_jsonl(OUT_DIR / "gold_labels.jsonl")
    sample = {r["id"]: r for r in _read_jsonl(OUT_DIR / "sample.jsonl")}
    for g in gold:
        g.setdefault("stratum", sample.get(g["id"], {}).get("stratum", ""))
    weights = json.loads((OUT_DIR / "sample_weights.json").read_text(encoding="utf-8"))["strata"]
    meta = json.loads((OUT_DIR / "candidates_meta.json").read_text(encoding="utf-8"))
    report = {"gold_summary": gold_summary(gold),
              "population_estimate": population_estimate(gold, weights, meta["archive_days"])}
    if args.pred:
        pred = _read_jsonl(Path(args.pred))
        report["predictor"] = Path(args.pred).stem
        report["metrics"] = evaluate(gold, pred, weights)
    out = OUT_DIR / f"metrics_{report.get('predictor', 'gold')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    brief = {k: report.get("metrics", {}).get(k) for k in ("confusion", "precision", "recall", "f1")}
    print(json.dumps({"gold": report["gold_summary"]["positives"],
                      "estimate": report["population_estimate"]["estimated_per_day"],
                      **brief}, ensure_ascii=False))
    print(f"→ {out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("candidates")
    p.add_argument("--archive", default=str(ARCHIVE_DIR))
    p.set_defaults(func=_cmd_candidates)
    p = sub.add_parser("sample")
    p.add_argument("--seed", type=int, default=SEED)
    p.set_defaults(func=_cmd_sample)
    p = sub.add_parser("baseline")
    p.set_defaults(func=_cmd_baseline)
    p = sub.add_parser("judge")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=_cmd_judge)
    p = sub.add_parser("evaluate")
    p.add_argument("--pred", default="")
    p.set_defaults(func=_cmd_evaluate)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
