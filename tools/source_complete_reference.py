"""Build auditable reference truth from the source-complete evidence store.

This is evaluation-only infrastructure.  It exports a reviewer packet with no
Gemini/config identity and imports a strict, fingerprint-bound answer into the
legacy Gold shape consumed by :mod:`tools.curation_p4_judge`.  The legacy
``human_label`` key is retained for compatibility, while explicit provenance
records whether the reviewer was human or model-assisted.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import curation_p4_judge as judge
from tools import source_complete_evidence as evidence

POLICY = "source-complete-reference-truth-v1"
LABEL_STATUS = "INDEPENDENT_SOURCE_COMPLETE_REVIEW"
REVIEWER_TYPES = {"human", "codex_agent", "independent_model"}


class ReferenceValidationError(ValueError):
    """The store or reference answer is incomplete or inconsistent."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceValidationError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReferenceValidationError(f"JSON object required: {path}")
    return value


def load_cases(store: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((store / "cases").glob("*.json")):
        document = _read_json(path)
        gate = evidence.validate_case_document(document, store)
        if not gate.source_complete:
            raise ReferenceValidationError(
                f"source-complete validation failed for {path.name}: "
                f"{json.dumps(gate.as_dict(), ensure_ascii=False)}"
            )
        try:
            body = (store / document["source_content"]["body_ref"]["path"]).read_text(
                encoding="utf-8"
            )
        except (KeyError, OSError, UnicodeDecodeError) as exc:
            raise ReferenceValidationError(f"cannot read body for {path.name}: {exc}") from exc
        rows.append({
            "case_id": document["case_id"],
            "case_fingerprint": document["case_fingerprint"],
            "source": {
                "title": document["source_content"]["title"],
                "description": document["source_content"]["description"],
                "body": body,
                "publisher": document["source_provenance"]["publisher"],
                "domain": document["source_provenance"]["domain"],
                "published_at": document["source_provenance"]["published_at"],
            },
            "current_output": document["production_output"]["normalized_output"],
            "risk_buckets": list(
                document.get("selection_metadata_not_gold", {}).get("risk_buckets") or []
            ),
        })
    if not rows:
        raise ReferenceValidationError(f"no source-complete cases in {store}")
    return rows


def export_packet(store: Path, out: Path) -> dict:
    cases = load_cases(store)
    packet = {
        "policy": POLICY,
        "instructions": {
            "evidence_boundary": (
                "Use only source and current_output. Do not browse, infer system identity, "
                "or use risk_buckets as an answer."
            ),
            "labels": {
                "PASS": "Publishable without substantive correction.",
                "REPAIR": "Central event remains usable but needs substantive correction.",
                "BLOCK": "Core event is distorted, unsupported, or unsafe to repair locally.",
            },
            "dimensions": list(judge.DIMENSIONS),
        },
        "cases": [{key: value for key, value in row.items() if key != "risk_buckets"}
                  for row in cases],
        "answer_template": {
            "policy": POLICY,
            "reviewer": {"type": "human|codex_agent|independent_model", "display": "required"},
            "judgments": [{
                "case_id": row["case_id"],
                "case_fingerprint": row["case_fingerprint"],
                "label": "PASS|REPAIR|BLOCK",
                "error_dimensions": [],
                "rationale": "required",
                "required_repair": None,
            } for row in cases],
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"policy": POLICY, "cases": len(cases), "packet": str(out)}


def _validate_answer(cases: list[dict], answer: dict) -> tuple[dict, dict[str, dict]]:
    if answer.get("policy") != POLICY:
        raise ReferenceValidationError("reference policy mismatch")
    reviewer = answer.get("reviewer")
    if not isinstance(reviewer, dict):
        raise ReferenceValidationError("reviewer object required")
    reviewer_type = str(reviewer.get("type") or "")
    reviewer_display = str(reviewer.get("display") or "").strip()
    if reviewer_type not in REVIEWER_TYPES or not reviewer_display:
        raise ReferenceValidationError("valid reviewer.type and reviewer.display are required")
    judgments = answer.get("judgments")
    if not isinstance(judgments, list):
        raise ReferenceValidationError("judgments array required")
    by_id: dict[str, dict] = {}
    for row in judgments:
        if not isinstance(row, dict):
            raise ReferenceValidationError("every judgment must be an object")
        case_id = str(row.get("case_id") or "")
        if not case_id or case_id in by_id:
            raise ReferenceValidationError(f"missing or duplicate case_id: {case_id!r}")
        by_id[case_id] = row
    expected = {row["case_id"] for row in cases}
    if set(by_id) != expected:
        raise ReferenceValidationError(
            f"judgment case set mismatch; missing={sorted(expected - set(by_id))}, "
            f"extra={sorted(set(by_id) - expected)}"
        )
    for case in cases:
        row = by_id[case["case_id"]]
        if row.get("case_fingerprint") != case["case_fingerprint"]:
            raise ReferenceValidationError(f"case fingerprint mismatch: {case['case_id']}")
        label = row.get("label")
        dimensions = row.get("error_dimensions")
        rationale = str(row.get("rationale") or "").strip()
        repair = row.get("required_repair")
        if label not in judge.VERDICTS:
            raise ReferenceValidationError(f"invalid label: {case['case_id']}")
        if (not isinstance(dimensions, list) or len(dimensions) != len(set(dimensions))
                or any(name not in judge.DIMENSIONS for name in dimensions)):
            raise ReferenceValidationError(f"invalid error_dimensions: {case['case_id']}")
        if not rationale:
            raise ReferenceValidationError(f"rationale required: {case['case_id']}")
        if label == "PASS" and (dimensions or repair not in (None, "")):
            raise ReferenceValidationError(f"PASS cannot contain errors/repair: {case['case_id']}")
        if label != "PASS" and (not dimensions or not str(repair or "").strip()):
            raise ReferenceValidationError(
                f"{label} requires error dimensions and repair: {case['case_id']}"
            )
    return {"type": reviewer_type, "display": reviewer_display}, by_id


def import_answer(store: Path, answer_path: Path, out: Path) -> dict:
    cases = load_cases(store)
    reviewer, by_id = _validate_answer(cases, _read_json(answer_path))
    gold_cases = []
    labels: Counter[str] = Counter()
    for case in cases:
        decision = by_id[case["case_id"]]
        labels[decision["label"]] += 1
        gold_cases.append({
            "id": case["case_id"],
            # Compatibility key used by the frozen calibration scorer.  The
            # provenance below prevents this from being misreported as human.
            "human_label": decision["label"],
            "label_status": LABEL_STATUS,
            "current_output": case["current_output"],
            "source_complete_evidence": {
                "status": evidence.SOURCE_COMPLETE_ELIGIBLE,
                "case_fingerprint": case["case_fingerprint"],
                "judge_evidence": case["source"],
            },
            "reference_truth": {
                "policy": POLICY,
                "reviewer": reviewer,
                "rationale": decision["rationale"],
                "error_dimensions": decision["error_dimensions"],
                "required_repair": decision.get("required_repair"),
            },
        })
    gold = {
        "schema_version": 1,
        "reference_truth_policy": POLICY,
        "reviewer": reviewer,
        "source_complete_cases": len(gold_cases),
        "label_counts": dict(sorted(labels.items())),
        "cases": gold_cases,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"policy": POLICY, "cases": len(gold_cases),
            "label_counts": dict(sorted(labels.items())), "gold": str(out)}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--store", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--store", type=Path, required=True)
    import_parser.add_argument("--answer", type=Path, required=True)
    import_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (export_packet(args.store, args.out) if args.command == "export"
                  else import_answer(args.store, args.answer, args.out))
    except ReferenceValidationError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
