"""Publication title presentation helpers."""

from __future__ import annotations

import difflib
import re


# 기관 표기는 화면에서 정규화한다. 수집 시점 라벨이 그대로 굳으면 이름을 바꿔도
# 과거 항목은 옛 이름으로 남아 필터에 같은 기관이 두 개로 갈린다.
# 기관명은 정식 명칭 + 영문 약자로 통일한다. 약자만 아는 사람과 한글 명칭만 아는
# 사람이 갈리는데, 발간물 목록은 원문을 찾아가는 통로라 양쪽 다 필요하다.
# 빌드 시점에 매핑하므로 이미 수집된 항목도 함께 교정된다.
_ORG_ACRONYM_RE = re.compile(r"[A-Z]{2,}")
# 제목 맨 앞의 '…(약자)' 덩어리. 뒤에 공백이나 구두점이 와야 제목 본문이 남는다.
_LEADING_PAREN_RE = re.compile(r"^([^()]{0,40}\(([^()]{1,40})\))\s*[:·\-–—]?\s*")


def strip_org_prefix(title: str, *orgs: str) -> str:
    """제목 맨 앞에 붙은 기관명을 걷어낸다.

    번역이 기관명을 제목 앞에 붙여 놓는다(실측 20건 중 17건) — 입력 형식이
    `[3] (OECD-NEA) Accelerating SMRs` 라 모델이 괄호 안까지 제목으로 읽는다.
    그런데 화면은 바로 위 줄에 이미 기관 바이라인을 세운다. 결과적으로 목록의
    모든 행이 같은 10~22자로 시작해 **정작 다른 부분이 오른쪽으로 밀린다**
    ("대다수가 다는 표시는 신호가 아니다"는 이 저장소의 기존 원칙).

    더 나쁜 것은 표기가 갈리는 경우다 — 제목은 `경제협력개발기구 원자력기구
    (OECD-NEA)`, 바이라인은 `OECD 원자력기구(NEA)` 라서 두 기관처럼 읽힌다.

    판정은 **약자**로 한다. 한국어 표기는 번역마다 흔들리지만 괄호 안 약자는
    안 흔들린다. 앞 덩어리의 괄호 안이 그 기관 약자를 담고 있고 뒤에 남는 말이
    있을 때만 자른다 — 제목이 통째로 기관명뿐이면 그대로 둔다.
    """
    title = str(title or "").strip()
    acronyms = {token for org in orgs
                for token in _ORG_ACRONYM_RE.findall(str(org or ""))}
    if not title or not acronyms:
        return title
    match = _LEADING_PAREN_RE.match(title)
    if not match:
        return title
    inside = match.group(2)
    if not any(acronym in inside for acronym in acronyms):
        return title
    rest = title[match.end():].strip()
    return rest or title


def gist_adds_nothing(gist: str, title_kr: str) -> bool:
    """gist 가 한국어 제목을 되풀이하기만 하면 참.

    v1 프롬프트가 "제목에서 읽어낼 수 있는 범위만"을 너무 곧이곧대로 받아 제목을
    한국어로 다시 쓴 것을 gist 로 냈다(실측: "원자력 안전을 위한 핵심 실험
    데이터세트 보존" → "원자력 안전 핵심 실험 데이터세트 보존"). 같은 말을 두 줄
    쓰면 목록만 길어지고 판단에는 보탬이 없다.

    v2 프롬프트가 문서 성격·범위를 쓰도록 바뀌었지만, 이미 캐시된 v1 gist 는
    다음 번역까지 남는다 — 그동안 화면에서 가린다.
    """
    gist, title_kr = (gist or "").strip(), (title_kr or "").strip()
    if not gist or not title_kr:
        return False
    squeeze = lambda text: "".join(text.split())
    a, b = squeeze(gist), squeeze(title_kr)
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.7
