"""Evaluation-only producer for Curation source-complete evidence.

The producer joins two views of the *same* production-equivalent call:

* ``gemini_client.call_json`` supplies the serialized request, provider response,
  raw model text, and parser path through an opt-in in-memory trace sink.
* ``news_bot.curate_batch`` supplies the exact article/context/batch, normalized
  output, validation result, and regeneration/split lifecycle through an opt-in
  evidence sink.

Nothing is written until an in-memory candidate passes ``completeness_gate`` and
is chosen by deterministic risk balancing.  This module has no network transport;
its CLI only plans, reports status, or consolidates validated local stores.
Enabling capture merely observes curation calls that production was already going
to make.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import math
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import source_complete_evidence as evidence

CAPTURE_FLAG = "NUCLENS_SOURCE_COMPLETE_CAPTURE"
STORE_ENV = "NUCLENS_SOURCE_COMPLETE_STORE"
TARGET_ENV = "NUCLENS_SOURCE_COMPLETE_TARGET"
DEFAULT_TARGET = 30
MAX_TARGET = 30
SELECTION_POLICY = "risk-balanced-v1"


class ProducerConfigurationError(ValueError):
    """Capture was explicitly requested with an unsafe or invalid configuration."""


class _TracedResult(dict):
    """A dict-compatible parsed response carrying evaluation-only trace metadata."""

    def __init__(self, value: dict, trace: dict | None):
        super().__init__(value)
        self._source_complete_trace = trace


class ProductionTraceClient:
    """Delegate to the real transport while attaching its one successful trace."""

    def __init__(self, delegate: Callable[..., dict]):
        self.delegate = delegate
        self.calls = 0
        self.trace_failures = 0

    def __call__(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        if "trace_sink" in kwargs:
            raise ProducerConfigurationError("trace_sink is owned by ProductionTraceClient")
        traces: list[dict] = []
        self.calls += 1
        result = self.delegate(
            system_prompt, user_message, trace_sink=traces.append, **kwargs)
        trace = traces[0] if len(traces) == 1 else None
        if trace is None:
            self.trace_failures += 1
        return _TracedResult(result, trace)


def _source_fingerprint(*objects: object) -> str:
    text = "\n\n".join(inspect.getsource(obj) for obj in objects)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def request_builder_fingerprint() -> str:
    import news_bot

    return "curation-request-builder-v1-" + _source_fingerprint(news_bot.curate_batch)


def body_extraction_fingerprint() -> str:
    import article_body

    return "article-body-v1-" + _source_fingerprint(
        article_body.extract_text, article_body.fetch_one, article_body.fetch_bodies)


def production_body_provenance(articles: list[dict], bodies: dict[str, str]) -> dict[str, dict]:
    """Describe bodies that came from the production ``article_body`` path."""
    version = body_extraction_fingerprint()
    return {
        str(article.get("hash") or ""): {
            "body_origin": "article_body.fetch_bodies",
            "body_extraction_method": "fetch_one->extract_text->matches_title",
            "body_extraction_version": version,
        }
        for article in articles
        if article.get("hash") in bodies and bodies.get(str(article.get("hash")))
    }


def _timestamp(epoch: object) -> str:
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _published_at(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "").strip()


def _domain(article: dict, canonical_url: str) -> str:
    return str(article.get("domain") or urlparse(canonical_url).netloc).strip()


def _safe_rejection(case_id: str, reason: str, gate: dict | None = None) -> dict:
    row: dict[str, Any] = {"case_id": case_id, "reason": reason}
    if gate:
        row["status"] = gate.get("status")
        row["missing"] = list(gate.get("missing") or ())
        row["errors"] = list(gate.get("errors") or ())
    return row


def _store_inventory(store: Path) -> dict:
    case_dir = store / "cases"
    rows: list[dict] = []
    counts: Counter[str] = Counter()
    ids: set[str] = set()
    for path in sorted(case_dir.glob("*.json")) if case_dir.exists() else []:
        document = json.loads(path.read_text(encoding="utf-8"))
        validation = evidence.validate_case_document(document, store)
        if not validation.source_complete:
            raise evidence.EvidenceValidationError(
                f"existing store is invalid at {path}: "
                + json.dumps(validation.as_dict(), ensure_ascii=False))
        case_id = str(document.get("case_id") or "")
        ids.add(case_id)
        buckets = list((document.get("selection_metadata_not_gold") or {}).get(
            "risk_buckets") or ())
        counts.update(buckets)
        rows.append({"case_id": case_id, "risk_buckets": buckets, "path": str(path)})
    return {"cases": rows, "ids": ids, "risk_counts": counts}


class SourceCompleteProducer:
    """Build candidates in memory and promote only balanced, complete cases."""

    def __init__(self, store: Path, *, target: int = DEFAULT_TARGET,
                 body_provenance: dict[str, dict] | None = None):
        if not 1 <= target <= MAX_TARGET:
            raise ProducerConfigurationError(
                f"target must be between 1 and {MAX_TARGET}; got {target}")
        self.store = Path(store)
        self.target = target
        self.body_provenance = copy.deepcopy(body_provenance or {})
        self.candidates: list[dict] = []
        self.rejections: list[dict] = []
        self._candidate_ids: set[str] = set()
        self.trace_client: ProductionTraceClient | None = None

    def traced_client(self, delegate: Callable[..., dict]) -> ProductionTraceClient:
        client = ProductionTraceClient(delegate)
        self.trace_client = client
        return client

    def record_curation_event(self, event: dict) -> None:
        """Join transport and curation state without writing the raw candidate."""
        normalized = event.get("normalized_outputs")
        if not isinstance(normalized, dict) or not normalized:
            return
        trace = event.get("transport_trace")
        if not isinstance(trace, dict):
            for source_hash in sorted(normalized):
                self.rejections.append(_safe_rejection(
                    f"curation-{source_hash[:16]}", "missing_transport_trace"))
            return
        parsed_output = trace.get("parsed_output")
        if parsed_output != dict(event.get("parsed_output") or {}):
            for source_hash in sorted(normalized):
                self.rejections.append(_safe_rejection(
                    f"curation-{source_hash[:16]}", "transport_curation_output_mismatch"))
            return

        articles = event.get("articles")
        reports = event.get("reports_context")
        bodies = event.get("bodies")
        if not isinstance(articles, list):
            articles = []
        if not isinstance(reports, list):
            reports = None
        if not isinstance(bodies, dict):
            bodies = {}
        by_hash = {
            str(row.get("hash") or ""): row
            for row in articles if isinstance(row, dict)
        }
        article_hashes = [str(row.get("hash") or "") for row in articles]
        raw_output = str(trace.get("raw_model_output") or "")
        trace_parsed = trace.get("parsed_output") if isinstance(trace.get("parsed_output"), dict) else {}
        parsed_items = trace_parsed.get("items")
        for source_hash, normalized_output in normalized.items():
            source_hash = str(source_hash)
            article = by_hash.get(source_hash) or {}
            try:
                position = article_hashes.index(source_hash)
            except ValueError:
                position = -1
            request = trace.get("request_payload")
            request_hash = evidence.request_fingerprint(request) if isinstance(request, dict) else ""
            case_id = f"curation-{source_hash[:16]}-{request_hash[:12]}"
            if case_id in self._candidate_ids:
                continue
            provenance = self.body_provenance.get(source_hash) or {}
            canonical_url = str(article.get("resolved_url") or article.get("link") or "")
            output_text = json.dumps(normalized_output, ensure_ascii=False, sort_keys=True)
            buckets = evidence.risk_buckets(
                str(article.get("title") or ""),
                str(article.get("description") or ""),
                str(bodies.get(source_hash) or ""),
                output_text,
            )
            parser_result = copy.deepcopy(trace.get("parser_result") or {})
            parser_result["matched_by"] = (event.get("matched_by") or {}).get(source_hash)
            payload = {
                "case_id": case_id,
                "source": {
                    "source_hash": source_hash,
                    "canonical_url": canonical_url,
                    "publisher": str(article.get("publisher") or article.get("site_name")
                                     or article.get("domain") or ""),
                    "domain": _domain(article, canonical_url),
                    "published_at": _published_at(article.get("pub") or article.get("published_at")),
                    "captured_at": _timestamp(trace.get("captured_at_epoch")),
                    "source_type": str(article.get("source_type") or article.get("feed") or "article"),
                    "provenance_version": evidence.PROVENANCE_VERSION,
                    "title": str(article.get("title") or ""),
                    "description": str(article.get("description") or ""),
                    "body": str(bodies.get(source_hash) or ""),
                    "body_origin": str(provenance.get("body_origin") or ""),
                    "body_extraction_method": str(
                        provenance.get("body_extraction_method") or ""),
                    "body_extraction_version": str(
                        provenance.get("body_extraction_version") or ""),
                },
                "production": {
                    "article": copy.deepcopy(article),
                    "reports_context": copy.deepcopy(reports),
                    "batch": {"article_hashes": article_hashes, "position": position},
                    "request_builder_fingerprint": request_builder_fingerprint(),
                },
                "request_payload": copy.deepcopy(request),
                "output": {
                    "provider_response": copy.deepcopy(trace.get("provider_response")),
                    "raw_model_output": raw_output,
                    "parsed_items": copy.deepcopy(parsed_items),
                    "normalized_output": copy.deepcopy(normalized_output),
                    "parser_result": parser_result,
                    "lifecycle": copy.deepcopy(event.get("lifecycle") or {}),
                    "validation": {"status": "PASS", "errors": []},
                },
                "evaluation_evidence": {
                    "included_fields": list(evidence.EVALUATION_FIELDS),
                },
                "selection_metadata": {
                    "risk_buckets": buckets,
                    "selection_policy": SELECTION_POLICY,
                },
            }
            gate = evidence.completeness_gate(payload)
            if not gate.source_complete:
                self.rejections.append(_safe_rejection(
                    case_id, "completeness_gate", gate.as_dict()))
                continue
            try:
                evidence.canonical_json_bytes(payload)
            except (TypeError, ValueError) as exc:
                self.rejections.append(_safe_rejection(
                    case_id, f"candidate_not_json_serializable:{type(exc).__name__}"))
                continue
            self._candidate_ids.add(case_id)
            self.candidates.append(payload)

    def finalize(self) -> dict:
        """Select against existing risk coverage and persist at most ``target`` cases."""
        inventory = _store_inventory(self.store)
        remaining = max(0, self.target - len(inventory["cases"]))
        selectable = [
            {"case_id": row["case_id"],
             "risk_buckets": row["selection_metadata"]["risk_buckets"],
             "payload": row}
            for row in self.candidates
            if row["case_id"] not in inventory["ids"]
        ]
        selected = evidence.select_balanced_candidates(
            selectable, target=remaining, initial_counts=inventory["risk_counts"])
        promoted: list[dict] = []
        for row in selected:
            result = evidence.promote_case(row["payload"], self.store)
            if not result.get("source_complete"):
                raise evidence.EvidenceValidationError(
                    "candidate passed the in-memory gate but promotion failed: "
                    + json.dumps(result, ensure_ascii=False))
            promoted.append({
                "case_id": row["case_id"],
                "risk_buckets": row["risk_buckets"],
                "case_path": result["case_path"],
                "case_fingerprint": result["case_fingerprint"],
            })
        after = _store_inventory(self.store)
        return {
            "status": "TARGET_REACHED" if len(after["cases"]) >= self.target else "ACCUMULATING",
            "target": self.target,
            "existing_before": len(inventory["cases"]),
            "complete_candidates_in_memory": len(self.candidates),
            "rejected_candidates": len(self.rejections),
            "rejections": self.rejections,
            "promoted": promoted,
            "eligible_after": len(after["cases"]),
            "risk_counts_after": dict(sorted(after["risk_counts"].items())),
            "observed_production_curation_calls": (
                self.trace_client.calls if self.trace_client else 0),
            "trace_failures": self.trace_client.trace_failures if self.trace_client else 0,
            "incremental_api_calls": {"gemini": 0, "openai": 0},
            "production_behavior_changed": False,
        }


def producer_from_environment(articles: list[dict], bodies: dict[str, str]) -> SourceCompleteProducer | None:
    """Create the dormant producer only for explicit, bounded capture mode."""
    if os.environ.get(CAPTURE_FLAG, "").strip().lower() != "on":
        return None
    raw_store = os.environ.get(STORE_ENV, "").strip()
    if not raw_store:
        raise ProducerConfigurationError(f"{STORE_ENV} is required when {CAPTURE_FLAG}=on")
    store = Path(raw_store)
    if not store.is_absolute():
        store = ROOT / store
    store = store.resolve()
    allowed = (ROOT / ".eval").resolve()
    if not store.is_relative_to(allowed):
        raise ProducerConfigurationError(
            f"source-complete store must stay below {allowed}; got {store}")
    try:
        target = int(os.environ.get(TARGET_ENV, str(DEFAULT_TARGET)))
    except ValueError as exc:
        raise ProducerConfigurationError(f"{TARGET_ENV} must be an integer") from exc
    return SourceCompleteProducer(
        store, target=target,
        body_provenance=production_body_provenance(articles, bodies))


def collection_plan(store: Path, target: int) -> dict:
    if not 1 <= target <= MAX_TARGET:
        raise ProducerConfigurationError(
            f"target must be between 1 and {MAX_TARGET}; got {target}")
    inventory = _store_inventory(store)
    remaining = max(0, target - len(inventory["cases"]))
    try:
        import news_bot
        batch_size = int(news_bot.BATCH_CHUNK)
    except (ImportError, AttributeError, TypeError, ValueError):
        batch_size = 15
    return {
        "status": "TARGET_REACHED" if remaining == 0 else "READY_TO_ACCUMULATE",
        "store": str(store),
        "target": target,
        "eligible": len(inventory["cases"]),
        "remaining": remaining,
        "risk_counts": dict(sorted(inventory["risk_counts"].items())),
        "collection_mode": "passive_on_existing_production_curation_calls",
        "minimum_future_successful_batches": math.ceil(remaining / batch_size),
        "incremental_api_calls": {"gemini": 0, "openai": 0},
        "live_api_calls_this_command": {"gemini": 0, "openai": 0},
        "cost_estimate_usd": None,
        "cost_note": (
            "Capture adds no calls. Before enabling a real run, report that run's normal "
            "curation call estimate/token cost and obtain explicit approval."),
        "activation": {
            CAPTURE_FLAG: "on",
            STORE_ENV: str(store),
            TARGET_ENV: str(target),
        },
        "forbidden_in_this_stage": [
            "Gold judgment", "judge calibration", "Gemini reasoning canary",
            "production reasoning activation",
        ],
    }


def _document_references(document: dict) -> list[dict]:
    return [
        document["source_content"]["body_ref"],
        document["serialized_request"]["ref"],
        document["production_output"]["provider_response_ref"],
        document["production_output"]["raw_model_output_ref"],
    ]


def _copy_selected_document(document: dict, source: Path, destination: Path) -> Path:
    for reference in _document_references(document):
        relative = Path(str(reference["path"]))
        source_path = source / relative
        destination_path = destination / relative
        data = source_path.read_bytes()
        if (evidence.sha256_bytes(data) != reference.get("sha256")
                or len(data) != reference.get("bytes")):
            raise evidence.EvidenceValidationError(
                f"source blob does not match manifest: {source_path}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists() and destination_path.read_bytes() != data:
            raise evidence.EvidenceValidationError(
                f"destination content-address collision: {destination_path}")
        if not destination_path.exists():
            shutil.copyfile(source_path, destination_path)
    case_path = destination / "cases" / f"{document['case_id']}.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if case_path.exists() and case_path.read_text(encoding="utf-8") != encoded:
        raise evidence.EvidenceValidationError(
            f"refusing to overwrite different consolidated case: {case_path}")
    case_path.write_text(encoded, encoding="utf-8")
    validation = evidence.validate_case_document(document, destination)
    if not validation.source_complete:
        raise evidence.EvidenceValidationError(
            f"consolidated case failed validation: {case_path}: "
            + json.dumps(validation.as_dict(), ensure_ascii=False))
    return case_path


def consolidate_stores(input_stores: list[Path], destination: Path,
                       target: int = DEFAULT_TARGET) -> dict:
    """Select at most ``target`` validated cases from temporary stores."""
    if not input_stores:
        raise ProducerConfigurationError("at least one --input-store is required")
    if not 1 <= target <= MAX_TARGET:
        raise ProducerConfigurationError(
            f"target must be between 1 and {MAX_TARGET}; got {target}")
    destination = Path(destination)
    existing = _store_inventory(destination)
    remaining = max(0, target - len(existing["cases"]))
    by_id: dict[str, dict] = {}
    for source in input_stores:
        source = Path(source)
        inventory = _store_inventory(source)
        for row in inventory["cases"]:
            case_id = row["case_id"]
            document = json.loads(Path(row["path"]).read_text(encoding="utf-8"))
            previous = by_id.get(case_id)
            if (previous is not None
                    and previous["document"].get("case_fingerprint")
                    != document.get("case_fingerprint")):
                raise evidence.EvidenceValidationError(
                    f"conflicting temporary cases share id {case_id}")
            by_id[case_id] = {
                "case_id": case_id,
                "risk_buckets": row["risk_buckets"],
                "document": document,
                "source_store": source,
            }
    candidates = [row for case_id, row in sorted(by_id.items())
                  if case_id not in existing["ids"]]
    selected = evidence.select_balanced_candidates(
        candidates, target=remaining, initial_counts=existing["risk_counts"])
    copied = []
    for row in selected:
        path = _copy_selected_document(
            row["document"], row["source_store"], destination)
        copied.append({
            "case_id": row["case_id"],
            "risk_buckets": row["risk_buckets"],
            "case_path": str(path),
        })
    after = _store_inventory(destination)
    return {
        "status": "TARGET_REACHED" if len(after["cases"]) >= target else "ACCUMULATING",
        "target": target,
        "temporary_stores": [str(Path(path)) for path in input_stores],
        "validated_temporary_candidates": len(by_id),
        "existing_before": len(existing["cases"]),
        "copied": copied,
        "eligible_after": len(after["cases"]),
        "risk_counts_after": dict(sorted(after["risk_counts"].items())),
        "api_calls": {"gemini": 0, "openai": 0},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status"):
        command = commands.add_parser(name)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--target", type=int, default=DEFAULT_TARGET)
    consolidate = commands.add_parser("consolidate")
    consolidate.add_argument("--input-store", action="append", type=Path, required=True)
    consolidate.add_argument("--store", type=Path, required=True)
    consolidate.add_argument("--target", type=int, default=DEFAULT_TARGET)
    args = parser.parse_args()
    report = (consolidate_stores(args.input_store, args.store, args.target)
              if args.command == "consolidate"
              else collection_plan(args.store, args.target))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
