import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import crawl_trigger_gate as gate


UTC = timezone.utc


class CrawlTriggerGateTests(unittest.TestCase):
    def test_normal_schedule_claims_and_delayed_backup_is_skipped(self):
        state = gate.empty_state()
        now = datetime(2026, 8, 28, 3, 11, tzinfo=UTC)  # 12:11 KST
        first = gate.decide_claim(
            state, event_name="schedule", run_id="schedule-12", now=now)
        gate.finalize(state, slot=first["slot"], attempt_id="schedule-12",
                      collect_outcome="success", new_article_count=4,
                      now=now + timedelta(minutes=20))
        backup = gate.decide_claim(
            state, event_name="workflow_dispatch", trigger_source="backup_watchdog",
            recovery_reason="trigger_missing", run_id="backup-12",
            now=now + timedelta(minutes=26))
        self.assertTrue(first["should_run"])
        self.assertFalse(backup["should_run"])
        self.assertEqual("slot_already_completed", backup["trigger_state"])

    def test_four_missing_schedules_each_recover_once(self):
        """8/28 12·15·18·21시 schedule run 미생성 실사례."""
        state = gate.empty_state()
        for hour in (3, 6, 9, 12):  # UTC == KST 12, 15, 18, 21
            now = datetime(2026, 8, 28, hour, 37, tzinfo=UTC)
            run_id = f"backup-{hour}"
            backup = gate.decide_claim(
                state, event_name="workflow_dispatch",
                trigger_source="backup_watchdog", recovery_reason="trigger_missing",
                run_id=run_id, now=now)
            self.assertTrue(backup["should_run"])
            self.assertEqual("schedule_missing_recovery", backup["trigger_state"])
            gate.finalize(state, slot=backup["slot"], attempt_id=run_id,
                          collect_outcome="success", new_article_count=1, now=now)

            delayed_schedule = gate.decide_claim(
                state, event_name="schedule", run_id=f"late-{hour}",
                now=now + timedelta(minutes=5))
            second_backup = gate.decide_claim(
                state, event_name="workflow_dispatch",
                trigger_source="backup_watchdog", recovery_reason="trigger_missing",
                run_id=f"backup-again-{hour}", now=now + timedelta(minutes=10))
            self.assertFalse(delayed_schedule["should_run"])
            self.assertFalse(second_backup["should_run"])
        self.assertEqual(4, len(state["slots"]))

    def test_active_claim_blocks_overlap_but_stale_claim_recovers(self):
        state = gate.empty_state()
        now = datetime(2026, 8, 28, 9, 11, tzinfo=UTC)
        first = gate.decide_claim(state, event_name="schedule", run_id="one", now=now)
        active = gate.decide_claim(
            state, event_name="workflow_dispatch", trigger_source="backup_watchdog",
            run_id="two", now=now + timedelta(minutes=30))
        stale = gate.decide_claim(
            state, event_name="workflow_dispatch", trigger_source="backup_watchdog",
            run_id="three", now=now + timedelta(minutes=46))
        self.assertTrue(first["should_run"])
        self.assertFalse(active["should_run"])
        self.assertEqual("slot_claim_active", active["trigger_state"])
        self.assertTrue(stale["should_run"])
        self.assertEqual("stale_claim_recovery", stale["trigger_state"])

    def test_failure_and_successful_zero_are_distinct(self):
        state = gate.empty_state()
        now = datetime(2026, 8, 28, 9, 11, tzinfo=UTC)
        failed = gate.decide_claim(state, event_name="schedule", run_id="failed", now=now)
        result = gate.finalize(
            state, slot=failed["slot"], attempt_id="failed",
            collect_outcome="failure", new_article_count=0, now=now)
        self.assertEqual("failed", result["collection_state"])

        retry = gate.decide_claim(
            state, event_name="workflow_dispatch", trigger_source="backup_watchdog",
            recovery_reason="workflow_failed", run_id="retry", now=now + timedelta(minutes=5))
        self.assertTrue(retry["should_run"])
        self.assertEqual("workflow_failed_recovery", retry["trigger_state"])
        result = gate.finalize(
            state, slot=retry["slot"], attempt_id="retry",
            collect_outcome="success", new_article_count=0,
            now=now + timedelta(minutes=10))
        self.assertEqual("success_zero_articles", result["collection_state"])

    def test_manual_dispatch_remains_force_run(self):
        state = gate.empty_state()
        now = datetime(2026, 8, 28, 9, 20, tzinfo=UTC)
        one = gate.decide_claim(
            state, event_name="workflow_dispatch", trigger_source="manual",
            run_id="manual-1", now=now)
        two = gate.decide_claim(
            state, event_name="workflow_dispatch", trigger_source="manual",
            run_id="manual-2", now=now)
        self.assertTrue(one["should_run"])
        self.assertTrue(two["should_run"])
        self.assertNotEqual(one["slot"], two["slot"])

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crawl_runs.json"
            state = gate.empty_state()
            gate.decide_claim(state, event_name="schedule", run_id="1",
                              now=datetime(2026, 8, 28, 0, 11, tzinfo=UTC))
            gate.save_state(state, path)
            self.assertEqual(state, gate.load_state(path))
            self.assertEqual(1, json.loads(path.read_text(encoding="utf-8"))["schema_version"])


class CrawlRecoveryWorkflowContractTests(unittest.TestCase):
    def test_crawl_claims_before_collection_and_reads_latest_main(self):
        workflow = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(
            encoding="utf-8")
        self.assertIn('cron: "11 */3 * * *"', workflow)
        self.assertLess(workflow.index("Claim three-hour crawl slot"),
                        workflow.index("Collect news"))
        self.assertIn("ref: main", workflow)
        self.assertIn("group: nuclens-state", workflow)
        self.assertIn("git add crawl_runs.json", workflow)
        self.assertIn("steps.collect.outputs.new_article_count", workflow)
        self.assertIn("CRAWL_LOOKBACK_HOURS", workflow)

    def test_watchdog_is_independent_and_requires_only_dispatch_token(self):
        config = (ROOT / "workers" / "crawl-watchdog" / "wrangler.jsonc").read_text(
            encoding="utf-8")
        deploy = (ROOT / ".github" / "workflows" /
                  "deploy-crawl-watchdog.yml").read_text(encoding="utf-8")
        worker = (ROOT / "workers" / "crawl-watchdog" / "src" /
                  "index.mjs").read_text(encoding="utf-8")
        self.assertIn('"crons": ["7,22,37,52 * * * *"]', config)
        self.assertIn('"required": ["GITHUB_TOKEN"]', config)
        self.assertIn("CRAWL_WATCHDOG_GITHUB_TOKEN", deploy)
        self.assertIn("--secrets-file", deploy)
        self.assertIn("/dispatches", worker)
        self.assertIn('trigger_source: "backup_watchdog"', worker)


if __name__ == "__main__":
    unittest.main()
