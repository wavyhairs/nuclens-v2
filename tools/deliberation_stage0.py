"""신규원전 공론화 레인 0단계 — 후보 추출 · 프리필터 평가 · 판정기 평가.

    python tools/deliberation_stage0.py candidates [--since 2026-09-01]
    python tools/deliberation_stage0.py prefilter          # gold 대비 프리필터·힌트
    python tools/deliberation_stage0.py requests           # 판정 요청 파일(키 없이 검토용)
    python tools/deliberation_stage0.py judge              # GEMINI_API_KEY 가 있는 곳에서만

gold 는 ``eval_artifacts/deliberation/gold.jsonl`` (메인 아카이브 9/1~10/15 프리필터 후보 171건 전량 손라벨).
판정기는 production 과 **같은 요청 생성기**(deliberation_lane.build_request)를 쓴다 — 평가용 프롬프트를
따로 두면 production 과 다른 숫자가 나온다(tools/llm_eval.py 머리 주석의 교훈).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import deliberation_lane  # noqa: E402

OUT = BASE / "eval_artifacts" / "deliberation"
GOLD = OUT / "gold.jsonl"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main_rows(since: str) -> list[dict]:
    rows = []
    for path in sorted((BASE / "archive").glob("*.jsonl")):
        if path.stem < since[:7]:
            continue
        rows.extend(r for r in _jsonl(path) if str(r.get("archived_at") or "") >= since)
    return rows


def gold_articles() -> tuple[list[dict], dict[str, dict]]:
    gold = {g["hash"]: g for g in _jsonl(GOLD)}
    first = min(g["archived_at"] for g in gold.values())[:10]
    articles = [r for r in main_rows(first) if r.get("hash") in gold]
    return articles, gold


def cmd_candidates(args) -> None:
    rows = main_rows(args.since)
    passed = [r for r in rows if deliberation_lane.prefilter(r)["passed"]]
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for r in passed:
            handle.write(json.dumps({"hash": r["hash"], "archived_at": r.get("archived_at"),
                                     "publisher": r.get("publisher"), "title": r.get("title_kr") or r.get("title")},
                                    ensure_ascii=False) + "\n")
    print(f"메인 아카이브 {len(rows)}건 → 프리필터 통과 {len(passed)}건 → {OUT / 'candidates.jsonl'}")


def cmd_prefilter(args) -> None:
    articles, gold = gold_articles()
    seen = {a["hash"] for a in articles}
    missing = [h for h in gold if h not in seen]
    stats = Counter()
    hint_by_label = Counter()
    for article in articles:
        verdict = deliberation_lane.prefilter(article)
        label = "in" if gold[article["hash"]]["in_scope"] else gold[article["hash"]].get("exclude_reason")
        stats[(label == "in", verdict["passed"])] += 1
        for hint in verdict["hints"]:
            hint_by_label[(hint, label)] += 1
    n_in = sum(1 for g in gold.values() if g["in_scope"])
    print(f"gold {len(gold)}건(포함 {n_in}) · 아카이브에서 찾음 {len(articles)} · 못 찾음 {len(missing)}")
    print(f"포함 기사 통과 {stats[(True, True)]}/{stats[(True, True)] + stats[(True, False)]}"
          f" · 제외 기사 통과 {stats[(False, True)]}/{stats[(False, True)] + stats[(False, False)]}")
    print("힌트 × 라벨:", dict(sorted(hint_by_label.items())))


def cmd_requests(args) -> None:
    articles, _ = gold_articles()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "requests.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for start in range(0, len(articles), deliberation_lane.BATCH_SIZE):
            chunk = articles[start:start + deliberation_lane.BATCH_SIZE]
            system, user = deliberation_lane.build_request(chunk)
            handle.write(json.dumps({"hashes": [a["hash"] for a in chunk], "user": user},
                                    ensure_ascii=False) + "\n")
    print(f"요청 {path} (system 프롬프트 v{deliberation_lane.PROMPT_VERSION} 는 모듈 상수)")


def pair_agreement(gold_keys: dict[str, str], pred_keys: dict[str, str]) -> dict:
    """같은 사건 판정의 쌍 단위 일치. 사건 이름 문자열이 아니라 '둘이 같은 사건인가'를 비교한다."""
    hashes = sorted(set(gold_keys) & set(pred_keys))
    tp = fp = fn = 0
    for a, b in itertools.combinations(hashes, 2):
        same_gold = gold_keys[a] == gold_keys[b]
        same_pred = deliberation_lane.event_key_norm(pred_keys[a]) == deliberation_lane.event_key_norm(pred_keys[b])
        tp += same_gold and same_pred
        fp += same_pred and not same_gold
        fn += same_gold and not same_pred
    return {"pairs": len(hashes) * (len(hashes) - 1) // 2, "tp": tp, "fp_over_merge": fp, "fn_split": fn}


def cmd_judge(args) -> None:
    articles, gold = gold_articles()
    verdicts = deliberation_lane.judge(articles)
    rows, conf = [], Counter()
    for article, verdict in zip(articles, verdicts):
        g = gold[article["hash"]]
        if verdict is None:
            conf["failed"] += 1
            continue
        conf[(g["in_scope"], verdict["in_scope"])] += 1
        rows.append({"hash": article["hash"], "gold": g, "pred": verdict})
    tp, fn, fp = conf[(True, True)], conf[(True, False)], conf[(False, True)]
    recall = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    gold_keys = {r["hash"]: r["gold"]["event_key"] for r in rows if r["gold"]["in_scope"] and r["pred"]["in_scope"]}
    pred_keys = {r["hash"]: r["pred"]["event_key"] for r in rows if r["gold"]["in_scope"] and r["pred"]["in_scope"]}
    result = {"prompt_version": deliberation_lane.PROMPT_VERSION, "recall": round(recall, 3),
              "precision": round(precision, 3), "failed": conf["failed"],
              "confusion": {f"{k[0]}->{k[1]}": v for k, v in conf.items() if isinstance(k, tuple)},
              "event_pairs": pair_agreement(gold_keys, pred_keys)}
    (OUT / "judge_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "judge_rows.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                                          encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("--since", default="2026-09-01")
    sub.add_parser("prefilter")
    sub.add_parser("requests")
    sub.add_parser("judge")
    args = parser.parse_args()
    {"candidates": cmd_candidates, "prefilter": cmd_prefilter, "requests": cmd_requests,
     "judge": cmd_judge}[args.cmd](args)


if __name__ == "__main__":
    main()
