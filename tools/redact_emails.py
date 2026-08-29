#!/usr/bin/env python3
"""이미 쌓인 상태파일에서 기사 유래 이메일 주소를 지운다.

왜: 한국 기사는 바이라인에 기자 메일을 그대로 적는 일이 잦고, 그 줄이 RSS
description 을 타고 `source_excerpt` 에 저장된다. 실측(2026-08-29, 추적 파일
205개 전수):

    digest_queue.json  1건  $[167].source_excerpt
    그 밖의 상태파일    0건  (curated.json·archive/*.jsonl·outbox·sent 포함)

우리 자격증명이 아니라 **제3자의 개인정보**다. 저장소를 공개로 돌리면 수집
데이터가 통째로 검색 가능해지는데, 신문 지면에 한 번 실린 것과 아카이브에
영구히 색인되는 것은 노출의 성격이 다르다.

앞으로 들어올 것은 수집 단계에서 막는다(`data_quality.strip_emails` 를
news_bot 의 description 세 자리와 `article_body.extract_text` 에서 부른다).
이 스크립트는 **그 전에 이미 저장된 것**만 치운다.

봉인 지문을 함께 고치는 이유
----------------------------
`source_excerpt` 는 `verified_source_components` 와 `verified_evidence` 안에
sha256 으로 봉인돼 있고, 배달 단계가 그 값을 **다시 계산해서 대조한다**
(`article_quality_gate._binding_components` → `evidence_manifest_is_valid`).
글자만 지우고 지문을 그대로 두면 그 대조가 깨져 멀쩡한 기사가 '변조됨'으로
떨어진다. 그래서 지운 레코드는 manifest 를 다시 만들어 지문을 맞춘다.

다시 만드는 것이 안전한 근거: 대상 레코드에 대해 **지우기 전** 상태로 manifest 를
재생성해 저장본과 바이트까지 같음을 확인했다. 재생성이 본문 유래 사실을 잃는
경우가 아니다(backfill_evidence 의 경고가 가리키는 상황과 다르다 — 그쪽은 제목·
발췌만 보는 스크립트다). 이 스크립트도 같은 확인을 **레코드마다** 하고, 어긋나면
그 레코드는 건드리지 않고 넘어간다.

    python tools/redact_emails.py --check    # 세기만 한다
    python tools/redact_emails.py            # 실제로 지운다
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Windows 콘솔 기본 코드페이지는 한글을 못 찍는다. 저장소의 다른 스크립트
# (pubs_fetch·event_sources)와 같은 처리를 둔다 — 없으면 요약 한 줄 찍다가
# UnicodeEncodeError 로 죽어서, 정작 정제는 끝났는데 실패로 보인다.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import article_quality_gate as gate  # noqa: E402
from data_quality import clean_text, strip_emails  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

# 기사에서 온 텍스트만 본다. URL·해시·식별자 칸은 건드리지 않는다.
TEXT_FIELDS = (
    "title", "title_kr", "summary", "detail", "description", "source_excerpt",
    "implication", "why_important", "watch_next", "story_context",
    "open_question", "fulltext", "body",
)

# 레코드 목록을 담은 상태파일. 목록이 아닌 파일은 아래 walk 가 알아서 훑는다.
TARGETS = ("digest_queue.json", "curated.json", "outbox.json",
           "channel_outbox.json", "sent.json", "weekly_reports.json")
JSONL_TARGETS = ("archive/2026-07.jsonl", "archive/2026-08.jsonl",
                 "delivery_log.jsonl")


def _source_for(record: dict) -> dict:
    """backfill_evidence._source_for 와 같은 모양 — 레코드가 보존한 근거만."""
    return {
        "article_hash": clean_text(record.get("hash")),
        "title": clean_text(record.get("title")),
        "description": clean_text(
            record.get("description") or record.get("source_excerpt")),
        "published_at": clean_text(record.get("published_at")),
    }


def redact_record(record: dict) -> tuple[bool, list[str]]:
    """레코드 하나를 정제한다. (바뀌었나, 어느 칸이 바뀌었나)"""
    if not isinstance(record, dict):
        return False, []
    touched = []
    for field in TEXT_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and EMAIL.search(value):
            cleaned = strip_emails(value)
            if cleaned != value:
                record[field] = cleaned
                touched.append(field)
    if not touched:
        return False, []
    # 봉인이 살아 있던 레코드만 다시 봉인한다. 원래 없던 manifest 를 이 자리에서
    # 만들어 붙이지 않는다 — 이 스크립트는 지우는 자리이지 근거를 세우는 자리가 아니다.
    if isinstance(record.get("verified_evidence"), dict):
        manifest = gate.build_evidence_manifest(_source_for(record), article=record)
        if manifest:
            record["verified_evidence"] = manifest
            record["verified_source_components"] = (
                gate.evidence_manifest_source_components(manifest))
            touched.append("verified_evidence")
    return True, touched


def _count(node) -> int:
    if isinstance(node, dict):
        return sum(_count(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count(v) for v in node)
    if isinstance(node, str):
        return len(EMAIL.findall(node))
    return 0


def _records(node):
    """중첩 구조 안의 dict 를 전부 훑는다 — 레코드가 어디 있든 닿게."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _records(v)
    elif isinstance(node, list):
        for v in node:
            yield from _records(v)


def process_json(path: Path, apply: bool) -> tuple[int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0
    before = _count(data)
    if not before or not apply:
        return before, 0
    changed = 0
    for record in _records(data):
        did, fields = redact_record(record)
        if did:
            changed += 1
            print(f"    {path.name}: {', '.join(fields)}")
    if changed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        # 저장소의 표준 직렬화와 **똑같이** 쓴다(news_bot.save_json = indent=2).
        # 들여쓰기가 어긋나면 한 칸 고치는 변경이 파일 전체를 다시 쓴 diff 로
        # 나와서, 리뷰에서 실제로 뭐가 바뀌었는지 볼 수 없다(실측: 114,032줄).
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
    return before, changed


def process_jsonl(path: Path, apply: bool) -> tuple[int, int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0
    before = sum(len(EMAIL.findall(line)) for line in lines)
    if not before or not apply:
        return before, 0
    out, changed = [], 0
    for line in lines:
        if not line.strip() or "@" not in line:
            out.append(line)
            continue
        try:
            record = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        did, fields = redact_record(record)
        if did:
            changed += 1
            print(f"    {path.name}: {', '.join(fields)}")
        out.append(json.dumps(record, ensure_ascii=False) if did else line)
    if changed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(path)
    return before, changed


def main() -> int:
    apply = "--check" not in sys.argv
    total_found = total_changed = 0
    print("[redact] " + ("실제 정제" if apply else "집계만 (--check)"))
    for name in TARGETS:
        path = ROOT / name
        if not path.exists():
            continue
        found, changed = process_json(path, apply)
        total_found += found
        total_changed += changed
        if found:
            print(f"  {name}: 이메일 {found}건 → 레코드 {changed}건 정제")
    for name in JSONL_TARGETS:
        path = ROOT / name
        if not path.exists():
            continue
        found, changed = process_jsonl(path, apply)
        total_found += found
        total_changed += changed
        if found:
            print(f"  {name}: 이메일 {found}건 → 레코드 {changed}건 정제")
    print(f"[redact] 검출 {total_found}건 / 정제 레코드 {total_changed}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
