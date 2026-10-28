"""재발송 그림자 판정을 지난 발송에 다시 돌려 사람 라벨과 맞대 본다 — LLM 호출 있음.

`repeat_shadow` 의 질문(프롬프트)을 바꿀 때 쓴다. 매일 기록은 하루 10~20장이라 2주를
기다려야 바뀐 질문의 효과가 보이지만, 회귀셋(`eval_artifacts/repeat_send/labels.jsonl`,
9/26~10/25 발송 256건 사람 판정)이 있으므로 그 날들의 발송을 그대로 다시 물으면 바로 잰다.

    python tools/repeat_shadow_replay.py --since 2026-09-26 --until 2026-10-28
    python tools/repeat_shadow_replay.py --since 2026-10-27 --until 2026-10-27 --max-calls 5

재료는 `delivery_log.jsonl` 의 그날 발송 카드(제목·요약·상세)와, 그날 **이전** 발송으로 만든
원장이다(`told_ledger.load_told`). 웹 이슈 원장은 지금 것이라 과거 시점보다 묶음이 조금 더
많을 수 있다(`repeat_send_replay.py` 와 같은 주의). 기록 파일은 건드리지 않는다.

읽는 법: 라벨 R(재탕)은 drop 이어야 하고, F(후속)·L(늦은 첫 보도)·D(별건)는 drop 이면 안 된다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import repeat_shadow  # noqa: E402
import told_ledger  # noqa: E402
from gemini_client import call_json  # noqa: E402

LABELS = ROOT / "eval_artifacts" / "repeat_send" / "labels.jsonl"


def sent_cards(path: Path = told_ledger.DELIVERY_LOG_FILE) -> dict[str, dict[str, list[dict]]]:
    """날짜 → 지역 → 그날 보낸 카드(브리핑 순서). 같은 줄이 두 번이면 하나만."""
    out: dict[str, dict[str, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    seen: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("record_type") or not row.get("hash"):
            continue
        key = (str(row.get("date")), str(row["hash"]))
        if key in seen:
            continue
        seen.add(key)
        region = row.get("region") or row.get("brief_region") or "국내"
        out[key[0]][region].append(row)
    for regions in out.values():
        for cards in regions.values():
            cards.sort(key=lambda r: int(r.get("brief_rank") or 99))
    return out


def load_labels(path: Path = LABELS) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    return {(row["date"], row["hash"]): row["label"]
            for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip())}


def throttled(pause: float):
    def call(*args, **kwargs):
        time.sleep(pause)
        return call_json(*args, **kwargs)
    return call


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--max-calls", type=int, default=60, help="전체 호출 상한")
    parser.add_argument("--pause", type=float, default=2.0, help="호출 사이 대기(초)")
    parser.add_argument("--out", type=Path, default=ROOT / "repeat_shadow_replay.json")
    args = parser.parse_args()

    cards = sent_cards()
    labels = load_labels()
    days = sorted(d for d in cards if args.since <= d <= args.until)
    budget = args.max_calls
    records: list[dict] = []
    for day in days:
        if budget <= 0:
            print(f"{day}: 호출 상한 소진 — 여기서 멈춤")
            break
        recs = repeat_shadow.run(day, dict(cards[day]), call=throttled(args.pause),
                                 max_calls=budget, time_budget=3600.0)
        budget -= sum(r["calls"] for r in recs)
        records += recs
        summary = collections.Counter(row["verdict"] for r in recs for row in r["rows"])
        print(f"{day}: {dict(summary)} · 남은 호출 {budget}")
        # 하루치가 판정 없이 전부 실패면 한도·설정 문제다 — 더 부르면 운영 버킷만 깎는다.
        if summary.get("error") and not any(v for v in summary if v not in ("error", "skipped", "new_issue")):
            print(f"{day}: 판정이 전부 실패 — 한도·설정 문제로 보고 여기서 멈춤")
            break

    table: collections.Counter = collections.Counter()
    misses: list[str] = []
    for rec in records:
        for row in rec["rows"]:
            label = labels.get((rec["date"], row["hash"]), "-")
            table[(label, row["verdict"])] += 1
            wrong = (label == "R" and row["verdict"] not in ("drop", "error", "skipped")) or (
                label in ("F", "L", "D") and row["verdict"] == "drop")
            if wrong or (label == "-" and row["verdict"] == "drop"):
                facts = "; ".join(f"[{f.get('kind')}/{f.get('date')}/{'Q' if f.get('quote_ok') else 'x'}] "
                                  f"{f.get('fact', '')[:60]}" for f in row.get("untold") or [])
                told = "; ".join(f"{f['told_in']}:{f['fact'][:40]}" for f in row.get("told_facts") or [])
                prior = (row.get("told") or [{}])[-1]
                misses.append(f"  {'틀림' if wrong else '확인'} {rec['date']} {rec['region']} #{row['position']} "
                              f"라벨={label} 판정={row['verdict']} {row['title'][:44]}\n"
                              f"      ← {prior.get('date')} {str(prior.get('title') or '')[:44]}\n"
                              f"      새것: {facts or '(없음)'}\n"
                              f"      이미 전함: {told or '(없음)'}")

    def count(label: str, verdicts: tuple[str, ...]) -> tuple[int, int]:
        hit = sum(n for (lab, v), n in table.items() if lab == label and v in verdicts)
        total = sum(n for (lab, v), n in table.items() if lab == label and v not in ("error", "skipped"))
        return hit, total

    print("\n라벨 × 판정")
    for (label, verdict), n in sorted(table.items()):
        print(f"  {label:<2} {verdict:<12} {n}")
    r_hit, r_total = count("R", ("drop",))
    print(f"\n재탕(R) 잡음: {r_hit}/{r_total}")
    for label, name in (("F", "후속"), ("L", "늦은 첫 보도"), ("D", "별건")):
        bad, total = count(label, ("drop",))
        print(f"{name}({label}) 잘못 뺌: {bad}/{total}")
    unlabeled_drop = sum(n for (lab, v), n in table.items() if lab == "-" and v == "drop")
    print(f"라벨 없는 카드 중 drop: {unlabeled_drop}")
    print(f"error·skipped: {sum(n for (_l, v), n in table.items() if v in ('error', 'skipped'))}")
    if misses:
        print("\n틀린 것·라벨 없는 drop")
        print("\n".join(misses))
    args.out.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
