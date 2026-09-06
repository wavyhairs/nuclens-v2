"""Prepare and validate GPT-5.6 Sol High provisional Gold review packages.

This tool never calls a model. Imported judgments remain AI-assisted provisional
sidecars and are never written to canonical Human Gold fixtures.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.gold_labeler import atomic_write_json

FIXTURES = ROOT / "tests" / "fixtures" / "gemini_reasoning"
DEFAULT_PACKAGE_DIR = ROOT / "docs" / "gold-labeling" / "sol-review"
DEFAULT_SIDECAR_DIR = ROOT / ".eval" / "gold-labels"
CONFIDENCE = {"high", "medium", "low"}
CONTRACTS = {
    "curation": {
        "task": "CURATION", "fixture": "curation_gold.json",
        "labels": {"CORRECT", "INCORRECT", "AMBIGUOUS"},
        "error_types": {"event_boundary", "chronology", "causality", "unit",
                        "stage", "attribution", "unsupported", "overclaim",
                        "other", "none"},
        "critical": {"event_boundary", "chronology", "causality"},
    },
    "semantic": {
        "task": "SEMANTIC", "fixture": "semantic_gold.json",
        "labels": {"SUPPORTED", "UNSUPPORTED", "AMBIGUOUS"},
        "error_types": {"causality", "chronology", "wrong_event_link",
                        "attribution", "unsupported_relation", "overclaim",
                        "contradiction", "other", "none"},
        "critical": {"causality", "chronology", "wrong_event_link",
                     "unsupported_relation"},
    },
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_sha256(path: Path) -> str:
    """Hash candidate/source content while ignoring mutable human-owned fields."""
    payload = deepcopy(_read(path))
    human_fields = {"human_label", "human_dimensions", "human_error_types",
                    "human_notes", "reason_code", "required_repair", "label_status"}
    for case in payload.get("cases") or []:
        for field in human_fields:
            case.pop(field, None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pending_cases(payload: dict) -> list[dict]:
    return [case for case in payload.get("cases") or []
            if case.get("label_status") == "HUMAN_LABEL_REQUIRED"
            and case.get("human_label") is None]


def _review_cues(*values: object) -> dict:
    text = " ".join(str(value or "") for value in values)
    return {
        "dates": sorted(set(re.findall(r"(?:20\d{2}[년./-]\s*)?\d{1,2}[월./-]\s*\d{1,2}일?", text))),
        "reactor_units": sorted(set(re.findall(r"[A-Za-z가-힣·ㆍ -]*\d+(?:[·ㆍ,]\d+)*호기", text))),
        "stages": sorted(set(term for term in (
            "신청", "심사", "승인", "허가", "착공", "건설", "시운전", "정지", "재가동",
            "상업운전", "계약", "발표") if term in text)),
    }


def _archive_rows(hashes: set[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in sorted((ROOT / "archive").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            row = json.loads(line)
            source_hash = row.get("hash")
            if source_hash in hashes and source_hash not in found:
                found[source_hash] = row
        if len(found) == len(hashes): break
    return found


def package_case(task_key: str, case: dict, archive_row: dict | None = None) -> dict:
    if task_key == "curation":
        source = case.get("source_input") or {}
        output = case.get("current_output") or {}
        archive_row = archive_row or {}
        verified = archive_row.get("verified_evidence") or {}
        return {
            "case_id": case["id"],
            "source": {"title": case.get("source_title") or source.get("title"),
                       "url": case.get("source_url") or source.get("url"),
                       "published_at": case.get("published_at") or source.get("published_at"),
                       "publisher": archive_row.get("publisher"),
                       "domain": archive_row.get("domain"),
                       "verified_evidence": verified or None},
            "nuclens_generated": output,
            "review_cues": {
                "actors_entities": verified.get("entities") or [],
                "event_topics": verified.get("topics") or [],
                "verified_stages": verified.get("stages") or [],
                "verified_claims": verified.get("claims") or [],
                "verified_quantities": verified.get("quantities") or {},
                **_review_cues(case.get("source_title"), output),
            },
        }
    source = case.get("source_evidence") or {}
    verified = source.get("verified_evidence") or {}
    return {
        "case_id": case["id"], "generated_statement": case.get("claim"),
        "source_evidence": source,
        "review_cues": {
            "source_date": source.get("published_at"),
            "actors_entities": verified.get("entities") or [],
            "event_topics": verified.get("topics") or [],
            "chronology_claims": verified.get("claims") or
                source.get("contract_facts") or [],
            **_review_cues(case.get("claim"), source),
        },
    }


def output_schema(task_key: str) -> dict:
    contract = CONTRACTS[task_key]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{contract['task']} GPT-5.6 Sol High provisional judgment",
        "type": "object", "additionalProperties": False,
        "required": ["case_id", "provisional_label", "error_types", "confidence",
                     "reason", "evidence_reference"],
        "properties": {
            "case_id": {"type": "string", "minLength": 1},
            "provisional_label": {"enum": sorted(contract["labels"])},
            "error_types": {"type": "array", "minItems": 1, "uniqueItems": True,
                            "items": {"enum": sorted(contract["error_types"])}},
            "confidence": {"enum": sorted(CONFIDENCE)},
            "reason": {"type": "string", "minLength": 1, "maxLength": 600},
            "evidence_reference": {"type": "string", "minLength": 1,
                                   "maxLength": 1000},
        },
        "allOf": [
            {"if": {"properties": {"provisional_label": {
                "enum": ["CORRECT" if task_key == "curation" else "SUPPORTED"]}}},
             "then": {"properties": {"error_types": {"const": ["none"]}}}},
            {"if": {"properties": {"provisional_label": {
                "not": {"enum": ["CORRECT" if task_key == "curation" else
                                   "SUPPORTED"]}}}},
             "then": {"properties": {"error_types": {
                 "not": {"contains": {"const": "none"}}}}}},
        ],
    }


def prepare(task_key: str, out_dir: Path) -> dict:
    contract = CONTRACTS[task_key]
    fixture = FIXTURES / contract["fixture"]
    payload = _read(fixture)
    pending = _pending_cases(payload)
    archive = (_archive_rows({case.get("source_hash") for case in pending
                              if case.get("source_hash")})
               if task_key == "curation" else {})
    cases = [package_case(task_key, case, archive.get(case.get("source_hash")))
             for case in pending]
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = out_dir / f"{task_key}-input.jsonl"
    input_path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n"
                                  for case in cases), encoding="utf-8")
    schema_path = out_dir / f"{task_key}-output.schema.json"
    schema_path.write_text(json.dumps(output_schema(task_key), ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")
    def display(path: Path) -> str:
        try: return str(path.relative_to(ROOT))
        except ValueError: return str(path)
    return {"task": contract["task"], "case_count": len(cases),
            "fixture_sha256": fixture_sha256(fixture),
            "candidate_sha256": candidate_sha256(fixture),
            "input": display(input_path), "schema": display(schema_path)}


def _sentence_count(reason: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?。])\s+|\n+", reason.strip())
                if part.strip()])


def validate_row(task_key: str, row: object, case_ids: set[str]) -> dict:
    contract = CONTRACTS[task_key]
    required = {"case_id", "provisional_label", "error_types", "confidence",
                "reason", "evidence_reference"}
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError(f"row fields must be exactly {sorted(required)}")
    case_id = row.get("case_id")
    if case_id not in case_ids:
        raise ValueError(f"unknown or non-pending case_id: {case_id!r}")
    label = row.get("provisional_label")
    if label not in contract["labels"]:
        raise ValueError(f"invalid provisional_label for {case_id}: {label!r}")
    errors = row.get("error_types")
    if (not isinstance(errors, list) or not errors or len(errors) != len(set(errors))
            or any(value not in contract["error_types"] for value in errors)):
        raise ValueError(f"invalid error_types for {case_id}")
    if "none" in errors and (len(errors) != 1 or label not in {"CORRECT", "SUPPORTED"}):
        raise ValueError(f"none must be the sole error type for a positive label: {case_id}")
    if label in {"CORRECT", "SUPPORTED"} and errors != ["none"]:
        raise ValueError(f"positive provisional label requires error_types=['none']: {case_id}")
    confidence = row.get("confidence")
    if confidence not in CONFIDENCE:
        raise ValueError(f"invalid confidence for {case_id}: {confidence!r}")
    reason = str(row.get("reason") or "").strip()
    if not 1 <= len(reason) <= 600 or not 1 <= _sentence_count(reason) <= 3:
        raise ValueError(f"reason must be 1-3 sentences and <=600 chars: {case_id}")
    evidence = str(row.get("evidence_reference") or "").strip()
    if not 1 <= len(evidence) <= 1000:
        raise ValueError(f"evidence_reference is required and <=1000 chars: {case_id}")
    return {"provisional_label": label, "error_types": errors,
            "confidence": confidence, "reason": reason,
            "evidence_reference": evidence}


def priority(task_key: str, row: dict, packaged: dict) -> tuple[int, list[str]]:
    score, reasons = 0, []
    if row["confidence"] == "low": score += 100; reasons.append("low_confidence")
    elif row["confidence"] == "medium": score += 30; reasons.append("medium_confidence")
    if row["provisional_label"] == "AMBIGUOUS": score += 90; reasons.append("ambiguous")
    critical = sorted(set(row["error_types"]) & CONTRACTS[task_key]["critical"])
    if critical: score += 60; reasons.extend(critical)
    source = packaged.get("source_evidence") or packaged.get("source") or {}
    if isinstance(source, dict) and len(source.get("sources") or []) > 1:
        score += 40; reasons.append("multiple_sources")
    return score, reasons


def read_jsonl(path: Path) -> list[object]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError as exc: raise ValueError(f"line {number}: {exc}") from exc
    return rows


def import_provisional(task_key: str, input_path: Path, out_path: Path) -> dict:
    contract = CONTRACTS[task_key]
    fixture = FIXTURES / contract["fixture"]
    payload = _read(fixture)
    pending = _pending_cases(payload)
    case_by_id = {case["id"]: package_case(task_key, case) for case in pending}
    labels, seen = {}, set()
    if out_path.exists():
        existing = _read(out_path)
        if (existing.get("task") != contract["task"]
                or existing.get("status") != "AI_ASSISTED_PROVISIONAL_NOT_GOLD"
                or existing.get("candidate_sha256") != candidate_sha256(fixture)):
            raise ValueError("existing provisional sidecar is stale or incompatible")
        labels.update(existing.get("labels") or {})
    for raw in read_jsonl(input_path):
        case_id = raw.get("case_id") if isinstance(raw, dict) else None
        if case_id in seen: raise ValueError(f"duplicate case_id in import: {case_id}")
        seen.add(case_id)
        row = validate_row(task_key, raw, set(case_by_id))
        score, reasons = priority(task_key, row, case_by_id[case_id])
        value = {**row, "review_priority": score,
                 "review_reasons": reasons, "human_reviewed": False}
        if case_id in labels and labels[case_id] != value:
            raise ValueError(f"case_id already imported with a different judgment: {case_id}")
        labels[case_id] = value
    sidecar = {"schema_version": 1, "task": contract["task"],
               "status": "AI_ASSISTED_PROVISIONAL_NOT_GOLD",
               "declared_judge": "gpt-5.6-sol-high",
               "fixture_sha256": fixture_sha256(fixture),
               "candidate_sha256": candidate_sha256(fixture),
               "imported_at": datetime.now(timezone.utc).isoformat(),
               "labels": labels}
    atomic_write_json(out_path, sidecar)
    report = provisional_report(sidecar, len(pending), out_path)
    report["imported_this_run"] = len(seen)
    return report


def provisional_report(sidecar: dict, expected: int, path: Path) -> dict:
    rows = list((sidecar.get("labels") or {}).values())
    return {"ok": True, "status": sidecar.get("status"), "imported": len(rows),
            "expected_pending": expected, "remaining": expected - len(rows),
            "label_distribution": dict(Counter(row["provisional_label"] for row in rows)),
            "confidence_distribution": dict(Counter(row["confidence"] for row in rows)),
            "hard_cases": sum(row.get("review_priority", 0) >= 60 for row in rows),
            "sidecar": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare/import Sol provisional Gold reviews")
    parser.add_argument("--task", choices=sorted(CONTRACTS))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_PACKAGE_DIR)
    parser.add_argument("--import-provisional", type=Path)
    parser.add_argument("--sidecar", type=Path)
    args = parser.parse_args()
    try:
        if args.import_provisional:
            if not args.task: parser.error("--task is required with --import-provisional")
            target = args.sidecar or DEFAULT_SIDECAR_DIR / f"{args.task}.sol_provisional.json"
            report = import_provisional(args.task, args.import_provisional, target)
        else:
            tasks = [args.task] if args.task else sorted(CONTRACTS)
            report = {"packages": [prepare(task, args.out_dir) for task in tasks],
                      "model_called": False,
                      "notice": "Inputs only; provisional labels are not Human Gold."}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
