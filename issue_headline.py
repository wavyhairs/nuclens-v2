"""이슈 카드의 **표시 제목** — 신원과 완전히 갈라 둔 계층.

왜 `issue.title` 을 못 고치나
-----------------------------
그 제목이 이 저장소의 **판정 입력**이기 때문이다. 한 줄만 바꿔도 다음이 전부 흔들린다 —

    dedup / event_stage        단계가 갈리는 자리를 제목에서 읽는다
    issue_continuity           진행도 척도를 제목에서 읽는다
    story_fingerprint          축을 제목에서 뜬다
    asset_alias.conflict       호기 모순을 제목에서 본다
    thread_judge               두 사건을 모델에게 보일 때 이 제목을 넘긴다

그래서 제목은 얼린다. 화면이 더 좋은 문장을 원하면 **다른 칸**을 만든다.
이 저장소는 같은 결론에 이미 두 번 닿았다 —

    `pick_detail`        "제목은 화면에 보일 문자열이지 신원이 아니다"
    `issue_change_log`   "판정 재료는 기사 해시뿐이다"

계약
----
    입력   canonical title + latest_change + 핵심 요지
    출력   issue.headline_display  (Web · Telegram · briefing 만 읽는다)

`headline_display` 는 **어떤 상류 판정에도 들어가지 않는다.** 이 모듈을 import
하는 쪽은 표시 계층뿐이고, `tests/test_issue_headline.py` 가 그것을 못 박는다.

왜 캐시가 입력 지문인가
-----------------------
같은 이슈가 하루 여덟 번 다시 지어진다. 제목이 그대로인데 매번 새로 물으면
호출량이 이슈 수 × 빌드 수가 된다. 그래서 **입력이 달라졌을 때만** 다시 묻는다.
지문은 세 입력을 그대로 이어 붙여 해시한 값이라, 한 글자만 달라져도 갱신된다.

무엇을 쓰지 않는가
------------------
투자 어휘. Nuclens 는 원자력·에너지 정책/산업 동향 서비스이지 투자뉴스가 아니다.
모델이 '수혜'·'호재'·'모멘텀' 을 쓰면 그 출력은 버리고 원 제목으로 물러선다.
**숫자도 새로 만들지 못한다** — 입력에 없는 수치가 제목에 나타나면 버린다.
헤드라인의 거짓 수치는 사용자가 확인할 방법이 없는 종류의 거짓말이다.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import llm_cache
import llm_policy

ROOT = Path(__file__).resolve().parent
CACHE_FILE = Path(os.environ.get("ISSUE_HEADLINE_CACHE_FILE")
                  or (ROOT / "issue_headlines.json"))
CACHE_KEY = "headlines"
CACHE_COMMENT = ("이슈 카드의 표시 제목. **신원이 아니다** — issue.title 을 대신하지 "
                 "않고, 어떤 판정에도 입력되지 않는다.")

PROMPT_VERSION = 1
CONTRACT_VERSION = "issue-headline-v1"

BATCH_SIZE = 10
MAX_OUTPUT_TOKENS = 4096
# 카드 한 줄. 넘으면 화면에서 잘리므로 모델이 아니라 여기서 막는다.
MAX_HEADLINE_CHARS = 64
MIN_HEADLINE_CHARS = 8

# 투자뉴스 문법. 하나라도 걸리면 그 출력을 버린다.
FORBIDDEN = (
    "호재", "수혜", "악재", "모멘텀", "테마주", "관련주", "목표주가", "주가",
    "시장 기대", "시장기대", "투자 유망", "급등", "급락", "상승세", "하락세",
    "매수", "매도", "밸류에이션", "실적 개선 기대", "장밋빛",
)

_DIGITS_RE = re.compile(r"\d[\d,.]*")
_SPACE_RE = re.compile(r"\s+")

SYSTEM_PROMPT = """너는 원자력·에너지 정책/산업 동향 뉴스의 편집자다.
이슈 카드 맨 위에 걸 **한 줄 제목**을 쓴다.

독자가 그 한 줄만 보고 "무슨 일이 일어났는가"를 알 수 있어야 한다.

규칙:
- 제공된 제목·변화·요지에 **실제로 있는 사실만** 쓴다. 없는 것을 덧붙이지 않는다.
- 주체와 행동을 앞에 둔다. "기후부, 전북 진안·충남 금산서 계절별 송전용량 시범사업 착수"
- 변화가 있으면 그것이 제목의 핵심이다. 무엇이 결정·착수·승인·연기됐는가.
- 개조식으로 쓴다. "…했습니다", "…한다고 밝혔다" 같은 기사체 서술어를 쓰지 않는다.
- 64자를 넘지 않는다.

금지:
- 투자·주가 관점. '호재', '수혜', '시장 기대', '관련주' 같은 어휘를 쓰지 않는다.
- 기사에 없는 정책 효과·전망을 단정하지 않는다.
- 분석자의 의견을 사실처럼 쓰지 않는다.
- 입력에 없는 숫자를 만들지 않는다.

원 제목이 이미 충분히 좋으면 그대로 두어도 된다. 억지로 고치지 않는다.

출력은 JSON 하나:
{"items": [{"idx": 0, "headline": "한 줄 제목"}]}
입력에 준 idx 를 모두 포함한다."""


def _clean(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\n", " ")).strip()


def input_fingerprint(title: str, change: str, detail: str) -> str:
    """이 세 입력이 달라졌을 때만 다시 묻는다."""
    joined = "\x1f".join(_clean(part) for part in (title, change, detail))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", "").rstrip(".")
            for match in _DIGITS_RE.finditer(text)}


def acceptable(headline: str, sources: str) -> tuple[bool, str]:
    """이 출력을 화면에 걸어도 되는가.

    Returns:
        (통과 여부, 거부 이유).
    """
    headline = _clean(headline)
    if not headline:
        return False, "empty"
    if len(headline) > MAX_HEADLINE_CHARS:
        return False, "too_long"
    if len(headline) < MIN_HEADLINE_CHARS:
        return False, "too_short"
    lowered = headline.lower()
    for word in FORBIDDEN:
        if word.lower() in lowered:
            return False, f"forbidden:{word}"
    invented = _numbers(headline) - _numbers(sources)
    if invented:
        # 입력에 없는 수치. 헤드라인의 거짓 숫자는 독자가 확인할 방법이 없다.
        return False, f"invented_number:{sorted(invented)[0]}"
    return True, ""


def load_cache(path: Path | None = None) -> dict:
    return llm_cache.load(path or CACHE_FILE, CACHE_KEY)


def save_cache(cache: dict, path: Path | None = None) -> None:
    llm_cache.save(cache, path or CACHE_FILE, key=CACHE_KEY,
                   prompt_version=PROMPT_VERSION, comment=CACHE_COMMENT)


def cached_headline(cache: dict, issue_id: str, fingerprint: str) -> str:
    entry = cache.get(issue_id)
    if not isinstance(entry, dict):
        return ""
    if entry.get("prompt_version") != PROMPT_VERSION:
        return ""
    if entry.get("input_fingerprint") != fingerprint:
        # 입력의 뜻이 달라졌다. 옛 제목을 그대로 걸면 화면이 거짓말을 한다.
        return ""
    return _clean(entry.get("headline"))


def build_user_message(rows: list[dict]) -> str:
    lines: list[str] = []
    for idx, row in enumerate(rows):
        lines.append(f"[{idx}]")
        lines.append(f"  현재 제목: {row['title']}")
        if row.get("change"):
            lines.append(f"  달라진 것: {row['change']}")
        if row.get("detail"):
            lines.append(f"  요지: {row['detail'][:300]}")
    return "\n".join(lines)


def _parse(payload: dict, count: int) -> dict[int, str]:
    out: dict[int, str] = {}
    for item in (payload or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < count:
            out[idx] = _clean(item.get("headline"))
    return out


def build(issues: list[dict], *, client=None, cache_path: Path | None = None,
          batch_size: int = BATCH_SIZE,
          max_new: int | None = None) -> tuple[dict[str, str], dict]:
    """이슈마다 표시 제목을 정한다.

    Args:
        issues: ``{"issue_id", "title", "change", "detail"}``.

    Returns:
        (``{issue_id: headline}``, stats). **어떤 이슈도 빠지지 않는다** — 물어보지
        못했거나 출력이 규칙에 걸리면 원 제목이 그 자리에 선다. 화면에 빈칸이
        생기지 않는 것이 이 계약의 전부다.
    """
    cache_path = cache_path or CACHE_FILE
    stats = {"contract": CONTRACT_VERSION, "prompt_version": PROMPT_VERSION,
             "issues": len(issues), "from_cache": 0, "asked": 0, "calls": 0,
             "rejected": 0, "failed": 0, "fell_back": 0,
             "reject_reasons": {}, "status": "ok"}
    out: dict[str, str] = {}
    todo: list[dict] = []
    cache = load_cache(cache_path)

    prepared = []
    for row in issues:
        title = _clean(row.get("title"))
        change = _clean(row.get("change"))
        detail = _clean(row.get("detail"))
        fingerprint = input_fingerprint(title, change, detail)
        prepared.append({"issue_id": str(row.get("issue_id") or ""), "title": title,
                         "change": change, "detail": detail,
                         "fingerprint": fingerprint})

    for row in prepared:
        hit = cached_headline(cache, row["issue_id"], row["fingerprint"])
        if hit:
            out[row["issue_id"]] = hit
            stats["from_cache"] += 1
            continue
        out[row["issue_id"]] = row["title"]      # 먼저 안전한 값을 깔아 둔다
        todo.append(row)

    if max_new is not None and len(todo) > max_new:
        stats["deferred"] = len(todo) - max_new
        todo = todo[:max_new]

    if todo:
        if client is None:
            try:
                import gemini_client  # noqa: PLC0415
                client = gemini_client
            except ImportError:
                client = None
        if client is None or not client.is_available():
            stats["status"] = "no_api_key"
            stats["fell_back"] += len(todo)
            todo = []

    if not todo:
        return out, stats

    policy = llm_policy.profile("issue_headline")
    model = policy.model()
    stats["model"] = model
    now = datetime.now(timezone.utc).isoformat()

    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        try:
            payload = client.call_json(
                SYSTEM_PROMPT, build_user_message(chunk),
                temperature=0.2, max_output_tokens=MAX_OUTPUT_TOKENS,
                model=model, label="issue_headline", **policy.reasoning_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001 — 실패는 '원 제목 유지' 로 흡수
            stats["failed"] += len(chunk)
            stats["fell_back"] += len(chunk)
            name = type(exc).__name__
            stats["reject_reasons"][name] = stats["reject_reasons"].get(name, 0) + len(chunk)
            continue
        stats["calls"] += 1
        parsed = _parse(payload, len(chunk))
        for idx, row in enumerate(chunk):
            headline = parsed.get(idx, "")
            sources = " ".join([row["title"], row["change"], row["detail"]])
            ok, reason = acceptable(headline, sources)
            if not ok:
                stats["rejected" if headline else "failed"] += 1
                stats["fell_back"] += 1
                key = reason or "unparsed"
                stats["reject_reasons"][key] = stats["reject_reasons"].get(key, 0) + 1
                continue
            out[row["issue_id"]] = headline
            stats["asked"] += 1
            cache[row["issue_id"]] = {
                "headline": headline,
                "input_fingerprint": row["fingerprint"],
                "canonical_title": row["title"],
                "prompt_version": PROMPT_VERSION,
                "model": model,
                "reviewed_at": now,
            }

    save_cache(cache, cache_path)
    if stats["failed"] and stats["status"] == "ok":
        stats["status"] = "partial_failure"
    return out, stats
