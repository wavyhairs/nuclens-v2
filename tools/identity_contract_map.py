"""사람의 관계 판정을 **profile 별** MERGE/SEPARATE 로 옮기는 버전 있는 매핑.

## 왜 매핑이 필요한가 — 계약이 서로 모순된다

`issue_review` 와 `dedup` 은 같은 예시를 들면서 **반대 판정을 요구한다.**

    issue_review.SYSTEM_PROMPT
      "그 사안의 후속 보도. 진행 단계가 바뀐 것은 같은 사건이다
       (심의 착수 → 심의 지연 → 승인, 협상 → 계약 체결)"        → MERGE

    dedup.ARTICLE_STORY_PROMPT 규칙 4
      "후속 보도라도 새로운 독립적 행동/결정/상태전환이 있으면 별도 그룹으로 유지한다.
       예: '심사 착수' 이후 '최종 승인', ... '협상' 이후 '계약 체결'"   → SEPARATE

버그가 아니라 서로 다른 질문이다. `issue_review` 는 오래 가는 **이슈**를 잇고,
`dedup` 은 그날 **브리핑 한 칸**의 중복을 없앤다. 단계가 바뀐 두 보도는 같은
이슈이면서 별개의 브리핑 항목일 수 있다.

그래서 따라오는 결론이 중요하다 — **하나의 Identity 정답지는 성립하지 않는다.**
같은 쌍의 정답이 profile 마다 다르다. 사람에게는 "관계"를 묻고, 판정은 profile
계약이 정한다. 그리고 그 변환을 코드에 박아 두지 않고 버전 있는 표로 둔다.

## 기존 60건이 어느 쪽에 맞춰져 있었나

`different_stage → SEPARATE` 는 **dedup 계약과 일치**하고 **issue_review 계약과
어긋난다.** 즉 기존 Gold 를 폐기할 이유는 없다. 옮겨 쓸 대상을 잘못 골랐을 뿐이다.

## 판정할 수 없는 것은 판정하지 않는다

reason code 하나로 두 계약을 모두 가를 수 없는 경우가 있다. 예를 들어
`follow_up_same_event` 는 dedup 에서 **후속에 새 행동이 있었는지**에 따라 갈리는데,
그 정보가 코드에 없다. 그런 칸은 `NEEDS_CASE_REVIEW` 로 남긴다. 추측해 채우면
사람이 다시 못 볼 곳에 오답이 박힌다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAP_VERSION = "identity-contract-map-v1"
MERGE, SEPARATE, REVIEW, EXCLUDE = "MERGE", "SEPARATE", "NEEDS_CASE_REVIEW", "EXCLUDE"

# reason_code → (issue_review, dedup 계열) 판정.
#
# 근거는 각 production 프롬프트의 문장이다. 근거 문장을 함께 적어 두는 이유는,
# 프롬프트가 바뀌면 이 표가 먼저 틀리기 때문이다 — 그때 무엇을 다시 읽어야 하는지
# 알 수 있어야 한다.
CONTRACT_MAP: dict[str, dict[str, object]] = {
    "same_action": {
        "issue_review": MERGE, "dedup": MERGE,
        "why": "동일 주체·대상의 한 사안 (양쪽 계약 모두 같은 사건)"},
    "same_meeting": {
        "issue_review": MERGE, "dedup": MERGE,
        "why": "같은 회의 보도 (양쪽 모두 같은 사건)"},
    "same_announcement": {
        "issue_review": MERGE, "dedup": MERGE,
        "why": "같은 발표 (양쪽 모두 같은 사건)"},
    "multi_source_same_event": {
        "issue_review": MERGE, "dedup": MERGE,
        "why": "같은 사안의 다매체 보도 — dedup 은 duplicate 로 묶는다"},
    "different_stage": {
        "issue_review": MERGE, "dedup": SEPARATE,
        "why": "★ 계약 충돌. issue_review 는 단계 변화를 같은 사건으로, "
               "dedup 규칙 4 는 새 결정/상태전환을 별도 그룹으로 본다"},
    "follow_up_same_event": {
        "issue_review": MERGE, "dedup": REVIEW,
        "why": "issue_review 는 후속 보도를 같은 사건으로 본다. dedup 은 후속에 "
               "새 독립 행동이 있었는지에 갈리는데 그 정보가 reason code 에 없다"},
    "different_action": {
        "issue_review": REVIEW, "dedup": SEPARATE,
        "why": "dedup 규칙 4 는 새 행동을 분리한다. issue_review 는 같은 사안 안의 "
               "행동인지 별개 안건인지에 갈리고, 그 구분이 reason code 에 없다"},
    "same_entity_only": {
        "issue_review": SEPARATE, "dedup": SEPARATE,
        "why": "issue_review '주체는 같지만 안건이 다르다', dedup 규칙 5 '다른 "
               "프로젝트면 별도 그룹' — 양쪽 계약 모두 다른 사건"},
    "different_unit": {
        "issue_review": SEPARATE, "dedup": SEPARATE,
        "why": "대상 호기·시설이 다르다 (양쪽 계약 모두 다른 사건)"},
    "broader_topic": {
        "issue_review": SEPARATE, "dedup": SEPARATE,
        "why": "개별 사건 대 업계 동향 (양쪽 계약 모두 다른 사건)"},
    "different_time": {
        "issue_review": REVIEW, "dedup": REVIEW,
        "why": "시점 차이만으로는 단계 변화인지 별개 사안인지 갈리지 않는다"},
    "insufficient_context": {
        "issue_review": EXCLUDE, "dedup": EXCLUDE,
        "why": "근거 부족 — 어느 계약으로도 판정 대상이 아니다"},
    "unclear_event_boundary": {
        "issue_review": EXCLUDE, "dedup": EXCLUDE,
        "why": "사건 경계 자체가 미정 — 1차 지표에서 뺀다"},
    "other": {
        "issue_review": REVIEW, "dedup": REVIEW,
        "why": "사유가 코드화되지 않았다. 케이스를 읽어야 한다"},
}

PROFILES = {"issue_review": "issue_review", "keei_match": "issue_review",
            "dedup": "dedup", "dedup_final": "dedup"}


def verdict(reason_code: str, profile: str) -> str:
    contract = PROFILES.get(profile)
    if contract is None:
        raise ValueError(f"매핑되지 않은 profile: {profile}")
    row = CONTRACT_MAP.get(reason_code)
    if row is None:
        raise ValueError(f"매핑되지 않은 reason_code: {reason_code}")
    return str(row[contract])


def apply_to(cases: list[dict], profile: str) -> dict:
    """사람이 붙인 reason code 를 profile 판정으로 옮기고, 남는 것을 센다."""
    usable, review, excluded, conflicts = [], [], [], []
    for case in cases:
        if not case.get("human_label"):
            continue
        decision = verdict(case["reason_code"], profile)
        row = {"id": case["id"], "reason_code": case["reason_code"],
               "stored_label": case["human_label"], "mapped": decision}
        if decision == REVIEW:
            review.append(row)
        elif decision == EXCLUDE:
            excluded.append(row)
        else:
            usable.append(row)
            if decision != case["human_label"]:
                # 기존 라벨이 이 profile 계약과 어긋난다. 라벨이 틀린 게 아니라
                # 다른 계약에 맞춰져 있었던 것이다.
                conflicts.append(row)
    return {
        "map_version": MAP_VERSION,
        "profile": profile,
        "contract": PROFILES[profile],
        "usable": len(usable),
        "needs_case_review": len(review),
        "excluded": len(excluded),
        "label_flipped_by_contract": len(conflicts),
        "flipped_rows": conflicts,
        "review_rows": review,
        "verdict_counts": dict(Counter(row["mapped"] for row in usable)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", type=Path,
        default=ROOT / "tests" / "fixtures" / "gemini_reasoning" /
        "identity_candidates.json")
    args = parser.parse_args()
    cases = json.loads(args.fixtures.read_text(encoding="utf-8"))["cases"]
    print(json.dumps(
        {profile: apply_to(cases, profile)
         for profile in ("issue_review", "dedup", "dedup_final")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
