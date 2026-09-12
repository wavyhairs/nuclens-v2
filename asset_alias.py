"""설비를 **호기까지** 같은 이름으로 부른다 — 한글과 영문 양쪽에서.

왜 필요한가
-----------
지금 호기를 읽는 자리는 `web/build_data._UNIT_RE` 하나인데 한국어 전용이다.

    _FACILITY_NAMES = ("신월성", "신한울", "고리", "월성", "한빛", …)   # 25개 전부 한글
    _UNIT_RE = rf"({_FACILITY_PATTERN})\\s*(\\d+)\\s*호기"                # '호기' 를 요구한다

그래서 영문 기사의 "Kori Unit 2" · "Hanbit 3" 은 **검사에 걸리지조차 않는다.**
`_cluster_facility_conflict` 가 "한빛 3호기와 4호기는 어떤 기사를 경유해도 같은
사건이 아니다"를 막고 있는데, 그 거부권이 World Nuclear News 기사에는 서지 않는다.

`entity_registry.json` 도 못 메운다. plant 26건이 **발전소 단위까지**이고
(`kori`, `shin-kori`) 호기가 없다.

무엇을 하지 않는가
------------------
새 레지스트리를 만들지 않는다. 발전소 이름은 `entity_registry` 에서 그대로
가져오고(한글 별칭 + `name_en`), 여기서는 **호기 번호를 붙이는 규칙**만 더한다.
그래서 발전소가 하나 늘면 고칠 자리는 레지스트리 한 곳이다.

산출 토큰
---------
    고리 2호기 · 고리2호기 · Kori Unit 2 · Kori-2 · Kori 2   →  {"kori-2"}
    고리 3·4호기 · Kori Units 3 and 4                        →  {"kori-3", "kori-4"}

이 토큰은 **검색 신호**다. 병합 판정을 여기서 내리지 않는다 — 자동 병합 근거로
쓰기에는 표본이 없다는 것이 `FOLLOW_UP_ENTITY_TYPES` 주석이 이미 내린 결론이다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import entity_match

ROOT = Path(__file__).resolve().parent

# 호기를 여러 개 이어 쓰는 표기. "3·4호기" · "1, 2호기" · "3 and 4"
_NUMBER_RUN = r"\d+(?:\s*(?:[·,~\-]|and|&)\s*\d+)*"
_HANGUL_UNIT = re.compile(rf"^\s*({_NUMBER_RUN})\s*호기")
# 영문은 `re.match` 로 이름 **바로 뒤**를 읽으므로 `\b` 로 시작하면 안 된다 —
# 이름과 공백 사이에는 단어 경계가 없어서 "Kori Unit 2" 가 통째로 안 걸린다.
# 첫 구현에서 실제로 영문 0건이었다.
_LATIN_UNIT = re.compile(
    rf"^[\s\-]*(?:units?|nos?\.?|reactors?)?[\s\-]*({_NUMBER_RUN})(?!\d)",
    re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z\-]*")

# 영문 이름 뒤에 붙는 꾸밈말. 발전소 이름만 남긴다.
_EN_SUFFIX = {"npp", "nuclear", "power", "plant", "station", "point", "unit",
              "units", "energy"}


@lru_cache(maxsize=1)
def _plant_names() -> tuple[tuple[str, str], ...]:
    """(정규화한 표기, 엔티티 id). 긴 이름이 먼저 걸리도록 정렬한다.

    '신고리' 가 '고리' 보다 먼저 매칭돼야 한다 — 아니면 신고리 2호기가
    고리 2호기가 된다.
    """
    out: list[tuple[str, str]] = []
    for entity in entity_match.load_entity_registry():
        if entity.get("type") != "plant":
            continue
        entity_id = str(entity.get("id") or "")
        names = set(entity.get("aliases") or ())
        names.add(str(entity.get("name_kr") or "").replace("원전", "").strip())
        english = str(entity.get("name_en") or "")
        words = [word for word in _LATIN_WORD.findall(english)
                 if word.lower() not in _EN_SUFFIX]
        if words:
            names.add(" ".join(words))
            names.add("".join(words))
        for name in names:
            name = name.strip()
            if len(name) >= 2:
                out.append((name.lower(), entity_id))
    out.sort(key=lambda row: (-len(row[0]), row[0]))
    return tuple(out)


def _numbers(run: str) -> list[str]:
    return _DIGITS.findall(run)


def unit_tokens(text: object) -> set[str]:
    """문장에서 `<발전소id>-<호기>` 토큰을 뽑는다. 한글·영문 모두."""
    lowered = str(text or "").lower()
    if not lowered:
        return set()
    found: set[str] = set()
    # 이미 긴 이름이 먹은 자리는 다시 읽지 않는다. 아니면 "신고리 2호기" 가
    # `shin-kori-2` 와 `kori-2` 를 **둘 다** 내놓는다 — 서로 다른 발전소다.
    consumed: list[tuple[int, int]] = []
    for name, entity_id in _plant_names():
        start = 0
        while True:
            index = lowered.find(name, start)
            if index < 0:
                break
            end = index + len(name)
            start = end
            if any(left <= index < right for left, right in consumed):
                continue
            match = (_HANGUL_UNIT.match(lowered[end:end + 24])
                     or _LATIN_UNIT.match(lowered[end:end + 24]))
            if not match:
                continue
            consumed.append((index, end))
            for number in _numbers(match.group(1)):
                found.add(f"{entity_id}-{int(number)}")
    return found


def plant_tokens(text: object) -> set[str]:
    """호기 없이 발전소만. 호기 토큰이 없을 때의 넓은 신호."""
    lowered = str(text or "").lower()
    return {entity_id for name, entity_id in _plant_names() if name in lowered}


def conflict(left: object, right: object) -> bool:
    """같은 발전소인데 **다른 호기**를 말하는가.

    한빛 3호기와 한빛 4호기는 어떤 기사를 경유해도 같은 사건이 아니다
    (`build_data._cluster_facility_conflict` 가 한국어에서 이미 그렇게 본다).
    한쪽에 호기가 없으면 모순이 아니다 — 없는 것은 반대가 아니다.
    """
    left_units, right_units = unit_tokens(left), unit_tokens(right)
    if not left_units or not right_units:
        return False
    shared_plants = {token.rsplit("-", 1)[0] for token in left_units} & \
                    {token.rsplit("-", 1)[0] for token in right_units}
    if not shared_plants:
        return False
    for plant in shared_plants:
        left_side = {token for token in left_units if token.startswith(f"{plant}-")}
        right_side = {token for token in right_units if token.startswith(f"{plant}-")}
        if left_side and right_side and not (left_side & right_side):
            return True
    return False
