"""해외 AI 레인 — 수집 회차마다 판정해서 별도 DB(ai_archive/)에 쌓는다.

어디서 도나
-----------
news_bot.main() 이 메인 아카이브 적재를 끝낸 **바로 뒤**에 부른다. 그 시점의 레코드는
이번 회차에 큐레이션된 기사 전부다 — noise 로 버려진 기사까지 한글 제목·요약을 달고
들어 있다. 이것이 "공유 후보 풀"이다. 여기에 AI 전용 피드(``AI_SOURCES``)에서 가져온
기사를 더한다. 전용 피드 기사는 **메인 큐에 들어가지 않는다** — 아침 브리핑이 바뀌면
안 되기 때문이다.

무엇을 하나
-----------
  1. 이미 판정한 기사(``ai_state.json``)·이미 DB 에 있는 기사(hash·주소·제목)를 뺀다.
  2. ``ai_lane.prefilter`` — AI 어휘 ∧ 원자력 어휘. 넓게.
  3. 전용 피드 기사는 본문을 받아 판정 재료로만 쓴다(저장하지 않는다 — 저작권).
  4. ``ai_lane.judge`` — Gemini 한 호출에 8건. 포함·제외와 분류, 포함이면 한국어 문장.
  5. 포함 → ``ai_archive/YYYY-MM.jsonl``. 제외 → 상태 파일에 사유만 남긴다(소급 셈용).
     판정 **실패**는 아무 표식도 남기지 않는다 — 다음 회차가 다시 묻는다. 실패를 '제외'로
     적으면 메인 레인의 sent 마킹 사고(noise 즉시 14일 봉인)와 같은 꼴이 된다.
     같은 기사가 세 번 실패하면 그때 포기한다.

가드레일
  · news_bot 을 import 하지 않는다. RSS 수집 함수는 호출자가 넘긴다(순환·secret 회피).
  · 어떤 예외도 크롤을 멈추지 않는다. 호출부도 try 로 감싼다.
  · 메인 상태(sent.json·curated.json·digest_queue.json·archive/)는 읽기만 한다.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote_plus

import ai_lane
from data_quality import normalize_url, source_profile, title_key

BASE = Path(__file__).parent
ARCHIVE_DIR = Path(os.environ.get("AI_ARCHIVE_DIR") or (BASE / "ai_archive"))
STATE_FILE = Path(os.environ.get("AI_STATE_FILE") or (BASE / "ai_state.json"))
RECORD_VERSION = 1
KST = timezone(timedelta(hours=9))

# 한 회차에 묻는 상한. 판정 통과는 하루 몇 건이지만 프리필터는 수십 건이 걸린다.
# 8건 배치 6회 = 48건. 넘친 것은 seen 표식이 없으니 다음 회차가 이어서 묻는다.
MAX_JUDGE_PER_RUN = int(os.environ.get("AI_LANE_MAX_JUDGE") or 48)
MAX_BODY_PER_RUN = 24
MAX_ATTEMPTS = 3
JUDGED_TTL_DAYS = 60
RUN_LOG_KEEP = 40


def _gnews(query: str, hl: str, gl: str, ceid: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}"


# ── 전용 피드 ────────────────────────────────────────────────────────────────
# Google News 검색은 '관련도순'이라 when: 연산자 없이는 몇 주 지난 기사가 섞인다
# (news_bot RSS_SOURCES 주석 실측). 괄호 키워드를 무시하는 매체가 있어도 해롭지 않다 —
# 모든 항목이 프리필터와 판정을 다시 거친다.
#
# 0단계 gold 의 진짜 사례 25건 중 11건이 NEI·ANS 에서 왔다. ANS 는 메인 RSS 가 이미
# 받으므로 공유 풀로 들어온다. NEI 는 직접 RSS 가 Cloudflare 403 이라 site: 우회다.
#
# 4단계(2026-10-14) 추가 근거 — 지난 30일 언어권별 소급 조사(docs §12): 우리가 놓친 사건
# 7건 중 4건이 러시아어(로사톰·루푸르), 1건이 중국어(CGN), 2건이 규제기관·국제기구(NRC
# 워크숍·FANR IAEA 혁신상)였다. 메인 아카이브 30일 10,974건에 러시아어·중국어 기사는 0건.
#   · ``kind``: "gnews"(기본) 는 when: 창이 있어야 한다. "rss" 는 매체 직접 피드 — 주소를
#     이 환경에서 열어 확인할 수 없어(프록시 차단) 넣고 첫 크롤 로그로 확인한다. 실패해도
#     레인은 멈추지 않는다(collect_feeds).
#   · ``lang``: 통계 라벨. 판정 프롬프트는 언어와 무관하다.
AI_SOURCES: list[dict] = [
    {"name": "AI·Google 원전 AI(EN)", "lang": "en",
     "url": _gnews('(nuclear OR reactor) ("artificial intelligence" OR "machine learning" '
                   'OR "generative AI" OR "AI") -"data center" -"data centers" when:1d',
                   "en-US", "US", "US:en")},
    {"name": "AI·Google 원전 디지털(EN)", "lang": "en",
     "url": _gnews('(nuclear OR reactor) (robot OR robotic OR drone OR "digital twin" '
                   'OR "virtual reality" OR "immersive" OR "I&C" OR "instrumentation and control") '
                   '-attack -strike -Zaporizhzhia when:1d', "en-US", "US", "US:en")},
    {"name": "AI·Google 원전 AI 벤더·제품(EN)", "lang": "en",
     "url": _gnews('("Atomic Canyon" OR Nuclearn OR "Blue Wave AI" OR NIVA OR AtomAssist '
                   'OR AtomMind OR Metroscope OR "PRO-AID" OR "nuclear AI") nuclear when:2d',
                   "en-US", "US", "US:en")},
    # 질의는 **200자 미만**으로 둔다. 실측(crawl #673, 2026-10-14): 278자·213자 질의는 when: 창이
    # 무시돼 100건·67건이 전부 3일 넘은 기사였고(창 안 0), 184자 이하 질의는 창이 지켜졌다.
    {"name": "AI·Google 사업자 AI(EN)", "lang": "en",
     "url": _gnews('(Constellation OR "Duke Energy" OR TVA OR "Southern Nuclear" OR Dominion '
                   'OR "Ontario Power Generation" OR "Bruce Power" OR EDF) nuclear '
                   '("AI" OR "artificial intelligence") when:1d', "en-US", "US", "US:en")},
    {"name": "AI·Google 제작사 AI(EN)", "lang": "en",
     "url": _gnews('(Westinghouse OR Framatome OR "GE Vernova" OR "Rolls-Royce SMR" OR NuScale '
                   'OR X-energy OR Kairos) nuclear ("AI" OR "artificial intelligence") when:1d',
                   "en-US", "US", "US:en")},
    {"name": "AI·Google 규제·연구소 AI(EN)", "lang": "en",
     "url": _gnews('(NRC OR IAEA OR "OECD NEA" OR "Idaho National Laboratory" OR "Argonne" '
                   'OR "Oak Ridge" OR EPRI OR INPO OR WANO) nuclear '
                   '("artificial intelligence" OR "machine learning" OR "AI") when:2d',
                   "en-US", "US", "US:en")},
    # 4단계 A2 — 미국 밖 규제기관·운영사. 기존 검색에 NRC 외 규제기관 이름이 없었다.
    {"name": "AI·Google 해외 규제기관 AI(EN) 1", "lang": "en",
     "url": _gnews('(FANR OR ENEC OR Barakah OR ONR OR CNSC OR ASNR) nuclear '
                   '("AI" OR "artificial intelligence" OR "digital twin") when:3d', "en-US", "US", "US:en")},
    {"name": "AI·Google 해외 규제기관 AI(EN) 2", "lang": "en",
     "url": _gnews('(STUK OR "SSM" OR "Nuclear Regulation Authority" OR CNCAN OR Rosatom) nuclear '
                   '("AI" OR "artificial intelligence" OR "digital twin") when:3d', "en-US", "US", "US:en")},
    {"name": "AI·Google NEI AI", "lang": "en",
     "url": _gnews('site:neimagazine.com (AI OR "artificial intelligence" OR "machine learning" '
                   'OR "digital twin" OR robot OR drone) when:3d', "en-US", "US", "US:en")},
    {"name": "AI·Google 원전 AI(JA)", "lang": "ja",
     "url": _gnews('(原子力 OR 原発) (AI OR 人工知能 OR 機械学習 OR ロボット OR デジタルツイン) when:1d',
                   "ja", "JP", "JP:ja")},
    {"name": "AI·Google 원전 AI(FR)", "lang": "fr",
     "url": _gnews('nucléaire ("intelligence artificielle" OR IA OR robotique OR "jumeau numérique") when:1d',
                   "fr", "FR", "FR:fr")},
    # 4단계 A1 — 러시아어·중국어. Google 은 2022년부터 RT 등 러시아 국영매체를 Google 뉴스에서
    # 뺐으므로 TASS·RIA 는 안 나올 수 있다. 민간 매체(atomic-energy.ru·ComNews·Vedomosti)는
    # 지난달 루푸르 건을 10곳 넘게 실었다. 중국은 첫 보도가 회사 위챗이라 행사급만 잡힌다.
    {"name": "AI·Google 원전 AI(RU)", "lang": "ru",
     "url": _gnews('(АЭС OR Росатом OR Росэнергоатом OR атомной) (ИИ OR "искусственный интеллект" '
                   'OR нейросеть OR "машинное обучение" OR "цифровой двойник" OR робот) when:2d',
                   "ru", "RU", "RU:ru")},
    {"name": "AI·Google 원전 AI(ZH)", "lang": "zh",
     "url": _gnews('(核电 OR 核能 OR 中广核 OR 中核) (人工智能 OR 大模型 OR 机器学习 OR 智能体 '
                   'OR 数字孪生 OR 机器人) when:2d', "zh-CN", "CN", "CN:zh-Hans")},
    # NRC·atomic-energy.ru 는 직접 RSS 주소가 틀렸다(crawl #673: 받음 0). NEI 처럼 site: 우회.
    # atomic-energy.ru 는 RU Google 피드가 이미 받는다(루푸르 건이 그 경로로 들어왔다) — 보강용.
    {"name": "AI·Google NRC AI", "lang": "en",
     "url": _gnews('site:nrc.gov (AI OR "artificial intelligence" OR "machine learning") when:7d',
                   "en-US", "US", "US:en")},
    {"name": "AI·Google Атомная энергия 2.0", "lang": "ru",
     "url": _gnews('site:atomic-energy.ru (ИИ OR "искусственный интеллект" OR цифровой OR робот) when:3d',
                   "ru", "RU", "RU:ru")},
    # 직접 RSS. 열리는 것은 확인했다(crawl #673: 받음 15). 보도자료가 드물어 창 안 0 인 날이 많다.
    {"name": "AI·IAEA 보도자료", "lang": "en", "kind": "rss",
     "url": "https://www.iaea.org/feeds/pressalerts"},
]
# 기간 백필용 Google 뉴스 질의 (tools/ai_lane_research_backfill.py). 정기 피드(AI_SOURCES)는 when: 창이라
# 지난 기간을 못 뒤진다 — 같은 주제를 after:/before: 로 잘라 다시 묻는다. 질의는 **기간을 붙여도 190자 이하**로
# 둔다(crawl #673 실측: 200자 넘는 질의는 기간 조건이 무시됐다). 그래서 정기 피드 질의를 그대로 쓰지 않고
# 짧게 다시 썼다. 간격(일)은 기사가 몰리는 질의일수록 짧다 — Google 뉴스 RSS 는 질의당 100건 안팎을 준다.
BACKFILL_QUERIES: tuple[tuple[str, str, str, int], ...] = (
    ("EN 원전 AI", "en", 'nuclear ("artificial intelligence" OR "machine learning" OR "generative AI")', 3),
    ("EN 원전 AI 약어", "en", '(nuclear OR reactor) AI -"data center" -"data centers"', 3),
    ("EN 원전 디지털", "en", 'nuclear (robot OR drone OR "digital twin" OR "virtual reality" OR immersive)', 7),
    ("EN 원전 계측제어", "en", 'nuclear ("I&C" OR "instrumentation and control" OR digitalization)', 7),
    ("EN 원전 AI 제품", "en", '("Atomic Canyon" OR Nuclearn OR "Blue Wave AI" OR NIVA OR AtomAssist OR Metroscope) nuclear', 14),
    ("EN 사업자 AI", "en", '(Constellation OR "Duke Energy" OR TVA OR "Southern Nuclear" OR Dominion OR EDF) nuclear AI', 7),
    ("EN 제작사 AI", "en", '(Westinghouse OR Framatome OR "GE Vernova" OR NuScale OR "Rolls-Royce SMR" OR X-energy) nuclear AI', 7),
    ("EN 규제·연구소 AI", "en", '(NRC OR IAEA OR "OECD NEA" OR INL OR Argonne OR "Oak Ridge" OR EPRI OR INPO OR WANO) nuclear AI', 7),
    ("EN 해외 규제기관 AI", "en", '(FANR OR ENEC OR ONR OR CNSC OR ASNR OR STUK OR Rosatom) nuclear AI', 7),
    ("EN NEI", "en", 'site:neimagazine.com (AI OR "machine learning" OR "digital twin" OR robot)', 14),
    ("EN NRC", "en", 'site:nrc.gov (AI OR "artificial intelligence")', 30),
    ("JA 원전 AI", "ja", "(原子力 OR 原発) (AI OR 人工知能 OR 機械学習 OR ロボット OR デジタルツイン)", 7),
    ("FR 원전 AI", "fr", 'nucléaire ("intelligence artificielle" OR IA OR robotique OR "jumeau numérique")', 7),
    ("RU 원전 AI", "ru", '(АЭС OR Росатом OR атомной) (ИИ OR "искусственный интеллект" OR нейросеть OR "цифровой двойник" OR робот)', 7),
    ("RU 아톰에네르기야", "ru", 'site:atomic-energy.ru (ИИ OR "искусственный интеллект" OR цифровой OR робот)', 7),
    ("ZH 원전 AI", "zh", "(核电 OR 核能 OR 中广核 OR 中核) (人工智能 OR 大模型 OR 机器学习 OR 数字孪生 OR 机器人)", 7),
)
_GNEWS_LOCALE = {"en": ("en-US", "US", "US:en"), "ja": ("ja", "JP", "JP:ja"), "fr": ("fr", "FR", "FR:fr"),
                 "ru": ("ru", "RU", "RU:ru"), "zh": ("zh-CN", "CN", "CN:zh-Hans")}


def backfill_query(query: str, start, end) -> str:
    return f"{query} after:{start.isoformat()} before:{end.isoformat()}"


def backfill_sources(since, until) -> list[dict]:
    """[since, until] 날짜(date)를 질의별 간격으로 잘라 기간 질의 피드 목록을 만든다."""
    out: list[dict] = []
    for label, lang, query, step in BACKFILL_QUERIES:
        start = since
        while start <= until:
            end = min(start + timedelta(days=step), until + timedelta(days=1))
            out.append({"name": f"AI 백필 {label} {start.isoformat()}", "lang": lang, "label": label,
                        "url": _gnews(backfill_query(query, start, end), *_GNEWS_LOCALE[lang])})
            start = end
    return out


# 전용 피드의 수집 창. when:1d 를 넘는 오래된 항목은 버린다(관련도순 대비).
FEED_LOOKBACK = timedelta(days=3)
# 기사 주소에 날짜가 박혀 있고(/2026/06/ 처럼) 그보다 오래됐으면 버린다. 실측(2026-10-14 두 번째
# 실전 회차): 6월 기사(energynow.com/2026/06/…)를 Google 뉴스가 오늘 다시 띄워 pub 이 오늘로
# 찍혔고, 그대로 오늘 회차에 들어갔다.
URL_DATE_LOOKBACK = timedelta(days=30)
_URL_DATE = re.compile(r"/((?:20)\d{2})[/-](\d{2})(?:[/-](\d{2}))?(?=[/-])")


def url_date(url: str) -> datetime | None:
    """주소 경로의 연/월(/일) → datetime(UTC). 일이 없으면 그 달 1일. 없거나 말이 안 되면 None."""
    m = _URL_DATE.search(url or "")
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def stale_by_url(url: str, now: datetime, lookback: timedelta = URL_DATE_LOOKBACK) -> bool:
    stamped = url_date(url)
    return stamped is not None and stamped < now - lookback


# ── 상태 ─────────────────────────────────────────────────────────────────────
def load_state(path: Path = STATE_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("judged", {})
            data.setdefault("attempts", {})
            data.setdefault("runs", [])
            return data
    except (OSError, ValueError):
        pass
    return {"judged": {}, "attempts": {}, "runs": []}


def save_state(state: dict, path: Path = STATE_FILE, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=JUDGED_TTL_DAYS)).isoformat()
    state["judged"] = {h: v for h, v in state.get("judged", {}).items()
                       if str((v or {}).get("at") or "") >= cutoff}
    state["attempts"] = {h: n for h, n in state.get("attempts", {}).items()
                         if h not in state["judged"] and n < MAX_ATTEMPTS}
    state["runs"] = state.get("runs", [])[-RUN_LOG_KEEP:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")


# ── 아카이브 ─────────────────────────────────────────────────────────────────
def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_archive(archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(archive_dir.glob("*.jsonl")):
        rows.extend(_read_jsonl(path))
    # union 병합·재실행으로 같은 줄이 두 번 있을 수 있다 — hash 로 한 번만.
    seen: set[str] = set()
    out = []
    for row in rows:
        digest = str(row.get("hash") or "")
        if not digest or digest in seen:
            continue
        seen.add(digest)
        out.append(row)
    return out


def archive_identities(rows: Iterable[dict]) -> dict[str, set[str]]:
    ids = {"hashes": set(), "urls": set(), "titles": set()}
    for row in rows:
        ids["hashes"].add(str(row.get("hash") or ""))
        for key in ("url", "resolved_url"):
            if row.get(key):
                ids["urls"].add(normalize_url(row[key]))
        for key in ("title", "title_kr"):
            if row.get(key):
                ids["titles"].add(title_key(row[key]))
    for bucket in ids.values():
        bucket.discard("")
    return ids


def append_records(records: list[dict], archive_dir: Path = ARCHIVE_DIR) -> int:
    if not records:
        return 0
    archive_dir.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict]] = {}
    for record in records:
        month = str(record.get("archived_at") or "")[:7] or datetime.now(timezone.utc).strftime("%Y-%m")
        by_month.setdefault(month, []).append(record)
    for month, rows in by_month.items():
        with (archive_dir / f"{month}.jsonl").open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(records)


# ── 입력 정리 ────────────────────────────────────────────────────────────────
def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def from_main_record(record: dict) -> dict:
    """메인 아카이브 레코드 → 레인 입력. 메인 큐레이션 문장을 그대로 들고 간다."""
    return {
        "hash": record.get("hash", ""),
        "origin": "shared",
        "title": record.get("title", ""),
        "title_kr": record.get("title_kr", ""),
        "summary": record.get("summary", ""),
        "detail": record.get("detail", ""),
        "why_important": record.get("why_important", ""),
        "implication": record.get("implication", ""),
        "url": record.get("url", ""),
        "resolved_url": record.get("resolved_url", ""),
        "domain": record.get("domain", ""),
        "publisher": record.get("publisher", ""),
        "pub": record.get("pub", ""),
        "scope": record.get("scope", ""),
        "countries": record.get("countries") if isinstance(record.get("countries"), list) else [],
        "main_importance": record.get("importance", ""),
        # 회차는 이 시각을 따른다. 레인이 한도 때문에 하루 늦게 판정해도 기사는
        # 메인이 수집한 날의 회차에 들어간다.
        "collected_at": record.get("archived_at", ""),
    }


def from_feed_item(item: dict, hasher: Callable[[str], str], source: dict | None = None) -> dict:
    """전용 피드 항목 → 레인 입력. 제목뿐이라 판정 전에 본문을 붙인다."""
    domain = item.get("publisher_domain") or ""
    source = source or {}
    return {
        "hash": hasher(item.get("raw_link") or item.get("link") or ""),
        "origin": "dedicated",
        "feed": source.get("name", ""),
        "lang": source.get("lang", ""),
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "link": item.get("link", ""),
        "url": item.get("link", ""),
        "domain": domain,
        "publisher": item.get("publisher", ""),
        "pub": _iso(item.get("pub")),
        "scope": "",
        "countries": [],
        "main_importance": "",
    }


def _feed_stat() -> dict:
    return {"fetched": 0, "fresh": 0, "stale_url": 0, "failed": 0, "candidates": 0,
            "judged": 0, "included": 0}


def collect_feeds(fetch: Callable[[str, str], list[dict]], hasher: Callable[[str], str],
                  *, now: datetime | None = None,
                  sources: list[dict] | None = None,
                  lookback: timedelta | None = None) -> tuple[list[dict], dict[str, dict]]:
    """전용 피드 전부 → (레인 입력 목록, 피드별 통계).

    피드별 통계는 4단계부터 로그와 ``ai_state.json`` 의 runs 에 함께 남는다 — 어느 경로가
    실제로 사건을 내는지 2주 뒤에 세어 보기 위해서다(docs §12). 피드 하나의 실패는 그 피드의
    ``failed`` 에만 적히고 나머지는 계속 간다.
    """
    now = now or datetime.now(timezone.utc)
    # lookback 은 공론화 레인의 기간 백필(after:/before: 로 자른 Google 뉴스 질의)이 넘긴다.
    # 기본값은 그대로다 — 정기 회차는 3일 창, 주소 날짜는 30일.
    window = lookback or FEED_LOOKBACK
    url_window = max(window, URL_DATE_LOOKBACK)
    out: list[dict] = []
    counts: dict[str, dict] = {}
    for src in sources if sources is not None else AI_SOURCES:
        stat = counts[src["name"]] = _feed_stat()
        try:
            items = fetch(src["url"], src["name"]) or []
        except Exception as exc:  # noqa: BLE001 — 피드 하나가 레인을 멈추면 안 된다
            print(f"  ! [AI] {src['name']} 실패: {type(exc).__name__}")
            stat["failed"] = 1
            items = []
        stat["fetched"] = len(items)
        for item in items:
            pub = item.get("pub")
            if isinstance(pub, datetime) and pub < now - window:
                continue
            if stale_by_url(item.get("raw_link") or item.get("link") or "", now, url_window):
                stat["stale_url"] += 1
                continue
            stat["fresh"] += 1
            out.append(from_feed_item(item, hasher, src))
    return out, counts


MAIN_ARCHIVE_DIR = BASE / "archive"
SHARED_LOOKBACK = timedelta(days=3)


def recent_main_records(*, now: datetime | None = None, archive_dir: Path = MAIN_ARCHIVE_DIR,
                        lookback: timedelta = SHARED_LOOKBACK) -> list[dict]:
    """메인 아카이브에서 최근 며칠치. **이번 회차 레코드만 받지 않는 이유**: Gemini 한도로
    레인이 한 회차를 건너뛰면 그 회차 기사는 다시 오지 않는다. 최근 3일을 매번 보면
    판정하지 않은 기사는 다음 회차가 이어서 묻는다(이미 판정한 것은 상태가 거른다)."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - lookback).isoformat()
    months = {now.strftime("%Y-%m"), (now - lookback).strftime("%Y-%m")}
    rows = []
    for month in sorted(months):
        for row in _read_jsonl(archive_dir / f"{month}.jsonl"):
            if str(row.get("archived_at") or "") >= cutoff:
                rows.append(row)
    return rows


# ── 판정 결과 → 레코드 ───────────────────────────────────────────────────────
def make_record(article: dict, verdict: dict, *, judged_at: str, model: str = "",
                judge: str = "gemini") -> dict:
    profile = source_profile(article.get("domain") or "", article.get("publisher") or "")
    main_title_kr = article.get("title_kr") or ""
    return {
        "v": RECORD_VERSION,
        "lane": "ai",
        "origin": article.get("origin", ""),
        "hash": article.get("hash", ""),
        "archived_at": judged_at,
        "collected_at": article.get("collected_at") or judged_at,
        "pub": article.get("pub", ""),
        "url": article.get("url", ""),
        "resolved_url": article.get("resolved_url", ""),
        "domain": profile.get("domain") or article.get("domain", ""),
        "publisher": profile.get("publisher") or article.get("publisher", ""),
        "source_type": profile.get("source_type", ""),
        "evidence_role": profile.get("evidence_role", ""),
        "source_tier": profile.get("source_tier", 3),
        "title": article.get("title", ""),
        # 사실 문장(제목·요약·기사 내용)은 메인 큐레이션 것이 우선이다 — 메인 헤드라인
        # 규칙과 결정적 정리기를 이미 거쳤다. 해석 문장(왜 중요한가·시사점)은 **레인 것이
        # 우선**이다. 메인 해석은 정책·투자 관점이라 "원전 AI 도입 흐름에서 무슨 뜻인가"를
        # 말하지 않고, noise 기사에서는 아예 비어 있다.
        "title_kr": main_title_kr or verdict.get("title_kr") or article.get("title", ""),
        "summary": article.get("summary") or verdict.get("summary", ""),
        "detail": article.get("detail", ""),
        "why_important": verdict.get("why_important") or article.get("why_important", ""),
        "implication": verdict.get("implication") or article.get("implication", ""),
        "countries": verdict.get("countries") or article.get("countries") or [],
        "main_importance": article.get("main_importance", ""),
        "main_scope": article.get("scope", ""),
        "feed": article.get("feed", ""),
        "lang": article.get("lang", ""),
        "tech_scope": verdict.get("tech_scope") or ("digital" if verdict.get("ai_tech") in ai_lane.DIGITAL_AI_TECH else "ai"),
        "application_area": verdict.get("application_area", ""),
        "ai_tech": verdict.get("ai_tech", "other"),
        "actor_type": verdict.get("actor_type", "other"),
        "actor_names": verdict.get("actor_names", []),
        "maturity": verdict.get("maturity", "announced"),
        "adoption_significance": verdict.get("adoption_significance", 0),
        "reason": verdict.get("reason", ""),
        "judge": judge,
        "judge_model": model,
        "prompt_version": ai_lane.PROMPT_VERSION,
    }


def domestic_guard(article: dict, verdict: dict | None) -> dict | None:
    """국내 주체 기사는 판정과 무관하게 포함하지 않는다(사용자 결정: 해외만).

    메인 큐레이션의 scope 는 '기사를 쓴 매체'가 아니라 '기사가 다루는 대상'으로 판정된다
    (news_bot 프롬프트 B-2). 그래서 해외 사례를 다룬 국내 매체 기사는 overseas 이고,
    여기서 걸리는 것은 한국이 주체·무대인 기사뿐이다.

    실측(2026-10-14 첫 실전 회차): 「현대차·HD현대, AI 자율운항 선박·SMR 추진 선박 협력」
    을 판정기가 포함으로 냈다(국가 KR·US). 메인은 이 기사를 kr 로 봤다.
    """
    if not verdict or not verdict.get("in_scope"):
        return verdict
    countries = {str(c).upper() for c in verdict.get("countries") or [] if c}
    if article.get("scope") == "kr" or countries == {"KR"}:
        return {"in_scope": False, "exclude_reason": "domestic", "reason": "국내 주체(결정적 규칙)"}
    return verdict


def datacenter_guard(article: dict, verdict: dict | None) -> dict | None:
    """프리필터가 '데이터센터 전력' 표시를 붙인 기사는 판정이 포함이라 해도 넣지 않는다.

    실측(2026-10-14 두 번째 실전 회차): 「AtkinsRealis, AI 데이터센터 전력 공급용 원전 기술
    미국 인허가 추진」을 판정기가 포함으로 냈다 — 원전이 AI 를 쓰는 기사가 아니라 AI 에
    전기를 대는 기사다. 0단계 gold 226건으로 미리 따져 보면 이 표시가 붙은 기사는 제외 37건·
    포함 0건이라, 이 규칙으로 잃는 진짜 사례는 없었다. 그래도 사유를 따로 남겨 되돌릴 수 있게 한다.
    """
    if not verdict or not verdict.get("in_scope"):
        return verdict
    marks = (article.get("_prefilter") or {}).get("hints") or []
    if "datacenter_power" in marks:
        return {"in_scope": False, "exclude_reason": "datacenter_power",
                "reason": "데이터센터 전력 표시(결정적 규칙)"}
    return verdict


# ── 한 회차 ──────────────────────────────────────────────────────────────────
def select_candidates(pool: list[dict], state: dict, identities: dict[str, set[str]]) -> tuple[list[dict], dict]:
    stats = {"pool": len(pool), "seen": 0, "duplicate": 0, "prefilter_out": 0,
             "conflict_drop": 0, "candidates": 0}
    chosen: list[dict] = []
    titles: set[str] = set()
    for article in pool:
        digest = article.get("hash") or ""
        if not digest or digest in state["judged"]:
            stats["seen"] += 1
            continue
        tkey = title_key(article.get("title"))
        if (digest in identities["hashes"]
                or normalize_url(article.get("url") or "") in identities["urls"]
                or (tkey and (tkey in identities["titles"] or tkey in titles))):
            stats["duplicate"] += 1
            continue
        verdict = ai_lane.prefilter(article)
        if not verdict["passed"]:
            # 탈락은 상태에 적지 않는다. 정규식이라 다시 걸러도 거의 공짜고, 적으면
            # 상태 파일이 회차마다 수백 줄씩 자란다(공유 풀은 최근 3일 전량이다).
            stats["conflict_drop" if verdict["dropped"] == "conflict" else "prefilter_out"] += 1
            continue
        article["_prefilter"] = verdict  # 판정 뒤 가드(datacenter_guard)가 읽는다. 저장되지 않는다.
        titles.add(tkey)
        chosen.append(article)
    # 공유 풀(메인 큐레이션 문장이 있는 쪽)을 먼저 묻는다 — 상한에 걸리면 전용 피드가 다음 회차로 밀린다.
    chosen.sort(key=lambda a: 0 if a.get("origin") == "shared" else 1)
    stats["candidates"] = len(chosen)
    return chosen, stats


def run(main_records: list[dict], *, fetch: Callable[[str, str], list[dict]] | None = None,
        hasher: Callable[[str], str] | None = None,
        body_fetcher: Callable[[list[dict]], tuple[dict, dict]] | None = None,
        judge_call: Callable | None = None, model: str = "",
        now: datetime | None = None, state_file: Path = STATE_FILE,
        archive_dir: Path = ARCHIVE_DIR) -> dict:
    """한 회차. 반환값은 운영 로그용 통계."""
    now = now or datetime.now(timezone.utc)
    judged_at = now.isoformat()
    state = load_state(state_file)
    archive = load_archive(archive_dir)
    identities = archive_identities(archive)

    pool = [from_main_record(r) for r in main_records or []]
    feed_counts: dict[str, dict] = {"공유 풀": _feed_stat()}
    feed_counts["공유 풀"]["fetched"] = feed_counts["공유 풀"]["fresh"] = len(pool)
    if fetch is not None and hasher is not None:
        feed_items, counts = collect_feeds(fetch, hasher, now=now)
        pool.extend(feed_items)
        feed_counts.update(counts)

    candidates, stats = select_candidates(pool, state, identities)
    batch = candidates[:MAX_JUDGE_PER_RUN]
    stats["deferred"] = len(candidates) - len(batch)
    feed_of = lambda a: a.get("feed") or "공유 풀"  # noqa: E731
    for article in candidates:
        feed_counts.setdefault(feed_of(article), _feed_stat())["candidates"] += 1

    dedicated = [a for a in batch if a.get("origin") == "dedicated"]
    stats["body"] = {"attempted": 0, "ok": 0}
    if dedicated and body_fetcher is not None:
        try:
            targets = dedicated[:MAX_BODY_PER_RUN]
            bodies, _ = body_fetcher(targets)
            stats["body"] = {"attempted": len(targets), "ok": sum(1 for a in targets if bodies.get(a["hash"]))}
            for article in dedicated:
                body = bodies.get(article["hash"])
                if body:
                    article["body_excerpt"] = body[:1500]
                if article.get("resolved_url"):
                    article["url"] = article["resolved_url"]
        except Exception as exc:  # noqa: BLE001 — 본문 없이도 제목으로 판정한다
            print(f"  ! [AI] 본문 수집 실패: {type(exc).__name__}")

    verdicts = ai_lane.judge(batch, call=judge_call) if batch else []
    records = []
    stats.update({"judged": 0, "included": 0, "excluded": 0, "failed": 0, "guarded": 0})
    for article, verdict in zip(batch, verdicts):
        digest = article["hash"]
        guarded = datacenter_guard(article, domestic_guard(article, verdict))
        if guarded is not verdict and guarded is not None:
            stats["guarded"] += 1
        verdict = guarded
        if verdict is None:
            stats["failed"] += 1
            state["attempts"][digest] = int(state["attempts"].get(digest, 0)) + 1
            if state["attempts"][digest] >= MAX_ATTEMPTS:
                state["judged"][digest] = {"at": judged_at, "in": False, "reason": "judge_failed"}
            continue
        stats["judged"] += 1
        feed_counts.setdefault(feed_of(article), _feed_stat())["judged"] += 1
        state["judged"][digest] = {"at": judged_at, "in": verdict["in_scope"],
                                   "reason": verdict.get("exclude_reason", ""),
                                   "feed": article.get("feed", ""), "v": ai_lane.PROMPT_VERSION}
        if verdict["in_scope"]:
            stats["included"] += 1
            feed_counts[feed_of(article)]["included"] += 1
            records.append(make_record(article, verdict, judged_at=judged_at, model=model))
        else:
            stats["excluded"] += 1
    append_records(records, archive_dir)
    stats["at"] = judged_at
    stats["feeds"] = feed_counts
    state["runs"].append(dict(stats))
    save_state(state, state_file, now=now)
    return stats


def format_stats(stats: dict) -> str:
    feeds = stats.get("feeds") or {}
    feed_total = sum(int((v or {}).get("fresh") or 0) for k, v in feeds.items() if k != "공유 풀")
    body = stats.get("body") or {}
    head = (f"[AI 레인] 풀 {stats.get('pool', 0)}(전용 피드 {feed_total}) · 기판정 {stats.get('seen', 0)}"
            f" · 중복 {stats.get('duplicate', 0)} · 프리필터 탈락 {stats.get('prefilter_out', 0)}"
            f" · 공격문맥 탈락 {stats.get('conflict_drop', 0)}"
            f" · 판정 {stats.get('judged', 0)} → 포함 {stats.get('included', 0)}"
            f" / 제외 {stats.get('excluded', 0)}(가드 {stats.get('guarded', 0)}) · 실패 {stats.get('failed', 0)}"
            f" · 다음 회차로 {stats.get('deferred', 0)}"
            f" · 본문 {body.get('ok', 0)}/{body.get('attempted', 0)}")
    lines = [head]
    for name, v in feeds.items():
        v = v or {}
        mark = " ✗실패" if v.get("failed") else ""
        lines.append(f"  [AI 피드] {name}: 받음 {v.get('fetched', 0)} · 창 안 {v.get('fresh', 0)}"
                     f" · 주소 날짜 탈락 {v.get('stale_url', 0)} · 후보 {v.get('candidates', 0)}"
                     f" · 판정 {v.get('judged', 0)} · 포함 {v.get('included', 0)}{mark}")
    return "\n".join(lines)
