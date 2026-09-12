"""기사에서 확인한 미래 일정을 **기억한다** → event_ledger.json.

왜 필요한가
-----------
`event_calendar` 는 상태를 두지 않는다. 매 빌드마다 `news_items`(최근 60일)에서
처음부터 다시 유도하고, 창이 하루씩 미끄러지면서 지난 일정이 저절로 빠진다.
그 설계는 취소·연기를 후속 기사에 맡길 수 있어 단순하고 튼튼하다.

다만 거기에는 산술적인 구멍이 하나 있다. 달력 창은 30일이고 기사 창은 60일인데,
**기사가 60일보다 더 먼 앞날을 예고하면 그 일정은 영영 화면에 못 선다.**

    D일   기사가 D+90 행사를 예고한다      → 달력 창(30일) 밖이라 안 선다
    D+60  그 기사가 news_items 에서 빠진다 → 재료가 사라진다
    D+75  행사가 달력 창에 들어온다        → 그 일정을 아는 기사가 이미 없다

실측(archive 2026-07~09, 미래 표지 절의 명시 날짜 기준): 60일 안에 닿는 미래
날짜가 172건이고, 60일보다 먼 것이 20건이다. 약 10%가 이 구멍으로 샌다. 샌 것이
사소하지도 않다 —

    ICRS15 / ANS RPSD        2026-10-25~29 제주   (7/27 보도)
    월성 2호기 설계수명 만료   2026-11-01          (8/13 보도)
    엔릿 유럽                 2026-11-10~12 빈     (8/13 보도)

무엇을 하는가
-------------
크롤이 돌 때 아카이브를 훑어 **달력 창 밖의 확정 일정**만 골라 이 파일에 쌓는다.
달력이 보는 30일 안의 일정은 지금처럼 기사에서 바로 유도한다 — 원장은 그 경로를
대체하지 않고 **그 앞을 잇는다.**

무엇을 하지 않는가
------------------
* LLM 을 부르지 않는다. 날짜도 이름도 원문의 부분 문자열이다.
* 추론으로 읽은 날짜는 담지 않는다(`date_basis` 가 inferred 인 것은 제외).
  몇 달을 들고 있어야 하는 값이라 근거가 단단한 것만 받는다. 범위의 꼬리는
  추론이 아니라 범위 표기이므로 받는다.
* 주제 판정을 통과하지 못한 것은 담지 않는다 — 기사 경로의 화면 게이트와 같은
  판정을 그대로 지난다(`event_calendar.verify_reported`).
* 원장을 믿지 않는다. 달력은 이 파일의 행도 세우기 전에 다시 잰다 — 저장본은
  파일이고 파일은 낡는다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import event_calendar

BASE = Path(__file__).parent
OUT_FILE = BASE / "event_ledger.json"
KST = timezone(timedelta(hours=9))

# 이만큼 앞까지 기억한다. 공식 일정 저장본과 같은 길이다 — 두 원장이 다른 앞날을
# 보면 같은 행사가 한쪽에만 있는 날이 생긴다.
HORIZON_DAYS = 400
# 지난 일정을 바로 지우지 않는 까닭은 `event_sources` 와 같다 — 갓 지난 일정이
# 남아 있어야 그 일을 뒤늦게 다룬 기사가 같은 사건으로 접힌다.
KEEP_PAST_DAYS = 14
MAX_EVENTS = 400
# 아카이브에서 훑는 뒤쪽 길이. 달력이 기사에서 직접 유도하는 창(60일)보다 짧으면
# 크롤이 몇 회차 쉬었을 때 그 사이 예고된 일정을 놓친다.
LOOKBACK_DAYS = 90


def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def candidates(articles: list[dict], today: date) -> list[dict]:
    """달력 창 **밖**의 확정 일정. 창 안의 것은 기사에서 바로 유도하므로 안 담는다.

    `event_calendar.build` 를 창만 길게 잡아 그대로 돌린다. 추출기·검증·주제
    판정을 여기서 다시 구현하지 않는 것이 요점이다 — 두 벌을 두면 어느 날
    화면과 원장이 다른 말을 한다.
    """
    horizon = event_calendar.build(articles, today, days=HORIZON_DAYS)
    rows: list[dict] = []
    edge = today + timedelta(days=event_calendar.HORIZON_DAYS)
    for row in horizon.get("events") or []:
        start = _as_date(row.get("date"))
        if start is None or start <= edge:
            continue
        # 몇 달을 들고 있을 값이다. **추론으로 읽은 날짜는** 그만한 무게를 못
        # 견딘다. 범위의 꼬리(syntactic)는 다르다 — "10월 25일부터 29일까지"
        # 의 29일은 추론이 아니라 한국어 범위 표기라 시간이 지나도 안 흔들린다.
        if str(row.get("date_basis") or "explicit") == "inferred":
            continue
        sources = row.get("sources") or []
        first = sources[0] if sources else {}
        rows.append({
            "id": row.get("id") or "",
            "date": row.get("date") or "",
            "end_date": row.get("end_date") or row.get("date") or "",
            "kind": row.get("kind") or "point",
            "label": row.get("label") or "",
            # 근거 문장. 달력이 이 줄을 그대로 다시 읽어 날짜를 확인한다.
            "clause": row.get("clause") or "",
            "title": first.get("title") or row.get("title") or "",
            "url": first.get("url") or row.get("url") or "",
            "publisher": first.get("publisher") or "",
            "hash": first.get("hash") or "",
            "story_id": first.get("story_id") or "",
            "topics": list(row.get("topics") or []),
            "first_seen": row.get("first_seen") or today.isoformat(),
            "last_seen": today.isoformat(),
        })
    return rows


def merge(kept: list[dict], fresh: list[dict]) -> tuple[list[dict], int]:
    """새로 본 것을 저장본에 얹는다. **최초 확인일은 낮은 쪽이 이긴다.**

    같은 기사(story)가 날짜를 고쳐 다시 오면 새 줄을 세우지 않고 그 줄을 옮긴다 —
    `event_sources.merge_events` 가 게시물 번호로 하는 일을 story_id 로 한다.
    옮기지 않으면 '9월 18일 토론회'와 '9월 25일로 연기된 같은 토론회'가 원장에
    나란히 남는다.
    """
    by_id = {row["id"]: row for row in kept if row.get("id")}
    by_story = {row["story_id"]: row for row in kept if row.get("story_id")}
    added = 0
    for row in fresh:
        existing = by_id.get(row["id"])
        if existing is not None:
            first_seen = min(existing.get("first_seen") or row["first_seen"],
                             row["first_seen"])
            existing.update(row)
            existing["first_seen"] = first_seen
            continue
        prior = by_story.get(row["story_id"]) if row.get("story_id") else None
        if prior is not None:
            moved = {**prior, **row,
                     "first_seen": min(prior.get("first_seen") or row["first_seen"],
                                       row["first_seen"])}
            by_id.pop(prior["id"], None)
            by_id[moved["id"]] = moved
            by_story[row["story_id"]] = moved
            continue
        by_id[row["id"]] = row
        if row.get("story_id"):
            by_story[row["story_id"]] = row
        added += 1
    return list(by_id.values()), added


def prune(events: list[dict], today: date) -> list[dict]:
    floor = (today - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    ceiling = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    alive = [row for row in events
             if str(row.get("end_date") or row.get("date") or "") >= floor
             and str(row.get("date") or "") <= ceiling]
    alive.sort(key=lambda row: (row.get("date") or "", row.get("label") or ""))
    return alive[:MAX_EVENTS]


def load_store(path: Path | None = None) -> dict:
    try:
        raw = json.loads((path or OUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {"generated_at": raw.get("generated_at") or "",
            "events": [row for row in (raw.get("events") or [])
                       if isinstance(row, dict)]}


def save_store(store: dict, path: Path | None = None) -> None:
    target = path or OUT_FILE
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(target)


def calendar_rows(store: dict, today: date) -> list[dict]:
    """달력에 넘길 모양. 기사 경로의 행과 **같은 칸 이름**을 쓴다.

    원장은 달력 창 밖의 것만 담지만, 넘길 때는 거르지 않는다 — 창 안으로 들어온
    행을 달력이 세우는 것이 이 원장의 존재 이유다. 창 안에서 같은 일정을 말한
    기사가 새로 들어오면 `_fold` 가 둘을 접고 근거를 합친다.
    """
    return [{**row, "origin": "remembered",
             "reference": row.get("first_seen") or today.isoformat()}
            for row in (store.get("events") or [])]


def run(articles: list[dict], today: date | None = None,
        path: Path | None = None) -> dict:
    now = datetime.now(KST)
    today = today or now.date()
    store = load_store(path)
    store["events"], added = merge(store["events"], candidates(articles, today))
    store["events"] = prune(store["events"], today)
    store["generated_at"] = now.isoformat(timespec="seconds")
    save_store(store, path)
    print(f"[event_ledger] 신규 {added}건, 보관 {len(store['events'])}건 "
          f"→ {(path or OUT_FILE).name}")
    return store


def _articles_from_archive(today: date) -> list[dict]:
    """아카이브에서 최근 창의 기사. 웹 빌드가 달력에 넘기는 것과 같은 모양으로 편다."""
    cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows: list[dict] = []
    for path in sorted((BASE / "archive").glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            when = str(record.get("pub") or record.get("archived_at") or "")[:10]
            if when >= cutoff:
                rows.append({**record, "article_date": when})
    return rows


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    _today = datetime.now(KST).date()
    run(_articles_from_archive(_today), _today)
