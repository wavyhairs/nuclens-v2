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
