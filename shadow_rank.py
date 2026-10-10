"""그림자 하루치 순위 — 아침 브리핑 선별 **옆에서** 기록만 한다. 발송은 바꾸지 않는다.

왜
--
큐레이션 등급은 8건 묶음 안에서 매겨진다. 같은 기사를 두 번 물은 86쌍(2026-10-09,
14일 캐시)에서 등급은 80%, 0~3점 칸은 67~75%, 주제 태그는 59%만 같았다. 그 등급
하나가 하루치 순위에서 7점을 움직인다(월성 2~4호기: 네이버 사본 17.6점 vs Google
사본 24.6점, 그날 국내 막차 21.4점). 묶음 크기를 15→8 로 줄여도 불일치는 줄지
않았다(13%→27%).

사용자 결정(2026-10-09): 한수원 직접 사안을 기준표에 더하지 않는다(연관 기사
과추천). 대신 **하루치를 한꺼번에 비교**해 순위를 매기는 방식과 **경계 기사
재질문**을 그림자 모드로 1주 돌리고, 실제 발송과 나란히 놓고 판단한다.

무엇
----
지역(국내/해외)마다, 오늘 후보 풀을 현재 점수로 잘라 상위 TOP_N 건을 AI 에게
한꺼번에 보여 주고 전부 줄 세우게 한다. **순서를 섞어 두 번** 묻는다 — 두 순위가
크게 갈리거나 실제 선정 경계(k 번째)를 사이에 두고 갈리는 기사가 '경계 기사'다.
경계 기사는 한 건씩 등급만 두 번 더 묻는다. 결과는 `shadow_rank_log.jsonl` 에
(날짜, 지역) 멱등으로 쌓인다. `tools/shadow_rank_report.py` 가 실제 발송과 비교한다.

안전장치
--------
- 어떤 예외도 밖으로 내지 않는다. 실패는 레코드의 `error` 로 남고 브리핑은 그대로 간다.
- 환경변수 SHADOW_RANK=off 면 아무것도 하지 않는다. 키가 없어도 건너뛴다.
- 현재 등급·점수는 프롬프트에 넣지 않는다 — 비교 대상이 그것이다.
- 호출 수는 지역당 2 + 경계 재질문(최대 REASK_LIMIT × 2). 하루 14회 안팎.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import gemini_client
import llm_policy
from gemini_client import GeminiError, call_json

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "shadow_rank_log.jsonl"

TOP_N = 30
BOUNDARY_GAP = 5          # 두 순위가 이만큼 갈리면 경계
REASK_LIMIT = 5           # 지역당 경계 재질문 상한(기사 수)
REASK_TIMES = 2

GRADES = ("must_read", "nice_to_know", "noise")

RANK_SYSTEM_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
아래 후보 기사들은 모두 오늘 아침 정책 브리핑에 실릴 수 있는 기사입니다.
의사결정자(본부장·부서장)가 오늘 꼭 알아야 하는 순서대로 **전부** 줄을 세우세요.

[판단 기준 — 절대 기준입니다. 몇 건을 뽑을지는 정하지 않습니다]
- 가장 앞: 확정된 사실. 정부·규제기관의 공식 의결·고시·시행령·법안 통과, 신규 원전
  부지 결정·인허가 발급, 계속운전 확정, SMR 표준설계인가, 사고·중대 안전 이슈,
  양자 협력 협정 체결, 수주·EPC 계약 체결, 전력수급기본계획 확정·고시.
- 그다음: 그런 결정으로 직접 이어지는 절차(신청·심사 착수·공청회·공론화)와 정책
  함의가 분명한 동향(전력수급·계통·요금 제도, 지역 수용성의 공식 결정과 요구).
- 맨 뒤: 전망·의견·칼럼, 홍보성 기사, 보도자료 단순 재탕, 외신 단순 번역.
- 같은 사건을 다룬 기사가 여럿이면 사실이 가장 많은 한 건만 앞에 두고 나머지는 뒤로.
- 입력 순서는 아무 의미가 없습니다. 매체 규모가 아니라 사건의 성격으로 판단합니다.

[grade]
- must_read: 즉시 알아야 함 — 위 '확정된 사실'에 해당.
- nice_to_know: 맥락·동향 — 정책 함의가 있는 기사.
- noise: 정책 함의 없음.

출력은 JSON 하나: {"ranking": [{"id": "머리표식", "rank": 1, "grade": "must_read|nice_to_know|noise"}, ...]}
모든 id 가 정확히 한 번씩 나와야 하고 rank 는 1부터 연속입니다. 설명문은 쓰지 마세요."""

REASK_SYSTEM_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
기사 한 건의 등급만 판정합니다. 다른 기사와 비교하지 말고 이 기사만 보고 정합니다.

- must_read: 즉시 알아야 함. 정부·규제기관 공식 의결·고시·인허가, 신규 부지 결정,
  계속운전 확정, SMR 표준설계인가, 사고·중대 안전 이슈, 양자 협정 체결, 수주 계약 체결,
  전력수급기본계획 확정.
- nice_to_know: 맥락·동향. 정책 함의가 있는 기사.
- noise: 정책 함의 없음. 전망·의견·홍보·재탕.

출력은 JSON 하나: {"grade": "must_read|nice_to_know|noise"}"""


def enabled() -> bool:
    """켜는 쪽이 명시한다 — daily-brief.yml 의 Plan 스텝이 SHADOW_RANK=on 을 준다.

    기본이 꺼짐인 이유: 테스트·로컬 plan 이 모르는 사이 Gemini 를 부르지 않게 하고,
    1주 측정이 끝나면 워크플로 한 줄로 끈다.
    """
    return os.environ.get("SHADOW_RANK", "").strip().lower() in ("on", "1", "true", "yes")


def candidates(pool: list[dict], scores: dict[str, float], top_n: int = TOP_N) -> list[dict]:
    """현재 점수 상위 top_n — 점수가 같으면 hash 로 고정해 재현 가능하게."""
    rows = [a for a in pool if a.get("hash")]
    rows.sort(key=lambda a: (-float(scores.get(a["hash"], 0.0)), a["hash"]))
    return rows[:top_n]


def _tag(article: dict) -> str:
    return str(article.get("hash") or "")[:8]


def _block(article: dict) -> str:
    lines = [
        f"[{_tag(article)}] {(article.get('title_kr') or article.get('title') or '')[:150]}",
        f"요약: {(article.get('summary') or '')[:200]}",
    ]
    detail = (article.get("detail") or "")[:400]
    if detail:
        lines.append(f"상세: {detail}")
    lines.append(f"출처: {article.get('publisher') or article.get('domain') or ''}")
    published = str(article.get("published_at") or "")[:10]
    if published:
        lines.append(f"게재일: {published}")
    return "\n".join(lines)


def parse_ranking(payload: object, tags: set[str]) -> dict[str, dict]:
    """{머리표식: {"rank": n, "grade": g}} — 빠지거나 겹치거나 모르는 id 가 있으면 ValueError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("ranking"), list):
        raise ValueError("ranking 배열이 없다")
    out: dict[str, dict] = {}
    for row in payload["ranking"]:
        if not isinstance(row, dict):
            continue
        tag = str(row.get("id") or "").strip()[:8]
        if tag not in tags:
            raise ValueError(f"모르는 id: {tag}")
        if tag in out:
            raise ValueError(f"중복 id: {tag}")
        try:
            rank = int(row.get("rank"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"rank 가 정수가 아님: {tag}") from exc
        grade = str(row.get("grade") or "").strip()
        out[tag] = {"rank": rank, "grade": grade if grade in GRADES else ""}
    missing = tags - set(out)
    if missing:
        raise ValueError(f"빠진 id {len(missing)}건: {sorted(missing)[:3]}")
    ranks = sorted(r["rank"] for r in out.values())
    if ranks != list(range(1, len(ranks) + 1)):
        # 연속이 아니면 순서대로 다시 매긴다 — 상대 순서는 보존된다.
        for position, tag in enumerate(sorted(out, key=lambda t: out[t]["rank"]), 1):
            out[tag]["rank"] = position
    return out


def rank_once(cands: list[dict], *, seed: int, call=None, label: str = "shadow_rank") -> dict[str, dict]:
    """후보를 seed 로 섞어 한 번 묻는다. 반환은 parse_ranking 결과."""
    order = list(cands)
    random.Random(seed).shuffle(order)
    tags = {_tag(a) for a in order}
    user = "\n\n---\n\n".join(_block(a) for a in order)
    policy = llm_policy.profile("shadow_rank")
    caller = call or call_json
    payload = caller(RANK_SYSTEM_PROMPT, user, temperature=0.2, max_output_tokens=4096,
                     timeout=120.0, model=policy.model(), label=label,
                     **policy.reasoning_kwargs())
    return parse_ranking(payload, tags)


def reask_grade(article: dict, *, call=None, label: str = "shadow_rank_reask") -> str:
    policy = llm_policy.profile("shadow_rank_reask")
    caller = call or call_json
    payload = caller(REASK_SYSTEM_PROMPT, _block(article), temperature=0.2,
                     max_output_tokens=256, timeout=60.0, model=policy.model(), label=label,
                     **policy.reasoning_kwargs())
    grade = str((payload or {}).get("grade") or "").strip() if isinstance(payload, dict) else ""
    return grade if grade in GRADES else ""


def compare(cands: list[dict], first: dict[str, dict], second: dict[str, dict],
            selected: list[dict], scores: dict[str, float]) -> list[dict]:
    """후보마다 점수 순위·그림자 순위 둘·경계 여부를 한 줄로."""
    k = len(selected)
    selected_hashes = {str(a.get("hash") or "") for a in selected}
    rows: list[dict] = []
    for position, article in enumerate(cands, 1):
        tag = _tag(article)
        r1 = first[tag]["rank"]
        r2 = second[tag]["rank"]
        gap = abs(r1 - r2)
        straddles = k > 0 and min(r1, r2) <= k < max(r1, r2)
        rows.append({
            "hash": article.get("hash", ""),
            "title": (article.get("title_kr") or article.get("title") or "")[:80],
            "importance": article.get("importance", ""),
            "score": round(float(scores.get(article.get("hash", ""), 0.0)), 2),
            "score_rank": position,
            "selected": article.get("hash", "") in selected_hashes,
            "shadow_rank": [r1, r2],
            "shadow_mean": round((r1 + r2) / 2, 1),
            "shadow_grade": [first[tag]["grade"], second[tag]["grade"]],
            "gap": gap,
            "boundary": bool(gap >= BOUNDARY_GAP or straddles),
        })
    return rows


ACTUAL_OUTCOMES = ("selected", "duplicate", "repeat", "below_floor", "ranked_out")


def actual_outcomes(selected: list[dict], diag: dict | None) -> dict[str, dict]:
    """실제 선별이 후보마다 무엇을 했나 — hash → {actual, dup_of?, follow_up?}.

    그림자는 선별 **전** 풀을 줄 세운다. 그래서 실제가 중복(같은 사건의 다른 기사)·
    연속일 반복(어제 이미 보낸 사건)·하한 미달로 정당하게 뺀 기사가 그림자 상위에
    남고, 보고서는 그것을 '그림자가 넣었을 기사'로 셌다. 첫 기록(2026-10-10)에서
    넣음 13건 중 3건이 중복으로 빠진 must_read 였다. 여기 적은 것으로 보고서가 같은
    사건을 하나로 묶고, 게이트를 통과한 후보끼리만 순위를 비교한다.

    ``follow_up`` 은 최근 보도의 후속이라 받은 감점(음수). 빠지지는 않았지만 그림자는
    어제 무엇이 나갔는지 모르므로 이 기사들을 그대로 올린다 — 따로 센다.
    """
    out: dict[str, dict] = {}
    diag = diag if isinstance(diag, dict) else {}
    for key, outcome in (("dropped_below_floor", "below_floor"),
                         ("dropped_repeat", "repeat"),
                         ("dropped_duplicates", "duplicate")):
        for row in diag.get(key) or ():
            h = str((row or {}).get("hash") or "")
            # 사건을 합칠 때 대표 기사가 자기 자신을 짝으로 남긴다(dup_of == hash).
            # 그건 중복이 아니라 대표다 — 10/10 비스트라 대표는 그 뒤 반복으로 빠졌다.
            if key == "dropped_duplicates" and str(row.get("dup_of") or "") == h:
                continue
            if h:
                out[h] = {"actual": outcome}
                if outcome == "duplicate" and row.get("dup_of"):
                    out[h]["dup_of"] = str(row["dup_of"])
    for h, parts in (diag.get("breakdowns") or {}).items():
        if not isinstance(parts, dict):
            continue
        penalty = sum(float(value) for name, value in parts.items()
                      if str(name).startswith("continuity:")
                      and isinstance(value, (int, float)) and value < 0)
        if penalty:
            out.setdefault(str(h), {})["follow_up"] = round(penalty, 2)
    for article in selected:
        h = str(article.get("hash") or "")
        if h:
            out.setdefault(h, {}).update({"actual": "selected"})
            out[h].pop("dup_of", None)
    return out


def run(region: str, pool: list[dict], selected: list[dict], scores: dict[str, float],
        today: str, *, call=None, top_n: int = TOP_N, now: datetime | None = None,
        diag: dict | None = None) -> dict:
    """한 지역의 그림자 기록 한 벌. 절대 raise 하지 않는다."""
    record: dict = {
        "record_type": "shadow_rank",
        "date": today,
        "region": region,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "model": "",
        "k": len(selected),
        "pool": len(pool),
        "candidates": 0,
        "calls": 0,
        "rows": [],
        "boundary": [],
        "reask": [],
        "error": None,
        # 실제 선정 전체(후보 30건 밖에서 대표로 올라온 기사까지)와, 행마다 실제 결과를
        # 적었는지. 2026-10-10 기록은 이 필드 없이 쌓였다(보고서는 '사유 미기록').
        "selected_hashes": [str(a.get("hash") or "") for a in selected],
        "actual_recorded": diag is not None,
    }
    try:
        record["model"] = llm_policy.profile("shadow_rank").model()
        cands = candidates(pool, scores, top_n)
        record["candidates"] = len(cands)
        if len(cands) < 2:
            record["error"] = "후보 2건 미만"
            return record
        first = rank_once(cands, seed=1, call=call)
        record["calls"] += 1
        second = rank_once(cands, seed=2, call=call)
        record["calls"] += 1
        rows = compare(cands, first, second, selected, scores)
        if diag is not None:
            outcomes = actual_outcomes(selected, diag)
            for row in rows:
                row.update({"actual": "ranked_out", **outcomes.get(row["hash"], {})})
        record["rows"] = rows
        boundary = [r for r in rows if r["boundary"]]
        # 경계 중에서도 실제 선정 경계(k)에 가까운 것부터 재질문한다.
        k = max(len(selected), 1)
        boundary.sort(key=lambda r: (abs(r["shadow_mean"] - k), r["score_rank"]))
        record["boundary"] = [r["hash"] for r in boundary]
        by_hash = {a.get("hash", ""): a for a in cands}
        for row in boundary[:REASK_LIMIT]:
            grades: list[str] = []
            for _ in range(REASK_TIMES):
                try:
                    grades.append(reask_grade(by_hash[row["hash"]], call=call))
                except GeminiError as exc:
                    grades.append(f"error:{type(exc).__name__}")
                record["calls"] += 1
            record["reask"].append({"hash": row["hash"], "title": row["title"],
                                    "importance": row["importance"], "grades": grades})
    except GeminiError as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    except Exception as exc:  # noqa: BLE001 — 그림자는 브리핑을 절대 막지 않는다
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return record


def append_log(records: list[dict], path: Path | None = None) -> int:
    """(date, region) 멱등 적재. 적재 건수 반환."""
    path = path or LOG_FILE
    existing: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                existing.add((str(row.get("date") or ""), str(row.get("region") or "")))
    added = 0
    with path.open("a", encoding="utf-8") as fp:
        for record in records:
            key = (str(record.get("date") or ""), str(record.get("region") or ""))
            if key in existing:
                continue
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added


def run_all(today: str, regions: dict[str, tuple], *, call=None) -> list[dict]:
    """{지역: (풀, 선정, 점수[, 진단])} → 레코드 목록. 꺼져 있거나 키가 없으면 빈 목록.

    진단(``ranking.rank_and_select`` 의 diag)을 주면 행마다 실제 결과를 적는다.
    """
    if not enabled():
        return []
    if call is None and not gemini_client.is_available():
        print("[shadow_rank] Gemini 키 없음 — 건너뜀")
        return []
    records: list[dict] = []
    for region, spec in regions.items():
        pool, selected, scores = spec[:3]
        diag = spec[3] if len(spec) > 3 else None
        record = run(region, pool, selected, scores, today, call=call, diag=diag)
        records.append(record)
        if record["error"]:
            print(f"[shadow_rank] {region}: 실패 — {record['error']}")
            continue
        rows = record["rows"]
        k = record["k"]
        shadow_top = {r["hash"] for r in rows if r["shadow_mean"] <= k}
        actual_top = {r["hash"] for r in rows if r["selected"]}
        print(f"[shadow_rank] {region}: 후보 {record['candidates']} · 선정 {k} · "
              f"그림자 상위 {k}와 겹침 {len(shadow_top & actual_top)} · "
              f"경계 {len(record['boundary'])} · 재질문 {len(record['reask'])}건 · "
              f"호출 {record['calls']}")
    return records
