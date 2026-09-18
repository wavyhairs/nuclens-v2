"""Blind, policy-versioned Curation P4 judge.

This is deliberately separate from ``llm_eval``.  Production curation returns
``items``; the judge returns verdicts about those items.  Pretending those are
one contract was the P4 stop condition.  This module owns the second contract
and cannot make a live call unless a caller supplies a positive call budget.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable

EVALUATOR_POLICY = "curation-blind-chatgpt-v1"
DIMENSIONS = (
    "event_boundary", "scope", "stage", "date", "causality",
    "factual_support", "unsupported_inference", "important_omission",
    "certainty", "core_distortion",
)
VERDICTS = ("PASS", "REPAIR", "BLOCK")

# Fixed before calibration.  Changing any value requires a new policy name.
CALIBRATION_THRESHOLDS = {
    "binary_pass_vs_intervention_agreement_min": 0.85,
    "false_pass_max": 0,
    "unsafe_pass_max": 0,
    "repeat_modal_stability_min": 0.90,
    "identical_pair_tie_rate_min": 0.95,
    "identical_pair_position_bias_max": 0.05,
    "duplicate_verdict_consistency_min": 0.95,
    "schema_or_logic_errors_max": 0,
    "repeats": 3,
}

SYSTEM_PROMPT = """You are an independent nuclear-news curation auditor.
Judge only the source packet and anonymous candidate outputs supplied. Never
infer which system, model, configuration, or candidate is preferred. Apply the
rubric independently to every candidate, then make all requested pairwise
comparisons. PASS means publishable without substantive correction. REPAIR
means the central event remains usable but one or more substantive statements,
boundaries, omissions, or certainty levels must be corrected. BLOCK means the
core event is distorted, unsupported, or unsafe to repair locally.

For each dimension use PASS, ERROR, or NOT_EVALUABLE. ERROR requires a severity
of MINOR, MAJOR, or CRITICAL; otherwise severity is NONE. Missing source
evidence is NOT_EVALUABLE, never an invented fact. factual_support covers all
asserted facts; unsupported_inference covers claims beyond evidence;
important_omission covers source facts whose absence materially changes the
meaning; core_distortion is CRITICAL when the main event becomes another event.
Return only the strict JSON schema response."""


def _dimension_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": list(DIMENSIONS)},
            "status": {"type": "string", "enum": ["PASS", "ERROR", "NOT_EVALUABLE"]},
            "severity": {"type": "string", "enum": ["NONE", "MINOR", "MAJOR", "CRITICAL"]},
            "reason": {"type": "string"},
        },
        "required": ["name", "status", "severity", "reason"],
        "additionalProperties": False,
    }


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string", "enum": list("ABCD")},
                    "dimensions": {"type": "array", "items": _dimension_schema()},
                    "final_verdict": {"type": "string", "enum": list(VERDICTS)},
                    "summary": {"type": "string"},
                },
                "required": ["candidate", "dimensions", "final_verdict", "summary"],
                "additionalProperties": False,
            },
        },
        "pairwise": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "left": {"type": "string", "enum": list("ABCD")},
                    "right": {"type": "string", "enum": list("ABCD")},
                    "preferred": {"type": "string", "enum": ["A", "B", "C", "D", "TIE"]},
                    "reason": {"type": "string"},
                },
                "required": ["left", "right", "preferred", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates", "pairwise"],
    "additionalProperties": False,
}


class JudgeValidationError(ValueError):
    """Malformed, incomplete, or logically inconsistent judge output."""


@dataclass(frozen=True)
class BlindRequest:
    case_id: str
    repeat: int
    aliases: dict[str, str]  # anonymous alias -> internal candidate id (never sent)
    body: dict


def result_key(case_id: str, repeat: int) -> str:
    return f"{EVALUATOR_POLICY}|{case_id}|{repeat}"


def completed_keys(rows: Iterable[dict]) -> set[str]:
    prefix = f"{EVALUATOR_POLICY}|"
    return {str(row.get("key")) for row in rows
            if row.get("status") == "ok" and str(row.get("key", "")).startswith(prefix)}


def _ordered_ids(case_id: str, repeat: int, candidate_ids: Iterable[str]) -> list[str]:
    return sorted(candidate_ids, key=lambda candidate_id: hashlib.sha256(
        f"{EVALUATOR_POLICY}|order|{case_id}|{repeat}|{candidate_id}".encode("utf-8")
    ).hexdigest())


def build_blind_request(case_id: str, source: dict, candidates: dict[str, dict],
                        repeat: int) -> BlindRequest:
    if not 2 <= len(candidates) <= 4:
        raise ValueError("blind judge requires 2-4 candidates")
    forbidden = ("model", "config", "reasoning", "thinking", "baseline")
    ordered = _ordered_ids(case_id, repeat, candidates)
    aliases = {chr(65 + index): internal for index, internal in enumerate(ordered)}
    anonymous = []
    for alias, internal in aliases.items():
        value = candidates[internal]
        encoded = json.dumps(value, ensure_ascii=False).lower()
        if any(term in encoded for term in forbidden):
            raise ValueError(f"candidate {internal!r} leaks evaluator metadata")
        anonymous.append({"candidate": alias, "output": value})
    payload = {
        "source": source,
        "candidates": anonymous,
        "required_pairs": [list(pair) for pair in combinations(aliases, 2)],
    }
    body = {
        "input": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "max_output_tokens": 4000,
    }
    return BlindRequest(case_id, repeat, aliases, body)


def validate_judgment(value: dict, aliases: Iterable[str]) -> dict:
    aliases = list(aliases)
    if not isinstance(value, dict):
        raise JudgeValidationError("judge result is not an object")
    rows = value.get("candidates")
    pairs = value.get("pairwise")
    if not isinstance(rows, list) or not isinstance(pairs, list):
        raise JudgeValidationError("candidates/pairwise missing")
    if {row.get("candidate") for row in rows if isinstance(row, dict)} != set(aliases):
        raise JudgeValidationError("candidate aliases missing or duplicated")
    if len(rows) != len(aliases):
        raise JudgeValidationError("duplicate candidate row")
    for row in rows:
        if row.get("final_verdict") not in VERDICTS:
            raise JudgeValidationError("invalid final verdict")
        dimensions = row.get("dimensions")
        if not isinstance(dimensions, list) or len(dimensions) != len(DIMENSIONS):
            raise JudgeValidationError("missing dimension")
        if {item.get("name") for item in dimensions if isinstance(item, dict)} != set(DIMENSIONS):
            raise JudgeValidationError("dimension names missing or duplicated")
        has_error = False
        for item in dimensions:
            status, severity = item.get("status"), item.get("severity")
            if status not in {"PASS", "ERROR", "NOT_EVALUABLE"}:
                raise JudgeValidationError("invalid dimension status")
            if severity not in {"NONE", "MINOR", "MAJOR", "CRITICAL"}:
                raise JudgeValidationError("invalid severity")
            if (status == "ERROR") != (severity != "NONE"):
                raise JudgeValidationError("status/severity inconsistency")
            has_error |= status == "ERROR"
        if row["final_verdict"] == "PASS" and has_error:
            raise JudgeValidationError("PASS candidate contains an error")
        if row["final_verdict"] != "PASS" and not has_error:
            raise JudgeValidationError("intervention verdict has no error")
    expected = {tuple(pair) for pair in combinations(aliases, 2)}
    actual: set[tuple[str, str]] = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            raise JudgeValidationError("pairwise row is not an object")
        key = (pair.get("left"), pair.get("right"))
        if key not in expected or key in actual:
            raise JudgeValidationError("unexpected or duplicate pair")
        if pair.get("preferred") not in {key[0], key[1], "TIE"}:
            raise JudgeValidationError("pairwise preference is not a member or TIE")
        actual.add(key)
    if actual != expected:
        raise JudgeValidationError("pairwise comparison missing")
    return value


def estimate_tokens(requests: Iterable[BlindRequest]) -> dict:
    """Conservative preflight estimate; actual usage must replace it after calls."""
    rows = list(requests)
    input_tokens = sum((len(json.dumps(row.body, ensure_ascii=False)) + 2) // 3 for row in rows)
    output_cap = sum(int(row.body["max_output_tokens"]) for row in rows)
    # Ten dimensions plus a summary need about 900 tokens per candidate in the
    # fixed schema. This is a planning estimate, not a billing observation.
    expected_output = sum(min(int(row.body["max_output_tokens"]),
                              900 * len(json.loads(row.body["input"])["candidates"]))
                          for row in rows)
    return {
        "calls": len(rows), "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": expected_output,
        "maximum_output_tokens": output_cap,
    }


def calibration_requests(gold: dict) -> list[BlindRequest]:
    eligible = {"USER_SPECIFIED", "HUMAN_LABELLED", "HUMAN_BLIND_CONFIRMED",
                "HUMAN_BLIND_CORRECTED"}
    requests = []
    for case in gold.get("cases") or []:
        if not case.get("human_label") or case.get("label_status") not in eligible:
            continue
        source = {key: value for key, value in (case.get("source_input") or {}).items()
                  if key != "url"}
        source["evidence_limit"] = (
            "Only the stored source fields are available. Mark unsupported dimensions "
            "NOT_EVALUABLE; do not fill them from memory or the URL."
        )
        candidates = {"primary": case["current_output"], "identical_control": case["current_output"]}
        for repeat in range(CALIBRATION_THRESHOLDS["repeats"]):
            requests.append(build_blind_request(case["id"], source, candidates, repeat))
    return requests


def manual_packet_text(packet_id: str, requests: list[BlindRequest]) -> str:
    """Create a paste/upload-ready ChatGPT questionnaire with no secret arm map."""
    cases = []
    for request in requests:
        payload = json.loads(request.body["input"])
        cases.append({"case_id": request.case_id, **payload})
    answer_shape = {
        "policy": EVALUATOR_POLICY,
        "packet_id": packet_id,
        "judge_model_display": "사용자가 ChatGPT UI에 표시된 모델명을 입력",
        "judgments": [{
            "case_id": "각 입력 case_id",
            "candidates": "각 후보별 candidate/dimensions/final_verdict/summary 배열",
            "pairwise": "required_pairs 전부에 대한 left/right/preferred/reason 배열",
        }],
    }
    return f"""# Nuclens Curation blind judge — {packet_id}

이 파일은 독립 blind 판정 질문지다. 반드시 **새 ChatGPT 대화**에서 실행한다.
웹 검색·URL 열기·외부 지식·이전 대화 기억을 사용하지 말고, 아래 source에 실제로
들어 있는 내용만 근거로 삼는다. source가 부족한 차원은 추측하지 말고
`NOT_EVALUABLE`로 판정한다. 후보의 작성 시스템이나 설정을 추정하지 않는다.

## 고정 rubric

{SYSTEM_PROMPT}

추가 출력 규칙:

- 아래 모든 case와 후보를 빠짐없이 판정한다.
- dimension은 정확히 이 순서로 한 번씩 쓴다: {', '.join(DIMENSIONS)}.
- reason과 summary는 간결하게 쓴다(각 25단어 이내 권장).
- required_pairs를 빠짐없이 한 번씩 판정한다.
- 설명, Markdown fence 없이 JSON object 하나만 출력한다.
- `judge_model_display`에는 현재 ChatGPT 화면에 표시된 모델명을 사용자가 확인해
  넣어야 한다. 모델이 확실히 알 수 없으면 빈 문자열로 두고 사용자가 저장 전에 채운다.

답변 최상위 형태:

{json.dumps(answer_shape, ensure_ascii=False, indent=2)}

각 judgment의 정확한 JSON Schema:

{json.dumps(JUDGE_SCHEMA, ensure_ascii=False, indent=2)}

## 입력

{json.dumps({"policy": EVALUATOR_POLICY, "packet_id": packet_id, "cases": cases}, ensure_ascii=False, indent=2)}
"""


def export_manual_calibration_packets(gold: dict, out_dir, *, repeats: int | None = None) -> list[dict]:
    """Write one independent ChatGPT packet per repeat plus a private import manifest."""
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    all_requests = calibration_requests(gold)
    repeat_count = repeats if repeats is not None else CALIBRATION_THRESHOLDS["repeats"]
    exported, manifest_rows = [], []
    for repeat in range(repeat_count):
        requests = [request for request in all_requests if request.repeat == repeat]
        packet_id = f"calibration-repeat-{repeat}"
        text = manual_packet_text(packet_id, requests)
        path = out / f"{packet_id}.md"
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        exported.append({"packet_id": packet_id, "path": str(path), "cases": len(requests),
                         "sha256": digest})
        manifest_rows.append({
            "packet_id": packet_id, "repeat": repeat, "packet_sha256": digest,
            "requests": [{"case_id": request.case_id, "aliases": request.aliases}
                         for request in requests],
        })
    (out / "calibration-manifest.private.json").write_text(json.dumps({
        "policy": EVALUATOR_POLICY, "packets": manifest_rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return exported


def import_manual_calibration_answer(answer_path, manifest_path) -> list[dict]:
    """Validate a whole manual packet atomically and convert it to calibration rows."""
    from pathlib import Path
    import time

    answer_file, manifest_file = Path(answer_path), Path(manifest_path)
    answer_bytes = answer_file.read_bytes()
    try:
        answer = json.loads(answer_bytes)
    except json.JSONDecodeError as exc:
        raise JudgeValidationError(f"manual answer is not JSON: {exc}") from exc
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if answer.get("policy") != EVALUATOR_POLICY or manifest.get("policy") != EVALUATOR_POLICY:
        raise JudgeValidationError("manual answer/manifest evaluator policy mismatch")
    packet = next((item for item in manifest.get("packets") or []
                   if item.get("packet_id") == answer.get("packet_id")), None)
    if packet is None:
        raise JudgeValidationError("unknown manual packet_id")
    judgments = answer.get("judgments")
    if not isinstance(judgments, list):
        raise JudgeValidationError("manual judgments array missing")
    by_case = {item.get("case_id"): item for item in judgments if isinstance(item, dict)}
    expected = {item["case_id"] for item in packet["requests"]}
    if len(by_case) != len(judgments) or set(by_case) != expected:
        raise JudgeValidationError("manual answer cases missing, duplicated, or unexpected")
    imported_at = time.time()
    answer_sha = hashlib.sha256(answer_bytes).hexdigest()
    rows = []
    for request_meta in packet["requests"]:
        case_id, aliases = request_meta["case_id"], request_meta["aliases"]
        item = dict(by_case[case_id])
        item.pop("case_id", None)
        value = validate_judgment(item, aliases)
        candidates = {row["candidate"]: row for row in value["candidates"]}
        primary_alias = next(alias for alias, internal in aliases.items() if internal == "primary")
        control_alias = next(alias for alias, internal in aliases.items()
                             if internal == "identical_control")
        pair = next(row for row in value["pairwise"]
                    if {row["left"], row["right"]} == {primary_alias, control_alias})
        rows.append({
            "key": result_key(case_id, int(packet["repeat"])), "status": "ok",
            "case_id": case_id, "repeat": int(packet["repeat"]),
            "primary_verdict": candidates[primary_alias]["final_verdict"],
            "control_verdict": candidates[control_alias]["final_verdict"],
            "identical_pair_preference": pair["preferred"],
            "identical_pair_left": pair["left"], "identical_pair_right": pair["right"],
            "logic_errors": 0, "aliases": aliases, "judgment": value,
            "provenance": {"mode": "manual_chatgpt", "packet_id": packet["packet_id"],
                           "packet_sha256": packet["packet_sha256"],
                           "answer_sha256": answer_sha,
                           "judge_model_display": answer.get("judge_model_display") or "",
                           "imported_at": imported_at},
        })
    return rows


def export_manual_canary_packet(requests: list[BlindRequest], out_dir) -> dict:
    """Export the 15-case/four-candidate canary judge packet."""
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    packet_id = "canary-judge"
    text = manual_packet_text(packet_id, requests)
    path = out / f"{packet_id}.md"
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (out / "canary-manifest.private.json").write_text(json.dumps({
        "policy": EVALUATOR_POLICY, "packet_id": packet_id,
        "packet_sha256": digest,
        "requests": [{"case_id": request.case_id, "repeat": request.repeat,
                      "aliases": request.aliases} for request in requests],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"packet_id": packet_id, "path": str(path), "cases": len(requests),
            "sha256": digest}


def import_manual_canary_answer(answer_path, manifest_path) -> list[dict]:
    """Validate the whole canary answer atomically; no partial rows survive."""
    from pathlib import Path
    import time

    answer_file, manifest_file = Path(answer_path), Path(manifest_path)
    answer_bytes = answer_file.read_bytes()
    try:
        answer = json.loads(answer_bytes)
    except json.JSONDecodeError as exc:
        raise JudgeValidationError(f"manual answer is not JSON: {exc}") from exc
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (answer.get("policy") != EVALUATOR_POLICY
            or manifest.get("policy") != EVALUATOR_POLICY
            or answer.get("packet_id") != manifest.get("packet_id")):
        raise JudgeValidationError("manual canary policy/packet mismatch")
    judgments = answer.get("judgments")
    if not isinstance(judgments, list):
        raise JudgeValidationError("manual judgments array missing")
    by_case = {item.get("case_id"): item for item in judgments if isinstance(item, dict)}
    expected = {item["case_id"] for item in manifest["requests"]}
    if len(by_case) != len(judgments) or set(by_case) != expected:
        raise JudgeValidationError("manual canary cases missing, duplicated, or unexpected")
    answer_sha = hashlib.sha256(answer_bytes).hexdigest()
    rows = []
    for meta in manifest["requests"]:
        value = dict(by_case[meta["case_id"]])
        value.pop("case_id", None)
        value = validate_judgment(value, meta["aliases"])
        rows.append({
            "key": result_key(meta["case_id"], int(meta["repeat"])), "status": "ok",
            "case_id": meta["case_id"], "repeat": int(meta["repeat"]),
            "aliases": meta["aliases"], "judgment": value,
            "provenance": {"mode": "manual_chatgpt", "packet_id": manifest["packet_id"],
                           "packet_sha256": manifest["packet_sha256"],
                           "answer_sha256": answer_sha,
                           "judge_model_display": answer.get("judge_model_display") or "",
                           "imported_at": time.time()},
        })
    return rows


def calibration_summary(gold: dict, rows: list[dict]) -> dict:
    """Score only current-policy complete rows. Missing/malformed data fails closed."""
    cases = {case["id"]: case for case in gold.get("cases") or []}
    latest = {str(row.get("key")): row for row in rows}
    accepted = [row for row in latest.values() if row.get("status") == "ok"
                and str(row.get("key", "")).startswith(f"{EVALUATOR_POLICY}|")]
    predictions: dict[str, list[str]] = {}
    binary_correct = false_pass = unsafe_pass = ties = duplicate_same = logic_errors = 0
    position_choices = {"left": 0, "right": 0}
    expected = 0
    for row in accepted:
        case = cases.get(row.get("case_id"))
        if not case:
            continue
        expected += 1
        primary = row.get("primary_verdict")
        control = row.get("control_verdict")
        predictions.setdefault(case["id"], []).append(primary)
        human_pass = case.get("human_label") == "PASS"
        binary_correct += (primary == "PASS") == human_pass
        false_pass += bool(primary == "PASS" and not human_pass)
        # In this Curation Gold every human intervention label is an obvious
        # intervention case for the safety gate, even though candidate_kind is
        # not present (that field belongs to Semantic Gold).
        unsafe_pass += bool(primary == "PASS" and not human_pass)
        ties += row.get("identical_pair_preference") == "TIE"
        preference = row.get("identical_pair_preference")
        if preference == row.get("identical_pair_left"):
            position_choices["left"] += 1
        elif preference == row.get("identical_pair_right"):
            position_choices["right"] += 1
        duplicate_same += primary == control
        logic_errors += int(row.get("logic_errors") or 0)
    stable = sum(len(values) >= 2 and max(values.count(v) for v in VERDICTS) >= 2
                 for values in predictions.values())
    denominators = {"judgments": expected, "cases": len(predictions)}
    metrics = {
        "binary_pass_vs_intervention_agreement": binary_correct / expected if expected else 0.0,
        "false_pass": false_pass,
        "unsafe_pass": unsafe_pass,
        "repeat_modal_stability": stable / len(predictions) if predictions else 0.0,
        "identical_pair_tie_rate": ties / expected if expected else 0.0,
        "identical_pair_position_bias_rate": ((expected - ties) / expected
                                                if expected else 1.0),
        "identical_pair_non_tie_positions": position_choices,
        "duplicate_verdict_consistency": duplicate_same / expected if expected else 0.0,
        "schema_or_logic_errors": logic_errors,
    }
    t = CALIBRATION_THRESHOLDS
    passed = (expected == len(calibration_requests(gold))
              and metrics["binary_pass_vs_intervention_agreement"] >= t["binary_pass_vs_intervention_agreement_min"]
              and false_pass <= t["false_pass_max"]
              and unsafe_pass <= t["unsafe_pass_max"]
              and metrics["repeat_modal_stability"] >= t["repeat_modal_stability_min"]
              and metrics["identical_pair_tie_rate"] >= t["identical_pair_tie_rate_min"]
              and metrics["identical_pair_position_bias_rate"] <= t["identical_pair_position_bias_max"]
              and metrics["duplicate_verdict_consistency"] >= t["duplicate_verdict_consistency_min"]
              and logic_errors <= t["schema_or_logic_errors_max"])
    return {"policy": EVALUATOR_POLICY, "status": "PASS" if passed else "NOT_PROVEN",
            "thresholds": CALIBRATION_THRESHOLDS, "metrics": metrics,
            "denominators": denominators,
            "case_stability": {
                case_id: ("STABLE" if len(values) >= 2
                          and max(values.count(v) for v in VERDICTS) >= 2
                          else "JUDGE_UNSTABLE")
                for case_id, values in sorted(predictions.items())
            },
            "unstable_cases": sorted(case_id for case_id, values in predictions.items()
                                     if len(values) < 2 or max(values.count(v) for v in VERDICTS) < 2)}
