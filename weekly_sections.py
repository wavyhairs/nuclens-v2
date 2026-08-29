"""주간 판세의 **결정적 코너** — 핵심사건·국가별 단신·발간물·예정.

왜 따로 있는가
--------------
주간 화면에 남아 있던 것은 '주제별 강약'과 '한수원 직접 영향' 둘뿐이었다.
그 주에 무엇이 있었는지(핵심사건), 어디서 났는지(국가별), 읽을 문서가
나왔는지(발간물), 다음에 무엇이 잡혀 있는지(예정)는 어디에도 없었다.

이 네 코너는 **LLM 에게 다시 묻지 않는다.** 재료가 이미 구조화돼 있기 때문이다.
  · 사건 묶음 — `weekly_bot.weekly_stories` (일일 브리핑과 같은 clusterer)
  · 등급·자질 — 큐레이션의 importance / features
  · 국가       — 큐레이션의 countries 를 근거 계약의 국가로 **다시 확인**
  · 발간물     — pubs_fetch 가 커밋한 publications.json 의 kind/off_topic 판정
  · 일정       — 큐레이션의 event_date 를 원문 문장에서 **다시 확인**
같은 것을 생성기에게 또 물으면 호출만 늘고 지어낼 자리가 생긴다. 여기서 하는
일은 이미 검증된 값을 고르고 정렬하는 것뿐이라, 결과가 재현되고 역추적된다.

무엇을 하지 않는가
------------------
* 수를 채우지 않는다. 근거가 없으면 그 코너는 빈 채로 둔다.
* 같은 사건을 코너마다 다시 내지 않는다 — 핵심사건에 선 사건은 국가별에서 빠진다.
* 확인되지 않은 국가·일정은 **버린다**. 모르면 침묵한다.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import article_quality_gate

ROOT = Path(__file__).parent
PUBLICATIONS_FILE = ROOT / "publications.json"

# 예정 코너를 독자 화면에 낼지 (2026-08-22 부터 False).
#
# 끈 것이지 지운 것이 아니다. 아래 일정 추출 — 선언된 event_date 되짚기와 원문
# 문장 직접 추출 — 은 그대로 돌고, `build_sections` 도 `upcoming` 을 계속 채워
# 저장본에 남긴다. 재료를 계속 쌓아 두어야 나중에 Event Calendar 를 설계할 때
# 지난 주차로 대조해 볼 수 있고, 저장 필드가 비면 그 대조할 것이 사라진다.
#
# 그 Event Calendar 는 만들어졌다 — `event_calendar.py`(흐름 탭 '앞으로 무엇이
# 있나'). 그래서 이 flag 는 **계속 꺼 둔다.** 같은 일정을 두 화면이 각자 고르면
# 주간 판세와 달력이 서로 다른 날짜를 말하게 되고, 그것이 이 코너를 끄게 만든
# 문제와 정확히 같은 모양이다. 저장본의 `upcoming` 은 그대로 남는다 — 달력이
# 지난 주차로 자기를 대조하는 자리가 여기다.
#
# 무엇을 끄는가: **읽는 사람에게 보이는 두 표면뿐**이다.
#   · 텔레그램 주간 브리핑 — `weekly_bot.format_weekly`
#   · 웹 흐름 탭의 주간 판세 — `web/build_data.py` 의 화면 페이로드,
#     그리고 그 데이터를 그리는 `web/public/app.js` (같은 이름의 상수)
# 다시 켤 때는 이 값을 True 로 두고 app.js 의 SHOW_WEEKLY_UPCOMING 도 같이
# 뒤집으면 된다 — 파이썬 상수가 브라우저까지 가지는 않기 때문이다.
SHOW_WEEKLY_UPCOMING = False

# 코너별 노출 상한. 하한은 없다 — 근거가 모자라면 그만큼만 낸다.
TOP_STORY_LIMIT = 3
COUNTRY_BRIEF_LIMIT = 6
PUBLICATION_LIMIT = 6
UPCOMING_LIMIT = 6

# 발간물 코너는 "보고서로 쓸 문서"만 받는다. pubs_fetch 의 kind 가 이미
# 언론기사(news_or_report)와 문서를 갈라 놓았으므로 그 판정을 그대로 쓴다.
PUBLICATION_KINDS = frozenset({"publication", "keei_insight", "analysis"})

# 미래 일정으로 인정하는 사건일 종류. announcement(발표일)·occurrence(발생일)는
# 지나간 일이라 '예정'이 아니다.
UPCOMING_DATE_TYPES = frozenset({"scheduled", "deadline", "effective"})

# 국가 한글 표기는 게이트가 국가 판정에 쓰는 어휘의 첫 항목이다. 표를 두 벌
# 두면 한쪽만 늘어나는 날이 오고, 그날 화면에는 코드가 그대로 나간다.
COUNTRY_NAMES = article_quality_gate.COUNTRY_NAMES


# ---- 사건 점수 -----------------------------------------------------------------
#
# 보도량은 점수의 일부일 뿐이다. 같은 발표를 열 곳이 쓰면 사건 하나로 묶이지만,
# 그래도 '많이 쓰였다'가 남아 매체 수로 점수를 밀 수 있다. 그래서 매체·후속
# 보도가 더할 수 있는 몫에 상한을 두고(합 2.4), 등급·진전·정책 자질이 그보다
# 크게 잡히도록(합 최대 11) 배분했다 — "보도량이 많다"가 "중요하다"를 이기지
# 못하게 하는 것이 이 코너의 존재 이유다.
_FEATURE_WEIGHTS = {
    "policy_materiality": 0.5,
    "market_materiality": 0.5,
    "korea_relevance": 0.5,
    "report_worthiness": 0.5,
}


def _text(story: dict) -> str:
    """사건의 주제 본문 — 제목과 요약. 해석·본문은 넣지 않는다."""
    return " ".join(
        str(article.get(field) or "")
        for article in story.get("articles") or []
        for field in ("title", "title_kr", "summary")
    )


def _feature_score(story: dict) -> float:
    total = 0.0
    for field, weight in _FEATURE_WEIGHTS.items():
        best = 0
        for article in story.get("articles") or []:
            features = article.get("features")
            if not isinstance(features, dict):
                continue
            try:
                best = max(best, int(features.get(field, 0)))
            except (TypeError, ValueError):
                continue
        total += min(max(best, 0), 3) * weight
    return total


def _published(story: dict) -> str:
    rep = (story.get("articles") or [{}])[0]
    return str(rep.get("published_at") or rep.get("cached_at") or "")


def story_score(story: dict, *, is_development=None) -> float:
    """사건의 주간 중요도. 결정적이고 설명 가능해야 한다."""
    articles = story.get("articles") or []
    rep = articles[0] if articles else {}
    score = 3.0 if rep.get("grade") == "must_read" else 1.0
    if is_development is not None and is_development(_text(story)):
        score += 2.0
    score += _feature_score(story)
    score += min(int(story.get("outlets") or 1), 4) * 0.4
    score += min(max(len(articles) - 1, 0), 4) * 0.2
    return round(score, 3)


# ---- 표시 단위 중복 억제 --------------------------------------------------------
#
# clusterer 는 같은 발표의 재보도를 묶지만 같은 사건을 다른 각도로 쓴 기사까지
# 묶지는 않는다("전력망 3법 통과" / "민생법안 70건 처리"). 코너 하나 안에서
# 그 둘이 나란히 서면 독자에게는 같은 줄이 두 번이다. 묶음 자체를 바꾸지 않고
# **표시할 때만** 겹치는 제목을 접는다 — clustering 규칙을 건드리면 일일
# 브리핑까지 같이 움직인다.
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}")
_TITLE_OVERLAP = 0.5


def title_tokens(title: object) -> frozenset[str]:
    """제목의 의미 토큰. 코너 사이의 '같은 줄' 판정에 쓴다."""
    return frozenset(token.casefold()
                     for token in _TOKEN_RE.findall(str(title or "")))


def _title_tokens(story: dict) -> frozenset[str]:
    rep = (story.get("articles") or [{}])[0]
    return title_tokens(rep.get("title_kr") or rep.get("title"))


def echoes(tokens: frozenset[str], seen: list[frozenset[str]]) -> bool:
    """이미 낸 제목 중 하나를 다시 말하고 있는가."""
    return _echoes(tokens, seen)


def _echoes(tokens: frozenset[str], seen: list[frozenset[str]]) -> bool:
    if not tokens:
        return False
    for other in seen:
        if not other:
            continue
        overlap = len(tokens & other) / min(len(tokens), len(other))
        if overlap >= _TITLE_OVERLAP:
            return True
    return False


def story_view(story: dict) -> dict:
    """코너가 공유하는 사건 표현 — 대표 제목·요약·원문·보도 폭."""
    articles = story.get("articles") or []
    rep = articles[0] if articles else {}
    return {
        "key": str(story.get("key") or ""),
        "hash": str(story.get("hash") or ""),
        "title": str(rep.get("title_kr") or rep.get("title") or ""),
        "summary": str(rep.get("summary") or ""),
        "link": str(rep.get("link") or ""),
        "articles": len(articles),
        "outlets": int(story.get("outlets") or 1),
        "published_at": _published(story),
    }


# ---- (1) 이번 주 핵심사건 -------------------------------------------------------

def top_stories(stories: list[dict], *, is_development,
                limit: int = TOP_STORY_LIMIT) -> list[dict]:
    """그 주의 판을 대표하는 사건 몇 개. 억지로 `limit` 을 채우지 않는다.

    후보 조건은 두 가지다 — 큐레이션이 must_read 로 뽑았고, 그 사건이 실제로
    **일어난 일**을 말한다(전망·주장 기사는 판세의 축이 될 수 있어도 '이번 주에
    무슨 일이 있었나'의 답은 아니다). 둘 다 만족하는 사건이 없으면 빈 목록이다.
    """
    scored = []
    for story in stories:
        rep = (story.get("articles") or [{}])[0]
        if rep.get("grade") != "must_read":
            continue
        if not is_development(_text(story)):
            continue
        scored.append((story_score(story, is_development=is_development), story))
    scored.sort(key=lambda row: (-row[0], row[1].get("key") or ""))

    out: list[dict] = []
    seen: list[frozenset[str]] = []
    for score, story in scored:
        tokens = _title_tokens(story)
        if _echoes(tokens, seen):
            continue
        seen.append(tokens)
        out.append({**story_view(story), "score": score})
        if len(out) >= limit:
            break
    return out


# ---- (2) 국가별 단신 ------------------------------------------------------------

def story_country(story: dict, contract) -> str:
    """이 사건의 무대는 어디인가 — **확인된 경우에만** 답한다.

    큐레이션이 붙인 countries 를 후보로 삼되, 근거 계약이 원문에서 결정적으로
    읽어 낸 국가와 겹치는 것만 인정한다. 겹치는 것이 없으면 빈 문자열이다.
    한 사건에 여러 나라가 걸리면(수출·합작) 큐레이션이 적어 둔 순서를 따른다 —
    그쪽이 기사의 주어에 가깝다.
    """
    if contract is None:
        return ""
    verified = getattr(contract, "countries", frozenset())
    for article in story.get("articles") or []:
        for value in article.get("countries") or []:
            code = str(value or "").strip().upper()
            if code in COUNTRY_NAMES and code in verified:
                return code
    return ""


def country_briefs(stories: list[dict], contracts: dict, *, is_development,
                   exclude: set[str] | None = None,
                   limit: int = COUNTRY_BRIEF_LIMIT) -> list[dict]:
    """국가당 대표 사건 하나. 국가 수를 채우려고 약한 기사를 올리지 않는다.

    핵심사건에 이미 선 사건(`exclude`)은 여기서 빠진다 — 같은 줄을 두 번 내는
    것은 정보가 아니라 반복이다.
    """
    exclude = exclude or set()
    scored = []
    for story in stories:
        key = str(story.get("key") or "")
        if key in exclude:
            continue
        if not is_development(_text(story)):
            continue
        code = story_country(story, contracts.get(key))
        if not code:
            continue
        scored.append((story_score(story, is_development=is_development), code, story))
    scored.sort(key=lambda row: (-row[0], row[2].get("key") or ""))

    out: list[dict] = []
    used: set[str] = set()
    seen: list[frozenset[str]] = []
    for score, code, story in scored:
        if code in used:
            continue
        tokens = _title_tokens(story)
        if _echoes(tokens, seen):
            continue
        used.add(code)
        seen.append(tokens)
        out.append({**story_view(story), "country": code,
                    "country_kr": COUNTRY_NAMES[code], "score": score})
        if len(out) >= limit:
            break
    return out


# ---- (3) 이번 주 발간물 ---------------------------------------------------------

def _pub_days(item: dict) -> tuple[str, str]:
    """(표시할 날짜, 우리가 처음 확인한 날). 없는 값은 빈 문자열."""
    def day(field: str) -> str:
        value = str(item.get(field) or "").strip()[:10]
        return value if len(value) == 10 else ""
    published, fetched = day("date"), day("fetched_at")
    return published or fetched, fetched


def week_publications(week_start: object, week_end: object, *,
                      items: list[dict] | None = None,
                      path: Path | None = None,
                      limit: int = PUBLICATION_LIMIT) -> list[dict]:
    """그 주에 나온 **문서**만. 언론기사·보도자료는 발간물이 아니다.

    판정은 pubs_fetch/pubs_translate 가 이미 붙여 둔 값을 쓴다 (추가 호출 0).
      · kind        — publication / keei_insight / analysis 만 받는다
      · off_topic   — 번역 배치가 '자료로 못 쓴다'고 본 것은 뺀다
    """
    start, end = str(week_start or "")[:10], str(week_end or "")[:10]
    if len(start) != 10 or len(end) != 10:
        return []
    if items is None:
        try:
            raw = json.loads((path or PUBLICATIONS_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = raw.get("items") if isinstance(raw, dict) else raw
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip().lower() not in PUBLICATION_KINDS:
            continue
        if item.get("off_topic") is True:
            continue
        # '이번 주 발간물'은 그 주에 **새로 손에 들어온** 문서다. 격주간지처럼
        # 발행일이 며칠 앞선 자료를 발행일만으로 자르면, 그 주에 처음 읽을 수
        # 있게 된 자료가 어느 주에도 안 뜬다(실측 W34: 그래서 0건이었다).
        day, fetched = _pub_days(item)
        if not day:
            continue
        if not (start <= day <= end) and not (start <= fetched <= end):
            continue
        title = str(item.get("title_kr") or item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        rows.append({
            "org": str(item.get("org_kr") or item.get("org") or "").strip(),
            "title": title,
            "url": url,
            "date": day,
            "gist": str(item.get("gist") or "").strip(),
            "relevance": str(item.get("relevance") or "").strip().lower(),
        })
    rows.sort(key=lambda row: (row["date"], row["title"]), reverse=True)
    return rows[:limit]


# ---- (4) 예정 -------------------------------------------------------------------

def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _evidence_text(article: dict) -> str:
    """일정 확인에 쓰는 원문 — 제목·요약에 더해 큐레이션이 뜬 본문 요지까지.

    날짜는 대개 본문에 있다(실측: event_date 가 있는 187건 중 대다수가
    `event_date_source=article_text`). 제목·요약만 보면 실제로 원문에 적힌
    날짜를 '근거 없음'으로 버린다. 이 텍스트는 **이 검사 전용**이며 판세 문장의
    근거 계약(`weekly_bot.weekly_contracts`)에는 들어가지 않는다 — 검증 범위를
    조용히 넓히지 않기 위해서다.
    """
    return " ".join(str(article.get(field) or "") for field in
                    ("title", "title_kr", "summary", "detail"))


# 절을 가르는 자리. 날짜와 일정 표지가 **같은 문장** 안에 있을 때만 인정한다.
_SENTENCE_RE = re.compile(r"[.。!?\n]+|(?<=다)\s+(?=[가-힣A-Z])")

# 앞으로 잡힌 일을 말하는 표지. 날짜가 적혀 있다고 다 일정은 아니다 —
# "2030년 목표"는 포부이지 일정이 아니고, "8월 15일 발생"은 과거다.
# (과거는 `week_end` 이후만 받는 것으로 이미 걸리므로, 이 표지가 막는 것은
#  주로 '수치로서의 미래 연도'다.)
_SCHEDULE_MARKER_RE = re.compile(
    r"예정|예고|계획이다|열린다|열릴|개최|주최|개막|착공|준공|시행|발효|"
    r"마감|접수|공청회|설명회|토론회|간담회|공람|열람|표결|의결 예정|"
    r"착수할|제출할|방문한다|방문할|출범|시작한다|시작할|"
    r"scheduled|will be held|will take place|takes place|due on|deadline")

# 이 코너가 보는 앞날의 길이. 반년 뒤 일정은 '다음에 무엇이 잡혀 있나'가
# 아니라 사업 계획이고, 그것은 판세 문장이 다룬다.
UPCOMING_HORIZON_DAYS = 120

# 사건일 정밀도가 '연도'뿐이면 일정이 아니다 — 화면에 '1월 1일'로 나간다.
UPCOMING_PRECISIONS = frozenset({"day", "month"})

# 예정 코너만 주제를 본다. 다른 코너는 중요도 순으로 잘라 상한을 두므로 약한
# 항목이 위로 올라오지 않지만, 달력은 날짜순이라 **무엇이든** 날짜만 적혀
# 있으면 자리를 차지한다(실측 W34: 미 재무부 국채 환매, 반도체 산업용수
# 토론회가 원자력 일정 자리를 먹었다).
#
# 통제 주제(news_bot.VALID_TOPICS)의 부분집합만 쓴다 — 새 분류를 만들지 않는다.
# regulation·power_market·datacenter_ai·finance·security_trade 는 원자력 밖에서도
# 쓰이므로 여기 없다. 다만 SMR·한수원 섹션의 규제 기사는 원자력 규제이므로
# 그 조합만 따로 받는다(실측: '원자력 지역자원시설세' 집회가 regulation 하나로
# 태깅돼 있었다).
_NUCLEAR_TOPICS = frozenset({"smr", "newbuild", "restart_lto", "fuel_cycle",
                             "waste", "fusion", "fukushima"})
_NUCLEAR_SECTIONS = frozenset({"smr", "khnp"})


def _is_nuclear_story(story: dict) -> bool:
    for article in story.get("articles") or []:
        topics = {str(value).strip().lower() for value in article.get("topics") or []}
        if topics & _NUCLEAR_TOPICS:
            return True
        if "regulation" in topics and str(article.get("section") or "") in _NUCLEAR_SECTIONS:
            return True
    return False


def _declared_schedule(article: dict, cutoff: date, horizon: date,
                       ) -> tuple[date, str] | None:
    """큐레이션이 뜬 event_date 를 **원문에 있는지 되짚어** 확인한다."""
    when = _as_date(article.get("event_date"))
    if when is None or not (cutoff < when <= horizon):
        return None
    if str(article.get("event_date_type") or "") not in UPCOMING_DATE_TYPES:
        return None
    precision = str(article.get("event_date_precision") or "")
    if precision not in UPCOMING_PRECISIONS:
        return None
    reference = _as_date(article.get("published_at") or article.get("cached_at"))
    if reference is None:
        return None
    if article_quality_gate.date_evidence_problem(
            when, precision, _evidence_text(article), reference):
        return None
    return when, precision


def _written_schedule(article: dict, cutoff: date, horizon: date,
                      ) -> tuple[date, str] | None:
    """원문 문장에 **적혀 있는** 앞날 일정.

    큐레이션의 event_date 는 채움률이 낮다(실측 W34: 기사 1001건 중 2건, 그마저
    연도 정밀도). 그래서 값이 없을 때는 원문을 직접 본다 — 단, 게이트가 카드
    날짜에 쓰는 것과 **같은 추출기**로, 실제로 적힌 달·일만 받는다.
    '다음 주'·'조만간' 같은 상대 표현은 그 추출기가 아예 날짜로 만들지 않으므로
    여기서 임의의 날짜가 생겨날 자리가 없다.
    """
    reference = _as_date(article.get("published_at") or article.get("cached_at"))
    if reference is None:
        return None
    best: date | None = None
    for clause in _SENTENCE_RE.split(_evidence_text(article)):
        if not _SCHEDULE_MARKER_RE.search(clause):
            continue
        for when in article_quality_gate.explicit_dates(clause, reference):
            if cutoff < when <= horizon and (best is None or when < best):
                best = when
    return (best, "day") if best is not None else None


def upcoming(stories: list[dict], week_end: object, *, is_development=None,
             limit: int = UPCOMING_LIMIT) -> list[dict]:
    """`week_end` 이후의 일정 중 **원문에서 그 날짜가 확인되는 것만**.

    큐레이션의 event_date 를 그대로 믿지 않는다. '다음 주'가 임의의 날짜로
    바뀌었는지, 기사 작성일을 행사일로 옮겨 적었는지는 값만 봐서는 알 수 없다.
    그래서 두 경로 모두 원문을 되짚는다 — 선언된 값은 게이트의 날짜 판정기로,
    선언이 없는 경우는 같은 추출기로 문장에서 직접.

    같은 일정이 여러 기사에 있으면 사건 하나로 접는다. 자를 때는 날짜순이
    아니라 **중요도순**으로 자르고 그 다음에 날짜로 늘어놓는다 — 날짜순으로
    자르면 가장 먼저 잡힌 사소한 일정이 그 달의 큰 일정을 밀어낸다.
    """
    cutoff = _as_date(week_end)
    if cutoff is None:
        return []
    horizon = cutoff + timedelta(days=UPCOMING_HORIZON_DAYS)
    found_rows: list[tuple[float, dict]] = []
    seen_titles: list[frozenset[str]] = []
    for story in stories:
        if not _is_nuclear_story(story):
            continue
        best: tuple[date, str] | None = None
        for article in story.get("articles") or []:
            found = (_declared_schedule(article, cutoff, horizon)
                     or _written_schedule(article, cutoff, horizon))
            if found and (best is None or found[0] < best[0]):
                best = found
        if best is None:
            continue
        tokens = _title_tokens(story)
        if _echoes(tokens, seen_titles):
            continue
        seen_titles.append(tokens)
        found_rows.append((story_score(story, is_development=is_development),
                           {**story_view(story), "date": best[0].isoformat(),
                            "precision": best[1]}))
    found_rows.sort(key=lambda row: (-row[0], row[1]["date"]))
    rows = [row for _score, row in found_rows[:limit]]
    rows.sort(key=lambda row: (row["date"], row["title"]))
    return rows


# ---- 한수원이 직접 당사자인 사건 ------------------------------------------------
#
# '한수원 직접 영향' 문단이 산업 일반론으로 흘러가는 것을 막는 재료다. 국내
# 기업의 해외 SMR 참여를 곧바로 한수원의 사업기회라고 쓰면 근거 없는 확장인데,
# 생성기에게는 그 경계가 보이지 않는다. 그래서 **한수원이 문장의 주어이거나
# 그 발전소가 무대인 사건**을 Python 이 먼저 골라 준다.
_KHNP_TERMS = (
    "한수원", "한국수력원자력", "khnp",
    "고리", "새울", "월성", "한울", "신한울", "한빛", "새울", "신고리", "신월성",
)


def khnp_stories(stories: list[dict], *, limit: int = 12) -> list[dict]:
    """한수원이 직접 당사자인 사건. 없으면 빈 목록이다."""
    rows = []
    for story in stories:
        haystack = re.sub(r"\s+", "", _text(story)).casefold()
        if any(term in haystack for term in _KHNP_TERMS):
            rows.append(story_view(story))
    return rows[:limit]


def build_sections(stories: list[dict], contracts: dict, *, is_development,
                   week_start: object, week_end: object,
                   publications: list[dict] | None = None,
                   publications_path: Path | None = None) -> dict:
    """네 코너를 한 번에. 저장본·텔레그램·웹이 같은 값을 쓴다."""
    top = top_stories(stories, is_development=is_development)
    return {
        "top_stories": top,
        "country_briefs": country_briefs(
            stories, contracts, is_development=is_development,
            exclude={row["key"] for row in top}),
        "publications": week_publications(
            week_start, week_end, items=publications, path=publications_path),
        "upcoming": upcoming(stories, week_end, is_development=is_development),
    }
