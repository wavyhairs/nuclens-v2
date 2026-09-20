"""Object(변화의 대상) 추출.

두 층으로 나눈다. 지표는 두 층을 **따로** 낸다 — 결정적 추출기만으로 가설이 서는지,
어휘표를 손으로 늘려야만 서는지가 GO/NO-GO 의 핵심 갈림길이기 때문이다.

v0  결정적. 이미 production 이 신뢰하는 추출기만 쓴다.
    unit     asset_alias.unit_tokens         고리 2호기 · Kori Unit 2 → kori-2
    plant    entity_registry type=plant       verified_evidence.entities(큐레이션 게이트가 확정한 id) + 태그
    project  entity_registry type=project     basic-plan(전기본) · pocheon-psh. team-korea·k-nuclear 는 브랜드라 제외

v0-x 실험용 어휘표. 라이브 스레드 94건에서 실제로 필요했던 kind(deal·policy_doc·legislation·
    program·tender·proceeding)를 손으로 15개쯤 적었다. **레지스트리가 아니다.** 이 표가 있어야만
    수치가 서면 그것은 "레지스트리 큐레이션이 선행 조건"이라는 뜻이고, 그 비용이 결론에 들어간다.

계층: unit ⊂ plant. 호기가 있으면 호기가 Object 이고 발전소는 범위다. 호기 없이 발전소만
있으면 Object 를 **절반만** 안 것이라 grade 가 entity 로 내려간다(resolver 참조).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import asset_alias
import entity_match

# 레지스트리 project 가운데 Object 로 셀 수 있는 것. 팀코리아·K원전은 대상이 아니라 브랜드다.
PROJECT_OBJECTS = frozenset({"basic-plan", "pocheon-psh"})

# 태그 → 발전소 id. 일반명사와 겹치는 발전소(고리·월성·한빛·한울·새울)는 자유문에서 찾지
# 않는다(entity_match 의 tag_or_unit_adjacent 정책과 같다). 태그는 큐레이션이 붙인 것이라 쓴다.
_PLANT_TAG_RE = re.compile(r"^#?([가-힣]+)원전$")

_SPACE_RE = re.compile(r"\s+")


def _compact(text: object) -> str:
    return _SPACE_RE.sub("", str(text or "")).lower()


@lru_cache(maxsize=1)
def _registry_by_id() -> dict[str, dict]:
    return {str(e.get("id")): e for e in entity_match.load_entity_registry()}


@lru_cache(maxsize=1)
def _plant_alias_index() -> dict[str, str]:
    """한글 별칭(공백 제거) → plant id. 태그 '#한빛원전' 의 '한빛' 을 푸는 데 쓴다."""
    out: dict[str, str] = {}
    for entity in _registry_by_id().values():
        if entity.get("type") != "plant":
            continue
        names = set(entity.get("aliases") or ())
        names.add(str(entity.get("name_kr") or "").replace("원전", "").strip())
        for name in names:
            key = _compact(name)
            if len(key) >= 2:
                out.setdefault(key, str(entity["id"]))
    return out


# ---- v0-x 실험용 어휘표 -------------------------------------------------------------
#
# (object_id, kind, 패턴들). 패턴은 공백을 지운 소문자 문자열에 부분일치. 여러 패턴을
# 튜플로 묶은 항목은 **전부** 들어 있어야 한다(AND). 문자열 하나는 그것만 있으면 된다.
LEXICON: tuple[tuple[str, str, tuple[object, ...]], ...] = (
    ("hlw-special-act", "legislation", ("고준위특별법", "고준위방폐물", "고준위방사성폐기물", "고준위법")),
    ("grid-special-act", "legislation", ("전력망특별법", "국가기간전력망", "전력망확충특별법", "기간전력망")),
    ("semicon-special-act", "legislation", ("반도체특별법",)),
    ("basic-plan-12", "policy_doc", ("12차전기본", "12차전력수급", "제12차전력수급", "12차전력수급기본계획")),
    ("industrial-tariff-differential", "policy", ("차등요금", "지역별전기요금", "차등제", "지역별차등")),
    ("khnp-westinghouse-8-units", "deal", (("웨스팅하우스", "8기"), ("한수원", "8기"), "미국내원전8기")),
    ("us-investment-project-1", "deal", ("대미투자1호", ("대미", "지분인수"), ("대미투자", "22억"))),
    ("westinghouse-ipo", "deal", (("웨스팅하우스", "ipo"), ("웨스팅하우스", "기업공개"), ("westinghouse", "ipo"))),
    ("doosan-terrapower-supply", "deal", (("두산", "테라파워"), ("doosan", "terrapower"))),
    ("doe-reactor-pilot", "program", ("원자로파일럿", "reactorpilot", "파일럿프로그램")),
    ("i-smr", "project", ("i-smr", "혁신형smr", "혁신형소형모듈원자로", "혁신형소형모듈")),
    ("holtec-palisades", "project", ("팰리세이즈", "palisades", "펠리세이즈")),
    ("balkhash-npp", "tender", ("발하슈", "balkhash")),
    ("newbuild-site-selection", "proceeding", (("신규원전", "부지"), ("신규원전", "공모"), ("신규원전", "유치"), ("원전", "부지공모"))),
    ("semicon-cluster-power", "policy", (("반도체클러스터", "전력"), ("반도체클러스터", "송전"), ("반도체", "전력공급"))),
    ("nuclear-submarine", "program", ("핵추진잠수함", "핵잠수함", "원자력추진잠수함")),
)

LEXICON_KIND: dict[str, str] = {oid: kind for oid, kind, _ in LEXICON}


def lexicon_objects(text: str) -> set[str]:
    compact = _compact(text)
    found: set[str] = set()
    for object_id, _kind, patterns in LEXICON:
        for pattern in patterns:
            if isinstance(pattern, tuple):
                if all(p in compact for p in pattern):
                    found.add(object_id)
                    break
            elif pattern in compact:
                found.add(object_id)
                break
    return found


# ---- 추출 결과 ---------------------------------------------------------------------

@dataclass(frozen=True)
class ObjectSignals:
    units: frozenset[str] = frozenset()      # kori-2
    plants: frozenset[str] = frozenset()     # kori  (호기의 발전소 포함)
    projects: frozenset[str] = frozenset()   # basic-plan
    lexicon: frozenset[str] = frozenset()    # v0-x

    def key(self, *, use_lexicon: bool) -> tuple[str, frozenset[str]] | None:
        """가장 구체적인 Object. 없으면 None.

        순서가 뜻을 갖는다: 호기 > project > (어휘표) > 발전소. 발전소만 있는 것은
        Object 를 절반만 안 상태라 resolver 가 entity grade 로 다룬다.
        """
        if self.units:
            return ("unit", self.units)
        if self.projects:
            return ("project", self.projects)
        if use_lexicon and self.lexicon:
            return ("lexicon", self.lexicon)
        if self.plants:
            return ("plant", self.plants)
        return None


@lru_cache(maxsize=1)
def _project_aliases() -> tuple[tuple[str, str], ...]:
    out = []
    for entity_id in PROJECT_OBJECTS:
        entity = _registry_by_id().get(entity_id) or {}
        for alias in list(entity.get("aliases") or ()) + [entity.get("name_kr") or ""]:
            key = _compact(alias)
            if len(key) >= 2:
                out.append((key, entity_id))
    return tuple(sorted(out, key=lambda row: -len(row[0])))


def extract(article: dict, *, gate_entities: bool = False) -> ObjectSignals:
    """기사 하나의 Object 신호. 기본은 제목(한·영)·태그만 본다.

    요약은 보지 않는다 — 배경 설명이 다른 대상을 끌고 들어온다(event_stage 가 요약을
    안 보는 것과 같은 이유). 첫 재생에서 실제로 났다: 웨스팅하우스 지분 인수 기사가
    요약에 예시로 적은 두코바니·바라카 때문에 발전소 Object 를 얻었다.

    `gate_entities` 는 큐레이션 게이트가 확정한 `verified_evidence.entities`(제목+요약에서
    뽑은 레지스트리 id)까지 Object 로 쓰는 arm 이다. 커버리지는 오르고 정밀도는 내리는지
    replay 가 따로 잰다.

    어휘표(v0-x)만 요약까지 본다: 딜·법안은 제목에 약칭으로만 나오는 일이 잦아서 그렇게
    하지 않으면 표가 있어도 안 잡힌다. 그 차이도 결과에 적는다.
    """
    title_kr = str(article.get("title_kr") or "")
    title = str(article.get("title") or "")
    tags = [str(t) for t in (article.get("tags") or [])]
    head = " ".join([title_kr, title, " ".join(tags)])
    head_compact = _compact(head)

    units = frozenset(asset_alias.unit_tokens(head))
    plants: set[str] = {u.rsplit("-", 1)[0] for u in units}
    projects: set[str] = set()
    for alias, entity_id in _project_aliases():
        if alias in head_compact:
            projects.add(entity_id)

    registry = _registry_by_id()
    if gate_entities:
        verified = article.get("verified_evidence") or {}
        for entity_id in verified.get("entities") or []:
            entity = registry.get(str(entity_id))
            if not entity:
                continue
            if entity.get("type") == "plant":
                plants.add(str(entity_id))
            if str(entity_id) in PROJECT_OBJECTS:
                projects.add(str(entity_id))
    alias_index = _plant_alias_index()
    for tag in tags:
        match = _PLANT_TAG_RE.match(tag.strip())
        if match:
            plant_id = alias_index.get(_compact(match.group(1)))
            if plant_id:
                plants.add(plant_id)
    # 일반명사와 겹치지 않는 발전소(두코바니·자포리자·팍스…)는 제목 자유문에서도 찾는다.
    for plant_id in asset_alias.plant_tokens(head):
        entity = registry.get(plant_id) or {}
        if entity.get("match_policy") is None:
            plants.add(plant_id)

    lexicon = lexicon_objects(" ".join([head, str(article.get("summary") or "")]))
    if "basic-plan-12" in lexicon:
        # 레지스트리의 basic-plan 과 같은 대상이다. 어휘표를 켜도 두 id 로 갈리지 않게 접는다.
        lexicon = (lexicon - {"basic-plan-12"})
        projects.add("basic-plan")
    return ObjectSignals(
        units=units, plants=frozenset(plants), projects=frozenset(projects),
        lexicon=frozenset(lexicon),
    )


def same_object(left: tuple[str, frozenset[str]], right: tuple[str, frozenset[str]]) -> bool:
    """두 Object 키가 같은 대상을 가리키는가. **kind 가 같고 집합이 겹칠 때만.**

    호기는 여기서 한 번 더 본다: 같은 발전소의 다른 호기는 겹치지 않으므로 False 가
    되고, 그것이 곧 거부권이다. '한빛 3·4호기' 와 '한빛 4호기' 는 겹치므로 True.
    """
    if left[0] != right[0]:
        return False
    return bool(left[1] & right[1])


def unit_conflict(left: ObjectSignals, right: ObjectSignals) -> bool:
    """같은 발전소인데 다른 호기 — 어떤 경로로도 같은 사건이 아니다."""
    if not left.units or not right.units:
        return False
    shared_plants = {u.rsplit("-", 1)[0] for u in left.units} & {u.rsplit("-", 1)[0] for u in right.units}
    for plant in shared_plants:
        l = {u for u in left.units if u.startswith(plant + "-")}
        r = {u for u in right.units if u.startswith(plant + "-")}
        if l and r and not (l & r):
            return True
    return False
