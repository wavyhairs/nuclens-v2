"""Resumable offline Gemini Gold evaluator; never touches production caches."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gemini_client

# ── 평가기 봉쇄 (P0) ────────────────────────────────────────────────────────
#
# 이 파일은 한때 task 마다 짧은 자체 판정 프롬프트를 들고 있었다. 그것은 어떤
# production 호출 경로도 아니었는데 정확도 숫자를 만들어 냈고, 그 숫자를 며칠
# 쌓은 뒤에야 평가기 자체가 틀렸다는 것을 알았다.
#
# 그래서 프롬프트 문자열을 지우는 것으로 끝내지 않는다 — 지운 문자열은 다시 적을
# 수 있다. **계약이 증명되지 않은 task 는 요청 자체를 만들 수 없게** 막는다.
# 다시 열려면 이 레지스트리에 production request builder 를 명시적으로 꽂아야
# 하고, 그건 fidelity 증명 없이는 통과하지 않는다.
class ContractNotProven(RuntimeError):
    """Raised when a task has no proven production call contract."""


@dataclass(frozen=True)
class TaskContract:
    """이 task 를 평가할 자격이 있는가.

    ``evaluator_policy`` 는 checkpoint namespace 이고 **필수**다. 이것이 없는
    결과 행은 다른(어쩌면 무효인) 평가기가 남긴 행과 구별되지 않는다. 그러면
    resume 로직이 현재 계약으로는 한 번도 돌지 않은 조합을 완료로 보고 건너뛴다.
    실제로 그 구멍이 있었다 — 무효 Identity 결과가 그대로 completed 였다.
    """

    task: str
    evaluator_policy: str | None = None
    request_builder: Callable[[dict], tuple[str, str, dict]] | None = None
    blocked_reason: str = ""

    def require(self) -> None:
        if self.request_builder is None or not self.evaluator_policy:
            raise ContractNotProven(
                f"{self.task}: {self.blocked_reason or 'no proven production contract'}")


# 현재 어떤 task 도 열려 있지 않다. P2(recorded-response fidelity)가 profile 별로
# 증명될 때마다 여기에 request_builder 와 evaluator_policy 를 함께 채운다.
TASKS: dict[str, TaskContract] = {
    task: TaskContract(task=task, blocked_reason=reason)
    for task, reason in (
        ("IDENTITY_REVIEW",
         "production replay (issue_review/keei_match/dedup) is not fidelity-proven; "
         "the removed title-pair judge was never a production call path"),
        ("CURATION",
         "existing Gold scores current_output, and production curate_batch runs "
         "BATCH_CHUNK-sized batches with regeneration/split/quarantine"),
        ("SEMANTIC",
         "semantic_verifier.verify sits behind a disabled gate; the active verifier "
         "is the Expert contract with score thresholds and critical claims"),
        ("SYNTHESIS", "no production contract and no task Gold"),
    )
}


def contract(task: str) -> TaskContract:
    try:
        return TASKS[task]
    except KeyError as exc:
        raise ContractNotProven(f"unknown evaluation task: {task}") from exc


def call_contract(task: str, case: dict) -> tuple[str, str, dict]:
    """production request 를 만들거나, 거부한다.

    폴백 분기를 일부러 두지 않는다. 계약이 증명되지 않은 task 는 API 에 닿는
    경로가 아예 없어야 한다.
    """
    entry = contract(task)
    entry.require()
    assert entry.request_builder is not None  # require() 가 좁혀 준다
    return entry.request_builder(case)


def result_key(model: str, config: str, fixture_id: str, repeat: int,
               evaluator_policy: str) -> str:
    if not evaluator_policy:
        raise ValueError("evaluator_policy is required for every result key")
    return f"{evaluator_policy}|{model}|{config}|{fixture_id}|{repeat}"


def completed_keys(rows: list[dict], evaluator_policy: str) -> set[str]:
    """**이 평가기 정책으로 만든** 행만 완료로 인정한다.

    옛 행은 불변 이력으로 파일에 그대로 남는다(삭제·이동하지 않는다 — provenance
    가 흐려진다). 다만 그것이 조합을 건너뛰게 해서는 안 된다. 다른 평가기의
    산물이므로 재사용하면 한 요약 안에 두 계약이 섞인다.
    """
    if not evaluator_policy:
        raise ValueError("evaluator_policy is required to resolve completed keys")
    prefix = f"{evaluator_policy}|"
    return {row["key"] for row in rows
            if row.get("status") == "ok"
            and str(row.get("key") or "").startswith(prefix)}


def latest_results(rows: list[dict]) -> list[dict]:
    return list({row["key"]: row for row in rows}.values())


def config_kwargs(config: str) -> dict[str, str]:
    if config in {"none", "unspecified", "current"}:
        return {}
    level = config.removeprefix("level:")
    if level not in gemini_client._THINKING_LEVELS:
        raise ValueError(f"invalid config: {config}")
    return {"thinking_level": level}


# 평가에 쓸 수 있는 라벨 상태. `HUMAN_REVIEWED_AI_ASSISTED` 는 **일부러 빠져 있다** —
# 사람이 읽고 승인했지만 AI 판정을 먼저 본 뒤의 판단이라, anchoring 크기를 재기 전에는
# 독립 정답이 아니다(tools/gold_provenance.py). 조용히 빠지는 것이 아니라 여기서
# 명시적으로 제외하고, `excluded_by_provenance()` 로 몇 건이 빠졌는지 보고한다.
EVALUATION_STATUSES = frozenset({"USER_SPECIFIED", "HUMAN_LABELLED"})
AI_ASSISTED_STATUS = "HUMAN_REVIEWED_AI_ASSISTED"


def labelled_cases(payload: dict) -> list[dict]:
    cases = []
    for case in payload.get("cases") or []:
        label = case.get("human_label")
        if label and case.get("label_status") in EVALUATION_STATUSES:
            cases.append({**case, "gold": label})
    return cases


def excluded_by_provenance(payload: dict) -> int:
    """출처 때문에 평가에서 빠진 라벨 수. 0 이 아니면 보고에 실어야 한다."""
    return sum(1 for case in payload.get("cases") or []
               if case.get("human_label")
               and case.get("label_status") == AI_ASSISTED_STATUS)


def redundant_arms(rows: list[dict]) -> dict[str, list[str]]:
    """canary 결과에서 baseline 과 **행동이 같은** arm 을 찾아낸다.

    실측(계획 문서 §1.1): `gemini-3.5-flash-lite` 에서 `level:low` 는 thought 토큰이
    0 이다. 즉 그 모델에서 low 는 thinking 을 끈 baseline 과 구별되지 않는다. 그런
    arm 을 dev 평가까지 끌고 가면 같은 설정을 두 번 재느라 quota 만 태운다.

    판정 근거를 관측값(thought 토큰)에 둔다 — 모델별 표를 손으로 적으면 모델이
    바뀔 때 조용히 틀린다.
    """
    totals: dict[tuple[str, str], int] = {}
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        key = (str(row.get("model")), str(row.get("config")))
        totals[key] = totals.get(key, 0) + int(row.get("thought_tokens") or 0)
        seen[key] = seen.get(key, 0) + 1
    redundant: dict[str, list[str]] = {}
    for (model, config), thoughts in sorted(totals.items()):
        if config in {"none", "unspecified", "current"} or thoughts:
            continue
        # 관측이 한 건뿐이면 "0 이었다"가 아니라 "아직 모른다"로 둔다.
        if seen[(model, config)] < 2:
            continue
        redundant.setdefault(model, []).append(config)
    return redundant


# ``user_message()`` 는 여기 있었다. 세 갈래(제목 두 줄 / claim+evidence / case
# 통째 직렬화) 모두 production 이 실제로 보내는 메시지가 아니었다. 메시지 구성은
# 이제 TASKS 의 request_builder 만 소유한다 — 계약이 열리지 않으면 만들어지지도
# 않는다.


def plan_jobs(models: list[str], configs: list[str], cases: list[dict],
              repeat: int, evaluator_policy: str) -> list[tuple]:
    """호출 순서를 정한다. **순서가 곧 측정 조건**이라 함수로 빼서 시험한다.

    예전에는 config 가 바깥 루프였다. 그러면 한 config 의 호출이 전부 끝난 뒤에
    다음 config 가 시작되므로, 뒤에 도는 arm 일수록 `_pace()` 의 분당 상한과 누적
    quota 압력을 더 받는다. 늘 마지막에 도는 arm(high)이 체계적으로 느리고 덜
    안정적으로 보이는데, 그건 reasoning 의 성질이 아니라 **자리의 성질**이다.

    그래서 case 를 바깥에 두고 case 안에서 config 순서를 섞는다. 섞기는 무작위가
    아니라 case id 해시라 재현된다 — 재개해도 같은 순서고, 결과를 본 뒤 순서를
    바꾸는 길도 막힌다.
    """
    jobs = []
    for model in models:
        for case in cases:
            for repeat_index in range(repeat):
                ordered = sorted(configs, key=lambda config: hashlib.sha256(
                    f"arm-order-v1|{case['id']}|{repeat_index}|{config}"
                    .encode("utf-8")).hexdigest())
                for config in ordered:
                    jobs.append((
                        model, config, case, repeat_index,
                        result_key(model, config, case["id"], repeat_index,
                                   evaluator_policy)))
    return jobs


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("status") == "ok"]
    latency = [float(row["latency_seconds"]) for row in completed]
    overhead = [float(row["overhead_seconds"]) for row in completed
                if row.get("overhead_seconds") is not None]
    false_merge = sum(row.get("gold") == "SEPARATE" and row.get("prediction") == "MERGE"
                      for row in completed)
    false_split = sum(row.get("gold") == "MERGE" and row.get("prediction") == "SEPARATE"
                      for row in completed)
    grouped: dict[str, list[str]] = {}
    for row in completed:
        key = f"{row['model']}|{row['config']}|{row['fixture_id']}"
        grouped.setdefault(key, []).append(str(row.get("prediction")))
    consistent = sum(len(set(values)) == 1 for values in grouped.values())
    error_counts: dict[str, int] = {}
    for row in completed:
        for error_type in row.get("error_types") or []:
            error_counts[error_type] = error_counts.get(error_type, 0) + 1
    return {
        "completed": len(completed),
        "failed": len(rows) - len(completed),
        "correct": sum(row.get("gold") == row.get("prediction") for row in completed),
        "false_merge": false_merge,
        "false_split": false_split,
        "repeat_consistency": consistent / len(grouped) if grouped else None,
        "latency_p50": statistics.median(latency) if latency else None,
        "latency_p95": (sorted(latency)[max(0, int(len(latency) * .95) - 1)]
                        if latency else None),
        # API 시간과 그 밖의 대기(페이싱·백오프)를 갈라 둔다. 섞으면 "느린
        # config" 와 "늦게 돈 config" 를 구별하지 못한다.
        "overhead_p50": (statistics.median(overhead) if overhead else None),
        "overhead_total": round(sum(overhead), 3) if overhead else None,
        "thought_tokens": sum(int(row.get("thought_tokens") or 0) for row in completed),
        "redundant_arms": redundant_arms(rows),
        "truncation": sum(bool(row.get("truncated")) for row in rows),
        "json_failure": sum(row.get("failure_type") == "json" for row in rows),
        "quota_429": sum(row.get("failure_type") == "quota" for row in rows),
        "error_types": error_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--max-new-calls", type=int, default=30,
        help="checkpoint after at most this many new calls (default: 30; 0 = plan only)")
    args = parser.parse_args()
    # 계약 확인이 **가장 먼저**다. fixture 를 읽거나 출력 폴더를 만들기 전에
    # 실패해야, 증명되지 않은 평가가 부분 산출물조차 남기지 않는다.
    try:
        entry = contract(args.task)
        entry.require()
    except ContractNotProven as exc:
        parser.error(
            f"refusing to evaluate — {exc}. "
            "Prove the production replay first (see "
            "docs/2026-09-08-gemini-reasoning-v2.md P2), then register a "
            "request_builder and evaluator_policy in TASKS.")
    evaluator_policy = entry.evaluator_policy or ""
    if args.max_new_calls < 0:
        parser.error("--max-new-calls must be >= 0")
    if args.max_new_calls and not gemini_client.is_available():
        parser.error("GEMINI_API_KEY is required for live evaluation")
    cases = labelled_cases(json.loads(args.fixtures.read_text(encoding="utf-8")))
    if not cases:
        parser.error("no human-labelled cases; refusing self-evaluation")
    configs = args.config or ["none", "level:medium", "level:high"]
    models = args.model or [gemini_client.MODEL]
    for config in configs:
        config_kwargs(config)
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if results_path.exists() else []
    # Only successful keys are complete. Quota/truncation/API failures stay pending
    # and may be retried on a later bounded run.
    done = completed_keys(rows, evaluator_policy)
    planned = plan_jobs(models, configs, cases, args.repeat, evaluator_policy)
    pending_before = [job for job in planned if job[4] not in done]
    new_attempted = 0
    quota_stopped = False
    with results_path.open("a", encoding="utf-8") as stream:
        for model, config, case, repeat, key in pending_before:
            if new_attempted >= args.max_new_calls:
                break
            kwargs = config_kwargs(config)
            before = len(gemini_client._CALL_DETAIL)
            started = time.monotonic()
            row = {"key": key, "model": model, "config": config,
                   "fixture_id": case["id"], "repeat": repeat,
                   "gold": case["gold"]}
            try:
                system_prompt, message, call_options = call_contract(args.task, case)
                result = gemini_client.call_json(
                    system_prompt, message, model=model,
                    label=f"eval:{args.task}", **call_options, **kwargs)
                detail = (gemini_client._CALL_DETAIL[-1]
                          if len(gemini_client._CALL_DETAIL) > before else {})
                wall_clock = time.monotonic() - started
                # `gemini_client` 의 타이머는 `_pace()` **뒤에** 시작하므로 순수
                # 요청·응답 시간이다. 평가기의 벽시계는 그 앞의 페이싱 대기와
                # 재시도 백오프까지 포함한다. 둘을 한 칸에 담으면 reliability 게이트가
                # "이 config 가 느리다"와 "이 config 가 늦게 돌았다"를 구별하지 못한다.
                api_latency = detail.get("latency_seconds")
                row.update(status="ok", prediction=result.get("verdict"),
                           error_types=result.get("error_types") or [],
                           latency_seconds=(api_latency if api_latency is not None
                                            else wall_clock),
                           wall_clock_seconds=wall_clock,
                           overhead_seconds=(max(0.0, wall_clock - api_latency)
                                             if api_latency is not None else None),
                           thought_tokens=detail.get("thought_tokens") or 0,
                           truncated=bool(detail.get("truncated")))
            except gemini_client.GeminiTruncated as exc:
                row.update(status="failed", failure_type="truncation",
                           truncated=True, error=str(exc)[:300])
            except Exception as exc:  # persisted checkpoint, never converted to PASS
                error = str(exc)
                is_quota = "429" in error or "quota" in error.lower()
                row.update(status="failed",
                           failure_type=("quota" if is_quota else "api_or_json"),
                           truncated=False, error=error[:300])
                quota_stopped = is_quota
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            rows.append(row)
            if row.get("status") == "ok":
                done.add(key)
            new_attempted += 1
            if quota_stopped:
                break
    summary = summarize(latest_results(rows))
    summary.update({
        "planned_combinations": len(planned),
        "completed_keys_before_run": len(planned) - len(pending_before),
        "new_calls_attempted": new_attempted,
        "pending_combinations": sum(job[4] not in done for job in planned),
        "checkpoint_call_limit": args.max_new_calls,
        "quota_state": "STOPPED_ON_QUOTA" if quota_stopped else "NOT_HIT",
    })
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
