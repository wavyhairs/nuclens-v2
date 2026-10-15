"""해외 AI 레인 → 웹 산출물(ai_issues.json). 4단계부터 이슈에 tech_scope(ai|digital)가 붙는다.

`ai_archive/` 의 판정 통과 기사를 **회차**와 **이슈**로 묶는다. 화면(「해외 AI」탭)·탐색의
"해외 AI" 범위·이슈 상세가 이 파일 하나만 읽는다.

회차
----
기사가 메인에 수집된 뒤(``collected_at``) **처음 나가는 아침 브리핑의 날짜**가 회차 날짜다
(2026-10-15 정정 — 아래 round_date). 레인이 한도 때문에 늦게 판정해도 기사는 수집 시각의
회차에 들어간다. 기사가 없는 날은 회차가 없다 — 날짜 이동은 회차가 있는 날만 오간다.
마지막 브리핑 뒤 기사는 ``pending`` 으로 따로 싣는다.

이슈 (같은 사건 묶기)
--------------------
하루 0~3건인 레인에 메인의 이슈 매칭 기계(임베딩·사건 정체성·검수 큐)는 과하다. 대신
설명 가능한 규칙 두 개만 쓴다.

  1. 제목이 같으면 같은 사건.
  2. **주체가 겹치고 적용 영역이 같고** 30일 안이면 같은 사건.
     실측 근거: NIVA 다섯 건(8/18~10/9, 토큰포스트·WNN·NEI·POWER·IEEE)이 전부
     주체 "Atomic Canyon" + 운영·정비였다. 같은 벤더의 다른 고객 발표가 영역까지 같으면
     함께 묶이는데, 그것은 "그 제품의 도입 타임라인"으로 읽혀 해롭지 않다.

이슈의 대표는 각 회차에서 근거가 가장 강한 기사(공식 원문 > 등급 > 먼저 나온 것)다.
대표는 상세의 "이 사건의 근거"(card), 나머지는 "추가 근거"(evidence)로 실린다.

중요도 (회차 안 정렬)
--------------------
LLM 에 중요도를 묻지 않는다. 판정이 낸 feature 로 Python 이 점수를 매기고 내역을 남긴다
(ranking.py 와 같은 원칙). ``도입 중대성×3 + 성숙도 + 주체 + 근거 구성``.

가드레일: stdlib + data_quality 만. build_data 의 검증 함수는 주입받는다.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import ai_lane
from data_quality import title_key

BASE = Path(__file__).parent
ARCHIVE_DIR = Path(os.environ.get("AI_ARCHIVE_DIR") or (BASE / "ai_archive"))
KST = timezone(timedelta(hours=9))
SCHEMA = "nuclens-ai-v1"
GROUP_WINDOW_DAYS = 30
QUIET_AFTER_DAYS = 30

MATURITY_POINTS = {"deployed": 3, "regulatory": 3, "contract": 3, "pilot": 2, "announced": 1,
                   "research": 0, "discussion": 0}
ACTOR_POINTS = {"utility": 2, "regulator": 2, "international_org": 2, "national_lab": 1,
                "vendor": 0, "other": 0, "ai_startup": -1}
VENDOR_ACTORS = {"vendor", "ai_startup"}


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
            # 메인이 국내로 본 기사는 싣지 않는다(ai_lane_pipeline.domestic_guard 와 같은 규칙).
            if isinstance(row, dict) and row.get("main_scope") == "kr":
                continue
            if isinstance(row, dict) and digest and digest not in seen:
                seen.add(digest)
                rows.append(row)
    return rows


# ── 회차 = 아침 브리핑 회차 (2026-10-15) ────────────────────────────────────
# 방향 문서 §4-1·§7-2 의 설계는 "그날 = 아침 브리핑과 같은 마감"이었는데, 2단계 구현은
# 수집 시각의 한국 날짜를 썼다. 그래서 새벽 크롤이 잡은 기사가 아침 브리핑이 나가기 전에
# 이미 「10월 15일」로 화면에 서고, 하루 동안 같은 날짜 목록이 계속 바뀌었다(사용자 지적).
# 이제 기사는 **수집된 뒤 처음 나가는 아침 브리핑의 날짜**로 간다. 브리핑 마감 시각은
# delivery_log.jsonl 의 selection_stats(그날 첫 선정 시각)다 — freshness.last_brief_at 과
# 같은 기준. 마지막 브리핑 뒤에 들어온 기사는 '다음 브리핑에 실릴 기사'(pending)로 따로 싣는다.
DELIVERY_LOG = Path(os.environ.get("AI_DELIVERY_LOG") or (BASE / "delivery_log.jsonl"))
_CUTOFFS: list[tuple[datetime, str]] | None = None


def load_briefing_cutoffs(path: Path = DELIVERY_LOG) -> list[tuple[datetime, str]]:
    """[(그날 첫 선정 시각, 브리핑 날짜)] 시각순. 재실행으로 줄이 여럿이면 가장 이른 것."""
    first: dict[str, datetime] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if '"selection_stats"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        day = str(row.get("date") or "")
        try:
            at = datetime.fromisoformat(str(row.get("generated_at") or ""))
        except ValueError:
            continue
        if row.get("record_type") != "selection_stats" or not day or at.tzinfo is None:
            continue
        if day not in first or at < first[day]:
            first[day] = at
    return sorted((at, day) for day, at in first.items())


def set_briefing_cutoffs(cutoffs: list[tuple[datetime, str]] | None) -> None:
    """테스트·빌드가 마감 목록을 주입한다. None 이면 다음 호출 때 파일에서 다시 읽는다."""
    global _CUTOFFS
    _CUTOFFS = cutoffs


def briefing_cutoffs() -> list[tuple[datetime, str]]:
    global _CUTOFFS
    if _CUTOFFS is None:
        _CUTOFFS = load_briefing_cutoffs()
    return _CUTOFFS


def latest_briefing_date() -> str:
    cutoffs = briefing_cutoffs()
    return cutoffs[-1][1] if cutoffs else ""


def _collected(row: dict) -> datetime | None:
    stamp = str(row.get("collected_at") or row.get("archived_at") or "")
    try:
        value = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _calendar_day(row: dict) -> str:
    stamp = str(row.get("collected_at") or row.get("archived_at") or "")
    try:
        return datetime.fromisoformat(stamp).astimezone(KST).date().isoformat()
    except ValueError:
        return stamp[:10]


def is_pending(row: dict) -> bool:
    """마지막 아침 브리핑보다 뒤에 수집된 기사 — 아직 어느 브리핑에도 실리지 않았다."""
    cutoffs = briefing_cutoffs()
    collected = _collected(row)
    return bool(cutoffs) and collected is not None and collected > cutoffs[-1][0]


def round_date(row: dict) -> str:
    """기사가 실리는 아침 브리핑 날짜. 마감 기록이 없거나 기록보다 앞선 기사는 달력 날짜.

    마지막 브리핑 뒤 기사(is_pending)는 **다음 브리핑 날짜**(마지막 브리핑 다음 날)를 받는다 —
    화면은 그 회차를 확정 회차와 따로 '다음 브리핑에 실릴 기사'로 보인다.
    """
    cutoffs = briefing_cutoffs()
    collected = _collected(row)
    if not cutoffs or collected is None or collected < cutoffs[0][0] - timedelta(days=2):
        return _calendar_day(row)
    times = [at for at, _ in cutoffs]
    index = bisect.bisect_left(times, collected)
    if index < len(cutoffs):
        return cutoffs[index][1]
    nxt = datetime.fromisoformat(cutoffs[-1][1]) + timedelta(days=1)
    return nxt.date().isoformat()


def article_date(row: dict) -> str:
    stamp = str(row.get("pub") or "")
    try:
        return datetime.fromisoformat(stamp).astimezone(KST).date().isoformat()
    except ValueError:
        return round_date(row)


def _actors(row: dict) -> set[str]:
    return {" ".join(str(name).lower().split()) for name in row.get("actor_names") or [] if str(name).strip()}


def _strength(row: dict) -> tuple:
    official = row.get("evidence_role") == "primary" or row.get("source_type") == "official"
    independent = row.get("evidence_role") == "independent"
    return (not official, not independent, int(row.get("source_tier") or 3),
            str(row.get("pub") or ""), row.get("hash", ""))


def group_rows(rows: list[dict]) -> list[list[dict]]:
    """기사 → 사건 묶음. 수집 순서로 훑으며 앞선 묶음에 붙일 수 있으면 붙인다."""
    groups: list[dict] = []
    for row in sorted(rows, key=lambda r: (str(r.get("collected_at") or ""), r.get("hash", ""))):
        day = datetime.fromisoformat(round_date(row))
        tkey = title_key(row.get("title_kr") or row.get("title"))
        actors = _actors(row)
        home = None
        for group in reversed(groups):
            if (day - group["last_day"]).days > GROUP_WINDOW_DAYS:
                continue
            if tkey and tkey in group["titles"]:
                home = group
                break
            if actors & group["actors"] and row.get("application_area") in group["areas"]:
                home = group
                break
        if home is None:
            home = {"rows": [], "titles": set(), "actors": set(), "areas": set(), "last_day": day}
            groups.append(home)
        home["rows"].append(row)
        home["titles"].add(tkey)
        home["actors"] |= actors
        home["areas"].add(row.get("application_area"))
        home["last_day"] = max(home["last_day"], day)
    return [group["rows"] for group in groups]


def score(row: dict, verification: dict | None = None) -> tuple[int, str]:
    significance = int(row.get("adoption_significance") or 0)
    maturity = MATURITY_POINTS.get(row.get("maturity"), 0)
    actor = ACTOR_POINTS.get(row.get("actor_type"), 0)
    evidence = 0
    if verification:
        if verification.get("official_source_count"):
            evidence += 1
        if int(verification.get("independent_source_count") or 0) >= 2:
            evidence += 1
    total = significance * 3 + maturity + actor + evidence
    label = lambda key: ai_lane.LABELS_KR.get(key, key)  # noqa: E731
    parts = [f"도입 중대성 {significance}×3", f"{label(row.get('maturity'))} {maturity:+d}",
             f"{label(row.get('actor_type'))} {actor:+d}"]
    if evidence:
        parts.append(f"근거 구성 {evidence:+d}")
    return total, " · ".join(parts)


def _tech_scope(row: dict) -> str:
    """ai | digital. 4단계 전 레코드(수동 백필·v5 판정)에는 칸이 없다 — 전부 AI 범위였다."""
    scope = row.get("tech_scope")
    if scope in ai_lane.TECH_SCOPES:
        return scope
    return "digital" if row.get("ai_tech") in ai_lane.DIGITAL_AI_TECH else "ai"


def _issue_id(first: dict) -> str:
    return "ai-" + hashlib.sha1(str(first.get("hash") or "").encode("utf-8")).hexdigest()[:12]


def _article_view(row: dict, role: str, briefing_date: str) -> dict:
    return {
        "hash": row.get("hash", ""),
        "title_kr": row.get("title_kr") or row.get("title", ""),
        "title": row.get("title", ""),
        "url": row.get("resolved_url") or row.get("url", ""),
        "publisher": row.get("publisher", ""),
        "domain": row.get("domain", ""),
        "source_type": row.get("source_type", ""),
        "evidence_role": row.get("evidence_role", ""),
        "source_tier": row.get("source_tier", 3),
        "article_date": article_date(row),
        "briefing_date": briefing_date if role == "card" else "",
        "member_role": role,
        "detail": row.get("detail", ""),
        "summary": row.get("summary", ""),
        "countries": row.get("countries") or [],
        "maturity": row.get("maturity", ""),
        "application_area": row.get("application_area", ""),
    }


def build_issue(rows: list[dict], *, today: str,
                verify: Callable[[list[dict]], dict] | None = None) -> dict:
    rows = sorted(rows, key=lambda r: (round_date(r), str(r.get("pub") or "")))
    by_round: dict[str, list[dict]] = {}
    for row in rows:
        by_round.setdefault(round_date(row), []).append(row)
    reps = {day: min(members, key=_strength) for day, members in by_round.items()}
    rep_hashes = {rep["hash"] for rep in reps.values()}
    latest_day = max(by_round)
    latest = reps[latest_day]
    related = []
    for row in rows:
        day = round_date(row)
        role = "card" if row["hash"] in rep_hashes else "evidence"
        related.append(_article_view(row, role, day))
    related.sort(key=lambda a: (a["article_date"], a["member_role"] == "card"), reverse=True)
    verification = verify(related) if verify else {}
    detail_row = next((r for r in reversed(rows) if r.get("detail")), None)
    countries = list(dict.fromkeys(c for r in rows for c in (r.get("countries") or [])))
    actors = list(dict.fromkeys(n for r in rows for n in (r.get("actor_names") or [])))
    round_scores = {}
    for day, rep in reps.items():
        best = max(by_round[day], key=lambda r: score(r, verification)[0])
        total, breakdown = score(best, verification)
        round_scores[day] = {"score": total, "breakdown": breakdown}
    days_quiet = (datetime.fromisoformat(today) - datetime.fromisoformat(latest_day)).days
    pick = lambda key: next((r.get(key) for r in reversed(rows) if r.get(key)), "")  # noqa: E731
    return {
        "issue_id": _issue_id(rows[0]),
        "lane": "ai",
        "title": latest.get("title_kr") or latest.get("title", ""),
        "headline_display": latest.get("title_kr") or latest.get("title", ""),
        "summary": latest.get("summary", ""),
        "detail": (detail_row or {}).get("detail", ""),
        "detail_source": ((detail_row or {}).get("title_kr", "")
                          if detail_row and detail_row is not latest else ""),
        "why_important": latest.get("why_important") or pick("why_important"),
        "implication": latest.get("implication") or pick("implication"),
        "open_question": "",
        "topics": list(dict.fromkeys(r.get("application_area") for r in rows if r.get("application_area"))),
        "tags": actors[:8],
        "regions": ["해외"],
        "countries": countries,
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
        "story_outlet_count": len({(a["publisher"] or a["domain"]).lower() for a in related}),
        "related_articles": related,
        "verification": verification,
        "application_area": latest.get("application_area", ""),
        "maturity": latest.get("maturity", ""),
        "tech_scope": _tech_scope(latest),
        "ai_tech": latest.get("ai_tech", ""),
        "actor_type": latest.get("actor_type", ""),
        "actor_names": actors,
        "vendor_claim": latest.get("actor_type") in VENDOR_ACTORS
        and latest.get("maturity") in ("announced", "contract"),
        "adoption_significance": max(int(r.get("adoption_significance") or 0) for r in rows),
        "round_scores": round_scores,
        "judges": sorted({str(r.get("judge") or "") for r in rows} - {""}),
    }


def build_payload(rows: list[dict] | None = None, *, now: datetime | None = None,
                  verify: Callable[[list[dict]], dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = load_rows() if rows is None else rows
    today = now.astimezone(KST).date().isoformat()
    # 마지막 아침 브리핑 뒤에 들어온 기사는 확정 회차에 넣지 않는다 — 다음 브리핑이 나가면
    # 그 날짜 회차로 들어간다. 그때까지는 '다음 브리핑에 실릴 기사'로 따로 보인다.
    pending_rows = [r for r in rows if is_pending(r)]
    rows = [r for r in rows if not is_pending(r)]
    issues = [build_issue(group, today=today, verify=verify) for group in group_rows(rows)]
    pending_issues = [dict(build_issue(group, today=today, verify=verify), pending=True)
                      for group in group_rows(pending_rows)]
    pending_issues.sort(key=lambda i: (-max(v["score"] for v in i["round_scores"].values()), i["issue_id"]))
    rounds: dict[str, list[dict]] = {}
    for issue in issues:
        for day in issue["rounds"]:
            rounds.setdefault(day, []).append(issue)
    round_rows = []
    for day in sorted(rounds, reverse=True):
        ordered = sorted(rounds[day], key=lambda i: (-i["round_scores"][day]["score"],
                                                     i["issue_id"]))
        round_rows.append({
            "date": day,
            "issue_ids": [i["issue_id"] for i in ordered],
            "article_count": sum(1 for r in rows if round_date(r) == day),
        })
    issues.sort(key=lambda i: (i["last_seen"], i["adoption_significance"]), reverse=True)
    recent_cut = (now.astimezone(KST) - timedelta(days=14)).date().isoformat()
    recent_events = sum(len(r["issue_ids"]) for r in round_rows if r["date"] > recent_cut)
    labels = {key: ai_lane.LABELS_KR[key] for key in
              ai_lane.APPLICATION_AREAS + ai_lane.MATURITY + ai_lane.AI_TECH + ai_lane.ACTOR_TYPES
              + ai_lane.TECH_SCOPES}
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "today": today,
        "latest_round": round_rows[0]["date"] if round_rows else "",
        # 회차의 기준. 화면은 '오늘'이 아니라 이 날짜와 최신 회차를 비교한다.
        "latest_briefing": latest_briefing_date(),
        "pending": {
            "date": round_date(pending_rows[0]) if pending_rows else "",
            "issues": pending_issues,
            "article_count": len(pending_rows),
        },
        "rounds": round_rows,
        "issues": issues,
        "labels": labels,
        # 화면의 나라 표(COUNTRY_LABELS)는 지도 격자와 함께 잠겨 있다 — 거기 없는 나라
        # (BD 방글라데시 등)는 이 표로 받는다. 없으면 코드가 그대로 화면에 나갔다(2026-10-15).
        "country_labels": dict(COUNTRY_KR),
        "stats": {
            "articles": len(rows),
            "issues": len(issues),
            "rounds": len(round_rows),
            "last_14d_daily_avg": round(recent_events / 14, 2),
            "by_area": dict(Counter(i["application_area"] for i in issues).most_common()),
            "by_maturity": dict(Counter(i["maturity"] for i in issues).most_common()),
            "by_scope": dict(Counter(i["tech_scope"] for i in issues).most_common()),
        },
    }


COUNTRY_KR = {"US": "미국", "CA": "캐나다", "JP": "일본", "CN": "중국", "FR": "프랑스", "GB": "영국",
              "SE": "스웨덴", "FI": "핀란드", "DE": "독일", "KR": "한국", "IN": "인도", "RU": "러시아",
              "GLOBAL": "글로벌", "EU": "EU",
              # 4단계 — 러시아어·중국어·규제기관 피드가 들어오며 늘어난 나라.
              "BD": "방글라데시", "AE": "UAE", "CZ": "체코", "UA": "우크라이나", "ES": "스페인",
              "BR": "브라질", "AR": "아르헨티나", "ZA": "남아공", "TR": "튀르키예", "EG": "이집트",
              "CH": "스위스", "BE": "벨기에", "NL": "네덜란드", "PL": "폴란드", "HU": "헝가리",
              "RO": "루마니아", "SK": "슬로바키아", "SI": "슬로베니아", "BG": "불가리아", "TW": "대만",
              "AU": "호주", "SA": "사우디", "IT": "이탈리아"}


def weekly_digest(week_start: str, week_end: str, rows: list[dict] | None = None,
                  *, limit: int = 3) -> dict:
    """주간 판세의 「해외 원전 AI」 단락. LLM 없이 이번 주 회차의 사건을 점수순으로 고른다.

    그 주에 회차가 하나도 없으면 빈 dict — 단락이 서지 않는다(수를 채우지 않는다).
    """
    rows = load_rows() if rows is None else rows
    week_rows = [r for r in rows if week_start <= round_date(r) <= week_end and not is_pending(r)]
    if not week_rows:
        return {}
    picked = []
    for group in group_rows(week_rows):
        best = max(group, key=lambda r: (score(r)[0], str(r.get("pub") or "")))
        latest = max(group, key=lambda r: round_date(r))
        countries = [c for c in (latest.get("countries") or []) if c]
        picked.append({
            "title": latest.get("title_kr") or latest.get("title", ""),
            "score": score(best)[0],
            "country_kr": " · ".join(COUNTRY_KR.get(c, c) for c in countries[:2]),
            "area_kr": ai_lane.LABELS_KR.get(latest.get("application_area"), ""),
            "maturity_kr": ai_lane.LABELS_KR.get(latest.get("maturity"), ""),
            "scope_kr": ai_lane.LABELS_KR.get(_tech_scope(latest), ""),
            "vendor_claim": latest.get("actor_type") in VENDOR_ACTORS
            and latest.get("maturity") in ("announced", "contract"),
        })
    picked.sort(key=lambda row: -row["score"])
    return {"count": len(picked), "articles": len(week_rows), "items": picked[:limit]}
