import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import urlopen

from tools import review_gold_labeler
from tools.sol_provisional_gold import candidate_sha256


class ReviewGoldLabelerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _fixture(self, task, payload):
        path = self.root / f"{task}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _provisional(self, fixture, task, labels):
        path = self.root / f"{task}.sol.json"
        path.write_text(json.dumps({
            "task": task.upper(), "status": "AI_ASSISTED_PROVISIONAL_NOT_GOLD",
            "candidate_sha256": candidate_sha256(fixture), "labels": labels,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def test_curation_sidecar_and_export_preserve_human_dimensions(self):
        fixture = self._fixture("curation", {
            "task": "CURATION",
            "dimension_contract": {"event_boundary": ["PASS", "FAIL"]},
            "cases": [{"id": "c1", "human_label": None,
                       "human_dimensions": {"event_boundary": None},
                       "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        labels = self.root / "labels.json"
        store = review_gold_labeler.ReviewLabelStore("curation", fixture, labels)
        store.save_label("c1", {"human_label": "REPAIR",
                                  "human_dimensions": {"event_boundary": "FAIL"},
                                  "required_repair": "사건을 분리", "human_notes": None})
        self.assertIsNone(json.loads(fixture.read_text(encoding="utf-8"))
                          ["cases"][0]["human_label"])
        with mock.patch("tools.review_gold_labeler.audit",
                        return_value={"ok": True, "errors": [], "warnings": []}):
            store.export()
        exported = json.loads(fixture.read_text(encoding="utf-8"))["cases"][0]
        self.assertEqual(exported["human_label"], "REPAIR")
        self.assertEqual(exported["human_dimensions"]["event_boundary"], "FAIL")
        self.assertEqual(exported["label_status"], "HUMAN_LABELLED")

    def test_export_does_not_normalize_unreviewed_candidates(self):
        pending = {"id": "c2", "human_label": None,
                   "human_dimensions": {"event_boundary": None},
                   "human_notes": None, "label_status": "HUMAN_LABEL_REQUIRED"}
        fixture = self._fixture("curation", {
            "task": "CURATION",
            "dimension_contract": {"event_boundary": ["PASS", "FAIL"]},
            "cases": [
                {"id": "c1", "human_label": None,
                 "human_dimensions": {"event_boundary": None},
                 "human_notes": None, "label_status": "HUMAN_LABEL_REQUIRED"},
                pending,
            ],
        })
        store = review_gold_labeler.ReviewLabelStore(
            "curation", fixture, self.root / "labels.json")
        store.save_label("c1", {"human_label": "PASS",
                                  "human_dimensions": {"event_boundary": "PASS"},
                                  "required_repair": None, "human_notes": None})
        with mock.patch("tools.review_gold_labeler.audit",
                        return_value={"ok": True, "errors": [], "warnings": []}):
            store.export()
        exported = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
        self.assertEqual(exported[1], pending)

    def test_export_preserves_unmodified_user_specified_status(self):
        fixture = self._fixture("curation", {
            "task": "CURATION", "dimension_contract": {"scope": ["PASS", "FAIL"]},
            "cases": [{"id": "c1", "human_label": "REPAIR",
                       "human_dimensions": {"scope": "FAIL"},
                       "required_repair": "범위 수정", "human_notes": None,
                       "label_status": "USER_SPECIFIED"}],
        })
        store = review_gold_labeler.ReviewLabelStore(
            "curation", fixture, self.root / "labels.json")
        with mock.patch("tools.review_gold_labeler.audit",
                        return_value={"ok": True, "errors": [], "warnings": []}):
            store.export()
        exported = json.loads(fixture.read_text(encoding="utf-8"))["cases"][0]
        self.assertEqual(exported["label_status"], "USER_SPECIFIED")

    def test_semantic_pass_rejects_error_types(self):
        fixture = self._fixture("semantic", {
            "task": "SEMANTIC", "error_type_contract": ["FACT_ERROR"],
            "cases": [{"id": "s1", "claim": "x", "source_evidence": {},
                       "human_label": None, "human_error_types": None,
                       "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        store = review_gold_labeler.ReviewLabelStore(
            "semantic", fixture, self.root / "labels.json")
        with self.assertRaises(review_gold_labeler.LabelValidationError):
            store.save_label("s1", {"human_label": "PASS",
                                     "human_error_types": ["FACT_ERROR"]})
        saved = store.save_label("s1", {"human_label": "BLOCK",
                                         "human_error_types": ["FACT_ERROR"]})
        self.assertEqual(saved["human_error_types"], ["FACT_ERROR"])

    def test_curation_requires_every_dimension(self):
        fixture = self._fixture("curation", {
            "task": "CURATION",
            "dimension_contract": {"event_boundary": ["PASS", "FAIL"],
                                   "scope": ["PASS", "FAIL"]},
            "cases": [{"id": "c1", "human_label": None,
                       "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        store = review_gold_labeler.ReviewLabelStore(
            "curation", fixture, self.root / "labels.json")
        with self.assertRaises(review_gold_labeler.LabelValidationError):
            store.save_label("c1", {"human_label": "PASS",
                                     "human_dimensions": {
                                         "event_boundary": "PASS", "scope": None}})

    def test_sidecar_hash_mismatch_is_rejected(self):
        fixture = self._fixture("semantic", {
            "task": "SEMANTIC", "error_type_contract": [], "cases": []})
        labels = self.root / "labels.json"
        labels.write_text(json.dumps({"task": "SEMANTIC", "fixture_sha256": "stale",
                                      "labels": {}}), encoding="utf-8")
        with self.assertRaises(review_gold_labeler.LabelValidationError):
            review_gold_labeler.ReviewLabelStore("semantic", fixture, labels)

    def test_public_state_excludes_selection_metadata_until_reference(self):
        fixture = self._fixture("semantic", {
            "task": "SEMANTIC", "error_type_contract": [],
            "cases": [{"id": "s1", "claim": "x", "source_evidence": {},
                       "selection_metadata_not_gold": {"review_focus": "FACT_ERROR"},
                       "human_label": None, "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        store = review_gold_labeler.ReviewLabelStore(
            "semantic", fixture, self.root / "labels.json")
        self.assertNotIn("review_focus", json.dumps(store.public_state()))
        self.assertEqual(store.reference("s1")["selection_metadata_not_gold"]
                         ["review_focus"], "FACT_ERROR")

    def test_server_is_loopback_only_and_serves_task_ui(self):
        fixture = self._fixture("semantic", {
            "task": "SEMANTIC", "error_type_contract": [],
            "cases": [{"id": "s1", "claim": "x", "source_evidence": {},
                       "human_label": None, "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        store = review_gold_labeler.ReviewLabelStore(
            "semantic", fixture, self.root / "labels.json")
        server = review_gold_labeler.create_server(store, 0)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/state",
                         timeout=3) as response:
                state = json.loads(response.read().decode("utf-8"))
            with urlopen(f"http://127.0.0.1:{server.server_address[1]}/",
                         timeout=3) as response:
                page = response.read().decode("utf-8")
            self.assertEqual(state["task"], "SEMANTIC")
            self.assertIn("선정 참고정보 보기 · 정답 아님", page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_sol_provisional_is_not_a_human_label_until_approved(self):
        fixture = self._fixture("semantic", {
            "task": "SEMANTIC", "error_type_contract": ["FACT_ERROR"],
            "cases": [{"id": "s1", "claim": "x", "source_evidence": {},
                       "human_label": None, "human_error_types": None,
                       "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        provisional = self._provisional(fixture, "semantic", {"s1": {
            "provisional_label": "UNSUPPORTED", "error_types": ["contradiction"],
            "confidence": "low", "reason": "근거와 모순된다.",
            "evidence_reference": "source fact", "review_priority": 160,
            "review_reasons": ["low_confidence", "contradiction"],
            "human_reviewed": False,
        }})
        store = review_gold_labeler.ReviewLabelStore(
            "semantic", fixture, self.root / "labels.json", provisional)
        self.assertEqual(store.current_labels(), {})
        self.assertEqual(store.counts()["hard_cases"], 1)
        self.assertEqual(store.public_state()["suggested_human"]["s1"]
                         ["human_label"], "BLOCK")
        with mock.patch("tools.review_gold_labeler.audit",
                        return_value={"ok": True, "errors": [], "warnings": []}):
            store.export()
        untouched = json.loads(fixture.read_text("utf-8"))["cases"][0]
        self.assertIsNone(untouched["human_label"])
        self.assertEqual(untouched["label_status"], "HUMAN_LABEL_REQUIRED")
        approved = store.approve_provisional("s1")
        self.assertTrue(approved["human_reviewed"])
        self.assertEqual((approved["human_label"], approved["human_error_types"]),
                         ("BLOCK", ["FACT_ERROR"]))
        with mock.patch("tools.review_gold_labeler.audit",
                        return_value={"ok": True, "errors": [], "warnings": []}):
            store.export()
        reloaded = review_gold_labeler.ReviewLabelStore(
            "semantic", fixture, self.root / "labels.json", provisional)
        self.assertEqual(reloaded.current_labels()["s1"]["human_label"], "BLOCK")

    def test_curation_ambiguous_requires_change_or_needs_review(self):
        fixture = self._fixture("curation", {
            "task": "CURATION", "dimension_contract": {
                "event_boundary": ["PASS", "FAIL", "AMBIGUOUS"],
                "scope": ["PASS", "FAIL"]},
            "cases": [{"id": "c1", "human_label": None,
                       "human_dimensions": {"event_boundary": None, "scope": None},
                       "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        provisional = self._provisional(fixture, "curation", {"c1": {
            "provisional_label": "AMBIGUOUS", "error_types": ["event_boundary"],
            "confidence": "low", "reason": "사건 경계가 불분명하다.",
            "evidence_reference": "source title", "review_priority": 250,
            "review_reasons": ["low_confidence", "ambiguous", "event_boundary"],
            "human_reviewed": False,
        }})
        store = review_gold_labeler.ReviewLabelStore(
            "curation", fixture, self.root / "labels.json", provisional)
        with self.assertRaises(review_gold_labeler.LabelValidationError):
            store.approve_provisional("c1")
        store.mark_needs_review("c1")
        self.assertEqual(store.current_labels(), {})
        self.assertEqual(store.counts()["needs_review"], 1)

    def test_provisional_candidate_hash_ignores_human_export_fields(self):
        fixture = self._fixture("semantic", {
            "task": "SEMANTIC", "error_type_contract": [],
            "cases": [{"id": "s1", "claim": "x", "source_evidence": {},
                       "human_label": None, "human_error_types": None,
                       "label_status": "HUMAN_LABEL_REQUIRED"}],
        })
        before = candidate_sha256(fixture)
        payload = json.loads(fixture.read_text("utf-8"))
        payload["cases"][0].update(human_label="PASS", human_error_types=[],
                                   label_status="HUMAN_LABELLED")
        fixture.write_text(json.dumps(payload), "utf-8")
        self.assertEqual(before, candidate_sha256(fixture))


if __name__ == "__main__":
    unittest.main()
