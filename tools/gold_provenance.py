"""Human Gold 의 **출처**를 바로잡고, anchoring 크기를 잴 표본을 고른다.

## 왜 필요한가

Curation/Semantic 의 `HUMAN_LABELLED` 라벨은 리뷰 UI 를 거쳐 만들어졌다. 그 UI 는
GPT-5.6 Sol 의 판정·확신도·근거를 **케이스와 동시에 보여 주고** `A` 한 키로 승인하게
했다. 사람이 읽고 눌렀다는 사실은 있지만, 그것은 독립 판정이 아니라 **비준**이다.
그대로 "Human Gold" 라고 부르면 reasoning 비교가 실제로는 "Gemini 가 Sol 과
얼마나 같은가"를 재게 된다.

그래서 라벨 값은 **한 글자도 바꾸지 않고** 상태만 `HUMAN_REVIEWED_AI_ASSISTED` 로
바꾼다. 폐기하지 않는 이유는 사람이 실제로 읽었기 때문이고, 승격하지 않는 이유는
anchoring 크기를 아직 모르기 때문이다.

`USER_SPECIFIED` 는 사용자가 직접 지정한 것이라 그대로 둔다.

## 표본을 경계층에 몰아 고르는 이유

anchoring 은 판정이 갈리는 자리에서 드러난다. 구성부터 정답이 정해진 케이스
(`unsupported_inference` 는 24/24 가 BLOCK)에서는 anchoring 이 있어도 라벨이 같아
아무것도 재지 못한다. 그래서 예산을 경계에 쓴다.

- Semantic `controlled_perturbation` — BLOCK 인지 REPAIR 인지가 판단 대상
- Curation `REPAIR` — PASS 와의 경계
- Curation `PASS` — REPAIR 로 내려갈 여지가 있는 쪽

## 무엇을 읽어야 하는가

일치율보다 **불일치의 방향**이다. 전부 "AI 가 더 관대"(REPAIR→PASS, BLOCK→REPAIR)
한 방향이면 anchoring 이고, 양방향이면 그냥 어려운 판정이다. 방향을 보지 않으면
같은 일치율을 정반대로 해석하게 된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "gemini_reasoning"
AI_ASSISTED = "HUMAN_REVIEWED_AI_ASSISTED"
DIRECT = "USER_SPECIFIED"
PENDING = "HUMAN_LABEL_REQUIRED"
# 리뷰 UI 를 거친 상태값. 이 상태로 저장된 라벨은 Sol 판정을 본 뒤의 판단이다.
ANCHORED_SOURCE = "HUMAN_LABELLED"
# blind 재검증을 거친 뒤의 상태. 표본은 **고정 집합**이라 승격 뒤에도 같은 케이스가
# 뽑혀야 한다 — 그래야 패킷이 재현되고 대조를 다시 돌릴 수 있다.
BLIND_CONFIRMED = "HUMAN_BLIND_CONFIRMED"
BLIND_CORRECTED = "HUMAN_BLIND_CORRECTED"
BLIND_ELIGIBLE = frozenset({AI_ASSISTED, BLIND_CONFIRMED, BLIND_CORRECTED})

# 관대함의 방향. 큰 값일수록 개입이 강한 판정이다.
SEVERITY = {"PASS": 0, "REPAIR": 1, "UNVERIFIABLE": 1, "BLOCK": 2}

BLIND_SAMPLE = (
    # (task, 층 이름, 고를 개수)
    ("semantic", "controlled_perturbation", 6),
    ("curation", "REPAIR", 6),
    ("curation", "PASS", 3),
)


def reclassify(payload: dict) -> tuple[dict, int]:
    """라벨 값은 그대로 두고 상태만 바꾼다."""
    changed = 0
    for case in payload.get("cases") or []:
        if case.get("label_status") == ANCHORED_SOURCE:
            case["label_status"] = AI_ASSISTED
            changed += 1
    if changed:
        payload["provenance_note"] = (
            "HUMAN_REVIEWED_AI_ASSISTED 는 사람이 읽고 승인했으나 AI 판정을 먼저 본 "
            "뒤의 판단이다. 라벨 값은 변경되지 않았다. 독립 Gold 로 승격하려면 "
            "blind 재검증으로 anchoring 크기를 먼저 재야 한다.")
    return payload, changed


def _stratum(task: str, case: dict) -> str | None:
    if task == "semantic":
        return case.get("candidate_kind")
    # 층은 **원래 보관돼 있던 라벨**로 정한다. 정정된 뒤의 라벨로 나누면 층이
    # 움직여서, 같은 표본이 다음 실행에 다른 층으로 잡힌다.
    return case.get("superseded_label") or case.get("human_label")


def select_blind_sample(payloads: dict[str, dict]) -> list[dict]:
    """경계층에서 결정적으로 고른다.

    선택은 모델 출력과 **무관해야** 한다. 정렬이나 인덱스가 아니라 case id 해시로
    고르는 이유가 그것이다 — 고른 뒤에 결과를 보고 표본을 바꾸는 길을 막는다.
    """
    picked: list[dict] = []
    for task, stratum, count in BLIND_SAMPLE:
        cases = [case for case in payloads[task].get("cases") or []
                 if case.get("label_status") in BLIND_ELIGIBLE
                 and _stratum(task, case) == stratum]
        cases.sort(key=lambda case: hashlib.sha256(
            f"blind-v1|{task}|{case['id']}".encode("utf-8")).hexdigest())
        if len(cases) < count:
            raise ValueError(
                f"{task}/{stratum}: AI 보조 라벨이 {len(cases)}건뿐이라 {count}건을 "
                "고를 수 없다")
        picked.extend({"task": task, "stratum": stratum, "case_id": case["id"]}
                      for case in cases[:count])
    return picked


def compare(blind: dict[str, str], payloads: dict[str, dict],
            sample: list[dict]) -> dict:
    """blind 판정과 보관된 라벨을 대조한다. **방향**이 핵심이다."""
    rows, directions = [], Counter()
    for item in sample:
        stored = next(case for case in payloads[item["task"]]["cases"]
                      if case["id"] == item["case_id"])
        new = blind.get(item["case_id"])
        if new is None:
            continue
        # **정정 전 라벨**과 비교한다. 정정된 라벨과 비교하면 승격 뒤 재실행에서
        # 전부 일치로 보여, 애초에 anchoring 을 발견한 증거가 지워진다.
        old = stored.get("superseded_label") or stored["human_label"]
        if new == old:
            direction = "agree"
        elif SEVERITY.get(new, 1) > SEVERITY.get(old, 1):
            # 사람이 blind 로 더 강하게 개입했다 = 보관된 라벨이 더 관대했다.
            direction = "stored_more_lenient"
        else:
            direction = "stored_more_strict"
        directions[direction] += 1
        rows.append({**item, "stored": old, "blind": new, "direction": direction})

    disagreements = len(rows) - directions["agree"]
    one_way = (disagreements > 0 and
               (directions["stored_more_lenient"] == disagreements
                or directions["stored_more_strict"] == disagreements))
    return {
        "compared": len(rows),
        "agreement": directions["agree"] / len(rows) if rows else None,
        "directions": dict(directions),
        "rows": rows,
        "reading": (
            "불일치가 한 방향으로 몰렸다 — anchoring 신호다. 재라벨 범위를 넓혀야 한다."
            if one_way else
            "불일치가 양방향이거나 없다 — anchoring 보다 판정 난이도로 읽는 쪽이 맞다."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="fixture 의 label_status 를 실제로 바꾼다")
    args = parser.parse_args()

    paths = {"curation": FIXTURES / "curation_gold.json",
             "semantic": FIXTURES / "semantic_gold.json"}
    payloads = {task: json.loads(path.read_text(encoding="utf-8"))
                for task, path in paths.items()}
    summary = {}
    for task, payload in payloads.items():
        payload, changed = reclassify(payload)
        summary[task] = {
            "reclassified": changed,
            "status_counts": dict(Counter(case.get("label_status")
                                          for case in payload["cases"])),
        }
        if args.apply:
            paths[task].write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
    summary["blind_sample"] = select_blind_sample(payloads)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
