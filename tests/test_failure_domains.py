"""Failure domains: collection/briefing must not inherit a web build's verdict.

실측 2026-09-01(crawl run 33508671552 외 4건): `Collect news` 성공 → `Commit state`
성공 → `Deploy web to Cloudflare Pages` 안의 `python web/build_data.py` 가
`duplicate issue_id ... 팰리세이즈 vs 자포리자` 로 죽음.  정상 수집 다섯 회차가
'크롤 실패'로 읽혔고, 정작 웹이 깨졌다는 사실은 운영 알림으로 한 번도 나가지
않았다.  이 파일은 그 두 축이 다시 붙지 않는지를 본다.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools import failure_domains as fd


CRAWL_HEALTHY = {"collect": "success", "state": "success"}
BRIEF_HEALTHY = {"plan": "success", "claim": "success",
                 "send": "success", "confirm": "success"}
WEB_HEALTHY = {"web_build": "success", "web_deploy": "success",
               "web_smoke": "success"}


class CrawlDomainTests(unittest.TestCase):
    """수집이 끝나고 상태가 커밋됐다면 그 회차의 수집은 성공이다."""

    def test_local_identity_conflict_keeps_collection_success(self):
        """국소 충돌 회차: 수집 성공 보존 + degraded 별도 관측."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, **WEB_HEALTHY},
                              build_mode="degraded", identity_quarantined=2)
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("degraded", verdict["web_status"])
        # degraded 는 ok 로 위장하지 않는다.
        self.assertNotEqual("success", verdict["web_status"])
        self.assertEqual(2, verdict["identity_quarantined"])
        # 그리고 잡을 빨갛게 만들지 않는다 — 사이트도 브리핑도 정상이다.
        self.assertFalse(verdict["job_should_fail"])

    def test_web_build_failure_never_reddens_a_finished_collection(self):
        """이번 사고 그 자체 — build_data 가 죽어도 수집은 success 다."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, "web_build": "failure",
                                        "web_deploy": "skipped",
                                        "web_smoke": "failure"})
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("failure", verdict["web_status"])
        self.assertEqual(["web_build", "web_smoke"], verdict["web_failed_stages"])
        self.assertFalse(verdict["job_should_fail"])

    def test_collection_failure_is_still_a_crawl_failure(self):
        """도메인을 가른다고 수집 실패까지 초록이 되면 안 된다."""
        verdict = fd.classify("crawl", {"collect": "failure", "state": "success",
                                        **WEB_HEALTHY})
        self.assertEqual("failure", verdict["core_status"])
        self.assertEqual(["collect"], verdict["core_failed_stages"])
        self.assertTrue(verdict["job_should_fail"])

    def test_unpushed_state_is_a_collection_failure(self):
        """수집은 '돌았다'가 아니라 '결과가 main 에 남았다'까지다."""
        verdict = fd.classify("crawl", {"collect": "success", "state": "failure",
                                        **WEB_HEALTHY})
        self.assertEqual("failure", verdict["core_status"])
        self.assertTrue(verdict["job_should_fail"])

    def test_deploy_window_skip_is_not_a_web_failure(self):
        """3시간 간격 게이트가 걸러 낸 회차를 실패로 부르면 거짓 경보가 된다."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, "web_build": "skipped",
                                        "web_deploy": "skipped",
                                        "web_smoke": "skipped"})
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("skipped", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])


class BriefDomainTests(unittest.TestCase):
    """이미 텔레그램으로 나간 브리핑을 뒤의 웹 오류가 되돌릴 수는 없다."""

    def test_sent_brief_survives_a_degraded_web_build(self):
        verdict = fd.classify("daily-brief", {**BRIEF_HEALTHY, **WEB_HEALTHY},
                              build_mode="degraded", identity_quarantined=3)
        self.assertEqual("briefing", verdict["core_name"])
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("degraded", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])

    def test_sent_brief_survives_a_failed_web_build(self):
        verdict = fd.classify("daily-brief", {**BRIEF_HEALTHY,
                                              "web_build": "failure",
                                              "web_deploy": "skipped",
                                              "web_smoke": "skipped"})
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("failure", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])

    def test_send_failure_is_still_a_brief_failure(self):
        verdict = fd.classify("daily-brief", {**BRIEF_HEALTHY, "send": "failure",
                                              **WEB_HEALTHY})
        self.assertEqual("failure", verdict["core_status"])
        self.assertTrue(verdict["job_should_fail"])

    def test_unrecorded_send_is_a_brief_failure(self):
        """confirm 실패는 중복 발송 위험이다 — 웹과 같은 취급을 하면 안 된다."""
        verdict = fd.classify("daily-brief", {**BRIEF_HEALTHY, "confirm": "failure",
                                              **WEB_HEALTHY})
        self.assertEqual("failure", verdict["core_status"])
        self.assertTrue(verdict["job_should_fail"])

    def test_claim_skip_leaves_the_brief_domain_skipped(self):
        """claim 이 막혀 발송 자체가 없던 날은 실패도 성공도 아니다."""
        verdict = fd.classify("daily-brief", {"plan": "skipped", "claim": "skipped",
                                              "send": "skipped", "confirm": "skipped",
                                              "web_build": "skipped",
                                              "web_deploy": "skipped",
                                              "web_smoke": "skipped"})
        self.assertEqual("skipped", verdict["core_status"])
        self.assertEqual("skipped", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])


class DegradedIsNotFailClosedTests(unittest.TestCase):
    """degraded 와 fail-closed 를 섞으면 둘 다 의미를 잃는다."""

    def test_a_crashed_build_is_never_downgraded_to_degraded(self):
        """systemic corruption 으로 죽은 빌드가 남긴 낡은 값에 속지 않는다."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, "web_build": "failure",
                                        "web_deploy": "skipped",
                                        "web_smoke": "skipped"},
                              build_mode="degraded", identity_quarantined=9)
        self.assertEqual("failure", verdict["web_status"])

    def test_ok_build_mode_stays_success(self):
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, **WEB_HEALTHY},
                              build_mode="ok")
        self.assertEqual("success", verdict["web_status"])

    def test_missing_build_mode_stays_success(self):
        """build_mode 를 못 받은 회차를 degraded 로 부르면 늑대소년이 된다."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, **WEB_HEALTHY},
                              build_mode="")
        self.assertEqual("success", verdict["web_status"])


class ReportingTests(unittest.TestCase):
    """판정이 사람 눈에 닿는 자리까지 가는지 — 초록 잡 뒤에 숨으면 안 된다."""

    def test_summary_names_both_domains_separately(self):
        outcomes = {**CRAWL_HEALTHY, "web_build": "failure",
                    "web_deploy": "skipped", "web_smoke": "skipped"}
        verdict = fd.classify("crawl", outcomes)
        summary = fd.render_summary(verdict, outcomes)
        self.assertIn("수집: `success`", summary)
        self.assertIn("웹 빌드·배포: `failure`", summary)
        self.assertIn("웹 데이터 빌드=failure", summary)

    def test_summary_states_the_quarantine_count_on_a_degraded_build(self):
        outcomes = {**CRAWL_HEALTHY, **WEB_HEALTHY}
        verdict = fd.classify("crawl", outcomes, build_mode="degraded",
                              identity_quarantined=2)
        summary = fd.render_summary(verdict, outcomes)
        self.assertIn("`degraded`", summary)
        self.assertIn("2건", summary)

    def test_a_web_failure_still_raises_a_workflow_annotation(self):
        """잡은 초록이어도 실행 화면에는 남는다 — 숨기지 않는다는 계약이다."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, "web_build": "failure",
                                        "web_deploy": "skipped",
                                        "web_smoke": "skipped"})
        lines = fd.annotations(verdict)
        self.assertEqual(1, len(lines))
        self.assertTrue(lines[0].startswith("::error::"))

    def test_a_degraded_build_warns_rather_than_errors(self):
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, **WEB_HEALTHY},
                              build_mode="degraded", identity_quarantined=2)
        lines = fd.annotations(verdict)
        self.assertEqual(1, len(lines))
        self.assertTrue(lines[0].startswith("::warning::"))

    def test_a_healthy_run_says_nothing(self):
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, **WEB_HEALTHY},
                              build_mode="ok")
        self.assertEqual([], fd.annotations(verdict))

    def test_cli_writes_both_domains_to_the_step_outputs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "output"
        summary = Path(tmp.name) / "summary"
        out.write_text("", encoding="utf-8")
        summary.write_text("", encoding="utf-8")
        argv = ["failure_domains.py", "--domain", "crawl",
                "--collect-outcome", "success", "--state-outcome", "success",
                "--web-build-outcome", "failure", "--web-deploy-outcome", "skipped",
                "--web-smoke-outcome", "skipped"]
        env = {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
        old_argv, old_env = sys.argv, dict(os.environ)
        sys.argv = argv
        os.environ.update(env)
        try:
            self.assertEqual(0, fd.main())
        finally:
            sys.argv = old_argv
            os.environ.clear()
            os.environ.update(old_env)
        written = out.read_text(encoding="utf-8")
        self.assertIn("core_status=success", written)
        self.assertIn("web_status=failure", written)
        self.assertIn("웹 빌드·배포: `failure`", summary.read_text(encoding="utf-8"))

    def test_the_reporter_is_never_itself_a_gate(self):
        """보고자가 exit 1 을 더하면 도메인을 하나 더 만드는 셈이다."""
        argv = ["failure_domains.py", "--domain", "crawl",
                "--collect-outcome", "failure", "--state-outcome", "failure",
                "--web-build-outcome", "failure"]
        old_argv, old_env = sys.argv, dict(os.environ)
        sys.argv = argv
        os.environ.pop("GITHUB_OUTPUT", None)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        try:
            self.assertEqual(0, fd.main())
        finally:
            sys.argv = old_argv
            os.environ.clear()
            os.environ.update(old_env)


class WorkflowWiringTests(unittest.TestCase):
    """분류기가 옳아도 워크플로가 안 물려 있으면 아무것도 달라지지 않는다."""

    def crawl(self):
        return (ROOT / ".github" / "workflows" / "crawl.yml").read_text(
            encoding="utf-8")

    def daily(self):
        return (ROOT / ".github" / "workflows" / "daily-brief.yml").read_text(
            encoding="utf-8")

    def step(self, workflow: str, step_id: str) -> str:
        """`id: <step_id>` 부터 다음 스텝 헤더 직전까지."""
        body = workflow.split(f"id: {step_id}\n", 1)[1]
        return body.split("      - name:", 1)[0]

    def test_crawl_web_steps_carry_their_own_outcome(self):
        crawl = self.crawl()
        for step_id in ("web-build", "web-deploy", "web-smoke"):
            self.assertIn(f"id: {step_id}", crawl, f"crawl 에 {step_id} 가 없다")
            self.assertIn("continue-on-error: true", self.step(crawl, step_id),
                          f"{step_id} 실패가 수집 결과를 오염시킨다")

    def test_crawl_core_steps_still_fail_the_job(self):
        """도메인을 가른다고 수집까지 초록이 되면 워크플로가 거짓말을 한다."""
        crawl = self.crawl()
        for step_id in ("collect", "state"):
            self.assertNotIn("continue-on-error", self.step(crawl, step_id),
                             f"{step_id} 실패가 잡을 빨갛게 만들지 못한다")

    def test_crawl_builds_and_deploys_as_separate_steps(self):
        """한 덩어리면 build_data 실패와 wrangler 실패를 구별할 수 없다."""
        crawl = self.crawl()
        self.assertIn("python web/build_data.py", self.step(crawl, "web-build"))
        self.assertNotIn("wrangler", self.step(crawl, "web-build"))
        self.assertIn("wrangler", self.step(crawl, "web-deploy"))

    def test_crawl_reports_the_web_pipeline_to_the_administrator(self):
        """이 경로가 없어서 다섯 회차 동안 웹 장애 알림이 0건이었다."""
        crawl = self.crawl()
        self.assertIn('--web-build-outcome "${{ steps.web-build.outcome }}"', crawl)
        self.assertIn('--web-deploy-outcome "${{ steps.web-deploy.outcome }}"', crawl)
        self.assertIn('--pipeline-observation-id "crawl:${{ github.run_id }}"', crawl)
        self.assertIn('--build-mode "${{ steps.web-build.outputs.build_mode }}"', crawl)

    def test_crawl_publishes_the_failure_domains(self):
        crawl = self.crawl()
        self.assertIn("python tools/failure_domains.py --domain crawl", crawl)
        self.assertIn('--state-outcome "${{ steps.state.outcome }}"', crawl)
        self.assertIn('--web-smoke-outcome "${{ steps.web-smoke.outcome }}"', crawl)

    def test_crawl_still_saves_the_audit_when_only_the_web_broke(self):
        """`failure()` 는 이제 안 뜬다 — 정작 이 스텝이 노리는 회차가 그 회차다."""
        crawl = self.crawl()
        audit = crawl.split("- name: Upload full issue audit (on failure)", 1)[1]
        audit = audit.split("        with:", 1)[0]
        self.assertIn("steps.web-build.outcome == 'failure'", audit)
        self.assertIn("steps.web-deploy.outcome == 'failure'", audit)
        self.assertIn("steps.web-smoke.outcome == 'failure'", audit)

    def test_daily_brief_web_steps_carry_their_own_outcome(self):
        daily = self.daily()
        for step_id in ("web-build", "web-deploy", "render-smoke"):
            self.assertIn(f"id: {step_id}", daily, f"daily-brief 에 {step_id} 가 없다")
            self.assertIn("continue-on-error: true", self.step(daily, step_id),
                          f"{step_id} 실패가 이미 나간 브리핑을 실패로 만든다")

    def test_daily_brief_delivery_steps_still_fail_the_job(self):
        daily = self.daily()
        for step_id in ("plan", "claim", "send", "confirm"):
            self.assertNotIn("continue-on-error", self.step(daily, step_id),
                             f"{step_id} 실패가 잡을 빨갛게 만들지 못한다")

    def test_daily_brief_downstream_guards_read_the_raw_outcome(self):
        """continue-on-error 는 conclusion 만 바꾼다. 조건이 conclusion 을 보면
        실패한 빌드 위에 배포가 그대로 올라간다."""
        daily = self.daily()
        self.assertIn("steps.web-build.outcome == 'success'", daily)
        self.assertNotIn("steps.web-build.conclusion", daily)

    def test_daily_brief_publishes_the_failure_domains(self):
        daily = self.daily()
        self.assertIn("python tools/failure_domains.py --domain daily-brief", daily)
        self.assertIn('--send-outcome "${{ steps.send.outcome }}"', daily)
        self.assertIn('--build-mode "${{ steps.web-build.outputs.build_mode }}"', daily)


if __name__ == "__main__":
    unittest.main()
