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
AI_SOURCES: list[dict] = [
    {"name": "AI·Google 원전 AI(EN)",
     "url": _gnews('(nuclear OR reactor) ("artificial intelligence" OR "machine learning" '
                   'OR "generative AI" OR "AI") -"data center" -"data centers" when:1d',
                   "en-US", "US", "US:en")},
    {"name": "AI·Google 원전 AI 벤더(EN)",
     "url": _gnews('("Atomic Canyon" OR Nuclearn OR "Blue Wave AI" OR "NIVA" nuclear '
                   'OR "nuclear AI") when:2d', "en-US", "US", "US:en")},
    {"name": "AI·Google 사업자·제작사 AI(EN)",
     "url": _gnews('(Constellation OR "Duke Energy" OR TVA OR "Southern Nuclear" OR Dominion '
                   'OR "Ontario Power Generation" OR "Bruce Power" OR EDF OR Westinghouse '
                   'OR Framatome OR "GE Vernova" OR "Rolls-Royce SMR" OR NuScale) nuclear '
                   '("AI" OR "artificial intelligence" OR "machine learning") when:1d',
                   "en-US", "US", "US:en")},
    {"name": "AI·Google 규제·연구소 AI(EN)",
     "url": _gnews('(NRC OR IAEA OR "OECD NEA" OR "Idaho National Laboratory" OR "Argonne" '
                   'OR "Oak Ridge" OR EPRI OR INPO OR WANO) nuclear '
                   '("artificial intelligence" OR "machine learning" OR "AI") when:2d',
                   "en-US", "US", "US:en")},
    {"name": "AI·Google NEI AI",
     "url": _gnews('site:neimagazine.com (AI OR "artificial intelligence" OR "machine learning" '
                   'OR "digital twin") when:3d', "en-US", "US", "US:en")},
    {"name": "AI·Google 원전 AI(JA)",
     "url": _gnews('(原子力 OR 原発) (AI OR 人工知能 OR 機械学習) when:1d', "ja", "JP", "JP:ja")},
    {"name": "AI·Google 원전 AI(FR)",
     "url": _gnews('nucléaire ("intelligence artificielle" OR IA) when:1d', "fr", "FR", "FR:fr")},
]
# 전용 피드의 수집 창. when:1d 를 넘는 오래된 항목은 버린다(관련도순 대비).
FEED_LOOKBACK = timedelta(days=3)


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


def from_feed_item(item: dict, hasher: Callable[[str], str]) -> dict:
    """전용 피드 항목 → 레인 입력. 제목뿐이라 판정 전에 본문을 붙인다."""
    domain = item.get("publisher_domain") or ""
    return {
        "hash": hasher(item.get("raw_link") or item.get("link") or ""),
        "origin": "dedicated",
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


def collect_feeds(fetch: Callable[[str, str], list[dict]], hasher: Callable[[str], str],
                  *, now: datetime | None = None,
                  sources: list[dict] | None = None) -> tuple[list[dict], dict[str, int]]:
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    counts: dict[str, int] = {}
    for src in sources if sources is not None else AI_SOURCES:
        try:
            items = fetch(src["url"], src["name"]) or []
        except Exception as exc:  # noqa: BLE001 — 피드 하나가 레인을 멈추면 안 된다
            print(f"  ! [AI] {src['name']} 실패: {type(exc).__name__}")
            items = []
        counts[src["name"]] = len(items)
        for item in items:
            pub = item.get("pub")
            if isinstance(pub, datetime) and pub < now - FEED_LOOKBACK:
                continue
            out.append(from_feed_item(item, hasher))
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


# ── 한 회차 ──────────────────────────────────────────────────────────────────
def select_candidates(pool: list[dict], state: dict, identities: dict[str, set[str]]) -> tuple[list[dict], dict]:
    stats = {"pool": len(pool), "seen": 0, "duplicate": 0, "prefilter_out": 0, "candidates": 0}
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
        if not ai_lane.prefilter(article)["passed"]:
            # 탈락은 상태에 적지 않는다. 정규식이라 다시 걸러도 거의 공짜고, 적으면
            # 상태 파일이 회차마다 수백 줄씩 자란다(공유 풀은 최근 3일 전량이다).
            stats["prefilter_out"] += 1
            continue
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
    feed_counts: dict[str, int] = {}
    if fetch is not None and hasher is not None:
        feed_items, feed_counts = collect_feeds(fetch, hasher, now=now)
        pool.extend(feed_items)

    candidates, stats = select_candidates(pool, state, identities)
    stats["feeds"] = feed_counts
    batch = candidates[:MAX_JUDGE_PER_RUN]
    stats["deferred"] = len(candidates) - len(batch)

    dedicated = [a for a in batch if a.get("origin") == "dedicated"]
    if dedicated and body_fetcher is not None:
        try:
            bodies, _ = body_fetcher(dedicated[:MAX_BODY_PER_RUN])
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
    stats.update({"judged": 0, "included": 0, "excluded": 0, "failed": 0})
    for article, verdict in zip(batch, verdicts):
        digest = article["hash"]
        if verdict is None:
            stats["failed"] += 1
            state["attempts"][digest] = int(state["attempts"].get(digest, 0)) + 1
            if state["attempts"][digest] >= MAX_ATTEMPTS:
                state["judged"][digest] = {"at": judged_at, "in": False, "reason": "judge_failed"}
            continue
        stats["judged"] += 1
        state["judged"][digest] = {"at": judged_at, "in": verdict["in_scope"],
                                   "reason": verdict.get("exclude_reason", "")}
        if verdict["in_scope"]:
            stats["included"] += 1
            records.append(make_record(article, verdict, judged_at=judged_at, model=model))
        else:
            stats["excluded"] += 1
    append_records(records, archive_dir)
    stats["at"] = judged_at
    state["runs"].append({k: v for k, v in stats.items() if k != "feeds"})
    save_state(state, state_file, now=now)
    return stats


def format_stats(stats: dict) -> str:
    feeds = stats.get("feeds") or {}
    feed_total = sum(feeds.values())
    return (f"[AI 레인] 풀 {stats.get('pool', 0)}(전용 피드 {feed_total}) · 기판정 {stats.get('seen', 0)}"
            f" · 중복 {stats.get('duplicate', 0)} · 프리필터 탈락 {stats.get('prefilter_out', 0)}"
            f" · 판정 {stats.get('judged', 0)} → 포함 {stats.get('included', 0)}"
            f" / 제외 {stats.get('excluded', 0)} · 실패 {stats.get('failed', 0)}"
            f" · 다음 회차로 {stats.get('deferred', 0)}")
