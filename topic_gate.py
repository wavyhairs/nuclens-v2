"""큐레이션 뒤의 결정적 주제 게이트 — "이 기사는 원자력·전력 얘기인가".

무엇이 문제였나
---------------
2026-10-03 아침 브리핑 16건에 "G7, 원유 1억 배럴 방출", "미 사법당국의 오픈AI·
앤스로픽 조사", "트럼프 84억 달러 석유 회수"가 이슈로 올랐다. 수집 쪽 원인(Google
`site:` 피드가 키워드를 무시)은 PR #221 이 막았지만, 그것은 다섯 피드 얘기다.
국내 Google 키워드 검색·산업부 보도자료처럼 다른 경로로 들어온 기사도 같은
방식으로 샌다.

마지막 방어선은 큐레이션 LLM 의 ``noise`` 판정이었다. 그 판정은 정확도 95% 다 —
9/19~10/2 아카이브에서 원자력 단어 없는 종합지 기사 904건 중 862건을 noise 로
찍었다. 그러나 나머지 42건(5%)이 nice_to_know 로 통과했고, 하루 수십 건이
들어오는 구조에서는 5% 가 매일 몇 건이다. 같은 기간 실제 발송된 브리핑 254건
중 16건이 이 부류였다.

무엇을 하나
-----------
LLM 판정 뒤에 **결정적으로 한 번 더** 본다. 두 신호만 쓴다.

  1. 원자력 어휘 — 제목·한글 제목·요약·태그에 원전/원자력/핵연료/nuclear/…
     가 있는가. 있으면 끝, 원자력 기사다.
  2. 없으면 ``khnp_relevance`` 의 사업환경 관련성 — 전력시장·수요·정책·발전사
     축이 걸리는가. AI 데이터센터 전력수요·전력수급기본계획·발전공기업 통합처럼
     원자력 단어 없이도 한수원 환경에 직접 걸리는 기사는 **의도적으로 싣는다**
     (아카이브 non-noise 2,938건 중 1,155건이 이 부류다. 그걸 다 빼면 브리핑이
     아니라 전문지 색인이 된다).

둘 다 없으면 ``off_topic`` 이다. 그때만 ``nice_to_know`` 를 ``noise`` 로 내린다.

  · ``must_read`` 는 손대지 않는다 — LLM 이 가장 확신한 등급이고, "트럼프, 한국
    기업의 미국 내 2,000억 달러 투자 발표"처럼 그 기사 자체엔 원전 낱말이 없어도
    같은 사건의 다른 기사(원전 8기)와 한 이슈가 되는 경우가 실제로 있다.
  · ``market`` 은 이미 브리핑·화면 밖이라 손댈 이유가 없다.
  · 내린 사실은 기록한다 — ``importance_llm`` 에 원래 등급, ``topic_verdict`` 에
    판정. 아카이브에 남으므로 "게이트가 과했나"를 사후에 셀 수 있다.

소급 검증(발송분 254건, 9/19~10/2): 걸리는 16건 중 must_read 2건은 예외로 남고,
14건은 CXMT D램·SK하이닉스 HBM·America.gov·디젤 재고·메탄 규제·G7 원유·오픈AI
조사 같은 것들이다. 전력 요금·발전사 통합 기사는 ``khnp_relevance`` 의 topics
대응표(``power_market``·``datacenter_ai``)로 살아남는다 — 그 대응이 빠져 있던
것이 함께 고친 버그다.

쓰지 않은 길
------------
  · 프롬프트에 "석유·가스·AI 규제는 noise" 를 더하기 — 비결정적이고, 프롬프트
    지문이 바뀌면 판정 캐시가 전부 무효가 돼 Gemini 한도를 태운다.
  · daily_brief 선별에서 빼기 — 큐레이션 결과를 바로 쓰는 화면(issues)에는
    그대로 남는다. 등급 자체를 내려야 큐·브리핑·화면이 한 숫자를 본다.
"""
from __future__ import annotations

import re

import khnp_relevance

# 한국어 원자력 어휘. **부분 문자열**로 보므로 짧은 토큰은 넣지 않는다 —
# "핵" 은 핵심, "고리" 는 연결고리, "농축" 은 농축액에 걸린다.
NUCLEAR_TERMS_KO: tuple[str, ...] = (
    "원전", "원자력", "원자로", "한수원", "한국수력원자력", "핵연료", "사용후핵연료",
    "원안위", "방사성", "방사능", "방폐", "우라늄", "핵융합", "소형모듈", "경수로",
    "중수로", "계속운전", "신한울", "새울", "한빛", "월성", "apr1400", "apr-1400",
    "ap1000", "웨스팅하우스", "두산에너빌리티", "원자력연료", "kaeri", "kins", "kinac",
    "iaea",
)

OFF_TOPIC = "off_topic"
ADJACENT = "adjacent"
ON_TOPIC = "on_topic"

# 이번 실행에서 내린 기사 — 실행 끝에 운영 요약용 품질 이벤트로 올린다.
DEMOTED: list[dict] = []


def _terms() -> tuple[str, ...]:
    # news_bot 이 이 모듈을 부르므로 모듈 수준에서 서로 import 하면 순환이다.
    import news_bot  # noqa: PLC0415
    return tuple(k.lower() for k in news_bot.NUCLEAR_TITLE_KEYWORDS) + NUCLEAR_TERMS_KO


def _subject(article: dict) -> str:
    parts = [article.get("title"), article.get("title_kr"), article.get("summary"),
             " ".join(str(t) for t in (article.get("tags") or []))]
    return " ".join(str(p or "") for p in parts).lower()


def has_nuclear_term(article: dict) -> bool:
    text = _subject(article)
    return any(term in text for term in _terms())


def assess(article: dict) -> dict:
    """{"verdict", "nuclear_term", "level", "score", "reasons"}."""
    term = has_nuclear_term(article)
    rel = khnp_relevance.relevance(article)
    level = rel.get("level")
    if term or level in ("required", "expected"):
        verdict = ON_TOPIC
    elif level == "optional":
        verdict = ADJACENT
    else:
        verdict = OFF_TOPIC
    return {"verdict": verdict, "nuclear_term": term, "level": level,
            "score": rel.get("score"), "reasons": rel.get("reasons") or []}


def apply(normalized: dict, article: dict) -> dict | None:
    """정규화된 큐레이션 결과에 판정을 싣고, 주제 밖 nice_to_know 를 noise 로 내린다.

    내렸으면 기록 row 를 돌려주고 DEMOTED 에도 쌓는다. 아니면 None.
    """
    probe = {**normalized,
             "title": article.get("title", ""),
             "domain": article.get("domain", "")}
    verdict = assess(probe)
    normalized["topic_verdict"] = verdict["verdict"]
    if verdict["verdict"] != OFF_TOPIC or normalized.get("importance") != "nice_to_know":
        return None
    normalized["importance_llm"] = normalized["importance"]
    normalized["importance"] = "noise"
    row = {
        "hash": article.get("hash", ""),
        "title": normalized.get("title_kr") or article.get("title", ""),
        "domain": article.get("domain", ""),
        "level": verdict["level"],
        "score": verdict["score"],
        "reasons": verdict["reasons"][:4],
    }
    DEMOTED.append(row)
    return row


def reset() -> None:
    DEMOTED.clear()


# ---- 직접 연결 — 브리핑 막차 자리를 누가 차지하는가 -------------------------------
#
# 위 게이트는 "브리핑에 실을 만한가"를 넓게 본다(``adjacent`` 도 통과). 그 폭은 의도된
# 것이지만, 원자력 기사가 점수에서 밀린 날 막차 자리를 원자력·전력과 이어지지 않는
# 기사가 차지했다. 그림자 하루치 순위(shadow_rank, 2026-10-10~18) 실측: 그림자가
# 내렸을 기사 48건 중 원자력 직접 관련은 31%, 올렸을 기사 46건은 74%였고, 내렸을
# 쪽에는 a16z AI 펀드·리튬 공급계약·LFP 배터리 증설·중국산 하이브리드카 규제·
# 부산시 시정 청사진이 있었다. `khnp_relevance` 는 '데이터센터'·'수출'·'공급망'
# 한 낱말로 축을 세우므로(카드 시사점 필요성 판정용 — 그쪽에선 넓어야 맞다) 이
# 기사들이 adjacent·expected 로 통과한다.
#
# 사용자 결정(2026-10-18): 원자력과 무관한 AI·반도체·배터리 산업 기사가 원전 핵심
# 기사보다 앞서지 못하게 한다. 석유 가격 정책도 전력·원전과 다른 축이라 같은 쪽.
#
# 그래서 여기서는 **좁게** 묻는다 — 제목·요약에 원자력 어휘나 전력 계통 어휘가
# 있는가. 큐레이션 LLM 이 붙인 topics 는 쓰지 않는다: 같은 기사를 두 번 물으면
# 주제 태그가 59% 만 같았다(shadow_rank docstring). 낱말 판정이라 같은 입력이면
# 늘 같은 답이다. 판정은 점수 조정의 재료일 뿐 삭제가 아니다(ranking ``topic_link``).

# 전력 계통·전원·요금·전력 공기업. 부분 문자열이고 공백을 지운 본문과 맞춘다.
# '발전' 하나는 넣지 않는다 — '경제 발전'·'발전 방안'에 걸린다.
POWER_TERMS_KO: tuple[str, ...] = (
    "전력", "전기요금", "전기료", "발전소", "발전사", "발전공기업", "발전량", "발전설비",
    "발전용량", "발전기", "발전엔진", "화력발전", "가스발전", "석탄발전", "복합발전",
    "열병합", "양수발전", "수소발전", "태양광", "풍력", "지열", "재생에너지", "탈석탄",
    "석탄화력", "송전", "배전",
    "변전", "계통", "한전", "한국전력", "전력거래소", "무탄소", "에너지믹스", "전원믹스",
    "탄소중립", "에너지정책", "에너지안보", "에너지저장", "lng", "천연가스", "가스공사",
    "에너지공기업",
    # 대미 투자 패키지는 원전 8기 프레임워크와 한 묶음이라 제목에 원전이 없어도 잇는다
    # (`백악관, 한국의 대미 에너지 투자 패키지 2000억 달러` 는 '에너지투자'로 걸린다).
    "대미투자", "대미전략투자", "에너지투자",
)
POWER_TERMS_EN: tuple[str, ...] = (
    "electricity", "power grid", "grid", "utility", "utilities", "power plant",
    "transmission", "megawatt", "gigawatt", "power purchase", "ppa", "energy policy",
)
# 위 NUCLEAR_TERMS_KO 에 없는 원자력 어휘. 덴마크 `핵폐기물 심층처분장`, 원안위의
# `생활 방사선` 제품 관리가 이 낱말로만 원자력이다.
NUCLEAR_EXTRA_KO: tuple[str, ...] = (
    "핵폐기물", "방사선", "핵추진", "핵물질", "고준위",
)
# 원자력 산업의 고유명사 — 기업·노형·발전소. 제목·요약이 일반 어휘 없이 이름만
# 쓰는 기사가 있다(`카자톰프롬, 2027 생산 가이던스 하향` — 세계 최대 우라늄
# 생산사인데 '우라늄'이 없다). 한글은 공백을 지운 부분 문자열, 영문은 단어 경계.
NUCLEAR_NAMES_KO: tuple[str, ...] = (
    "카자톰프롬", "카메코", "오라노", "프라마톰", "센트러스", "로사톰", "유렌코",
    "테라파워", "뉴스케일", "엑스에너지", "카이로스", "홀텍", "오클로", "뉴클레오",
    "라스트에너지", "나트륨원자로", "아쿠유", "바라카", "두코바니", "팰리세이즈",
    "힝클리", "사이즈웰", "플라망빌", "모호체", "루푸르", "톈완", "엘다바",
    # 짧은 국내 발전소명은 호기·원전을 붙여 쓴다 — '한울타리'·'연결고리'에 걸린다.
    # ('보글'(Vogtle)도 같은 이유로 영문만 둔다 — '보글보글'.)
    "한울원전", "한울1", "한울2", "한울3", "한울4", "한울5", "한울6",
    "고리원전", "고리1", "고리2", "고리3", "고리4",
    # 한울 소재지. 군 단위 기사(`울진행복 에너지연금`)가 원전 지원금 얘기다.
    "울진",
)
NUCLEAR_NAMES_EN: tuple[str, ...] = (
    "kazatomprom", "cameco", "orano", "framatome", "centrus", "rosatom", "urenco",
    "terrapower", "nuscale", "x-energy", "kairos", "holtec", "oklo", "newcleo",
    "last energy", "natrium", "bwrx", "akkuyu", "barakah", "dukovany", "palisades",
    "vogtle", "hinkley", "sizewell", "flamanville", "mochovce", "rooppur", "el dabaa",
    "nrc",
)

LINK_NUCLEAR = "nuclear"
LINK_POWER = "power"
LINK_NONE = "none"

_SPACE_RE = re.compile(r"\s+")
_POWER_EN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in POWER_TERMS_EN) + r")\b")
_NUCLEAR_NAMES_EN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in NUCLEAR_NAMES_EN) + r")\b")


def direct_link(article: dict) -> str:
    """원자력(nuclear) · 전력(power) · 연결 없음(none)."""
    if has_nuclear_term(article):
        return LINK_NUCLEAR
    text = " ".join(str(article.get(k) or "")
                    for k in ("title", "title_kr", "summary")).lower()
    compact = _SPACE_RE.sub("", text)
    if (any(term in compact for term in NUCLEAR_EXTRA_KO + NUCLEAR_NAMES_KO)
            or _NUCLEAR_NAMES_EN_RE.search(text)):
        return LINK_NUCLEAR
    if any(term in compact for term in POWER_TERMS_KO) or _POWER_EN_RE.search(text):
        return LINK_POWER
    return LINK_NONE


def annotate_links(items: list[dict]) -> None:
    """후보마다 ``topic_link`` 를 싣는다(제자리). ``ranking.score_item`` 이 읽는다."""
    for item in items:
        item["topic_link"] = direct_link(item)
