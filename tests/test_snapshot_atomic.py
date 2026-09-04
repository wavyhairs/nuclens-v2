"""Failure-injection contract for crawler-owned atomic JSON snapshots."""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import news_bot


class _WriteFailure:
    def __init__(self, stream, error: Exception, partial: bool):
        self.stream = stream
        self.error = error
        self.partial = partial

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.stream.close()
        return False

    def write(self, text: str):
        if self.partial:
            self.stream.write(text[: max(1, len(text) // 3)])
            self.stream.flush()
        raise self.error

    def flush(self):
        self.stream.flush()


class AtomicSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.path = self.base / "curated.json"

    def tearDown(self):
        self._tmp.cleanup()

    def assert_no_temp_residue(self):
        self.assertEqual(list(self.base.glob(".nuclens-atomic-*.tmp")), [])

    def test_all_crawler_snapshot_owners_route_through_save_json(self):
        state = {"sent": {"future": "2999-01-01T00:00:00+00:00"}}
        curated = {"future": {"cached_at": "2999-01-01T00:00:00+00:00"}}
        queue = [{"hash": "future"}]
        with mock.patch.object(news_bot, "save_json") as save:
            news_bot.save_state(state)
            news_bot.save_curated(curated)
            news_bot.save_queue(queue)
        self.assertEqual([call.args[0] for call in save.call_args_list], [
            news_bot.STATE_FILE,
            news_bot.CURATED_CACHE_FILE,
            news_bot.DIGEST_QUEUE_FILE,
        ])

    def test_existing_file_is_replaced_with_exact_legacy_bytes_and_mode(self):
        self.path.write_text("old", encoding="utf-8")
        os.chmod(self.path, 0o640)
        mode_before = stat.S_IMODE(self.path.stat().st_mode)
        payload = {"한글": "값", "rows": [1, None]}

        news_bot.save_json(self.path, payload)

        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), mode_before)
        self.assert_no_temp_residue()

    def test_new_file_keeps_path_write_text_creation_mode(self):
        reference = self.base / "reference.json"
        reference.write_text("reference", encoding="utf-8")

        news_bot.save_json(self.path, {"new": True})

        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),
                         stat.S_IMODE(reference.stat().st_mode))
        self.assert_no_temp_residue()

    def test_malformed_existing_file_is_replaced_without_being_read(self):
        self.path.write_text("{broken", encoding="utf-8")
        news_bot.save_json(self.path, {"recovered": True})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"recovered": True})
        self.assert_no_temp_residue()

    def test_empty_state_keeps_exact_serialization(self):
        news_bot.save_json(self.path, {})
        self.assertEqual(self.path.read_bytes(), b"{}")
        news_bot.save_json(self.path, [])
        self.assertEqual(self.path.read_bytes(), b"[]")
        self.assert_no_temp_residue()

    def test_temp_open_failure_keeps_existing_file(self):
        self.path.write_text("old-complete", encoding="utf-8")
        with mock.patch.object(news_bot.os, "open", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                news_bot.save_json(self.path, {"new": True})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old-complete")
        self.assert_no_temp_residue()

    def test_replace_failure_keeps_existing_file_and_removes_temp(self):
        self.path.write_text("old-complete", encoding="utf-8")
        with mock.patch.object(news_bot.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                news_bot.save_json(self.path, {"new": True})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old-complete")
        self.assert_no_temp_residue()

    def _assert_write_failure_isolated(self, error: Exception, *, partial: bool):
        self.path.write_text("old-complete", encoding="utf-8")
        real_fdopen = os.fdopen

        def failing_fdopen(fd, *args, **kwargs):
            return _WriteFailure(real_fdopen(fd, *args, **kwargs), error, partial)

        with mock.patch.object(news_bot.os, "fdopen", side_effect=failing_fdopen):
            with self.assertRaises(type(error)):
                news_bot.save_json(self.path, {"large": [1, 2, 3]})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old-complete")
        self.assert_no_temp_residue()

    def test_simulated_enospc_keeps_existing_file_and_removes_temp(self):
        self._assert_write_failure_isolated(
            OSError(errno.ENOSPC, "simulated disk full"), partial=False
        )

    def test_interrupted_partial_write_keeps_existing_file_and_removes_temp(self):
        self._assert_write_failure_isolated(
            RuntimeError("simulated interruption"), partial=True
        )


if __name__ == "__main__":
    unittest.main()
