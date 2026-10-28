"""재발송 그림자 판정 — 발송 원장에 대 "새 사실이 있나"를 묻고 **기록만** 한다.

발송은 바꾸지 않는다. `shadow_rank` 와 같은 자리(선정이 끝난 뒤)에서 돌고 같은 방식으로
쌓인다(`repeat_shadow_log.jsonl`, (날짜, 지역) 멱등). 2주 기록을 회귀셋
(`eval_artifacts/repeat_send/`)·현행 판정과 나란히 본 뒤 전환을 정한다.
설계 근거: `docs/2026-10-25-repeat-send-root-cause-review.md` §5.

현행 판정과 무엇이 다른가
------------------------
현행 `dedup.cross_day_repeats` 는 "후보가 보낸 카드와 같은 사건인가 → 새 행동이 있나"를
묻는다. 비교 대상은 7일·어휘 3건이고, 확인 질문은 "날짜로 먼저 보라"면서 후보의 사건일을
넘기지 않으며, 답이 모호하면 새 소식으로 남긴다. 10/25 는 그 셋이 다 겹쳤다.

여기서는

1. 비교 대상을 `told_ledger.related` 가 찾는다 — 30일, 어휘·근거 해시·웹 이슈 확장.
2. 질문은 하나다. **후보의 사실 중 TOLD 어디에도 없는 것**을, 일어난 날짜와 원문 인용을
   붙여 적게 한다. 후보와 TOLD 의 사건일을 입력에 넣는다.
3. 처분은 모델이 아니라 이 모듈이 정한다(``disposition``).

    new_issue    원장에 같은 이야기가 없다 — 묻지 않는다
    drop         TOLD 에 없는 **진전**이 0개 (인용이 후보 원문에 있는 사실만 센다).
                 배경 설명만 새로우면 이것이다(row.drop_reason = background_only)
    late         없는 진전이 있으나 전부 직전 브리핑일보다 앞서 일어났다
    new          직전 브리핑일 이후 일어난 진전이 하나라도 있다
    new_undated  없는 진전이 있으나 날짜를 모른다(그리고 늦은 것으로 확정되지도 않았다)
    unverified   모델이 적은 사실의 인용이 하나도 후보 원문에 없다
    error        호출 실패 — 전환 뒤 처분은 'late 와 같게'(빼지도 올리지도 않는다)
    skipped      호출 상한에 걸려 묻지 못함

v2 (2026-10-28) — 진전과 배경을 가른다
---------------------------------------
v1 첫 3일(10/26~28, 46장) drop 0. 10/27 「DL이앤씨, 엑스에너지와 4세대 SMR 표준화 설계 계약
체결」은 10/12 카드와 제목까지 같았는데, 모델이 '국내 건설사 최초'·'SMR 시장 2035년 5000억
달러'를 TOLD 에 없는 사실로 적어 new_undated 가 됐다. 지시문은 배경을 빼라고 했지만 지켜지지
않았고, 사실 하나의 성격을 코드가 볼 수 없었다. 그래서 사실마다 ``kind`` 를 받는다 —
``development``(결정·계약·발표·수치 변화·새 일정·사고·발견처럼 **일이 달라진 것**) 와
``background``(위치·규모 같은 고정 속성, 연혁, 시장 전망, 의의, '최초' 같은 수식). 처분은
development 만 센다. kind 가 없거나 이상하면 development 로 본다(빼는 쪽으로 기울지 않는다).

사실마다 ``told_in`` 도 받는다 — 그 사실을 이미 전한 TOLD 번호(T3) 또는 null. 회귀셋 재생
(9/26~10/25, 사람 라벨 256건)에서 '없는 사실만 적어라'고 하면 모델이 비교를 건너뛰고 핵심
사건을 그대로 새 사실로 적었다(재탕 50건 중 16건만 잡음 — 10/7 에 보낸 「구글·콘스텔레이션
20년 계약」을 10/8 카드에서 '새 사실'로 적는 식). 사실을 먼저 다 적고 하나씩 카드 번호를
대게 하면 비교를 건너뛸 수 없다. 번호가 범위 밖이면 '확인 안 됨'(=새 사실)으로 읽는다.
이름 있는 주체가 새로 낸 전망·보고서는 development 다 — 같은 재생에서 「JP모건, 전력난 2030년까지
지속 전망」 같은 별건 15건이 '전망은 적지 마라' 지시 때문에 사실 0개로 빠졌다.

date 는 일이 일어났거나 **처음 드러난** 날이다. v1 은 「한국전력기술, 도면 5년간 노출 방치」
에서 노출이 시작된 2020-07-01 을 적어 late 가 됐다 — 이번에 감사로 드러난 일이다.

안전장치
--------
- 어떤 예외도 밖으로 내지 않는다. 실패는 레코드의 ``error`` 로 남는다.
- REPEAT_SHADOW=on 일 때만 돈다(워크플로가 켠다). 키가 없으면 건너뛴다.
- 호출 상한 MAX_CALLS(하루, 두 지역 합). 한 번에 CASES_PER_CALL 건만 묻는다 — 많이 묶으면
  비교가 얕아진다. 하루 카드 20장 안팎이라 평소 7회쯤, 버킷 500 RPD 의 2% 안이다.
- 시간 예산 TIME_BUDGET_S. 호출 하나는 최대 2회 시도 × CALL_TIMEOUT_S 라, 모델이 느린 날에도
  plan 을 붙잡는 시간은 최악 약 4.5분(예산 직전에 시작한 호출 하나까지)이다. 평소는 수 초.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gemini_client
import issue_continuity
import llm_policy
import told_ledger
from gemini_client import GeminiError, call_json

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
LOG_FILE = ROOT / "repeat_shadow_log.jsonl"
PROMPT_VERSION = 2

CASES_PER_CALL = 3
MAX_CALLS = 10
# 그림자가 아침 발송을 늦추지 않게 하는 시간 예산. 호출 하나는 CALL_TIMEOUT_S·재시도 1회,
# 전체가 TIME_BUDGET_S 를 넘으면 남은 묶음은 skipped. (기본값 120초×재시도 3회×4회면 20분을 넘는다.)
CALL_TIMEOUT_S = 60.0
CALL_RETRIES = 1
TIME_BUDGET_S = 150.0

VERDICTS = ("new_issue", "drop", "late", "new", "new_undated", "unverified", "error", "skipped")

SYSTEM_PROMPT = """당신은 원자력 아침 브리핑의 사실 확인 담당입니다.

각 CASE 는 오늘 보낼 후보(CANDIDATE) 하나와, 같은 이야기로 이미 독자에게 보낸 카드(T1, T2, …)들입니다.
CANDIDATE 가 전하는 **핵심 사실을 최대 5개** 적고, 사실마다 이미 전한 카드가 있는지 표시하세요.

1. 첫 사실은 CANDIDATE **제목이 전하는 일**입니다. 나머지는 요약·상세에서 중요한 순서로.
2. told_in: 그 사실을 이미 전한 카드 번호("T3"). 표현·언어·매체·수치 표기가 달라도 같은 일이면
   같은 사실입니다('1.07조'='1조 722억', '3.6GW 계약'='20년 전력구매계약'). 같은 계약·결정·
   발표를 다른 날 기사가 다시 전해도 같은 사실입니다. **어느 카드에도 없을 때만** null.
3. kind:
   · "development": 일이 달라진 것 — 결정·의결·승인·보류·계약·착수·완료·사고·발견, 수치나
     일정의 변경·확정. 이름 있는 주체(기관·기업·인물)가 **새로 낸** 발표·보고서·전망·발언도
     그 자체가 development 입니다.
   · "background": 일이 달라지지 않은 것 — 위치·용량 같은 고정 속성, 연혁, 주체 없는 해설·
     일반론, 의의·목적, '국내 최초' 같은 수식.
4. date: 그 일이 **일어났거나 처음 드러난** 날(YYYY-MM-DD). 기사 게재일이 아닙니다. '5년간
   방치'처럼 기간이면 드러난(감사·발표·보도된) 날입니다. '지난 15일'은 CANDIDATE published
   기준으로 풉니다. 모르면 null.
5. quote: CANDIDATE 의 제목·요약·상세에서 **그대로 옮긴** 짧은 구절(바꿔 쓰지 마세요).

⚠️ 출력은 JSON 하나만:
{"cases": [{"case": 0, "facts": [{"fact": "짧게", "told_in": "T2 또는 null", "kind": "development 또는 background", "date": "YYYY-MM-DD 또는 null", "quote": "원문 그대로"}]}]}
모든 case 를 빠짐없이 답하세요."""


def enabled() -> bool:
    """켜는 쪽이 명시한다 — daily-brief.yml 의 Plan 스텝이 REPEAT_SHADOW=on 을 준다.

    기본이 꺼짐인 이유는 `shadow_rank.enabled` 와 같다: 테스트·로컬 plan 이 모르는 사이
    Gemini 를 부르지 않게 하고, 측정이 끝나면 워크플로 한 줄로 끈다.
    """
    return os.environ.get("REPEAT_SHADOW", "").strip().lower() in ("on", "1", "true", "yes")


def _trim(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _published_day(article: dict) -> str:
    raw = str(article.get("published_at") or article.get("queued_at") or article.get("pub") or "")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10]
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(KST).date().isoformat()


def _case_block(index: int, cand: dict, told: list[dict]) -> str:
    lines = [
        f"[CASE {index}] CANDIDATE published={_published_day(cand)} "
        f"event_date={cand.get('event_date') or 'null'}",
        f"  TITLE: {_trim(cand.get('title_kr') or cand.get('title'), 200)}",
    ]
    original = _trim(cand.get("title"), 200)
    if original and original != _trim(cand.get("title_kr"), 200):
        lines.append(f"  TITLE_ORIGINAL: {original}")
    lines += [f"  SUMMARY: {_trim(cand.get('summary'), 500)}",
              f"  DETAIL: {_trim(cand.get('detail'), 600)}",
              "  TOLD (보낸 날짜순):"]
    for n, row in enumerate(told, 1):
        lines += [f"   (T{n}) sent={row.get('date')} event_date={row.get('event_date') or 'null'}",
                  f"     TITLE: {_trim(row.get('title_kr') or row.get('title'), 200)}",
                  f"     SUMMARY: {_trim(row.get('summary'), 350)}"]
        if row.get("detail"):
            lines.append(f"     DETAIL: {_trim(row.get('detail'), 400)}")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return re.sub(r"[\s\"'“”‘’·,.…]+", "", text or "")


def quote_ok(quote: object, cand: dict) -> bool:
    """인용이 후보 원문(제목·원제목·요약·상세)에 그대로 있는가. 공백·따옴표·구두점은 무시."""
    q = _normalize(str(quote or ""))
    if len(q) < 4:
        return False
    body = _normalize(" ".join(str(cand.get(k) or "") for k in ("title_kr", "title", "summary", "detail")))
    return q in body


def _valid_date(value: object) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _told_ref(value: object, told_count: int) -> str | None:
    """'T3' → 'T3'. 범위 밖·형식 밖은 None — 이미 전했다고 **확인되지 않은** 것으로 본다."""
    match = re.fullmatch(r"\s*[Tt]\s*(\d+)\s*", str(value or ""))
    if not match or not 1 <= int(match.group(1)) <= told_count:
        return None
    return f"T{int(match.group(1))}"


def parse_cases(payload: object, count: int,
                told_counts: list[int] | None = None) -> dict[int, list[dict]]:
    """{case 번호: 사실 목록}. 형식이 어긋난 항목은 버린다. 빠진 case 는 결과에 없다.

    사실마다 ``told_in`` (이미 전한 카드 번호, 없으면 None)과 ``kind`` 가 붙는다. 옛 응답
    형식(``untold``)은 전부 told_in=None 으로 읽는다.
    """
    out: dict[int, list[dict]] = {}
    cases = payload.get("cases") if isinstance(payload, dict) else None
    for entry in cases if isinstance(cases, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("case"), int):
            continue
        index = entry["case"]
        if not 0 <= index < count or index in out:
            continue
        told_count = told_counts[index] if told_counts else 99
        listed = entry.get("facts") if "facts" in entry else entry.get("untold")
        facts = []
        for fact in listed or []:
            if isinstance(fact, dict) and str(fact.get("fact") or "").strip():
                kind = str(fact.get("kind") or "").strip().lower()
                facts.append({"fact": _trim(fact.get("fact"), 120),
                              "told_in": _told_ref(fact.get("told_in"), told_count),
                              # 모르는 값은 development — 빼는 쪽으로 기울지 않는다.
                              "kind": "background" if kind == "background" else "development",
                              "date": _valid_date(fact.get("date")),
                              "quote": _trim(fact.get("quote"), 160)})
        out[index] = facts
    return out


def disposition(facts: list[dict], cand: dict, last_brief_day: str) -> str:
    """모델이 적은 '없는 사실'로 처분을 정한다(결정적). facts 에 quote_ok 가 붙어 있어야 한다.

    ``facts`` 는 TOLD 에 없는 사실(told_in 이 빈 것)만이다 — ``run`` 이 거른다.
    """
    if not facts:
        return "drop"
    verified = [f for f in facts if f.get("quote_ok")]
    if not verified:
        return "unverified"
    developments = [f for f in verified if f.get("kind", "development") != "background"]
    if not developments:
        # 원문에서 확인된 새것이 배경뿐이다. 인용을 못 맞춘 진전이 있으면 빼지 않는다.
        if any(f.get("kind", "development") != "background" for f in facts):
            return "unverified"
        return "drop"
    dates = [f.get("date") for f in developments]
    if any(d and d >= last_brief_day for d in dates):
        return "new"
    if all(d for d in dates):
        return "late"
    return "new_undated"


def judge(cases: list[tuple[dict, list[dict]]], *, call=None,
          label: str = "repeat_shadow") -> dict[int, list[dict]]:
    """CASE 묶음 한 번 호출. 실패는 GeminiError/ValueError 로 올린다(run 이 잡는다)."""
    policy = llm_policy.profile("repeat_shadow")
    caller = call or call_json
    user = "\n\n".join(_case_block(i, cand, told) for i, (cand, told) in enumerate(cases))
    payload = caller(SYSTEM_PROMPT, user, temperature=0.05, max_output_tokens=4096,
                     timeout=CALL_TIMEOUT_S, retries=CALL_RETRIES, model=policy.model(),
                     label=label, **policy.reasoning_kwargs())
    parsed = parse_cases(payload, len(cases), [len(told) for _cand, told in cases])
    missing = [i for i in range(len(cases)) if i not in parsed]
    if missing:
        raise ValueError(f"빠진 case {len(missing)}건: {missing[:5]}")
    return parsed


def _told_view(row: dict) -> dict:
    return {"date": row.get("date"), "hash": row.get("hash"),
            "title": _trim(row.get("title_kr") or row.get("title"), 80),
            "via": list(row.get("via") or [])}


def run(today: str, regions: dict[str, list[dict]], *, call=None,
        told: list[dict] | None = None, issues: dict[str, str] | None = None,
        cfg: dict | None = None, now: datetime | None = None,
        max_calls: int = MAX_CALLS, time_budget: float = TIME_BUDGET_S,
        clock=time.monotonic) -> list[dict]:
    """{지역: 선정 카드 목록} → 지역별 레코드. 절대 raise 하지 않는다."""
    generated = (now or datetime.now(timezone.utc)).isoformat()
    records = [{"record_type": "repeat_shadow", "date": today, "region": region,
                "generated_at": generated, "prompt_version": PROMPT_VERSION, "model": "",
                "last_brief_day": "", "calls": 0, "rows": [], "summary": {}, "error": None}
               for region in regions]
    try:
        model = llm_policy.profile("repeat_shadow").model()
        told = told_ledger.load_told(today) if told is None else told
        issues = told_ledger.issue_index() if issues is None else issues
        if cfg is None:
            try:
                cfg = json.loads((ROOT / "ranking_config.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cfg = {}
        cont_cfg = issue_continuity.resolve_config(cfg)
        generic = issue_continuity.generic_anchors(told)
        last_brief_day = max((str(r.get("date") or "") for r in told), default="")
        pending: list[tuple[dict, dict, dict, list[dict]]] = []
        for record, (region, cards) in zip(records, regions.items()):
            record["model"] = model
            record["last_brief_day"] = last_brief_day
            for position, cand in enumerate(cards, 1):
                rel = told_ledger.related(cand, told, issues, cont_cfg, generic)
                row = {"hash": cand.get("hash", ""),
                       "title": _trim(cand.get("title_kr") or cand.get("title"), 80),
                       "position": position, "importance": cand.get("importance", ""),
                       "event_date": cand.get("event_date"),
                       "told": [_told_view(r) for r in rel], "untold": [],
                       "verdict": "new_issue" if not rel else "", "error": None}
                record["rows"].append(row)
                if rel:
                    pending.append((record, row, cand, rel))
        calls = 0
        started = clock()
        for start in range(0, len(pending), CASES_PER_CALL):
            chunk = pending[start:start + CASES_PER_CALL]
            if calls >= max_calls or clock() - started > time_budget:
                for _record, row, _cand, _rel in chunk:
                    row["verdict"] = "skipped"
                continue
            calls += 1
            for record in {id(r): r for r, *_ in chunk}.values():
                record["calls"] += 1
            try:
                parsed = judge([(cand, rel) for _r, _row, cand, rel in chunk], call=call)
            except (GeminiError, ValueError) as exc:
                message = f"{type(exc).__name__}: {str(exc)[:160]}"
                for _record, row, _cand, _rel in chunk:
                    row["verdict"] = "error"
                    row["error"] = message
                continue
            for index, (_record, row, cand, _rel) in enumerate(chunk):
                listed = [{**f, "quote_ok": quote_ok(f.get("quote"), cand)} for f in parsed[index]]
                untold = [f for f in listed if not f.get("told_in")]
                row["told_facts"] = [{"fact": f["fact"], "told_in": f["told_in"]}
                                     for f in listed if f.get("told_in")]
                row["untold"] = untold
                if not listed:
                    # 핵심 사실을 하나도 못 적었다 — 비교를 안 한 것이지 재탕의 증거가 아니다.
                    row["verdict"] = "unverified"
                    continue
                row["verdict"] = disposition(untold, cand, last_brief_day)
                if row["verdict"] == "drop":
                    row["drop_reason"] = "background_only" if untold else "all_told"
    except Exception as exc:  # noqa: BLE001 — 그림자는 브리핑을 절대 막지 않는다
        message = f"{type(exc).__name__}: {str(exc)[:200]}"
        for record in records:
            record["error"] = message
    for record in records:
        summary: dict[str, int] = {}
        for row in record["rows"]:
            if row["verdict"]:
                summary[row["verdict"]] = summary.get(row["verdict"], 0) + 1
        record["summary"] = summary
        lead = next((r for r in record["rows"] if r["position"] == 1), None)
        record["lead_verdict"] = lead["verdict"] if lead else ""
    return records


def run_all(today: str, regions: dict[str, list[dict]], *, call=None, **kwargs) -> list[dict]:
    """꺼져 있거나 키가 없으면 빈 목록. 지역별 한 줄씩 로그를 찍는다."""
    if not enabled():
        return []
    if call is None and not gemini_client.is_available():
        print("[repeat_shadow] Gemini 키 없음 — 건너뜀")
        return []
    records = run(today, regions, call=call, **kwargs)
    for record in records:
        if record["error"]:
            print(f"[repeat_shadow] {record['region']}: 실패 — {record['error']}")
            continue
        print(f"[repeat_shadow] {record['region']}: 카드 {len(record['rows'])} · "
              f"판정 {record['summary']} · 1번 {record['lead_verdict'] or '-'} · 호출 {record['calls']}")
    return records


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
    if not records:
        return 0
    with path.open("a", encoding="utf-8") as fp:
        for record in records:
            key = (str(record.get("date") or ""), str(record.get("region") or ""))
            if key in existing or not record.get("rows"):
                continue
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added
