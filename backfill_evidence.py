"""과거 데이터를 **실제 근거가 있는 만큼만** 현행 근거 계약으로 올린다.

문제: curated.json·digest_queue.json·archive/*.jsonl 에 쌓인 기록은 PR #27 이
도입한 계약(published_at, 근거 manifest v2, 명시적 curation_status) 이전에
만들어졌다. 실측 2026-08-17: curated 3,581건·queue 181건·archive 4,080건 중
manifest 를 가진 것이 하나도 없다. 그래서 오디오·주간 서사가 옛 기록을 근거로
쓸 때는 제목 문자열 말고 기댈 것이 없고, 검증에서 지운 사건일이 다른 소비자
쪽에서 원래 값 그대로 살아 있다.

이 스크립트가 하는 일과 **하지 않는 일**:

  * 한다 — 아카이브에 남아 있는 원문 게시시각(`pub`)으로 published_at 을 복구,
    원문 제목과 그 시각으로 manifest v2 를 봉인, 근거로 확인되지 않는 사건일을
    비우고, 원문과 어긋나는 레코드를 quarantined 로 표시한다.
  * 하지 않는다 — 근거 없이 reviewed/verified 로 올리지 않는다. 발행시각을 모를
    때 현재시각이나 cached_at 을 발행시각인 척 적지 않는다(fallback 을 쓰고
    있다는 사실만 남긴다). 사건일을 추정하지 않는다. 원문 본문을 저장하지 않는다.

기본 실행은 dry-run 이다. 무엇이 몇 건 왜 바뀌는지 먼저 보고, `--apply` 는
그것을 그대로 쓴다. 두 번 돌려도 두 번째는 0건이어야 한다(멱등).

  python backfill_evidence.py                     # 전수 진단 (쓰기 없음)
  python backfill_evidence.py --samples 5         # 사유별 샘플까지
  python backfill_evidence.py --apply             # 실제 반영
  python backfill_evidence.py --targets curated   # 대상 한정
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import article_quality_gate as gate
from data_quality import clean_text

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).parent
CURATED_FILE = ROOT / "curated.json"
QUEUE_FILE = ROOT / "digest_queue.json"
ARCHIVE_DIR = ROOT / "archive"
TARGETS = ("curated", "queue", "archive")

# 발행시각을 모를 때 그 자리에 쓰이던 필드. 이 이름을 남겨 두면 "발행시각이
# 있다"와 "수집시각을 발행시각처럼 쓰고 있다"를 사후에 구분할 수 있다.
FALLBACK_TIME_FIELDS = ("queued_at", "cached_at", "archived_at")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def load_archive_lines() -> list[tuple[Path, dict | None, str]]:
    """(파일, 레코드 또는 None, 원본 줄).

    깨진 줄도 **원문 그대로 들고 간다.** 파싱된 것만 다시 쓰면 읽지 못한 줄이
    조용히 사라진다 — 이 작업에서 절대 하면 안 되는 것이 기록을 잃는 것이다.
    """
    rows: list[tuple[Path, dict | None, str]] = []
    for path in sorted(ARCHIVE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append((path, json.loads(line), line))
            except json.JSONDecodeError:
                rows.append((path, None, line))
    return rows


def utc_iso(value: object, *, now: datetime | None = None) -> str:
    """수집이 manifest 를 묶을 때 쓰는 것과 **같은 모양**의 UTC ISO 문자열.

    아카이브는 `pub` 를 원래 시간대 그대로 남긴다("2026-08-07T06:50:00+09:00").
    수집은 UTC 로 정규화한 값에 manifest 를 묶는다("2026-08-06T21:50:00+00:00").
    같은 순간이지만 문자열이 달라, 원문 형태로 되살리면 결속이 어긋나 멀쩡한
    manifest 가 통째로 다시 만들어진다 — 그러면 본문 유래 사실이 사라진다
    (실측 2026-08-17: 그렇게 curated 근거 4,657 → 3,708 로 949개가 날아갔다).

    news_bot.normalize_publication_timestamp 와 같은 계약이다. 그 모듈을
    임포트하지 않는 이유는 수집 모듈이 토큰 없는 환경에서 종료하기 때문이고,
    그래서 여기서는 같은 규칙을 stdlib 로만 다시 쓴다. 미래 값도 같이 버린다.
    """
    text = clean_text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return "" if parsed > reference else parsed.isoformat()


def archive_publication_times(rows, *, now: datetime | None = None) -> dict[str, str]:
    """hash → 수집 당시 피드가 준 게시시각 (UTC 정규화).

    이것이 이 백필의 유일한 '진짜' 발행시각 근거다. curated/queue 는 이 값을
    버렸지만 아카이브는 `pub` 에 남겨 두었다.
    """
    found: dict[str, str] = {}
    for _path, record, _raw in rows:
        if record is None:
            continue
        article_hash = clean_text(record.get("hash"))
        published_at = utc_iso(record.get("pub"), now=now)
        if article_hash and published_at and article_hash not in found:
            found[article_hash] = published_at
    return found


def _source_for(record: dict) -> dict:
    """레코드가 실제로 보존하고 있는 원문 근거만 모은다."""
    return {
        "article_hash": clean_text(record.get("hash")),
        "title": clean_text(record.get("title")),
        "description": clean_text(
            record.get("description") or record.get("source_excerpt")),
        "published_at": clean_text(record.get("published_at")),
    }


def _fallback_time_field(record: dict) -> str:
    for field in FALLBACK_TIME_FIELDS:
        if clean_text(record.get(field)):
            return field
    return ""


def plan_record(record: dict, *, published_at: str = "",
                article_hash: str = "") -> tuple[dict, list[str]]:
    """한 레코드의 백필 결과와 사유 목록. 입력은 건드리지 않는다."""
    updated = dict(record)
    reasons: list[str] = []
    # curated.json 은 hash 를 키로만 갖는다. manifest 의 출처 결속은 레코드 안의
    # hash 를 보므로, 키에 있는 값을 레코드에도 적어 둬야 나중에 검증이 선다.
    #
    # 사유를 반드시 남긴다. 이것만 고치면 되는 레코드(재큐레이션이 hash 만 지운
    # 경우)가 있는데, 사유가 없으면 report.changed 가 0 이라 파일이 아예 안 써진다.
    if article_hash and not clean_text(updated.get("hash")):
        updated["hash"] = article_hash
        reasons.append("hash_restored")

    # ① 발행시각 — 아카이브에 남은 값만 쓴다. 없으면 지어내지 않고, 지금 무엇을
    #    대신 보고 있는지만 적는다.
    if not clean_text(updated.get("published_at")):
        if published_at:
            updated["published_at"] = published_at
            updated["published_at_source"] = "archive_pub"
            reasons.append("published_at_recovered")
        else:
            field = _fallback_time_field(updated)
            if field and updated.get("published_at_fallback") != field:
                updated["published_at_fallback"] = field
                reasons.append("published_at_unavailable")

    # ② 사건일 — 제목·설명 같은 **보존된** 근거로 확인되는 것만 남는다.
    #    본문이 사라진 뒤에 '본문에서 봤다'고 적힌 날짜는 아무도 확인할 수 없다.
    source = _source_for(updated)
    integrity = gate.audit_article_integrity(
        updated, source=source,
        reference_date=source["published_at"] or updated.get("cached_at")
        or updated.get("archived_at") or updated.get("queued_at"),
    )
    for finding in integrity.findings:
        if finding.field == "event_date":
            reasons.append(f"event_date_cleared:{finding.code}")
    if "event_date" in integrity.removed_fields:
        for key, value in (("event_date", None), ("event_date_type", "unknown"),
                           ("event_date_precision", "unknown"),
                           ("event_date_source", "unknown")):
            updated[key] = value

    # ③ 원문과 어긋나는 레코드는 승격이 아니라 격리다. 다른 상태는 손대지
    #    않는다 — 근거 없이 reviewed 로 올리는 것이 이 작업의 금지 사항이다.
    if not integrity.eligible:
        codes = ",".join(sorted(
            f.code for f in integrity.findings if f.severity == "quarantine"))
        if clean_text(updated.get("curation_status")) != "quarantined":
            updated["curation_status"] = "quarantined"
            updated["quarantine_reason"] = codes
            reasons.append(f"quarantined:{codes}")
        return updated, reasons

    # ④ 근거 manifest — 원문 제목(+ 복구된 발행시각)이 있을 때만 만든다.
    #    본문은 저장하지 않으므로 manifest 도 제목이 말한 것까지만 봉인한다.
    #
    #    이미 결속이 살아 있는 manifest 는 손대지 않는다. 수집은 본문까지 보고
    #    manifest 를 만들지만 이 스크립트는 제목·발췌만 본다 — 다시 만들면 본문
    #    유래 사실이 조용히 사라진다(실측 2026-08-17: queue 25건·curated 64건이
    #    줄고, 최악은 73개 사실 → 5개). 이 스크립트는 빈자리를 메우는 자리지
    #    남의 근거를 다시 쓰는 자리가 아니다.
    if gate.evidence_manifest_is_valid(updated.get("verified_evidence"),
                                       article=updated):
        return updated, reasons
    manifest = gate.build_evidence_manifest(_source_for(updated), article=updated)
    if manifest:
        components = gate.evidence_manifest_source_components(manifest)
        if (updated.get("verified_evidence") != manifest
                or updated.get("verified_source_components") != components):
            updated["verified_evidence"] = manifest
            updated["verified_source_components"] = components
            reasons.append("manifest_backfilled")
    elif not clean_text(updated.get("title")):
        reasons.append("manifest_skipped_no_title")
    return updated, reasons


def _sample(before: dict, after: dict) -> dict:
    """샘플은 **바뀌기 전** 값을 보여 준다 — 비운 뒤의 null 만 보면 사유를 못 읽는다."""
    return {
        "hash": clean_text(before.get("hash") or after.get("hash"))[:12],
        "title": clean_text(before.get("title"))[:60],
        "event_date": before.get("event_date"),
        "event_date_source": before.get("event_date_source"),
        "published_at": after.get("published_at") or before.get("published_at") or "",
    }


class Report:
    """대상별 변경 예정 건수·사유·샘플."""

    def __init__(self, name: str, total: int):
        self.name = name
        self.total = total
        self.changed = 0
        self.reasons: Counter = Counter()
        self.samples: dict[str, list[dict]] = {}

    def add(self, before: dict, after: dict, reasons: list[str], *,
            samples: int) -> None:
        if not reasons:
            return
        self.changed += 1
        for reason in reasons:
            self.reasons[reason] += 1
            bucket = self.samples.setdefault(reason, [])
            if len(bucket) < samples:
                bucket.append(_sample(before, after))

    def as_dict(self) -> dict:
        return {"target": self.name, "total": self.total, "changed": self.changed,
                "reasons": dict(self.reasons.most_common())}

    def render(self, samples: int) -> str:
        lines = [f"[{self.name}] {self.total}건 중 {self.changed}건 변경 예정"]
        for reason, count in self.reasons.most_common():
            lines.append(f"  - {reason}: {count}건")
            for row in self.samples.get(reason, [])[:samples]:
                lines.append(f"      {json.dumps(row, ensure_ascii=False)}")
        return "\n".join(lines)


def run_curated(published, *, apply: bool, samples: int) -> Report:
    curated = _load_json(CURATED_FILE, {})
    report = Report("curated", len(curated))
    updated: dict[str, dict] = {}
    for article_hash, record in curated.items():
        if not isinstance(record, dict):
            updated[article_hash] = record
            continue
        result, reasons = plan_record(
            record, published_at=published.get(article_hash, ""),
            article_hash=article_hash)
        report.add(record, result, reasons, samples=samples)
        updated[article_hash] = result
    if apply and report.changed:
        _write_json(CURATED_FILE, updated)
    return report


def run_queue(published, *, apply: bool, samples: int) -> Report:
    rows = _load_json(QUEUE_FILE, [])
    if not isinstance(rows, list):
        return Report("queue", 0)
    report = Report("queue", len(rows))
    updated: list[dict] = []
    for record in rows:
        if not isinstance(record, dict):
            updated.append(record)
            continue
        result, reasons = plan_record(
            record, published_at=published.get(clean_text(record.get("hash")), ""))
        report.add(record, result, reasons, samples=samples)
        updated.append(result)
    if apply and report.changed:
        _write_json(QUEUE_FILE, updated)
    return report


def run_archive(rows, published, *, apply: bool, samples: int) -> Report:
    report = Report("archive", sum(1 for _p, record, _r in rows if record is not None))
    by_file: dict[Path, list[str]] = {}
    for path, record, raw in rows:
        if record is None:
            report.reasons["unparsable_line_preserved"] += 1
            by_file.setdefault(path, []).append(raw)
            continue
        # 아카이브의 `pub` 이 곧 그 발행시각이다. published_at 계약으로만 옮긴다.
        result, reasons = plan_record(
            record, published_at=published.get(clean_text(record.get("hash")), ""))
        report.add(record, result, reasons, samples=samples)
        by_file.setdefault(path, []).append(json.dumps(result, ensure_ascii=False))
    if apply and report.changed:
        for path, lines in by_file.items():
            _write_lines(path, lines)
    return report


def _write_json(path: Path, payload) -> None:
    """원자적 교체 — 중간에 죽어도 반쪽 파일이 남지 않는다.

    들여쓰기는 이 파일을 평소에 쓰는 쪽(news_bot.save_json ·
    daily_brief.save_queue)과 같은 2 를 쓴다. 다르게 쓰면 백필이 파일 전체를
    다시 포맷하고, 다음 크롤이 도로 되돌린다 — 'git 이 DB' 인 저장소에서
    내용이 아닌 서식으로 매 회차 수만 줄짜리 커밋이 생긴다.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def _write_lines(path: Path, lines: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    tmp.replace(path)


def delivery_impact(rows) -> dict:
    """백필이 정상 기사를 대량으로 못 나가게 만들지 않는지 본다.

    '검증을 켰더니 그날 브리핑이 비었다'는 이 작업에서 가장 비싼 실패다.
    적용 전후로 발송 가능 판정을 세어 그 사고를 미리 잡는다.
    """
    before = Counter()
    after = Counter()
    for record in rows:
        before[gate.assess_delivery_eligibility(record).action] += 1
        planned, _reasons = plan_record(record)
        after[gate.assess_delivery_eligibility(planned).action] += 1
    return {"before": dict(before), "after": dict(after)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="실제로 파일을 고친다 (기본은 dry-run)")
    parser.add_argument("--targets", default=",".join(TARGETS),
                        help=f"대상 ({'|'.join(TARGETS)}), 쉼표 구분")
    parser.add_argument("--samples", type=int, default=2,
                        help="사유별로 보여 줄 샘플 수")
    args = parser.parse_args(argv)
    wanted = [t.strip() for t in args.targets.split(",") if t.strip() in TARGETS]

    archive_rows = load_archive_lines()
    published = archive_publication_times(archive_rows)
    mode = "적용" if args.apply else "dry-run"
    print(f"[backfill] {mode} · 아카이브 {len(archive_rows)}행 중 "
          f"발행시각 근거 {len(published)}건 확보")

    reports = []
    if "curated" in wanted:
        reports.append(run_curated(published, apply=args.apply, samples=args.samples))
    if "queue" in wanted:
        reports.append(run_queue(published, apply=args.apply, samples=args.samples))
    if "archive" in wanted:
        reports.append(run_archive(archive_rows, published,
                                   apply=args.apply, samples=args.samples))
    for report in reports:
        print(report.render(args.samples))

    queue_rows = [r for r in _load_json(QUEUE_FILE, []) if isinstance(r, dict)]
    if queue_rows:
        print(f"[backfill] 현재 큐 발송 판정: "
              f"{json.dumps(delivery_impact(queue_rows), ensure_ascii=False)}")
    print(f"[backfill] 요약: {json.dumps([r.as_dict() for r in reports], ensure_ascii=False)}")
    if not args.apply:
        print("[backfill] dry-run 이라 아무 파일도 쓰지 않았다. 반영하려면 --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
