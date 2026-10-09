"""원문 대조에서 연달아 격리되는 기사를 보류한다 — 격리의 끝 상태.

왜 이 파일이 있나
-----------------
원문과 안 맞는다고 격리된 기사는 ``sent`` 로 마킹되지 않는다. 그래서 수집 창(RSS
24시간 · 공식 게시판 7일) 안에 있는 동안 3시간마다 다시 요약됐고, 창을 벗어나면
아무 기록 없이 사라졌다. 하루 요약은 매번 "다음 수집에서 다시 만듭니다"라고만 했다.
실측 2026-09-26~10-10(수집 캡처 재생, #242 게이트): 같은 공식 해명자료가 52회
격리됐고, 끝내 격리된 107건 중 100건은 게이트가 원문 표기를 잘못 읽은 것이었다.

몇 번에서 끊나 — 같은 재생에서 연속 격리 횟수별로 그 뒤에 통과한 기사를 셌다.

=======  ==============  =====================
연속     그 뒤 통과       보류하면 아끼는 시도
=======  ==============  =====================
2회      7 / 25          295
4회      4 / 17          202
6회      0 / 12          144
=======  ==============  =====================

모델이 회차마다 조금씩 다르게 써서 우연히 통과하는 몫이 6회 전까지 남아 있다.
기사를 잃는 쪽이 호출보다 비싸므로 6회에서 끊는다.

보류는 판정이 아니라 **재시도 중단**이다. 기사를 내보내지도 지우지도 않고, 하루
요약에 걸린 구절과 함께 한 번 적는다. 보류를 풀면(항목을 지우면) 다음 수집이 다시
요약한다. 상태는 ``sent.json`` 의 ``integrity_holds`` 에 둔다 — 수집·브리핑 두
워크플로가 이미 커밋하는 파일이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

STATE_KEY = "integrity_holds"
HOLD_AFTER_RUNS = 6
# 공식 게시판 수집 창(7일)보다 길게 — 창 안에 있는 동안 보류가 풀려 다시 돌면 안 된다.
RETENTION_DAYS = 14
STATUS_RETRYING = "retrying"
STATUS_HELD = "held"


def _holds(state: dict) -> dict:
    holds = state.get(STATE_KEY)
    if not isinstance(holds, dict):
        holds = {}
        state[STATE_KEY] = holds
    return holds


def held_hashes(state: Mapping) -> set[str]:
    holds = state.get(STATE_KEY) if isinstance(state, Mapping) else None
    if not isinstance(holds, Mapping):
        return set()
    return {h for h, row in holds.items()
            if isinstance(row, Mapping) and row.get("status") == STATUS_HELD}


def record_run(state: dict, quarantined: Mapping[str, Mapping], passed: Iterable[str],
               *, now: datetime | None = None) -> list[dict]:
    """이번 회차 결과를 적고, 이번에 새로 보류된 항목을 돌려준다.

    ``quarantined`` 는 hash → {title, link, reason, concerns}. 통과한 기사는 연속이
    끊겼으므로 지운다. 같은 회차에 두 번 들어와도 한 번만 센다(호출부가 dict 로 준다).
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    holds = _holds(state)
    for h in passed:
        holds.pop(h, None)
    newly_held: list[dict] = []
    for h, info in quarantined.items():
        row = holds.get(h) if isinstance(holds.get(h), dict) else None
        if row is None:
            row = {"runs": 0, "first_at": stamp, "status": STATUS_RETRYING}
        row["runs"] = int(row.get("runs") or 0) + 1
        row["last_at"] = stamp
        for key in ("title", "link", "reason", "concerns"):
            if info.get(key):
                row[key] = info[key]
        if row.get("status") != STATUS_HELD and row["runs"] >= HOLD_AFTER_RUNS:
            row["status"] = STATUS_HELD
            row["held_at"] = stamp
            newly_held.append({"hash": h, **row})
        holds[h] = row
    return newly_held


def prune(state: dict, *, now: datetime | None = None) -> int:
    """오래된 항목을 지운다. 지운 개수."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    holds = _holds(state)
    stale = [h for h, row in holds.items()
             if not isinstance(row, Mapping) or str(row.get("last_at") or "") < cutoff]
    for h in stale:
        holds.pop(h, None)
    return len(stale)
