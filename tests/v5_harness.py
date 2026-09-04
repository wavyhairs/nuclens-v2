"""V5.1 deterministic characterization helpers.

This module is test infrastructure only.  It deliberately does not normalize text,
coerce types, or contact a real network.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import socket
import urllib.request
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "v5_characterization.json"
_MODEL_RE = re.compile(r"/models/([^/:]+):")


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    """Canonicalize JSON key order only; preserve strings, types and list order."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_hash(model: str, body: dict[str, Any]) -> str:
    material = model + "\0" + canonical_json(body)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def hash_request(request: urllib.request.Request) -> str:
    match = _MODEL_RE.search(request.full_url)
    if not match:
        raise AssertionError(f"model missing from frozen request URL: {request.full_url!r}")
    raw = request.data
    if not isinstance(raw, bytes):
        raise AssertionError("frozen request has no byte JSON body")
    return request_hash(match.group(1), json.loads(raw.decode("utf-8")))


class _FrozenResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()
        return False


class FrozenUrlOpen:
    """Request-hash keyed urllib fixture with observable call counts."""

    def __init__(self, responses: dict[str, dict[str, Any]]):
        self.responses = copy.deepcopy(responses)
        self.calls: Counter[str] = Counter()

    def __call__(self, request: urllib.request.Request, timeout: float = 0) -> _FrozenResponse:
        del timeout
        digest = hash_request(request)
        self.calls[digest] += 1
        if digest not in self.responses:
            raise AssertionError(f"unregistered external request: {digest}")
        payload = {
            "candidates": [{"content": {"parts": [{
                "text": json.dumps(self.responses[digest], ensure_ascii=False)
            }]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1,
                              "totalTokenCount": 2},
        }
        return _FrozenResponse(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())


@contextmanager
def block_internet():
    """Block IPv4/IPv6 sockets while leaving local IPC (AF_UNIX) usable."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def guarded_connect(instance, *args, **kwargs):
        if instance.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("internet socket blocked by V5.1 harness")
        return original_connect(instance, *args, **kwargs)

    def guarded_connect_ex(instance, *args, **kwargs):
        if instance.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("internet socket blocked by V5.1 harness")
        return original_connect_ex(instance, *args, **kwargs)

    def blocked_connection(*_args, **_kwargs):
        raise AssertionError("internet connection blocked by V5.1 harness")

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = blocked_connection
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.create_connection = original_create_connection


def _without_allowed(value: Any, path: tuple[str, ...], allowed: set[tuple[str, ...]]) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_allowed(child, path + (str(key),), allowed)
            for key, child in value.items()
            if path + (str(key),) not in allowed
        }
    if isinstance(value, list):
        return [_without_allowed(child, path + (str(index),), allowed)
                for index, child in enumerate(value)]
    return value


def assert_exact(actual: Any, expected: Any,
                 allowed_paths: Iterable[tuple[str, ...]] = ()) -> None:
    """Compare recursively with no normalization beyond explicitly allowed paths."""
    allowed = set(allowed_paths)
    left = _without_allowed(actual, (), allowed)
    right = _without_allowed(expected, (), allowed)
    if left != right:
        raise AssertionError(
            "characterization mismatch\nactual=" + canonical_json(left)
            + "\nexpected=" + canonical_json(right)
        )


def production_source_hashes(root: Path = ROOT) -> dict[str, str]:
    """Hash source/config/workflow inputs, excluding tests, generated state and VCS."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] in {".git", ".pytest_cache", "tests"}:
            continue
        if "__pycache__" in rel.parts:
            continue
        if path.suffix in {".py", ".yml", ".yaml"} or path.name in {
            "requirements.txt", "ranking_config.json", "sources.json", "keywords.json",
            "entity_registry.json", "issue_match_overrides.json", "selection_overrides.json",
        }:
            files.append(path)
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


def deterministic_env(seed: str = "1") -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "PYTHONHASHSEED": seed,
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GEMINI_API_KEY": "",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "NUCLENS_SKIP_DATA_GATES": "1",
    })
    return env
