"""Pure publication relevance and exclusion policy."""

from __future__ import annotations

import re


# 발간물 탭은 "보고서로 쓸 만한 문서인가"를 판단하는 자리다. 기관 피드에는 그
# 판단에 쓸 수 없는 종류가 섞여 들어온다 — 실측 29건 중 12건(41%)이었다.
#
# 두 갈래로 나눠 거른다. 갈래를 합치지 않는 이유는 오탐이 났을 때 어느 규칙이
# 잡았는지 로그로 바로 알기 위해서다.
#
#   EVENT   행사·교육·인사 소식. 문서가 아니라 일정이다.
#           (Joshikai 10주년, NextGen 여름학교, TCOFF-2 진행상황 회의…)
#   NONPOWER  IAEA 발간물의 절반은 FAO 공동 프로그램(농업·식품·수자원·축산)이다.
#           원자력 기술을 쓰지만 발전·정책과 접점이 없다.
#           (Plant Breeding / Insect Pest Control / Soils / Food Safety 뉴스레터…)
#
# 제목 규칙이라 완벽하지 않다. 정확한 판정은 pubs_translate 가 번역과 같은 배치
# 호출에서 매기는 `off_topic` 이고(추가 호출 0회), 그 값이 있으면 우선한다.
# 규칙은 이미 수집된 항목을 즉시 교정하는 폴백이다.
PUBLICATION_EVENT_RE = re.compile(
    r"(summer school|winter school|training course|workshop|webinar|symposium"
    r"|joshikai|mentoring|mentorship|stem leaders|diversity|internship|scholarship"
    r"|award|prize|anniversary|celebrat|members meet|meet in \w+ to review"
    r"|kicks? off|welcomes? new|appoint|obituary|in memoriam)",
    re.IGNORECASE,
)
PUBLICATION_NONPOWER_RE = re.compile(
    r"(plant breeding|insect pest|soils newsletter|animal production"
    r"|food safety|food irradiation|crop |livestock|fertili[sz]er"
    r"|freshwater|groundwater|nitrate|zoonot|veterinar)",
    re.IGNORECASE,
)


# off_topic 을 통과한 뒤에도 목록이 안 읽히는 두 번째 이유. 실측 2026-08-05 라이브
# 19건 중 12건이 **연구·설계 실무자용 기술문서**다 — 전산유체역학 코드 검증, 붕괴열
# 시뮬레이션 검증 데이터, 흑연 조사 크리프, 계측제어 요구사항 공학, 외부 선량 측정량.
# 원자력과 무관한 게 아니라서 off_topic 으로 지울 수 없고, 지워서도 안 된다.
# 대신 화면에서 접는다(relevance=technical).
#
# 정확한 판정은 pubs_translate v3 가 번역과 같은 배치에서 매기는 `relevance` 이고
# (추가 호출 0회), 그 값이 있으면 규칙을 아예 태우지 않는다 — off_topic 과 같은
# 3분기 계약(값 있음 / 값 없음). 규칙은 v2 캐시가 남아 있는 동안의 폴백이다.
PUBLICATION_TECHNICAL_RE = re.compile(
    r"(전산유체역학|다상유체|열수력|붕괴열|핵종 재고|크리프|중성자 조사"
    r"|계측제어|요구사항 공학|선량|시뮬레이션 검증|벤치마크|해석 코드|코드 검증"
    r"|임계 안전|모델링 프레임워크|실험 데이터|방사선단위|방사선 노출"
    r"|thermal.?hydraulic|computational fluid|multiphase|decay heat|creep"
    r"|dosimet|neutronic|benchmark|code validation|instrumentation and control"
    r"|requirements engineering|criticality safety)",
    re.IGNORECASE,
)
PUBLICATION_RELEVANCE_VALUES = ("policy", "market", "technical")


def publication_relevance(item: dict) -> str:
    """정책 담당자 기준 쓰임. 'technical' 만 화면에서 접힌다."""
    verdict = str(item.get("relevance") or "").strip().lower()
    if verdict in PUBLICATION_RELEVANCE_VALUES:
        return verdict
    # 판정이 없는 항목(v2 캐시·번역 실패·한국어 원문)만 제목 규칙으로 본다.
    haystack = f"{item.get('title_kr') or ''} {item.get('title') or ''}"
    if PUBLICATION_TECHNICAL_RE.search(haystack):
        return "technical"
    # 애매하면 접지 않는다 — 잘못 접는 쪽이 해롭다.
    return "policy"


def publication_drop_reason(item: dict) -> str:
    """제외 사유. 빈 문자열이면 표시한다."""
    verdict = item.get("off_topic")
    if verdict is True:
        return str(item.get("off_topic_reason") or "off_topic")
    if verdict is False:
        # LLM 이 "관련 있음"으로 봤다면 제목 규칙으로 뒤집지 않는다. 규칙은
        # 제목 낱말만 보므로 'Workshop on Regulatory Harmonisation' 같은 것을
        # 잘못 잡는다 — 판정이 있는 항목에서는 규칙을 아예 태우지 않는다.
        return ""
    # 판정이 없는 항목(v1 캐시·번역 실패·한국어 원문)만 제목 규칙으로 거른다.
    title = str(item.get("title") or "")
    if PUBLICATION_EVENT_RE.search(title):
        return "event"
    if PUBLICATION_NONPOWER_RE.search(title):
        return "nonpower"
    return ""
