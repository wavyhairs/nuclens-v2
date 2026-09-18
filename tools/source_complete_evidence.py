"""Source-complete evidence store for future Curation P4 calibration.

This module is evaluation infrastructure only.  It has no network transport and
does not alter production state.  A case is persisted only after an in-memory,
fail-closed validation proves that the source text, exact production inputs,
serialized request, raw/parsed output, and judge-visible evidence are mutually
consistent.

Historical Curation Gold remains useful provenance, but title-only historical
rows are deliberately not promoted into this store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import replay_inputs

SCHEMA_VERSION = 1
PROVENANCE_VERSION = "curation-source-complete-v1"
HISTORICAL_ONLY = "HISTORICAL_ONLY"
SOURCE_COMPLETE_ELIGIBLE = "SOURCE_COMPLETE_CALIBRATION_ELIGIBLE"
UNSCORABLE = "UNSCORABLE_MISSING_EVIDENCE"
DEFAULT_RETENTION_DAYS = 21

EVALUATION_FIELDS = (
    "source.title",
    "source.description",
    "source.body",
    "source.publisher",
    "source.domain",
    "source.published_at",
    "production_output.normalized_output",
)

RISK_BUCKETS = (
    "event_boundary", "scope", "stage", "date", "causality",
    "unsupported_inference", "omission", "normal_pass_like",
    "historical_error_prone",
)

_SECRET_KEYS = {
    "api_key", "apikey", "authorization", "x-goog-api-key",
    "proxy-authorization", "cookie", "set-cookie", "client_secret",
    "access_token", "refresh_token",
}
_SECRET_PATTERNS = (
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\bBearer\s+[0-9A-Za-z._~+/-]{12,}\b", re.IGNORECASE),
)
_FORBIDDEN_SELECTION_KEYS = {"gold", "gold_label", "human_label", "verdict", "answer"}


class EvidenceValidationError(ValueError):
    """A candidate cannot be promoted without changing its evidence."""


@dataclass(frozen=True)
class GateResult:
    source_complete: bool
    status: str
    missing: tuple[str, ...]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_complete": self.source_complete,
            "status": self.status,
            "missing": list(self.missing),
            "errors": list(self.errors),
        }


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def request_fingerprint(payload: dict) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def body_fingerprint(body: str) -> str:
    """Hash the exact prompt text.  Newline or whitespace changes are material."""
    return sha256_bytes(body.encode("utf-8"))


def _secret_findings(value: Any, path: str = "$" ) -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).strip().lower() in _SECRET_KEYS:
                findings.append(f"forbidden secret key at {child_path}")
            findings.extend(_secret_findings(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_secret_findings(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(f"secret-like value at {path}")
                break
    return findings


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _raw_items(raw_model_output: str) -> list[dict] | None:
    try:
        value = json.loads(raw_model_output)
    except (TypeError, json.JSONDecodeError):
        return None
    items = value.get("items") if isinstance(value, dict) else None
    if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
        return None
    return items


def _provider_model_text(response: dict) -> str:
    try:
        return str(response["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError):
        return ""


def _request_user_text(request_payload: dict) -> str:
    try:
        parts = request_payload["contents"][0]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))


def completeness_gate(payload: dict) -> GateResult:
    """Validate a not-yet-persisted candidate and fail closed on every gap."""
    missing: list[str] = []
    errors: list[str] = []

    def require_text(container: dict, key: str, path: str) -> str:
        value = container.get(key)
        if not _nonempty(value):
            missing.append(f"{path}.{key}")
            return ""
        return str(value)

    case_id = require_text(payload, "case_id", "$" )
    source = payload.get("source")
    production = payload.get("production")
    output = payload.get("output")
    evaluation = payload.get("evaluation_evidence")
    selection = payload.get("selection_metadata")
    request = payload.get("request_payload")
    for name, value in (
        ("source", source), ("production", production), ("output", output),
        ("evaluation_evidence", evaluation), ("selection_metadata", selection),
        ("request_payload", request),
    ):
        if not isinstance(value, dict):
            missing.append(f"$.{name}")

    if not all(isinstance(value, dict) for value in
               (source, production, output, evaluation, selection, request)):
        errors.extend(_secret_findings(payload))
        return GateResult(False, UNSCORABLE, tuple(sorted(set(missing))),
                          tuple(sorted(set(errors))))

    source_hash = require_text(source, "source_hash", "$.source")
    for field in (
        "canonical_url", "publisher", "domain", "published_at", "captured_at",
        "source_type", "provenance_version", "title", "description", "body",
        "body_origin", "body_extraction_method", "body_extraction_version",
    ):
        require_text(source, field, "$.source")
    if source.get("provenance_version") != PROVENANCE_VERSION:
        errors.append("unsupported provenance_version")
    if source.get("canonical_url") and not str(source["canonical_url"]).startswith(("http://", "https://")):
        errors.append("canonical_url is not HTTP(S)")

    article = production.get("article")
    reports = production.get("reports_context")
    batch = production.get("batch")
    if not isinstance(article, dict):
        missing.append("$.production.article")
        article = {}
    if not isinstance(reports, list):
        missing.append("$.production.reports_context")
    if not isinstance(batch, dict):
        missing.append("$.production.batch")
        batch = {}
    builder = require_text(production, "request_builder_fingerprint", "$.production")
    order = batch.get("article_hashes")
    position = batch.get("position")
    if not isinstance(order, list) or not order:
        missing.append("$.production.batch.article_hashes")
    if not isinstance(position, int):
        missing.append("$.production.batch.position")
    elif isinstance(order, list) and (position < 0 or position >= len(order)):
        errors.append("batch position is outside article_hashes")
    elif isinstance(order, list) and source_hash and order[position] != source_hash:
        errors.append("batch position does not map to source_hash")
    if article.get("hash") != source_hash:
        errors.append("production article hash does not map to source_hash")
    for field in ("title", "description"):
        if article.get(field) != source.get(field):
            errors.append(f"production article {field} differs from source evidence")
    if not builder:
        missing.append("$.production.request_builder_fingerprint")

    user_text = _request_user_text(request)
    if not user_text:
        missing.append("$.request_payload.contents[0].parts.text")
    else:
        for label, expected in (
            ("hash tag", source_hash[:8]), ("title", source.get("title")),
            ("description", source.get("description")), ("body", source.get("body")),
        ):
            if expected and str(expected) not in user_text:
                errors.append(f"serialized request does not contain exact source {label}")

    raw_model_output = require_text(output, "raw_model_output", "$.output")
    provider_response = output.get("provider_response")
    parsed_items = output.get("parsed_items")
    normalized = output.get("normalized_output")
    parser_result = output.get("parser_result")
    lifecycle = output.get("lifecycle")
    validation = output.get("validation")
    if not isinstance(provider_response, dict):
        missing.append("$.output.provider_response")
    if not isinstance(parsed_items, list):
        missing.append("$.output.parsed_items")
    if not isinstance(normalized, dict) or not normalized:
        missing.append("$.output.normalized_output")
    if not isinstance(parser_result, dict):
        missing.append("$.output.parser_result")
        parser_result = {}
    if not isinstance(lifecycle, dict):
        missing.append("$.output.lifecycle")
        lifecycle = {}
    if not isinstance(validation, dict):
        missing.append("$.output.validation")
        validation = {}
    raw_items = _raw_items(raw_model_output) if raw_model_output else None
    if isinstance(provider_response, dict) and raw_model_output:
        if _provider_model_text(provider_response) != raw_model_output:
            errors.append("provider response does not contain the exact raw_model_output")
    if raw_items is None:
        errors.append("raw_model_output is not an items JSON object")
    elif parsed_items != raw_items:
        errors.append("raw_model_output items do not equal parsed_items")
    if isinstance(parsed_items, list) and source_hash:
        matches = [row for row in parsed_items if str(row.get("id") or "") == source_hash[:8]]
        if len(matches) != 1:
            errors.append("parsed_items do not map exactly once to source_hash")
    if parser_result.get("status") != "PASS":
        errors.append("parser_result is not PASS")
    for field in ("regenerated", "split", "quarantined", "lost"):
        if field not in lifecycle:
            missing.append(f"$.output.lifecycle.{field}")
    if lifecycle.get("quarantined") or lifecycle.get("lost"):
        errors.append("quarantined or lost output is not calibration eligible")
    if validation.get("status") != "PASS":
        errors.append("production validation is not PASS")

    included = evaluation.get("included_fields")
    if included != list(EVALUATION_FIELDS):
        errors.append("evaluation evidence subset is not the fixed explicit field list")
    if set(selection).intersection(_FORBIDDEN_SELECTION_KEYS):
        errors.append("selection metadata contains a Gold answer")
    buckets = selection.get("risk_buckets")
    if not isinstance(buckets, list) or not buckets:
        missing.append("$.selection_metadata.risk_buckets")
    elif not set(buckets).issubset(RISK_BUCKETS):
        errors.append("selection metadata contains an unknown risk bucket")

    errors.extend(_secret_findings(payload))
    complete = not missing and not errors and bool(case_id)
    return GateResult(complete, SOURCE_COMPLETE_ELIGIBLE if complete else UNSCORABLE,
                      tuple(sorted(set(missing))), tuple(sorted(set(errors))))


def _put_blob(root: Path, kind: str, data: bytes, suffix: str) -> dict[str, Any]:
    digest = sha256_bytes(data)
    relative = Path("blobs") / kind / f"{digest}{suffix}"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise EvidenceValidationError(f"content-address collision at {relative}")
    else:
        path.write_bytes(data)
    return {"kind": kind, "sha256": digest, "bytes": len(data),
            "path": relative.as_posix()}


def _read_blob(root: Path, reference: dict) -> bytes:
    path = root / str(reference.get("path") or "")
    data = path.read_bytes()
    if sha256_bytes(data) != reference.get("sha256") or len(data) != reference.get("bytes"):
        raise EvidenceValidationError(f"blob integrity mismatch: {path}")
    return data


def promote_case(payload: dict, store: Path) -> dict:
    """Persist one selected case; incomplete candidates leave no case manifest."""
    gate = completeness_gate(payload)
    if not gate.source_complete:
        return gate.as_dict()
    store = Path(store)
    source = payload["source"]
    output = payload["output"]
    body_ref = _put_blob(store, "body", source["body"].encode("utf-8"), ".txt")
    request_ref = _put_blob(store, "request", canonical_json_bytes(payload["request_payload"]), ".json")
    response_ref = _put_blob(store, "response", canonical_json_bytes(output["provider_response"]), ".json")
    raw_output_ref = _put_blob(store, "model-output", output["raw_model_output"].encode("utf-8"), ".json")

    document = {
        "schema_version": SCHEMA_VERSION,
        "case_id": payload["case_id"],
        "history_status": None,
        "source_complete_status": SOURCE_COMPLETE_ELIGIBLE,
        "calibration_eligible": True,
        "source_provenance": {
            key: source[key] for key in (
                "source_hash", "canonical_url", "publisher", "domain", "published_at",
                "captured_at", "source_type", "provenance_version",
            )
        },
        "source_content": {
            "title": source["title"], "description": source["description"],
            "body_ref": body_ref, "body_origin": source["body_origin"],
            "body_extraction_method": source["body_extraction_method"],
            "body_extraction_version": source["body_extraction_version"],
        },
        "production_input": payload["production"],
        "serialized_request": {
            "ref": request_ref,
            "canonicalization": "utf8-json-sort-keys-compact-v1",
            "fingerprint": request_fingerprint(payload["request_payload"]),
        },
        "production_output": {
            "provider_response_ref": response_ref,
            "raw_model_output_ref": raw_output_ref,
            "parsed_items": output["parsed_items"],
            "normalized_output": output["normalized_output"],
            "parser_result": output["parser_result"],
            "lifecycle": output["lifecycle"],
            "validation": output["validation"],
        },
        "evaluation_evidence": {
            "included_fields": list(EVALUATION_FIELDS),
            "source": {
                "title": source["title"], "description": source["description"],
                "body_ref": body_ref, "publisher": source["publisher"],
                "domain": source["domain"], "published_at": source["published_at"],
            },
            "production_output": {"normalized_output": output["normalized_output"]},
        },
        "selection_metadata_not_gold": payload["selection_metadata"],
        "storage": {
            "deduplication": "sha256-content-addressed-v1",
            "html_stored": False, "images_stored": False,
        },
    }
    document["case_fingerprint"] = sha256_bytes(canonical_json_bytes(document))
    validated = validate_case_document(document, store)
    if not validated.source_complete:
        raise EvidenceValidationError(json.dumps(validated.as_dict(), ensure_ascii=False))
    case_path = store / "cases" / f"{payload['case_id']}.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if case_path.exists() and case_path.read_text(encoding="utf-8") != encoded:
        raise EvidenceValidationError(f"refusing to overwrite different case: {case_path}")
    case_path.write_text(encoded, encoding="utf-8")
    return {**gate.as_dict(), "case_path": str(case_path),
            "case_fingerprint": document["case_fingerprint"]}


def validate_case_document(document: dict, store: Path) -> GateResult:
    missing: list[str] = []
    errors: list[str] = []
    if document.get("source_complete_status") != SOURCE_COMPLETE_ELIGIBLE:
        errors.append("case status is not source-complete eligible")
    if document.get("calibration_eligible") is not True:
        errors.append("calibration_eligible is not true")
    try:
        body = _read_blob(store, document["source_content"]["body_ref"]).decode("utf-8")
        request = json.loads(_read_blob(store, document["serialized_request"]["ref"]))
        response = json.loads(_read_blob(store, document["production_output"]["provider_response_ref"]))
        raw = _read_blob(store, document["production_output"]["raw_model_output_ref"]).decode("utf-8")
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, EvidenceValidationError) as exc:
        missing.append("content-addressed evidence blob")
        errors.append(str(exc))
        return GateResult(False, UNSCORABLE, tuple(missing), tuple(errors))
    expected = document["serialized_request"].get("fingerprint")
    if request_fingerprint(request) != expected:
        errors.append("serialized request fingerprint mismatch")
    stored_case_fingerprint = document.get("case_fingerprint")
    fingerprint_input = {key: value for key, value in document.items()
                         if key != "case_fingerprint"}
    if sha256_bytes(canonical_json_bytes(fingerprint_input)) != stored_case_fingerprint:
        errors.append("case fingerprint mismatch")
    if _raw_items(raw) != document["production_output"].get("parsed_items"):
        errors.append("raw output no longer maps to parsed_items")
    if _provider_model_text(response) != raw:
        errors.append("provider response no longer maps to raw model output")
    if body_fingerprint(body) != document["source_content"]["body_ref"].get("sha256"):
        errors.append("body hash mismatch")
    if _secret_findings(document) or _secret_findings(request) or _secret_findings(response):
        errors.append("secret-like material found in persisted evidence")
    evaluation = document.get("evaluation_evidence") or {}
    if evaluation.get("included_fields") != list(EVALUATION_FIELDS):
        errors.append("evaluation evidence subset drifted")
    complete = not missing and not errors
    return GateResult(complete, SOURCE_COMPLETE_ELIGIBLE if complete else UNSCORABLE,
                      tuple(sorted(set(missing))), tuple(sorted(set(errors))))


def historical_registry(gold: dict) -> dict:
    """Mark old title-only Gold as history without changing any Gold label."""
    rows = []
    historical_calibration_statuses = {
        "USER_SPECIFIED", "HUMAN_LABELLED", "HUMAN_BLIND_CONFIRMED",
        "HUMAN_BLIND_CORRECTED",
    }
    for case in gold.get("cases") or []:
        if (not case.get("human_label")
                or case.get("label_status") not in historical_calibration_statuses):
            continue
        rows.append({
            "case_id": case.get("id"),
            "history_status": HISTORICAL_ONLY,
            "source_complete_status": UNSCORABLE,
            "calibration_eligible": False,
            "missing_evidence": [
                "source.description", "source.body", "production.article_object",
                "production.reports_context", "production.batch",
                "serialized_request", "raw_model_output", "parser_lifecycle",
            ],
            "preserved_human_label": case.get("human_label"),
            "preserved_label_status": case.get("label_status"),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": PROVENANCE_VERSION,
        "cases": rows,
        "counts": {
            HISTORICAL_ONLY: len(rows),
            SOURCE_COMPLETE_ELIGIBLE: 0,
            UNSCORABLE: len(rows),
        },
    }


def risk_buckets(title: str, description: str, body: str, output: str = "") -> list[str]:
    source = " ".join((title, description, body))
    combined = f"{source} {output}"
    buckets: set[str] = set()
    if any(token in combined for token in ("·", " 및 ", "동시에", "한편", ";")):
        buckets.add("event_boundary")
    if any(token in combined for token in ("한국", "미국", "유럽", "글로벌", "해외", "국내")):
        buckets.add("scope")
    if any(token in combined for token in ("계획", "검토", "예정", "승인", "허가", "착공", "운영", "협상", "계약")):
        buckets.add("stage")
    if any(char.isdigit() for char in combined):
        buckets.add("date")
    if any(token in combined for token in ("때문", "따라", "영향", "원인", "결과", "전망")):
        buckets.add("causality")
    source_terms = set(re.findall(r"[0-9A-Za-z가-힣]{3,}", source.lower()))
    output_terms = set(re.findall(r"[0-9A-Za-z가-힣]{3,}", output.lower()))
    if output_terms - source_terms:
        buckets.add("unsupported_inference")
    if len(source) > 600 and len(output) < max(80, len(source) // 8):
        buckets.add("omission")
    if not buckets.intersection({"event_boundary", "stage", "causality", "unsupported_inference"}):
        buckets.add("normal_pass_like")
    if buckets.intersection({"event_boundary", "scope", "stage", "date", "causality"}):
        buckets.add("historical_error_prone")
    return sorted(buckets)


def select_balanced_candidates(candidates: Iterable[dict], target: int = 24) -> list[dict]:
    """Deterministic risk balancing; selection metadata never contains an answer."""
    remaining = [dict(row) for row in candidates]
    selected: list[dict] = []
    counts: Counter[str] = Counter()
    while remaining and len(selected) < target:
        def rank(row: dict) -> tuple[float, str]:
            buckets = set(row.get("risk_buckets") or ())
            balance = sum(1.0 / (1 + counts[name]) for name in buckets)
            digest = hashlib.sha256(str(row.get("case_id") or "").encode("utf-8")).hexdigest()
            return balance, digest
        choice = max(remaining, key=rank)
        remaining.remove(choice)
        selected.append(choice)
        counts.update(choice.get("risk_buckets") or ())
    return selected


def _response_text(record: dict) -> str:
    try:
        return str(record["response"]["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError):
        return ""


def estimate_capture_storage(records: Iterable[dict]) -> dict:
    """Measure old captures for sizing only; never declare them calibration eligible."""
    samples: list[dict[str, Any]] = []
    for record in records:
        detail = record.get("detail") or {}
        if detail.get("task") != "curation" or detail.get("retry_count") not in (0, None):
            continue
        request = record.get("request_body") or {}
        raw = _response_text(record)
        parsed = _raw_items(raw)
        if parsed is None:
            continue
        by_tag = {str(row.get("id") or ""): row for row in parsed}
        try:
            blocks = replay_inputs.parse_curation_prompt(_request_user_text(request))
        except ValueError:
            continue
        request_bytes = len(canonical_json_bytes(request))
        response_bytes = len(canonical_json_bytes(record.get("response") or {}))
        for block in blocks:
            if not _nonempty(block.get("description")) or not _nonempty(block.get("body")):
                continue
            item = by_tag.get(block["tag"])
            if item is None:
                continue
            metadata = {
                "title": block["title"], "description": block["description"],
                "source": block["source"], "parsed_item": item,
                "risk_buckets": risk_buckets(block["title"], block["description"],
                                               block["body"], json.dumps(item, ensure_ascii=False)),
            }
            samples.append({
                "body": block["body"].encode("utf-8"),
                "request": canonical_json_bytes(request),
                "response": canonical_json_bytes(record.get("response") or {}),
                "metadata": canonical_json_bytes(metadata),
                "request_bytes": request_bytes, "response_bytes": response_bytes,
            })
    if not samples:
        return {"measurement_basis": "historical_capture_size_only_not_eligible", "cases": 0}
    naive = sum(len(row["body"]) + len(row["request"]) + len(row["response"])
                + len(row["metadata"]) for row in samples)
    body_blobs = {sha256_bytes(row["body"]): row["body"] for row in samples}
    request_blobs = {sha256_bytes(row["request"]): row["request"] for row in samples}
    response_blobs = {sha256_bytes(row["response"]): row["response"] for row in samples}
    metadata_bytes = sum(len(row["metadata"]) for row in samples)
    body_bytes = sum(len(value) for value in body_blobs.values())
    dedup = (body_bytes + sum(len(value) for value in request_blobs.values())
             + sum(len(value) for value in response_blobs.values()) + metadata_bytes)
    mib = 1024 * 1024
    average = dedup / len(samples)
    naive_average = naive / len(samples)
    return {
        "measurement_basis": "historical_capture_size_only_not_eligible",
        "cases": len(samples),
        "unique_bodies": len(body_blobs),
        "unique_requests": len(request_blobs),
        "unique_responses": len(response_blobs),
        "average_case_mib_before_dedup": round(naive_average / mib, 4),
        "average_case_mib_after_dedup": round(average / mib, 4),
        "projected_mib_before_dedup": {
            str(count): round(naive_average * count / mib, 3) for count in (20, 30, 100)
        },
        "projected_mib_after_dedup": {
            str(count): round(average * count / mib, 3) for count in (20, 30, 100)
        },
        "body_share_after_dedup": round(body_bytes / dedup, 4),
        "measured_mib_before_dedup": round(naive / mib, 3),
        "measured_mib_after_dedup": round(dedup / mib, 3),
        "dedup_reduction": round(1 - dedup / naive, 4),
        "note": "Old captures size the store but remain incomplete and ineligible.",
    }


def load_capture_records(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.glob("*/llm_capture.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    historical = sub.add_parser("historical-registry")
    historical.add_argument("--gold", type=Path, required=True)
    historical.add_argument("--out", type=Path, required=True)
    estimate = sub.add_parser("estimate")
    estimate.add_argument("--capture-root", type=Path, required=True)
    estimate.add_argument("--out", type=Path)
    validate = sub.add_parser("validate-store")
    validate.add_argument("--store", type=Path, required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("--input", type=Path, required=True)
    promote.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "historical-registry":
        report = historical_registry(json.loads(args.gold.read_text(encoding="utf-8")))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.command == "estimate":
        report = estimate_capture_storage(load_capture_records(args.capture_root))
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
    elif args.command == "promote":
        report = promote_case(json.loads(args.input.read_text(encoding="utf-8")), args.store)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["source_complete"]:
            return 2
    else:
        rows = []
        for path in sorted((args.store / "cases").glob("*.json")):
            result = validate_case_document(json.loads(path.read_text(encoding="utf-8")), args.store)
            rows.append({"path": str(path), **result.as_dict()})
        report = {"cases": rows, "eligible": sum(row["source_complete"] for row in rows),
                  "api_calls": {"gemini": 0, "openai": 0}}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if any(not row["source_complete"] for row in rows):
            return 2
        return 0
    print(json.dumps({**report, "api_calls": {"gemini": 0, "openai": 0}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
