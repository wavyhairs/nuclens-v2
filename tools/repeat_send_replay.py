"""재발송 회귀셋 재생 — LLM 0회.

`eval_artifacts/repeat_send/labels.jsonl` (9/26~10/25 발송 중 앞선 발송과 같은 이슈로
의심되는 256건을 사람이 판정한 것)을 읽어 두 가지를 잰다.

1. 찾기 재현율 — 재탕(R)마다 판정 근거가 된 앞선 카드를
     현행  : dedup._suspects (발송 7일, 어휘 상위 3건)
     원장  : told_ledger.related (발송 30일, 어휘·근거·웹 이슈 확장)
   가 후보 목록에 넣는가. 넣지 못하면 뒤의 어떤 판정도 그 재탕을 못 잡는다.
2. 그림자 판정 대조 — `repeat_shadow_log.jsonl` 이 있으면 판정(drop/late/new…)을
   라벨에 대 본다. 판정이 쌓이기 전에는 건너뛴다.

    python tools/repeat_send_replay.py            # 요약
    python tools/repeat_send_replay.py --misses   # 원장이 못 찾은 재탕 나열

주의: 웹 이슈 원장(issue_ledger.json)은 **지금** 상태라, 과거 시점에는 아직 없던 묶음이
섞일 수 있다(앞선 카드끼리의 묶음은 대부분 그때도 있었다). 재현율은 상한에 가깝게 읽는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dedup  # noqa: E402
import issue_continuity  # noqa: E402
import told_ledger  # noqa: E402

LABELS = ROOT / "eval_artifacts" / "repeat_send" / "labels.jsonl"
SHADOW_LOG = ROOT / "repeat_shadow_log.jsonl"


def load_labels(path: Path = LABELS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sends_by_key(path: Path = told_ledger.DELIVERY_LOG_FILE) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and not row.get("record_type") and row.get("hash"):
            out.setdefault((str(row.get("date")), str(row["hash"])), row)
    return out


def retrieval(labels: list[dict], *, show_misses: bool = False) -> dict:
    cfg = issue_continuity.resolve_config(json.loads((ROOT / "ranking_config.json").read_text(encoding="utf-8")))
    sends = sends_by_key()
    issues = told_ledger.issue_index()
    result = collections.Counter()
    misses: list[str] = []
    for lab in labels:
        if lab["label"] != "R" or not lab.get("shown_priors"):
            continue
        cand = sends.get((lab["date"], lab["hash"]))
        if cand is None:
            result["no_send_record"] += 1
            continue
        target = lab["shown_priors"][0]["hash"]
        told30 = told_ledger.load_told(lab["date"])
        told7 = told_ledger.load_told(lab["date"], days=dedup.CROSS_DAY_SENT_DAYS)
        generic = issue_continuity.generic_anchors(told30)
        old = {r["hash"] for r in dedup._suspects(cand, told7, generic)}
        new_rows = told_ledger.related(cand, told30, issues, cfg, generic)
        new = {r["hash"] for r in new_rows}
        result["R"] += 1
        result["old_hit"] += target in old
        result["new_hit"] += target in new
        result["new_only"] += (target in new) and (target not in old)
        result["old_only"] += (target in old) and (target not in new)
        if target not in new:
            misses.append(f"  {lab['date']} {lab['title'][:40]} ← {lab['shown_priors'][0]['date']} "
                          f"{lab['shown_priors'][0]['title'][:40]}")
    print(f"찾기 재현율 — 재탕 {result['R']}건의 판정 근거 카드")
    print(f"  현행(7일·어휘 3건) : {result['old_hit']}")
    print(f"  원장(30일·확장)    : {result['new_hit']}  (원장만 찾음 {result['new_only']}, 현행만 찾음 {result['old_only']})")
    if show_misses and misses:
        print("원장이 못 찾은 재탕:")
        print("\n".join(misses))
    return dict(result)


def shadow_agreement(labels: list[dict], path: Path = SHADOW_LOG) -> dict:
    if not path.exists():
        print("그림자 판정 기록 없음 — 건너뜀")
        return {}
    by_key = {(lab["date"], lab["hash"]): lab["label"] for lab in labels}
    table = collections.Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        for row in rec.get("rows") or ():
            label = by_key.get((rec.get("date"), row.get("hash")), "미판정")
            table[(label, row.get("verdict"))] += 1
    print("그림자 판정 × 라벨")
    for (label, verdict), n in sorted(table.items()):
        print(f"  {label:<4} {verdict:<14} {n}")
    return {f"{k[0]}|{k[1]}": v for k, v in table.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--misses", action="store_true", help="원장이 못 찾은 재탕 나열")
    args = parser.parse_args()
    labels = load_labels()
    counts = collections.Counter(lab["label"] for lab in labels)
    print(f"회귀셋 {len(labels)}건: {dict(counts)}")
    retrieval(labels, show_misses=args.misses)
    shadow_agreement(labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
