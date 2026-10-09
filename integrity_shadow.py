"""원문 대조 게이트가 격리한 기사를 원문을 읽는 검사기에 한 번 더 묻는다 — **그림자 모드**.

왜 (2026-10-10 실측)
--------------------
``article_quality_gate.audit_article_integrity`` 는 원문과 출력에서 수치·엔티티를
정규식과 등록부로 읽어 대조한다. 원문에서 **못 읽은** 것을 원문에 **없는** 것으로
보고, 재생성 뒤에도 그러면 기사를 뺀다. 9/26~10/10 수집 캡처를 #242 게이트로
재생하면 끝내 격리된 기사가 107건이었는데, 하나씩 판정하니

* 게이트 오탐 100 — `1천570억` 을 570억으로, `900㎿` 를 못 읽음, `한전원자력연료` 를
  한국전력으로, 대만 원자력안전위원회를 한국 원안위로, `FEED 1` 을 1호기로 …
* 원문이 빈약해 모델이 원문 밖 사실을 쓴 것 6 · 진짜 모델 오류 1

표기 변형의 꼬리는 끝이 없어서(#209·#242 두 번 메우고도 이 비율이다) 파서를
메우는 것만으로는 수렴하지 않는다. 판정은 원문을 읽는 쪽이 해야 한다.

무엇을
------
끝내 격리된 기사마다 마지막 출력과 원문을 ``summary_verify.verify`` 에 넘긴다 —
기사 1건씩, 원문 인용을 원문에 대 보는 그 검사기다(별도 버킷·상한·429/402 즉시
중단이 이미 있다). 판정은 ``integrity_shadow.jsonl`` 에만 남기고 **격리·보류·발송은
하나도 바꾸지 않는다.** 1주 기록을 사람이 본 뒤(``tools/integrity_shadow_report.py``)
판정을 격리에 반영할지 정한다 — ``summary_verify`` 를 경고 모드로 들인 것과 같은 길.

판정 → 반영했다면 할 일(지금은 기록만):

* ok              → would_release (게이트 오탐으로 보고 내보낸다)
* contradiction   → would_repair  (검사기가 인용한 원문 구절로 재생성)
* unsupported     → would_strip   (원문에 없는 구절을 빼라고 재생성)
* stale·그 밖     → uncertain
* 호출 못 함      → no_verdict    (지금처럼 격리)
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Mapping

from data_quality import clean_text
import summary_verify

ROOT = Path(__file__).parent
LOG_FILE = Path(os.environ.get("INTEGRITY_SHADOW_FILE") or (ROOT / "integrity_shadow.jsonl"))

# 요청 수 상한. 9/26~10/10 재생에서 끝내 격리는 하루 22회 안팎(같은 출력은 다시 묻지
# 않으므로 실제 호출은 더 적다). 검사기 버킷은 summary_verify 250 + 다른 작업 64~76
# 이라 60 을 더해도 하루 500 안에 든다.
DEFAULT_DAILY_CAP = 60
DEFAULT_RUN_CAP = 20
# 원문 요약(description)만 있을 때 넘기는 길이. 수집 단계 description 과 같은 자름.
_DESCRIPTION_CHARS = 600

DECISIONS = {
    "ok": "would_release",
    "contradiction": "would_repair",
    "unsupported": "would_strip",
}
DECISION_LABELS = {
    "would_release": "원문과 맞음",
    "would_repair": "원문과 다름",
    "would_strip": "원문에 없음",
    "uncertain": "판정 불확실",
    "no_verdict": "판정 못 받음",
}


def enabled() -> bool:
    return os.environ.get("INTEGRITY_SHADOW", "on").strip().lower() not in {
        "0", "off", "false", "no"}


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def targets(quarantines: Mapping[str, Mapping], articles: Mapping[str, Mapping],
            bodies: Mapping[str, str]) -> list[dict]:
    """격리 기록 → 검사 대상. 본문이 없으면 원문 요약으로, 그것도 없으면 제목만.

    ``quarantines`` 는 hash → {curation, codes, concerns, stage}.
    """
    out: list[dict] = []
    for h, row in quarantines.items():
        article = articles.get(h) or {}
        curation = row.get("curation") or {}
        if not isinstance(curation, Mapping):
            continue
        body = clean_text(bodies.get(h) or "")
        description = clean_text(article.get("description") or "")[:_DESCRIPTION_CHARS]
        source_kind = "body" if body else ("description" if description else "title")
        pub = article.get("pub")
        published = pub.date().isoformat() if isinstance(pub, datetime) else str(
            curation.get("published_at") or "")[:10]
        out.append({
            "hash": h, "title": article.get("title") or "", "body": body or description,
            "published": published,
            "title_kr": curation.get("title_kr") or "",
            "summary": curation.get("summary") or "",
            "detail": curation.get("detail") or "",
            "log_extra": {
                "source_kind": source_kind,
                "gate_stage": row.get("stage") or "",
                "gate_codes": list(row.get("codes") or ())[:6],
                "concerns": list(row.get("concerns") or ())[:4],
                "source_title": (article.get("title") or "")[:120],
            },
        })
    return out


def run(items: list[dict], *, client=None, now: datetime | None = None,
        path: Path = LOG_FILE) -> tuple[list[dict], dict]:
    return summary_verify.verify(
        items, client=client, now=now, path=path,
        day_cap=_int_env("INTEGRITY_SHADOW_DAILY_CAP", DEFAULT_DAILY_CAP),
        per_run_cap=_int_env("INTEGRITY_SHADOW_RUN_CAP", DEFAULT_RUN_CAP),
        row_extra={"stage": "integrity_shadow"},
    )


def decision(row: Mapping) -> str:
    if not row.get("called"):
        return "no_verdict"
    return DECISIONS.get(str(row.get("verdict") or ""), "uncertain")


def latest_decisions(hashes, path: Path = LOG_FILE) -> dict[str, str]:
    """hash → 가장 최근에 받은 판정. 같은 출력은 다시 묻지 않으므로 기록에서 읽는다."""
    wanted = set(hashes)
    found: dict[str, tuple[str, str]] = {}
    for row in summary_verify.load_log(path):
        h = row.get("hash")
        if h not in wanted or not row.get("called"):
            continue
        stamp = str(row.get("checked_at") or "")
        if h not in found or stamp >= found[h][0]:
            found[h] = (stamp, decision(row))
    return {h: value for h, (_stamp, value) in found.items()}


def report(rows: list[dict], stats: Mapping) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("called"):
            key = decision(row)
            counts[key] = counts.get(key, 0) + 1
    judged = " · ".join(f"{DECISION_LABELS[key]} {counts[key]}"
                        for key in DECISION_LABELS if counts.get(key))
    line = (f"[격리 재검·그림자] 대상 {stats.get('targets', 0)} · 판정 {stats.get('checked', 0)}"
            + (f" ({judged})" if judged else "")
            + f" · 같은 출력 재사용 {stats.get('cached', 0)}"
            + f" · 상한으로 못 봄 {stats.get('skipped_cap', 0)}")
    if stats.get("stopped"):
        line += f" · 중단 {stats['stopped']}"
    return [line]
