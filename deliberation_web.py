"""신규원전 공론화 레인 → 웹 산출물(deliberation_issues.json).

`deliberation_archive/` 의 기사를 **회차**와 **사건(이슈)**으로 묶는다. 「신규원전 공론화」 탭·탐색의
'공론화' 범위·이슈 상세가 이 파일 하나만 읽는다.

회차
----
해외 AI 레인과 같다(``ai_lane_web.round_date``) — 기사가 수집된 뒤 **처음 나가는 아침 브리핑의 날짜**.
「오늘」 탭과 날짜 축이 같아야 두 탭을 오가며 읽힌다. 마지막 브리핑 뒤에 들어온 기사는 ``pending`` 으로
따로 싣고, 화면은 그것을 목록 **위**에 「오늘 들어온 기사 · 3시간마다 갱신」으로 보인다 — 이 탭의 요청이
"3시간마다 계속 띄우기"라서다.

같은 사건 묶기 — AI 레인과 **다르게** 둔다
------------------------------------------
AI 레인의 규칙(주체 겹침 ∧ 적용 영역 같음)은 여기서 과병합된다 — 주체가 거의 늘 「정부·공론화위」다.
대신 결정적 비교 셋만 쓴다. 의미 유사도로 잇지 않는다(이 저장소의 오병합 교훈).

  1. 접힌 기사(``folded_into``)는 대표의 사건에 붙는다 — 메인이 같은 기사라고 본 것이다.
  2. 사건 이름(``event_key``)이 공백·문장부호를 지우고 같고, 그 사건의 마지막 기사로부터 7일 안.
     판정기는 최근 사건 이름 목록을 받아 같은 사건에 같은 이름을 쓴다(deliberation_lane.build_request).
  3. 제목이 같다(``title_key``).

0단계 gold(포함 140건, 사건 35개)를 이 규칙으로 묶으면 사건 35개가 그대로 나온다(test_deliberation_web).

중요도 (회차 안 정렬)
--------------------
LLM 에 순위를 묻지 않는다. ``중대성×3 + 주체 + 기사 유형 + 매체 수 + 공식 원문``. 매체 수를 넣는 이유:
이 주제에서는 다매체 보도가 곧 그날의 큰 사건이다(「공론화위 출범」 매체 26곳).

가드레일: stdlib + data_quality + ai_lane_web 의 회차 함수만. build_data 의 검증 함수는 주입받는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import ai_lane_web as rounds_of
import deliberation_lane as lane
from data_quality import title_key

BASE = Path(__file__).parent
ARCHIVE_DIR = Path(os.environ.get("DELIBERATION_ARCHIVE_DIR") or (BASE / "deliberation_archive"))
KST = timezone(timedelta(hours=9))
SCHEMA = "nuclens-deliberation-v1"
GROUP_WINDOW_DAYS = 7
QUIET_AFTER_DAYS = 30

ACTOR_POINTS = {"government": 2, "committee": 2, "assembly": 2, "local": 1, "industry": 1,
                "civic": 1, "expert": 1, "media": 0}
TYPE_POINTS = {"report": 1, "analysis": 1, "interview": 1, "editorial": 0, "press_release": 0}

round_date = rounds_of.round_date
is_pending = rounds_of.is_pending


def load_rows(archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for path in sorted(archive_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            digest = str((row or {}).get("hash") or "")
            if isinstance(row, dict) and digest and digest not in seen:
                seen.add(digest)
                rows.append(row)
    return rows


def _publisher(row: dict) -> str:
    return str(row.get("publisher") or row.get("domain") or "").strip()


def group_rows(rows: list[dict]) -> list[list[dict]]:
    """기사 → 사건 묶음. 수집 순서로 훑으며 앞선 묶음에 붙일 수 있으면 붙인다.

    창은 묶음의 **첫날**부터 잰다. 마지막 날부터 재면 같은 사건 이름이 엿새마다 한 번씩만 나와도
    끝없이 이어 붙는다 — 원천 재검색 백필(2026-10-15) 실측: 「기후부 신규 원전 규모 공론화 거쳐 결정
    방침」 하나가 9/20~10/13 기사 257건을 삼켰다. 접힌 기사는 창과 상관없이 대표를 따라간다.
    """
    groups: list[dict] = []
    home_of: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: (str(r.get("collected_at") or ""), r.get("origin") == "folded",
                                           r.get("hash", ""))):
        day = datetime.fromisoformat(round_date(row))
        key = lane.event_key_norm(row.get("event_key"))
        tkey = title_key(row.get("title_kr") or row.get("title"))
        home = home_of.get(str(row.get("folded_into") or ""))
        if home is None:
            for group in reversed(groups):
                if (day - group["first_day"]).days > GROUP_WINDOW_DAYS:
                    continue
                if (key and key in group["keys"]) or (tkey and tkey in group["titles"]):
                    home = group
                    break
        if home is None:
            home = {"rows": [], "keys": set(), "titles": set(), "first_day": day}
            groups.append(home)
        home["rows"].append(row)
        if key:
            home["keys"].add(key)
        if tkey:
            home["titles"].add(tkey)
        home_of[row["hash"]] = home
    return [group["rows"] for group in groups]


def _strength(row: dict) -> tuple:
    """회차 대표: 직접 판정한 기사 > 공식 원문 > 독립 매체 > 등급 > 중대성 > 먼저 나온 것."""
    return (row.get("judge") == "inherited", row.get("confidence") == "low",
            *rounds_of._strength(row)[:3], -int(row.get("significance") or 0),
            str(row.get("pub") or ""), row.get("hash", ""))


def score(row: dict, outlets: int, verification: dict | None = None) -> tuple[int, str]:
    significance = int(row.get("significance") or 0)
    actor = ACTOR_POINTS.get(row.get("actor_type"), 0)
    kind = TYPE_POINTS.get(row.get("article_type"), 0)
    breadth = 2 if outlets >= 4 else 1 if outlets >= 2 else 0
    official = 1 if (verification or {}).get("official_source_count") else 0
    label = lambda key: lane.LABELS_KR.get(key, key)  # noqa: E731
    parts = [f"중대성 {significance}×3", f"{label(row.get('actor_type'))} {actor:+d}",
             f"{label(row.get('article_type'))} {kind:+d}"]
    if breadth:
        parts.append(f"매체 {outlets}곳 {breadth:+d}")
    if official:
        parts.append("공식 원문 +1")
    return significance * 3 + actor + kind + breadth + official, " · ".join(parts)


def _issue_id(first: dict) -> str:
    return "dl-" + hashlib.sha1(str(first.get("hash") or "").encode("utf-8")).hexdigest()[:12]


def _article_view(row: dict, role: str) -> dict:
    day = round_date(row)
    return {
        "hash": row.get("hash", ""),
        "title_kr": row.get("title_kr") or row.get("title", ""),
        "title": row.get("title", ""),
        "url": row.get("resolved_url") or row.get("url", ""),
        "publisher": _publisher(row),
        "domain": row.get("domain", ""),
        "source_type": row.get("source_type", ""),
        "evidence_role": row.get("evidence_role", ""),
        "source_tier": row.get("source_tier", 3),
        "article_date": rounds_of.article_date(row),
        "round": day,
        "briefing_date": day if role == "card" else "",
        "member_role": role,
        "detail": row.get("detail", ""),
        "summary": row.get("summary", ""),
        "article_type": row.get("article_type", ""),
        "stance": row.get("stance", ""),
        "actor_type": row.get("actor_type", ""),
        "confidence": row.get("confidence", ""),
        "origin": row.get("origin", ""),
        "pub": row.get("pub", ""),
        "collected_at": row.get("collected_at", ""),
    }


def build_issue(rows: list[dict], *, today: str, verify: Callable[[list[dict]], dict] | None = None) -> dict:
    rows = sorted(rows, key=lambda r: (round_date(r), str(r.get("pub") or ""), r.get("hash", "")))
    by_round: dict[str, list[dict]] = {}
    for row in rows:
        by_round.setdefault(round_date(row), []).append(row)
    reps = {day: min(members, key=_strength) for day, members in by_round.items()}
    rep_hashes = {rep["hash"] for rep in reps.values()}
    latest_day = max(by_round)
    # 제목·요약은 기사가 가장 많이 나온 회차의 대표에서 — 사건이 알려진 날이다. 마지막 회차 대표는
    # 뒤늦게 사건 이름을 빌려 단 곁가지 기사일 때가 많다(백필 실측: 국감 기사가 「추진 결정」의 제목이 됐다).
    peak_day = max(by_round, key=lambda day: (len(by_round[day]), day))
    headline = reps[peak_day]
    related = [_article_view(row, "card" if row["hash"] in rep_hashes else "evidence") for row in rows]
    related.sort(key=lambda a: (a["article_date"], a["member_role"] == "card"), reverse=True)
    verification = verify(related) if verify else {}
    own = [r for r in rows if r.get("judge") != "inherited"]
    pick = lambda key: next((r.get(key) for r in reversed(own or rows) if r.get(key)), "")  # noqa: E731
    detail_row = next((r for r in reversed(rows) if r.get("detail")), None)
    round_scores = {}
    for day, members in by_round.items():
        outlets = len({_publisher(r).lower() for r in members if _publisher(r)})
        best = max(members, key=lambda r: (int(r.get("significance") or 0), r.get("judge") != "inherited"))
        total, breakdown = score(best, outlets, verification)
        round_scores[day] = {"score": total, "breakdown": breakdown, "articles": len(members),
                             "outlets": outlets}
    days_quiet = (datetime.fromisoformat(today) - datetime.fromisoformat(latest_day)).days
    actor_names = list(dict.fromkeys(n for r in rows for n in (r.get("actor_names") or [])))
    stages = list(dict.fromkeys(r.get("stage") for r in reversed(rows) if r.get("stage")))
    return {
        "issue_id": _issue_id(rows[0]),
        "lane": "deliberation",
        "title": headline.get("title_kr") or headline.get("title", ""),
        "headline_display": headline.get("title_kr") or headline.get("title", ""),
        "event_key": headline.get("event_key", ""),
        "summary": headline.get("summary") or pick("summary"),
        "detail": (detail_row or {}).get("detail", ""),
        "detail_source": ((detail_row or {}).get("title_kr", "")
                          if detail_row and detail_row is not headline else ""),
        "why_important": pick("why_important"),
        "implication": pick("implication"),
        "open_question": "",
        "topics": stages,
        "tags": actor_names[:8],
        "regions": ["국내"],
        "countries": ["KR"],
        "entity_ids": [],
        "importance": "nice_to_know",
        "first_seen": min(by_round),
        "last_seen": latest_day,
        "rounds": sorted(by_round, reverse=True),
        "tracked_briefings": len(by_round),
        "briefing_count": len(by_round),
        "status": "ongoing" if len(by_round) > 1 else "new",
        "lifecycle": "quiet" if days_quiet > QUIET_AFTER_DAYS else "active",
        "article_count": len(related),
        "card_article_count": sum(1 for a in related if a["member_role"] == "card"),
        "evidence_article_count": sum(1 for a in related if a["member_role"] == "evidence"),
        "story_article_count": len(related),
        "story_outlet_count": len({a["publisher"].lower() for a in related if a["publisher"]}),
        "related_articles": related,
        "verification": verification,
        "stage": headline.get("stage", ""),
        "actor_type": headline.get("actor_type", ""),
        "actor_names": actor_names,
        "actor_types": [k for k, _ in Counter(r.get("actor_type") for r in rows if r.get("actor_type")).most_common()],
        "stance": headline.get("stance", ""),
        "stance_counts": dict(Counter(r.get("stance") for r in rows if r.get("stance")).most_common()),
        "article_type": headline.get("article_type", ""),
        "editorial": headline.get("article_type") == "editorial",
        "significance": max(int(r.get("significance") or 0) for r in rows),
        # 모든 기사가 '검토'(확신 낮음)면 사건 자체가 범위 경계에 있다 — 화면이 표시한다.
        "review": all(r.get("confidence") == "low" for r in rows),
        "round_scores": round_scores,
        "judges": sorted({str(r.get("judge") or "") for r in rows} - {""}),
    }


def _ordered(issues: list[dict], day: str) -> list[dict]:
    return sorted(issues, key=lambda i: (-i["round_scores"][day]["score"],
                                         -i["round_scores"][day]["articles"], i["issue_id"]))


def build_payload(rows: list[dict] | None = None, *, now: datetime | None = None,
                  verify: Callable[[list[dict]], dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = load_rows() if rows is None else rows
    today = now.astimezone(KST).date().isoformat()
    pending_rows = [r for r in rows if is_pending(r)]
    rows = [r for r in rows if not is_pending(r)]
    issues = [build_issue(group, today=today, verify=verify) for group in group_rows(rows)]
    pending_issues = [dict(build_issue(group, today=today, verify=verify), pending=True)
                      for group in group_rows(pending_rows)]
    # 오늘 들어온 사건의 회차는 내일이다 — 상세의 「○월 ○일 시작」은 회차가 아니라 기사 날짜로 둔다.
    for issue in pending_issues:
        issue["first_seen"] = min((a["article_date"] for a in issue["related_articles"] if a["article_date"]),
                                  default=issue["first_seen"])
    pending_day = round_date(pending_rows[0]) if pending_rows else ""
    pending_issues = _ordered(pending_issues, pending_day) if pending_issues else []
    by_day: dict[str, list[dict]] = {}
    for issue in issues:
        for day in issue["rounds"]:
            by_day.setdefault(day, []).append(issue)
    round_rows = []
    for day in sorted(by_day, reverse=True):
        members = [r for r in rows if round_date(r) == day]
        round_rows.append({
            "date": day,
            "issue_ids": [i["issue_id"] for i in _ordered(by_day[day], day)],
            "article_count": len(members),
            "outlet_count": len({_publisher(r).lower() for r in members if _publisher(r)}),
        })
    issues.sort(key=lambda i: (i["last_seen"], i["significance"]), reverse=True)
    recent_cut = (now.astimezone(KST) - timedelta(days=14)).date().isoformat()
    recent_articles = sum(r["article_count"] for r in round_rows if r["date"] > recent_cut)
    # 지금 국면 — 가장 최근의 공식 사건(중대성 3)의 국면. 입장·반응은 국면이 아니다.
    # 오늘 들어온 기사(pending)도 본다 — 「의제 확정」처럼 오늘의 공식 사건이 곧 지금 국면이다.
    milestones = [i for i in issues + pending_issues if i["significance"] >= 3 and i["stage"] != "reaction"]
    current = max(milestones, key=lambda i: (i["last_seen"], i["first_seen"]), default=None)
    labels = {key: lane.LABELS_KR[key] for key in
              lane.STAGES + lane.ACTOR_TYPES + lane.STANCES + lane.ARTICLE_TYPES + lane.CONFIDENCE}
    all_rows = rows + pending_rows
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "today": today,
        "latest_round": round_rows[0]["date"] if round_rows else "",
        "latest_briefing": rounds_of.latest_briefing_date(),
        "current_stage": ({"stage": current["stage"], "label": lane.LABELS_KR.get(current["stage"], ""),
                           "issue_id": current["issue_id"], "title": current["title"],
                           # 회차 날짜가 아니라 기사 날짜 — 오늘 들어온 사건의 회차는 내일이다.
                           "date": min((a["article_date"] for a in current["related_articles"] if a["article_date"]),
                                       default=current["first_seen"])} if current else {}),
        "pending": {
            "date": pending_day,
            "issues": pending_issues,
            "article_count": len(pending_rows),
            "outlet_count": len({_publisher(r).lower() for r in pending_rows if _publisher(r)}),
        },
        "rounds": round_rows,
        "issues": issues,
        "labels": labels,
        "stats": {
            "articles": len(all_rows),
            "outlets": len({_publisher(r).lower() for r in all_rows if _publisher(r)}),
            "issues": len(issues),
            "rounds": len(round_rows),
            "editorials": sum(1 for r in all_rows if r.get("article_type") == "editorial"),
            "review": sum(1 for r in all_rows if r.get("confidence") == "low"),
            "last_14d_daily_articles": round(recent_articles / 14, 1),
            "by_stage": dict(Counter(i["stage"] for i in issues).most_common()),
            "by_actor": dict(Counter(r.get("actor_type") for r in all_rows if r.get("actor_type")).most_common()),
            "by_stance": dict(Counter(r.get("stance") for r in all_rows if r.get("stance")).most_common()),
        },
    }
