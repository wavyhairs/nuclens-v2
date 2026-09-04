"""V5.1 refactor safety harness and frozen behavior contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import channel_queue
import daily_brief
import gemini_client
import llm_cache
import news_bot
from v5_characterize import characterize
from v5_harness import (FrozenUrlOpen, assert_exact, block_internet, canonical_json,
                        deterministic_env, load_fixture, production_source_hashes,
                        request_hash)


class CharacterizationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture()

    def test_fixture_is_sanitized_and_covers_required_dimensions(self):
        self.assertTrue(self.fixture["meta"]["sanitized"])
        self.assertGreaterEqual(len(self.fixture["historical_regression_cases"]), 1)
        for case in self.fixture["historical_regression_cases"]:
            self.assertTrue(case["reference"].startswith(("tests/", "web/tests/")))
        self.assertEqual(
            set(self.fixture["coverage_dimensions"]),
            {"normal", "same_story_multiple_sources", "different_event_stage",
             "same_issue_follow_up", "must_separate_story_and_issue", "legacy_identity",
             "null_empty_malformed_optional_fields", "duplicate_and_reordered_jsonl",
             "admin_override", "kst_midnight", "daily_cutoff", "weekly_boundary",
             "month_boundary", "korean_hanja_emoji", "unicode_nfc_nfd"},
        )

    def test_exact_business_characterization(self):
        actual = characterize()
        expected = self.fixture["expected"]
        digest = hashlib.sha256(canonical_json(actual).encode("utf-8")).hexdigest()
        self.assertEqual(digest, expected["characterization_sha256"])
        # Keep critical output fields visible as well as locking the complete payload digest.
        assert_exact(actual["identity"]["ids"], expected["identity_ids"])
        self.assertEqual(actual["identity"]["display_id"], expected["display_id"])
        assert_exact(actual["ranking"]["selected_hashes"], expected["selected_hashes"])
        assert_exact(
            [row["hash"] for row in actual["ranking"]["diagnostics"]["dropped_duplicates"]],
            expected["dropped_duplicate_hashes"],
        )
        self.assertEqual(
            actual["continuity"]["diagnostics"]["verdicts"][0]["progression"],
            expected["continuity_progression"],
        )
        self.assertEqual(actual["continuity"]["items"][0]["story_id"],
                         expected["continuity_story_id"])
        assert_exact([row["issue_id"] for row in actual["web_issues"]],
                     expected["web_issue_ids"])
        assert_exact([[member["hash"] for member in row["members"]]
                      for row in actual["web_issues"]], expected["web_member_hashes"])

    def test_comparator_rejects_unlisted_difference(self):
        with self.assertRaisesRegex(AssertionError, "characterization mismatch"):
            assert_exact({"title": "A"}, {"title": "B"})

    def test_comparator_allows_only_explicit_path(self):
        assert_exact(
            {"generated_at": "new", "value": 1},
            {"generated_at": "old", "value": 1},
            allowed_paths={("generated_at",)},
        )
        with self.assertRaises(AssertionError):
            assert_exact({"generated_at": "new", "value": 2},
                         {"generated_at": "old", "value": 1},
                         allowed_paths={("generated_at",)})

    def test_json_snapshot_serialization_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload = [{"한글": "값", "empty": None}]
            news_path = base / "news.json"
            daily_path = base / "daily.json"
            outbox_path = base / "outbox.json"
            channel_path = base / "channel.json"
            news_bot.save_json(news_path, payload)
            daily_brief.save_queue(payload, daily_path)
            daily_brief.save_outbox({"payload": payload}, outbox_path)
            channel_queue.save_queue({"schema_version": 1, "batches": []}, channel_path)
            self.assertEqual(news_path.read_bytes(), daily_path.read_bytes())
            self.assertEqual(
                news_path.read_text(encoding="utf-8"),
                '[\n  {\n    "한글": "값",\n    "empty": null\n  }\n]',
            )
            self.assertEqual(
                outbox_path.read_text(encoding="utf-8"),
                '{\n  "payload": [\n    {\n      "한글": "값",\n      "empty": null\n    }\n  ]\n}',
            )
            self.assertEqual(channel_path.read_bytes()[-1:], b"\n")

    def test_frozen_gemini_request_hash_and_call_count(self):
        model = "gemini-test-model"
        body = {
            "system_instruction": {"parts": [{"text": "시스템  그대로"}]},
            "contents": [{"role": "user", "parts": [{"text": "Café ⚛️"}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 64,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        digest = request_hash(model, body)
        frozen = FrozenUrlOpen({digest: {"verdict": "same", "score": 1}})
        with mock.patch.object(gemini_client, "API_KEY", "fixture-key"), \
             mock.patch.object(gemini_client, "_pace", lambda _model: None), \
             mock.patch.object(gemini_client.urllib.request, "urlopen", frozen), \
             block_internet():
            result = gemini_client.call_json(
                "시스템  그대로", "Café ⚛️", temperature=0.2,
                max_output_tokens=64, retries=0, thinking_budget=0,
                model=model, label="v5-fixture",
            )
        self.assertEqual(result, {"verdict": "same", "score": 1})
        self.assertEqual(frozen.calls, {digest: 1})
        self.assertEqual(frozen.total_calls, 1)

    def test_cache_round_trip_and_stale_version_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            payload = {"request-a": {"prompt_version": 7, "result": "고정"}}
            llm_cache.save(payload, path, key="reviews", prompt_version=7,
                           comment="v5 fixture")
            self.assertEqual(llm_cache.load(path, "reviews"), payload)
            self.assertTrue(llm_cache.is_current(payload["request-a"], 7))
            self.assertFalse(llm_cache.is_current(payload["request-a"], 8))
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{\n  "_comment": "v5 fixture",\n  "prompt_version": 7,\n  "reviews": {\n'
                '    "request-a": {\n      "prompt_version": 7,\n      "result": "고정"\n'
                '    }\n  }\n}\n',
            )

    def test_unknown_gemini_request_fails_closed(self):
        frozen = FrozenUrlOpen({})
        with mock.patch.object(gemini_client, "API_KEY", "fixture-key"), \
             mock.patch.object(gemini_client, "_pace", lambda _model: None), \
             mock.patch.object(gemini_client.urllib.request, "urlopen", frozen), \
             block_internet(), self.assertRaisesRegex(AssertionError, "unregistered"):
            gemini_client.call_json("changed prompt", "input", retries=0,
                                    model="gemini-test-model")

    def test_three_hash_seeds_emit_identical_payload(self):
        outputs = []
        for seed in ("1", "17", "101"):
            env = deterministic_env(seed)
            env["PYTHONPATH"] = os.pathsep.join(
                [str(ROOT / "tests" / "network_block"), str(ROOT), str(ROOT / "tests")]
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "v5_characterize.py")],
                cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                timeout=90,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs.append(proc.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_imports_have_no_source_writes_or_external_calls(self):
        before = production_source_hashes()
        env = deterministic_env("17")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "network_block"), str(ROOT)]
        )
        code = (
            "import news_bot, daily_brief, weekly_bot, issue_continuity, "
            "story_identity, ranking, channel_queue, gemini_client; print('IMPORT_OK')"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("IMPORT_OK", proc.stdout)
        self.assertEqual(before, production_source_hashes())

    def test_workflow_execution_and_path_trigger_contract(self):
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / ".github" / "workflows").glob("*.yml")
        }
        self.assertIn("python news_bot.py", workflows["crawl.yml"])
        self.assertIn("python daily_brief.py", workflows["daily-brief.yml"])
        self.assertIn("python weekly_bot.py", workflows["weekly.yml"])
        self.assertIn("python web/build_data.py", workflows["deploy-web.yml"])
        python_ci = workflows["python-tests.yml"]
        for trigger in ("**.py", "tests/**", "web/tests/**", "requirements.txt",
                        "ranking_config.json", ".github/workflows/python-tests.yml"):
            self.assertIn(trigger, python_ci)
        self.assertIn("python -m unittest discover -s tests", python_ci)
        self.assertIn("GEMINI_API_KEY: \"\"", python_ci)

    def test_workflow_import_mode_in_temporary_checkout(self):
        """Reproduce repo-root direct-script imports without executing production mains."""
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "checkout"
            checkout.mkdir()
            for source in ROOT.rglob("*.py"):
                rel = source.relative_to(ROOT)
                if rel.parts[0] in {".git", ".pytest_cache", "tests"} or \
                        "__pycache__" in rel.parts:
                    continue
                target = checkout / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            for name in ("ranking_config.json", "sources.json", "keywords.json",
                         "entity_registry.json", "issue_match_overrides.json",
                         "selection_overrides.json"):
                shutil.copy2(ROOT / name, checkout / name)

            env = deterministic_env("101")
            env["PYTHONPATH"] = os.pathsep.join(
                [str(ROOT / "tests" / "network_block"), str(checkout)]
            )
            for script in ("news_bot.py", "daily_brief.py", "weekly_bot.py",
                           "web/build_data.py"):
                code = ("import runpy; runpy.run_path(" + repr(script)
                        + ", run_name='v5_workflow_smoke'); print('SMOKE_OK')")
                proc = subprocess.run(
                    [sys.executable, "-c", code], cwd=checkout, env=env,
                    capture_output=True, text=True, encoding="utf-8", timeout=60,
                )
                self.assertEqual(proc.returncode, 0, f"{script}: {proc.stderr}")
                self.assertIn("SMOKE_OK", proc.stdout)

    def test_cli_help_exit_codes_are_zero(self):
        env = deterministic_env("1")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "network_block"), str(ROOT)]
        )
        for script in ("daily_brief.py", "weekly_bot.py", "channel_queue.py"):
            proc = subprocess.run([sys.executable, script, "--help"], cwd=ROOT, env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(proc.returncode, 0, f"{script}: {proc.stderr}")
            self.assertIn("usage:", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()
