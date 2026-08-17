"""배포를 막지 않는 데이터 품질 계측 — 지표 게이트를 '측정'으로 되돌린다.

지표 게이트 두 개(추적률·주별 합계 비율)는 배포 경로에서 꺼져 있다. 옳은
판단이다 — 뉴스가 한산하거나 한 주에 몰린 것만으로 CSS 오타 수정까지 배포가
막히기 때문이다(2026-08-03, 2026-08-11 실사고, `NUCLENS_SKIP_DATA_GATES`).

문제는 끄고 나니 **아무 데서도 재지 않게 됐다**는 것이다:

    deploy-web    NUCLENS_SKIP_DATA_GATES=1 로 건너뛴다
    crawl         웹 테스트를 아예 돌리지 않는다
    daily-brief   웹 테스트를 아예 돌리지 않는다

로컬에서 누가 직접 실행할 때만 켜진다. 2026-08-15 에 그 대가를 치렀다. 주별
합계 비율이 2.02 로 넘어가 흐름 탭의 주제 표와 슬로프 그래프가 통째로 사라졌는데,
바로 그것을 감시하라고 만든 `test_live_data_weeks_are_within_the_front_end_gate`
가 CI 어디에도 안 걸려 있어서 화면에서 눈으로 발견될 때까지 아무도 몰랐다.

그래서 **조치를 발견에 맞춘다.** 배포를 막는 대신 매일 한 번 재서 delivery_log 에
남긴다. 임계값 미달은 워크플로 경고(`::warning::`)로 뜨지만 종료 코드는 0이다.
다만 빌드 산출물이 없어 **측정 기록 자체를 만들지 못한 경우**에는 1을 반환한다.
워크플로가 이를 비치명 step outcome으로 받아 관리자에게 알리기 위한 신호이며,
기사나 배포 품질의 임계값을 이유로 배포를 막는 게이트는 아니다.

임계값은 테스트와 같은 값을 쓰되 통과/실패를 판정하지 않고 기록만 한다. 값을
낮춰 '통과'시키는 유혹이 없도록, 애초에 통과라는 개념을 두지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
WEB_DATA = ROOT / "web" / "public" / "data"
DELIVERY_LOG = ROOT / "delivery_log.jsonl"
KST = timezone(timedelta(hours=9))

sys.path.insert(0, str(ROOT / "web"))
import build_data  # noqa: E402  — 주별 집계 규칙을 재구현하지 않고 그대로 쓴다

# 아래 셋은 web/public/app.js 의 같은 이름 상수를 옮긴 것이다. 여기서 재는 것은
# "데이터가 나쁜가"가 아니라 **"화면이 입을 다무는가"** 라서, 값이 어긋나면
# 계측이 화면과 다른 이야기를 하게 된다. tests/test_data_gate_metrics.py 가
# app.js 를 읽어 대조한다.
TOPIC_WEEK_SAMPLE_RATIO = 2.0
TOPIC_FLOW_MIN_WEEKS = 3
TOPIC_FLOW_MAX_WEEKS = 4
# test_tracking_rate_meets_target 과 같은 값. 내려서 통과시키지 말 것 —
# 올려야 할 것은 지표지 기준선이 아니다.
TRACKING_RATE_TARGET = 0.20

RECORD_TYPE = "data_quality_gate"


def _load(name: str):
    try:
        return json.loads((WEB_DATA / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def measure_tracking(meta: dict) -> dict:
    """추적률. 최신 브리핑 하나가 아니라 최근 7회차 누적이다(build_data 가 계산).

    로컬 폴백 벡터 빌드는 병합이 구조적으로 보수적이라 값이 낮게 나온다 —
    환경 차이지 코드 결함이 아니므로 `applicable` 로 구분해 남긴다.
    """
    window = meta.get("tracking_window_briefings") or 0
    applicable = bool(meta.get("remote_embedding_selected_count")) and \
        window >= build_data.TRACKING_WINDOW_BRIEFINGS
    rate = meta.get("tracking_window_rate")
    return {
        "applicable": applicable,
        "rate": rate,
        "target": TRACKING_RATE_TARGET,
        "below_target": bool(applicable and rate is not None and rate < TRACKING_RATE_TARGET),
        "window_briefings": window,
        "issue_count": meta.get("tracking_window_issue_count"),
        "tracked_issue_count": meta.get("tracking_window_tracked_issue_count"),
        "remote_embeddings": bool(meta.get("remote_embedding_selected_count")),
    }


def measure_topic_weeks(catalog: list[dict], briefings: list[dict]) -> dict:
    """주별 이슈 합계의 기울기. 이 값이 2.0 을 넘으면 화면이 방향을 말하지 않는다.

    화면은 두 곳에서 이 게이트를 쓴다 — 주제 흐름 표는 최근 3~4주 전체를,
    슬로프 그래프는 마지막 2주만 본다. 한쪽만 막히는 날이 실제로 있으므로
    (2026-08-15: 3주 2.04 차단 / 2주 2.00 통과) 둘 다 남긴다.
    """
    weeks, series = build_data.build_topic_weeks(
        catalog, [row.get("date") for row in briefings if row.get("date")])
    totals = [sum(values[i] for values in series.values()) for i in range(len(weeks))]

    def ratio(window: list[int]) -> float | None:
        if len(window) < 2 or min(window) <= 0:
            return None
        return round(max(window) / min(window), 4)

    # 화면의 topicFlowSpan() 과 같은 규칙 — 온전한 주가 3개 미만이면 표 자체를
    # 내리므로 비율을 잴 대상이 없다.
    span = 0 if len(totals) < TOPIC_FLOW_MIN_WEEKS else min(TOPIC_FLOW_MAX_WEEKS, len(totals))
    flow = ratio(totals[-span:]) if span else None
    slope = ratio(totals[-2:])
    return {
        "weeks": weeks,
        "totals": totals,
        "limit": TOPIC_WEEK_SAMPLE_RATIO,
        "flow_ratio": flow,
        "slope_ratio": slope,
        # 화면에서 실제로 보이는가. 이것이 이 지표의 존재 이유다.
        "flow_visible": bool(flow is not None and flow <= TOPIC_WEEK_SAMPLE_RATIO),
        "slope_visible": bool(slope is not None and slope <= TOPIC_WEEK_SAMPLE_RATIO),
    }


def build_record(now: datetime | None = None) -> dict | None:
    meta = _load("meta.json")
    catalog = _load("issues.json")
    briefings = _load("briefings.json")
    if not isinstance(meta, dict) or not isinstance(catalog, list) or not isinstance(briefings, list):
        print("[data-gate] 빌드 산출물 없음 — build_data.py 이후에 실행돼야 한다")
        return None
    now = now or datetime.now(timezone.utc)
    workflow_run_id = str(os.environ.get("GITHUB_RUN_ID") or "").strip()
    return {
        "record_type": RECORD_TYPE,
        "date": now.astimezone(KST).date().isoformat(),
        "generated_at": now.astimezone(KST).isoformat(),
        # 같은 workflow run을 재시도해도 하나의 관측으로 계산한다. 로컬 실행은
        # generated_at을 쓰는 기존 동작으로 자연스럽게 돌아간다.
        "observation_id": (f"github-run:{workflow_run_id}" if workflow_run_id
                           else now.astimezone(KST).isoformat()),
        "tracking": measure_tracking(meta),
        "topic_weeks": measure_topic_weeks(catalog, briefings),
        "archive_quality": meta.get("archive_quality") or {},
    }


def append(record: dict, path: Path | None = None) -> None:
    path = path or DELIVERY_LOG
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def report(record: dict) -> None:
    """사람이 읽는 요약 + 미달 시 워크플로 경고. 종료 코드는 바꾸지 않는다."""
    tracking = record["tracking"]
    weeks = record["topic_weeks"]
    archive_quality = record.get("archive_quality") or {}
    if tracking["applicable"]:
        print(f"[data-gate] 추적률 {tracking['rate']} "
              f"({tracking['tracked_issue_count']}/{tracking['issue_count']}, "
              f"최근 {tracking['window_briefings']}회차) 기준 {tracking['target']}")
        if tracking["below_target"]:
            print(f"::warning::추적률 {tracking['rate']} < {tracking['target']} — "
                  f"병합 판정기를 봐야 한다 (배포는 계속한다)")
    else:
        reason = "원격 임베딩 없음" if not tracking["remote_embeddings"] else "브리핑 회차 부족"
        print(f"[data-gate] 추적률 측정 대상 아님 — {reason}")

    print(f"[data-gate] 주별 합계 {weeks['totals']} "
          f"(흐름 {weeks['flow_ratio']} / 슬로프 {weeks['slope_ratio']}, 상한 {weeks['limit']})")
    for key, label in (("flow_visible", "주제 흐름 표"), ("slope_visible", "슬로프 그래프")):
        if not weeks[key]:
            print(f"::warning::{label}가 화면에서 숨는다 — 주별 모수가 기울었다 "
                  f"{weeks['totals']} (배포는 계속한다)")
    if archive_quality.get("quarantined"):
        print(f"::warning::원문과 다른 아카이브 기사 {archive_quality['quarantined']}건을 "
              "웹 출력에서 격리했다 (배포는 계속한다)")
    if archive_quality.get("sanitized"):
        print(f"::warning::아카이브 기사 {archive_quality['sanitized']}건의 잘못된 사건일 등 "
              "무결성 필드를 정제했다 (배포는 계속한다)")


def main() -> int:
    record = build_record()
    if record is None:
        # 데이터 기준 미달이 아니라 계측 실행 실패다. GitHub step은
        # continue-on-error로 배포를 계속하고, outcome은 운영 알림에 전달한다.
        return 1
    append(record)
    report(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
