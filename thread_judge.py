"""두 **사건**이 같은 장기 이야기의 다른 단계인가 — `same_event` 와 다른 질문이다.

왜 별도 계약인가
----------------
`issue_review.SYSTEM_PROMPT` 는 정확히 반대쪽을 지킨다.

    다른 사건이다: … 주체는 같지만 안건이 다르다 … 대상 원전·호기·국가가 다르다
    판단이 서지 않으면 false 를 택한다. 잘못 합치는 것이 놓치는 것보다 해롭다.

그 계약을 그대로 쓰면 장기 스토리는 **정의상 만들어지지 않는다.**

    고리 2호기 계속운전 신청   ↔  고리 2호기 계속운전 심사 착수
        same_event = false   (단계가 다르다)
        same_thread = true   (한 이야기다)

    고리 2호기 계속운전       ↔  고리 3·4호기 계속운전 심사
        same_event = false
        same_thread = false  (대상이 다르다)

    고리 2호기 계속운전       ↔  고리 2호기 계획예방정비
        same_event = false
        same_thread = false  (같은 호기지만 다른 사안이다)

셋째 줄이 이 모듈이 실제로 어려운 이유다. 호기가 같다는 것만으로는 아무것도
결정되지 않는다.

캐시를 따로 두는 이유
---------------------
`issue_review.PROMPT_VERSION` 을 건드리면 **13,419건이 한 번에 무효**가 된다
(이미 1,715건이 v1 인 채로 사문이다). 계약이 다르므로 파일도 키도 버전도 나눈다.

규칙이 먼저다
-------------
LLM 은 경계 사례만 본다. 값싸고 확실한 거부를 먼저 건다 —

    호기 모순          고리 2호기 ↔ 고리 3호기       (언어를 건너 본다)
    공통 신원 신호 0   분야만 같다                    (topic 은 story 가 아니다)

**규칙으로 승인하지 않는다.** 승인은 precision 이 필요한 판단이고, 위 셋째 줄이
보여주듯 구조화 신호만으로는 갈리지 않는다.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import asset_alias
import llm_cache
import llm_policy

try:  # pragma: no cover
    from gemini_client import GeminiTruncated
except ImportError:  # pragma: no cover
    class GeminiTruncated(Exception):
        """gemini_client 부재 시 자리표시자."""

ROOT = Path(__file__).resolve().parent
CACHE_FILE = Path(os.environ.get("THREAD_JUDGE_CACHE_FILE")
                  or (ROOT / "thread_llm_reviews.json"))
CACHE_KEY = "threads"
CACHE_COMMENT = "장기 스토리 연결 판정 캐시. issue_llm_reviews.json 과 다른 계약이다."

# 이 모듈만의 버전. `issue_review.PROMPT_VERSION` 과 **함께 움직이지 않는다.**
PROMPT_VERSION = 1
CONTRACT_VERSION = "thread-judge-v1"

BATCH_SIZE = 12
MAX_OUTPUT_TOKENS = 16384
MIN_SPLIT_SIZE = 2
SPLIT_BUDGET = 4

VERDICTS = ("same_thread", "different_thread", "uncertain")

# 규칙이 거부하는 이유. 판정 분포를 보면 어디서 후보가 죽는지 바로 보인다.
REJECT_UNIT_CONFLICT = "unit_conflict"
REJECT_NO_SHARED_IDENTITY = "no_shared_identity"
# 구조화 신호가 없어도 이만큼 특이한 어휘를 공유하면 판정으로 넘긴다.
# `event_retrieval` 의 lexical 은 IDF 가중 겹침을 길이로 정규화한 값이다.
#
# 6.0 의 근거(2026-09-13 실측, 후보 2,789쌍 중 구조화 신호 0 인 1,516쌍):
#   5.0~6.0 구간은 눈으로 봐도 분야만 같다 —
#     "한수원 한빛원전 저장시설 논란" ↔ "아마존 데이터센터 탄소배출 논란"
#     "트럼프 AI 데이터센터 규제완화" ↔ "엔비디아 랜시엄 투자"
#   6.0 이상 214쌍에는 제목이 글자까지 같은 쌍이 몰려 있다(원장에 남은 재클러스터링
#   흔적). 그건 반드시 봐야 하는 쌍이다.
LEXICAL_RESCUE = 6.0

SYSTEM_PROMPT = """너는 원자력 산업 뉴스의 장기 추적을 담당하는 편집자다.
두 **사건**이 같은 장기 이야기(스토리)의 서로 다른 단계인지 판정한다.

이것은 "같은 사건인가"를 묻는 것이 **아니다.** 두 사건이 서로 다른 사건인 것은
이미 전제다. 묻는 것은 독자가 하나의 이야기로 이어 읽어야 하는가이다.

같은 스토리다 (same_thread):
- 같은 대상(특정 호기·특정 프로젝트·특정 법안/계획)에 대한 하나의 절차가 단계를
  밟아 가는 것. 신청 → 심사 착수 → 의견수렴 → 심의 → 결정
- 같은 계약·사업의 협상 → 체결 → 착공 → 준공
- 같은 사고·고장의 발생 → 원인조사 → 복구 → 재발방지
- 한쪽이 다른 쪽의 직접적인 원인이거나 결과인 경우

다른 스토리다 (different_thread):
- 대상이 다르다. 다른 호기·다른 부지·다른 나라 사업이면 절차가 같아도 다르다
- 같은 대상이지만 **다른 사안**이다 (계속운전 심사 ↔ 계획예방정비,
  계속운전 심사 ↔ 부지 내 저장시설 반대)
- 같은 정책 이름이지만 다른 연도·다른 차수다
- 같은 정비지만 다른 회차다
- 주제만 같다 (둘 다 SMR, 둘 다 계속운전 일반론, 둘 다 업계 전망)
- 한쪽이 특정 사안이고 다른 쪽은 업계 전반의 동향·전망·의견이다

판단이 서지 않으면 uncertain 을 택한다. 억지로 이으면 서로 다른 사안이 한
이야기로 연쇄 병합되고, 그 이야기는 아무 말도 하지 않게 된다.

relationship 은 같은 스토리일 때만 의미가 있다. 다음 중 하나를 고른다:
  stage_progress   같은 절차의 다음 단계
  cause_effect     한쪽이 다른 쪽의 원인 또는 결과
  same_matter      같은 사안의 다른 측면(같은 단계일 수도 있다)

출력은 JSON 하나:
{"items": [{"idx": 0, "verdict": "same_thread", "relationship": "stage_progress",
  "reason": "25자 이내 근거"}]}
입력에 준 idx 를 모두 포함한다."""

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def pair_id(left_id: str, right_id: str) -> str:
    """좌우 순서와 무관한 키. 사건 id 는 D 가 못 박아 안정적이다."""
    first, second = sorted((str(left_id), str(right_id)))
    return f"{first}--{second}"


def load_cache(path: Path | None = None) -> dict:
    return llm_cache.load(path or CACHE_FILE, CACHE_KEY)


def save_cache(cache: dict, path: Path | None = None) -> None:
    llm_cache.save(cache, path or CACHE_FILE, key=CACHE_KEY,
                   prompt_version=PROMPT_VERSION, comment=CACHE_COMMENT)


def cached_verdict(cache: dict, key: str) -> dict | None:
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    if entry.get("prompt_version") != PROMPT_VERSION:
        return None
    if entry.get("verdict") not in VERDICTS:
        return None
    return entry


def _text(event) -> str:
    return f"{getattr(event, 'title', '')} {getattr(event, 'summary', '')}"


def rule_verdict(left, right, signals: dict | None = None) -> tuple[str, str] | None:
    """LLM 없이 끝나는 자리. **거부만** 한다.

    Args:
        signals: `event_retrieval` 이 낸 신호. 없으면 구조화 신호만 본다.

    Returns:
        (verdict, reason) 또는 None(= LLM 에게 물어야 한다).
    """
    if asset_alias.conflict(_text(left), _text(right)):
        return "different_thread", REJECT_UNIT_CONFLICT
    shared = (
        (left.units & right.units)
        | (left.entities & right.entities)
        | (left.assets & right.assets)
        | (left.plants & right.plants)
    )
    if shared:
        return None
    # 구조화 신호가 하나도 없다. 대개는 분야만 같은 쌍이다 — 그런데 **그것만으로
    # 거부하면 구조화 칸이 비어 있는 옛 사건이 통째로 잘린다.** 첫 실측에서
    # 후보 2,204쌍 중 2,027쌍(92%)이 이 규칙 하나로 죽었고, 원인은 원장의 옛
    # 항목에 `entity_ids` 가 없는 것이었다. 그래서 어휘가 충분히 특이하면
    # 판정으로 넘긴다 — 규칙은 **값싼 거부**이지 최종 판단이 아니다.
    lexical = float((signals or {}).get("lexical") or 0.0)
    if lexical >= LEXICAL_RESCUE:
        return None
    return "different_thread", REJECT_NO_SHARED_IDENTITY


def build_user_message(pairs: list[dict]) -> str:
    lines: list[str] = []
    for idx, row in enumerate(pairs):
        left, right = row["left"], row["right"]
        lines.append(f"[{idx}]")
        for tag, event in (("A", left), ("B", right)):
            facts = event.raw.get("facts") or {}
            lines.append(f"  {tag} 제목: {event.title}")
            lines.append(f"     기간: {event.first_seen} ~ {event.last_seen}"
                         f" · 회차 {event.briefing_count}")
            if event.summary:
                lines.append(f"     요약: {event.summary[:220]}")
            lines.append(f"     대상: 호기={sorted(event.units) or '-'}"
                         f" 엔티티={sorted(event.entities) or '-'}")
            lines.append(f"     지문: 주체={facts.get('actors') or '-'}"
                         f" 설비={facts.get('assets') or '-'}"
                         f" 사건군={facts.get('event_family') or '-'}"
                         f" 행동={facts.get('action') or '-'}")
    return "\n".join(lines)


def _parse(payload: dict, count: int) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in (payload or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < count:
            continue
        verdict = str(item.get("verdict") or "")
        if verdict not in VERDICTS:
            continue
        out[idx] = {
            "verdict": verdict,
            "relationship": str(item.get("relationship") or ""),
            "reason": str(item.get("reason") or "")[:80],
        }
    return out


def judge(pairs: list[dict], *, cache_path: Path | None = None, client=None,
          batch_size: int = BATCH_SIZE,
          max_new_pairs: int | None = None) -> tuple[dict[str, dict], dict]:
    """후보 쌍을 판정한다.

    Args:
        pairs: ``{"key", "left", "right"}`` — left/right 는 event_retrieval.Event.

    Returns:
        (verdicts, stats). 판정하지 못한 쌍은 **넣지 않는다**(= 잇지 않는다).
        `issue_similarity` 의 원칙 그대로 — 잘못 잇는 것이 놓치는 것보다 해롭다.
    """
    cache_path = cache_path or CACHE_FILE
    stats = {"contract": CONTRACT_VERSION, "prompt_version": PROMPT_VERSION,
             "candidates": len(pairs), "rule_rejected": 0, "from_cache": 0,
             "asked": 0, "calls": 0, "failed": 0, "splits": 0,
             "rule_reasons": {}, "failure_reasons": {}, "status": "ok"}
    verdicts: dict[str, dict] = {}
    todo: list[dict] = []
    cache = load_cache(cache_path)

    for row in pairs:
        key = row["key"]
        ruled = rule_verdict(row["left"], row["right"], row.get("signals"))
        if ruled is not None:
            verdict, reason = ruled
            verdicts[key] = {"verdict": verdict, "reason": reason,
                             "relationship": "", "method": "rule"}
            stats["rule_rejected"] += 1
            stats["rule_reasons"][reason] = stats["rule_reasons"].get(reason, 0) + 1
            continue
        hit = cached_verdict(cache, key)
        if hit is not None:
            verdicts[key] = {**hit, "method": "cache"}
            stats["from_cache"] += 1
            continue
        todo.append(row)

    if max_new_pairs is not None and len(todo) > max_new_pairs:
        stats["deferred"] = len(todo) - max_new_pairs
        todo = todo[:max_new_pairs]

    if todo:
        if client is None:
            try:
                import gemini_client  # noqa: PLC0415
                client = gemini_client
            except ImportError:
                client = None
        if client is None or not client.is_available():
            stats["status"] = "no_api_key"
            stats["failed"] = len(todo)
            stats["failure_reasons"]["no_api_key"] = len(todo)
            todo = []

    policy = llm_policy.profile("thread_judge")
    model = policy.model()
    fingerprint = llm_policy.generation_policy_fingerprint(policy, PROMPT_VERSION)
    stats["model"] = model
    stats["generation_policy_fingerprint"] = fingerprint
    now = datetime.now(timezone.utc).isoformat()
    split_budget = SPLIT_BUDGET

    def ask(chunk: list[dict]) -> None:
        nonlocal split_budget
        try:
            payload = client.call_json(
                SYSTEM_PROMPT, build_user_message(chunk),
                temperature=0.0, max_output_tokens=MAX_OUTPUT_TOKENS,
                model=model, label="thread_judge", **policy.reasoning_kwargs(),
            )
        except GeminiTruncated:
            if len(chunk) >= MIN_SPLIT_SIZE * 2 and split_budget > 0:
                split_budget -= 1
                stats["splits"] += 1
                mid = len(chunk) // 2
                ask(chunk[:mid])
                ask(chunk[mid:])
                return
            stats["failed"] += len(chunk)
            stats["failure_reasons"]["truncated"] = \
                stats["failure_reasons"].get("truncated", 0) + len(chunk)
            return
        except Exception as exc:  # noqa: BLE001 — 실패는 '잇지 않음' 으로 흡수
            stats["failed"] += len(chunk)
            name = type(exc).__name__
            stats["failure_reasons"][name] = stats["failure_reasons"].get(name, 0) + len(chunk)
            return
        stats["calls"] += 1
        parsed = _parse(payload, len(chunk))
        for idx, row in enumerate(chunk):
            if idx not in parsed:
                stats["failed"] += 1
                stats["failure_reasons"]["unparsed"] = \
                    stats["failure_reasons"].get("unparsed", 0) + 1
                continue
            result = parsed[idx]
            verdicts[row["key"]] = {**result, "method": "llm"}
            stats["asked"] += 1
            cache[row["key"]] = {
                **result,
                "left_title": row["left"].title,
                "right_title": row["right"].title,
                "prompt_version": PROMPT_VERSION,
                "generation_policy_fingerprint": fingerprint,
                "model": model,
                "reviewed_at": now,
            }

    for start in range(0, len(todo), batch_size):
        ask(todo[start:start + batch_size])
    if todo:
        save_cache(cache, cache_path)
    if stats["failed"] and stats["status"] == "ok":
        stats["status"] = "partial_failure"
    return verdicts, stats
