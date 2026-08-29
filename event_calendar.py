"""앞으로 30일 달력 — **원문 문장에서 확인된 일정만** 칸에 세운다.

왜 다시 만드나
--------------
주간 판세의 '예정' 코너(`weekly_sections.upcoming`)는 2026-08-22 에 껐다
(`SHOW_WEEKLY_UPCOMING`). 끈 이유는 날짜가 틀려서가 아니다 — 추출기는 원문에
적힌 날짜를 정확히 가져왔다. 틀린 것은 **그 날짜와 함께 나간 라벨**이었다.

향후 30일 건을 전수 추적한 실측(2026-08-29):

    화면            원문 절                                  어긋난 지점
    9/1  한빛…설명회  "25일 …설명회를 시작으로 9월 1일 토론회"   9/1 은 토론회,
                                                              라벨은 8/25 설명회
    9/10 영덕…10월    "8월 27일부터 9월 10일까지 공모"           기간의 끝을 점으로
    9/20 포항 집회    "8월 23일부터 9월 20일까지 집회"           같은 사건이 W34
                                                              저장본에선 8/23
    9/1  ×5 건       "9월 중", "9월부터"                        월 정밀도를 1일로

원인은 넷이다.
  ① 날짜는 절에서, 라벨은 기사 제목에서 따로 왔다 — 둘의 짝이 안 맞는다.
  ② "A부터 B까지"를 점 하나로 접었다.
  ③ 월 정밀도를 그 달 1일에 못박았다.
  ④ 주제 이탈(지방교육재정교부금 등)이 원자력 자리를 먹었다.

그래서 이 모듈은 **날짜와 라벨을 같은 절에서 함께 뽑는다.** 라벨은 기사 제목이
아니라 그 날짜가 적힌 문장에서 나오고, 화면은 그 절을 원문 그대로 곁들인다.
칩의 짧은 이름은 길잡이이고, 근거는 언제나 그 문장이다.

무엇을 하지 않는가
------------------
* LLM 에게 다시 묻지 않는다. 날짜도 라벨도 원문의 부분 문자열이다.
* 월·연 정밀도를 날짜 칸에 넣지 않는다 — 그 달 아래 스트립으로 따로 낸다.
* 근거가 없으면 버린다. 칸을 채우려고 추정하지 않는다.

재료가 얼마나 되나 (2026-08-29 실측, 빌드가 넘기는 news_items 3,358건 기준)
--------------------------------------------------------------------------
앞으로 30일에 일정 13건이 9칸에 서고 22칸이 빈다. 한 칸 최대 3건.

**큐레이션의 event_date 는 거의 안 온다.** 아카이브에는 427건이 있지만 웹
빌드의 무결성 게이트(`build_data.apply_archive_integrity_gate`)가 원문에서
확인 못 한 361건을 지우고 65건만 남긴다 — 그중 월 정밀도는 3건이다. 그래서
'이 달 중' 스트립은 대개 비어 있고, 칸에 서는 것은 사실상 전부 **원문 문장에서
직접 뽑은 일정**이다. 이 모듈이 선언 경로 하나에 기대지 않는 이유가 그것이다.

즉 이 달력의 설계 문제는 "칸에 어떻게 욱여
넣나"가 아니라 **"드문드문한 달력을 어떻게 의도한 모양으로 보이게 하나"** 다.
칸에 상한을 두고 나머지를 "+N"으로 접는 이유가 그것이다 — 이 모듈은 창 안의
일정을 **전부** 싣고, 몇 개까지 보일지는 칸 크기를 아는 화면이 정한다
(app.js 의 CAL_MAX_CHIPS).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

import article_quality_gate

# 달력이 보는 앞날. 한 화면에 드는 길이이자 사용자가 요청한 창이다.
HORIZON_DAYS = 30

# 이 창보다 오래된 기사가 말한 일정은 더 싣지 않는다. 기사는 curated 에 영원히
# 남으므로 상한이 없으면 두 달 전에 한 번 언급된 일정이 취소된 뒤에도 칸을
# 지키고 앉아 있다. 취소·연기는 대개 후속 기사로 오고, 그 기사가 이 창 안에
# 있으면 같은 사건으로 접히며 최신 날짜가 이긴다.
LOOKBACK_DAYS = 60

# 절을 가르는 자리. weekly_sections 와 같은 어법이다 — 날짜와 일정 표지가
# **같은 절** 안에 있을 때만 일정으로 인정한다.
_CLAUSE_RE = re.compile(r"[.。!?\n]+|(?<=다)\s+(?=[가-힣A-Z])")

# 앞으로 잡힌 일을 말하는 표지. 날짜가 적혀 있다고 다 일정은 아니다 —
# "2030년 목표"는 포부이고 "8월 15일 발생"은 과거다.
_MARKER_RE = re.compile(
    r"예정|예고|계획이다|열린다|열릴|개최|주최|개막|착공|준공|시행|발효|"
    r"마감|접수|공모|모집|공청회|설명회|토론회|간담회|세미나|포럼|"
    r"공람|열람|표결|의결|심의|제출|출범|방문|시작한다|시작할|착수할|"
    r"scheduled|will be held|will take place|takes place|due on|deadline")

# 기한을 말하는 표지. 이것이 걸리면 그 날짜는 '그날 무엇을 한다'가 아니라
# '그날까지'다 — 칩이 ◇ 로 서고 이름 끝에 '마감'이 붙는다.
_DEADLINE_RE = re.compile(r"마감|까지|접수|신청|제출 기한|기한|deadline|due")

# 기간을 말하는 자리. "A부터 B까지" 처럼 두 날짜가 한 절에 있으면 점이 아니라
# 막대다. 이것이 없으면 두 날짜 중 앞의 것 하나만 쓴다.
_RANGE_RE = re.compile(r"부터[^.]{0,40}?까지|~|∼|–|—|through|until")

# 일정의 이름이 되는 명사. 긴 것이 먼저 걸려야 '준공식'이 '준공'에 먹히지 않는다.
_EVENT_NOUNS = (
    "정기국회", "임시국회", "국정감사", "국무회의", "본회의", "상임위원회",
    "기자회견", "정상회담", "주민투표", "의견수렴", "계획예방정비",
    "입법예고", "행정예고", "전략환경영향평가", "환경영향평가",
    "공청회", "설명회", "토론회", "간담회", "세미나", "심포지엄", "포럼",
    "기공식", "준공식", "착공식", "개소식", "발대식", "출범식",
    "운영허가", "건설허가", "사용승인", "인허가",
    "기자간담회", "실무회의", "이사회", "위원회", "총회", "회의", "회담",
    "재가동", "시운전", "계속운전", "정기검사", "정기보수",
    "공모", "모집", "접수", "입찰", "낙찰", "계약", "체결", "협약",
    "착공", "준공", "개막", "폐막", "개최", "개소", "출범", "방문",
    "시행", "발효", "적용", "마감", "선정", "제출", "발표", "공개", "수렴",
    "표결", "의결", "가결", "부결", "심의", "공람", "열람", "심사",
    "집회", "시위", "행진", "파업", "선고", "공판", "재판", "판결",
    "가동", "정비", "점검", "설문", "투표", "교육", "행사", "축제",
    "출시", "상장", "개통", "발간", "공고", "착수", "종료", "만료",
    "전시회", "박람회", "콘퍼런스", "컨퍼런스", "워크숍", "국제회의",
)
_EVENT_NOUN_RE = re.compile(
    "|".join(sorted((re.escape(noun) for noun in _EVENT_NOUNS),
                    key=len, reverse=True)))

# 라벨을 다듬을 때 떼는 조사. 뒤에서부터 한 번만 뗀다 — "국회에" → "국회".
# 한 글자 조사는 **이·가·도·와·과를 떼지 않는다.** 그 글자로 끝나는 명사가
# 흔해서, 떼면 원문에 없는 낱말이 생긴다 — 실측: '표준설계인가 심사'가
# '표준설계인 심사'가 됐다(인가 = 허가이지 주격 조사가 아니다). 평가·국가·
# 결과·성과도 같은 자리에서 깨진다. 남은 한 글자(을·를·은·는·에·의·로)는
# 아래 최소 어간 길이 규칙과 함께 쓴다.
_PARTICLE_RE = re.compile(
    r"(?:에서는|으로는|에게서|에서의|으로써|이라는|라는|에게|에서|으로|"
    r"까지|부터|이라|라고|과의|와의|의|를|을|은|는|로|에)$")
# 조사를 뗀 뒤 남아야 하는 최소 길이. '마을'에서 '을'을 떼면 '마'가 된다.
_MIN_STEM = 2

# 수식어로 쓸 수 없는 꼴. 용언은 이름이 아니다 — "국회 논의하 세미나"·
# "한국전력공사 따라 임원추천위원회" 가 이 자리에서 났다. 관형사형·연결형은
# 앞말이 잘린 조각이라 라벨에 붙으면 뜻이 사라진다.
_NOT_A_NOUN_RE = re.compile(
    r"(?:하|해|되|된|될|돼|하는|되는|한|위한|통한|대한|관한|따른|따라|통해|"
    r"위해|대해|관해|인해|의해|앞둔|앞두고|있는|없는|"
    r"열린|열릴|같은|많은|첫|또|및|등|이번|지난|오는|올|내|각)$")

# 사건 명사 **뒤에** 올 수 있는 글자. 여기에 없는 한글이 붙어 있으면 그것은
# 더 긴 낱말의 앞부분이지 사건이 아니다 — 실측: "'특별법 시행령' 제정안을
# 입법예고하며"에서 '시행'이 걸려 9/21 칩이 '특별법 시행 마감'이 됐는데,
# 그 날짜는 입법예고 마감일이고 시행일은 12월 17일이었다. 조사와 용언 어미는
# 낱말을 늘리지 않으므로 통과시킨다("공모하며"·"제출될"·"집회를").
_NOUN_TAIL_RE = re.compile(r"^(?:[하해했한할함되된될됐돼키시받중은는이가을를"
                           r"에의과와도로만뿐씩부까]|[^가-힣]|$)")

# 이미 끝난 일을 말하는 꼬리. 사건 명사 뒤에 이것이 붙어 있으면 그 명사는
# 앞날의 일정이 아니다 — 실측: "산업안전보건 강조기간이 **종료되었음에도**
# … 8월 31일까지 연장 운영"에서 8/31 칩의 이름이 '강조기간 종료'가 됐다.
_PAST_TAIL_RE = re.compile(r"^(?:되었|됐|했|하였|였|이었|한\s|된\s)")

# 제목 맨 앞의 주체. 이 저장소의 제목은 대부분 "한수원, …" 꼴이라 첫 쉼표
# 앞이 행위자다. 없으면 주체 없이 행위만 쓴다 — 지어내지 않는다.
_ACTOR_RE = re.compile(r"^\s*([^,，·…]{2,14}?)\s*[,，]")

# 날짜 표기 자체. 라벨을 만들 때 날짜 토큰은 수식어가 될 수 없다.
_DATE_TOKEN_RE = re.compile(r"\d")

# 사건일 종류 중 앞날로 인정하는 것. announcement(발표일)·occurrence(발생일)는
# 지나간 일이라 예정이 아니다.
DECLARED_TYPES = frozenset({"scheduled", "deadline", "effective"})


def _text(article: dict) -> str:
    """일정 확인에 쓰는 원문 — 제목·요약에 더해 큐레이션이 뜬 본문 요지까지.

    날짜는 대개 본문에 있다(실측: event_date 가 있는 444건 중 405건이
    `event_date_source=article_text`). 제목·요약만 보면 실제로 원문에 적힌
    날짜를 '근거 없음'으로 버린다.
    """
    return " ".join(str(article.get(field) or "") for field in
                    ("title_kr", "title", "summary", "detail"))


def _clauses(article: dict) -> list[str]:
    """일정을 찾는 단위. **필드를 먼저 가르고** 그 안에서 절로 나눈다.

    네 필드를 한 문자열로 이어 붙인 뒤 절로 자르면, 마침표로 끝나지 않는 제목이
    본문 첫 문장과 한 덩어리가 된다. 그러면 "8월 27일부터 9월 10일까지"의 두
    날짜가 제목에 있던 다른 날짜와 뒤섞여 앞뒤가 바뀌고, 기간이 기간으로 안
    읽힌다(실측: 영덕 명칭 공모가 기간 대신 점으로 섰다).
    """
    rows: list[str] = []
    for field in ("title_kr", "title", "summary", "detail"):
        for clause in _CLAUSE_RE.split(str(article.get(field) or "")):
            clause = clause.strip()
            if clause:
                rows.append(clause)
    return rows


def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _reference(article: dict) -> date | None:
    """상대 표기('내일')와 연도 없는 날짜를 푸는 기준일 = 그 기사의 보도일."""
    for field in ("article_date", "published_at", "pub", "cached_at", "archived_at"):
        parsed = _as_date(article.get(field))
        if parsed is not None:
            return parsed
    return None


def _strip_particle(token: str) -> str:
    stripped = _PARTICLE_RE.sub("", token).strip()
    return stripped if len(stripped) >= _MIN_STEM else token


# 이름에 남으면 안 되는 것 — 원문에서 잘라 온 따옴표 조각과, 칸이 이미 말하고
# 있는 날짜다. 둘 다 **빼기만** 하므로 남는 낱말은 여전히 원문의 말이다
# (verify 의 '라벨은 원문에 있어야 한다' 검사가 그대로 통과한다).
_LABEL_NOISE_RE = re.compile(r"[\"'‘’“”「」『』（）()\[\]<>·…]+")
_LABEL_DATE_RE = re.compile(
    r"\d{4}\s*년\s*|\d{1,2}\s*월\s*\d{1,2}\s*일(?:까지|부터)?\s*|"
    r"\d{1,2}\s*월(?:까지|부터|중)?\s*|\d{1,2}\s*일(?:까지|부터)?\s*")


def _clean_label(label: str) -> str:
    text = _LABEL_DATE_RE.sub(" ", _LABEL_NOISE_RE.sub(" ", label))
    text = re.sub(r"\s*[,，]\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _actor(article: dict) -> str:
    """제목 맨 앞의 행위자. 없으면 빈 문자열 — 없는 주체를 만들지 않는다."""
    match = _ACTOR_RE.match(str(article.get("title_kr") or article.get("title") or ""))
    return match.group(1).strip() if match else ""


def _whole_noun(clause: str, at: int, noun: str) -> tuple[int, str]:
    """붙어 있는 한글을 앞으로 끌어와 낱말을 온전히 만든다.

    사건 명사표는 낱말 안쪽에도 걸린다 — '기후정의행진'의 '행진', '공공기관
    운영위원회'의 '위원회'. 잘린 조각을 라벨에 쓰면 '기후정 행진'처럼 원문에
    없는 말이 되므로, 앞의 한글 덩어리를 통째로 끌어와 낱말을 복원한다.
    """
    start = at
    while start > 0 and "가" <= clause[start - 1] <= "힣" and at - start < 12:
        start -= 1
    return start, clause[start:at + len(noun)]


def _event_phrase(clause: str, at: int) -> str:
    """절에서 일정의 이름이 될 명사구를 뽑는다.

    날짜에 **글자 수로 가장 가까운** 사건 명사를 고른다 — 한 절에 "설명회를
    시작으로 9월 1일 토론회"처럼 둘 이상이 있을 때, 그 날짜가 가리키는 것은
    붙어 있는 쪽이다(위 머리말 ①번 오류가 정확히 이 자리에서 났다).

    뒤쪽만 보다가 앞쪽으로 떨어지는 방식이었는데 그러면 두 군데서 틀렸다.
      · 뒤에 명사가 없으면 절 **맨 뒤**의 것을 집어 왔다 — "강조기간이
        종료되었음에도 … 8월 31일까지 연장 운영"에서 이름이 '강조기간 종료'.
      · 날짜가 사건 바로 뒤에 붙는 꼴을 놓쳤다 — "임기 만료(9월 19일)에 따라
        임원추천위원회를"에서 9/19 는 만료일이지 위원회 날짜가 아니다.
    거리로 재면 둘 다 맞는다. 같은 거리면 뒤를 택한다(한국어는 대개 날짜가
    사건 앞에 온다).
    """
    matches = [match for match in _EVENT_NOUN_RE.finditer(clause)
               if _NOUN_TAIL_RE.match(clause[match.end():match.end() + 2])
               and not _PAST_TAIL_RE.match(clause[match.end():match.end() + 4])]
    if not matches:
        return ""
    def distance(match):
        after = match.start() >= at
        gap = match.start() - at if after else at - match.end()
        return (max(gap, 0), 0 if after else 1)
    chosen = min(matches, key=distance)
    head_at, noun = _whole_noun(clause, chosen.start(), chosen.group(0))
    # 바로 앞 낱말이 평범한 한글 낱말이면 수식어로 붙인다("국회에 제출" →
    # "국회 제출"). 날짜·숫자 토큰과 용언은 붙이지 않는다.
    head = clause[:head_at].strip()
    modifier = ""
    if head:
        token = _strip_particle(head.split()[-1])
        if (token and 2 <= len(token) <= 8 and not _DATE_TOKEN_RE.search(token)
                and not _NOT_A_NOUN_RE.search(token)
                and token not in noun and noun not in token):
            modifier = token
    return f"{modifier} {noun}".strip()


def _label(article: dict, clause: str, at: int, kind: str) -> str:
    """칸에 서는 짧은 이름 = 주체 + 행위. 둘 다 원문의 부분 문자열이다.

    '마감'은 종류(kind)에서 온다 — 그 날짜가 기간의 끝이라는 사실을 라벨이
    말해야 독자가 '그날 행사'로 읽지 않는다.
    """
    phrase = _event_phrase(clause, at)
    if not phrase:
        return ""
    if kind == "deadline" and not phrase.endswith("마감"):
        phrase = f"{phrase} 마감"
    actor = _actor(article)
    # 주체가 이미 이름 안에 있으면 두 번 쓰지 않는다 — '919 기후정의행진 조직위
    # 기후정의행진' 이 이 자리에서 났다.
    if not actor or _norm(phrase) in _norm(actor):
        return _clean_label(phrase or actor)
    if _norm(actor) in _norm(phrase):
        return _clean_label(phrase)
    return _clean_label(f"{actor} {phrase}")


def _range_pair(clause: str, days: list[date], reference: date,
                ) -> tuple[date, date] | None:
    """절이 "A부터 B까지"로 말하는 구간. 두 날짜 **사이에** 기간 표지가 있어야 한다.

    표지를 절 어디서나 찾으면 "지난 8월 15일 발표한 계획에 따라 9월 3일까지
    제출"이 8/15~9/3 기간이 된다. 실제로 이어진 두 날짜만 짝으로 본다.
    """
    marked = sorted((_date_position(clause, day, end=False), day)
                    for day in days)
    marked = [(at, day) for at, day in marked if at >= 0]
    for (left_at, left), (right_at, right) in zip(marked, marked[1:]):
        between = clause[left_at:right_at + 12]
        if left < right and _RANGE_RE.search(between):
            return left, right
    return None


def _span(clause: str, days: list[date], today: date, horizon: date,
          reference: date) -> tuple[date, date, str] | None:
    """절이 말하는 구간과 종류.

    기간은 시작이 이미 지났어도 끝이 창 안이면 살린다 — 포항 집회
    (8/23~9/20)가 그 경우다. 시작만 보고 버리면 '진행 중인 일정'이 통째로
    사라지고, 끝만 보고 점으로 찍으면 그날 시작하는 행사처럼 읽힌다.
    """
    pair = _range_pair(clause, days, reference)
    if pair:
        start, end = pair
        if end < today or start > horizon:
            return None
        return start, end, "range"
    ahead = [day for day in days if day >= today]
    if not ahead:
        return None
    start = min(ahead)
    if start > horizon:
        return None
    if _DEADLINE_RE.search(clause):
        return start, start, "deadline"
    return start, start, "point"


def _declared_event(article: dict, today: date, horizon: date) -> dict | None:
    """큐레이션이 뜬 일 정밀도 사건일 — **원문에서 되짚어** 확인한 것만.

    본문 표지(`_MARKER_RE`)를 요구하지 않는 유일한 경로다. 'ETF 출시'·'전시회
    공개'처럼 표지 낱말 없이도 앞날인 일이 있고, 그것을 표지표로 다 적으면
    표가 사전이 된다. 대신 큐레이션이 종류를 scheduled/deadline/effective 로
    못박았고 게이트가 그 날짜를 원문에서 확인했다는 두 조건을 함께 요구한다.
    """
    when = _as_date(article.get("event_date"))
    if when is None or not (today <= when <= horizon):
        return None
    kind_declared = str(article.get("event_date_type") or "")
    if kind_declared not in DECLARED_TYPES:
        return None
    if str(article.get("event_date_precision") or "") != "day":
        return None
    reference = _reference(article)
    if reference is None:
        return None
    text = _text(article)
    if article_quality_gate.date_evidence_problem(when, "day", text, reference):
        return None
    clause = _date_clause(_clauses(article), when)
    if not clause:
        # 어느 문장에 적힌 날짜인지 모르면 이름을 뽑을 자리가 없다.
        return None
    kind = "deadline" if kind_declared == "deadline" or _DEADLINE_RE.search(clause) else "point"
    label = _label(article, clause, max(_date_position(clause, when), 0), kind)
    if not label:
        # 사건 명사표에 없는 일도 있다("ETF 출시", "전시회 공개"). 그럴 때는
        # 제목을 이름으로 쓰되, **제목 자체가 그 날짜를 말할 때만** 허용한다.
        # 날짜와 이름이 같은 문장에서 나와야 한다는 이 모듈의 원칙이 그대로
        # 지켜지는 유일한 경우다 — 머리말 ①번 오류가 다시 나지 않는다.
        title = str(article.get("title_kr") or article.get("title") or "")
        if _date_position(title, when) < 0:
            return None
        label = _clean_label(title)
        if not label:
            return None
    return {"date": when.isoformat(), "end_date": when.isoformat(),
            "kind": kind, "label": label, "clause": clause, "origin": "declared"}


def _date_clause(clauses: list[str], when: date) -> str:
    """그 날짜가 실제로 적힌 절. 없으면 빈 문자열이다."""
    for clause in clauses:
        if _date_position(clause, when) >= 0:
            return clause
    return ""


def _clause_events(article: dict, today: date, horizon: date) -> list[dict]:
    """기사 하나에서 나오는 일정들. 절 단위로 보고, 절마다 하나만 낸다."""
    reference = _reference(article)
    if reference is None:
        return []
    rows: list[dict] = []
    for clause in _clauses(article):
        if not _MARKER_RE.search(clause):
            continue
        days = list(article_quality_gate.explicit_dates(clause, reference))
        if not days:
            continue
        span = _span(clause, days, today, horizon, reference)
        if span is None:
            continue
        start, end, kind = span
        # 이름은 **창 안에서 실제로 일이 벌어지는 날짜** 옆에서 뽑는다. 기간의
        # 시작이 이미 지났으면 독자가 보는 것은 끝이다.
        anchor = start if start >= today else end
        at = max(_date_position(clause, anchor), 0)
        label = _label(article, clause, at, kind)
        if not label:
            continue
        rows.append({"date": start.isoformat(),
                     "end_date": end.isoformat(),
                     "kind": kind, "label": label, "clause": clause,
                     "origin": "clause"})
    return rows


def _date_position(clause: str, when: date, *, end: bool = True) -> int:
    """절 안에서 그 날짜가 적힌 자리. 못 찾으면 -1 이다."""
    for pattern in (rf"{when.month}\s*월\s*{when.day}\s*일",
                    rf"{when.year}[.\-/]\s*{when.month:02d}[.\-/]\s*{when.day:02d}",
                    rf"(?<!\d){when.month}[.\-/]{when.day}(?!\d)"):
        match = re.search(pattern, clause)
        if match:
            return match.end() if end else match.start()
    return -1


def verify(event: dict, article: dict, today: date, horizon: date) -> str:
    """달력을 만들 때 **다시 보는** 검사. 통과하면 빈 문자열.

    저장된 값을 믿지 않는다. 여기서 막는 것은 지난 코너에서 실제로 났던 오류들
    이다 — 날짜만 맞고 라벨이 다른 문장에서 온 경우(①), 기간의 끝을 그날 행사로
    읽은 경우(②), 창 밖 날짜가 끼어든 경우.
    """
    start, end = _as_date(event.get("date")), _as_date(event.get("end_date"))
    if start is None or end is None or start > end:
        return "span_invalid"
    # 점은 창 안에서 시작해야 하고, 기간은 창과 **겹치기만** 하면 된다 —
    # 이미 진행 중인 기간을 시작일만 보고 버리면 화면에서 통째로 사라진다.
    if start > horizon or end < today:
        return "out_of_window"
    if start < today and event.get("kind") != "range":
        return "out_of_window"
    clause = str(event.get("clause") or "")
    # 선언 경로는 큐레이션의 종류 판정이 표지를 대신한다(`_declared_event`).
    if event.get("origin") != "declared" and not _MARKER_RE.search(clause):
        return "no_schedule_marker"
    reference = _reference(article)
    if reference is None:
        return "no_reference_date"
    # ① 날짜가 그 절에 실제로 적혀 있는가.
    written = set(article_quality_gate.explicit_dates(clause, reference))
    if start not in written or (end != start and end not in written):
        return "date_not_in_clause"
    # ② 라벨의 모든 낱말이 그 절이나 제목에 있는가 — 지어낸 이름을 막는다.
    haystack = re.sub(r"\s+", "", clause + str(article.get("title_kr") or "")
                      + str(article.get("title") or ""))
    for word in str(event.get("label") or "").split():
        if word == "마감" and event.get("kind") == "deadline":
            continue  # 종류에서 온 말이다 — 원문에 없어도 지어낸 것이 아니다
        if re.sub(r"\s+", "", word) not in haystack:
            return "label_not_in_source"
    return ""


def _declared_month(article: dict, today: date, horizon: date) -> dict | None:
    """월 정밀도 일정. 날짜 칸에 넣지 않고 '그 달 중' 스트립으로 낸다.

    이것을 그 달 1일에 못박았던 것이 지난 코너의 셋째 오류다(실측: 9/1 한 칸에
    5건이 몰렸고 그중 어느 것도 9월 1일 일정이 아니었다).
    """
    when = _as_date(article.get("event_date"))
    if when is None:
        return None
    month = f"{when.year:04d}-{when.month:02d}"
    # 달력이 보는 창에 걸친 달만. 창이 8/29~9/28 이면 10월 일정은 이 화면이
    # 말할 몫이 아니다 — 넣으면 '앞으로 30일'이라는 제목이 거짓이 된다.
    if month not in _months_in_window(today, horizon):
        return None
    if str(article.get("event_date_type") or "") not in DECLARED_TYPES:
        return None
    if str(article.get("event_date_precision") or "") != "month":
        return None
    reference = _reference(article)
    if reference is None:
        return None
    text = _text(article)
    if article_quality_gate.date_evidence_problem(when, "month", text, reference):
        return None
    clause = _month_clause(_clauses(article), when) or ""
    label = _label(article, clause, 0, "point") if clause else ""
    title = str(article.get("title_kr") or article.get("title") or "")
    # 이름이 '발표'·'계속운전' 한 낱말로 줄면 무엇의 발표인지가 없다. 그럴
    # 때는 제목을 쓴다 — 제목도 그 기사의 말이므로 지어낸 것이 아니다.
    if len(label.split()) < 2:
        label = title
    return {"month": month, "label": label, "clause": clause}


def _months_in_window(today: date, horizon: date) -> set[str]:
    months, cursor = set(), today
    while cursor <= horizon:
        months.add(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor += timedelta(days=1)
    return months


def _month_clause(clauses: list[str], when: date) -> str:
    """'9월 중'·'9월부터'가 실제로 적힌 절. 없으면 빈 문자열이다."""
    needle = re.compile(rf"(?<!\d){when.month}\s*월(?!\s*\d)")
    for clause in clauses:
        if needle.search(clause) and _MARKER_RE.search(clause):
            return clause
    return ""


def _norm(value: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", str(value or "").lower())


def _event_id(row: dict) -> str:
    seed = f"{row['date']}|{_norm(row['label'])}"
    return "ev-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


# 어느 기사에나 있는 말. 같은 사건인지 가리는 데 쓰면 전부 같은 사건이 된다.
_STOP_TOKENS = frozenset({
    "원전", "원자력", "한수원", "정부", "국회", "사업", "추진", "개최", "위한",
    "관련", "발표", "이번", "지난", "계획", "확대", "지원", "신규", "전력",
})


def _title_tokens(row: dict) -> set[str]:
    text = re.sub(r"[^가-힣A-Za-z0-9]", " ", str(row.get("title") or ""))
    return {word for word in text.split()
            if len(word) > 1 and word not in _STOP_TOKENS}


def _label_noun(label: str) -> str:
    """이름 안의 사건 명사. 종류에서 붙인 '마감'은 빼고 본다."""
    match = _EVENT_NOUN_RE.search(re.sub(r"\s*마감$", "", label))
    return match.group(0) if match else ""


def _same_event(left: dict, right: dict) -> bool:
    """같은 일정인가.

    같은 날 같은 종류의 사건이라도 기사마다 주체를 달리 쓴다 — '한빛원전
    토론회'와 '한수원 토론회'는 같은 자리다(실측 2026-09-01). 그래서 이름만
    보지 않고 **기사 제목이 같은 것을 말하는지**까지 본다.
    """
    # 겹치기만 하면 같은 자리로 본다 — 한 기사가 선언 경로로 점 하나를,
    # 문장 경로로 그 점을 품은 기간을 함께 낼 수 있다.
    if left["date"] > right["end_date"] or right["date"] > left["end_date"]:
        return False
    if left.get("story_id") and left["story_id"] == right.get("story_id"):
        return True
    if _label_noun(left["label"]) != _label_noun(right["label"]):
        return False
    return len(_title_tokens(left) & _title_tokens(right)) >= 2


def build(articles: list[dict], today: object, *, days: int = HORIZON_DAYS) -> dict:
    """오늘부터 `days` 일까지의 달력 payload.

    매 빌드마다 기사에서 처음부터 다시 유도한다. 상태 파일을 두지 않는 이유는
    창이 하루씩 미끄러지기 때문이다 — 지난 일정은 저절로 빠지고, 취소·연기는
    그 사실을 담은 후속 기사가 같은 사건으로 접히면서 최신 날짜가 이긴다.
    """
    start = _as_date(today)
    if start is None:
        return {"start": "", "end": "", "events": [], "month_notes": [], "dropped": {}}
    horizon = start + timedelta(days=days)
    oldest = start - timedelta(days=LOOKBACK_DAYS)

    found: list[dict] = []
    month_rows: list[dict] = []
    dropped: dict[str, int] = {}
    for article in articles:
        reference = _reference(article)
        if reference is None or reference < oldest:
            continue
        month_note = _declared_month(article, start, horizon)
        if month_note:
            month_rows.append({**month_note, **_source_view(article)})
        declared = _declared_event(article, start, horizon)
        candidates = _clause_events(article, start, horizon)
        if declared:
            candidates.append(declared)
        for event in candidates:
            problem = verify(event, article, start, horizon)
            if problem:
                dropped[problem] = dropped.get(problem, 0) + 1
                continue
            found.append({**event, **_source_view(article),
                          "reference": reference.isoformat()})

    events = _fold(found)
    for row in events:
        row["id"] = _event_id(row)
    events.sort(key=lambda row: (row["date"], row["end_date"], row["label"]))
    return {
        "start": start.isoformat(),
        "end": horizon.isoformat(),
        "days": days,
        "events": events,
        "month_notes": _fold_months(month_rows),
        # 왜 몇 건이 빠졌는지. 화면에는 안 나가고 빌드 로그가 읽는다 —
        # 어느 날 달력이 비면 '못 찾은 것'과 '버린 것'을 구분할 수 있어야 한다.
        "dropped": dropped,
    }


def _source_view(article: dict) -> dict:
    """칩이 근거로 다는 기사 한 건. 원문을 복제하지 않고 가리키기만 한다."""
    return {
        "hash": str(article.get("hash") or ""),
        "story_id": str(article.get("story_id") or ""),
        "issue_id": str(article.get("issue_id") or ""),
        "title": str(article.get("title_kr") or article.get("title") or ""),
        "url": str(article.get("url") or ""),
        "publisher": str(article.get("publisher") or article.get("domain") or ""),
        "topics": list(article.get("topics") or []),
    }


def _fold(rows: list[dict]) -> list[dict]:
    """같은 일정을 하나로. 최신 보도가 이름과 날짜를 정한다.

    포항 집회가 W34 저장본에서 8/23, 다시 계산하면 9/20 이었던 것이 이 자리의
    문제였다 — 같은 사건이 기사마다 다른 날짜로 서면 독자는 어느 쪽도 못 믿는다.
    """
    rows = sorted(rows, key=lambda row: row.get("reference") or "", reverse=True)
    folded: list[dict] = []
    for row in rows:
        for kept in folded:
            if _same_event(kept, row):
                # 한 기사가 제목 절과 본문 절에서 같은 일정을 두 번 낼 수 있다.
                # 그대로 쌓으면 출처 목록에 같은 기사가 두 줄로 서고 '보도 N건'
                # 이 부풀려진다(실측: 영덕 공모 상세에 국민일보가 두 번).
                if all(source["hash"] != row.get("hash") for source in kept["sources"]):
                    kept["sources"].append(_source_view(row))
                kept["first_seen"] = min(kept["first_seen"], row["reference"])
                # 기간이 점을 이긴다. 같은 일을 한쪽은 하루로, 다른 쪽은
                # "9월 2일부터 10월 13일까지"로 말했다면 넓은 쪽이 사실이다.
                if row["kind"] == "range" and kept["kind"] != "range":
                    # 이름도 함께 가져온다. 종류만 바꾸면 기간 막대에 '마감'이
                    # 붙은 이름이 남아 화면이 스스로와 어긋난다.
                    kept.update(date=row["date"], end_date=row["end_date"],
                                kind="range", clause=row["clause"],
                                label=row["label"], origin=row["origin"])
                break
        else:
            folded.append({**row, "sources": [_source_view(row)],
                           "first_seen": row["reference"]})
    for row in folded:
        row["source_count"] = len(row["sources"])
        row.pop("reference", None)
    return folded


def _fold_months(rows: list[dict]) -> list[dict]:
    """'그 달 중' 줄. 같은 달의 같은 일은 하나로 접는다.

    이름만 비교하면 안 접힌다 — 한쪽은 제목으로 떨어지고(한 낱말짜리 이름은
    제목을 쓴다) 다른 쪽은 명사구로 남기 때문이다. 실측: '영덕 신규원전
    건설사업 전략환경영향평가'와 '영덕 신규원전 2기 건설 본궤도…9월 전략환경
    영향평가 초안 제출'이 두 줄로 섰다. 그래서 제목이 같은 것을 말하는지도 본다.
    """
    folded: list[dict] = []
    for row in rows:
        if any(kept["month"] == row["month"]
               and (_norm(kept["label"]) == _norm(row["label"])
                    or len(_title_tokens(kept) & _title_tokens(row)) >= 2)
               for kept in folded):
            continue
        folded.append(row)
    folded.sort(key=lambda row: (row["month"], row["label"]))
    return folded
