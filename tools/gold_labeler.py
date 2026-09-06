"""Local-only browser UI for human Identity Gold labeling.

The default save target is an ignored sidecar. The tracked canonical fixture is
changed only by an explicit Export action or ``--export``. No model/API client is
imported or called by this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import uuid
import webbrowser
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.validate_reasoning_gold import audit

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "gemini_reasoning" / "identity_candidates.json"
DEFAULT_LABELS = ROOT / ".eval" / "gold-labels" / "identity_labels.json"
ASSET_DIR = ROOT / "tools" / "gold_labeler_assets"
LABELS = ("MERGE", "SEPARATE", "AMBIGUOUS")
LABEL_REASONS = {
    "MERGE": (
        "same_action", "same_meeting", "same_announcement",
        "multi_source_same_event", "follow_up_same_event", "other",
    ),
    "SEPARATE": (
        "same_entity_only", "different_action", "different_stage", "different_unit",
        "broader_topic", "different_time", "other",
    ),
    "AMBIGUOUS": ("insufficient_context", "unclear_event_boundary", "other"),
}
KEYBOARD_LABELS = {"m": "MERGE", "s": "SEPARATE", "a": "AMBIGUOUS"}
MAX_BODY_BYTES = 16 * 1024
MAX_NOTE_LENGTH = 240


class LabelValidationError(ValueError):
    """Raised when a human-label mutation does not satisfy the UI contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write_json(path: Path, payload: dict) -> None:
    """Flush a complete sibling file and atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def validate_label(label: str, reason: str, note: str | None = None) -> tuple[str, str, str | None]:
    label = str(label or "").upper()
    reason = str(reason or "")
    cleaned_note = str(note or "").strip() or None
    if label not in LABELS:
        raise LabelValidationError(f"invalid label: {label!r}")
    if reason not in LABEL_REASONS[label]:
        raise LabelValidationError(f"invalid reason {reason!r} for {label}")
    if reason == "other" and not cleaned_note:
        raise LabelValidationError("other reason requires a short note")
    if cleaned_note and len(cleaned_note) > MAX_NOTE_LENGTH:
        raise LabelValidationError(f"note exceeds {MAX_NOTE_LENGTH} characters")
    if reason != "other":
        cleaned_note = None
    return label, reason, cleaned_note


def filter_case_ids(cases: list[dict], labels: dict[str, dict], mode: str) -> list[str]:
    ids = [case["id"] for case in cases]
    if mode == "first60":
        return ids[:60]
    if mode == "all":
        return ids
    if mode == "unlabeled":
        return [case_id for case_id in ids if case_id not in labels]
    raise ValueError(f"invalid filter mode: {mode}")


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[0-9A-Za-z가-힣·ㆍ]+", text or "")
            if len(token) > 1}


def _comparison(case: dict) -> dict:
    a_title = (case.get("a") or {}).get("title") or case.get("left_title") or ""
    b_title = (case.get("b") or {}).get("title") or case.get("right_title") or ""
    a_tokens, b_tokens = _tokens(a_title), _tokens(b_title)
    union = a_tokens | b_tokens
    units_pattern = r"\d+(?:[·ㆍ,\-]\d+)*호기"
    a_units = set(re.findall(units_pattern, a_title))
    b_units = set(re.findall(units_pattern, b_title))
    metadata = case.get("metadata") or {}
    return {
        "date_gap_days": metadata.get("date_gap_days"),
        "same_published_date": metadata.get("date_gap_days") == 0,
        "shared_entities": metadata.get("shared_entities") or [],
        "common_units": sorted(a_units & b_units),
        "title_token_overlap": round(len(a_tokens & b_tokens) / len(union), 3) if union else 0,
        "same_publisher": ((case.get("a") or {}).get("publisher") ==
                           (case.get("b") or {}).get("publisher")),
    }


class LabelStore:
    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE,
                 labels_path: Path = DEFAULT_LABELS) -> None:
        self.fixture_path = Path(fixture_path)
        self.labels_path = Path(labels_path)
        self._lock = threading.RLock()
        self.fixture_payload = _read_json(self.fixture_path)
        self.cases = self.fixture_payload.get("cases") or []
        self.case_by_id = {case["id"]: case for case in self.cases}
        if len(self.case_by_id) != len(self.cases):
            raise LabelValidationError("candidate fixture contains duplicate ids")
        self.fixture_sha256 = _sha256(self.fixture_path)
        self.sidecar = self._load_sidecar()
        self._validate_sidecar()

    def _load_sidecar(self) -> dict:
        if not self.labels_path.exists():
            return {
                "schema_version": 1,
                "task": "IDENTITY_REVIEW",
                "fixture_sha256": self.fixture_sha256,
                "updated_at": None,
                "labels": {},
            }
        payload = _read_json(self.labels_path)
        if not isinstance(payload.get("labels"), dict):
            raise LabelValidationError("sidecar labels must be an object")
        return payload

    def _validate_sidecar(self) -> None:
        for case_id, entry in self.sidecar.get("labels", {}).items():
            if case_id not in self.case_by_id:
                raise LabelValidationError(f"sidecar refers to unknown case: {case_id}")
            if entry.get("cleared") is True:
                continue
            validate_label(entry.get("human_label"), entry.get("reason_code"),
                           entry.get("human_notes"))

    def _fixture_labels(self) -> dict[str, dict]:
        labels: dict[str, dict] = {}
        for case in self.cases:
            if (case.get("human_label") in LABELS
                    and case.get("label_status") in {"HUMAN_LABELLED", "USER_SPECIFIED"}):
                labels[case["id"]] = {
                    "human_label": case["human_label"],
                    "reason_code": case.get("reason_code"),
                    "human_notes": case.get("human_notes"),
                    "source": "fixture",
                }
        return labels

    def current_labels(self) -> dict[str, dict]:
        with self._lock:
            labels = self._fixture_labels()
            for case_id, entry in self.sidecar.get("labels", {}).items():
                if entry.get("cleared") is True:
                    labels.pop(case_id, None)
                else:
                    labels[case_id] = {**entry, "source": "sidecar"}
            return labels

    def _persist(self) -> None:
        self.sidecar["fixture_sha256"] = self.fixture_sha256
        self.sidecar["updated_at"] = _utc_now()
        atomic_write_json(self.labels_path, self.sidecar)

    def save_label(self, case_id: str, label: str, reason: str,
                   note: str | None = None) -> dict:
        with self._lock:
            if case_id not in self.case_by_id:
                raise LabelValidationError(f"unknown case: {case_id}")
            label, reason, note = validate_label(label, reason, note)
            entry = {
                "human_label": label,
                "reason_code": reason,
                "human_notes": note,
                "updated_at": _utc_now(),
            }
            self.sidecar.setdefault("labels", {})[case_id] = entry
            self._persist()
            return dict(entry)

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
        verdicts = {label: 0 for label in LABELS}
        for entry in labels.values():
            verdicts[entry["human_label"]] += 1
        total = len(self.cases)
        first60_total = min(60, total)
        first60_done = sum(case["id"] in labels for case in self.cases[:first60_total])
        return {
            "total": total,
            "labeled": len(labels),
            "remaining": total - len(labels),
            **{label.lower(): verdicts[label] for label in LABELS},
            "first60_labeled": first60_done,
            "first60_total": first60_total,
            "first60_complete": first60_done == first60_total,
            "evaluation_ready": first60_done >= 60,
        }

    def public_state(self) -> dict:
        labels = self.current_labels()
        candidates = []
        for index, case in enumerate(self.cases, 1):
            candidates.append({
                "id": case["id"], "position": index,
                "a": deepcopy(case.get("a") or {}),
                "b": deepcopy(case.get("b") or {}),
                "comparison": _comparison(case),
            })
        first_unlabeled = next((case["id"] for case in self.cases[:60]
                                if case["id"] not in labels),
                               self.cases[0]["id"] if self.cases else None)
        return {
            "task": "Identity Gold",
            "counts": self.counts(),
            "candidates": candidates,
            "labels": labels,
            "reasons": {key: list(value) for key, value in LABEL_REASONS.items()},
            "shortcuts": {"labels": KEYBOARD_LABELS, "previous": "ArrowLeft",
                          "next": "ArrowRight", "save": "Enter"},
            "start_id": first_unlabeled,
            "default_filter": "first60",
            "sidecar_path": str(self.labels_path),
        }

    def reference(self, case_id: str) -> dict:
        case = self.case_by_id.get(case_id)
        if not case:
            raise LabelValidationError(f"unknown case: {case_id}")
        metadata = case.get("metadata") or {}
        return {
            "warning": "모델/캐시 판정은 참고정보이며 Gold 정답이 아닙니다.",
            "selection_group": metadata.get("selection_group"),
            "known_edge_case": metadata.get("known_edge_case"),
            "embedding_similarity": metadata.get("embedding_similarity"),
            "cached_review_not_gold": metadata.get("cached_review_not_gold"),
            "model_comparison": metadata.get("model_comparison"),
        }

    def validate_state(self) -> dict:
        with self._lock:
            errors: list[str] = []
            try:
                self._validate_sidecar()
            except LabelValidationError as exc:
                errors.append(str(exc))
            canonical = audit(self.fixture_path.parent)
            return {
                "ok": not errors and canonical["ok"],
                "errors": errors + canonical["errors"],
                "warnings": canonical["warnings"],
                "counts": self.counts(),
                "fixture_sha256": self.fixture_sha256,
                "sidecar_saved": self.labels_path.exists(),
            }

    def _export_payload(self) -> dict:
        payload = deepcopy(self.fixture_payload)
        reason_contract = list(payload.get("reason_codes") or [])
        for reason in dict.fromkeys(reason for values in LABEL_REASONS.values()
                                    for reason in values):
            if reason not in reason_contract:
                reason_contract.append(reason)
        payload["reason_codes"] = reason_contract
        labels = self.current_labels()
        for case in payload.get("cases") or []:
            entry = labels.get(case["id"])
            if entry:
                case["human_label"] = entry["human_label"]
                case["reason_code"] = entry["reason_code"]
                case["human_notes"] = entry.get("human_notes")
                case["label_status"] = "HUMAN_LABELLED"
            else:
                case["human_label"] = None
                case["reason_code"] = None
                case["human_notes"] = None
                case["label_status"] = "HUMAN_LABEL_REQUIRED"
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
            return {
                "ok": report["ok"], "errors": report["errors"],
                "warnings": report["warnings"], "counts": self.counts(),
                "fixture": str(self.fixture_path),
                "fixture_sha256": self.fixture_sha256,
            }


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class GoldLabelerHandler(BaseHTTPRequestHandler):
    server_version = "NuclensGoldLabeler/1"

    @property
    def store(self) -> LabelStore:
        return self.server.label_store  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[gold-labeler] {self.address_string()} {format % args}")

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_asset(self, filename: str, content_type: str) -> None:
        path = ASSET_DIR / filename
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LabelValidationError("request body must be UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise LabelValidationError("request JSON must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_asset("index.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.js":
                self._send_asset("app.js", "text/javascript; charset=utf-8")
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

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/labels":
                body = self._read_body()
                entry = self.store.save_label(
                    body.get("id"), body.get("label"), body.get("reason"), body.get("note"))
                self._send_json({"ok": True, "label": entry,
                                 "counts": self.store.counts()})
            elif parsed.path == "/api/export":
                report = self.store.export()
                self._send_json(report, HTTPStatus.OK if report["ok"] else
                                HTTPStatus.UNPROCESSABLE_ENTITY)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except LabelValidationError as exc:
            self._send_json({"ok": False, "error": str(exc)},
                            HTTPStatus.UNPROCESSABLE_ENTITY)

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler contract
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


def create_server(store: LabelStore, port: int = DEFAULT_PORT) -> LocalThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    server = LocalThreadingHTTPServer((HOST, port), GoldLabelerHandler)
    server.label_store = store  # type: ignore[attr-defined]
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Identity Gold human labeler")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--validate", action="store_true")
    action.add_argument("--export", action="store_true")
    args = parser.parse_args()

    try:
        store = LabelStore(args.fixture, args.labels)
        if args.validate:
            report = store.validate_state()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
        if args.export:
            report = store.export()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
        server = create_server(store, args.port)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    actual_port = server.server_address[1]
    url = f"http://{HOST}:{actual_port}"
    print(f"Identity Gold labeler: {url}")
    print(f"자동 저장: {store.labels_path}")
    print("종료: Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGold labeler를 종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
