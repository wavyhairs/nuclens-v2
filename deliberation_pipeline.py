"""신규원전 공론화 레인 — 수집 회차마다 판정해서 별도 DB(deliberation_archive/)에 쌓는다.

어디서 도나
-----------
news_bot.main() 이 해외 AI 레인 바로 뒤에 부른다. 메인 상태(sent.json·curated.json·digest_queue.json·archive/)는
읽기만 하고, 이 레인이 고른 기사는 **메인 큐에 들어가지 않는다** — 아침 브리핑이 바뀌면 안 된다.

입력 다섯 갈래 (재현율 우선 — 설계 문서 §2)
------------------------------------------
  1. shared    최근 3일 메인 아카이브(큐레이션된 대표 기사). 한글 제목·요약·기사 내용을 그대로 쓴다.
  2. raw       이번 회차 수집 후보 중 아카이브에 없는 대표(큐레이션 한도·무결성 게이트에 걸린 것).
  3. folded    이번 회차 수집 단계에서 대표에 **접힌** 기사(raw_sources). 아카이브에는 남지 않는다 —
               0단계 실측으로 「의제 확정」 아카이브 1줄 뒤에 22건이 접혀 있었다. 대표가 포함이면
               판정을 **상속**하고(호출 없음), 대표가 제외·미판정이면 스스로 프리필터·판정을 거친다.
  4. dedicated 전용 질의 — 네이버 뉴스 API(질의당 100건씩, 3일 창을 다 덮을 때까지 페이지를 넘긴다)와
               Google 뉴스 KR. 메인의 30건·6시간 창·매체 점수·홍보성·요약 길이 필터를 타지 않는다.
  5. backlog   지난 회차에 판정 한도로 밀린 후보(3일 보관). 큰 사건 날 접힌 기사 수십 건이 한 회차에
               다 판정되지 못해도 잃지 않는다.

판정 실패는 표식 없이 다음 회차가 다시 묻고(이월), 세 번 실패하면 포기한다 — AI 레인과 같다.

가드레일
  · news_bot 을 import 하지 않는다. 검색·RSS·해시 함수는 호출자가 넘긴다(순환·secret 회피).
  · 어떤 예외도 크롤을 멈추지 않는다. 호출부도 try 로 감싼다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable

import ai_lane_pipeline as shared
import deliberation_lane as lane
from data_quality import normalize_url, source_profile, title_key
from story_cluster import raw_sources_of

BASE = Path(__file__).parent
ARCHIVE_DIR = Path(os.environ.get("DELIBERATION_ARCHIVE_DIR") or (BASE / "deliberation_archive"))
STATE_FILE = Path(os.environ.get("DELIBERATION_STATE_FILE") or (BASE / "deliberation_state.json"))
RECORD_VERSION = 1

# 한 회차에 판정하는 상한(8건 배치 8회). 접힌 기사는 대표를 상속하면 호출이 없으므로 대부분
# 대표·전용 질의 기사만 센다. 넘친 것은 backlog 로 이월된다.
MAX_JUDGE_PER_RUN = int(os.environ.get("DELIBERATION_MAX_JUDGE") or 64)
MAX_ATTEMPTS = 3
WINDOW = timedelta(days=3)
BACKLOG_TTL = timedelta(days=3)
KNOWN_KEY_DAYS = 14

# ── 전용 질의 ────────────────────────────────────────────────────────────────
# 지역지가 쓰는 말은 「공론화」가 아니라 지역명이다 — 그래서 지역 × 공론화 묶음을 따로 둔다.
# 공론화 없는 유치·부지 기사는 범위 밖(사용자 결정 2026-10-15)이라 '영덕 원전' 같은 질의는 두지 않는다.
NAVER_QUERIES: tuple[str, ...] = (
    # 주제
    "원전 공론화", "신규원전 공론화", "신규 원전 공론화", "공론화위원회 원전", "원전 공론화위",
    "시민참여단 원전", "원전 숙의", "원전 공론조사", "전기본 공론화", "전력수급기본계획 공론화",
    "기후에너지환경부 공론화", "원전 공론화 반발", "원전 공론화 환경단체", "원전 공론화 국회",
    "원전 공론화 의제", "원전 공론화 권고안",
    # 지역 × 공론화
    "영덕 공론화", "울진 공론화", "삼척 공론화", "경주 공론화", "기장 공론화", "영광 공론화",
    "원전 공론화 주민", "원전 공론화 지역",
    # 사설·칼럼
    "사설 원전 공론화", "사설 공론화 원전", "칼럼 원전 공론화", "기고 원전 공론화", "시론 공론화",
)
NAVER_PAGE = 100          # 네이버 검색 API 의 display 상한
NAVER_MAX_START = 1000    # start 상한


def _gnews(query: str) -> str:
    return shared._gnews(query, "ko", "KR", "KR:ko")


# Google 뉴스 KR. 질의는 200자 미만(AI 레인 실측: 그보다 길면 when: 창이 무시됐다).
# site: 묶음은 네이버 색인이 늦거나 빠지는 지역지·전문지 보강이다. 도메인이 틀리면 그 피드만
# '받음 0' 으로 로그에 남고 레인은 계속 간다 — 2주 뒤 포함 0 인 피드는 뺀다.
GNEWS_SOURCES: list[dict] = [
    {"name": "공론화·Google 원전 공론화", "lang": "ko",
     "url": _gnews("(원전 OR 원자력) (공론화 OR 공론화위원회 OR 시민참여단 OR 숙의) when:2d")},
    {"name": "공론화·Google 사설", "lang": "ko",
     "url": _gnews("(사설 OR 칼럼 OR 기고) (원전 OR 원자력) 공론화 when:3d")},
    {"name": "공론화·Google 지역", "lang": "ko",
     "url": _gnews("(영덕 OR 울진 OR 삼척 OR 경주 OR 기장 OR 영광 OR 울주) 공론화 when:2d")},
    {"name": "공론화·Google 영남 지역지", "lang": "ko",
     "url": _gnews("(site:kbmaeil.com OR site:hidomin.com OR site:kyongbuk.co.kr OR site:imaeil.com "
                   "OR site:yeongnam.com OR site:ksilbo.co.kr) 공론화 when:3d")},
    {"name": "공론화·Google 기타 지역지", "lang": "ko",
     "url": _gnews("(site:busan.com OR site:kookje.co.kr OR site:kwangju.co.kr OR site:jnilbo.com "
                   "OR site:kado.net OR site:kwnews.co.kr) 공론화 when:3d")},
    {"name": "공론화·Google 에너지 전문지", "lang": "ko",
     "url": _gnews("(site:electimes.com OR site:ekn.kr OR site:energydaily.co.kr OR site:e2news.com "
                   "OR site:todayenergy.kr OR site:energy-news.co.kr) 공론화 when:3d")},
]


# 기간 백필용 Google 뉴스 질의 — when: 대신 after:/before: 로 기간을 자른다. 네이버 검색 API 는 기간 지정이
# 없고 최신순 1,000건까지만 주므로, 「원전 공론화」처럼 하루 수십 건 나오는 질의는 한 달 전까지 닿지 못한다
# (crawl #681: 전체 32,337건). 그 공백을 기간별 Google 질의가 메운다. 주제 질의는 기사가 몰리는 주제라 3일씩,
# 사설·지역은 7일씩 자른다(Google 뉴스 RSS 는 질의당 100건 안팎을 준다).
BACKFILL_GNEWS: tuple[tuple[str, str, int], ...] = (
    ("주제", "(원전 OR 원자력) (공론화 OR 공론화위원회 OR 시민참여단 OR 숙의)", 3),
    ("사설", "(사설 OR 칼럼 OR 기고 OR 시론) (원전 OR 원자력) 공론화", 7),
    ("지역", "(영덕 OR 울진 OR 삼척 OR 경주 OR 기장 OR 영광 OR 울주) 원전 공론화", 7),
)


def gnews_range_sources(since: datetime, until: datetime) -> list[dict]:
    """[since, until) 을 질의별 간격으로 잘라 Google 뉴스 KR 피드 목록을 만든다."""
    out: list[dict] = []
    for label, query, step in BACKFILL_GNEWS:
        start = since.date()
        while start < until.date():
            end = min(start + timedelta(days=step), until.date() + timedelta(days=1))
            out.append({"name": f"공론화·Google 백필 {label} {start.isoformat()}", "lang": "ko",
                        "url": _gnews(f"{query} after:{start.isoformat()} before:{end.isoformat()}")})
            start = end
    return out


# ── 상태 ─────────────────────────────────────────────────────────────────────
def load_state(path: Path = STATE_FILE) -> dict:
    state = shared.load_state(path)
    state.setdefault("backlog", {})
    return state


def save_state(state: dict, path: Path = STATE_FILE, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - BACKLOG_TTL).isoformat()
    state["backlog"] = {h: item for h, item in (state.get("backlog") or {}).items()
                        if h not in state.get("judged", {}) and str(item.get("queued_at") or "") >= cutoff}
    shared.save_state(state, path, now=now)


# ── 입력 정리 ────────────────────────────────────────────────────────────────
_ITEM_KEYS = ("hash", "origin", "feed", "title", "title_kr", "summary", "detail", "description", "url",
              "resolved_url", "domain", "publisher", "pub", "scope", "main_importance", "collected_at",
              "folded_into")


def _slim(item: dict) -> dict:
    return {key: item[key] for key in _ITEM_KEYS if item.get(key) not in (None, "", [])}


def _queued(item: dict, now_iso: str) -> dict:
    """이월 항목. 처음 밀린 시각을 지킨다 — 회차마다 새로 찍으면 3일 보관이 끝나지 않는다."""
    return {**_slim(item), "queued_at": item.get("queued_at") or now_iso}


def from_main_record(record: dict) -> dict:
    item = shared.from_main_record(record)
    item["main_article_type"] = record.get("article_type", "")
    return item


def from_collected(article: dict, *, now_iso: str) -> dict:
    """이번 회차 수집 후보(대표) — 큐레이션 전이라 제목·설명뿐이다."""
    return {
        "hash": str(article.get("hash") or ""),
        "origin": "raw",
        "feed": str(article.get("feed") or ""),
        "title": lane.strip_html(article.get("title")),
        "description": lane.strip_html(article.get("description")),
        "url": article.get("link") or article.get("url") or "",
        "domain": article.get("domain") or article.get("publisher_domain") or "",
        "publisher": article.get("publisher") or "",
        "pub": shared._iso(article.get("pub")),
        "collected_at": now_iso,
    }


def from_folded(raw: dict, rep_hash: str, *, now_iso: str) -> dict:
    return {
        "hash": str(raw.get("hash") or ""),
        "origin": "folded",
        "feed": str(raw.get("feed") or ""),
        "title": lane.strip_html(raw.get("title")),
        "url": raw.get("link") or "",
        "domain": raw.get("domain") or "",
        "publisher": raw.get("publisher") or "",
        "pub": str(raw.get("pub") or ""),
        "collected_at": now_iso,
        "folded_into": rep_hash,
    }


def naver_item(item: dict, hasher: Callable[[str], str], query: str, *, now_iso: str) -> dict | None:
    link = item.get("originallink") or item.get("link") or ""
    if not link:
        return None
    try:
        pub = parsedate_to_datetime(item.get("pubDate") or "")
    except (TypeError, ValueError):
        pub = None
    url = normalize_url(link)
    domain = url.split("/")[2] if url.count("/") >= 2 else ""
    domain = domain[4:] if domain.startswith("www.") else domain
    return {
        "hash": hasher(url),
        "origin": "dedicated",
        "feed": f"네이버 「{query}」",
        "title": lane.strip_html(item.get("title")),
        "description": lane.strip_html(item.get("description")),
        "url": url,
        "domain": domain,
        "publisher": source_profile(domain).get("publisher", "") or domain,
        "pub": pub.astimezone(timezone.utc).isoformat() if pub else "",
        "collected_at": now_iso,
    }


def collect_naver(search: Callable[..., dict], hasher: Callable[[str], str], *, now: datetime,
                  queries: Iterable[str] = NAVER_QUERIES,
                  window: timedelta = WINDOW) -> tuple[list[dict], dict[str, dict]]:
    """질의마다 3일 창을 다 덮을 때까지 100건씩 페이지를 넘긴다.

    ``search(query, display=, start=)`` 은 네이버 응답 JSON 전체({"total", "items"})를 돌려준다.
    통계의 ``total`` 은 창과 무관한 전체 건수, ``covered`` 는 창의 끝까지 닿았는지다 — 닿지 못하면
    (상한 1,000건에 걸리면) 놓침이 생긴 것이라 로그에 ✗ 로 남는다.
    """
    cutoff = now - window
    now_iso = now.isoformat()
    out: list[dict] = []
    counts: dict[str, dict] = {}
    for query in queries:
        stat = counts[f"네이버 「{query}」"] = {**shared._feed_stat(), "total": 0, "pages": 0, "covered": True}
        start = 1
        try:
            while start <= NAVER_MAX_START:
                payload = search(query, display=NAVER_PAGE, start=start) or {}
                items = payload.get("items") or []
                stat["pages"] += 1
                stat["total"] = int(payload.get("total") or stat["total"] or 0)
                stat["fetched"] += len(items)
                oldest = None
                for raw in items:
                    item = naver_item(raw, hasher, query, now_iso=now_iso)
                    if item is None:
                        continue
                    pub = datetime.fromisoformat(item["pub"]) if item["pub"] else None
                    oldest = pub if pub and (oldest is None or pub < oldest) else oldest
                    if pub and pub < cutoff:
                        continue
                    stat["fresh"] += 1
                    out.append(item)
                if len(items) < NAVER_PAGE or (oldest is not None and oldest < cutoff):
                    break
                start += NAVER_PAGE
            else:
                stat["covered"] = False
        except Exception as exc:  # noqa: BLE001 — 질의 하나가 레인을 멈추면 안 된다
            print(f"  ! [공론화] 네이버 「{query}」 실패: {type(exc).__name__}")
            stat["failed"] = 1
    return out, counts


# ── 판정 결과 → 레코드 ───────────────────────────────────────────────────────
CLASS_FIELDS = ("stage", "actor_type", "actor_names", "stance", "article_type", "significance",
                "event_key", "event_date", "confidence")


def _publisher_name(article: dict) -> str:
    """매체 이름. 접힌 기사 일부는 이름 칸에 주소가 들어 있다(「https://www.」) — 그때는 도메인."""
    name = str(article.get("publisher") or "").strip()
    if not name or name.startswith(("http://", "https://")):
        return str(article.get("domain") or "").removeprefix("www.")
    return name


def make_record(article: dict, verdict: dict, *, judged_at: str, model: str = "",
                judge: str = "gemini") -> dict:
    profile = source_profile(article.get("domain") or "", _publisher_name(article))
    title = article.get("title") or ""
    # 사실 문장은 메인 큐레이션이 우선이다(메인 헤드라인 규칙을 이미 거쳤다). 해석 문장은 레인 것이
    # 우선이다 — 메인 해석은 한수원 정책 관점이라 "공론화 절차에서 무슨 뜻인가"를 말하지 않는다.
    record = {
        "v": RECORD_VERSION,
        "lane": "deliberation",
        "origin": article.get("origin", ""),
        "hash": article.get("hash", ""),
        "archived_at": judged_at,
        "collected_at": article.get("collected_at") or judged_at,
        "pub": article.get("pub", ""),
        "url": article.get("url", ""),
        "resolved_url": article.get("resolved_url", ""),
        "domain": profile.get("domain") or article.get("domain", ""),
        "publisher": profile.get("publisher") or _publisher_name(article),
        "source_type": profile.get("source_type", ""),
        "evidence_role": profile.get("evidence_role", ""),
        "source_tier": profile.get("source_tier", 3),
        "title": title,
        "title_kr": article.get("title_kr") or verdict.get("title_kr") or title,
        "summary": article.get("summary") or verdict.get("summary", "") or article.get("description", ""),
        "detail": article.get("detail", ""),
        "why_important": verdict.get("why_important") or article.get("why_important", ""),
        "implication": verdict.get("implication") or article.get("implication", ""),
        "main_importance": article.get("main_importance", ""),
        "feed": article.get("feed", ""),
        "folded_into": article.get("folded_into", ""),
        "reason": verdict.get("reason", ""),
        "judge": judge,
        "judge_model": model,
        "prompt_version": lane.PROMPT_VERSION,
    }
    for key in CLASS_FIELDS:
        record[key] = verdict.get(key, "" if key != "significance" else 0)
    if not record["event_key"]:
        record["event_key"] = record["title_kr"][:40]
    return record


def inherit(article: dict, rep: dict, *, judged_at: str) -> dict:
    """접힌 기사 — 대표의 분류를 물려받는다. 해석 문장은 대표 것이 이슈에 실리므로 비운다.

    제목이 프리필터를 못 넘으면(공론화·원전 어휘가 제목에 없으면) 받되 「검토」로 표시한다. 메인의
    접기가 넓게 묶은 날이 있다 — 백필 실측: 「공론화위원회 출범」 묶음에 「기후부 출범 1년」 기사가 섞였다.
    """
    verdict = {key: rep.get(key) for key in CLASS_FIELDS}
    if lane.is_editorial_title(article.get("title")):
        verdict["article_type"] = "editorial"
    if not lane.prefilter({"title": article.get("title")})["passed"]:
        verdict["confidence"] = "low"
    verdict["reason"] = f"대표 기사 판정 상속({rep.get('hash', '')})"
    return make_record({**article, "summary": ""}, verdict, judged_at=judged_at, judge="inherited")


def recall_guard(article: dict, verdict: dict | None) -> dict | None:
    """사설·칼럼·기고의 '배경 언급' 제외는 포함·검토로 되돌린다(설계 §3-4).

    사설은 본래 여러 의제를 한 번에 다룬다 — 공론화가 한 단락이어도 여론 지형을 읽는 재료다.
    판정기는 제외 기사에 기사 유형을 적지 않으므로 제목 머리의 표지로 본다.
    """
    if not verdict or verdict.get("in_scope") or verdict.get("exclude_reason") != "background_mention":
        return verdict
    if not lane.is_editorial_title(article.get("title")):
        return verdict
    title = article.get("title_kr") or article.get("title") or ""
    return {"in_scope": True, "exclude_reason": "none", "stage": "reaction", "actor_type": "media",
            "actor_names": [], "stance": "neutral", "article_type": "editorial", "significance": 0,
            "event_key": title[:40], "event_date": "", "confidence": "low",
            "reason": "사설·칼럼 배경 언급 → 검토(결정적 규칙)", "title_kr": "", "summary": "",
            "why_important": "", "implication": ""}


def known_event_keys(archive: list[dict], *, now: datetime) -> list[str]:
    """최근 사건 이름 — 판정기가 같은 사건에 같은 이름을 쓰게 프롬프트에 싣는다. 사설·칼럼은 빼다."""
    cutoff = (now - timedelta(days=KNOWN_KEY_DAYS)).isoformat()
    rows = [r for r in archive if str(r.get("collected_at") or "") >= cutoff
            and r.get("article_type") != "editorial" and r.get("event_key")]
    rows.sort(key=lambda r: str(r.get("collected_at") or ""), reverse=True)
    return list(dict.fromkeys(r["event_key"] for r in rows))


def _identity(item: dict) -> tuple[str, str, tuple[str, str]]:
    pub = str(item.get("publisher") or item.get("domain") or "").strip().lower()
    return (str(item.get("hash") or ""), normalize_url(item.get("url") or ""),
            (title_key(item.get("title_kr") or item.get("title")), pub))


# ── 한 회차 ──────────────────────────────────────────────────────────────────
def build_pool(main_records: Iterable[dict], collected: Iterable[dict], dedicated: Iterable[dict],
               backlog: Iterable[dict], *, now_iso: str) -> list[dict]:
    pool = [from_main_record(r) for r in main_records or []]
    for article in collected or []:
        pool.append(from_collected(article, now_iso=now_iso))
        rep = str(article.get("hash") or "")
        pool.extend(from_folded(raw, rep, now_iso=now_iso) for raw in raw_sources_of(article))
    pool.extend(dedicated or [])
    pool.extend(dict(item) for item in backlog or [])
    return pool


def run(main_records: list[dict], *, collected: list[dict] | None = None,
        naver: Callable[..., dict] | None = None, fetch: Callable[[str, str], list[dict]] | None = None,
        hasher: Callable[[str], str] | None = None, judge_call: Callable | None = None, model: str = "",
        now: datetime | None = None, state_file: Path = STATE_FILE,
        archive_dir: Path = ARCHIVE_DIR, since: datetime | None = None,
        max_judge: int | None = None) -> dict:
    """한 회차. 반환값은 운영 로그용 통계.

    ``since`` 를 주면 **기간 백필**이다(tools/deliberation_backfill.py --research). 네이버는 since 까지
    페이지를 넘기고, Google 은 기간별 질의(gnews_range_sources)로 바꾸며, 기사의 수집 시각을 발행
    시각으로 찍는다 — 그래야 9월 기사가 오늘 회차가 아니라 그 무렵의 아침 브리핑 회차에 선다.
    ``max_judge`` 는 판정 상한(기본은 정기 회차 상한). 넘친 것은 정기 회차처럼 이월된다.
    """
    now = now or datetime.now(timezone.utc)
    limit = MAX_JUDGE_PER_RUN if max_judge is None else max_judge
    window = (now - since) if since is not None else WINDOW
    sources = gnews_range_sources(since, now) if since is not None else GNEWS_SOURCES
    judged_at = now.isoformat()
    state = load_state(state_file)
    archive = shared.load_archive(archive_dir)
    by_hash = {r["hash"]: r for r in archive}
    ids = [_identity(r) for r in archive]
    seen_hash = {h for h, _, _ in ids}
    seen_url = {u for _, u, _ in ids if u}
    seen_title = {t for _, _, t in ids if t[0]}

    feeds: dict[str, dict] = {}
    dedicated: list[dict] = []
    if naver is not None and hasher is not None:
        items, counts = collect_naver(naver, hasher, now=now, window=window)
        dedicated.extend(items)
        feeds.update(counts)
    if fetch is not None and hasher is not None:
        items, counts = shared.collect_feeds(fetch, hasher, now=now, sources=sources,
                                             lookback=window if since is not None else None)
        for item in items:
            item["collected_at"] = judged_at
        dedicated.extend(items)
        feeds.update(counts)
    if since is not None:
        for item in dedicated:
            item["collected_at"] = item.get("pub") or judged_at

    pool = build_pool(main_records, collected or [], dedicated, (state.get("backlog") or {}).values(),
                      now_iso=judged_at)
    for name, origin in (("공유 풀", "shared"), ("이번 회차 대표", "raw"), ("접힌 기사", "folded")):
        n = sum(1 for a in pool if a.get("origin") == origin)
        feeds.setdefault(name, shared._feed_stat()).update(fetched=n, fresh=n)

    stats = {"pool": len(pool), "seen": 0, "duplicate": 0, "prefilter_out": 0, "inherited": 0,
             "backlog_in": len(state.get("backlog") or {})}
    fresh: list[dict] = []
    for article in pool:
        digest = article.get("hash") or ""
        if not digest or digest in state["judged"]:
            stats["seen"] += 1
            continue
        h, url, tkey = _identity(article)
        if h in seen_hash or (url and url in seen_url) or (tkey[0] and tkey in seen_title):
            stats["duplicate"] += 1
            continue
        seen_hash.add(h)
        if url:
            seen_url.add(url)
        if tkey[0]:
            seen_title.add(tkey)
        fresh.append(article)

    feed_of = lambda a: a.get("feed") if a.get("origin") == "dedicated" else {  # noqa: E731
        "shared": "공유 풀", "raw": "이번 회차 대표", "folded": "접힌 기사"}.get(a.get("origin"), "이월")
    order = {"shared": 0, "raw": 1, "dedicated": 2, "folded": 3}
    first = [a for a in fresh if a.get("origin") != "folded"]
    folded = [a for a in fresh if a.get("origin") == "folded"]

    candidates: list[dict] = []
    prefilter_out: set[str] = set()
    for article in first:
        if lane.prefilter(article)["passed"]:
            candidates.append(article)
        else:
            stats["prefilter_out"] += 1
            prefilter_out.add(article["hash"])
    candidates.sort(key=lambda a: (order.get(a.get("origin"), 4), str(a.get("pub") or "")))

    records: list[dict] = []
    verdict_of: dict[str, dict] = {}
    backlog: dict[str, dict] = {}
    stats.update({"judged": 0, "included": 0, "excluded": 0, "failed": 0, "guarded": 0, "deferred": 0})
    keys = known_event_keys(archive, now=now)

    def judge_batch(batch: list[dict]) -> None:
        verdicts = lane.judge(batch, known_keys=keys, call=judge_call) if batch else []
        for article, verdict in zip(batch, verdicts):
            digest = article["hash"]
            feed = feeds.setdefault(feed_of(article), shared._feed_stat())
            guarded = recall_guard(article, verdict)
            if guarded is not verdict:
                stats["guarded"] += 1
            verdict = guarded
            if verdict is None:
                stats["failed"] += 1
                state["attempts"][digest] = int(state["attempts"].get(digest, 0)) + 1
                if state["attempts"][digest] >= MAX_ATTEMPTS:
                    state["judged"][digest] = {"at": judged_at, "in": False, "reason": "judge_failed"}
                else:
                    backlog[digest] = _queued(article, judged_at)
                continue
            stats["judged"] += 1
            feed["judged"] += 1
            verdict_of[digest] = verdict
            state["judged"][digest] = {"at": judged_at, "in": verdict["in_scope"],
                                       "reason": verdict.get("exclude_reason", ""),
                                       "feed": article.get("feed", ""), "v": lane.PROMPT_VERSION}
            if verdict["in_scope"]:
                stats["included"] += 1
                feed["included"] += 1
                record = make_record(article, verdict, judged_at=judged_at, model=model)
                records.append(record)
                by_hash[digest] = record
                if verdict.get("event_key") and verdict["event_key"] not in keys:
                    keys.insert(0, verdict["event_key"])
            else:
                stats["excluded"] += 1

    for article in candidates:
        feeds.setdefault(feed_of(article), shared._feed_stat())["candidates"] += 1
    batch = candidates[:limit]
    for article in candidates[limit:]:
        backlog[article["hash"]] = _queued(article, judged_at)
    judge_batch(batch)

    # 접힌 기사: 대표가 포함이고 **제목도 공론화 기사로 보이면** 상속. 대표가 포함이어도 제목이 프리필터를
    # 못 넘으면 스스로 판정한다 — 첫 실전 회차(2026-10-15 crawl #681)에서 메인의 수집 단계 접기가
    # 「고리 3·4호기 계속운전 심의」 12건을 「원전 공론화 의제 확정」 대표 밑에 접었다(둘 다 '계속운전'을
    # 말한다). 상속했으면 사건 하나가 엉뚱한 기사 12건을 달고 섰다. 판정은 재현율 규칙 그대로라
    # 애매하면 포함·검토가 되고, 분명한 계속운전 기사만 빠진다. 대표가 제외·후보 밖이면 스스로 판정,
    # 대표가 밀렸으면 함께 이월.
    second: list[dict] = []
    for article in folded:
        rep_hash = article.get("folded_into") or ""
        rep = by_hash.get(rep_hash)
        titled = lane.prefilter({"title": article.get("title")})["passed"]
        if rep is not None and not titled:
            second.append(article)
            continue
        if rep is not None:
            record = inherit(article, rep, judged_at=judged_at)
            records.append(record)
            by_hash[article["hash"]] = record
            state["judged"][article["hash"]] = {"at": judged_at, "in": True, "reason": "inherited",
                                                "feed": article.get("feed", ""), "v": lane.PROMPT_VERSION}
            stats["inherited"] += 1
            stats["included"] += 1
            feeds["접힌 기사"]["included"] += 1
            continue
        if rep_hash in backlog:
            backlog[article["hash"]] = _queued(article, judged_at)
            continue
        if not lane.prefilter(article)["passed"]:
            stats["prefilter_out"] += 1
            continue
        second.append(article)
    room = max(0, limit - len(batch))
    for article in second[room:]:
        backlog[article["hash"]] = _queued(article, judged_at)
    for article in second:
        feeds["접힌 기사"]["candidates"] += 1
    judge_batch(second[:room])

    stats["deferred"] = len(backlog)
    state["backlog"] = backlog
    shared.append_records(records, archive_dir)
    stats["at"] = judged_at
    stats["feeds"] = feeds
    # 실행 기록에는 0 이 아닌 숫자만 남긴다 — 피드가 40개라 그대로 두면 상태 파일이 회차마다 부푼다.
    compact = {name: {k: v for k, v in (row or {}).items() if (k == "covered" and v is False)
                      or (k != "covered" and v)}
               for name, row in feeds.items()}
    state["runs"].append({**{k: v for k, v in stats.items() if k != "feeds"}, "feeds": compact})
    save_state(state, state_file, now=now)
    return stats


def format_stats(stats: dict) -> str:
    feeds = stats.get("feeds") or {}
    lines = [
        f"[공론화 레인] 풀 {stats.get('pool', 0)} · 기판정 {stats.get('seen', 0)} · 중복 {stats.get('duplicate', 0)}"
        f" · 프리필터 탈락 {stats.get('prefilter_out', 0)} · 판정 {stats.get('judged', 0)}"
        f" → 포함 {stats.get('included', 0)}(상속 {stats.get('inherited', 0)}) / 제외 {stats.get('excluded', 0)}"
        f" · 사설 되살림 {stats.get('guarded', 0)} · 실패 {stats.get('failed', 0)}"
        f" · 이월 {stats.get('deferred', 0)}(지난 회차에서 {stats.get('backlog_in', 0)})"]
    for name, v in feeds.items():
        v = v or {}
        mark = " ✗실패" if v.get("failed") else (" ✗창 미달" if v.get("covered") is False else "")
        total = f" · 전체 {v['total']}" if v.get("total") else ""
        lines.append(f"  [공론화 피드] {name}: 받음 {v.get('fetched', 0)}{total} · 창 안 {v.get('fresh', 0)}"
                     f" · 후보 {v.get('candidates', 0)} · 판정 {v.get('judged', 0)} · 포함 {v.get('included', 0)}{mark}")
    return "\n".join(lines)
