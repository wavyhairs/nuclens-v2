"""Source health persistence and non-blocking admin alert planning."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import operational_monitoring as monitor


T0 = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)


class SourceHealthTests(unittest.TestCase):
    def test_snapshot_distinguishes_failure_empty_and_missing(self):
        observations = monitor.source_observations(
            {
                "counts": {"IAEA": 12, "NSSC": 0, "WNN": 0},
                "kept": {"IAEA": 2},
                "errors": {"WNN": "HTTP 503"},
            },
            {"IAEA": "feed", "NSSC": "official", "WNN": "feed", "KHNP": "official"},
        )
        rows = {row["name"]: row for row in observations}
        self.assertEqual("ok", rows["IAEA"]["status"])
        self.assertEqual("empty", rows["NSSC"]["status"])
        self.assertEqual("failed", rows["WNN"]["status"])
        self.assertEqual("failed", rows["KHNP"]["status"])
        self.assertIn("not observed", rows["KHNP"]["error"])

    def test_health_persists_streaks_and_recovers(self):
        health = monitor.update_source_health(None, [{
            "name": "KHNP", "kind": "official", "status": "ok", "count": 10, "kept": 1,
        }], T0)
        row = health["sources"]["KHNP"]
        first_success = row["last_success_at"]
        self.assertEqual(0, row["consecutive_failures"])
        self.assertEqual(10, row["last_count"])

        for hours in (1, 2):
            health = monitor.update_source_health(health, [{
                "name": "KHNP", "kind": "official", "status": "failed",
                "count": 0, "kept": 0, "error": "timeout",
            }], T0 + timedelta(hours=hours))
        row = health["sources"]["KHNP"]
        self.assertEqual(2, row["consecutive_failures"])
        self.assertEqual(first_success, row["last_success_at"])
        self.assertEqual("timeout", row["last_error"])

        health = monitor.update_source_health(health, [{
            "name": "KHNP", "kind": "official", "status": "empty", "count": 0, "kept": 0,
        }], T0 + timedelta(hours=3))
        row = health["sources"]["KHNP"]
        self.assertEqual(0, row["consecutive_failures"])
        self.assertEqual(1, row["consecutive_empty"])
        self.assertEqual("", row["last_error"])

    def test_unobserved_sources_are_preserved(self):
        health = monitor.update_source_health(None, [
            {"name": "A", "status": "ok", "count": 1},
            {"name": "B", "status": "ok", "count": 2},
        ], T0)
        health = monitor.update_source_health(health, [
            {"name": "A", "status": "failed", "error": "x"},
        ], T0 + timedelta(hours=1))
        self.assertEqual(1, health["sources"]["B"]["checks"])
        self.assertEqual("ok", health["sources"]["B"]["last_status"])

    def test_alerts_use_different_failure_and_empty_thresholds(self):
        health = {"sources": {
            "NSSC": {"kind": "official", "consecutive_failures": 2,
                     "last_checked_at": "run-2", "last_error": "HTTP 500"},
            "IAEA": {"kind": "feed", "consecutive_empty": 3,
                     "last_checked_at": "run-3"},
            "WNN": {"kind": "feed", "consecutive_empty": 2,
                    "last_checked_at": "run-2"},
        }}
        signals = monitor.source_health_signals(health)
        self.assertEqual({"source:NSSC:failure", "source:IAEA:empty"},
                         {signal.key for signal in signals})


class PartialSourceFailureTests(unittest.TestCase):
    """실패도 0건도 아닌 조용한 부분 장애 — counts/kept 로는 보이지 않는다.

    임계값은 실측에서 왔다(2026-08-02~17, 감시 대상 18개 출처). 발행 간격은
    p50 3일 · p95 5일 · 최대 5일이라 stale 14일은 관측 상한의 약 3배이고 현재
    0/18 이 걸린다. 그리고 counts>0·kept=0 은 그냥 조용한 날이라 신호가 아니다.
    """

    HEALTHY = {"entries": 10, "usable": 10,
               "newest_pub": (T0 - timedelta(days=1)).isoformat()}

    def health_after(self, diagnostics, runs=2, now=T0):
        health = None
        for index in range(runs):
            health = monitor.update_source_health(
                health,
                monitor.source_observations(
                    {"counts": {"WNN": 10}, "diagnostics": {"WNN": diagnostics}},
                    {"WNN": "feed"}),
                now + timedelta(hours=index))
        return health

    def keys(self, diagnostics, **kwargs):
        health = self.health_after(diagnostics, **kwargs)
        return {s.key for s in monitor.source_health_signals(health, now=T0)}

    def test_healthy_feed_is_silent(self):
        self.assertEqual(self.keys(self.HEALTHY), set())

    def test_snapshot_without_diagnostics_stays_silent(self):
        """옛 스냅샷에는 이 계기가 없다 — 없다고 장애로 읽으면 안 된다."""
        health = monitor.update_source_health(
            None, monitor.source_observations({"counts": {"WNN": 10}}, {"WNN": "feed"}),
            T0)
        self.assertEqual(monitor.source_health_signals(health, now=T0), [])
        self.assertNotIn("last_newest_pub", health["sources"]["WNN"])

    def test_bozo_with_partial_entries_is_reported(self):
        """0건+bozo 는 이미 실패로 잡힌다. 일부만 건진 경우가 조용히 새던 쪽이다."""
        self.assertEqual(
            self.keys({**self.HEALTHY, "usable": 3, "bozo": True,
                       "bozo_exception": "mismatched tag"}),
            {"source:WNN:partial-parse"})

    def test_a_single_bozo_run_is_not_an_alert(self):
        self.assertEqual(
            self.keys({**self.HEALTHY, "usable": 3, "bozo": True}, runs=1), set())

    def test_full_page_with_nothing_usable_is_a_format_change(self):
        self.assertEqual(self.keys({**self.HEALTHY, "entries": 12, "usable": 0}),
                         {"source:WNN:unusable"})

    def test_small_result_with_nothing_usable_is_not_an_alert(self):
        """항목이 적으면 정상적으로도 전건이 걸러진다."""
        self.assertEqual(self.keys({**self.HEALTHY, "entries": 3, "usable": 0}), set())

    def test_stale_feed_is_reported(self):
        self.assertEqual(
            self.keys({**self.HEALTHY,
                       "newest_pub": (T0 - timedelta(days=40)).isoformat()}),
            {"source:WNN:stale"})

    def test_normal_quiet_period_is_not_stale(self):
        for days in (5, 13):
            with self.subTest(days=days):
                self.assertEqual(
                    self.keys({**self.HEALTHY,
                               "newest_pub": (T0 - timedelta(days=days)).isoformat()}),
                    set())

    def test_counts_without_kept_is_not_a_failure(self):
        """게시판이 10건을 주고 전건이 신선도 컷에 떨어지는 것은 정상이다."""
        health = None
        for index in range(3):
            health = monitor.update_source_health(
                health,
                monitor.source_observations(
                    {"counts": {"WNN": 10}, "kept": {"WNN": 0},
                     "diagnostics": {"WNN": self.HEALTHY}}, {"WNN": "feed"}),
                T0 + timedelta(hours=index))
        self.assertEqual(monitor.source_health_signals(health, now=T0), [])

    def test_newest_pub_never_moves_backwards(self):
        """한 실행이 일부만 읽어 와도 그 피드가 갑자기 오래된 것으로 보이면 안 된다."""
        health = self.health_after(self.HEALTHY, runs=1)
        health = monitor.update_source_health(
            health,
            monitor.source_observations(
                {"counts": {"WNN": 1}, "diagnostics": {"WNN": {
                    "entries": 1, "usable": 1,
                    "newest_pub": (T0 - timedelta(days=200)).isoformat()}}},
                {"WNN": "feed"}),
            T0 + timedelta(hours=1))
        self.assertEqual(health["sources"]["WNN"]["last_newest_pub"],
                         self.HEALTHY["newest_pub"])
        self.assertEqual(monitor.source_health_signals(health, now=T0), [])

    def test_hard_failure_does_not_also_raise_partial_alerts(self):
        """이미 실패로 알린 출처에 경보를 하나 더 붙이지 않는다."""
        health = None
        for index in range(2):
            health = monitor.update_source_health(
                health,
                monitor.source_observations(
                    {"counts": {"WNN": 0}, "errors": {"WNN": "HTTP 503"},
                     "diagnostics": {"WNN": {"entries": 12, "usable": 0}}},
                    {"WNN": "feed"}),
                T0 + timedelta(hours=index))
        self.assertEqual({s.key for s in monitor.source_health_signals(health, now=T0)},
                         {"source:WNN:failure"})

    def test_recovery_clears_the_partial_streaks(self):
        health = self.health_after({**self.HEALTHY, "usable": 0, "entries": 12,
                                    "bozo": True})
        self.assertTrue(monitor.source_health_signals(health, now=T0))
        health = monitor.update_source_health(
            health,
            monitor.source_observations(
                {"counts": {"WNN": 10}, "diagnostics": {"WNN": self.HEALTHY}},
                {"WNN": "feed"}),
            T0 + timedelta(hours=5))
        self.assertEqual(monitor.source_health_signals(health, now=T0), [])


class QualitySignalTests(unittest.TestCase):
    def test_data_gate_builds_actionable_signals(self):
        record = {
            "record_type": "data_quality_gate",
            "generated_at": "2026-08-17T09:00:00+09:00",
            "tracking": {"applicable": True, "below_target": True, "rate": 0.1,
                         "target": 0.2, "window_briefings": 7},
            "topic_weeks": {"flow_ratio": 2.5, "flow_visible": False,
                            "slope_ratio": 1.2, "slope_visible": True,
                            "totals": [40, 100], "limit": 2.0},
        }
        signals = monitor.data_gate_signals(record)
        self.assertEqual({"quality:tracking-rate", "quality:topic-weeks"},
                         {signal.key for signal in signals})
        self.assertTrue(all(signal.min_occurrences == 2 for signal in signals))

    def test_archive_integrity_quarantine_alerts_immediately(self):
        signals = monitor.data_gate_signals({
            "generated_at": "2026-08-17T08:00:00+09:00",
            "archive_quality": {
                "quarantined": 2,
                "quarantine_samples": [{"hash": "bad-title-1"}, {"hash": "bad-title-2"}],
            },
            "tracking": {"applicable": False},
            "topic_weeks": {},
        })

        self.assertEqual(1, len(signals))
        self.assertEqual("quality:archive-integrity", signals[0].key)
        self.assertEqual("critical", signals[0].severity)
        self.assertEqual(1, signals[0].min_occurrences)

    def test_archive_integrity_sanitize_is_an_immediate_stable_signal(self):
        signals = monitor.data_gate_signals({
            "generated_at": "2026-08-17T08:00:00+09:00",
            "observation_id": "github-run:123",
            "archive_quality": {
                "quarantined": 0,
                "sanitized": 1,
                "sanitize_samples": [{"hash": "bad-date-1",
                                      "codes": ["invalid_event_date"]}],
            },
            "tracking": {"applicable": False},
            "topic_weeks": {},
        })

        self.assertEqual(1, len(signals))
        self.assertEqual("quality:archive-integrity", signals[0].key)
        self.assertEqual("warning", signals[0].severity)
        self.assertEqual("github-run:123", signals[0].observation_id)
        self.assertEqual(1, signals[0].min_occurrences)
        self.assertIn("1건", signals[0].detail)
        # 자동 정정은 사고가 아니다 — 운영자 등급은 '확인 필요'여야 한다.
        self.assertEqual(monitor.LEVEL_ATTENTION, signals[0].level)
        self.assertIn("없음", signals[0].impact)
        self.assertIn("sanitized=1", signals[0].technical)

    def test_archive_quarantine_and_sanitize_share_one_escalating_signal(self):
        signals = monitor.data_gate_signals({
            "observation_id": "github-run:124",
            "archive_quality": {"quarantined": 2, "sanitized": 3},
            "tracking": {"applicable": False}, "topic_weeks": {},
        })
        self.assertEqual(1, len(signals))
        self.assertEqual("critical", signals[0].severity)
        self.assertEqual(monitor.LEVEL_ATTENTION, signals[0].level)
        self.assertIn("2건", signals[0].detail)
        self.assertIn("3건", signals[0].detail)
        # 해시·집계는 기술 상세로 내려간다.
        self.assertIn("quarantined=2", signals[0].technical)
        self.assertIn("sanitized=3", signals[0].technical)

    def test_issue_candidate_guards_reach_the_administrator(self):
        """후보 감시는 **판정을 여기서 다시 하지 않는다.** issue_candidate_stats 가
        이미 내린 결론을 전달만 한다 — 임계값이 두 곳에 있으면 반드시 어긋난다.
        """
        signals = monitor.data_gate_signals({
            "observation_id": "github-run:200",
            "tracking": {"applicable": False}, "topic_weeks": {},
            "issue_candidates": {
                "applicable": True,
                "guards": [
                    {"id": "issue-candidate:preselect-headroom", "severity": "critical",
                     "title": "어휘 예선 컷 20 이 실제 병합을 놓친다 (evidence)",
                     "detail": "컷 밖으로 밀린 병합 16건 / 400건"},
                    {"id": "issue-candidate:merge-rate-drift", "severity": "warning",
                     "title": "카드 병합률이 최근 회차와 크게 다르다", "detail": "-47%"},
                ],
            },
        })
        self.assertEqual(["issue-candidate:preselect-headroom",
                          "issue-candidate:merge-rate-drift"],
                         [signal.key for signal in signals])
        self.assertEqual(["critical", "warning"], [signal.severity for signal in signals])
        self.assertTrue(all(signal.scope == "data_gate" for signal in signals))
        # 이 기록은 **하루 한 번**만 생긴다(data_gate_metrics 는 daily-brief 전용).
        # 그래서 부르는 속도를 심각도로 가른다: 이미 병합을 놓치고 있는 critical 은
        # 즉시, 여유가 줄었다는 warning 은 이틀 연속일 때.
        self.assertEqual([1, 2], [signal.min_occurrences for signal in signals])

    def test_a_quiet_candidate_run_sends_nothing(self):
        signals = monitor.data_gate_signals({
            "observation_id": "github-run:201",
            "tracking": {"applicable": False}, "topic_weeks": {},
            "issue_candidates": {"applicable": True, "guards": []},
        })
        self.assertEqual([], signals)

    def test_a_desynced_telemetry_pages_on_the_first_run(self):
        """계측이 루프와 어긋나면 그 회차 수치 전체를 못 믿는다 — 하루 기다릴 일이 아니다."""
        signals = monitor.data_gate_signals({
            "observation_id": "github-run:202",
            "tracking": {"applicable": False}, "topic_weeks": {},
            "issue_candidates": {"applicable": True, "guards": [
                {"id": "issue-candidate:telemetry-desync", "severity": "critical",
                 "title": "후보 계측이 루프와 어긋났다", "detail": "4건"},
            ]},
        })
        self.assertEqual(1, signals[0].min_occurrences)

    def test_a_warning_waits_one_more_day_before_paging(self):
        """여유가 줄었다는 신호는 하루 흔들림일 수 있다. 이틀 연속일 때 부른다."""
        signals = monitor.data_gate_signals({
            "observation_id": "github-run:203",
            "tracking": {"applicable": False}, "topic_weeks": {},
            "issue_candidates": {"applicable": True, "guards": [
                {"id": "issue-candidate:preselect-headroom", "severity": "warning",
                 "title": "어휘 예선 컷 20 의 여유가 줄었다 (evidence)", "detail": "p99 15위"},
            ]},
        })
        self.assertEqual(2, signals[0].min_occurrences)

    def test_a_record_from_before_the_diagnostics_existed_is_ignored(self):
        """delivery_log 에 이미 쌓인 옛 기록에는 이 칸이 없다. 그것이 알림이 되면
        도입 첫날 과거 회차 전부가 한꺼번에 운다."""
        signals = monitor.data_gate_signals({
            "observation_id": "old", "tracking": {"applicable": False}, "topic_weeks": {},
        })
        self.assertEqual([], signals)

    def test_insufficient_topic_window_does_not_alert(self):
        signals = monitor.data_gate_signals({
            "generated_at": "x", "tracking": {"applicable": False},
            "topic_weeks": {"flow_ratio": None, "flow_visible": False,
                            "slope_ratio": None, "slope_visible": False},
        })
        self.assertEqual([], signals)

    def test_latest_record_ignores_unrelated_log_rows(self):
        rows = [
            {"record_type": "data_quality_gate", "generated_at": "2026-08-16"},
            {"record_type": "curation_failure", "generated_at": "2026-08-18"},
            {"record_type": "data_quality_gate", "generated_at": "2026-08-17"},
        ]
        self.assertEqual("2026-08-17", monitor.latest_data_gate_record(rows)["generated_at"])


class WebPipelineSignalTests(unittest.TestCase):
    def test_build_failure_is_reported_without_downstream_noise(self):
        signals = monitor.web_pipeline_signals({
            "web_build": "failure", "data_gate": "skipped", "web_deploy": "skipped",
        }, observation_id="daily-brief:1")
        self.assertEqual(1, len(signals))
        self.assertEqual("quality:web-pipeline-failure", signals[0].key)
        self.assertEqual("daily-brief:1", signals[0].observation_id)
        # step 이름과 outcome 은 기술 상세다. 운영자 문장은 서비스 영향을 말한다.
        self.assertIn("웹 데이터 빌드=failure", signals[0].technical)
        self.assertNotIn("Cloudflare", signals[0].technical)
        self.assertEqual(monitor.LEVEL_ACTION, signals[0].level)
        self.assertIn("사이트", signals[0].impact)

    def test_metrics_and_deploy_failures_can_be_reported_together(self):
        signals = monitor.web_pipeline_signals({
            "web_build": "success", "data_gate": "failure", "web_deploy": "failure",
        }, observation_id="daily-brief:2")
        self.assertEqual(1, len(signals))
        self.assertIn("데이터 품질 기록=failure", signals[0].technical)
        self.assertIn("Cloudflare 배포·스모크=failure", signals[0].technical)
        # 배포가 죽은 회차다 — 지표만 빈 것과 같은 등급으로 부르면 안 된다.
        self.assertEqual(monitor.LEVEL_ACTION, signals[0].level)

    def test_all_success_has_no_failure_signal(self):
        self.assertEqual([], monitor.web_pipeline_signals({
            "web_build": "success", "data_gate": "success", "web_deploy": "success",
        }, observation_id="daily-brief:3"))

    def test_a_workflow_without_a_metrics_step_is_not_reported_as_failing(self):
        """crawl 에는 data_gate 스텝이 없다 — 물어본 적 없는 단계는 실패가 아니다.

        실측 2026-09-04 run 33833880969: 배포가 잡 제한에 잘린 진짜 원인 옆에
        `데이터 품질 기록=missing` 이 나란히 붙어 나갔다. crawl.yml 은
        `--data-gate-outcome` 을 넘기지 않는데(그 스텝이 아예 없다) 안 넘어온
        값이 `missing` 으로 채워져 실패로 세어졌다.
        """
        signals = monitor.web_pipeline_signals({
            "web_build": "success", "web_deploy": "cancelled",
        }, observation_id="crawl:33833880969")
        self.assertEqual(1, len(signals))
        self.assertIn("Cloudflare 배포·스모크=cancelled", signals[0].technical)
        self.assertNotIn("데이터 품질 기록", signals[0].technical)
        # 없는 기록을 찾아보라는 안내도 함께 사라져야 한다.
        self.assertNotIn("data_quality_gate", signals[0].technical)

    def test_a_healthy_crawl_round_without_a_metrics_step_says_nothing(self):
        """오탐을 없앤다고 정상 회차에 신호가 남으면 고친 게 아니다."""
        self.assertEqual([], monitor.web_pipeline_signals({
            "web_build": "success", "web_deploy": "success",
        }, observation_id="crawl:1"))

    def test_a_skipped_metrics_step_is_not_a_failure_either(self):
        """daily-brief 의 data-gate 는 claim 실패 회차에 건너뛴다. 그 회차는
        브리핑 도메인이 이미 따로 울고 있다 — 여기서 한 번 더 울면 소음이다.
        """
        self.assertEqual([], monitor.web_pipeline_signals({
            "web_build": "success", "data_gate": "skipped", "web_deploy": "success",
        }, observation_id="daily-brief:4"))

    def test_a_real_metrics_failure_still_reports_with_its_hint(self):
        """선택 단계라고 **실패까지** 눈감으면 daily-brief 의 지표 감시가 죽는다."""
        signals = monitor.web_pipeline_signals({
            "web_build": "success", "data_gate": "failure", "web_deploy": "success",
        }, observation_id="daily-brief:5")
        self.assertEqual(1, len(signals))
        self.assertIn("데이터 품질 기록=failure", signals[0].technical)
        self.assertIn("data_quality_gate", signals[0].technical)
        # 지표만 빈 날은 '조치 필요'가 아니다 — 기존 등급 그대로.
        self.assertEqual(monitor.LEVEL_ATTENTION, signals[0].level)

    def test_a_missing_deploy_outcome_still_pages(self):
        """필수 단계는 여전히 fail-loud 다. 값이 안 넘어오는 것 자체가 배선
        사고이고, 조용해지는 쪽이 훨씬 나쁘다 — 이 알림이 생긴 이유가 웹이
        깨졌는데 알림이 0건이던 2026-09-01 회차다.
        """
        for label, outcomes in (
            ("deploy 누락", {"web_build": "success"}),
            ("build 누락", {"web_deploy": "success"}),
        ):
            with self.subTest(label):
                signals = monitor.web_pipeline_signals(outcomes,
                                                       observation_id="crawl:2")
                self.assertEqual(1, len(signals))
                self.assertIn("missing", signals[0].technical)

    def test_collection_crash_is_visible_without_a_source_snapshot(self):
        signals = monitor.collection_pipeline_signals(
            "failure", observation_id="crawl:42")
        self.assertEqual(1, len(signals))
        self.assertEqual("source:collection-pipeline-failure", signals[0].key)
        self.assertEqual("critical", signals[0].severity)
        self.assertEqual("crawl:42", signals[0].observation_id)

    def test_successful_collection_has_no_failure_signal(self):
        self.assertEqual([], monitor.collection_pipeline_signals(
            "success", observation_id="crawl:43"))


class WebIdentityDegradedTests(unittest.TestCase):
    """degraded 는 step outcome 이 success 라 web_pipeline 신호로는 절대 안 나온다.

    그 회차를 ok 로 부르면 격리가 일어났다는 사실이 어디에도 남지 않고,
    failure 로 부르면 systemic corruption 의 fail-closed 와 구별되지 않는다.
    """

    def test_a_degraded_build_is_reported_even_though_the_step_succeeded(self):
        signals = monitor.web_identity_signals(
            "degraded", quarantined_count=2, observation_id="crawl:99")
        self.assertEqual(1, len(signals))
        self.assertEqual("quality:web-identity-degraded", signals[0].key)
        self.assertEqual("web_identity", signals[0].scope)
        self.assertEqual("crawl:99", signals[0].observation_id)
        self.assertIn("2", signals[0].detail)
        self.assertIn("build_mode=degraded", signals[0].technical)

    def test_degraded_is_not_an_outage(self):
        """사이트도 브리핑도 정상이다 — ACTION 으로 부르면 진짜 장애가 안 읽힌다."""
        signal = monitor.web_identity_signals("degraded", quarantined_count=1)[0]
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertEqual("warning", signal.severity)

    def test_an_ongoing_degraded_state_is_not_new_news_every_three_hours(self):
        """크롤은 3시간마다 돈다. 같은 오염이 남아 있는 동안 다시 울리면 안 된다."""
        first = monitor.web_identity_signals("degraded", quarantined_count=2)[0]
        again = monitor.web_identity_signals("degraded", quarantined_count=2)[0]
        worse = monitor.web_identity_signals("degraded", quarantined_count=5)[0]
        self.assertEqual(first.fingerprint, again.fingerprint)
        self.assertNotEqual(first.fingerprint, worse.fingerprint)

    def test_an_ok_build_is_silent(self):
        self.assertEqual([], monitor.web_identity_signals("ok", quarantined_count=0))

    def test_an_unmeasured_build_is_silent(self):
        """빌드가 죽어 build_mode 가 없는 회차는 web_pipeline 신호의 몫이다."""
        self.assertEqual([], monitor.web_identity_signals(None))
        self.assertEqual([], monitor.web_identity_signals(""))


class AlertLifecycleTests(unittest.TestCase):
    def signal(self, observation: str, *, severity: str = "warning") -> monitor.AlertSignal:
        return monitor.AlertSignal(
            key="quality:test", scope="quality", title="테스트 경고", detail="상세",
            severity=severity, observation_id=observation, min_occurrences=2,
        )

    def test_requires_distinct_consecutive_observations(self):
        state, due = monitor.evaluate_alerts([self.signal("run-1")], None,
                                             evaluated_scopes={"quality"}, now=T0)
        self.assertEqual([], due)
        # Workflow retry: the same metrics record must not count twice.
        state, due = monitor.evaluate_alerts([self.signal("run-1")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(minutes=5))
        self.assertEqual([], due)
        self.assertEqual(1, state["items"]["quality:test"]["consecutive"])

        state, due = monitor.evaluate_alerts([self.signal("run-2")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(days=1))
        self.assertEqual(["quality:test"], [signal.key for signal in due])

    def test_notification_cooldown_recovery_and_recurrence(self):
        state, _ = monitor.evaluate_alerts([self.signal("r1")], None,
                                           evaluated_scopes={"quality"}, now=T0)
        state, due = monitor.evaluate_alerts([self.signal("r2")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=1))
        state = monitor.mark_notified(state, due, T0 + timedelta(hours=1))

        state, due = monitor.evaluate_alerts([self.signal("r3")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=2))
        self.assertEqual([], due)
        state, due = monitor.evaluate_alerts([self.signal("r4")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=26))
        self.assertEqual(1, len(due))
        state = monitor.mark_notified(state, due, T0 + timedelta(hours=26))

        # 사라진 문제는 **해결 통지 한 건**으로 닫는다. 닫혔다는 말을 듣지 못하면
        # 어제 받은 경고가 아직 살아 있는지 알 수 없다.
        state, due = monitor.evaluate_alerts([], state, evaluated_scopes={"quality"},
                                             now=T0 + timedelta(hours=27))
        self.assertEqual([monitor.LEVEL_RESOLVED], [row.level for row in due])
        self.assertFalse(state["items"]["quality:test"]["active"])
        state = monitor.mark_notified(state, due, T0 + timedelta(hours=27))
        _, again = monitor.evaluate_alerts([], state, evaluated_scopes={"quality"},
                                           now=T0 + timedelta(hours=27, minutes=5))
        self.assertEqual([], again)
        state, due = monitor.evaluate_alerts([self.signal("r5")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=28))
        self.assertEqual([], due)
        self.assertEqual(1, state["items"]["quality:test"]["consecutive"])

    def test_severity_escalation_bypasses_cooldown(self):
        first = monitor.AlertSignal("source:x", "source", "x", "d", "warning", "r1", 1)
        state, due = monitor.evaluate_alerts([first], None, evaluated_scopes={"source"}, now=T0)
        state = monitor.mark_notified(state, due, T0)
        critical = monitor.AlertSignal("source:x", "source", "x", "worse", "critical", "r2", 1)
        state, due = monitor.evaluate_alerts([critical], state, evaluated_scopes={"source"},
                                             now=T0 + timedelta(hours=1))
        self.assertEqual(1, len(due))

    def test_source_evaluation_does_not_resolve_quality_scope(self):
        state = {"items": {"quality:x": {"scope": "quality", "active": True,
                                          "consecutive": 3}}}
        state, _ = monitor.evaluate_alerts([], state, evaluated_scopes={"source"}, now=T0)
        self.assertTrue(state["items"]["quality:x"]["active"])

    def test_sender_failure_is_non_blocking_and_retryable(self):
        signal = monitor.AlertSignal("x", "quality", "title", "detail", min_occurrences=1)
        state, due = monitor.evaluate_alerts([signal], None,
                                             evaluated_scopes={"quality"}, now=T0)

        def fail(_message):
            raise OSError("network down")

        unchanged, result = monitor.notify_alerts(state, due, fail, now=T0)
        self.assertFalse(result["sent"])
        self.assertNotIn("last_notified_at", unchanged["items"]["x"])

        messages = []
        sent, result = monitor.notify_alerts(unchanged, due,
                                             lambda message: messages.append(message) or {"ok": True},
                                             now=T0)
        self.assertTrue(result["sent"])
        self.assertIn("last_notified_at", sent["items"]["x"])
        self.assertIn("Nuclens+ 운영 알림", messages[0])

    def test_unsent_alert_survives_recovery_and_next_day(self):
        signal = monitor.AlertSignal(
            "source:x", "source", "수집 장애", "연속 실패", observation_id="run-2",
            min_occurrences=1,
        )
        state, due = monitor.evaluate_alerts(
            [signal], None, evaluated_scopes={"source"}, now=T0)
        self.assertEqual(1, len(due))
        self.assertTrue(state["items"]["source:x"]["pending_notification"])

        # The source recovers before an admin sender is available. The incident
        # is resolved operationally but its unsent notification remains due.
        state, due = monitor.evaluate_alerts(
            [], state, evaluated_scopes={"source"}, now=T0 + timedelta(days=1))
        self.assertFalse(state["items"]["source:x"]["active"])
        self.assertEqual(["source:x"], [row.key for row in due])

        state = monitor.mark_notified(state, due, T0 + timedelta(days=1))
        state, due = monitor.evaluate_alerts(
            [], state, evaluated_scopes=set(), now=T0 + timedelta(days=2))
        self.assertEqual([], due)
        self.assertFalse(state["items"]["source:x"]["pending_notification"])


if __name__ == "__main__":
    unittest.main()
