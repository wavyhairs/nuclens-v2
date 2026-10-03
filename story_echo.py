"""뒷북 되풀이 — 메이저가 먼저 쓴 사실을 며칠 뒤 다른 매체가 다시 쓴 기사.

무엇이 문제였나 (2026-10-03 국내 1위)
--------------------------------------
  10/1 10:39  서울신문(must_read)  "223억 달러 텍사스 가스발전 1호 확정, 원전 8기 합의"
  10/2 00:20  중앙일보(must_read)  "24억 달러 송금" — 10/2 국내 2위로 발송. 본문 요지에
                                   "원전 8기 최대 1200억 달러 합의"가 들어 있었다.
  10/3 03:04  글로벌E(must_read)   서울신문 10/1 과 **요약이 같다.** 본문 없음. → 10/3 국내 1위

큐레이션 LLM 은 기사 한 건만 보고 등급을 매기므로 "계약 체결 확정"이면 뒷북이라도
must_read 다. 연속일 반복 판정(dedup.cross_day_repeats · stale_event_review)은 **실제로
발송된 카드**와만 비교한다 — 10/2 카드의 제목·요약은 송금 얘기였고 8기 합의는 본문
요지에만 있었으며, 서울신문 10/1 기사는 발송된 적이 없어 비교 대상에도 없었다. 그래서
판정기는 "새 사실(텍사스 확정·8기 합의)"이라 답했다. 사실은 이틀 전 기사의 되풀이다.

9/19~10/2 발송 254건을 소급하면 같은 꼴이 약 20건(8%)이다 — 석탄발전 60기 폐지(머니
투데이 → 77시간 뒤 투데이에너지), 텍사스 1호 확정(그린드경제 → 49시간 뒤 시사포커스),
톈완 7호기 임계(WNN → 9시간 뒤 글로벌이코노믹) ….

무엇을 하나
-----------
LLM 없이 결정적으로 본다. 후보 기사와 **같은 story 로 묶인, 직전 브리핑보다 먼저 나온,
큐레이션을 받은** 선행 기사들을 하나씩 대조한다. 어느 하나와

  · 제목 유사도(issue_continuity.title_similarity)가 ECHO_TITLE_SIMILARITY 이상이거나,
    ECHO_QUANTITY_SIMILARITY 이상이면서 후보의 숫자 사실(223억·8기)이 전부 선행 기사에
    있고,
  · 진전 판정(issue_continuity.progression — #220 의 척도·단계어·수치)이 ``none`` 이면

되풀이다. 소급 검증에서 외교장관 '회담 예정 → 회담 계기 기대'(0.62, 수치 없음), NERC
규제 ↔ 하원 법안(0.36), 한미 정상 '후속 논의 → 핵잠수함 협력 합의'(0.65, 수치 없음)는
걸리지 않았고, 위 20건은 전부 걸렸다.

되풀이의 처분은 둘로 갈린다.

  · **story 가 이미 브리핑에 나갔으면 뺀다.** 독자는 그 사실을 이미 봤다. 10/3 글로벌E
    가 이 경우다(10/2 중앙일보 카드가 같은 story).
  · **아직 한 번도 안 나갔으면 must_read 를 내리고 날짜를 단다.** 메이저가 쓴 날 8칸
    경쟁에서 밀린 소식이 사흘 뒤 작은 매체 기사로 1위에 오르는 일은 막되, 소식 자체를
    영영 빠뜨리지는 않는다(2026-09-26 결정 D1 과 같은 선). 원래 등급은 ``importance_llm``
    에 남고 ``stale_since`` 에 선행 기사 날짜가 붙어 1번 자리를 피하고 제목에 날짜가 간다.

공식 1차 자료(evidence_role=primary)는 건드리지 않는다 — 보도자료가 기사보다 늦게
올라오는 것은 되풀이가 아니라 원문이다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import freshness
import issue_continuity

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent

ECHO_TITLE_SIMILARITY = 0.70
ECHO_QUANTITY_SIMILARITY = 0.60
BRIEFED_WINDOW_DAYS = 7


def archive_rows(days: int = 14, *, root: Path | None = None,
                 now: datetime | None = None) -> dict[str, dict]:
    """최근 아카이브의 hash → 대조에 필요한 필드. freshness.archive_dates 와 같은 창."""
    root = root or ROOT / "archive"
    now = (now or datetime.now(KST)).astimezone(KST)
    since = now - timedelta(days=days)
    months = {now.strftime("%Y-%m"), since.strftime("%Y-%m")}
    found: dict[str, dict] = {}
    for month in sorted(months):
        path = root / f"{month}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            stamp = freshness._parse(row.get("pub"))
            if not row.get("hash") or stamp is None or stamp < since:
                continue
            found[row["hash"]] = {
                "hash": row["hash"], "published_at": row.get("pub"),
                "title": row.get("title") or "", "title_kr": row.get("title_kr") or "",
                "summary": row.get("summary") or "", "detail": row.get("detail") or "",
                "domain": row.get("domain") or "", "publisher": row.get("publisher") or "",
                "importance": row.get("importance") or "",
                "evidence_role": row.get("evidence_role") or "",
            }
    return found


def briefed_stories(path: Path | None = None, *, days: int = BRIEFED_WINDOW_DAYS,
                    today: str | None = None) -> dict[str, set[str]]:
    """최근 발송된 카드의 {"story_ids", "hashes"} — '독자가 이미 본 story' 판정 재료."""
    path = path or ROOT / "delivery_log.jsonl"
    story_ids: set[str] = set()
    hashes: set[str] = set()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"story_ids": story_ids, "hashes": hashes}
    floor = ""
    if today:
        try:
            floor = (datetime.fromisoformat(today) - timedelta(days=days)).date().isoformat()
        except ValueError:
            floor = ""
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("record_type") is not None or not row.get("hash"):
            continue
        if floor and str(row.get("date") or "") < floor:
            continue
        hashes.add(str(row["hash"]))
        if row.get("story_id"):
            story_ids.add(str(row["story_id"]))
        for member in row.get("story_members") or ():
            if isinstance(member, dict) and member.get("hash"):
                hashes.add(str(member["hash"]))
    return {"story_ids": story_ids, "hashes": hashes}


def _member_hashes(item: dict) -> list[str]:
    hashes = list(item.get("story_article_hashes") or [])
    hashes += [m.get("hash") for m in item.get("story_members") or () if isinstance(m, dict)]
    own = item.get("hash")
    return [h for h in dict.fromkeys(hashes) if h and h != own]


def find_echo(item: dict, rows: dict[str, dict], cutoff: datetime | None,
              grace_hours: float) -> dict | None:
    """되풀이면 선행 기사 정보를, 아니면 None."""
    if cutoff is None or str(item.get("evidence_role") or "") == "primary":
        return None
    own_quantities = issue_continuity._quantities(item)
    best: dict | None = None
    for h in _member_hashes(item):
        prior = rows.get(h)
        if not prior or not (prior.get("summary") or "").strip():
            continue
        if freshness.stale_since({"published_at": prior.get("published_at")},
                                 cutoff, grace_hours) is None:
            continue  # 같은 회차의 다른 매체 기사 — 되풀이가 아니라 동시 보도다
        similarity = issue_continuity.title_similarity(item, prior)
        subset = bool(own_quantities) and own_quantities <= issue_continuity._quantities(prior)
        if similarity < ECHO_TITLE_SIMILARITY and not (
                similarity >= ECHO_QUANTITY_SIMILARITY and subset):
            continue
        verdict = issue_continuity.progression(prior, item, evidence_confirmed=True)
        if verdict.get("verdict") != "none":
            continue
        candidate = {
            "prior_hash": h,
            "prior_title": (prior.get("title_kr") or prior.get("title") or "")[:120],
            "prior_date": str(prior.get("published_at") or "")[:10],
            "prior_domain": prior.get("domain") or prior.get("publisher") or "",
            "similarity": round(similarity, 3),
            "quantities_subset": subset,
        }
        if best is None or similarity > best["similarity"]:
            best = candidate
    return best


def apply(items: list[dict], rows: dict[str, dict], cutoff: datetime | None,
          grace_hours: float, briefed: dict[str, set[str]] | None = None,
          ) -> tuple[list[dict], list[dict]]:
    """(남길 것, 뺀 것). 남긴 되풀이는 등급을 내리고 날짜를 단다.

    뺀 것은 진단 요약(hash·title·importance·prior_*·reason="story_echo_repeat").
    남긴 되풀이는 ``story_echo`` 에 선행 기사 정보가 붙고 ``importance_llm`` 에 원래 등급이
    남는다.
    """
    briefed = briefed or {"story_ids": set(), "hashes": set()}
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        echo = find_echo(item, rows, cutoff, grace_hours)
        if echo is None:
            kept.append(item)
            continue
        seen = (str(item.get("story_id") or "") in briefed["story_ids"]
                or echo["prior_hash"] in briefed["hashes"])
        row = {
            "hash": item.get("hash", ""),
            "title": (item.get("title_kr") or item.get("title") or "")[:80],
            "importance": item.get("importance", ""),
            "domain": item.get("domain", ""),
            **echo,
        }
        if seen:
            cont = dict(item.get("continuity") or {})
            cont.update({
                "matched": True, "drop": True,
                "prior_hash": echo["prior_hash"], "prior_title": echo["prior_title"],
                "prior_date": echo["prior_date"],
                "identity_confirmed": True, "identity_method": "story_echo",
                "progression": "none",
                "match_reasons": list(cont.get("match_reasons") or []) + ["story_echo:repeat"],
            })
            item["continuity"] = cont
            dropped.append({**row, "reason": "story_echo_repeat"})
            continue
        item["story_echo"] = echo
        if str(item.get("importance") or "") == "must_read":
            item["importance_llm"] = "must_read"
            item["importance"] = "nice_to_know"
        if echo["prior_date"]:
            item["stale_since"] = echo["prior_date"]
        kept.append(item)
    return kept, dropped
