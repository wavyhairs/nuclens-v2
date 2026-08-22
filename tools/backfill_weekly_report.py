"""저장된 주간 리포트 하나를 지금 로직으로 다시 만든다 — **발송 없이 저장만**.

왜 있는가
    주간 판세는 금요일 스케줄에서 한 번 계산되고, 그때 나온 것이 그대로
    `weekly_reports.json` 에 남아 웹 '주간 흐름'으로 간다. 그래서 판세 로직을
    고쳐도 **이미 저장된 주는 옛 결과 그대로다** — 다음 주가 올 때까지.
    (PR #53 로 사건 단위 검증이 들어간 뒤 2026-W34 가 정확히 그 상태였다:
    코드는 새것인데 화면은 policy 2 / theme 1 인 옛 산출물.)

    `weekly_bot.main()` 을 다시 돌리면 될 것 같지만 안 된다. 거기엔 텔레그램
    발송과 채널 공개가 붙어 있어, 지난 주 판세가 오늘 구독자에게 다시 나간다.
    이 스크립트는 같은 함수들을 **고정 기간**으로 부르고 저장만 한다.

    정기 실행 경로(`weekly_bot.main`)는 건드리지 않는다. 여기서 하는 일은
    "어느 기간의 기사를 넣을지"를 인자로 고정하는 것뿐이고, 기사 선별·합성·
    근거 검증·저장은 전부 `weekly_bot` 의 함수 그대로다.

쓰는 법
    python tools/backfill_weekly_report.py --start 2026-08-15 --end 2026-08-21
    python tools/backfill_weekly_report.py --start ... --end ... --dry-run

    기간은 **KST 달력 날짜**(양끝 포함)다. 주차 키·week_start·week_end 는
    `--end` 가 속한 ISO 주차로 정해지므로, 그 주의 마지막 날을 준다.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import article_quality_gate
import gemini_client
import weekly_bot
from weekly_bot import KST


def _window(start: str, end: str) -> tuple[datetime, datetime]:
    """KST 달력 날짜 두 개 → 그 기간의 시작·끝 시각(양끝 포함)."""
    first = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=KST)
    last = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=KST)
    if last < first:
        raise SystemExit(f"기간이 거꾸로다: {start} ~ {end}")
    return (datetime.combine(first.date(), time.min, tzinfo=KST),
            datetime.combine(last.date(), time.max, tzinfo=KST))


def _moment(value: object) -> datetime | None:
    """curated 의 시각 문자열 → aware datetime.

    published_at 은 KST 오프셋(+09:00), cached_at 은 UTC 로 저장된다. 정기 경로는
    문자열 비교로 어림잡지만(그쪽 동작은 건드리지 않는다), 고정 기간을 자를 때는
    오프셋을 실제로 해석해야 8/15 새벽과 8/21 밤이 경계에서 새지 않는다.
    """
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def window_articles(curated: dict, since: datetime, until: datetime) -> list[dict]:
    """고정 기간의 기사 — 선별 규칙은 `weekly_bot.get_week_articles` 그대로.

    등급·fallback·무결성 게이트를 여기서 다시 쓰지 않는다. 규칙이 두 벌이 되면
    한쪽만 고쳐지는 날이 오고, 그날 backfill 은 정기 실행과 다른 기사 집합 위에
    판세를 쓴다. 그래서 curated 를 기간으로 먼저 자른 뒤, 그 부분집합을 원래
    함수에 그대로 넘긴다. 함수 안의 '최근 7일' 컷오프가 기간을 다시 자르지
    않도록 WEEK_DAYS 만 잠시 넓힌다.
    """
    subset = {
        key: value for key, value in curated.items()
        if isinstance(value, dict)
        and (moment := _moment(value.get("published_at") or value.get("cached_at")))
        and since <= moment <= until
    }
    span = max((datetime.now(KST) - since).days + 2, weekly_bot.WEEK_DAYS)
    original = weekly_bot.WEEK_DAYS
    weekly_bot.WEEK_DAYS = span
    try:
        return weekly_bot.get_week_articles(subset)
    finally:
        weekly_bot.WEEK_DAYS = original


def synthesize_with_audit(items: list[dict], agg: dict) -> tuple[dict, dict]:
    """합성 결과와 **LLM 원본**을 함께 돌려준다.

    backfill 은 사람이 한 번 보고 커밋하는 작업이라, "무엇이 왜 빠졌는가"를
    보여 주지 못하면 검토할 수 없다. 정기 실행 경로는 건드리지 않고 여기서만
    호출을 감싸 원본을 붙잡는다.
    """
    raw: dict = {}
    real = gemini_client.call_json

    def capture(system, user, **kwargs):
        result = real(system, user, **kwargs)
        raw.update(copy.deepcopy(result))
        return result

    gemini_client.call_json = capture
    try:
        return weekly_bot.batch_synthesize(items, agg), raw
    finally:
        gemini_client.call_json = real


def _rows(payload: dict, key: str) -> list[dict]:
    return [row for row in (payload.get(key) or []) if isinstance(row, dict)]


def report_audit(raw: dict, final: dict, items: list[dict]) -> None:
    """원본 → 최종 사이에서 무엇이 빠졌고, 남은 것은 무엇을 근거로 서 있는가."""
    if not raw:
        return
    contracts = weekly_bot.weekly_contracts(items)
    everything = weekly_bot._unique_contracts(contracts)
    titles = {str(article.get("hash") or "")[:8]:
              (article.get("title_kr") or article.get("title") or "")
              for article in items}

    identity = {"policy_shifts": "what", "theme_moves": "theme",
                "key_events": "headline", "report_candidates": "topic"}
    for key, field in identity.items():
        before, after = _rows(raw, key), _rows(final, key)
        kept_ids = {str(row.get(field) or "") for row in after}
        print(f"\n[audit] {key}: {len(before)} → {len(after)}")
        for row in before:
            if str(row.get(field) or "") in kept_ids:
                continue
            reason = "문장이 지목한 근거와 어긋남"
            if key == "theme_moves":
                _kept, thin = weekly_bot.audit_theme_moves([row], contracts)
                if thin:
                    reason = (f"독립 사건 부족 (관련 사건 {thin[0]['stories']}건 · "
                              f"실제 진전 {thin[0]['events']}건)")
            else:
                _kept, findings = article_quality_gate.audit_evidence_items(
                    [row], contracts, text_fields=(field,),
                    analysis_fields=tuple(k for k in row if k not in
                                          {field, "evidence_hashes", "hash"}),
                    hash_field="hash" if key == "key_events" else "evidence_hashes",
                    require_evidence=key != "report_candidates",
                    fallback_contracts=everything)
                if findings:
                    detail = {k: v for k, v in findings[0].details.items()
                              if k != "text"}
                    reason = f"{findings[0].code} {json.dumps(detail, ensure_ascii=False)}"
            print(f"  - 제외: {str(row.get(field) or '')[:60]}")
            print(f"      사유: {reason}")
        for row in after:
            cited = [str(h)[:8] for h in (row.get("evidence_hashes")
                                          or ([row.get("hash")] if row.get("hash") else []))]
            print(f"  + 유지: {str(row.get(field) or '')[:60]}")
            for short in cited:
                mark = "○" if short in contracts else "✗"
                print(f"      {mark} {short} {titles.get(short, '(이번 주 기사 아님)')[:60]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="KST 시작일 (YYYY-MM-DD, 포함)")
    parser.add_argument("--end", required=True, help="KST 종료일 (YYYY-MM-DD, 포함)")
    parser.add_argument("--dry-run", action="store_true",
                        help="합성까지만 하고 저장하지 않는다")
    args = parser.parse_args()

    since, until = _window(args.start, args.end)
    # 합성은 GEMINI_API_KEY 를 환경에서 본다. 로컬 실행에서는 .env 를 읽는 쪽이
    # gemini_client 뿐이라, 그 값을 환경으로 올려 준다 (CI 는 이미 환경에 있다).
    if not os.environ.get("GEMINI_API_KEY") and gemini_client.API_KEY:
        os.environ["GEMINI_API_KEY"] = gemini_client.API_KEY

    items = window_articles(weekly_bot.load_curated(), since, until)
    if not items:
        raise SystemExit(f"{args.start}~{args.end} 기사 0건 — 중단한다")

    stories = weekly_bot.weekly_stories(items)
    print(f"[backfill] {args.start}~{args.end}: 기사 {len(items)}건 → 사건 {len(stories)}건")

    agg = weekly_bot.build_aggregates(items)
    synthesis, raw = synthesize_with_audit(items, agg)
    print(f"[backfill] policy {len(synthesis['policy_shifts'])} · "
          f"theme {len(synthesis['theme_moves'])} · "
          f"key_events {len(synthesis['key_events'])} · "
          f"watchpoints {len(synthesis['watchpoints'])} · "
          f"report_candidates {len(synthesis['report_candidates'])}")
    if not synthesis["policy_shifts"] and not synthesis["theme_moves"]:
        raise SystemExit("합성 결과가 비었다 — 저장하지 않는다 (키·쿼터 확인)")
    report_audit(raw, synthesis, items)

    if args.dry_run:
        print(json.dumps(synthesis, ensure_ascii=False, indent=2))
        print("[backfill] --dry-run — 저장하지 않았다")
        return

    # 주차 키·week_start·week_end 는 `now` 에서 나온다. 기간 마지막 날을 주어야
    # 그 주의 리포트를 덮어쓴다 (오늘 날짜로 부르면 다음 주차가 새로 생긴다).
    saved = weekly_bot.save_weekly_report(synthesis, agg, items, now=until)
    if not saved:
        print("[backfill] 내용이 기존과 같아 저장하지 않았다")
        return

    # `now` 를 기간 끝으로 준 대가로 generated_at 이 그 주 금요일로 적힌다.
    # 언제 만든 산출물인지는 이 파일에서 유일하게 그 필드만 말하므로, 저장 뒤에
    # 실제 시각으로 바로잡는다. 내용 비교에서 빠지는 필드라 저장 판정에는
    # 영향이 없다.
    path = weekly_bot.WEEKLY_REPORTS_FILE
    store = json.loads(path.read_text(encoding="utf-8"))
    key = weekly_bot.week_id(until)
    store["reports"][key]["generated_at"] = datetime.now(KST).isoformat()
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"[backfill] {key} 저장 완료 — 발송은 하지 않았다")


if __name__ == "__main__":
    main()
