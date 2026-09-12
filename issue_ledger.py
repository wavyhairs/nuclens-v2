"""이슈가 어디로 갔는지 **기억한다** → issue_ledger.json.

왜 필요한가
-----------
`web/build_data.build_issue_pages` 는 매 빌드마다 `/issue` 를 통째로 지우고
그때의 카탈로그만 다시 만든다. 그래서 이슈가 카탈로그에서 빠지는 순간 그
주소는 하드 404 가 된다(`web/public/404.html` 이 SPA 폴백을 의도적으로 꺼 둔다).

빠지는 이유가 노화만이 아니라는 것이 문제였다. 2026-09-12 실측 —

    9/1 빌드의 이슈 447건을 11일 뒤 라이브와 대조: 75건(16.8%)이 사라짐
    사라진 표본 5건의 first_seen 은 08-26 ~ 08-31 로 **창 한복판**이었고,
    5건 전부 라이브에서 404 였다. 노화가 아니라 재클러스터링 이동이다.
    같은 날 rss.xml 의 고유 이슈 링크 185건 중 33건(17.8%)이 이미 깨져 있었다.

RSS 항목은 구독자 리더에 영구히 남는다. 웹에서 "창에서 밀려났다"는 것이
구독자 쪽에서는 그냥 깨진 링크다.

무엇을 하는가
-------------
카탈로그가 만들어질 때마다 이슈의 신원을 이 파일에 쌓는다. 셋을 남긴다.

``moved_to``   재클러스터링으로 다른 id 로 옮겨간 자리. 옛 주소가 새 주소로
               이어지도록 `build_issue_pages` 가 이 값을 읽어 리다이렉트 쪽지를
               만든다. 명세의 merged_into 가 여기서 나온다.
``revisions``  갱신 시점마다의 제목·요약. **인용 복원용**이다 — 같은 기간
               살아남은 이슈의 18.8% 가 제목이 바뀌었고(실측), 보고서 각주에
               붙은 주소가 다른 말을 하기 시작하면 인용이 성립하지 않는다.
``hashes``     근거 기사 해시. 이동 판정의 유일한 재료다.

이동은 어떻게 아는가 — 어휘가 아니라 기사로
--------------------------------------------
제목이 닮았는지로 이동을 판정하면 이 저장소가 이미 여러 번 겪은 오병합을
원장에까지 들인다. 기사 해시만 본다: 카탈로그의 한 기사는 정확히 한 이슈에만
속하므로(`test_global_issue_catalog_contains_each_delivered_article_once`)
`hash → issue_id` 는 함수다. 사라진 이슈의 기사들이 지금 어느 이슈에 있는지
세어 **가장 많이 받아 간 쪽**을 이동 대상으로 본다. 아무 데도 없으면 이동이
아니라 보관(노화)이다.

무엇을 하지 않는가
------------------
* LLM 을 부르지 않는다. 전부 카탈로그가 이미 만든 값의 재배치다.
* 지우지 않는다. 원장은 아카이브라 prune 이 없다 — 대신 이슈당 revisions 와
  hashes 에 상한을 둔다(무한히 자라는 것은 목록이 아니라 항목 쪽이다).
* 제목을 고르지 않는다. 카탈로그가 고른 대표를 그대로 받아 적는다.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
OUT_FILE = BASE / "issue_ledger.json"
KST = timezone(timedelta(hours=9))

# 이슈당 상한. revisions 는 제목이 바뀔 때만 늘고(하루 최대 1), hashes 는 그
# 이슈의 근거 기사 수다. 둘 다 실측 상한이 훨씬 낮아 잘리는 일이 드물지만,
# 원장은 영구 파일이라 상한 없는 칸을 두지 않는다.
MAX_REVISIONS = 24
MAX_HASHES = 80


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _today(today: object = None) -> str:
    if today:
        return str(today)[:10]
    return datetime.now(KST).date().isoformat()


def catalog_rows(issue_catalog: list[dict]) -> list[dict]:
    """빌드된 이슈 카탈로그 → 원장이 받아 적는 모양."""
    rows: list[dict] = []
    for issue in issue_catalog:
        issue_id = _clean(issue.get("issue_id"))
        if not issue_id:
            continue
        hashes = [
            _clean(article.get("hash"))
            for article in (issue.get("related_articles") or [])
            if _clean(article.get("hash"))
        ]
        if not hashes:
            representative = issue.get("representative_article") or {}
            hashes = [_clean(representative.get("hash"))] if representative.get("hash") else []
        rows.append({
            "issue_id": issue_id,
            "title": _clean(issue.get("title")),
            "summary": _clean(issue.get("summary")),
            "region": _clean(issue.get("region")),
            "first_seen": _clean(issue.get("first_seen")),
            "last_seen": _clean(issue.get("last_seen")),
            "article_count": int(issue.get("article_count") or 0),
            "briefing_count": int(issue.get("briefing_count") or 0),
            "topics": [t for t in (issue.get("topics") or []) if t][:4],
            "hashes": hashes[:MAX_HASHES],
        })
    return rows


def _revision(row: dict, day: str) -> dict:
    return {"date": day, "title": row["title"], "summary": row["summary"]}


def merge(store: dict, rows: list[dict], day: str) -> dict:
    """카탈로그 행을 원장에 얹는다. 최초 확인일은 낮은 쪽이 이긴다."""
    issues = store["issues"]
    added = 0
    revised = 0
    for row in rows:
        existing = issues.get(row["issue_id"])
        if existing is None:
            issues[row["issue_id"]] = {
                **row,
                "revisions": [_revision(row, day)],
                "moved_to": "",
                # 이 항목을 **원장이 마지막으로 적은 날.** `last_seen` 과 다르다 —
                # 저쪽은 카탈로그가 말한 이슈의 최종 활동일이라 여러 이슈가 같은
                # 값을 갖고, 그러면 `event_identity.owner_index` 가 기사 소유권을
                # 가릴 수 없어 그 해시를 버린다(실측 2,733개 중 378개). 이 칸은
                # 빌드마다 단조 증가하므로 '직전 빌드가 이 기사를 누구에게 줬나'를
                # 정확히 말한다.
                "last_written": day,
            }
            added += 1
            continue
        # 이동했다가 같은 id 로 되살아난 이슈. 되살아난 쪽이 현재이므로 이동
        # 표시를 지운다 — 안 지우면 살아 있는 주소가 남의 주소로 넘긴다.
        existing["moved_to"] = ""
        # 해시는 **덮지 않고 쌓는다.** `event_identity` 가 이 칸을 신원의 유일한
        # 증거로 읽으므로(그 모듈 머리말), 이번 회차에 안 붙은 기사가 지워지면
        # 다음 빌드가 같은 사건을 못 알아본다 — 부착은 빌드마다 흔들린다
        # (PR #105 실측: 같은 id 로 살아남은 372건 중 56건이 근거 215건을 잃었다).
        # 이른 것이 앞에 남는다: 최초 기사가 그 사건의 신원 앵커다.
        merged_hashes = list(dict.fromkeys(
            [h for h in (existing.get("hashes") or []) if h] + row["hashes"]
        ))
        row = {**row, "hashes": merged_hashes[:MAX_HASHES]}
        revisions = existing.get("revisions") or []
        last = revisions[-1] if revisions else {}
        if last.get("title") != row["title"] or last.get("summary") != row["summary"]:
            revisions.append(_revision(row, day))
            revised += 1
        existing.update(row)
        existing["last_written"] = day
        existing["first_seen"] = min(
            filter(None, [existing.get("first_seen"), row["first_seen"]]),
            default=row["first_seen"],
        )
        existing["revisions"] = revisions[-MAX_REVISIONS:]
    return {"added": added, "revised": revised}


def resolve_moves(store: dict, rows: list[dict]) -> int:
    """카탈로그에서 사라진 이슈의 기사들이 지금 어느 이슈에 있는지 찾는다.

    아무 데도 없으면 이동이 아니다 — 창 밖으로 나간 **보관**이고, 그 주소는
    그대로 살아 있어야 한다(리다이렉트가 아니라 보관 페이지를 만든다).
    """
    live_ids = {row["issue_id"] for row in rows}
    owner: dict[str, str] = {}
    for row in rows:
        for article_hash in row["hashes"]:
            owner.setdefault(article_hash, row["issue_id"])
    moved = 0
    for issue_id, entry in store["issues"].items():
        if issue_id in live_ids:
            continue
        votes = Counter(
            owner[article_hash]
            for article_hash in (entry.get("hashes") or [])
            if article_hash in owner
        )
        votes.pop(issue_id, None)
        if not votes:
            continue
        target = votes.most_common(1)[0][0]
        if entry.get("moved_to") != target:
            entry["moved_to"] = target
            moved += 1
    return moved


def alias_target(store: dict, issue_id: str) -> str:
    """옮겨간 끝의 id. 고리를 만나면 멈춘다(원장이 자기를 가리키지 않게)."""
    seen = {issue_id}
    current = issue_id
    for _ in range(8):
        entry = store["issues"].get(current) or {}
        nxt = _clean(entry.get("moved_to"))
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return current if current != issue_id else ""


def archived(store: dict, live_ids: set[str]) -> list[dict]:
    """살아 있지도 않고 옮겨가지도 않은 이슈 — 보관 페이지를 세울 대상."""
    return [
        entry for issue_id, entry in store["issues"].items()
        if issue_id not in live_ids and not _clean(entry.get("moved_to"))
    ]


def redirects(store: dict, live_ids: set[str]) -> dict[str, str]:
    """옛 id → 현재 id. **페이지가 실제로 서는 대상**으로 끝나는 것만 돌려준다.

    조건을 '살아 있는 이슈'로만 두면 구멍이 하나 생긴다: A 로 흡수된 B 가 있고
    나중에 A 마저 창 밖으로 나가면, A 는 보관 페이지를 얻는데 B 는 리다이렉트
    대상에서 빠져 **다시 404** 가 된다. 흡수됐다는 이유로 더 일찍 사라지는 셈이라
    이 원장이 존재하는 이유와 정면으로 어긋난다. 보관 페이지도 도착지로 센다.
    """
    reachable = live_ids | {
        issue_id for issue_id, entry in store["issues"].items()
        if issue_id not in live_ids and not _clean(entry.get("moved_to"))
    }
    out: dict[str, str] = {}
    for issue_id in store["issues"]:
        if issue_id in live_ids:
            continue
        target = alias_target(store, issue_id)
        if target and target in reachable:
            out[issue_id] = target
    return out


def backfill_rows(delivery_items: list[dict]) -> list[dict]:
    """발송 이력에서 원장 초기값을 만든다.

    원장을 오늘부터 시작하면 **이미 깨진 주소는 영영 404** 다. 발송 이력은
    story_id 와 대표 해시를 회차마다 적어 두므로, 그것만으로도 옛 주소가
    무엇을 가리켰는지 되살릴 수 있다. 정확한 카탈로그 재현이 아니라
    "그 주소가 어떤 사건이었는지"를 남기는 것이 목적이다.
    """
    by_id: dict[str, dict] = {}
    for item in delivery_items:
        if item.get("record_type"):
            continue
        article_hash = _clean(item.get("hash"))
        if not article_hash:
            continue
        day = _clean(item.get("date"))
        issue_id = _clean(item.get("story_id")) or f"issue-{article_hash}"
        title = _clean(item.get("title_kr")) or _clean(item.get("title"))
        entry = by_id.get(issue_id)
        if entry is None:
            by_id[issue_id] = {
                "issue_id": issue_id,
                "title": title,
                "summary": _clean(item.get("summary")),
                "region": _clean(item.get("region")),
                "first_seen": day,
                "last_seen": day,
                "article_count": 1,
                "briefing_count": 1,
                "topics": [],
                "hashes": [article_hash],
            }
            continue
        entry["article_count"] += 1
        if article_hash not in entry["hashes"]:
            entry["hashes"] = (entry["hashes"] + [article_hash])[:MAX_HASHES]
        if day and day > (entry["last_seen"] or ""):
            entry["last_seen"] = day
            entry["briefing_count"] += 1
            if title:
                entry["title"] = title
        if day and (not entry["first_seen"] or day < entry["first_seen"]):
            entry["first_seen"] = day
    return list(by_id.values())


def load_store(path: Path | None = None) -> dict:
    try:
        raw = json.loads((path or OUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    issues = raw.get("issues")
    if not isinstance(issues, dict):
        issues = {}
    return {
        "generated_at": raw.get("generated_at") or "",
        "issues": {
            key: value for key, value in issues.items()
            if isinstance(value, dict) and _clean(value.get("issue_id"))
        },
    }


def save_store(store: dict, path: Path | None = None) -> None:
    target = path or OUT_FILE
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(target)


def snapshot(entry: dict) -> dict:
    """보관 페이지가 읽는 모양. 기사 본문은 담지 않는다 — 원장은 신원 기록이지
    기사 저장소가 아니다."""
    return {
        "issue_id": entry.get("issue_id", ""),
        "title": entry.get("title", ""),
        "summary": entry.get("summary", ""),
        "region": entry.get("region", ""),
        "first_seen": entry.get("first_seen", ""),
        "last_seen": entry.get("last_seen", ""),
        "article_count": int(entry.get("article_count") or 0),
        "briefing_count": int(entry.get("briefing_count") or 0),
        "topics": list(entry.get("topics") or []),
        "revisions": list(entry.get("revisions") or [])[-MAX_REVISIONS:],
        "archived": True,
    }


def run(issue_catalog: list[dict], today: object = None,
        path: Path | None = None, *, save: bool = True) -> dict:
    """카탈로그 한 번당 원장 한 번. 같은 입력이면 같은 결과다(멱등)."""
    day = _today(today)
    store = load_store(path)
    rows = catalog_rows(issue_catalog)
    counts = merge(store, rows, day)
    counts["moved"] = resolve_moves(store, rows)
    live_ids = {row["issue_id"] for row in rows}
    counts["archived"] = len(archived(store, live_ids))
    counts["total"] = len(store["issues"])
    store["generated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    if save:
        save_store(store, path)
    print(f"[issue_ledger] 신규 {counts['added']} · 제목갱신 {counts['revised']} · "
          f"이동 {counts['moved']} · 보관 {counts['archived']} · 누적 {counts['total']}")
    return {"store": store, "counts": counts, "live_ids": live_ids}
