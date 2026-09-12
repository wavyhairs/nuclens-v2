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

세 번째 오류: 날짜를 **아예 못 읽던** 자리 (2026-09-12)
-------------------------------------------------------
위 넷은 전부 "날짜는 맞는데 그 옆이 틀렸다" 였다. 그 뒤에 다른 종류가 나왔다.

    9/18 제12차 전기본 제7차 정책토론회   본문이 "오는 18일" 이라고만 썼다
    WSCE 2026 (9.9~9.11)                "9월 9일부터 11일까지" 의 11일을 잃었다

둘 다 `article_quality_gate` 의 날짜 추출기에서 죽었다. 그 추출기는 **검증용**
이라 월이 붙지 않은 날을 일부러 안 읽는다 — 카드 게이트가 "이 날짜가 원문에
적혀 있는가"를 물을 때 쓰므로 느슨해지면 무결성 판정 전체가 함께 느슨해진다.
맞는 설계인데, 달력이 그것을 **발견용으로도** 쓴 것이 문제였다.

그리고 둘 다 `dropped` 에 한 줄도 안 남겼다. 버린 것이 아니라 못 본 것이라
셀 자리가 없었다. 그래서 지금은 셋을 나눠 센다.

    dropped   근거가 모자라 세우지 않기로 한 것 — 판단이다
    missed    판단할 기회조차 없었던 것 — 파서가 못 따라간 자리다
    basis     선 일정을 **어떻게 읽었는가** — 추론분이 늘면 여기서 보인다

날짜를 읽는 세 등급
-------------------
`article_quality_gate.scheduled_dates` 가 날짜마다 등급을 함께 돌려준다.
등급이 다르면 쓰는 자리도 다르다.

    explicit   "9월 18일"·"2026-09-18"     원문에 그대로 적혔다
    syntactic  "9월 9일부터 11일까지"의 11일 앞 날짜의 월을 잇는 **범위 표기**다.
                                          추론이 아니라 한국어 구문이라 가드가 없다.
    inferred   "오는 18일"                 보도일 기준으로 풀었다. 게이트를 지난
                                          것만이고, 화면이 그 사실을 밝힌다.

추론 게이트는 **토큰 앞뒤 문맥**으로 판정한다. 절 단위로 미래·과거를 가르면
한 절이 둘을 함께 담을 때 반드시 한쪽을 틀린다 — 실측 2026-08-09: "… 12일
예정된 일식으로 … FT가 8일(현지시간) 보도했습니다" 에서 12일은 행사일이고
8일은 보도일이다.

네 번째 재료와 화면 게이트
--------------------------
기사 창(60일)보다 먼 앞날을 예고한 일정은 그 기사가 창 밖으로 나가면 재료가
사라진다. `event_ledger` 가 그 사이를 잇고, 이 모듈은 원장의 행도 `verify_
remembered` 로 다시 잰다(근거 문장을 함께 저장해 두므로 다시 읽을 수 있다).

그리고 기사 경로에도 주제 판정을 둔다(`verify_reported`). 공식 경로에는 처음
부터 있었고 기사 경로에만 없었다 — 실측 2026-09-10: 달력 기사 경로 48건 중
47건이 주제 판정을 한 번도 받지 않은 채 칸에 서 있었고, 그중에는 'Qnity 신임
CFO 선임'·'CME GPU 선물 출시'·'전국 자전거 동호인 친환경 레이스'가 있었다.
날짜를 읽는 폭을 넓히면서 이 자리를 비워 두면 넓힌 만큼 그대로 잡음이 된다.

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

두 번째 재료: 공식 일정원 (2026-08-29)
--------------------------------------
위의 모든 이야기는 **기사에서 유도한** 일정에 대한 것이다. 그 경로에는 구조적인
구멍이 하나 있다 — 보도되지 않은 일정은 존재하지 않는다. 학회 심포지엄·의원실
정책토론회·협회 행사는 열리고 나서야 기사가 되거나, 끝내 기사가 되지 않는다.

그래서 `event_sources` 가 기관 게시판·국회 일정 API 를 직접 걷어
`event_schedule.json` 에 쌓고, 이 모듈이 그것을 **같은 달력에 함께** 세운다.
두 재료는 성질이 다르다.

    기사 경로    날짜와 이름을 문장에서 캐낸다. 근거 = 그 문장.
    공식 경로    날짜·이름·주최·장소가 칸으로 온다. 근거 = 그 공지.

같은 일정이 양쪽에 있으면 **공식이 이긴다.** 기관이 제 행사에 대해 적은 날짜가
그 행사를 전한 기사보다 정확하기 때문이다(실측: '제226회 원자력계 조찬강연회'가
협회 일정표와 협회 공지 양쪽에 9/4 로 있다). 진 쪽은 사라지지 않고 그 일정의
근거 목록에 **함께 실린다** — 보도 몇 건이 이 일정을 다뤘는지가 값이다.

공식이라고 무조건 세우지는 않는다. 모든 공식 행은 수집 때 한 번,
그리고 **여기서 다시 한 번** `event_relevance` 의 관심 분야·중요도 판정을
지난다(`verify_official`). 저장본을 믿지 않는 것은 기사 경로의 `verify` 와
같은 원칙이고, 판정 기준을 고치면 다시 걷지 않아도 다음 빌드에서 반영된다.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

import article_quality_gate
import event_relevance

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
    # 관형형을 함께 본다 — "부산 벡스코에서 **열리는** 박람회" 는 한국어에서
    # 앞날을 말하는 가장 흔한 꼴 중 하나인데 종결형(열린다·열릴)만 적혀 있어
    # 절이 통째로 후보에서 빠졌다(실측 2026-09-09: 한화큐셀 기사 두 건이
    # 9/16~18 기후산업국제박람회를 정확히 적었는데 후보가 0건이었다).
    r"예정|예고|계획이다|열린다|열릴|열리는|열리며|개최|개최되는|개최될|"
    r"주최|개막|착공|준공|시행|발효|진행되는|"
    r"마감|접수|공모|모집|공청회|설명회|토론회|간담회|세미나|포럼|"
    # 행사 이름 자체가 표지인 것들. 사건 명사표에는 이미 있는데 여기 없어서
    # 절이 후보로도 안 올라왔다.
    r"박람회|전시회|학술대회|심포지엄|심포지움|컨퍼런스|콘퍼런스|워크숍|"
    r"공람|열람|표결|의결|심의|제출|출범|방문|시작한다|시작할|착수할|"
    r"scheduled|will be held|will take place|takes place|due on|deadline")

# 기한을 말하는 표지. 이것이 걸리면 그 날짜는 '그날 무엇을 한다'가 아니라
# '그날까지'다 — 칩이 ◇ 로 서고 이름 끝에 '마감'이 붙는다.
_DEADLINE_RE = re.compile(r"마감|까지|접수|신청|제출 기한|기한|deadline|due")

# 그 일정이 없어졌다는 말. 날짜는 여전히 절에 적혀 있으므로 후보는 만들어지고,
# 그대로 두면 취소된 토론회가 칸에 남는다 — 그래서 후보에 표를 달아 두고 접은
# 뒤에 내린다. 지우지 않고 표를 다는 이유는 같은 일정을 말한 다른 기사가
# 있을 때 **그쪽까지 함께** 내려야 하기 때문이다.
# **서술어 꼴만** 본다. 낱말만 찾으면 취소의 대상이 그 행사가 아닌 데도 걸린다 —
# 실측 2026-08-19: "919 기후정의행진 조직위가 9월 19일 대규모 집회를 예고하며
# 신규 원전 건설 중단과 노후 원전 수명연장 **철회를 촉구했다**" 에서 철회는 그
# 집회의 요구사항이지 집회가 취소됐다는 말이 아니다. 그런데 그 한 낱말 때문에
# 실제로 열린 집회가 달력에서 조용히 사라졌다.
#
# 행사의 취소는 언제나 그 행사를 주어로 한 서술어로 온다("토론회가 취소됐다").
# 목적어로 오는 취소("철회를 촉구")는 남의 이야기다.
_CANCEL_RE = re.compile(
    # '연기' 는 여기 넣지 않는다 — 연기는 없어진 것이 아니라 **옮겨진** 것이고,
    # 그 자리는 `_POSTPONE_RE` 가 맡는다. 여기 넣으면 날짜가 밀린 일정이
    # 화면에서 통째로 사라진다.
    r"(?:취소|무산|철회|백지화)\s*(?:됐|되었|된다|되며|하기로|한다고)|"
    r"잠정\s*중단|열리지\s*않|개최하지\s*않")

# 날짜가 옮겨졌다는 말. 이 표지가 있으면 한 절의 두 날짜 중 **뒤엣것**이 새
# 날짜다 — "9월 18일 토론회가 9월 25일로 연기됐다" 에서 앞의 날짜는 이제
# 일어나지 않는 날이다. 표지가 없을 때의 규칙(앞엣것)을 그대로 쓰면 취소된
# 날짜가 칸에 선다.
_POSTPONE_RE = re.compile(r"연기|미뤄|미루|늦춰|순연|변경")

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
# 용언 어간이 조사 제거로 남는 꼴도 막는다 — "열리는 박람회" 에서 '는' 을 떼면
# '열리' 가 남아 '열리 박람회' 라는 원문에 없는 말이 된다(실측 2026-09-17,
# 관형형을 표지에 들이면서 새로 생긴 자리다).
_NOT_A_NOUN_RE = re.compile(
    r"(?:하|해|되|된|될|돼|하는|되는|한|위한|통한|대한|관한|따른|따라|통해|"
    r"위해|대해|관해|인해|의해|앞둔|앞두고|있는|없는|"
    r"열리|열려|열린|열릴|치러|치르|참가|참여|주최|주관|개최|진행|"
    r"같은|많은|첫|또|및|등|이번|지난|오는|올|내|각)$")

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
        label = _clean_label(phrase or actor)
    elif _norm(actor) in _norm(phrase):
        label = _clean_label(phrase)
    else:
        label = _clean_label(f"{actor} {phrase}")
    # 남은 것이 사건 명사 하나뿐이면('개최'·'시행') 독자는 무엇의 개최인지를
    # 모른다. 그럴 때는 제목을 쓴다 — `_declared_month` 가 같은 자리에서 이미
    # 하고 있는 일이고, 제목도 그 기사의 말이므로 지어낸 이름이 아니다.
    if _label_is_bare(label):
        title = _clean_label(str(article.get("title_kr") or article.get("title") or ""))
        return title or label
    return label


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
    # 연기 공지는 한 절에 옛 날짜와 새 날짜를 함께 담는다. 새 날짜가 뒤에 온다.
    start = max(ahead) if _POSTPONE_RE.search(clause) else min(ahead)
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
            "kind": kind, "label": label, "clause": clause, "origin": "declared",
            # 선언 경로는 게이트가 원문에서 그 날짜를 확인한 것만 온다.
            "date_basis": "explicit"}


def _date_clause(clauses: list[str], when: date) -> str:
    """그 날짜가 실제로 적힌 절. 없으면 빈 문자열이다."""
    for clause in clauses:
        if _date_position(clause, when) >= 0:
            return clause
    return ""


def _clause_events(article: dict, today: date, horizon: date,
                   missed: dict[str, int] | None = None) -> list[dict]:
    """기사 하나에서 나오는 일정들. 절 단위로 보고, 절마다 하나만 낸다.

    `missed` 는 **후보가 아예 안 만들어진** 자리를 센다. 버린 것(`dropped`)과
    못 본 것은 다르다 — 실측으로 두 사례(9/18 정책토론회·WSCE 2026)를 재현했을
    때 `dropped` 가 통째로 비어 있었다. 조용히 사라지는 실패는 고칠 수 없다.
    """
    reference = _reference(article)
    if reference is None:
        return []
    rows: list[dict] = []
    for clause in _clauses(article):
        if not _MARKER_RE.search(clause):
            continue
        found = article_quality_gate.scheduled_dates(clause, reference)
        days = sorted(found)
        if not days:
            if missed is not None and _DAY_HINT_RE.search(clause):
                # 날짜처럼 생긴 것이 있는데 한 건도 못 읽었다. 파서의 구멍이다.
                missed["no_readable_date"] = missed.get("no_readable_date", 0) + 1
            continue
        span = _span(clause, days, today, horizon, reference)
        if span is None:
            if missed is not None:
                missed["no_future_span"] = missed.get("no_future_span", 0) + 1
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
                     "origin": "clause",
                     "cancelled": bool(_CANCEL_RE.search(clause)),
                     "postponed": bool(_POSTPONE_RE.search(clause)),
                     # 이 일정의 날짜를 **어떻게 읽었는가**. 화면이 추론분에
                     # '추정' 을 다는 근거이고, 등급이 낮은 쪽이 이긴다 —
                     # 시작일이 추론이면 그 일정 전체가 추론이다.
                     "date_basis": _basis(found, start, end)})
    return rows


# 날짜처럼 생긴 것. 이 표지가 있는데 한 건도 못 읽으면 파서가 못 따라간 자리다.
_DAY_HINT_RE = re.compile(r"\d\s*일|\d\s*월|\d{4}[.\-/]\d{1,2}")

# 날짜를 읽은 등급. 낮은 쪽이 그 일정의 등급이다.
_BASIS_RANK = {"explicit": 0, "syntactic": 1, "inferred": 2}


def _basis(found: dict, start: date, end: date) -> str:
    """시작과 끝 중 **더 약한** 근거. 둘 다 없으면 explicit 로 둔다."""
    grades = [found.get(day) for day in (start, end) if found.get(day)]
    if not grades:
        return "explicit"
    return max(grades, key=lambda grade: _BASIS_RANK.get(grade, 0))


def _date_position(clause: str, when: date, *, end: bool = True) -> int:
    """절 안에서 그 날짜가 적힌 자리. 못 찾으면 -1 이다.

    월이 붙은 꼴을 먼저 찾고, 없으면 **월 없이 적힌 날**을 찾는다. 뒤엣것이
    없으면 "9월 9일부터 11일까지"의 11일이나 "오는 18일"이 절 어디에 있는지를
    몰라 이름을 뽑을 자리(`_event_phrase`)와 범위 판정(`_range_pair`)이 둘 다
    무너진다 — 날짜는 읽었는데 일정이 안 서는 자리가 그것이었다.
    """
    for pattern in (rf"{when.month}\s*월\s*{when.day}\s*일",
                    rf"{when.month}\s*월\s*{when.day}(?!\s*\d)",
                    rf"{when.year}[.\-/]\s*{when.month:02d}[.\-/]\s*{when.day:02d}",
                    rf"(?<!\d){when.month}[.\-/]{when.day}(?!\d)"):
        match = re.search(pattern, clause)
        if match:
            return match.end() if end else match.start()
    match = re.search(rf"(?<![\d월])\s*{when.day}\s*일", clause)
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
    # ① 날짜가 그 절에 실제로 적혀 있는가 — **발견 때와 같은 추출기로** 다시 읽는다.
    # 저장된 값이 아니라 절을 다시 읽는다는 원칙은 그대로다. 달라진 것은 읽는
    # 폭이지 믿는 대상이 아니다.
    written = article_quality_gate.scheduled_dates(clause, reference)
    if start not in written or (end != start and end not in written):
        return "date_not_in_clause"
    # ② 추론으로 읽은 날은 **미래 표지만으로는 부족하다.** 그 절이 일정의
    # 이름이 될 사건 명사를 실제로 담고 있어야 한다 — "오는 18일" 뒤에
    # 토론회·공청회가 없으면 그것은 일정이 아니라 문장 속의 날짜다.
    if _basis(written, start, end) == "inferred" and not _EVENT_NOUN_RE.search(clause):
        return "inferred_without_event_noun"
    # ③ 라벨의 모든 낱말이 그 절이나 제목에 있는가 — 지어낸 이름을 막는다.
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


# 그 자체로는 **무엇이 일어나는지 말하지 않는** 사건 명사. "개최"는 열린다는
# 뜻이지 무엇이 열리는지가 아니다. 같은 일정을 두 기사가 다르게 부를 때 이쪽을
# 고르면 칸이 아무 말도 안 하게 된다 — 실측 2026-09-13: 제목 절이 '기장군 개최'
# 로, 본문 절이 '기장군 사업 설명회' 로 떨어졌는데 앞엣것이 이겼다.
_GENERIC_NOUNS = frozenset({
    "개최", "시행", "진행", "발표", "공개", "실시", "착수", "시작",
    "방문", "적용", "종료", "선정", "제출", "출시",
})


def _label_is_vague(label: str) -> bool:
    """이름이 '누가 + 한다' 까지만 말하고 **무엇을** 이 빠졌는가."""
    noun = _label_noun(label)
    return bool(noun) and noun in _GENERIC_NOUNS


def _label_is_bare(label: str) -> bool:
    """이름이 사건 명사 하나로 줄었는가 — '개최'·'시행'·'접수 마감'.

    그런 이름은 날짜 옆에 놓여도 아무것도 말하지 않는다. 무엇이 열리는지가
    빠져 있기 때문이다.
    """
    noun = _label_noun(label)
    rest = {token for token in _label_tokens({"label": label})
            if token not in {"마감", _norm(noun)} and _norm(token) != _norm(noun)}
    return not rest


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
    # **같은 것을 가리키는 근거가 먼저다.** 날짜 겹침을 먼저 보면 두 군데서 진다.
    #   · 한 기사가 절 둘에서 같은 일정을 다른 이름으로 낸다(실측 55건 중 7쌍:
    #     '에너지경제연구원 개최' ‖ '에너지경제연구원 정책세미나'). 날짜는 같으니
    #     겹침은 통과하지만 이름 명사가 달라 아래 판정에서 갈라졌다.
    #   · 연기는 **겹치지 않는다.** 9/18 토론회가 9/25 로 밀리면 두 줄이 나란히
    #     선다 — 공식 경로는 게시물 번호로 이미 이 자리를 막고 있다
    #     (`event_sources.merge_events`). 기사 경로의 등가 열쇠가 story_id 다.
    if _same_source(left, right):
        return True
    if left.get("story_id") and left["story_id"] == right.get("story_id"):
        return True
    if left["date"] > right["end_date"] or right["date"] > left["end_date"]:
        return False
    if left.get("origin") == "official" or right.get("origin") == "official":
        return _same_named_event(left, right)
    # 이름의 사건 명사가 같거나, **기사 제목이 같은 것을 말하거나.** 둘을 함께
    # 요구했더니 같은 행사가 갈라졌다(실측 2026-09-17: '전북 새만금 청정에너지
    # 박람회' 를 한 기사는 '개최' 로, 다른 기사는 '박람회' 로 불러 명사가 어긋났는데
    # 제목은 박람회·새만금·전북·청정에너지 를 함께 담고 있었다). 제목이 넷을
    # 겹치는데 이름 명사 하나가 다르다고 두 줄로 세울 이유가 없다.
    if _label_noun(left["label"]) == _label_noun(right["label"]):
        return True
    return len(_title_tokens(left) & _title_tokens(right)) >= 2


def _same_source(left: dict, right: dict) -> bool:
    """같은 기사에서 나온 두 후보인가.

    한 기사가 같은 일정을 두 번 말하는 것은 흔하다 — 제목에서 한 번, 본문에서
    한 번. 그 둘이 달력에 두 줄로 서면 '보도 N건'이 부풀고 칸이 같은 말을
    두 번 한다. 같은 기사가 같은 날 서로 다른 두 일정을 말하는 경우와 가르려고
    **날짜가 겹칠 때만** 같은 것으로 본다.
    """
    if not (left.get("hash") and left.get("hash") == right.get("hash")):
        return False
    return not (left["date"] > right["end_date"] or right["date"] > left["end_date"])


def _same_named_event(left: dict, right: dict) -> bool:
    """공식 일정이 낀 짝짓기. **이름으로** 잰다.

    사건 명사표(`_EVENT_NOUNS`)는 기사 문장에서 사건을 캐내려고 만든 것이라
    행사의 고유명에는 잘 안 듣는다 — 실측(2026-09-04): 협회 일정표의
    '제226회 원자력계 조찬강연회'와 협회 공지의 '제226회 원자력계 조찬강연회
    개최 및 참가등록 안내'가 같은 행사인데 두 줄로 섰다. '강연회'가 표에 없어
    한쪽 명사는 빈 값, 다른 쪽은 제목에 섞인 '개최'가 잡혀 둘이 어긋난 탓이다.

    공식 일정은 이름이 곧 그 행사의 고유명이므로 이름을 직접 견준다.
      · 한쪽 이름이 다른 쪽에 통째로 들어 있으면 같은 행사다
        ('Nuclear Energy Conference & Expo' ⊂ '2026 Nuclear Energy Conference
        & Expo (NECX)', 'ICRS15&RPSD2026' = 'ICRS15-RPSD2026').
      · 아니면 뜻이 있는 낱말 두 개 이상이 겹쳐야 한다.

    기사와 견줄 때는 **이름끼리 재지 않는다.** 기사 경로의 이름은 문장에서
    합성한 짧은 말이라('한국원자력학회 심포지움 개최') 행사의 고유명과 겹치는
    낱말이 사건 명사 하나뿐인 일이 흔하다. 대신 **공식 이름이 그 기사 안에
    실제로 나오는가**를 묻는다 — 위 기사의 제목은 '전원믹스와 시장제도
    심포지움'이라 공식 이름의 낱말을 그대로 담고 있다.
    """
    if left.get("origin") == "official" and right.get("origin") == "official":
        left_name, right_name = _norm(left.get("label")), _norm(right.get("label"))
        if not left_name or not right_name:
            return False
        # 너무 짧은 이름은 우연히 포함된다 — '개최'가 아무 제목에나 들어 있다.
        if len(left_name) >= 6 and len(right_name) >= 6:
            if left_name in right_name or right_name in left_name:
                return True
        return len(_label_tokens(left) & _label_tokens(right)) >= 2
    formal, reported = ((left, right) if left.get("origin") == "official"
                        else (right, left))
    return _article_names_the_event(formal, reported)


def _article_names_the_event(formal: dict, reported: dict) -> bool:
    """그 기사가 이 공식 행사를 **이름으로** 부르고 있는가.

    기사의 제목·근거 문장·합성한 이름을 통째로 haystack 으로 놓고, 공식 이름의
    낱말이 몇 개나 그 안에 있는지 센다.

    문턱이 둘인 이유: 낱말 두 개만으로는 같은 날 열린 남의 행사가 붙는다
    ('AI'·'시대'는 어디에나 있다). 그래서 걸린 낱말 중 **적어도 하나는 길어야**
    한다 — 짧은 낱말만 겹치는 것은 우연이고, '전원믹스와' 같은 긴 낱말이
    겹치는 것은 우연이 아니다.
    """
    haystack = "".join(_norm(reported.get(field)) for field in
                       ("title", "clause", "label"))
    if not haystack:
        return False
    hits = [token for token in _label_tokens(formal)
            if _norm(token) and _norm(token) in haystack]
    return len(hits) >= 2 and any(len(_norm(token)) >= 4 for token in hits)


def _label_tokens(row: dict) -> set[str]:
    """이름 안의 뜻 있는 낱말. 어디에나 있는 말과 한 글자는 빼고 본다."""
    text = re.sub(r"[^가-힣A-Za-z0-9]", " ", str(row.get("label") or ""))
    return {word.lower() for word in text.split()
            if len(word) > 1 and word not in _STOP_TOKENS}


def verify_official(row: dict, today: date, horizon: date) -> str:
    """공식 일정을 달력에 세우기 전에 **다시 보는** 검사. 통과하면 빈 문자열.

    수집기가 이미 판정했는데 왜 또 보나 — 저장본은 파일이고 파일은 낡는다.
    `event_schedule.json` 은 하루 한 번 채워지고 빌드는 세 시간마다 도는데,
    그 사이 관심 분야 표가 바뀌면 낡은 판정이 화면에 남는다. 여기서 다시 재면
    다시 걷지 않아도 다음 빌드에서 바로 반영된다. 기사 경로의 `verify` 가
    저장된 event_date 를 안 믿는 것과 같은 이유다.
    """
    start, end = _as_date(row.get("date")), _as_date(row.get("end_date"))
    if start is None or end is None or start > end:
        return "official_span_invalid"
    # 점은 창 안에서 시작해야 하고, 기간은 창과 겹치기만 하면 된다 — 기사 경로와
    # 같은 규칙이다(이미 시작한 학술대회가 화면에서 통째로 사라지지 않게).
    if start > horizon or end < today:
        return "official_out_of_window"
    if start < today and row.get("kind") != "range":
        return "official_out_of_window"
    label = str(row.get("label") or "").strip()
    if not label:
        return "official_no_label"
    if not str(row.get("url") or "").strip():
        # 근거 URL 이 없으면 독자가 확인할 길이 없다. 이 달력은 확인할 수 없는
        # 일정을 세우지 않는다.
        return "official_no_source_url"
    # 수집기가 판정에 쓴 것과 **같은 입력**으로 다시 잰다. 표시용 주최(host)를
    # 넣으면 두 판정이 달라져, 수집이 막은 것을 여기서 통과시키게 된다.
    verdict = event_relevance.judge(
        row.get("notice_title") or label, row.get("organizer"),
        row.get("place"), label)
    if not verdict["ok"]:
        return f"official_{verdict['reason']}"
    return ""


def verify_reported(row: dict, *, judge=None) -> str:
    """기사에서 유도한 일정을 화면에 세울 것인가. 통과하면 빈 문자열.

    **발견은 넓게, 표시는 좁게.** 위 `verify` 까지가 "이 날짜와 이름이 원문에
    있는가"를 묻는 근거 판정이고, 여기는 "그것이 이 달력이 말할 일인가"를 묻는
    주제 판정이다. 둘을 합치지 않는 이유는 버린 까닭을 가르기 위해서다.

    공식 경로에는 이 자리가 처음부터 있었고(`verify_official`) 기사 경로에만
    없었다. 그래서 실측(2026-09-10) 달력 기사 경로 48건 중 47건이 주제 판정을
    한 번도 받지 않은 채 칸에 서 있었다 — 'Qnity 신임 CFO 선임', 'CME GPU 선물
    출시', '전국 자전거 동호인 친환경 레이스' 가 그렇게 들어왔다. 날짜를 읽는
    폭을 넓히면서 이 자리를 비워 두면 넓힌 만큼 그대로 잡음이 된다.
    """
    verdict = (judge or event_relevance.judge_reported)(
        row.get("title"), row.get("label"), row.get("clause"))
    if not verdict["ok"]:
        return f"reported_{verdict['reason']}"
    # 주제를 판정으로 얻었으면 그 값을 쓴다. 기사의 topics 는 기사 전체에 대한
    # 것이라 그 안의 한 일정과 어긋날 수 있다 — 빈 칸일 때만 채운다.
    if not row.get("topics") and verdict["topics"]:
        row["topics"] = list(verdict["topics"])
    return ""


def _official_row(row: dict) -> dict:
    """저장된 공식 일정 한 줄 → 달력이 쓰는 행 모양.

    기사 경로의 행과 **같은 칸 이름**을 쓴다(date·end_date·kind·label·clause).
    그래야 `_fold` 가 둘을 같은 자리에서 접고, 화면이 한 가지 모양만 그린다.
    근거 문장(clause) 자리에는 기관이 실제로 올린 공지 제목이 들어간다.
    """
    notice = str(row.get("notice_title") or row.get("label") or "")
    return {
        "date": str(row.get("date") or ""),
        "end_date": str(row.get("end_date") or row.get("date") or ""),
        "kind": str(row.get("kind") or "point"),
        "label": str(row.get("label") or ""),
        "clause": notice,
        "origin": "official",
        # 기관이 제 공지에 적은 날짜다 — 읽은 것이지 푼 것이 아니다.
        "date_basis": "explicit",
        # 판정이 읽는 원문. `clause` 와 같은 값이지만 이름을 따로 둔다 —
        # 이 칸이 빠져 있어 `verify_official` 이 짧은 이름만 보고 협회 일정표의
        # 'Roadmaps to New Nuclear 2026' 을 '일정이 아니다'로 버렸다(실측).
        # 구분 칸('(세미나)')은 notice_title 에만 있고 label 에는 없다.
        "notice_title": notice,
        # 공식 경로만 갖는 칸 — 시각·주최·장소는 기사에서 좀처럼 안 나온다.
        "time": str(row.get("time") or ""),
        "host": str(row.get("host") or ""),
        "place": str(row.get("place") or ""),
        "source_id": str(row.get("source_id") or ""),
        "organizer": str(row.get("organizer") or ""),
        # `_source_view` 가 읽는 칸들.
        "hash": str(row.get("id") or ""),
        "story_id": "",
        "issue_id": "",
        "title": notice,
        "url": str(row.get("url") or ""),
        "publisher": str(row.get("publisher") or ""),
        "topics": list(row.get("topics") or []),
        # 최초 확인일. 기사 경로의 보도일과 같은 자리를 쓴다 — `_fold` 가 이
        # 값으로 최신순을 매기고 first_seen 을 낮은 쪽으로 지킨다.
        "reference": str(row.get("first_seen") or ""),
    }


def verify_remembered(row: dict, today: date, horizon: date, *, judge=None) -> str:
    """원장에 적힌 기사 유래 일정을 세우기 전에 **다시 보는** 검사.

    저장본을 믿지 않는다는 원칙은 공식 경로(`verify_official`)와 같다. 다른 점은
    다시 잴 재료가 여기에는 그대로 있다는 것이다 — 원장이 근거 문장(`clause`)과
    최초 보도일을 함께 들고 있으므로, 기사가 이미 창 밖으로 나갔어도 **그 문장을
    다시 읽어** 날짜를 확인할 수 있다. 기억한다는 것은 판정을 건너뛴다는 뜻이
    아니다.
    """
    start, end = _as_date(row.get("date")), _as_date(row.get("end_date"))
    if start is None or end is None or start > end:
        return "remembered_span_invalid"
    if start > horizon or end < today:
        return "remembered_out_of_window"
    if start < today and row.get("kind") != "range":
        return "remembered_out_of_window"
    if not str(row.get("url") or "").strip():
        return "remembered_no_source_url"
    clause = str(row.get("clause") or "")
    reference = _as_date(row.get("first_seen"))
    if not clause or reference is None:
        return "remembered_no_evidence"
    written = article_quality_gate.scheduled_dates(clause, reference)
    if start not in written or (end != start and end not in written):
        return "remembered_date_not_in_clause"
    if written.get(start) == "inferred":
        # 원장은 추론분을 담지 않는다. 저장 뒤에 판정이 바뀌어 등급이 내려갔다면
        # 몇 달을 들고 있던 값을 그대로 세울 이유가 없다.
        return "remembered_not_explicit"
    return verify_reported(row, judge=judge)


def build(articles: list[dict], today: object, *, days: int = HORIZON_DAYS,
          official: list[dict] | None = None,
          remembered: list[dict] | None = None, judge=None) -> dict:
    """오늘부터 `days` 일까지의 달력 payload.

    `judge` 는 기사 경로의 주제 판정을 갈아 끼우는 자리다. 날짜·이름 추출만
    재는 단위 테스트가 주제와 무관한 실측 문장을 쓰기 때문에 열어 둔다 —
    운영에서는 비워 두고 `event_relevance.judge_reported` 가 판정한다.

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
    # 버린 것과 **못 본 것**을 가른다. 앞엣것은 근거가 모자라 세우지 않기로 한
    # 판단이고, 뒤엣것은 판단할 기회조차 없었던 자리다 — 달력이 빌 때 고쳐야
    # 할 곳이 게이트인지 파서인지를 이 둘이 가른다.
    missed: dict[str, int] = {}
    for article in articles:
        reference = _reference(article)
        if reference is None or reference < oldest:
            continue
        month_note = _declared_month(article, start, horizon)
        if month_note:
            month_rows.append({**month_note, **_source_view(article)})
        declared = _declared_event(article, start, horizon)
        candidates = _clause_events(article, start, horizon, missed)
        if declared:
            candidates.append(declared)
        for event in candidates:
            problem = verify(event, article, start, horizon)
            if problem:
                dropped[problem] = dropped.get(problem, 0) + 1
                continue
            row = {**event, **_source_view(article),
                   "reference": reference.isoformat()}
            problem = verify_reported(row, judge=judge)
            if problem:
                dropped[problem] = dropped.get(problem, 0) + 1
                continue
            found.append(row)

    # 공식 일정원. 기사와 **같은 목록**에 넣고 같은 fold 를 태운다 — 화면에
    # 두 종류의 달력이 생기지 않게 하는 것이 이 통합의 요점이다.
    for row in (official or []):
        candidate = _official_row(row)
        problem = verify_official(candidate, start, horizon)
        if problem:
            dropped[problem] = dropped.get(problem, 0) + 1
            continue
        if not candidate["reference"]:
            # 최초 확인일이 없으면 최신순을 매길 수 없다. 오늘 본 것으로 둔다.
            candidate["reference"] = start.isoformat()
        found.append(candidate)

    # 세 번째 재료: 전에 확인해 원장에 담아 둔 기사 유래 일정. 기사가 창 밖으로
    # 나간 뒤에도 그 일정이 살아 있게 하는 자리다(`event_ledger` 머리말).
    for row in (remembered or []):
        problem = verify_remembered(row, start, horizon, judge=judge)
        if problem:
            dropped[problem] = dropped.get(problem, 0) + 1
            continue
        found.append({**row, "date_basis": "explicit",
                      "source_kind": "news",
                      "reference": row.get("reference") or row.get("first_seen")
                      or start.isoformat()})

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
        "missed": missed,
        # 날짜를 어떻게 읽어서 선 일정인지의 분포. 추론분이 갑자기 늘면
        # 게이트가 헐거워진 것이므로 회차 사이 비교로 잡을 수 있다.
        "basis": _basis_counts(events),
    }


def _source_view(article: dict) -> dict:
    """칩이 근거로 다는 한 건. 원문을 복제하지 않고 가리키기만 한다.

    기사와 공식 공지가 한 목록에 섞이므로 `source_kind` 로 둘을 가른다 — 화면이
    '공식'과 '보도'를 다른 무게로 그려야 독자가 근거의 성질을 안다.

    이름이 `kind` 가 아닌 이유: 이 딕셔너리는 `build` 에서 일정 행 위에 그대로
    펼쳐진다(`{**event, **_source_view(article)}`). `kind` 로 두었더니 일정의
    종류(point·deadline·range)를 근거의 종류가 덮어써서 모든 칩이 'news' 가 됐다.
    """
    view = {
        "hash": str(article.get("hash") or ""),
        "story_id": str(article.get("story_id") or ""),
        "issue_id": str(article.get("issue_id") or ""),
        "title": str(article.get("title_kr") or article.get("title") or ""),
        "url": str(article.get("url") or ""),
        "publisher": str(article.get("publisher") or article.get("domain") or ""),
        "topics": list(article.get("topics") or []),
        "source_kind": "official" if article.get("origin") == "official" else "news",
        # 오래전에 확인해 원장이 들고 있던 일정인가. 화면이 '처음 확인' 날짜를
        # 근거 줄에 곁들일 수 있게 남긴다 — 두 달 전 기사가 근거라는 사실 자체가
        # 독자에게 값이다.
        **({"remembered": True} if article.get("origin") == "remembered" else {}),
    }
    # 공식 공지만 갖는 칸. 기사에는 없으므로 빈 값을 만들지 않는다.
    for field in ("source_id", "first_seen"):
        if article.get(field):
            view[field] = str(article[field])
    return view


def _fold(rows: list[dict]) -> list[dict]:
    """같은 일정을 하나로. 최신 보도가 이름과 날짜를 정한다.

    포항 집회가 W34 저장본에서 8/23, 다시 계산하면 9/20 이었던 것이 이 자리의
    문제였다 — 같은 사건이 기사마다 다른 날짜로 서면 독자는 어느 쪽도 못 믿는다.

    **공식 공지가 기사를 이긴다.** 먼저 놓인 행이 그 일정의 날짜·이름·주최를
    정하므로, 공식 경로를 앞으로 당겨 정렬한다. 기관이 제 행사에 대해 적은
    날짜는 그것을 전한 기사보다 정확하다 — 진 쪽은 버려지지 않고 아래에서 근거
    목록에 붙는다.
    """
    # 두 번 정렬한다. 파이썬의 정렬은 안정적이라 나중 정렬이 앞의 순서를
    # 그룹 안에서 그대로 지킨다 — 최신순을 먼저 매기고, 그 위에 공식 우선을 얹는다.
    rows = sorted(rows, key=lambda row: row.get("reference") or "", reverse=True)
    # 근거가 단단한 쪽이 그 일정의 이름과 날짜를 정한다. 추론으로 읽은 줄이
    # 먼저 놓이면 그 줄의 이름이 그룹 전체의 이름이 되는데, 추론은 대개 그
    # 일정을 **지나가며** 언급한 기사에서 나온다 — 실측 2026-09-11: SMR 특별법
    # 시행일을 'SMR 상용화 일정 가속화 시행'(원문에 날짜가 적힘)으로 부르던 것이
    # '경남도의회 시행'(건의안 기사에서 "11일 시행되는" 을 추론)으로 덮였다.
    rows = sorted(rows, key=lambda row: _BASIS_RANK.get(
        str(row.get("date_basis") or "explicit"), 0))
    rows = sorted(rows, key=lambda row: row.get("origin") != "official")
    folded: list[dict] = []
    for row in rows:
        for kept in folded:
            if _same_event(kept, row):
                # 한 기사가 제목 절과 본문 절에서 같은 일정을 두 번 낼 수 있다.
                # 그대로 쌓으면 출처 목록에 같은 기사가 두 줄로 서고 '보도 N건'
                # 이 부풀려진다(실측: 영덕 공모 상세에 국민일보가 두 번).
                if all(source["hash"] != row.get("hash") for source in kept["sources"]):
                    kept["sources"].append(_source_view(row))
                if row.get("cancelled"):
                    kept["cancelled"] = True
                # 연기 공지가 붙으면 **그 공지의 날짜가 이긴다.** 보도일 순서로
                # 가리면 같은 날 나온 예고와 연기가 입력 순서로 갈린다 — 어느
                # 쪽이 새 사실인지는 순서가 아니라 그 문장이 말한다.
                if row.get("postponed") and not kept.get("postponed"):
                    kept.update(date=row["date"], end_date=row["end_date"],
                                kind=row["kind"], clause=row["clause"],
                                label=row["label"], postponed=True)
                kept["first_seen"] = min(kept["first_seen"], row["reference"])
                # 진 쪽이 들고 있던 시각·주최·장소를 이긴 쪽의 빈 칸에 채운다.
                # 같은 행사를 두 곳이 다르게 적는다 — 협회 일정표는 장소를 주고
                # 주최를 안 주고, 협회 공지는 그 반대다(실측 9/4 조찬강연회).
                # 이긴 쪽의 값을 덮지는 않는다. 채우기만 한다.
                for field in ("time", "host", "place"):
                    if not kept.get(field) and row.get(field):
                        kept[field] = row[field]
                # 이긴 쪽의 이름이 어디에나 있는 말뿐이면('개최') 독자에게 아무것도
                # 말하지 않는다. 진 쪽이 뜻 있는 낱말을 들고 있으면 그것을 쓴다 —
                # 날짜와 근거는 그대로 두고 **이름만** 바꾼다.
                # 이름만 바꾼다 — 날짜와 근거는 이긴 쪽 그대로다.
                #   ① 어디에나 있는 말뿐이면('개최') 진 쪽의 이름을 쓴다.
                #   ② 둘 다 이름이 있어도 이긴 쪽이 '무엇을' 을 안 말하면
                #      (기장군 **개최** vs 기장군 사업 **설명회**) 말하는 쪽을 쓴다.
                if ((_label_is_bare(kept["label"]) and not _label_is_bare(row["label"]))
                        or (_label_is_vague(kept["label"])
                            and not _label_is_vague(row["label"])
                            and not _label_is_bare(row["label"]))):
                    kept["label"] = row["label"]
                # 기간이 점을 이긴다. 같은 일을 한쪽은 하루로, 다른 쪽은
                # "9월 2일부터 10월 13일까지"로 말했다면 넓은 쪽이 사실이다.
                #
                # 단 **공식 공지가 이미 정한 날짜는 기사가 못 바꾼다.** 기관이
                # 제 행사를 하루로 공지했는데 기사가 주변 일정까지 묶어 기간으로
                # 말하는 일이 있고, 그때 넓은 쪽을 택하면 공식 날짜가 기사에
                # 덮인다 — 이 통합에서 공식을 앞세운 이유가 통째로 무너진다.
                if (row["kind"] == "range" and kept["kind"] != "range"
                        and not (kept.get("origin") == "official"
                                 and row.get("origin") != "official")):
                    # 이름도 함께 가져온다. 종류만 바꾸면 기간 막대에 '마감'이
                    # 붙은 이름이 남아 화면이 스스로와 어긋난다.
                    # 날짜를 바꾸면 **그 날짜를 어떻게 읽었는지**도 함께 온다.
                    # 안 가져오면 추론으로 선 점이 기사에 적힌 기간으로 바뀐 뒤에도
                    # 'inferred' 표를 달고 있어 화면이 근거를 잘못 말한다.
                    kept.update(date=row["date"], end_date=row["end_date"],
                                kind="range", clause=row["clause"],
                                label=row["label"], origin=row["origin"],
                                date_basis=row.get("date_basis", "explicit"))
                break
        else:
            folded.append({**row, "sources": [_source_view(row)],
                           "first_seen": row["reference"]})
    for row in folded:
        row["source_count"] = len(row["sources"])
        row.pop("reference", None)
    # 취소를 말한 기사가 하나라도 붙어 있으면 그 일정은 내린다. 접힌 뒤에 보는
    # 이유는 취소 기사와 예고 기사가 **같은 일정의 두 근거**이기 때문이다 —
    # 후보 단계에서 취소분만 버리면 예고분이 그대로 칸에 남는다.
    return [row for row in folded if not row.get("cancelled")]


def _basis_counts(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in events:
        grade = str(row.get("date_basis") or "explicit")
        counts[grade] = counts.get(grade, 0) + 1
    return counts


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
