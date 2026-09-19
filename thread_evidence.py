"""장기 스토리 연결의 **증거가 얼마나 좁은가**를 재는 한 곳.

왜 별도 모듈인가
----------------
`thread_judge` 는 "모델이 뭐라고 했나"를 들고 오고, `thread_identity` 는 "그 답으로
어떻게 묶나"를 정한다. 그 사이에 빠져 있던 것이 **그 답을 믿어도 되는가**다.

실측(2026-09-19, 캐시 3,170건)이 보여준 것 —

    원안위, 2027년 예산 3,030억 편성…  ↔  원안위, 오르비텍 핵연료물질 사용 허가
        verdict = same_thread
        reason  = "원자력안전위원회의 규제 및 예산 관련 활동임"

이 둘이 공유하는 것은 `nssc` 하나다. 기관이 같다는 말은 장기 스토리의 근거가
아니다 — 원안위는 매주 서로 다른 안건을 의결한다. 같은 구조로 —

    미국의 러시아산 우라늄 수입 금지법  ↔  웨스팅하우스 지분 확보 시나리오
        verdict = same_thread / cause_effect
        공유    = westinghouse 하나

`cause_effect` 는 특히 위험하다. 공통 기업이 하나 있으면 편집자는 언제나 이야기를
만들어 낼 수 있기 때문이다.

판별력 — 이 모듈의 전부
-----------------------
    호기            kori-2 는 한 대상만 가리킨다              강하다
    설비·프로젝트   특정 시설·특정 사업                        강하다
    기관·기업       업계 전반에 반복 등장한다                  약하다

기관명을 코드에 적지 않는다. `entity_registry.json` 이 이미 type 을 들고 있고
(plant 26 · company 21 · org 13 · project 4), 그 표가 늘어나면 이 모듈은 그대로
따라간다. `nssc` 라고 적는 순간 `doe` 와 `nrc` 가 다음 주에 같은 사고를 낸다.

왜 retrieval 이 아니라 여기인가
-------------------------------
후보 생성은 recall 이 일이다(`event_retrieval` 의 docstring). 여기서 좁은 증거를
요구하면 구조화 칸이 빈 옛 사건이 통째로 잘린다 — `thread_judge.LEXICAL_RESCUE`
가 정확히 그 사고를 한 번 겪고 생겼다. 그래서 거르는 자리는 판정 **뒤**다.

    retrieval   recall 유지
    judge       모델이 답한다
    여기        좁은 증거가 없으면 잇지 않는다
    cluster     negative 증거가 있으면 합치지 않는다

어휘 문턱의 근거
----------------
좁은 공유가 하나도 없는 same_thread 388쌍을 갈라 보면(2026-09-19 실측) —

    제목이 글자까지 같은 쌍 83건    lexical 최소 6.75 · 중앙 21.03
    제목이 다른 쌍       305건      lexical 최소 1.27 · 중앙  5.25

위 오병합 세 쌍은 2.04 · 2.39 · 2.03 이다. 3.0 은 그 사이에서 **중복 쌍 쪽으로
한참 떨어진** 자리다. 이 문턱이 중복 사건을 건드리는 일은 구조적으로 없다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import thread_judge

ROOT = Path(__file__).resolve().parent
REGISTRY_FILE = ROOT / "entity_registry.json"

EVIDENCE_VERSION = "thread-evidence-v1"

# 한 대상을 가리키는 type. 나머지(company·org)는 업계 전반에 반복 등장한다.
DISCRIMINATIVE_TYPES = frozenset({"plant", "project"})

# 좁은 공유가 없을 때 요구하는 어휘 겹침. 위 docstring 의 실측 참조.
MIN_LEXICAL_WITHOUT_SCOPE = 3.0

# `cause_effect` 는 더 높은 바를 쓴다. 인과를 주장하려면 같은 대상을 말하고 있거나,
# 적어도 **판정으로 넘길 만큼 특이한** 어휘를 공유해야 한다. 그 바는 이미 정해져
# 있다 — `thread_judge.LEXICAL_RESCUE`. 같은 척도(event_retrieval 의 lexical)를
# 쓰므로 숫자를 여기서 새로 정하지 않는다.
MIN_LEXICAL_FOR_CAUSE = thread_judge.LEXICAL_RESCUE

# 게이트가 거부하는 이유. 분포를 보면 어디서 링크가 죽는지 바로 보인다.
REJECT_GENERIC_SCOPE = "generic_scope_only"
REJECT_CAUSE_WITHOUT_OBJECT = "cause_effect_without_object"


@lru_cache(maxsize=1)
def entity_types(path: str | None = None) -> dict[str, str]:
    """entity_id → type. 표가 없으면 빈 표로 돈다(전부 약한 증거로 취급)."""
    target = Path(path) if path else REGISTRY_FILE
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("entities") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    return {str(row.get("id")): str(row.get("type") or "")
            for row in rows if isinstance(row, dict) and row.get("id")}


def discriminative(entity_ids, *, types: dict[str, str] | None = None) -> frozenset:
    """이 엔티티들 중 **한 대상을 가리키는** 것만.

    표에 없는 엔티티는 약한 쪽으로 둔다. 모르는 것을 강한 증거로 세우면
    표가 늘 때마다 판정이 조용히 바뀐다.
    """
    table = entity_types() if types is None else types
    return frozenset(value for value in entity_ids
                     if table.get(str(value)) in DISCRIMINATIVE_TYPES)


def narrow_scope(left, right, *, types: dict[str, str] | None = None) -> frozenset:
    """두 사건이 공유하는 **좁은 대상**. 없으면 빈 집합.

    Args:
        left, right: `event_retrieval.Event`.
    """
    shared_units = frozenset(left.units & right.units)
    shared_assets = frozenset(left.assets & right.assets)
    shared_entities = discriminative(left.entities & right.entities, types=types)
    return shared_units | shared_assets | shared_entities


def gate(verdict: dict, left, right, signals: dict | None = None,
         *, types: dict[str, str] | None = None) -> tuple[bool, str]:
    """이 판정을 **고리로 써도 되는가.**

    `same_thread` 만 본다. 거부는 `different_thread` 가 아니라 `uncertain` 이다 —
    "근거가 모자라 잇지 않는다"는 것과 "다르다고 판정했다"는 것은 다른 말이고,
    뒤쪽만 `thread_identity` 의 거부권 재료가 된다(`PART B` 의 uncertain 규칙).

    Returns:
        (통과 여부, 거부 이유). 통과면 이유는 빈 문자열.
    """
    if (verdict or {}).get("verdict") != "same_thread":
        return False, ""
    scope = narrow_scope(left, right, types=types)
    lexical = float((signals or {}).get("lexical") or 0.0)
    relationship = str((verdict or {}).get("relationship") or "").strip()

    if relationship == "cause_effect" and not scope and lexical < MIN_LEXICAL_FOR_CAUSE:
        # 공통 기업·기관만 있는데 인과를 주장한다. 실측의 '우라늄 금지법 →
        # 웨스팅하우스 지분 시나리오' 가 정확히 이 모양이다.
        return False, REJECT_CAUSE_WITHOUT_OBJECT
    if not scope and lexical < MIN_LEXICAL_WITHOUT_SCOPE:
        # 좁은 대상도 없고 어휘도 안 겹친다. 남은 근거는 '같은 기관' 뿐이다.
        return False, REJECT_GENERIC_SCOPE
    return True, ""
