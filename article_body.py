"""원문 본문을 큐레이션 직전에 가져온다. 저장하지 않고 프롬프트에만 쓴다.

배경 (2026-08-07 사용자 지적):
    "지금 ai가 대충 제목만 보고 요약하는 것 같아서 내용이 제대로 안 담겨있는 경우가
     많음. 예를들면 이런거는 가동중단이라고 적혀있으면서 밑에는 가동중단을 막았다고
     적혀있음."

    실측이 맞았다. ``curate_batch`` 가 모델에 준 것은 ``제목[:150]`` + RSS
    ``description[:200]`` + 출처 세 줄뿐이고, RSS description 은 Google News 경유
    기사(전체의 51%)에서 제목의 재탕이다. 그래서 아카이브 1,007건에서
    **요약의 57%가 제목 재진술**이고 **제목에 없는 수치를 담은 요약은 12%**뿐이었다.
    모델이 게을러서가 아니라 읽을 것을 준 적이 없다.

    같은 결핍이 모순도 만든다. 제목이 '3기 가동 중단'인데 해석이 '가동 중단을 피했다'로
    붙은 것은, 두 기사 모두 제목 한 줄씩만 있어서 어느 쪽이 최신 상태인지 판정할 근거가
    프롬프트 안에 없었기 때문이다.

설계:
    - **본문은 저장하지 않는다.** 아카이브·큐·웹 산출물 어디에도 넣지 않는다.
      저작권 판단(2026-07-31)을 뒤집지 않는다 — 남는 것은 한국어 요약뿐이다.
    - 실패는 전부 조용히 통과. 본문이 없으면 지금과 똑같이 제목·description 으로
      큐레이션한다. 수집·발송이 본문 때문에 멈추는 일은 없어야 한다.
    - Gemini 호출 수는 늘지 않는다. chunk 당 입력 토큰만 늘어난다(무료 티어 한도는
      RPM·RPD 가 먼저 걸린다 — 2026-08-06 실측).
    - stdlib + requests 만. trafilatura·readability 도입은 계속 보류.

Google News 우회:
    수집 URL 의 51%가 ``news.google.com/rss/articles/...`` 다. 이 주소는 302 가 아니라
    JS 인터스티셜을 준다 — 그냥 GET 하면 본문이 아니라 구글 페이지를 읽는다.
    기사 페이지에서 ``data-n-a-sg``(서명)·``data-n-a-ts``(시각)를 뽑아
    ``batchexecute`` 에 되물으면 실제 주소가 나온다(실측 6/6 성공).
    **이 두 속성명이 사라지면 조용히 전부 실패한다** — 그래서 통계를 찍는다.
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from data_quality import strip_emails

try:  # requests 는 news_bot 이 이미 의존하지만, 단독 import 시 죽지 않게 둔다
    import requests
except ImportError:  # pragma: no cover - 실행 환경엔 항상 있다
    requests = None  # type: ignore[assignment]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 프롬프트에 넣을 본문 길이. 리드와 첫 본문 몇 문단에 사실이 몰려 있으므로
# 앞에서 자른다.
#
# 1,500 → 6,000 (2026-10-03). 열흘치 요약 사실검사에서 "원문에 없는 내용"
# 판정의 일부가 실은 **잘린 뒷부분**이었다("기후부가 3개월간 어떤 행보를 보였는지
# 잘려 있어 확인할 수 없다"). 요약기와 검사기가 같은 1,500자를 보니 둘 다 뒷부분을
# 모른다 — 요약기는 추측하고 검사기는 '없다'고 한다.
#
# 상한 없이 받은 140건 실측: 중앙값 1,507자, 상위 10% 3,304자, 최장 10,597자.
#   상한 1,500 → 잘리는 기사 50%  /  3,000 → 13%  /  6,000 → 2%
# 6,000 이면 97% 가 통째로 들어가고 글자 총량은 3,000 대비 11% 는다. 상한을 아예
# 없애지 않는 이유는 모델이 아니라 **추출 실패**다 — 가끔 기사가 아닌 긴 목록·
# 댓글을 긁어오는데, 상한이 그 피해를 묶어 둔다. 호출 수는 늘지 않고 토큰만
# 는다(news_bot.BATCH_CHUNK 주석의 호출당 입력 계산).
MAX_BODY_CHARS = int(os.environ.get("MAX_BODY_CHARS") or 6000)
# 이보다 짧으면 '본문 없음'으로 본다. 저작권 안내문·쿠키 배너만 긁힌 경우가 여기 걸린다.
MIN_BODY_CHARS = 220

FETCH_TIMEOUT = 12.0
WORKERS = 8
# 한 크롤에서 본문을 시도할 최대 건수. MAX_CURATION_PER_RUN(80) 과 맞춰 둔다.
MAX_FETCH_PER_RUN = int(os.environ.get("MAX_BODY_FETCH_PER_RUN", "80"))

# 본문이 유료·봇차단으로 막힌 것이 확정된 도메인. 시도해도 401·403 만 받으므로
# 시간만 태운다. **여기 넣기 전에 반드시 실측할 것** — 넣으면 영영 안 부른다.
BLOCKED_DOMAINS = {
    "reuters.com",      # 401 (실측 2026-08-07, 5/5)
    "ft.com",           # 페이월
    "markets.ft.com",
    "bloomberg.com",
    "wsj.com",
    "nikkei.com",
    "denkishimbun.com",
}

_DROP_TAGS = re.compile(
    r"(?is)<(script|style|noscript|svg|iframe|form|nav|aside|header|footer|figure|figcaption)\b.*?</\1>"
)
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_TAG = re.compile(r"(?s)<[^>]+>")
_ARTICLE = re.compile(r"(?is)<article\b[^>]*>(.*?)</article>")
_P_BLOCK = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
# <p> 를 안 쓰고 <br> 로 줄을 나누는 국내 매체가 많다(실측 thin 16건의 대부분).
_BLOCK_SPLIT = re.compile(r"(?is)</?(?:br|p|div|li|h[1-6]|tr|section)\b[^>]*>")
_META_DESC = re.compile(
    r"""(?is)<meta[^>]+(?:property|name)\s*=\s*["'](?:og:description|description)["'][^>]*>"""
)
_META_CONTENT = re.compile(r"""(?is)content\s*=\s*["'](.*?)["']""")
_WS = re.compile(r"\s+")

# 본문에 섞여 들어오는 상투구. 한 줄 통째로 버린다.
_JUNK_LINE = re.compile(
    r"(?i)(무단\s*전재|재배포\s*금지|저작권자\s*©|ⓒ\s*\w+|구독하기|구독 신청|"
    r"기사제보|보도자료|카카오톡|네이버에서|뉴스레터|앱 다운로드|"
    r"all rights reserved|sign up|subscribe|newsletter|advertisement|"
    r"cookie|privacy policy|terms of (use|service)|"
    r"please enable js|ad ?blocker|follow us on)"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_SENT_END = re.compile(r"(?<=[.!?。])\s|(?<=다\.)\s|(?<=요\.)\s")


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str) -> bool:
    host = _domain(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


# --------------------------------------------------------------------------
# Google News 주소 복원
# --------------------------------------------------------------------------

_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def is_google_news(url: str) -> bool:
    return "news.google." in (url or "") and "/articles/" in (url or "")


def resolve_google_news(url: str, session) -> str | None:
    """Google News RSS 주소 → 실제 기사 주소. 실패하면 None."""
    try:
        article_id = url.split("/articles/")[1].split("?")[0]
    except IndexError:
        return None
    try:
        page = session.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": UA})
        signature = _SIG_RE.search(page.text)
        timestamp = _TS_RE.search(page.text)
        if not signature or not timestamp:
            # 구글이 마크업을 바꾸면 여기서 전부 죽는다. 통계로 드러나게 둔다.
            return None
        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
              None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            article_id, int(timestamp.group(1)), signature.group(1),
        ])
        payload = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = session.post(
            _BATCH_URL, data={"f.req": payload}, timeout=FETCH_TIMEOUT,
            headers={"User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        if resp.status_code != 200:
            return None
        for line in resp.text.splitlines():
            if "garturlres" not in line:
                continue
            return json.loads(json.loads(line)[0][2])[1]
    except Exception:  # noqa: BLE001 — 본문 부재는 비치명
        return None
    return None


# --------------------------------------------------------------------------
# 본문 추출
# --------------------------------------------------------------------------

def _clean_line(raw: str) -> str:
    text = html_module.unescape(_TAG.sub(" ", raw))
    return _WS.sub(" ", text).strip()


def _usable(line: str) -> bool:
    if len(line) < 40:
        return False
    if _JUNK_LINE.search(line):
        return False
    if _EMAIL.search(line) and len(line) < 80:
        return False
    return True


def _collect(fragment: str, splitter) -> list[str]:
    lines = []
    for raw in splitter(fragment):
        line = _clean_line(raw)
        if _usable(line) and line not in lines:
            lines.append(line)
    return lines


def _meta_description(page: str) -> str:
    for tag in _META_DESC.finditer(page):
        content = _META_CONTENT.search(tag.group(0))
        if not content:
            continue
        line = _clean_line(content.group(1))
        if len(line) >= 60:
            return line
    return ""


def extract_text(page_html: str, *, limit: int | None = None, title: str = "") -> str:
    """HTML → 본문 텍스트. 못 뽑으면 빈 문자열.

    ``<p>`` → 블록 태그 분해 → meta description 순으로 물러난다. 국내 매체 상당수가
    ``<p>`` 없이 ``<br>`` 로만 줄을 나눠서(실측) 두 번째 경로가 실제로 필요하다.

    ``title`` 을 주면 ``<article>`` 이 여러 개일 때 **제목과 가장 겹치는** 것을
    고른다. 2026-10-03 실측(140건): 헤럴드경제·EBN·이코노미사이언스는 한 페이지에
    ``<article>`` 이 9~13개(관련기사·인기기사 블록)이고 가장 긴 것이 남의 기사였다.
    강릉 데이터센터 기사의 본문이 개그우먼 홈쇼핑 기사로 뽑혀 제목 불일치로
    버려졌다. 제목이 없으면 예전처럼 가장 긴 것.
    """
    if limit is None:
        limit = MAX_BODY_CHARS
    if not page_html:
        return ""
    cleaned = _DROP_TAGS.sub(" ", _COMMENT.sub(" ", page_html))

    # <article> 이 있으면 그 안이 본문일 확률이 높다. 없으면 문서 전체.
    scopes = [m.group(1) for m in _ARTICLE.finditer(cleaned)] or [cleaned]
    if title and len(scopes) > 1:
        scope = max(scopes, key=lambda frag: (_title_hits(_clean_line(frag), title)[0], len(frag)))
    else:
        scope = max(scopes, key=len)

    lines = _collect(scope, lambda frag: _P_BLOCK.findall(frag))
    if len(" ".join(lines)) < MIN_BODY_CHARS:
        lines = _collect(scope, _BLOCK_SPLIT.split)
    body = "\n".join(lines).strip()

    if len(body) < MIN_BODY_CHARS:
        fallback = _meta_description(page_html)
        # meta description 한 줄이라도 제목 재탕보다는 낫다. 다만 본문으로
        # 착각하지 않게 짧으면 버린다.
        body = fallback if len(fallback) >= 60 else ""

    if not body:
        return ""
    # 본문에는 바이라인 기자 메일이 자주 붙어 온다. 저장되기 전 여기서 지운다 —
    # 본문이 curated·archive·큐레이션 프롬프트로 갈라지기 전의 마지막 한 자리다.
    # **자르기 전에** 지운다: 길이 안쪽으로 끝나는 본문이 훨씬 흔해서, 자른 뒤에만
    # 지우면 대부분의 기사가 그대로 빠져나간다.
    body = strip_emails(body)
    if len(body) <= limit:
        return body
    # 문장 중간에서 자르면 모델이 잘린 절을 사실로 읽는다. 경계에서 끊는다.
    head = body[:limit]
    parts = _SENT_END.split(head)
    if len(parts) > 1:
        head = " ".join(parts[:-1])
    return head.strip()


# --------------------------------------------------------------------------
# 수집
# --------------------------------------------------------------------------

# 제목 토큰. 한국어는 조사가 붙어 완전 일치가 안 되므로(선행 사례: keei_match 에서
# '영덕군과' ≠ '영덕군' 때문에 진짜 매칭이 후보에서 탈락할 뻔했다) 접두로 맞춘다.
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}")
# 제목 토큰의 이 비율만큼은 본문에 있어야 같은 기사로 본다.
#
# 2026-08-10 실측으로 올렸다(0.30/2 → 0.50/3). 라이브에서 폴리뉴스 '한수원, 신규
# 대형 원전 및 SMR 부지 후보지 선정' 기사가 **해외건설 수주** 본문을 받아 그대로
# 이슈 상세의 '기사 내용'이 돼 있었다. 겹친 낱말은 '원전'·'대형' 둘뿐인데 짧은
# 제목에서는 round(8*0.30)=2 라 하한 2 와 맞물려 통과했다 — 원자력 기사면 어디에나
# 있는 낱말 두 개가 '같은 사건'의 증거로 쓰인 것이다. 이 저장소가 KEEI 매칭·이슈
# 병합에서 이미 두 번 겪은 함정("같은 분야"를 "같은 사건"으로 읽는 것)이 수집
# 계층에서 재발했다.
#
# 보정 근거: 아카이브 직접 URL 최신 51건을 실제로 받아 제목↔본문 겹침을 쟀다.
# 44건이 0.55 이상이고 0.50 미만은 4건뿐이다(0.25 인 오탐 사례와 명확히 갈린다).
# 새 기준에서 그 4건은 본문 없이 진행한다 — 오탐의 대가는 다른 사건의 본문이
# 전문가에게 '이 기사의 내용'으로 제시되는 것이고, 누락의 대가는 원래부터 있던
# 제목+요약 경로로 돌아가는 것뿐이다. 비대칭이므로 버리는 쪽으로 기운다.
_RELEVANCE_RATIO = 0.50
_RELEVANCE_MIN_HITS = 3


def _title_hits(text: str, title: str) -> tuple[int, int]:
    """(제목 토큰 중 text 에 있는 수, 제목 토큰 수). 조사 한 글자를 떼고도 본다."""
    tokens = set(_TOKEN_RE.findall(title or ""))
    if not tokens:
        return 0, 0
    haystack = (text or "").lower()
    hits = 0
    for token in tokens:
        needle = token.lower()
        # 조사 한 글자를 떼고도 본다('원전이' → '원전').
        if needle in haystack or (len(needle) > 2 and needle[:-1] in haystack):
            hits += 1
    return hits, len(tokens)


_PAGE_TITLE_RES = (
    re.compile(r"""(?is)<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']{1,300})["']"""),
    re.compile(r"""(?is)<meta[^>]+content=["']([^"']{1,300})["'][^>]+property=["']og:title["']"""),
    re.compile(r"(?is)<title[^>]*>(.*?)</title>"),
)


def extract_page_title(page_html: str) -> str:
    """페이지가 스스로 말하는 제목(og:title → <title>). 없으면 빈 문자열."""
    for pattern in _PAGE_TITLE_RES:
        match = pattern.search(page_html or "")
        if match:
            line = _clean_line(html_module.unescape(match.group(1)))
            if line:
                return line
    return ""


# 페이지 제목이 기사 제목과 이만큼 겹치면 "이 페이지가 그 기사"라고 본다.
_PAGE_TITLE_RATIO = 0.50
_PAGE_TITLE_MIN_HITS = 2
# 그때 본문 쪽 하한. 0이면 안 된다 — 페이지는 맞는데 본문 범위를 잘못 잡은
# 경우(관련기사 블록)를 걸러야 하므로 제목 낱말이 조금은 본문에 있어야 한다.
_BODY_RATIO_WITH_PAGE_TITLE = 0.25


def matches_title(body: str, title: str, page_title: str = "") -> bool:
    """본문이 그 제목의 기사인가.

    필요한 이유: 프롬프트가 "제목과 본문이 어긋나면 본문이 우선"이라고 지시하므로,
    엉뚱한 페이지(섹션 목록·관련기사 블록·리다이렉트된 다른 글)를 긁어오면 그
    오류가 그대로 요약이 된다. 실측에서 서로 다른 두 URL 이 같은 본문을 돌려주고
    요약까지 같아진 사례가 있었다. 판정할 수 없으면 **본문을 버린다** — 본문
    없이 돌아가는 경로는 이미 있고 그쪽이 안전하다.

    ``page_title`` (og:title) 이 기사 제목과 맞으면 본문 쪽 하한을 낮춘다.
    2026-10-03 실측(140건): 제목 불일치로 버린 12건 중 9건이 **맞는 기사**였다.
    "30조 넣고 수익률 15%?…그래서 얼마 버나", "누가 말이 맞아?" 처럼 제목이
    구어·동사 낱말로 돼 있어 본문 첫 1,500자에 그 낱말이 없었던 것이다. 페이지
    제목은 그 구어 낱말까지 그대로 들고 있으므로 "이 페이지가 그 기사"의 증거가
    된다. 다만 본문 겹침 0은 통과시키지 않는다 — 페이지는 맞는데 본문 범위가
    남의 기사인 경우(관련기사 블록)는 여전히 버려야 한다. 폴리뉴스 사고(해외건설
    본문)는 페이지 제목부터 다르므로 이 완화로 되살아나지 않는다.
    """
    if not title:
        return True
    hits, unique = _title_hits(body, title)
    if unique == 0:
        return True
    if hits >= max(_RELEVANCE_MIN_HITS, round(unique * _RELEVANCE_RATIO)):
        return True
    if page_title:
        page_hits, _ = _title_hits(page_title, title)
        page_matches = page_hits >= max(_PAGE_TITLE_MIN_HITS, round(unique * _PAGE_TITLE_RATIO))
        if page_matches and hits >= max(2, round(unique * _BODY_RATIO_WITH_PAGE_TITLE)):
            return True
    return False


_SITE_NAME_RE = re.compile(
    r"""(?is)<meta[^>]+property=["']og:site_name["'][^>]+content=["']([^"']{1,80})["']""")
_SITE_NAME_REV_RE = re.compile(
    r"""(?is)<meta[^>]+content=["']([^"']{1,80})["'][^>]+property=["']og:site_name["']""")
# 포털이 자기 이름을 앞에 붙인다: `Daum | 노컷뉴스`, `NAVER | 연합뉴스`.
_PORTAL_PREFIX_RE = re.compile(r"^(?:daum|naver|네이버|다음)\s*[|·>-]\s*", re.I)


def extract_site_name(page_html: str) -> str:
    """og:site_name 에서 매체명. 못 얻으면 빈 문자열.

    본문 때문에 어차피 페이지를 받고 있으므로 이건 공짜다. 아카이브의 매체명
    59%가 `hankyung.com` 처럼 도메인 그대로인데(2026-08-10 실측 1,017건 중 601건),
    도메인→이름 표를 손으로 만들면 꼬리가 251개라 유지가 안 되고 **틀리기도 한다** —
    `chosun.com` 의 실제 매체는 조선일보가 아니라 조선비즈인 기사가 있었다.
    페이지가 스스로 말하는 이름이 표보다 정확하다.

    표본 30건 실측: 29건이 받아졌고 그중 25건에서 이름을 얻었다(중앙일보·국민일보·
    이데일리·아시아경제·조선비즈·대한경제·강원도민일보…). 못 얻은 4건은 태그가
    없는 매체이고 그때는 종전 표기를 그대로 둔다.
    """
    match = _SITE_NAME_RE.search(page_html) or _SITE_NAME_REV_RE.search(page_html)
    if not match:
        return ""
    name = _PORTAL_PREFIX_RE.sub("", match.group(1)).strip()
    # 도메인·URL 을 site_name 에 넣어 둔 매체가 있다 — 그건 지금 값과 다를 게 없다.
    if not name or " " in name and len(name) > 40:
        return ""
    if name.lower().startswith(("http", "www.")) or ("." in name and " " not in name):
        return ""
    return name


def fetch_one(url: str, session, title: str = "",
              meta: dict | None = None) -> tuple[str, str]:
    """(본문, 상태). 상태는 통계·진단용이며 호출자는 본문만 보면 된다.

    `meta` 를 주면 페이지에서 곁다리로 알아낸 것을 담아 준다(`site_name`·`url`).
    본문 판정과 매체명은 갈라 두는 게 맞다 — 본문이 제목과 안 맞아 버려지는
    기사도 매체명은 멀쩡하다.
    """
    if not url:
        return "", "no_url"
    if is_google_news(url):
        resolved = resolve_google_news(url, session)
        if not resolved:
            return "", "google_unresolved"
        url = resolved
    if meta is not None:
        meta["url"] = url
    if is_blocked(url):
        return "", "blocked_domain"
    try:
        resp = session.get(
            url, timeout=FETCH_TIMEOUT,
            headers={"User-Agent": UA,
                     "Accept-Language": "ko,en-US;q=0.8,en;q=0.6"},
        )
    except Exception as exc:  # noqa: BLE001
        return "", f"error_{type(exc).__name__}"
    if resp.status_code >= 400:
        return "", f"http_{resp.status_code}"
    # 인코딩 추정 실패로 한글이 깨지면 본문이 통째로 쓰레기가 된다.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    if meta is not None:
        # 본문 판정보다 **먼저** 담는다. 제목과 안 맞아 본문을 버리는 기사도
        # 매체명은 멀쩡하고, 그 기사도 카드에는 실린다.
        meta["site_name"] = extract_site_name(resp.text)
    body = extract_text(resp.text, title=title)
    if not body:
        return "", "thin"
    if not matches_title(body, title, extract_page_title(resp.text)):
        return "", "title_mismatch"
    return body, "ok"


def fetch_bodies(articles: list[dict], *, max_fetch: int = MAX_FETCH_PER_RUN,
                 workers: int = WORKERS, session_factory=None) -> tuple[dict[str, str], dict]:
    """{hash: 본문} 과 통계. 실패한 기사는 키가 없다(호출자는 그냥 건너뛴다).

    매체명을 알아내면 기사 dict 의 `site_name` 에 직접 적어 둔다 — 본문은
    저장하지 않는 계약이지만(저작권) 매체명은 표시용 사실이라 아카이브에 남는다.
    """
    stats = {"attempted": 0, "ok": 0, "chars": 0, "reasons": {}}
    if not articles or requests is None:
        return {}, stats

    targets = articles[:max_fetch]
    stats["attempted"] = len(targets)
    if len(articles) > len(targets):
        stats["deferred"] = len(articles) - len(targets)

    local = threading.local()
    make_session = session_factory or requests.Session

    def session_for_thread():
        got = getattr(local, "session", None)
        if got is None:
            got = make_session()
            local.session = got
        return got

    def work(article: dict) -> tuple[str, str, str, str]:
        meta: dict = {}
        title = str(article.get("title") or "")
        body, status = fetch_one(article.get("link") or "", session_for_thread(), title, meta)
        if meta.get("site_name"):
            article["site_name"] = meta["site_name"]
        # Google News 리다이렉트를 푼 실주소. 원래 link 는 그대로 둔다 —
        # 그게 dedup 키(url_hash)라 바꾸면 같은 기사가 새 기사로 들어온다.
        resolved = meta.get("url") or ""
        if resolved and resolved != (article.get("link") or ""):
            article["resolved_url"] = resolved
        failed_at = _domain(resolved or article.get("link") or "")
        # 원 매체가 막으면 네이버 미러로 한 번 더 간다. 운영(GitHub Actions 의
        # 해외 데이터센터 IP)에서는 403 이 한 회차에 17~45건인데, 같은 기사 140건을
        # 국내 주거용 IP 에서 받으면 403 은 2건이다 — 헤더가 아니라 IP 차단이다
        # (브라우저 전체 헤더로도 변화 없음, 2026-10-03 실측). 그래서 헤더를
        # 바꾸는 대신 **다른 주소**로 간다. 네이버 API 가 준 미러 주소는 수집 때
        # `naver_link` 로 들고 있다(news_bot). 미러 페이지는 본문이 <article>
        # 하나에 og:title 과 함께 있어 그대로 뽑힌다(실측 2/2). 제목 불일치는
        # 원 페이지가 맞는 기사가 아니라는 뜻이 아니라 판정 실패라 미러로 안 간다.
        mirror = str(article.get("naver_link") or "")
        if not body and mirror and status != "title_mismatch":
            mirror_meta: dict = {}
            mirror_body, mirror_status = fetch_one(mirror, session_for_thread(), title, mirror_meta)
            if mirror_body:
                body, status = mirror_body, "ok_naver"
            else:
                status = f"{status}>naver_{mirror_status}"
        return article.get("hash", ""), body, status, failed_at

    bodies: dict[str, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(work, targets))
    except Exception as exc:  # noqa: BLE001 — 본문 부재는 비치명
        stats["reasons"]["pool_error"] = f"{type(exc).__name__}"
        return {}, stats

    failed_domains: dict[str, dict[str, int]] = {}
    for article_hash, body, status, failed_at in results:
        stats["reasons"][status] = stats["reasons"].get(status, 0) + 1
        if body and article_hash:
            bodies[article_hash] = body
            stats["ok"] += 1
            stats["chars"] += len(body)
        elif failed_at:
            # 어느 매체가 막는지 로그에 남긴다. 2026-10-03 까지는 http_403=45 라는
            # 숫자만 있어 어느 도메인인지 알 수 없었고, 그래서 차단 목록도
            # 미러 전략도 실측 없이는 못 정했다.
            key = status.split(">")[0]
            failed_domains.setdefault(key, {})
            failed_domains[key][failed_at] = failed_domains[key].get(failed_at, 0) + 1
    if failed_domains:
        stats["failed_domains"] = failed_domains
    if stats["ok"]:
        stats["avg_chars"] = stats["chars"] // stats["ok"]
    return bodies, stats


def format_stats(stats: dict) -> str:
    reasons = stats.get("reasons") or {}
    detail = " ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
    rate = (stats["ok"] * 100 // stats["attempted"]) if stats.get("attempted") else 0
    line = (f"[body] 본문 {stats.get('ok', 0)}/{stats.get('attempted', 0)}건 ({rate}%) "
            f"평균 {stats.get('avg_chars', 0)}자 | {detail}")
    failed = stats.get("failed_domains") or {}
    for status in ("http_403", "title_mismatch", "thin"):
        domains = failed.get(status) or {}
        if domains:
            top = sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            line += "\n[body] " + status + ": " + ", ".join(f"{d}({n})" for d, n in top)
    return line
