"""AI 요약이 원문과 다른 사실을 말하는지 검사한다 — **경고 모드**.

배경 (2026-09-24):
    12차 전기본 공론화 기사의 요약이 원문의 "정부안이 12월에 나온다"를
    "최종안은 12월에 발표"로 바꿔 적었다. 같은 날 다른 매체는 "최종 확정은
    내년 2월"이라고 썼으므로, 화면에서는 두 카드가 서로 부딪히는 것처럼 보였다.
    요약은 보고서에 그대로 인용되므로 이런 오류는 곧 사고다.

측정 (아카이브 9/18~23 실제 61건 + 심은 오류 14건, gemini-3.1-flash-lite):
    - **기사 1건씩** 물으면 숫자·날짜·단계 부풀리기 오류를 잡는다(심은 것 13/14,
      TSMC "양산 2028→2027" 실제 오류). 10건씩 묶으면 옆 기사의 원문을 근거로
      끌어와 판정이 오염됐다 — 그래서 묶지 않는다.
    - 추론(thinking) 레벨은 결과를 바꾸지 않았다(없음·low·medium·high 동일).
      high 는 기사 한 건에 생각 6.3만 토큰까지 폭주했다. 그래서 끈다.
    - **"정부안→최종안"은 어떤 설정으로도 LLM 이 못 잡았다.** 그래서 이 유형은
      아래 `stage_word_findings` 의 결정적 규칙이 맡는다.
    - 날짜를 안 주면 모델이 자기 옛 지식으로 멀쩡한 기사를 오보라고 했다
      ("현직 대통령이 아니다"). 오늘 날짜와 "원문이 유일한 진실" 문구는 필수다.

경고 모드:
    결과를 `summary_checks.jsonl` 에 남기고 로그에 찍기만 한다. 요약·등급·발송은
    **하나도 바꾸지 않는다.** 1주일 결과를 사람이 본 뒤에 조치(요약 비우기 등)를
    켤지 정한다.

한도:
    기사 요약(curation)과 같은 모델 버킷(3.1-flash-lite)을 쓴다. 이 검사는 부가
    기능이므로 요약이 한도에 밀리면 안 된다 — 하루 상한과 회차 상한을 두고,
    429·402 를 한 번이라도 보면 그 회차는 즉시 멈춘다. 멈춰서 못 본 기사는 다음
    회차가 24시간 안에서 본문을 다시 받아 이어서 본다(`backlog_candidates`).

저장:
    append-only JSONL(`.gitattributes` 의 merge=union). crawl 과 daily-brief 가
    같은 파일에 동시에 줄을 붙여도 rebase 충돌로 수집분 커밋이 죽지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import llm_policy

ROOT = Path(__file__).parent
LOG_FILE = Path(os.environ.get("SUMMARY_CHECKS_FILE") or (ROOT / "summary_checks.jsonl"))

# 프롬프트를 고치면 올린다. 같은 요약이라도 버전이 다르면 다시 검사한다.
PROMPT_VERSION = 1

# 검사 대상 등급. noise·market 은 화면에 서지 않는다.
KEPT_IMPORTANCE = frozenset({"must_read", "nice_to_know"})

# 무료 일일 한도(500)는 16:00 KST(= 07:00 UTC)에 리셋된다. 상한도 같은 날로 센다.
QUOTA_DAY_OFFSET = timedelta(hours=7)

KST = timezone(timedelta(hours=9))

# 2026-09-17~23 실측: 3.1 사용량 최대 123회/일(크롤 104 + 브리핑 19).
# 250 을 더하면 373 — 500 의 75%. 기사가 많은 날(등급 기사 284건)은 상한이 막는다.
DEFAULT_DAILY_CAP = 250
# 크롤 1회에 새로 들어오는 등급 기사는 보통 수십 건이다. 한 회차가 하루 몫을
# 다 쓰지 않게 한다 — 분당 15회 제한에서 80회는 6분 안팎이다.
DEFAULT_RUN_CAP = 80

# 한도·결제 오류로 멈춘 회차의 남은 기사를 다음 회차가 이어서 본다. 본문은 저장하지
# 않으므로(저작권) 다시 받아 와야 한다 — 그래서 창을 좁게, 회차당 개수를 작게 둔다.
BACKLOG_HOURS = 24
BACKLOG_PER_RUN = 30

_MAX_TEXT = 300


def enabled() -> bool:
    return os.environ.get("SUMMARY_VERIFY", "on").strip().lower() not in {"0", "off", "false", "no"}


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default


def daily_cap() -> int:
    return _int_env("SUMMARY_VERIFY_DAILY_CAP", DEFAULT_DAILY_CAP)


def run_cap() -> int:
    return _int_env("SUMMARY_VERIFY_RUN_CAP", DEFAULT_RUN_CAP)


def quota_day(moment: datetime) -> str:
    return (moment.astimezone(timezone.utc) - QUOTA_DAY_OFFSET).date().isoformat()


# ── 결정적 규칙: 절차 단계어 ─────────────────────────────────────────────
#
# LLM 이 끝내 못 잡은 유형이다. 원문은 초안 단계를 말하는데 요약은 최종 단계를
# 말하면 걸린다. 범위를 일부러 좁게 둔다 — 동사형(검토했다→확정했다)까지 넓히면
# 한 기사 안에 두 단계가 함께 나오는 흔한 문장("검토 끝에 확정")에 헛경보가 난다.
DRAFT_TERMS = ("정부안", "초안", "잠정안", "시안")
FINAL_TERMS = ("최종안", "확정안")


def stage_word_findings(summary_text: str, source_text: str) -> list[str]:
    """요약이 원문에 없는 '최종' 단계어를 쓰고, 원문은 초안 단계어를 쓰는 경우."""
    summary_text = summary_text or ""
    source_text = source_text or ""
    drafts = [term for term in DRAFT_TERMS if term in source_text]
    if not drafts:
        return []
    return [f"{final}←{'/'.join(drafts)}"
            for final in FINAL_TERMS
            if final in summary_text and final not in source_text]


# ── LLM 검사 ──────────────────────────────────────────────────────────────

def system_prompt(today: datetime) -> str:
    day = today.astimezone(KST)
    return f"""당신은 원자력·에너지 정책 브리핑의 사실검증 담당자입니다.
오늘은 {day.year}년 {day.month}월 {day.day}일입니다. 올해는 {day.year}년, 내년은 {day.year + 1}년, 작년은 {day.year - 1}년입니다.
원문·요약의 '올해·내년·작년'은 위 연도로 바꿔 읽으세요. 같은 연도를 다르게 표기한 것은 모순이 아닙니다.
**원문이 유일한 진실입니다.** 당신이 알고 있는 세상 지식(누가 대통령인지, 어떤 투자가 실제로 있었는지 등)으로 판단하지 마세요.
원문에 적힌 내용은 모두 사실로 간주합니다.

각 항목은 [원문](기사 제목+본문, 앞부분만 잘려 있을 수 있음)과 [요약](AI 가 만든 제목·요약·상세)입니다.
요약이 원문과 **양립할 수 없는 사실**을 말하는지만 봅니다. 문체·생략·표현 차이, 연도 표기 방식(내년·올해)은 문제가 아닙니다.

특히 다음을 원문과 대조하세요:
- 절차 단계어: 검토/추진/논의/협의/예정 ↔ 합의/확정/체결/승인/허가/완료, 정부안/초안 ↔ 최종안/확정안
- 숫자·단위·비율, 날짜·연도·기한
- 주체(누가)와 대상, 단독/공동

판정 기준 — 순서대로 적용:
1. 원문에 요약과 **다른 값·다른 단계가 명시**돼 있다 → "contradiction". source_quote 에 그 다른 값이 적힌 원문 구절을 그대로 인용.
2. 원문이 그 내용을 **아예 언급하지 않는다**(원문이 잘렸을 수 있음) → 반드시 "unsupported". 언급이 없는 것은 모순이 아니다.
3. 원문이 과거(수개월 이상 전) 일로 언급한 사건을 요약 제목이 새 소식처럼 세웠다 → "stale".
4. 그 밖 → "ok".

출력은 JSON 하나: {{"items": [{{"id": "...", "verdict": "ok|contradiction|stale|unsupported", "field": "title|summary|detail|", "claim": "요약의 문제 구절", "source_quote": "원문 인용", "reason": "한 줄"}}]}}
입력의 id 를 모두 포함하세요."""


def build_user_message(target: dict) -> str:
    # 발행일을 준다. 없으면 모델이 '내년'을 제 기준으로 풀어 멀쩡한 문장을 모순이라
    # 짚었다(스모크 2026-09-24: TSMC "내년 1분기"=2027 을 틀렸다고 함).
    published = str(target.get("published") or "")[:10]
    return (f"### id={target['hash'][:8]}\n[원문]\n발행일: {published or '미상'}\n"
            f"제목: {target.get('title') or ''}\n"
            f"본문: {target.get('body') or ''}\n"
            f"[요약]\n제목: {target.get('title_kr') or ''}\n요약: {target.get('summary') or ''}\n"
            f"상세: {target.get('detail') or ''}\n")


def _normalize(text: str) -> str:
    return re.sub(r"[\s\"'“”‘’`·…]+", "", str(text or ""))


def quote_in_source(quote: str, source: str) -> bool:
    """모델이 인용한 구절이 **이 기사의 원문에** 실제로 있는가.

    묶음 측정에서 모델이 옆 기사의 원문을 근거로 인용한 적이 있다. 1건씩 물어도
    모델은 원문을 바꿔 적을 수 있으므로, 인용이 원문에 없으면 모순 판정을 믿지
    않는다. 말줄임(…)으로 이어 붙인 인용은 조각마다 확인한다.
    """
    haystack = _normalize(source)
    pieces = [_normalize(part) for part in re.split(r"\.{3}|…", str(quote or ""))]
    pieces = [piece for piece in pieces if len(piece) >= 6]
    return bool(pieces) and all(piece in haystack for piece in pieces)


# 모델이 contradiction 이라 적고 이유로는 "언급 없음"을 댄 경우. 측정에서 4/10 이
# 이랬다 — 원문이 잘려 확인할 수 없는 것을 모순이라 부른 것이다.
_NOT_MENTIONED = re.compile(
    r"언급(되지|이 없|은 없|이 전혀|하지 않)|내용(은|이) 없|원문에 없|확인할 수 없|명시되어 있지 않")


def classify(row: dict, source: str) -> tuple[str, bool]:
    """모델 판정을 후처리한다. (최종 판정, 인용이 원문에 있는가)."""
    verdict = str(row.get("verdict") or "").strip().lower()
    if verdict not in {"ok", "contradiction", "stale", "unsupported"}:
        return "invalid", False
    quoted = quote_in_source(row.get("source_quote") or "", source)
    if verdict == "contradiction":
        if _NOT_MENTIONED.search(str(row.get("reason") or "")):
            return "unsupported", quoted
        if not quoted:
            return "quote_unverified", False
    return verdict, quoted


def input_sha(target: dict) -> str:
    raw = "\x1f".join(str(target.get(key) or "") for key in ("title_kr", "summary", "detail"))
    return hashlib.sha256(f"{PROMPT_VERSION}\x1f{raw}".encode("utf-8")).hexdigest()[:16]


# ── 기록 ─────────────────────────────────────────────────────────────────

def load_log(path: Path = LOG_FILE) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # union 병합이 줄을 망가뜨려도 나머지는 산다
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_log(rows: list[dict], path: Path = LOG_FILE) -> None:
    if not rows:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"[요약검사] 기록 실패 — {exc}")


def _clip(value: object) -> str:
    text = str(value or "").strip()
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT - 1] + "…"


def _is_stop_error(exc: Exception) -> bool:
    """한도(429)·결제(402)·설정 오류는 그 회차 전체를 멈춘다 — 요약 몫을 지킨다."""
    if type(exc).__name__ == "GeminiConfigError":
        return True
    text = str(exc)
    return "HTTP 429" in text or "HTTP 402" in text


def verify(targets: list[dict], *, client=None, now: datetime | None = None,
           path: Path = LOG_FILE, day_cap: int | None = None,
           per_run_cap: int | None = None) -> tuple[list[dict], dict]:
    """대상 기사를 1건씩 검사하고 결과를 기록한다. 요약은 바꾸지 않는다.

    Args:
        targets: [{"hash", "title", "body", "title_kr", "summary", "detail"}, ...]
    Returns:
        (이번에 기록한 행, 통계)
    """
    now = now or datetime.now(timezone.utc)
    day = quota_day(now)
    day_cap = daily_cap() if day_cap is None else day_cap
    per_run_cap = run_cap() if per_run_cap is None else per_run_cap
    stats = {"targets": len(targets), "checked": 0, "cached": 0, "failed": 0,
             "skipped_cap": 0, "stopped": "", "status": "ok",
             "contradiction": 0, "stale": 0, "unsupported": 0,
             "quote_unverified": 0, "rule": 0}
    if client is None:
        import gemini_client as client  # 지연 import — 테스트에서 대역 주입

    history = load_log(path)
    done = {(row.get("hash"), row.get("input_sha")) for row in history if row.get("called")}
    # 호출 없이 규칙만 적은 행. 밀린 기사를 회차마다 다시 볼 때 같은 줄이 쌓이지 않게.
    noted = {(row.get("hash"), row.get("input_sha")) for row in history}
    used_today = sum(1 for row in history if row.get("called") and row.get("quota_day") == day)
    budget = max(0, min(day_cap - used_today, per_run_cap))
    available = client.is_available()
    if not available:
        stats["status"] = "no_api_key"
    policy = llm_policy.profile("summary_verify")
    system = system_prompt(now)

    rows: list[dict] = []
    for target in targets:
        sha = input_sha(target)
        if (target["hash"], sha) in done:
            stats["cached"] += 1
            continue
        source = f"{target.get('title') or ''}\n{target.get('body') or ''}"
        summary_text = " ".join(str(target.get(key) or "")
                                for key in ("title_kr", "summary", "detail"))
        rule = stage_word_findings(summary_text, source)
        stats["rule"] += bool(rule)
        row = {
            "hash": target["hash"], "input_sha": sha, "prompt_version": PROMPT_VERSION,
            "checked_at": now.isoformat(timespec="seconds"), "quota_day": day,
            "title_kr": _clip(target.get("title_kr")), "rule": rule,
            "called": False, "verdict": "",
        }
        if not available or stats["stopped"] or budget <= 0:
            stats["skipped_cap"] += bool(available and not stats["stopped"])
            if rule and (target["hash"], sha) not in noted:
                rows.append(row)  # 규칙은 호출 없이도 결과가 있다
            continue
        try:
            payload = client.call_json(
                system, build_user_message(target),
                temperature=0.0, max_output_tokens=2048,
                model=policy.model(), label="summary_verify",
                **policy.reasoning_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001 — 검사 실패가 수집을 멈추면 안 된다
            stats["failed"] += 1
            if _is_stop_error(exc):
                stats["stopped"] = type(exc).__name__
                stats["status"] = f"stopped: {str(exc)[:80]}"
            continue
        budget -= 1
        items = payload.get("items") if isinstance(payload, dict) else None
        answer = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if answer is None:
            stats["failed"] += 1
            continue
        verdict, quoted = classify(answer, source)
        stats["checked"] += 1
        if verdict in stats:
            stats[verdict] += 1
        row.update({
            "called": True, "model": policy.model(), "verdict": verdict,
            "model_verdict": str(answer.get("verdict") or ""),
            "field": _clip(answer.get("field")), "claim": _clip(answer.get("claim")),
            "source_quote": _clip(answer.get("source_quote")),
            "quote_in_source": quoted, "reason": _clip(answer.get("reason")),
        })
        rows.append(row)
    append_log(rows, path)
    return rows, stats


def remaining_budget(*, now: datetime | None = None, path: Path = LOG_FILE,
                     day_cap: int | None = None, per_run_cap: int | None = None) -> int:
    """이번 회차에 더 물을 수 있는 횟수. 밀린 기사의 본문을 받을지 정할 때 쓴다."""
    now = now or datetime.now(timezone.utc)
    day = quota_day(now)
    day_cap = daily_cap() if day_cap is None else day_cap
    per_run_cap = run_cap() if per_run_cap is None else per_run_cap
    used = sum(1 for row in load_log(path) if row.get("called") and row.get("quota_day") == day)
    return max(0, min(day_cap - used, per_run_cap))


def _parse_time(value: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def backlog_candidates(curated: dict, exclude: set[str], is_fallback, *,
                       now: datetime | None = None, path: Path = LOG_FILE,
                       limit: int = BACKLOG_PER_RUN) -> list[dict]:
    """최근 요약됐는데 아직 검사하지 못한 기사. 본문 없이 돌려준다(오래된 것부터).

    이번 회차 대상(`exclude`)은 뺀다 — 그쪽은 본문이 이미 손에 있다. 창을 넘긴
    기사는 포기한다: 이미 여러 번 화면에 나갔고, 경고의 쓸모는 새 요약에 있다.
    """
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(hours=BACKLOG_HOURS)
    checked = {(row.get("hash"), row.get("input_sha"))
               for row in load_log(path) if row.get("called")}
    out = []
    for h, cur in (curated or {}).items():
        if h in exclude or not isinstance(cur, dict) or not cur.get("link"):
            continue
        if cur.get("importance") not in KEPT_IMPORTANCE or is_fallback(cur):
            continue
        cached_at = _parse_time(cur.get("cached_at"))
        if cached_at is None or cached_at < oldest or cached_at > now:
            continue
        candidate = {"hash": h, "title": cur.get("title") or "", "link": cur["link"],
                     "title_kr": cur.get("title_kr") or "", "summary": cur.get("summary") or "",
                     "detail": cur.get("detail") or "",
                     "published": str(cur.get("published_at") or "")[:10],
                     "_cached_at": cached_at}
        if (h, input_sha(candidate)) in checked:
            continue
        out.append(candidate)
    out.sort(key=lambda row: (row["_cached_at"], row["hash"]))
    for row in out:
        row.pop("_cached_at")
    return out[:max(0, limit)]


def attach_bodies(candidates: list[dict], fetch) -> list[dict]:
    """본문을 다시 받아 붙인다. 못 받은 기사는 뺀다(다음 회차가 창 안에서 다시 본다)."""
    if not candidates:
        return []
    bodies, _stats = fetch([{"hash": row["hash"], "link": row["link"], "title": row["title"]}
                            for row in candidates])
    out = []
    for row in candidates:
        body = (bodies or {}).get(row["hash"]) or ""
        if body:
            out.append({**row, "body": body})
    return out


def targets_from_curation(articles: list[dict], curated: dict, bodies: dict,
                          attempted: set[str], is_fallback) -> list[dict]:
    """이번 회차에 새로 요약했고, 본문이 있고, 화면에 설 등급인 기사만."""
    out = []
    for article in articles:
        h = article.get("hash")
        cur = curated.get(h)
        body = bodies.get(h) or ""
        if h not in attempted or not body or not isinstance(cur, dict):
            continue
        if cur.get("importance") not in KEPT_IMPORTANCE or is_fallback(cur):
            continue
        out.append({"hash": h, "title": article.get("title") or "", "body": body,
                    # news_bot 이 정규화해 둔 ISO 발행시각(원시 pub 은 RFC 822 일 수 있다)
                    "published": str(cur.get("published_at") or "")[:10],
                    "title_kr": cur.get("title_kr") or "", "summary": cur.get("summary") or "",
                    "detail": cur.get("detail") or ""})
    return out


def report(rows: list[dict], stats: dict) -> list[str]:
    lines = [f"[요약검사] 대상 {stats['targets']} · 검사 {stats['checked']} · 이미 검사 {stats['cached']}"
             f" · 상한 보류 {stats['skipped_cap']} · 실패 {stats['failed']} · 상태 {stats['status']}"
             f" → 모순 {stats['contradiction']} · 과거사건 {stats['stale']}"
             f" · 인용불일치 {stats['quote_unverified']} · 단계어 규칙 {stats['rule']} (경고 모드)"]
    for row in rows:
        if row.get("verdict") in {"contradiction", "stale"} or row.get("rule"):
            lines.append(f"  ! {row['hash'][:8]} {row.get('verdict') or '-'}"
                         f"{' 규칙:' + ','.join(row['rule']) if row.get('rule') else ''}"
                         f" | {row.get('title_kr', '')[:40]} | 요약: {row.get('claim', '')[:60]}"
                         f" | 원문: {row.get('source_quote', '')[:60]}")
    return lines
