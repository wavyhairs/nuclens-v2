"""story fingerprint(사건 지문)의 축 정의와 두 지문의 대조.

지문은 `dedup.ARTICLE_STORY_PROMPT` 가 story 묶음마다 받아 오는 자유형 LLM
필드다. 계약은 프롬프트가 정한다:

    {"countries": [...], "actors": [...], "assets": [...],
     "event_family": "...", "drivers": [...], "event_date": "..."}

읽는 자리가 둘이다 — 텔레그램 쪽 `issue_continuity.same_issue` 와 웹 쪽
`web/build_data.issue_similarity`. 둘이 각자 별칭표를 들고 있었고 **어긋났다**:
web 쪽 표는 원인 축 별칭을 `("cause", "driver")` 로 적어, 프롬프트가 실제로
쓰는 복수형 `drivers` 를 한 번도 읽지 못했다.

그 한 글자가 어떻게 나타났는지 (2026-08-19 라이브 빌드 실측):

    지문만으로 붙은 쌍 11건 중 10건이 오병합.
    10건 전부 `drivers` 가 완전히 어긋나 있었고(예: `trade negotiation`·
    `investment` ↔ `AI`·`semiconductor`·`power_demand`), 하나뿐인 정상 병합만
    `drivers` 를 공유했다(`investment`).

읽지 못한 축은 `compared` 에서 빠지고, 유사도는 **비교한 축에 대해서만** 평균
낸다. 그래서 남은 것이 `countries`·`actors`·`event_family` 셋뿐이면 —
'한국 · 산업부 · policy_decision' — 유사도가 1.0 이 됐다. 구체적인 축이
**없을수록 점수가 높아지는** 상태였다.

그래서 이 파일이 있다. 축 표는 한 곳에만 있고, 대조 결과는 겹친 축뿐 아니라
**어긋난 축(contested)** 도 함께 돌려준다 — 어긋남은 희석이 아니라 증거다.
"""

from __future__ import annotations

import re
from typing import NamedTuple

_SPACE_RE = re.compile(r"\s+")

# 축 이름 → (지문에서 찾을 키들, 가중치).
#
# 키를 여러 개 두는 것은 LLM 이 같은 뜻을 다른 이름으로 쓰기 때문이다. 프롬프트가
# 정한 이름을 **반드시 첫 번째로** 둔다 — 별칭만 적고 본명을 빠뜨린 것이 위
# `drivers` 사고였다.
AXES: dict[str, tuple[tuple[str, ...], float]] = {
    "countries": (("countries", "country"), 1.0),
    "actors": (("actors", "actor", "operator", "organization"), 1.4),
    "assets": (("assets", "asset", "facility", "project", "plant"), 1.8),
    "event": (("event_family", "event_type", "event"), 1.5),
    "action": (("action", "decision", "stage"), 1.3),
    "cause": (("drivers", "driver", "cause"), 0.8),
}

# 축의 두 역할. 이 구분이 지문 판정의 전부다.
#
# 범위(scope) — 닫힌 어휘다. 같은 값을 공유해도 '같은 사건'의 근거가 못 된다.
#   실측 71건: `event_family` 는 값이 15종뿐이고 `policy_decision` 하나가 45%,
#   `countries` 는 `south korea` 48% · `usa` 45%.
# 신원(identity) — 구체적인 당사자·대상·원인·행위.
#   실측 71건: `actors` 77종 · `assets` 59종 · `drivers` 84종.
#
# 기관명이 `actors` 에 들어오면 그것도 범위에 가깝다(`DOE` 8.5% · `government`
# 7%). 그래서 신원 축은 **둘 이상**을 요구한다 — 하나로는 못 가른다는 것이
# build_data.FOLLOW_UP_ENTITY_TYPES 주석의 실측(기관 포함 시 40건 중 3건)과
# issue_continuity.same_issue 의 `anchor_min_shared=2` 가 이미 말하고 있다.
SCOPE_AXES: tuple[str, ...] = ("countries", "event")
IDENTITY_AXES: tuple[str, ...] = ("actors", "assets", "action", "cause")

# `event_date` 는 축이 **아니다** — 그리고 그 상태로 믿을 수 없다.
#
# 프롬프트 계약(`dedup.ARTICLE_STORY_PROMPT`)은 지문에 `event_date` 를 같이 받아
# 오지만 `AXES` 에 없으므로 `compare()` 가 한 번도 읽지 않는다. 그래서 이 칸은
# **무결성 게이트를 지나지 않는다** — 최상위 `event_date` 는
# `article_quality_gate` 가 선언된 출처(title·description·article_text)와 원문의
# 날짜 표기를 대조해 검증 못 한 값을 비우지만(실측: 아카이브 427건 중 361건을
# 비우고 65건만 남는다), 지문 안의 값에는 그 경로가 없다.
#
# 실측 2026-09-12, delivery_log 801건:
#
#     지문에 event_date 가 채워진 것        124건 (15%)
#     발송연도 − 사건연도 = 0               70건
#     발송연도 − 사건연도 = 2               33건 (26.6%)  ← LLM 이 2026 을 2024 로
#     그 밖(−4 ~ +1)                        21건
#
#     2026-08-16 발송  event_date=2024-08-14  테라파워-한국 기업 SMR 협력
#     2026-08-21 발송  event_date=2024-08-20  국회, 전력망 특별법 등 70건 처리
#
# 지금은 읽는 곳이 없어서 무해하다. 타임라인을 이 칸 위에 세우는 순간 해롭다.
# 그래서 **원시 칸을 직접 읽지 말고 아래 접근자를 쓸 것.** 연도가 기사일과
# 어긋나면 빈 문자열을 돌려준다 — 위 33건이 전부 여기서 걸린다.
_ISO_DAY_LEN = 10


def reported_event_date(fingerprint: object, article: object) -> str:
    """지문이 말한 사건일. **기사일과 연도가 맞을 때만** 값이 나온다.

    사건일은 기사일보다 앞설 수 있고(뒤늦게 알려진 결정) 뒤일 수도 있다(예고된
    일정). 그래서 날짜 차이로는 못 가른다. 가르는 것은 연도다 — 같은 해나 바로
    앞뒤 해까지는 정상 보도 범위이고, 두 해 이상 어긋난 값은 실측에서 전부
    LLM 의 연도 오기였다.

    이 함수는 검증이 아니라 **거름망**이다. 통과한 값도 원문에서 확인된 것은
    아니므로, 화면에 세울 때는 최상위 `event_date`(게이트를 지난 값)를 먼저 보고
    이 값은 보조로만 쓸 것.
    """
    if not isinstance(fingerprint, dict) or not isinstance(article, dict):
        return ""
    raw = _SPACE_RE.sub(" ", str(fingerprint.get("event_date") or "").strip())
    if len(raw) < 4 or not raw[:4].isdigit():
        return ""
    reference = str(
        article.get("article_date") or article.get("date")
        or article.get("briefing_date") or ""
    )[:_ISO_DAY_LEN]
    if len(reference) < 4 or not reference[:4].isdigit():
        return ""
    if abs(int(reference[:4]) - int(raw[:4])) > 1:
        return ""
    return raw


class Comparison(NamedTuple):
    """두 지문의 대조 결과.

    similarity  — 비교한 축의 가중 일치율. 비교 못 한 축은 분모에서 빠진다.
    compared    — 양쪽 다 값이 있어 실제로 비교한 축 수.
    shared      — 값이 하나라도 겹친 축.
    contested   — 비교했는데 **하나도** 겹치지 않은 축. 다른 사건이라는 증거다.
    """

    similarity: float
    compared: int
    shared: list[str]
    contested: list[str]


def tokens(value: object) -> set[str]:
    """지문 한 칸을 비교 가능한 낱말 집합으로. 문자열 한 개도 받는다."""
    if isinstance(value, (list, tuple, set)):
        values: list = list(value)
    elif value in (None, ""):
        values = []
    else:
        values = [value]
    out: set[str] = set()
    for item in values:
        text = _SPACE_RE.sub(" ", str(item or "").strip().lower())
        if text:
            out.add(text)
    return out


def axis_values(fingerprint: dict, axis: str) -> set[str]:
    """한 축이 지문에서 말하는 값 전부(별칭 키를 합쳐서)."""
    keys, _weight = AXES[axis]
    out: set[str] = set()
    for key in keys:
        out |= tokens(fingerprint.get(key))
    return out


def compare(left: object, right: object) -> Comparison:
    """두 지문을 축별로 대조한다. 지문이 없으면 빈 결과."""
    if not isinstance(left, dict) or not isinstance(right, dict) or not left or not right:
        return Comparison(0.0, 0, [], [])
    hit = total = 0.0
    compared = 0
    shared: list[str] = []
    contested: list[str] = []
    for axis, (_keys, weight) in AXES.items():
        left_values = axis_values(left, axis)
        right_values = axis_values(right, axis)
        if not left_values or not right_values:
            continue
        compared += 1
        total += weight
        if left_values & right_values:
            shared.append(axis)
            hit += weight
        else:
            contested.append(axis)
    return Comparison(
        hit / total if total else 0.0,
        compared,
        shared,
        contested,
    )
