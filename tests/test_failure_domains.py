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


def step_condition(workflow: str, step_id: str) -> str | None:
    """워크플로에 적힌 `if:` 를 그대로 읽는다. 없으면 None(암묵 `success()`).

    조건을 테스트에 베껴 적지 않는다 — 베껴 적으면 워크플로가 바뀌어도 모형은
    그대로라 시뮬레이션이 거짓말을 하게 된다.
    """
    block = workflow.split(f"id: {step_id}\n", 1)[1].split("      - name:", 1)[0]
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("if:"):
            continue
        value = stripped[len("if:"):].strip()
        if value.startswith((">", "|")):
            raise AssertionError(
                f"{step_id}: 접힌 `if:` 는 이 시뮬레이터가 읽지 못한다")
        return value
    return None


def evaluate_condition(condition: str | None, context: dict) -> bool:
    """`X == 'literal'` 의 && 결합과 `always()` 만 다루는 최소 평가기.

    웹 경로의 조건이 실제로 이 모양뿐이다.  다른 모양이 들어오면 조용히 참을
    돌려주는 대신 터뜨린다 — 조용한 참이 바로 이 테스트가 잡으려는 종류의
    거짓 초록이다.
    """
    if condition is None:
        # 암묵 `success()`.  스킵된 스텝도, 실패한 continue-on-error 스텝도
        # 잡 상태를 바꾸지 않으므로 웹 경로에서는 계속 참이다 — 이것이 정확히
        # 배포 창 미달 회차에 스모크가 돌던 이유다.
        return context["job_successful"]
    for term in condition.split("&&"):
        term = term.strip()
        if term == "always()":
            continue
        left, sep, right = term.partition("==")
        if not sep:
            raise AssertionError(f"평가할 수 없는 조건: {term!r}")
        key = left.strip()
        want = right.strip().strip("'\"")
        if key not in context:
            raise AssertionError(f"시뮬레이터가 모르는 값: {key!r}")
        if context[key] != want:
            return False
    return True


def simulate_crawl_web_path(workflow: str, *, should_deploy: str,
                            build: str = "success", deploy: str = "success",
                            smoke: str = "success") -> dict:
    """crawl.yml 의 실제 `if:` 위에서 GitHub 의 스텝 게이팅을 재생한다.

    돌지 못한 스텝의 outcome 은 `skipped` 다.  분류기에 넘길 outcome 맵을
    **손으로 적지 않고** 워크플로에서 유도하는 것이 요점이다 — 손으로 적으면
    실제로는 나올 수 없는 조합을 검증하게 된다.
    """
    injected = {"web-build": build, "web-deploy": deploy, "web-smoke": smoke}
    context = {"job_successful": True,
               "steps.web-gate.outputs.should_deploy": should_deploy}
    outcomes = {}
    for step_id in ("web-build", "web-deploy", "web-smoke"):
        condition = step_condition(workflow, step_id)
        ran = evaluate_condition(condition, context)
        outcome = injected[step_id] if ran else "skipped"
        context[f"steps.{step_id}.outcome"] = outcome
        outcomes[step_id.replace("-", "_")] = outcome
    return outcomes


class CrawlWebPathSemanticsTests(unittest.TestCase):
    """분류기가 아니라 **워크플로가 실제로 만드는 조합**을 본다.

    분류기 단위 테스트는 outcome 맵을 손으로 준다.  그래서 워크플로가 그런
    맵을 만들 수 없어도 초록이다 — 실제로 `web-smoke` 에 조건이 없던 동안
    `배포 창 미달 → web_status=skipped` 테스트가 그렇게 통과하고 있었다.
    """

    def setUp(self):
        self.crawl = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(
            encoding="utf-8")

    def web_status(self, **kwargs) -> str:
        build_mode = kwargs.pop("build_mode", "")
        outcomes = simulate_crawl_web_path(self.crawl, **kwargs)
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, **outcomes},
                              build_mode=build_mode,
                              identity_quarantined=2 if build_mode else 0)
        return verdict["web_status"]

    def test_deploy_window_miss_leaves_the_whole_web_path_skipped(self):
        """회귀 — 아무것도 짓지도 올리지도 않은 회차가 success 로 보고됐다."""
        outcomes = simulate_crawl_web_path(self.crawl, should_deploy="false")
        self.assertEqual({"web_build": "skipped", "web_deploy": "skipped",
                          "web_smoke": "skipped"}, outcomes)
        self.assertEqual("skipped", self.web_status(should_deploy="false"))

    def test_smoke_never_runs_without_a_deploy_in_this_round(self):
        """스모크는 '지금 올린 것이 사는가'를 보는 검사다."""
        for label, kwargs in (
            ("배포 창 미달", {"should_deploy": "false"}),
            ("빌드 실패", {"should_deploy": "true", "build": "failure"}),
            ("배포 실패", {"should_deploy": "true", "deploy": "failure"}),
        ):
            with self.subTest(label):
                outcomes = simulate_crawl_web_path(self.crawl, **kwargs)
                self.assertEqual("skipped", outcomes["web_smoke"])

    def test_a_healthy_deploy_round_is_success(self):
        self.assertEqual("success", self.web_status(should_deploy="true"))

    def test_a_build_failure_is_a_web_failure(self):
        self.assertEqual("failure",
                         self.web_status(should_deploy="true", build="failure"))

    def test_a_deploy_failure_is_a_web_failure(self):
        self.assertEqual("failure",
                         self.web_status(should_deploy="true", deploy="failure"))

    def test_a_smoke_failure_is_a_web_failure(self):
        """배포는 됐는데 화면이 죽은 회차 — 이 스텝의 존재 이유다."""
        self.assertEqual("failure",
                         self.web_status(should_deploy="true", smoke="failure"))

    def test_identity_quarantine_is_degraded_not_success(self):
        self.assertEqual("degraded",
                         self.web_status(should_deploy="true", build_mode="degraded"))

    def test_the_four_states_are_reachable_and_distinct(self):
        """요구된 네 상태가 실제 워크플로 조합에서 전부 서로 다르게 나온다."""
        observed = {
            "skipped": self.web_status(should_deploy="false"),
            "success": self.web_status(should_deploy="true"),
            "degraded": self.web_status(should_deploy="true", build_mode="degraded"),
            "failure": self.web_status(should_deploy="true", build="failure"),
        }
        self.assertEqual({"skipped": "skipped", "success": "success",
                          "degraded": "degraded", "failure": "failure"}, observed)


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

    # ── 실행 예산 ──────────────────────────────────────────────────────────
    #
    # 도메인 분리가 옳아도 **잡이 배포 도중 잘리면** 그 회차의 웹은 실패다.
    # 실측 2026-09-04 (run 33833880969 · 33845140906 · 33859264667): 수집
    # success → 상태 커밋 success → 빌드 success → `timeout-minutes: 30` 이
    # `Deploy web to Cloudflare Pages` 한복판에 떨어져 세 회차 연속 cancelled.
    # 06:37 회차는 wrangler 가 `Deployment complete` 를 찍고 7 초 뒤에 잘렸다 —
    # 배포는 실제로 됐는데 운영 알림은 '배포 실패'로 나갔다.
    #
    # 예산은 코드가 아니라 워크플로에 적힌 숫자이므로 여기서 본다.

    def _minutes(self, block: str) -> int | None:
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("timeout-minutes:"):
                return int(stripped.split(":", 1)[1].strip())
        return None

    def crawl_job(self) -> str:
        """`  crawl:` 잡 블록. 파일의 마지막 잡이라 끝까지 간다."""
        marker = "\n  crawl:\n"
        crawl = self.crawl()
        self.assertIn(marker, crawl)
        return crawl.split(marker, 1)[1]

    def test_crawl_job_budget_covers_the_slowest_measured_round(self):
        """완주한 회차보다 짧은 제한은 '느린 성공'을 장애로 바꾼다.

        실측(2026-09-01~09-04, 배포 창에 든 17회차) 단계별 최댓값:
        수집 9.7분 + 빌드 20.7분 + 배포 3.2분 + setup·상태·스모크·알림 1.4분
        ≒ 35분.  완주한 회차의 실제 최댓값은 29.5분이었다.  제한은 그보다
        커야 한다 — 그러지 못한 값이 30 이었다.
        """
        budget = self._minutes(self.crawl_job().split("    steps:", 1)[0])
        self.assertIsNotNone(budget, "crawl 잡에 timeout-minutes 가 없다")
        self.assertGreaterEqual(
            budget, 40,
            "실측 최악 예산 약 35분 + 여유보다 짧다 — 배포가 잡 제한에 잘린다")

    def test_crawl_job_budget_still_releases_the_shared_lock_in_time(self):
        """제한의 원래 목적은 그대로다 — 막힌 실행이 `nuclens-state` 를 쥐고
        다음 크롤과 daily-brief 를 막지 못하게 하는 것.  크롤 주기는 3시간이고
        배포 간격 게이트는 165분이다.  둘 중 짧은 쪽보다 넉넉히 짧아야 한다.
        """
        budget = self._minutes(self.crawl_job().split("    steps:", 1)[0])
        self.assertLessEqual(
            budget, 90,
            "락을 너무 오래 쥔다 — 3시간 주기·165분 배포 게이트를 잠식한다")

    def test_crawl_deploy_step_is_bounded_before_the_job_is(self):
        """잡 제한이 배포에서 떨어지면 **뒤가 통째로 잘린다** — 스모크도,
        운영 알림도, issue review 캐시 커밋도 못 돈다.  스텝 제한이 먼저
        떨어지면 `continue-on-error` 가 받아 web_deploy=failure 로 남고
        나머지는 정상 실행된다.  그 둘은 같은 '배포 실패'가 아니다.
        """
        deploy = self.step(self.crawl(), "web-deploy")
        step_budget = self._minutes(deploy)
        self.assertIsNotNone(step_budget, "배포 스텝에 자체 timeout-minutes 가 없다")
        job_budget = self._minutes(self.crawl_job().split("    steps:", 1)[0])
        self.assertLess(step_budget, job_budget,
                        "스텝 제한이 잡 제한보다 늦게 떨어지면 아무 의미가 없다")
        # 실측 최장 완주는 약 3.2분(npx 설치 164초 + 업로드 20초 + sleep 15).
        # 그보다 짧으면 느린 성공을 죽인다.
        self.assertGreaterEqual(step_budget, 6,
                                "실측 최장 배포(약 3.2분)에 여유가 없다")

    def test_a_timed_out_deploy_is_a_web_failure_only(self):
        """스텝 제한에 걸린 배포는 웹만 실패다 — 수집은 이미 끝나 push 됐다."""
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, "web_build": "success",
                                        "web_deploy": "failure",
                                        "web_smoke": "skipped"})
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("failure", verdict["web_status"])
        self.assertFalse(verdict["job_should_fail"])

    def test_a_cancelled_deploy_is_a_web_failure_only(self):
        """잡 제한에 잘린 회차도 같은 판정이어야 한다 — `cancelled` 는
        `skipped` 가 아니다.  실측 run 33833880969 의 실제 outcome 조합이다.
        """
        verdict = fd.classify("crawl", {**CRAWL_HEALTHY, "web_build": "success",
                                        "web_deploy": "cancelled",
                                        "web_smoke": "skipped"},
                              build_mode="ok")
        self.assertEqual("success", verdict["core_status"])
        self.assertEqual("failure", verdict["web_status"])
        self.assertEqual(["web_deploy"], verdict["web_failed_stages"])
        self.assertFalse(verdict["job_should_fail"])

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
