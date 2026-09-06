"""Local-only browser labeler for Curation and Semantic reasoning Gold.

Routine saves use an ignored sidecar. The canonical fixture changes only after
an explicit export. This module deliberately does not import a model client.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.gold_labeler import (
    ASSET_DIR, HOST, LabelValidationError, _read_json, _sha256, _utc_now,
    atomic_write_json,
)
from tools.sol_provisional_gold import candidate_sha256, validate_row
from tools.validate_reasoning_gold import ERROR_TYPES, audit

FIXTURES = ROOT / "tests" / "fixtures" / "gemini_reasoning"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024
MAX_NOTE_LENGTH = 1000

TASKS = {
    "curation": {
        "task": "CURATION",
        "title": "Curation Gold",
        "fixture": FIXTURES / "curation_gold.json",
        "sidecar": ROOT / ".eval" / "gold-labels" / "curation_labels.json",
        "provisional": ROOT / ".eval" / "gold-labels" /
                       "curation.sol_provisional.json",
        "labels": ("PASS", "REPAIR", "BLOCK"),
        "shortcuts": {"p": "PASS", "r": "REPAIR", "b": "BLOCK"},
    },
    "semantic": {
        "task": "SEMANTIC",
        "title": "Semantic Gold",
        "fixture": FIXTURES / "semantic_gold.json",
        "sidecar": ROOT / ".eval" / "gold-labels" / "semantic_labels.json",
        "provisional": ROOT / ".eval" / "gold-labels" /
                       "semantic.sol_provisional.json",
        "labels": ("PASS", "REPAIR", "UNVERIFIABLE", "BLOCK"),
        "shortcuts": {
            "p": "PASS", "r": "REPAIR", "n": "UNVERIFIABLE", "b": "BLOCK",
        },
    },
}


def _clean_note(value: object) -> str | None:
    note = str(value or "").strip() or None
    if note and len(note) > MAX_NOTE_LENGTH:
        raise LabelValidationError(f"note exceeds {MAX_NOTE_LENGTH} characters")
    return note


class ReviewLabelStore:
    def __init__(self, task_key: str, fixture_path: Path | None = None,
                 labels_path: Path | None = None,
                 provisional_path: Path | None = None) -> None:
        if task_key not in TASKS:
            raise LabelValidationError(f"unsupported task: {task_key}")
        self.task_key = task_key
        self.spec = TASKS[task_key]
        self.fixture_path = Path(fixture_path or self.spec["fixture"])
        self.labels_path = Path(labels_path or self.spec["sidecar"])
        if provisional_path is not None:
            self.provisional_path = Path(provisional_path)
        elif fixture_path is not None:
            # Custom/temporary fixtures must not accidentally inherit the real
            # repository provisional sidecar.  A colocated file remains an
            # explicit opt-in for fixture-specific integration tests.
            self.provisional_path = self.fixture_path.with_suffix(
                ".sol_provisional.json")
        else:
            self.provisional_path = Path(self.spec["provisional"])
        self._lock = threading.RLock()
        self.fixture_payload = _read_json(self.fixture_path)
        if self.fixture_payload.get("task") != self.spec["task"]:
            raise LabelValidationError("fixture task does not match --task")
        self.cases = self.fixture_payload.get("cases") or []
        self.case_by_id = {case["id"]: case for case in self.cases}
        if len(self.case_by_id) != len(self.cases):
            raise LabelValidationError("candidate fixture contains duplicate ids")
        self.fixture_sha256 = _sha256(self.fixture_path)
        self.sidecar = self._load_sidecar()
        self._validate_sidecar()
        self.provisional = self._load_provisional()

    def _load_provisional(self) -> dict:
        if not self.provisional_path.exists():
            return {"task": self.spec["task"], "labels": {}}
        payload = _read_json(self.provisional_path)
        if payload.get("task") != self.spec["task"]:
            raise LabelValidationError("provisional sidecar task does not match --task")
        if payload.get("candidate_sha256"):
            if payload["candidate_sha256"] != candidate_sha256(self.fixture_path):
                raise LabelValidationError("provisional candidate content is stale")
        elif payload.get("fixture_sha256") != self.fixture_sha256:
            raise LabelValidationError("provisional sidecar fixture hash is stale")
        if payload.get("status") != "AI_ASSISTED_PROVISIONAL_NOT_GOLD":
            raise LabelValidationError("provisional sidecar status is not safe")
        labels = payload.get("labels")
        if not isinstance(labels, dict):
            raise LabelValidationError("provisional labels must be an object")
        # Provisional rows were imported while their cases were pending.  After a
        # human export those same cases legitimately become HUMAN_LABELLED while
        # the immutable candidate hash stays valid, so reload/validate must still
        # accept the original provisional metadata.
        candidate_ids = set(self.case_by_id)
        for case_id, entry in labels.items():
            if entry.get("human_reviewed") is not False:
                raise LabelValidationError(
                    f"provisional row must remain human_reviewed=false: {case_id}")
            raw = {"case_id": case_id,
                   **{field: entry.get(field) for field in (
                       "provisional_label", "error_types", "confidence", "reason",
                       "evidence_reference")}}
            try:
                validate_row(self.task_key, raw, candidate_ids)
            except ValueError as exc:
                raise LabelValidationError(str(exc)) from exc
        return payload

    def _load_sidecar(self) -> dict:
        if not self.labels_path.exists():
            return {
                "schema_version": 1,
                "task": self.spec["task"],
                "fixture_sha256": self.fixture_sha256,
                "updated_at": None,
                "labels": {},
            }
        payload = _read_json(self.labels_path)
        if payload.get("task") != self.spec["task"]:
            raise LabelValidationError("sidecar task does not match --task")
        if payload.get("fixture_sha256") != self.fixture_sha256:
            raise LabelValidationError(
                "sidecar fixture hash is stale; reconcile or archive it before continuing")
        if not isinstance(payload.get("labels"), dict):
            raise LabelValidationError("sidecar labels must be an object")
        return payload

    def _validate_entry(self, entry: dict) -> dict:
        label = str(entry.get("human_label") or "").upper()
        if label not in self.spec["labels"]:
            raise LabelValidationError(f"invalid {self.spec['task']} label: {label!r}")
        note = _clean_note(entry.get("human_notes"))
        if self.task_key == "curation":
            contract = self.fixture_payload.get("dimension_contract") or {}
            dimensions = entry.get("human_dimensions")
            if not isinstance(dimensions, dict) or set(dimensions) != set(contract):
                raise LabelValidationError("curation dimensions do not match contract")
            for name, value in dimensions.items():
                if value is None:
                    raise LabelValidationError(f"curation dimension {name} is not labelled")
                if value not in contract[name]:
                    raise LabelValidationError(f"invalid {name} value: {value!r}")
            return {
                "human_label": label,
                "human_dimensions": dict(dimensions),
                "required_repair": _clean_note(entry.get("required_repair")),
                "human_notes": note,
            }
        error_types = entry.get("human_error_types")
        if not isinstance(error_types, list) or any(
                value not in ERROR_TYPES for value in error_types):
            raise LabelValidationError("semantic error types do not match contract")
        if label == "PASS" and error_types:
            raise LabelValidationError("PASS cannot have semantic error types")
        if label in {"REPAIR", "BLOCK"} and not error_types:
            raise LabelValidationError(f"{label} requires at least one semantic error type")
        return {
            "human_label": label,
            "human_error_types": list(dict.fromkeys(error_types)),
            "human_notes": note,
        }

    def _validate_sidecar(self) -> None:
        for case_id, entry in self.sidecar.get("labels", {}).items():
            if case_id not in self.case_by_id:
                raise LabelValidationError(f"sidecar refers to unknown case: {case_id}")
            if entry.get("cleared") is not True and entry.get("needs_review") is not True:
                self._validate_entry(entry)

    def _fixture_labels(self) -> dict[str, dict]:
        fields = ("human_label", "human_dimensions", "required_repair", "human_notes") \
            if self.task_key == "curation" else \
            ("human_label", "human_error_types", "human_notes")
        return {
            case["id"]: {**{field: deepcopy(case.get(field)) for field in fields},
                         "source": "fixture"}
            for case in self.cases
            if case.get("human_label") in self.spec["labels"]
            and case.get("label_status") in {"HUMAN_LABELLED", "USER_SPECIFIED"}
        }

    def current_labels(self) -> dict[str, dict]:
        labels = self._fixture_labels()
        for case_id, entry in self.sidecar.get("labels", {}).items():
            if entry.get("cleared") is True:
                labels.pop(case_id, None)
            elif entry.get("human_reviewed") is True:
                labels[case_id] = {**deepcopy(entry), "source": "sidecar"}
        return labels

    def _persist(self) -> None:
        self.sidecar["fixture_sha256"] = self.fixture_sha256
        self.sidecar["updated_at"] = _utc_now()
        atomic_write_json(self.labels_path, self.sidecar)

    def save_label(self, case_id: str, payload: dict) -> dict:
        with self._lock:
            if case_id not in self.case_by_id:
                raise LabelValidationError(f"unknown case: {case_id}")
            action = str(payload.pop("review_action", "CHANGE"))
            if action not in {"CHANGE", "APPROVE"}:
                raise LabelValidationError(f"invalid review action: {action}")
            entry = self._validate_entry(payload)
            entry["human_reviewed"] = True
            entry["review_action"] = action
            entry["updated_at"] = _utc_now()
            self.sidecar.setdefault("labels", {})[case_id] = entry
            self._persist()
            return deepcopy(entry)

    def _approved_entry(self, case_id: str) -> dict:
        provisional = (self.provisional.get("labels") or {}).get(case_id)
        if not provisional:
            raise LabelValidationError(f"no Sol provisional label for {case_id}")
        label = provisional["provisional_label"]
        if self.task_key == "curation":
            if label == "AMBIGUOUS":
                raise LabelValidationError("ambiguous Curation provisional needs human change")
            dimensions = {name: "PASS" for name in
                          self.fixture_payload["dimension_contract"]}
            mapping = {"event_boundary": "event_boundary", "chronology": "date",
                       "causality": "causality", "unit": "scope", "stage": "stage",
                       "attribution": "scope", "unsupported": "scope",
                       "overclaim": "scope"}
            for error_type in provisional["error_types"]:
                dimension = mapping.get(error_type)
                if dimension:
                    dimensions[dimension] = "FAIL"
                elif error_type == "other":
                    dimensions["event_boundary"] = "AMBIGUOUS"
            return {"human_label": "PASS" if label == "CORRECT" else "REPAIR",
                    "human_dimensions": dimensions, "required_repair": None,
                    "human_notes": None, "review_action": "APPROVE"}
        mapping = {"causality": "CAUSALITY_ERROR", "chronology": "TEMPORAL_ERROR",
                   "wrong_event_link": "SCOPE_ERROR", "attribution": "ENTITY_ERROR",
                   "unsupported_relation": "UNSUPPORTED_INFERENCE",
                   "overclaim": "CERTAINTY_ERROR", "contradiction": "FACT_ERROR",
                   "other": "UNSUPPORTED_INFERENCE"}
        canonical = {"SUPPORTED": "PASS", "UNSUPPORTED": "BLOCK",
                     "AMBIGUOUS": "UNVERIFIABLE"}[label]
        return {"human_label": canonical,
                "human_error_types": list(dict.fromkeys(
                    mapping[value] for value in provisional["error_types"]
                    if value in mapping)),
                "human_notes": None, "review_action": "APPROVE"}

    def approve_provisional(self, case_id: str) -> dict:
        return self.save_label(case_id, self._approved_entry(case_id))

    def mark_needs_review(self, case_id: str) -> None:
        with self._lock:
            if case_id not in self.case_by_id:
                raise LabelValidationError(f"unknown case: {case_id}")
            self.sidecar.setdefault("labels", {})[case_id] = {
                "needs_review": True, "human_reviewed": False,
                "review_action": "NEEDS_REVIEW", "updated_at": _utc_now()}
            self._persist()

    def clear_label(self, case_id: str) -> None:
        with self._lock:
            if case_id not in self.case_by_id:
                raise LabelValidationError(f"unknown case: {case_id}")
            self.sidecar.setdefault("labels", {})[case_id] = {
                "cleared": True, "updated_at": _utc_now(),
            }
            self._persist()

    def counts(self) -> dict:
        labels = self.current_labels()
        verdicts = {label: 0 for label in self.spec["labels"]}
        for entry in labels.values():
            verdicts[entry["human_label"]] += 1
        provisional = self.provisional.get("labels") or {}
        review_entries = self.sidecar.get("labels") or {}
        initial_ids = self.recommended_initial_ids()
        reviewed_ids = set(labels)
        return {
            "total": len(self.cases),
            "labeled": len(labels),
            "remaining": len(self.cases) - len(labels),
            "verdicts": verdicts,
            "provisional": len(provisional),
            "hard_cases": sum(entry.get("review_priority", 0) >= 60
                              for entry in provisional.values()),
            "needs_review": sum(entry.get("needs_review") is True
                                for entry in review_entries.values()),
            "initial_review_target": len(initial_ids),
            "initial_review_done": sum(case_id in reviewed_ids for case_id in initial_ids),
            "evaluation_ready": bool(initial_ids) and all(
                case_id in reviewed_ids for case_id in initial_ids),
        }

    def recommended_initial_ids(self) -> list[str]:
        provisional = self.provisional.get("labels") or {}
        if not provisional:
            return []
        order = {case["id"]: index for index, case in enumerate(self.cases)}
        ranked = sorted(provisional, key=lambda case_id: (
            -int(provisional[case_id].get("review_priority", 0)), order[case_id]))
        base = 24 if self.task_key == "curation" else 36
        selected = [case_id for case_id in ranked
                    if provisional[case_id].get("review_priority", 0) >= 60]
        coverage = []
        for field in ("provisional_label", "confidence"):
            for value in sorted({entry[field] for entry in provisional.values()}):
                match = next((case_id for case_id in ranked
                              if provisional[case_id][field] == value), None)
                if match: coverage.append(match)
        for error_type in sorted({value for entry in provisional.values()
                                  for value in entry["error_types"]}):
            match = next((case_id for case_id in ranked
                          if error_type in provisional[case_id]["error_types"]), None)
            if match: coverage.append(match)
        selected = list(dict.fromkeys(selected + coverage))
        target = min(len(ranked), max(base, len(selected)))
        selected.extend(case_id for case_id in ranked if case_id not in selected)
        return selected[:target]

    def _case_view(self, case: dict, position: int) -> dict:
        if self.task_key == "curation":
            return {
                "id": case["id"], "position": position,
                "source": {
                    "title": case.get("source_title"), "url": case.get("source_url"),
                    "published_at": case.get("published_at"),
                    "source_hash": case.get("source_hash"),
                },
                "output": deepcopy(case.get("current_output") or {}),
            }
        source = case.get("source_evidence") or {}
        return {
            "id": case["id"], "position": position, "claim": case.get("claim"),
            "source": {
                "title": source.get("original_title"), "url": source.get("url"),
                "published_at": source.get("published_at"),
                "source_hash": source.get("source_hash"),
                "contract_facts": deepcopy(source.get("contract_facts")),
                "verified_evidence": deepcopy(source.get("verified_evidence")),
            },
        }

    def public_state(self) -> dict:
        labels = self.current_labels()
        first_unlabeled = next((case["id"] for case in self.cases
                                if case["id"] not in labels),
                               self.cases[0]["id"] if self.cases else None)
        provisional = deepcopy(self.provisional.get("labels") or {})
        suggestions = {}
        for case_id in provisional:
            try:
                suggestion = self._approved_entry(case_id)
                suggestion.pop("review_action", None)
                suggestions[case_id] = suggestion
            except LabelValidationError:
                pass
        recommended = self.recommended_initial_ids()
        all_order = recommended + [case["id"] for case in self.cases
                                   if case["id"] not in recommended]
        return {
            "task": self.spec["task"], "title": self.spec["title"],
            "counts": self.counts(), "labels": labels,
            "candidates": [self._case_view(case, index)
                           for index, case in enumerate(self.cases, 1)],
            "label_contract": list(self.spec["labels"]),
            "shortcuts": self.spec["shortcuts"],
            "dimension_contract": deepcopy(
                self.fixture_payload.get("dimension_contract") or {}),
            "error_type_contract": list(
                self.fixture_payload.get("error_type_contract") or []),
            "provisional": provisional,
            "suggested_human": suggestions,
            "review_status": deepcopy(self.sidecar.get("labels") or {}),
            "review_order": all_order,
            "recommended_initial_ids": recommended,
            "start_id": (recommended[0] if recommended else first_unlabeled),
            "default_filter": "priority" if provisional else "unlabeled",
            "sidecar_path": str(self.labels_path),
            "provisional_path": str(self.provisional_path),
        }

    def reference(self, case_id: str) -> dict:
        case = self.case_by_id.get(case_id)
        if not case:
            raise LabelValidationError(f"unknown case: {case_id}")
        return {
            "warning": "선정/생성 메타데이터는 Gold 정답이나 source evidence가 아닙니다.",
            "selection_metadata_not_gold": deepcopy(
                case.get("selection_metadata_not_gold")),
            "candidate_kind": case.get("candidate_kind"),
            "generated_context_not_source_evidence": deepcopy(
                case.get("generated_context_not_source_evidence")),
        }

    def validate_state(self) -> dict:
        errors: list[str] = []
        try:
            self._validate_sidecar()
        except LabelValidationError as exc:
            errors.append(str(exc))
        canonical = audit(self.fixture_path.parent)
        return {
            "ok": not errors and canonical["ok"],
            "errors": errors + canonical["errors"],
            "warnings": canonical["warnings"], "counts": self.counts(),
            "fixture_sha256": self.fixture_sha256,
            "sidecar_saved": self.labels_path.exists(),
        }

    def _export_payload(self) -> dict:
        payload = deepcopy(self.fixture_payload)
        labels = self.current_labels()
        sidecar_labels = self.sidecar.get("labels", {})
        for case in payload.get("cases") or []:
            entry = labels.get(case["id"])
            sidecar_entry = sidecar_labels.get(case["id"])
            if entry and entry.get("source") == "fixture" and not sidecar_entry:
                # An untouched canonical contract must remain byte-for-byte
                # equivalent at the data level; do not normalize optional fields.
                continue
            if not entry and not (
                    sidecar_entry and sidecar_entry.get("cleared") is True):
                # Likewise, exporting reviewed rows must not add empty optional
                # fields to unrelated pending candidates.
                continue
            if self.task_key == "curation":
                fields = ("human_label", "human_dimensions", "required_repair",
                          "human_notes")
                empty = {"human_label": None,
                         "human_dimensions": {key: None for key in
                                              payload["dimension_contract"]},
                         "required_repair": None, "human_notes": None}
            else:
                fields = ("human_label", "human_error_types", "human_notes")
                empty = {"human_label": None, "human_error_types": None,
                         "human_notes": None}
            values = ({field: deepcopy(entry.get(field)) for field in fields}
                      if entry else empty)
            case.update(values)
            case["label_status"] = (
                "HUMAN_LABELLED" if entry else "HUMAN_LABEL_REQUIRED")
        return payload

    def export(self) -> dict:
        with self._lock:
            atomic_write_json(self.fixture_path, self._export_payload())
            self.fixture_payload = _read_json(self.fixture_path)
            self.cases = self.fixture_payload.get("cases") or []
            self.case_by_id = {case["id"]: case for case in self.cases}
            self.fixture_sha256 = _sha256(self.fixture_path)
            self._persist()
            report = audit(self.fixture_path.parent)
            return {"ok": report["ok"], "errors": report["errors"],
                    "warnings": report["warnings"], "counts": self.counts(),
                    "fixture": str(self.fixture_path),
                    "fixture_sha256": self.fixture_sha256}


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "NuclensReviewGoldLabeler/1"

    @property
    def store(self) -> ReviewLabelStore:
        return self.server.label_store  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[review-gold-labeler] {self.address_string()} {format % args}")

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_asset(self, name: str, content_type: str) -> None:
        body = (ASSET_DIR / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise LabelValidationError("invalid content length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise LabelValidationError("invalid request body size")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LabelValidationError("request body must be UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise LabelValidationError("request JSON must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_asset("review.html", "text/html; charset=utf-8")
            elif parsed.path == "/review.js":
                self._send_asset("review.js", "text/javascript; charset=utf-8")
            elif parsed.path == "/style.css":
                self._send_asset("style.css", "text/css; charset=utf-8")
            elif parsed.path == "/api/state":
                self._send_json(self.store.public_state())
            elif parsed.path == "/api/reference":
                case_id = (parse_qs(parsed.query).get("id") or [""])[0]
                self._send_json(self.store.reference(case_id))
            elif parsed.path == "/api/validate":
                report = self.store.validate_state()
                self._send_json(report, HTTPStatus.OK if report["ok"] else
                                HTTPStatus.UNPROCESSABLE_ENTITY)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except LabelValidationError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/labels":
                body = self._read_body()
                entry = self.store.save_label(str(body.pop("id", "")), body)
                self._send_json({"ok": True, "label": entry,
                                 "counts": self.store.counts()})
            elif parsed.path == "/api/export":
                report = self.store.export()
                self._send_json(report, HTTPStatus.OK if report["ok"] else
                                HTTPStatus.UNPROCESSABLE_ENTITY)
            elif parsed.path == "/api/review":
                body = self._read_body()
                case_id, action = str(body.get("id", "")), body.get("action")
                if action == "APPROVE":
                    entry = self.store.approve_provisional(case_id)
                    self._send_json({"ok": True, "label": entry,
                                     "counts": self.store.counts()})
                elif action == "NEEDS_REVIEW":
                    self.store.mark_needs_review(case_id)
                    self._send_json({"ok": True, "counts": self.store.counts()})
                else:
                    raise LabelValidationError(f"invalid review action: {action}")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except LabelValidationError as exc:
            self._send_json({"ok": False, "error": str(exc)},
                            HTTPStatus.UNPROCESSABLE_ENTITY)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path != "/api/labels":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            case_id = (parse_qs(parsed.query).get("id") or [""])[0]
            self.store.clear_label(case_id)
            self._send_json({"ok": True, "counts": self.store.counts()})
        except LabelValidationError as exc:
            self._send_json({"ok": False, "error": str(exc)},
                            HTTPStatus.UNPROCESSABLE_ENTITY)


def create_server(store: ReviewLabelStore, port: int = DEFAULT_PORT) -> LocalThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    server = LocalThreadingHTTPServer((HOST, port), Handler)
    server.label_store = store  # type: ignore[attr-defined]
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Curation/Semantic Gold labeler")
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--provisional", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--validate", action="store_true")
    action.add_argument("--export", action="store_true")
    args = parser.parse_args()
    try:
        store = ReviewLabelStore(args.task, args.fixture, args.labels,
                                 args.provisional)
        if args.validate or args.export:
            report = store.export() if args.export else store.validate_state()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
        server = create_server(store, args.port)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    url = f"http://{HOST}:{server.server_address[1]}"
    print(f"{store.spec['title']} labeler: {url}")
    print(f"자동 저장: {store.labels_path}")
    print("종료: Ctrl+C")
    if not args.no_browser:
        threading.Timer(.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGold labeler를 종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
