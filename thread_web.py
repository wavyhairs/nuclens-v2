"""장기 스토리 원장을 **화면이 읽을 수 있는 것**으로 옮긴다 → data/threads.json.

무엇을 하는가
-------------
`thread_ledger.json`(영속) + `issue_ledger.json`(사건 본문) + `event_ledger.json`
(미래 일정)을 합쳐 한 파일을 만든다. 새로 판정하지 않고 **LLM 을 부르지 않는다** —
이 단계는 순수 투영이라 빌드마다 돌아도 공짜다.

    thread_ledger.json   ← 하루 1회 tools/build_threads.py 가 판정해서 쌓는다
    threads.json         ← 빌드마다(하루 8회+) 이 모듈이 다시 만든다

그래서 두 주기가 갈린다. 판정은 비싸고 투영은 싸다.

무엇을 하지 않는가
------------------
**기존 "스토리" 메뉴를 건드리지 않는다.** 그 화면은 `issues.json` 을 거른 목록이고
단위가 이슈다. 여기 있는 것은 그 이슈들을 묶은 상위 객체라 목록의 단위가 다르다
(`docs/2026-09-13-long-term-story.md` F12). 두 화면이 따로 선다.

안전장치 — 왜 숨기는 쪽이 기본인가
----------------------------------
이 계층은 Beta 다. 판정이 자동이고 앵커율이 낮아(121개 중 호기 앵커 8개) 오병합이
남아 있다. 그래서 **의심스러우면 화면을 통째로 내린다**:

    build_unknown    판정 빌드가 무엇을 했는지 원장에 없다
    build_incomplete 판정하지 못한 후보가 허용치를 넘었다 (아래 비율)
    stale            원장이 STALE_HOURS 보다 오래됐다 — 정기 빌드가 멈춘 것이다
    too_few          스토리가 너무 적다. 한산한 날이 아니라 파이프라인이 깨진 신호다
    no_ledger        원장이 없거나 비었다
    switched_off     운영 스위치로 껐다

불완전한 판정 그래프에서 나온 값을 내보내지 않는다는 규칙은 F 단계에서 이미 한 번
값을 치르고 정한 것이다(RPD 소진으로 60쌍이 미판정이던 회차의 수치가 겉보기에는
최종과 같았지만 정본에 쓰지 않았다).

숨김은 **두 겹**이다. 여기서 한 번 재고, 화면이 `hide_after` 로 한 번 더 잰다 —
배포 자체가 멈춰 낡은 파일이 CDN 에 남는 경우는 빌드 시점 판정으로 잡을 수 없다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asset_alias
import entity_match
import event_retrieval
import thread_ledger

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))

CONTRACT_VERSION = "thread-web-v1"

# 원장이 이만큼 낡으면 화면을 내린다. 판정 빌드는 하루 1회라 사흘은 세 번을
# 내리 놓쳤다는 뜻이다 — 그 정도면 고장이지 한산한 날이 아니다.
STALE_HOURS = max(1, int(os.environ.get("NUCLENS_THREAD_STALE_HOURS") or 72))
# 목록이 한 화면을 못 채우면 화면이 아니다. 811건 원장에서 실측 121개가 나오므로
# 한 자릿수로 떨어지는 것은 뉴스가 조용해서가 아니라 검색이나 판정이 깨진 것이다.
MIN_THREADS = 8

# 판정하지 못한 후보의 허용 비율. **status 하나로 가르지 않는 이유**가 있다 —
# 후보 생성은 점수 동률 경계에서 회차마다 몇 쌍씩 흔들려서(집합 순회 순서),
# 정상적인 회차도 처음 보는 쌍 서너 개를 달고 온다. 그걸로 화면을 내리면
# 안전장치가 상시 켜져 있는 소음이 된다.
#
# 값은 실제 사고에 맞춰 잡았다: 2026-09-13 의 Gemini RPD 소진 회차는 3,068쌍 중
# 60쌍(2.0%)이 미판정이었고 그 수치는 정본에 쓰지 않기로 한 회차다. 경계선 잡음
# (4/3,069 = 0.1%)은 통과하고 그 사고는 확실히 걸리는 자리가 1% 다.
MAX_UNJUDGED_RATIO = 0.01

# 이 화면의 이름이 장기 스토리다. 하루 안에 끝난 묶음은 장기 스토리가 아니라
# **사건 계층의 중복**이다 — 같은 일을 이틀에 걸쳐 보도한 것을 event matcher 가
# 못 합쳤고 thread judge 가 뒤늦게 이었을 뿐이라, 목록에 세우면 화면이 제 이름과
# 다른 것을 보여 준다(실측 2026-09-13: 121개 중 20개가 0~1일짜리 2건 묶음).
# 판정을 되돌리는 것이 아니라 이 화면의 자격만 정하는 값이다.
MIN_LIFESPAN_DAYS = 2

# 운영 스위치. off 는 사람이 내리는 kill switch 고, on 은 로컬에서 키 없이
# 화면을 볼 때만 쓴다(그때 status 는 no_api_key 라 자동 판정은 숨긴다).
SWITCH_ENV = "NUCLENS_LONG_TERM_STORY"


def _switch() -> str:
    value = str(os.environ.get(SWITCH_ENV) or "auto").strip().lower()
    return value if value in ("on", "off", "auto") else "auto"


def _now() -> datetime:
    return datetime.now(KST)


def _parse_ts(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _age_hours(value: object, now: datetime) -> float | None:
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 3600


# ── 이름표 ────────────────────────────────────────────────────────────────
# 원장은 id 로만 말한다(`khnp` · `saeul-3`). 화면에 id 를 그대로 내보내면 읽는
# 사람이 사전을 들고 와야 하므로 **빌드가 이름을 붙인다** — 레지스트리가 이미
# 여기 있고 프런트에는 없다.

def _registry() -> tuple[dict[str, str], dict[str, str]]:
    entities: dict[str, str] = {}
    plants: dict[str, str] = {}
    for entity in entity_match.load_entity_registry():
        entity_id = str(entity.get("id") or "")
        name = str(entity.get("name_kr") or entity.get("name_en") or "").strip()
        if not entity_id or not name:
            continue
        entities[entity_id] = name
        if entity.get("type") == "plant":
            plants[entity_id] = name.replace("원전", "").strip() or name
    return entities, plants


def unit_label(unit: str, plants: dict[str, str]) -> str:
    """`saeul-3` → `새울 3호기`. 발전소 이름을 모르면 id 를 그대로 돌려준다."""
    plant, _, number = str(unit or "").rpartition("-")
    if not plant or not number.isdigit():
        return str(unit or "")
    name = plants.get(plant)
    return f"{name} {int(number)}호기" if name else str(unit)


# ── 다음 관전점 ───────────────────────────────────────────────────────────
# `tools/build_threads.py` 가 쓰던 것을 여기로 옮겼다. 그림자 빌드와 웹 투영이
# 각자 짝을 지으면 같은 스토리에 다른 일정이 붙는다.

def load_milestones(path: Path | None = None) -> list[dict]:
    """미래 일정의 재료. **새 일정 시스템을 만들지 않는다** —

    `event_ledger.json` 이 이미 달력 창 밖의 확정 일정을 쌓고 있다(월성 2호기
    설계수명 만료 2026-11-01 …). 추론으로 읽은 날짜는 애초에 담기지 않으므로
    (`date_basis` 가 inferred 인 것은 제외) 이 목록은 그대로 쓸 수 있다.
    """
    try:
        payload = json.loads(
            (path or (ROOT / "event_ledger.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = []
    for row in payload.get("events") or []:
        text = f"{row.get('title') or ''} {row.get('clause') or ''}"
        rows.append({**row, "units": asset_alias.unit_tokens(text),
                     "plants": asset_alias.plant_tokens(text)})
    rows.sort(key=lambda row: str(row.get("date") or ""))
    return rows


def next_milestone(thread: dict, milestones: list[dict]) -> dict | None:
    """이 스토리가 다음에 볼 공식 일정. 호기가 먼저, 없으면 발전소."""
    units = set(thread.get("units") or ())
    plants = {unit.rsplit("-", 1)[0] for unit in units}
    for row in milestones:
        if units and (units & row["units"]):
            return {"date": row.get("date"), "title": row.get("title"),
                    "label": row.get("label"), "matched_by": "unit"}
    for row in milestones:
        if plants and (plants & row["plants"]):
            return {"date": row.get("date"), "title": row.get("title"),
                    "label": row.get("label"), "matched_by": "plant"}
    return None


# ── 죽은 주소를 내보내지 않는다 ───────────────────────────────────────────
# `event_retrieval.load_events` 는 `issue_ledger.json` 의 **모든** 항목을 사건으로
# 낸다. 원장은 아카이브라 prune 이 없으므로 거기에는 이미 다른 이슈로 흡수된
# 항목(`moved_to`)도 그대로 남아 있다 — 실측 2026-09-13: 811건 중 219건(27%).
#
# 그 id 를 타임라인 링크로 내보내면 화면에서 죽은 클릭이 된다. 흡수된 이슈는
# `build_issue_pages` 가 보관 스냅샷(`/data/issue/<id>.json`)을 만들지 않기
# 때문이다(`issue_ledger.archived` 가 moved 를 제외한다). 정적 주소
# `/issue/<id>/` 는 리다이렉트 쪽지가 받아 주지만 앱 안의 상세는 그 쪽지를 읽지
# 않아서, 라이브 실측 2026-09-13 기준 타임라인 317행 중 147행(46%)이 "이 이슈를
# 찾을 수 없습니다" 로 끝났다(101개 스토리 중 68개는 **맨 아래 행**이 그랬다).
#
# 그래서 링크만 현재 주소로 옮긴다. **행 자체는 지우지 않는다** — 흡수는 보통
# "중복이었다"가 아니라 "이 사건이 더 큰 이슈로 굴러 들어갔다"라서, 흡수된 쪽의
# 제목·날짜는 그날 실제로 보도된 기록이다(팍스 원전 스토리는 8행 중 6행이 한
# 이슈로 흡수됐지만 여섯 날의 내용이 전부 다르다). 지우면 이야기가 사라진다.

def surviving_id(event_id: str, by_id: dict) -> str:
    """`moved_to` 사슬의 끝 — 지금 실제로 열리는 주소. 고리를 만나면 멈춘다."""
    seen: set[str] = set()
    current = str(event_id)
    while current and current not in seen:
        seen.add(current)
        raw = getattr(by_id.get(current), "raw", None) or {}
        target = str(raw.get("moved_to") or "").strip()
        if not target:
            return current
        current = target
    return current


def _collapse_ghosts(members: list, by_id: dict) -> list:
    """같은 이슈로 흡수되면서 **제목까지 같은** 행은 한 번만 세운다.

    위에서 행을 지키기로 했지만, 한 가지는 지워야 한다: 흡수 전후의 두 항목이
    제목까지 같은 경우다. 그것은 이야기의 두 장면이 아니라 한 장면이 id 두 개를
    입고 나란히 선 것이라, 읽는 사람에게는 같은 줄이 두 번 찍힌 것으로만 보인다
    (실측 2026-09-13: 101개 스토리 중 77개에서 101행 — 317행이 216행이 됐다).
    제목이 다르면 남긴다.
    """
    seen: set[tuple[str, str]] = set()
    out = []
    for event in members:
        key = (surviving_id(event.issue_id, by_id),
               " ".join(str(event.title or "").split()))
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


# ── 살아 있는 스토리 ──────────────────────────────────────────────────────

def live_entries(store: dict) -> list[dict]:
    """원장에서 **이번 판정이 세운 것만** 고른다.

    원장은 지우지 않는다. 그래서 흡수된 스토리(`moved_to`)와, 구성원이 전부
    떨어져 나가 더는 서지 않는 스토리가 파일에 그대로 남아 있다. 후자는 옛
    `event_ids` 를 들고 있어 겉보기에 멀쩡하므로 **판정 빌드가 남긴 명단**으로
    가른다. 명단이 없는 옛 파일은 흡수되지 않은 것을 전부 살아 있다고 본다.
    """
    entries = store.get("threads") or {}
    live_ids = store.get("live_thread_ids")
    if isinstance(live_ids, list) and live_ids:
        wanted = [str(value) for value in live_ids]
        return [entries[thread_id] for thread_id in wanted
                if thread_id in entries and not entries[thread_id].get("moved_to")]
    return [entry for entry in entries.values() if not entry.get("moved_to")]


def _thread_view(entry: dict, by_id: dict, labels: tuple[dict, dict],
                 milestones: list[dict]) -> dict:
    entity_names, plant_names = labels
    members = [by_id[event_id] for event_id in (entry.get("event_ids") or ())
               if event_id in by_id]
    members.sort(key=lambda event: (event.first_seen or "", event.issue_id))
    members = _collapse_ghosts(members, by_id)
    first = next((event.first_seen for event in members if event.first_seen), None)
    last = max((event.last_seen for event in members if event.last_seen), default=None)
    # 원장의 first_seen 은 단조 감소만 하는 값이라(신원이 왕복하지 않게) 현재
    # 구성원보다 이를 수 있다. 화면에 적는 기간은 **지금 들고 있는 사건들**의
    # 것이어야 하므로 구성원에서 다시 잰다.
    first_seen = first.isoformat() if first else str(entry.get("first_seen") or "")
    last_seen = last.isoformat() if last else str(entry.get("last_seen") or "")
    units = [str(unit) for unit in (entry.get("units") or ())]
    entities = [str(value) for value in (entry.get("entity_ids") or ())]
    excluded = [str(unit) for unit in
                ((entry.get("scope") or {}).get("excludes") or {}).get("units") or ()]
    view = {
        "thread_id": str(entry.get("thread_id") or ""),
        # 원장의 title 은 **앵커(가장 이른 사건)의 제목**이다. 앵커가 가장 이른
        # 사건인 이유는 id 가 왕복하지 않게 하려는 것이지(`thread_identity.mint_id`)
        # 그 제목이 이야기를 가장 잘 부르기 때문이 아니다 — 제목은 거기에 얹혀
        # 갔을 뿐이다. 화면은 **지금 어디까지 왔나**를 묻는 자리라 최신 사건의
        # 제목을 건다(실측 2026-09-13: 101개 전부가 최초 제목이었고, 그중 67개는
        # 최신 제목과 달랐다 — 47일 된 카드가 첫날의 이름으로 서 있었다).
        # 원장은 그대로 둔다. 신원(안정)과 표시(현재)는 다른 일이다.
        "title": (members[-1].title if members else str(entry.get("title") or "")),
        # 이름이 움직여도 주소는 안 움직인다는 것을 화면이 말할 수 있게 남긴다.
        "origin_title": str(entry.get("title") or ""),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "lifespan_days": (last - first).days if (first and last) else None,
        "event_count": len(members),
        "briefing_count": sum(event.briefing_count for event in members),
        "units": units,
        "unit_labels": [unit_label(unit, plant_names) for unit in units],
        "entity_ids": entities,
        "entity_labels": [entity_names.get(value, value) for value in entities],
        # 범위는 두 쪽이 다 있어야 쓸모가 있다 — 무엇이 들어오는가와 **무엇이
        # 닮았지만 들어오지 않는가**. 자동 군집을 정직하게 보이는 자리다.
        "excluded_unit_labels": [unit_label(unit, plant_names) for unit in excluded[:6]],
        "identity_origin": str(entry.get("identity_origin") or ""),
        # 최신이 맨 위. 이 목록은 `last_seen` 으로 정렬된 **최근 움직인 것** 의
        # 목록이고, 카드 제목도 최신 사건을 건다 — 제목 바로 아래 첫 행이 그
        # 제목과 다른 날을 가리키면 읽는 사람에게는 어긋난 것으로 보인다.
        # 이슈 상세의 타임라인도 최신순이다(`byTimelineOrder`).
        #
        # 잃는 것은 적어 둔다: '착수 → 보류 → 결정' 이 위에서 아래로 거꾸로
        # 읽힌다. 훑는 화면에서는 "지금 어디까지 왔나"가 먼저라고 보고 그쪽을
        # 골랐다. 되돌리려면 여기 한 줄과 아래 검사 하나만 뒤집으면 된다.
        "events": [{
            # 제목·날짜는 그날의 기록이고, id 는 **지금 열리는 주소**다. 흡수된
            # 사건은 둘이 갈리므로 링크 쪽만 현재 주소로 옮긴다(위 주석).
            "event_id": surviving_id(event.issue_id, by_id),
            "title": event.title,
            "date": event.first_seen.isoformat() if event.first_seen else "",
            "last_seen": event.last_seen.isoformat() if event.last_seen else "",
            "briefing_count": event.briefing_count,
        } for event in reversed(members)],
    }
    view["next_milestone"] = next_milestone({"units": units}, milestones)
    return view


def _stats(threads: list[dict]) -> dict:
    lifespans = sorted(row["lifespan_days"] for row in threads
                       if row["lifespan_days"] is not None)
    return {
        "threads": len(threads),
        "events": sum(row["event_count"] for row in threads),
        "median_lifespan_days": lifespans[len(lifespans) // 2] if lifespans else 0,
        "max_lifespan_days": lifespans[-1] if lifespans else 0,
        "over_30_days": sum(1 for value in lifespans if value > 30),
        "with_milestone": sum(1 for row in threads if row.get("next_milestone")),
    }


def gate(store: dict, thread_count: int, *, now: datetime) -> tuple[bool, list[str]]:
    """화면을 내보낼지 정한다. 이유는 **전부** 남긴다 — 하나만 남기면 다음 사람이
    원인 하나를 고치고 여전히 안 뜨는 화면을 다시 조사한다."""
    reasons: list[str] = []
    switch = _switch()
    if switch == "off":
        return False, ["switched_off"]
    build = store.get("build") or {}
    candidates = int(build.get("candidates") or 0)
    if not store.get("threads"):
        reasons.append("no_ledger")
    if not build or candidates <= 0:
        # 판정 빌드가 무엇을 했는지 모르는 원장이다. 옛 파일이거나 판정을 한 번도
        # 돌리지 않았거나 — 어느 쪽이든 내보낼 근거가 없다.
        reasons.append("build_unknown")
    elif int(build.get("failed") or 0) / candidates > MAX_UNJUDGED_RATIO:
        reasons.append("build_incomplete")
    age = _age_hours(store.get("generated_at"), now)
    if age is None:
        reasons.append("no_timestamp")
    elif age > STALE_HOURS:
        reasons.append("stale")
    if thread_count < MIN_THREADS:
        reasons.append("too_few")
    if reasons and switch == "on":
        return True, reasons
    return not reasons, reasons


def build_payload(*, store: dict | None = None, events: list | None = None,
                  milestones: list[dict] | None = None,
                  now: datetime | None = None) -> dict:
    """원장 → 화면 계약. 실패해도 예외를 내보내지 않는 것은 호출자의 몫이다."""
    now = now or _now()
    store = thread_ledger.load_store() if store is None else store
    events = event_retrieval.load_events() if events is None else events
    by_id = {event.issue_id: event for event in events}
    milestones = load_milestones() if milestones is None else milestones
    labels = _registry()

    threads = [_thread_view(entry, by_id, labels, milestones)
               for entry in live_entries(store)]
    # 구성원을 하나도 못 찾은 스토리는 그릴 것이 없다. 원장이 사건보다 앞서
    # 갔거나(테스트 픽스처) 사건 id 가 바뀐 경우다.
    threads = [row for row in threads if row["event_count"] >= 2]
    short_ids = {row["thread_id"] for row in threads
                 if (row["lifespan_days"] or 0) < MIN_LIFESPAN_DAYS}
    threads = [row for row in threads if row["thread_id"] not in short_ids]
    # 최근 움직인 것이 위로. 장기 스토리의 값어치는 '아직 끝나지 않았다'에 있다.
    threads.sort(key=lambda row: (row["last_seen"], row["event_count"],
                                  row["thread_id"]), reverse=True)

    visible, reasons = gate(store, len(threads), now=now)
    source = str(store.get("generated_at") or "")
    stamped = _parse_ts(source)
    return {
        "version": CONTRACT_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "source_generated_at": source,
        "visible": visible,
        "status": "ok" if visible and not reasons else (reasons[0] if reasons else "ok"),
        "reasons": reasons,
        "stale_hours": STALE_HOURS,
        # 화면이 스스로 다시 재는 기준. 배포가 멈춰 낡은 파일이 CDN 에 남으면
        # 빌드 시점 판정으로는 잡을 수 없다 — 그때는 이 시각이 잡는다.
        "hide_after": (stamped + timedelta(hours=STALE_HOURS)).isoformat(timespec="seconds")
                      if stamped else "",
        # 내보내되 온전하지는 않은 회차. 숨길 만큼은 아니어도 다음 사람이 수치를
        # 읽을 때는 알아야 한다 — 겉보기에 정상인 반쪽 판정이 F 단계의 함정이었다.
        "degraded": str((store.get("build") or {}).get("status") or "") not in ("", "ok"),
        "build": store.get("build") or {},
        "stats": ({**_stats(threads), "short_excluded": len(short_ids)} if visible
                  else {"threads": len(threads), "short_excluded": len(short_ids)}),
        # 흡수된 스토리의 옛 주소. 공유된 링크가 죽지 않게 한다.
        "redirects": thread_ledger.redirects(
            store, {row["thread_id"] for row in threads}) if visible else {},
        # 내보내지 않을 것은 싣지도 않는다. 숨긴 화면의 데이터가 파일에 남아
        # 있으면 '숨김'이 표시 문제로 내려앉는다.
        "threads": threads if visible else [],
    }


def main() -> int:  # pragma: no cover — 손으로 확인할 때만 쓴다
    payload = build_payload()
    print(json.dumps({key: value for key, value in payload.items()
                      if key != "threads"}, ensure_ascii=False, indent=1))
    for row in payload["threads"][:5]:
        print(f"  {row['first_seen']}–{row['last_seen']} "
              f"({row['lifespan_days']}일 · {row['event_count']}건) {row['title'][:48]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
