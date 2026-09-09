"""사람이 내린 판정을 canonical fixture 에 올린다. 원본은 지우지 않는다.

## 무엇이 확인됐길래 올리는가

blind 재검증 15건 결과: 일치 11, 불일치 4, **불일치가 전부 한 방향**(보관 라벨이 더
관대)이었다. 층별로 보면 성격이 확연히 갈린다.

    REPAIR 층                 6/6 일치      → 보관 라벨이 견고하다
    controlled_perturbation   5/6 일치      → 대체로 견고, 경계 1건만 흔들림
    PASS 층                   0/3 일치      → **전부 뒤집혔다**

즉 anchoring 이 **관대한 방향으로만** 작동했다. AI 가 "문제 없음"이라 하면 사람이
그대로 승인했지만, 가리고 보니 문제가 있었다.

## 승격 규칙

- **blind 와 보관이 일치**하면 그 라벨은 독립 검증을 통과한 것이다 →
  `HUMAN_BLIND_CONFIRMED`. 이제 평가에 쓸 수 있다.
- **불일치**하면 blind 쪽을 canonical 로 삼는다 → `HUMAN_BLIND_CORRECTED`.
  모델 결과에 맞춘 수정이 아니라 **가려진 판정이 가려지지 않은 판정을 이긴 것**이다.
  보관 라벨은 `superseded_label` 로 남겨 이력을 지우지 않는다.
- **blind 를 거치지 않은 AI 보조 라벨**은 그대로 둔다. 승격도 강등도 하지 않는다.

## Identity 는 라벨이 아니라 관계를 올린다

Identity 22건은 reason code 로 두 계약을 가를 수 없어 사람에게 관계를 물은 것이다.
그래서 `human_label` 을 덮지 않고 `human_relation` 을 **덧붙인다.** 판정은 여전히
`review_queue.RELATION_TO_VERDICT` 가 profile 별로 정한다 — 하나의 정답지가 없기
때문이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import review_queue
from tools.gold_labeler import atomic_write_json
from tools.gold_provenance import AI_ASSISTED, FIXTURES

BLIND_CONFIRMED = "HUMAN_BLIND_CONFIRMED"
BLIND_CORRECTED = "HUMAN_BLIND_CORRECTED"
RELATION_REVIEWED = "HUMAN_RELATION_REVIEWED"


def apply_blind(payload: dict, blind: dict[str, str]) -> dict:
    """가린 채 받은 판정을 반영한다. 원본 라벨은 `superseded_label` 로 남는다."""
    changed = Counter()
    for case in payload.get("cases") or []:
        verdict = blind.get(case["id"])
        if verdict is None:
            continue
        if case.get("label_status") != AI_ASSISTED:
            # blind 표본은 AI 보조 라벨에서만 뽑았다. 다른 상태가 섞였다면
            # 표본 선정이 어긋난 것이므로 조용히 덮지 않는다.
            changed["skipped_unexpected_status"] += 1
            continue
        if verdict == case["human_label"]:
            case["label_status"] = BLIND_CONFIRMED
            changed["confirmed"] += 1
        else:
            case["superseded_label"] = case["human_label"]
            case["human_label"] = verdict
            case["label_status"] = BLIND_CORRECTED
            changed["corrected"] += 1
    return dict(changed)


def apply_relations(payload: dict, relations: dict[str, str]) -> dict:
    """관계 판정을 덧붙인다. `human_label` 과 `reason_code` 는 건드리지 않는다."""
    changed = Counter()
    for case in payload.get("cases") or []:
        relation = relations.get(case["id"])
        if relation is None:
            continue
        if relation not in review_queue.RELATION_TO_VERDICT:
            raise ValueError(f"{case['id']}: 알 수 없는 관계 {relation!r}")
        case["human_relation"] = relation
        case["relation_status"] = RELATION_REVIEWED
        changed["relations"] += 1
    return dict(changed)


def stratum_risk(payload: dict) -> dict:
    """어느 층의 보관 라벨이 무너졌는지 센다.

    표본에서 한 층이 통째로 뒤집혔다면, 그 층의 **나머지 라벨도 못 믿는다.** 그
    사실이 보고서에 남아야 다음 재라벨 범위를 정할 수 있다.
    """
    risk: dict[str, dict] = {}
    for case in payload.get("cases") or []:
        stored = case.get("superseded_label") or case.get("human_label")
        if not stored:
            continue
        row = risk.setdefault(stored, {"sampled": 0, "corrected": 0, "unsampled": 0})
        if case.get("label_status") == BLIND_CORRECTED:
            row["sampled"] += 1
            row["corrected"] += 1
        elif case.get("label_status") == BLIND_CONFIRMED:
            row["sampled"] += 1
        elif case.get("label_status") == AI_ASSISTED:
            row["unsampled"] += 1
    for label, row in risk.items():
        row["verdict"] = _verdict(row)
    return risk


def _verdict(row: dict) -> str:
    """층의 상태를 **다음에 할 일**로 번역한다.

    "표본이 전부 뒤집혔다"만으로 재라벨을 요구하면 두 가지를 놓친다. 표본이 1건이면
    한 번 뒤집힌 것이 층 전체의 성질이라는 근거가 못 되고, 미표본이 0이면 뒤집혔더라도
    **재라벨할 대상 자체가 없다.** 둘 다 실제 데이터에서 나왔다.
    """
    sampled, corrected, left = row["sampled"], row["corrected"], row["unsampled"]
    if not sampled:
        return "UNVERIFIED" if left else "EMPTY"
    if not left:
        return "RESOLVED_WITH_CORRECTIONS" if corrected else "CONFIRMED"
    if corrected == sampled and sampled >= 2:
        return "RELABEL_REQUIRED"
    if corrected:
        return "PARTIALLY_SHAKEN"
    return "CONFIRMED"


def run(apply: bool) -> dict:
    blind = review_queue.load(("blind",))
    relations = review_queue.load(("identity",))
    report: dict[str, object] = {"blind_judgments": len(blind),
                                 "relation_judgments": len(relations)}
    for task in ("curation", "semantic"):
        path = FIXTURES / f"{task}_gold.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        report[task] = apply_blind(payload, blind)
        report[f"{task}_stratum_risk"] = stratum_risk(payload)
        if apply:
            atomic_write_json(path, payload)

    path = FIXTURES / "identity_candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    report["identity"] = apply_relations(payload, relations)
    if apply:
        atomic_write_json(path, payload)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="fixture 에 실제로 쓴다 (생략하면 미리보기)")
    args = parser.parse_args()
    print(json.dumps(run(args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
