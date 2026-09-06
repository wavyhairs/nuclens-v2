import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import urlopen

from tools import gold_labeler


def candidate(case_id: str, position: int) -> dict:
    return {
        "id": case_id,
        "left_title": f"A {position}",
        "right_title": f"B {position}",
        "a": {
            "source_hash": f"a{position}", "title": f"원전 {position}호기 정지",
            "original_title": f"A {position}", "published_at": "2026-09-01T00:00:00+00:00",
            "publisher": "source-a", "url": "https://example.com/a",
            "summary": "A summary",
        },
        "b": {
            "source_hash": f"b{position}", "title": f"원전 {position}호기 재가동",
            "original_title": f"B {position}", "published_at": "2026-09-02T00:00:00+00:00",
            "publisher": "source-b", "url": "https://example.com/b",
            "summary": "B summary",
        },
        "metadata": {
            "date_gap_days": 1, "shared_entities": ["khnp"],
            "cached_review_not_gold": {"same_event": True},
            "model_comparison": {"status": "NOT_EVALUATED"},
        },
        "human_label": None,
        "reason_code": None,
        "human_notes": None,
        "label_status": "HUMAN_LABEL_REQUIRED",
    }


class GoldLabelerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / "identity_candidates.json"
        self.labels = self.root / "labels.json"
        payload = {
            "schema_version": 2,
            "task": "IDENTITY_REVIEW",
            "label_contract": list(gold_labeler.LABELS),
            "reason_codes": ["same_action", "different_action", "insufficient_context"],
            "cases": [candidate(f"pair-{index:03d}", index) for index in range(1, 76)],
        }
        self.fixture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def store(self):
        return gold_labeler.LabelStore(self.fixture, self.labels)

    def test_candidate_load_and_first_unlabelled_resume(self):
        store = self.store()
        self.assertEqual(len(store.cases), 75)
        store.save_label("pair-001", "MERGE", "same_action")
        store.save_label("pair-002", "SEPARATE", "different_action")
        self.assertEqual(store.public_state()["start_id"], "pair-003")

    def test_human_label_survives_reload_and_can_be_modified(self):
        store = self.store()
        store.save_label("pair-001", "MERGE", "same_action")
        reloaded = self.store()
        self.assertEqual(reloaded.current_labels()["pair-001"]["human_label"], "MERGE")
        reloaded.save_label("pair-001", "SEPARATE", "different_stage")
        edited = self.store().current_labels()["pair-001"]
        self.assertEqual((edited["human_label"], edited["reason_code"]),
                         ("SEPARATE", "different_stage"))

    def test_atomic_save_flushes_then_replaces_without_residue(self):
        store = self.store()
        with mock.patch("tools.gold_labeler.os.replace", wraps=gold_labeler.os.replace) as replace:
            store.save_label("pair-001", "MERGE", "same_action")
        replace.assert_called_once()
        self.assertEqual(json.loads(self.labels.read_text(encoding="utf-8"))["labels"]
                         ["pair-001"]["human_label"], "MERGE")
        self.assertEqual(list(self.root.glob(".labels.json.*.tmp")), [])

    def test_invalid_label_and_reason_are_rejected(self):
        store = self.store()
        with self.assertRaises(gold_labeler.LabelValidationError):
            store.save_label("pair-001", "MAYBE", "same_action")
        with self.assertRaises(gold_labeler.LabelValidationError):
            store.save_label("pair-001", "MERGE", "different_stage")
        with self.assertRaises(gold_labeler.LabelValidationError):
            store.save_label("pair-001", "AMBIGUOUS", "other", "")

    def test_other_reason_keeps_only_its_short_note(self):
        store = self.store()
        entry = store.save_label("pair-001", "AMBIGUOUS", "other", "경계 불명확")
        self.assertEqual(entry["human_notes"], "경계 불명확")
        entry = store.save_label("pair-002", "MERGE", "same_action", "discard me")
        self.assertIsNone(entry["human_notes"])

    def test_keyboard_contract_maps_m_s_a(self):
        self.assertEqual(gold_labeler.KEYBOARD_LABELS,
                         {"m": "MERGE", "s": "SEPARATE", "a": "AMBIGUOUS"})
        javascript = (gold_labeler.ASSET_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("data.shortcuts.labels[key]", javascript)
        self.assertIn('event.key === "Enter"', javascript)

    def test_first60_and_unlabelled_filters(self):
        store = self.store()
        cases = store.cases
        labels = {"pair-001": {}, "pair-061": {}}
        self.assertEqual(len(gold_labeler.filter_case_ids(cases, labels, "first60")), 60)
        unlabeled = gold_labeler.filter_case_ids(cases, labels, "unlabeled")
        self.assertEqual(len(unlabeled), 73)
        self.assertNotIn("pair-001", unlabeled)
        self.assertNotIn("pair-061", unlabeled)

    def test_first60_completion_enables_evaluation_ready_only_at_sixty(self):
        store = self.store()
        for index in range(1, 61):
            store.save_label(f"pair-{index:03d}", "MERGE", "same_action")
        counts = store.counts()
        self.assertTrue(counts["first60_complete"])
        self.assertTrue(counts["evaluation_ready"])
        self.assertEqual(counts["first60_labeled"], 60)

    def test_clear_supports_previous_label_correction(self):
        store = self.store()
        store.save_label("pair-001", "MERGE", "same_action")
        store.clear_label("pair-001")
        self.assertNotIn("pair-001", self.store().current_labels())

    def test_sidecar_save_does_not_damage_candidate_fixture(self):
        production_fixture = gold_labeler.DEFAULT_FIXTURE
        before = hashlib.sha256(production_fixture.read_bytes()).hexdigest()
        store = gold_labeler.LabelStore(production_fixture, self.labels)
        store.save_label(store.cases[0]["id"], "MERGE", "same_action")
        after = hashlib.sha256(production_fixture.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_export_updates_canonical_fixture_and_calls_validator(self):
        store = self.store()
        store.save_label("pair-001", "SEPARATE", "different_time")
        fake_audit = {"ok": True, "errors": [], "warnings": []}
        with mock.patch("tools.gold_labeler.audit", return_value=fake_audit) as validator:
            report = store.export()
        validator.assert_called_once_with(self.fixture.parent)
        exported = json.loads(self.fixture.read_text(encoding="utf-8"))
        first, second = exported["cases"][:2]
        self.assertEqual((first["human_label"], first["reason_code"], first["label_status"]),
                         ("SEPARATE", "different_time", "HUMAN_LABELLED"))
        self.assertEqual(second["label_status"], "HUMAN_LABEL_REQUIRED")
        self.assertIn("different_time", exported["reason_codes"])
        self.assertTrue(report["ok"])

    def test_exported_copy_passes_real_gold_validator(self):
        source_dir = gold_labeler.DEFAULT_FIXTURE.parent
        fixture_dir = self.root / "fixtures"
        shutil.copytree(source_dir, fixture_dir)
        fixture = fixture_dir / "identity_candidates.json"
        store = gold_labeler.LabelStore(fixture, self.labels)
        store.save_label(store.cases[0]["id"], "SEPARATE", "different_time")
        report = store.export()
        self.assertTrue(report["ok"], report["errors"])

    def test_validate_cli_logic_integrates_canonical_audit(self):
        store = self.store()
        fake_audit = {"ok": True, "errors": [], "warnings": []}
        with mock.patch("tools.gold_labeler.audit", return_value=fake_audit) as validator:
            report = store.validate_state()
        validator.assert_called_once_with(self.fixture.parent)
        self.assertTrue(report["ok"])

    def test_public_state_hides_model_verdict_until_reference_request(self):
        store = self.store()
        state_text = json.dumps(store.public_state())
        self.assertNotIn("cached_review_not_gold", state_text)
        reference = store.reference("pair-001")
        self.assertIn("cached_review_not_gold", reference)

    def test_server_binds_only_to_loopback_and_serves_state(self):
        store = self.store()
        server = gold_labeler.create_server(store, 0)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/state",
                         timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            with urlopen(f"http://127.0.0.1:{server.server_address[1]}/",
                         timeout=3) as response:
                page = response.read().decode("utf-8")
            self.assertEqual(payload["counts"]["total"], 75)
            self.assertIn("Identity Gold", page)
            self.assertIn("모델 판정은 기본 숨김", page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_no_gemini_call_is_reachable(self):
        import gemini_client
        with mock.patch.object(gemini_client, "call_json") as call_json:
            store = self.store()
            store.public_state()
            store.save_label("pair-001", "MERGE", "same_action")
        call_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
