"""해외 AI·DX 레인 **원천 재검색** 기간 백필 — 지금 알고리즘으로 since 부터 다시 찾아 판정한다.

    python tools/ai_lane_research_backfill.py --since 2026-07-01 --fetch-only     # 수만 센다(판정 0)
    python tools/ai_lane_research_backfill.py --since 2026-07-01 --max-judge 640  # 판정·적재

tools/ai_lane_backfill.py(0단계 gold 를 손으로 옮긴 것)와 다르다. 여기서는 레인이 지금 쓰는 것을
그대로 쓴다 — 프리필터(AI ∨ DX, 러·중 어휘), Gemini 판정 v6, 국내·데이터센터 가드, 같은 레코드 모양.
다만 기사 원천을 **지난 기간에 대해 다시 연다**.

  1. 메인 아카이브(archive/*.jsonl)의 since 이후 전부 — 정기 레인은 최근 3일만 본다.
  2. Google 뉴스 기간 질의(ai_lane_pipeline.BACKFILL_QUERIES) — 영·일·불·러·중, after:/before: 로 잘라서.
     정기 피드는 when: 창이라 지난 기간을 못 뒤진다.

판정 한도 — Gemini 무료 일일 한도(모델당 500회)는 메인 크롤·브리핑과 같이 쓴다. 그래서
  · 판정 전에 결정적 규칙으로 걸러지는 기사는 묻지 않는다: 메인이 국내(kr)로 본 기사,
    데이터센터 전력 표시가 붙은 기사(두 가드가 판정과 무관하게 제외하는 것과 같다).
  · 한 번에 묻는 기사 수를 16건으로 올린다(정기 회차 8건). 호출 수가 절반이 된다.
  · ``--max-judge`` 를 넘은 후보는 남긴다. 같은 명령을 다시 돌리면 판정 이력(ai_state.json)에 없는
    것만 이어서 묻는다 — 재검색은 다시 하지만 판정은 이어진다.

수집 시각(collected_at)은 **발행 시각**으로 찍는다 — 그래야 7월 기사가 오늘 회차가 아니라 그 무렵의
아침 브리핑 회차에 선다(ai_lane_web.round_date). 메인 아카이브 기사는 원래 수집 시각 그대로다.
레코드에는 ``backfill: "research"`` 를 단다.

Gemini 키와 Google 뉴스가 필요해 Actions(`.github/workflows/ai-lane-backfill.yml`)에서 돈다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import ai_lane  # noqa: E402
import ai_lane_pipeline as lane  # noqa: E402

KST = timezone(timedelta(hours=9))
BATCH_SIZE = 16
FETCH_PAUSE_SEC = 1.2
MAX_BODY = 160


def main_rows(since: date, archive_dir: Path = BASE / "archive") -> list[dict]:
    """메인 아카이브에서 since(KST 날짜) 이후 수집분."""
    cut = datetime.combine(since, datetime.min.time(), tzinfo=KST).astimezone(timezone.utc).isoformat()
    out: list[dict] = []
    for path in sorted(archive_dir.glob("*.jsonl")):
        if path.stem < since.strftime("%Y-%m"):
            continue
        for row in lane._read_jsonl(path):
            if str(row.get("archived_at") or "") >= cut:
                out.append(row)
    return out


def pre_exclude(article: dict) -> str:
    """판정 없이 정해지는 제외 사유. 빈 문자열이면 판정에 올린다."""
    if article.get("scope") == "kr":
        return "domestic"
    if "datacenter_power" in ((article.get("_prefilter") or {}).get("hints") or []):
        return "datacenter_power"
    return ""


def throttled(fetch):
    def call(url: str, name: str) -> list[dict]:
        time.sleep(FETCH_PAUSE_SEC)
        return fetch(url, name)
    return call


def run(since: date, until: date, *, fetch=None, hasher=None, body_fetcher=None, judge_call=None,
        model: str = "", max_judge: int = 640, fetch_only: bool = False,
        now: datetime | None = None, state_file: Path = lane.STATE_FILE,
        archive_dir: Path = lane.ARCHIVE_DIR, main_archive_dir: Path = BASE / "archive") -> dict:
    now = now or datetime.now(timezone.utc)
    judged_at = now.isoformat()
    state = lane.load_state(state_file)
    identities = lane.archive_identities(lane.load_archive(archive_dir))

    pool = [lane.from_main_record(r) for r in main_rows(since, main_archive_dir)]
    shared_n = len(pool)
    feed_counts: dict[str, dict] = {}
    if fetch is not None and hasher is not None:
        start = datetime.combine(since, datetime.min.time(), tzinfo=KST)
        items, counts = lane.collect_feeds(fetch, hasher, now=now, sources=lane.backfill_sources(since, until),
                                           lookback=now - start + timedelta(days=1))
        for item in items:
            item["collected_at"] = item.get("pub") or judged_at
        pool.extend(items)
        feed_counts = counts

    candidates, stats = lane.select_candidates(pool, state, identities)
    stats["shared_pool"] = shared_n
    stats["dedicated_pool"] = len(pool) - shared_n
    by_label: Counter = Counter()
    for name, v in feed_counts.items():
        label = name.rsplit(" ", 1)[0]
        by_label[label] += int(v.get("fresh") or 0)
    stats["feeds_by_query"] = dict(by_label)
    stats["feed_failures"] = sum(1 for v in feed_counts.values() if v.get("failed"))

    to_judge: list[dict] = []
    pre = Counter()
    for article in candidates:
        reason = pre_exclude(article)
        if reason:
            pre[reason] += 1
            if not fetch_only:
                state["judged"][article["hash"]] = {"at": judged_at, "in": False, "reason": reason,
                                                    "feed": article.get("feed", ""), "v": ai_lane.PROMPT_VERSION,
                                                    "backfill": "research"}
        else:
            to_judge.append(article)
    stats["pre_excluded"] = dict(pre)
    stats["to_judge"] = len(to_judge)
    stats["estimated_calls"] = -(-len(to_judge) // BATCH_SIZE)
    if fetch_only:
        return stats

    # 오래된 것부터 — 이어서 돌릴 때 기간이 앞에서부터 채워진다.
    to_judge.sort(key=lambda a: (str(a.get("collected_at") or ""), 0 if a.get("origin") == "shared" else 1))
    batch = to_judge[:max_judge]
    stats["deferred"] = len(to_judge) - len(batch)
    dedicated = [a for a in batch if a.get("origin") == "dedicated"]
    stats["body"] = {"attempted": 0, "ok": 0}
    if dedicated and body_fetcher is not None:
        try:
            targets = dedicated[:MAX_BODY]
            bodies, _ = body_fetcher(targets)
            stats["body"] = {"attempted": len(targets), "ok": sum(1 for a in targets if bodies.get(a["hash"]))}
            for article in dedicated:
                if bodies.get(article["hash"]):
                    article["body_excerpt"] = bodies[article["hash"]][:1500]
                if article.get("resolved_url"):
                    article["url"] = article["resolved_url"]
        except Exception as exc:  # noqa: BLE001 — 본문 없이도 제목으로 판정한다
            print(f"  ! [AI 백필] 본문 수집 실패: {type(exc).__name__}")

    verdicts = ai_lane.judge(batch, call=judge_call, batch_size=BATCH_SIZE) if batch else []
    records = []
    stats.update({"judged": 0, "included": 0, "excluded": 0, "failed": 0, "guarded": 0})
    reasons: Counter = Counter()
    for article, verdict in zip(batch, verdicts):
        guarded = lane.datacenter_guard(article, lane.domestic_guard(article, verdict))
        if guarded is not verdict and guarded is not None:
            stats["guarded"] += 1
        verdict = guarded
        if verdict is None:
            stats["failed"] += 1   # 이력에 안 적는다 — 다시 돌리면 다시 묻는다
            continue
        stats["judged"] += 1
        state["judged"][article["hash"]] = {"at": judged_at, "in": verdict["in_scope"],
                                            "reason": verdict.get("exclude_reason", ""),
                                            "feed": article.get("feed", ""), "v": ai_lane.PROMPT_VERSION,
                                            "backfill": "research"}
        if verdict["in_scope"]:
            stats["included"] += 1
            record = lane.make_record(article, verdict, judged_at=judged_at, model=model)
            record["backfill"] = "research"
            # 수집 달의 파일에 넣는다 — 회차는 collected_at 을 따른다.
            record["archived_at"] = record["collected_at"]
            records.append(record)
        else:
            stats["excluded"] += 1
            reasons[verdict.get("exclude_reason", "")] += 1
    stats["exclude_reasons"] = dict(reasons)
    lane.append_records(records, archive_dir)
    lane.save_state(state, state_file, now=now)
    stats["included_titles"] = [(r["collected_at"][:10], r.get("tech_scope"), r["title_kr"]) for r in records]
    return stats


def recheck(*, judge_call=None, now: datetime | None = None, state_file: Path = lane.STATE_FILE,
            archive_dir: Path = lane.ARCHIVE_DIR) -> dict:
    """이미 들어간 Gemini 판정 레코드를 **지금 판정 기준**으로 다시 묻고, 이제 제외되는 것을 DB 에서 뺀다.

    판정 기준을 좁힌 뒤(v7) 앞선 판정이 남긴 오판을 걷어 내는 용도다. 손으로 적은 레코드
    (judge=manual-claude)는 건드리지 않는다. 판정 실패는 그대로 둔다(빼지 않는다).
    """
    now = now or datetime.now(timezone.utc)
    judged_at = now.isoformat()
    rows = lane.load_archive(archive_dir)
    targets = [r for r in rows if r.get("judge") == "gemini"
               and int(r.get("prompt_version") or 0) < ai_lane.PROMPT_VERSION]
    articles = []
    for r in targets:
        article = {"hash": r["hash"], "title": r.get("title", ""), "title_kr": r.get("title_kr", ""),
                   "summary": r.get("summary", ""), "detail": r.get("detail", ""),
                   "publisher": r.get("publisher", ""), "domain": r.get("domain", ""),
                   "scope": r.get("main_scope", ""), "countries": r.get("countries") or []}
        article["_prefilter"] = ai_lane.prefilter(article)
        articles.append(article)
    verdicts = ai_lane.judge(articles, call=judge_call, batch_size=BATCH_SIZE) if articles else []
    state = lane.load_state(state_file)
    drop: set[str] = set()
    reasons: Counter = Counter()
    failed = 0
    for article, verdict in zip(articles, verdicts):
        verdict = lane.datacenter_guard(article, lane.domestic_guard(article, verdict))
        if verdict is None:
            failed += 1
            continue
        if not verdict.get("in_scope"):
            drop.add(article["hash"])
            reasons[verdict.get("exclude_reason", "")] += 1
            state["judged"][article["hash"]] = {"at": judged_at, "in": False,
                                                "reason": verdict.get("exclude_reason", ""),
                                                "v": ai_lane.PROMPT_VERSION, "recheck": True}
    asked = {a["hash"] for a in articles}
    removed_titles = []
    for path in sorted(archive_dir.glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if row.get("hash") in drop:
                removed_titles.append(row.get("title_kr", ""))
                continue
            if row.get("hash") in asked:
                row["prompt_version"] = ai_lane.PROMPT_VERSION   # 다시 물어 살아남았다
                line = json.dumps(row, ensure_ascii=False)
            keep.append(line)
        path.write_text("".join(x + "\n" for x in keep), encoding="utf-8")
    lane.save_state(state, state_file, now=now)
    return {"rechecked": len(articles), "removed": len(drop), "failed": failed,
            "reasons": dict(reasons), "removed_titles": removed_titles}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--until", default="", help="KST 날짜, 비우면 오늘")
    parser.add_argument("--max-judge", type=int, default=640)
    parser.add_argument("--fetch-only", action="store_true", help="재검색·프리필터까지만 하고 수를 센다(판정 0)")
    parser.add_argument("--recheck", action="store_true",
                        help="먼저 기존 Gemini 레코드를 지금 기준으로 다시 묻고 제외되는 것을 뺀다")
    args = parser.parse_args()

    import article_body  # noqa: PLC0415
    import gemini_client  # noqa: PLC0415
    import llm_policy  # noqa: PLC0415
    import news_bot  # noqa: PLC0415

    if not args.fetch_only and not gemini_client.is_available():
        sys.exit("ERROR: Gemini 키가 없다 — Actions 의 ai-lane-backfill 워크플로에서 실행할 것")
    if args.recheck and not args.fetch_only:
        result = recheck()
        removed = result.pop("removed_titles", [])
        print("[AI 백필 재검사] " + json.dumps(result, ensure_ascii=False))
        for title in removed:
            print(f"  - {title}")
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else datetime.now(KST).date()
    stats = run(since, until, fetch=throttled(news_bot.fetch_rss), hasher=news_bot.url_hash,
                body_fetcher=article_body.fetch_bodies, model=llm_policy.profile(ai_lane.POLICY_TASK).model(),
                max_judge=args.max_judge, fetch_only=args.fetch_only)
    titles = stats.pop("included_titles", [])
    print("[AI 백필] " + json.dumps(stats, ensure_ascii=False))
    for day, scope, title in titles:
        print(f"  + {day} [{scope}] {title}")
    if not args.fetch_only:
        try:
            print(gemini_client.format_call_stats())
        except Exception as exc:  # noqa: BLE001
            print(f"[gemini] 호출 통계 실패: {exc}")


if __name__ == "__main__":
    main()
