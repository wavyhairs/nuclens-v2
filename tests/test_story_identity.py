import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import dedup
import story_cluster
import story_identity
from web import build_data
from tools import failure_domains, story_identity_migration


def article(article_hash, title, *, country, actor, asset, story_id="", source=""):
    row = {
        "hash": article_hash,
        "title": title,
        "title_kr": title,
        "publisher": "fixture",
        "story_fingerprint": {
            "countries": [country],
            "actors": [actor],
            "assets": [asset],
            "event_family": "incident_safety",
            "drivers": ["fixture"],
        },
    }
    if story_id:
        row["story_id"] = story_id
        row["story_id_source"] = source or "history"
    return row


class DisplayIdentitySeparationTests(unittest.TestCase):
    def test_consolidation_never_inherits_a_member_history_id(self):
        representative = article(
            "palisades", "Palisades 원전 연료 장전", country="USA",
            actor="Holtec", asset="Palisades NPP")
        poisoned_member = article(
            "zap", "자포리자 원전 블랙아웃", country="Ukraine",
            actor="IAEA", asset="Zaporizhzhia NPP",
            story_id="story-zap-history")

        story_cluster.consolidate_story_metadata(
            representative, [representative, poisoned_member],
            relation="merge", stage="semantic_story")

        self.assertEqual(representative["story_id"], "story-palisades")
        self.assertEqual(poisoned_member["story_id"], "story-zap-history")
        self.assertEqual(
            {row["story_id"] for row in representative["display_group_members"]},
            {"story-palisades", "story-zap-history"},
        )

    def test_adversarial_llm_merge_cannot_move_zaporizhzhia_identity(self):
        rows = [
            article("palisades", "Palisades 원전 연료 장전", country="USA",
                    actor="Holtec", asset="Palisades NPP"),
            article("zap", "자포리자 원전 블랙아웃", country="Ukraine",
                    actor="IAEA", asset="Zaporizhzhia NPP",
                    story_id="story-zap-history"),
            article("paks", "Paks 원전 프로젝트", country="Hungary",
                    actor="Rosatom", asset="Paks II"),
            article("hamaoka", "Hamaoka 원전 안전공사", country="Japan",
                    actor="Chubu Electric", asset="Hamaoka NPP"),
            article("wna", "WNA 원자력 투자 전망", country="UK",
                    actor="WNA", asset="nuclear investment"),
        ]
        scores = {row["hash"]: 100 - index for index, row in enumerate(rows)}
        response = {
            "groups": [{
                "indices": list(range(len(rows))),
                "relation": "merge",
                "reason": "adversarial false merge",
                "fingerprint": {},
            }]
        }
        with mock.patch.object(dedup, "is_available", return_value=True), \
                mock.patch.object(dedup, "call_json", return_value=response):
            kept, dropped = dedup._dedup_articles_impl(
                rows, scores, prompt="fixture", label="fixture", stage="semantic_story")

        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 4)
        self.assertEqual(kept[0]["story_id"], "story-palisades")
        self.assertEqual(rows[1]["story_id"], "story-zap-history")
        self.assertEqual(len({row["story_id"] for row in rows}), 5)
        self.assertEqual(len(kept[0]["display_group_members"]), 5)


class LegacyRegistryTests(unittest.TestCase):
    def test_conflicting_legacy_registry_cannot_infect_a_new_article(self):
        legacy_id = "story-poisoned"
        palisades = article(
            "old-us", "Palisades 원전 재가동", country="USA",
            actor="Holtec", asset="Palisades NPP", story_id=legacy_id)
        zaporizhzhia = article(
            "old-ua", "자포리자 원전 블랙아웃", country="Ukraine",
            actor="IAEA", asset="Zaporizhzhia NPP", story_id=legacy_id)
        candidate = article(
            "new-us", "Palisades 원전 재가동 후속", country="USA",
            actor="Holtec", asset="Palisades NPP")

        result = story_identity.inherit(
            candidate, palisades, records=[palisades, zaporizhzhia],
            fingerprint_confirmed=True, evidence_confirmed=False)

        self.assertFalse(result.inherited)
        self.assertEqual(result.status, "legacy_rejected")
        self.assertEqual(candidate["story_id"], "story-new-us")

    def test_uncontaminated_confirmed_followup_keeps_the_history_id(self):
        prior = article(
            "old", "Palisades 원전 재가동", country="USA",
            actor="Holtec", asset="Palisades NPP", story_id="story-stable")
        candidate = article(
            "new", "Palisades 원전 재가동 후속", country="USA",
            actor="Holtec", asset="Palisades NPP")
        result = story_identity.inherit(
            candidate, prior, records=[prior],
            fingerprint_confirmed=True, evidence_confirmed=False)
        self.assertTrue(result.inherited)
        self.assertEqual(candidate["story_id"], "story-stable")
        self.assertTrue(story_identity.is_canonical(candidate))


class WebIdentityIsolationTests(unittest.TestCase):
    @staticmethod
    def issue(issue_id, article_hash):
        representative = {"hash": article_hash, "title_kr": article_hash}
        return {
            "issue_id": issue_id, "representative": representative,
            "members": [representative], "first_seen": "2026-09-01",
            "last_seen": "2026-09-01", "match_diagnostics": [],
        }

    def test_local_collision_is_forced_split_and_build_can_continue(self):
        issues = [self.issue("story-poisoned", "palisades"),
                  self.issue("story-poisoned", "zap")]
        diagnostic = build_data.resolve_local_issue_id_conflicts(issues)
        self.assertEqual(diagnostic["status"], "degraded")
        self.assertEqual({row["issue_id"] for row in issues},
                         {"issue-palisades", "issue-zap"})
        self.assertTrue(all(row["identity_status"] == "quarantined" for row in issues))
        build_data.validate_issue_catalog_ids(issues)

    def test_systemic_collision_still_fails_closed(self):
        issues = [self.issue("story-poisoned", f"hash-{index}") for index in range(6)]
        with self.assertRaisesRegex(ValueError, "systemic"):
            build_data.resolve_local_issue_id_conflicts(issues, max_local=5)

    def test_legacy_id_alone_is_not_a_web_merge_reason(self):
        left = article("left", "서로 다른 표현 A", country="USA",
                       actor="A", asset="")
        right = article("right", "완전히 무관한 표현 B", country="USA",
                        actor="B", asset="")
        left.update(story_id="story-legacy", story_id_source="history")
        right.update(story_id="story-legacy", story_id_source="history")
        matched, _score, diag = build_data.issue_similarity(left, right)
        self.assertFalse(matched)
        self.assertNotEqual(diag["method"], "story_id")

    def test_legacy_id_plus_concrete_asset_evidence_preserves_followup(self):
        left = article("left", "표현 A", country="Ukraine",
                       actor="IAEA", asset="Zaporizhzhia NPP")
        right = article("right", "표현 B", country="Ukraine",
                        actor="IAEA", asset="Zaporizhzhia NPP")
        left.update(story_id="story-legacy", story_id_source="history")
        right.update(story_id="story-legacy", story_id_source="history")
        matched, _score, diag = build_data.issue_similarity(left, right)
        self.assertTrue(matched)
        self.assertEqual(diag["method"], "legacy_story_id_confirmed")


class MigrationAuditTests(unittest.TestCase):
    def test_registry_audit_counts_only_concrete_conflicts(self):
        rows = [
            article("us", "US", country="USA", actor="Holtec", asset="Palisades",
                    story_id="story-bad"),
            article("ua", "UA", country="Ukraine", actor="IAEA", asset="Zaporizhzhia",
                    story_id="story-bad"),
            article("ok", "OK", country="Korea", actor="KHNP", asset="Shin Hanul",
                    story_id="story-ok"),
        ]
        report = story_identity.audit_registry(rows)
        self.assertEqual(report["legacy_story_count"], 2)
        self.assertEqual(report["identity_conflict_story_count"], 1)
        self.assertEqual(report["automatic_split_count"], 1)
        self.assertEqual(report["preservable_story_count"], 1)

    def test_apply_splits_only_the_infected_non_owner_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"date": "2026-08-01", "hash": "owner", "title_kr": "A"},
                {"date": "2026-08-02", "hash": "infected", "title_kr": "B",
                 "story_id": "story-owner", "story_id_source": "history"},
            ]
            path = root / "delivery_log.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                            encoding="utf-8")
            report = {"conflicts": [{
                "story_id": "story-owner", "left_hash": "owner",
                "right_hash": "infected",
            }]}
            self.assertEqual(
                story_identity_migration.apply_delivery_migration(root, report), 1)
            migrated = [json.loads(line) for line in path.read_text(
                encoding="utf-8").splitlines()]
            self.assertNotIn("story_id", migrated[0])
            self.assertEqual(migrated[1]["story_id"], "story-infected")
            self.assertEqual(
                story_identity_migration.apply_delivery_migration(root, report), 0)

    def test_migration_preserves_compatible_followups_when_named_owner_is_outside_window(self):
        conflicts = [
            {"story_id": "story-legacy", "left_hash": "zap-a",
             "right_hash": "palisades"},
            {"story_id": "story-legacy", "left_hash": "zap-b",
             "right_hash": "palisades"},
        ]
        split = story_identity_migration._conflicted_non_owner_hashes(conflicts)
        self.assertEqual(split["story-legacy"], {"palisades"})


class IdentityVerdictReachesTheWorkflowTests(unittest.TestCase):
    """격리는 일어났는데 워크플로가 모르면 degraded 는 ok 와 구별되지 않는다.

    `build_mode` 는 지금까지 meta.json/status.json 안에만 있었고 그 파일을 여는
    사람은 없다.  이 클래스는 build_data 의 판정이 step output → 실패 도메인
    분류까지 실제로 전달되는지를 본다.
    """

    @staticmethod
    def issue(issue_id, article_hash):
        representative = {"hash": article_hash, "title_kr": article_hash}
        return {
            "issue_id": issue_id, "representative": representative,
            "members": [representative], "first_seen": "2026-09-01",
            "last_seen": "2026-09-01", "match_diagnostics": [],
        }

    def publish(self, diagnostics):
        """`$GITHUB_OUTPUT` 에 실제로 쓰인 key=value 를 돌려준다."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "github_output"
            out.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
                build_data.publish_build_mode(diagnostics)
            written = {}
            for line in out.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    written[key] = value
            return written

    def test_a_local_conflict_is_carried_to_the_workflow_as_degraded(self):
        """팰리세이즈/자포리자 그 조합 — 수집은 성공, 웹만 degraded 다."""
        issues = [self.issue("story-poisoned", "palisades"),
                  self.issue("story-poisoned", "zap")]
        diagnostics = build_data.resolve_local_issue_id_conflicts(issues)
        self.assertEqual("degraded", diagnostics["status"])

        written = self.publish(diagnostics)
        self.assertEqual("degraded", written["build_mode"])
        self.assertEqual("2", written["identity_quarantined"])

        verdict = failure_domains.classify(
            "crawl",
            {"collect": "success", "state": "success", "web_build": "success",
             "web_deploy": "success", "web_smoke": "success"},
            build_mode=written["build_mode"],
            identity_quarantined=int(written["identity_quarantined"]))
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("degraded", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])

    def test_the_same_verdict_preserves_a_sent_daily_brief(self):
        issues = [self.issue("story-poisoned", "palisades"),
                  self.issue("story-poisoned", "zap")]
        diagnostics = build_data.resolve_local_issue_id_conflicts(issues)
        written = self.publish(diagnostics)
        verdict = failure_domains.classify(
            "daily-brief",
            {"plan": "success", "claim": "success", "send": "success",
             "confirm": "success", "web_build": "success", "web_deploy": "success",
             "web_smoke": "success"},
            build_mode=written["build_mode"],
            identity_quarantined=int(written["identity_quarantined"]))
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("degraded", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])

    def test_a_clean_build_reports_ok_not_silence(self):
        """조용히 아무것도 안 쓰면 '측정 안 함'과 '정상'을 구별할 수 없다."""
        diagnostics = build_data.resolve_local_issue_id_conflicts(
            [self.issue("story-a", "a"), self.issue("story-b", "b")])
        written = self.publish(diagnostics)
        self.assertEqual("ok", written["build_mode"])
        self.assertEqual("0", written["identity_quarantined"])

    def test_systemic_corruption_still_fails_closed_and_reports_nothing(self):
        """fail-closed 가 degraded 로 내려앉으면 안전장치의 의미가 사라진다.

        예외로 죽으므로 publish_build_mode 에 닿지 못하고, build_mode 가 없는
        회차는 분류기에서 degraded 가 아니라 web failure 로 읽힌다.
        """
        issues = [self.issue("story-poisoned", f"hash-{index}") for index in range(6)]
        with self.assertRaisesRegex(ValueError, "systemic"):
            build_data.resolve_local_issue_id_conflicts(issues, max_local=5)

        verdict = failure_domains.classify(
            "crawl",
            {"collect": "success", "state": "success", "web_build": "failure",
             "web_deploy": "skipped", "web_smoke": "skipped"},
            build_mode="", identity_quarantined=0)
        self.assertEqual("failure", verdict["web_status"])
        # 그래도 수집은 오염되지 않는다 — 그것이 이 작업의 전부다.
        self.assertEqual("success", verdict["core_status"])
        self.assertFalse(verdict["job_should_fail"])

    def test_reporting_never_becomes_a_new_failure_mode(self):
        """보고 경로가 빌드를 죽이면 빌드를 살리려던 변경이 빌드를 죽인다."""
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(Path("/") / "nope" / "x")}):
            build_data.publish_build_mode({"status": "degraded",
                                           "quarantined_cluster_count": 2})


if __name__ == "__main__":
    unittest.main()
