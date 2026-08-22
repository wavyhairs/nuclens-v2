"""운영자가 5초 안에 세 가지를 답할 수 있는가를 고정하는 계약 테스트.

    ① 무슨 일이 있었나  ② 서비스에 영향이 있나  ③ 내가 할 일이 있나

여기서 지키는 것은 문구가 아니라 **의미**다. 문자열 전체 일치로 못 박으면 표현을
못 고치게 되고, 그러면 알림은 영영 안 읽히는 상태로 굳는다. 그래서 등급·영향·조치
필드와 '기술 값이 운영자 문장에 새지 않는가'만 검사한다.

실제 관측값을 그대로 쓴다(2026-08-20~22 delivery_log.jsonl · sent.json):
같은 격리 21건이 회차마다 새 critical 로 5회 통지됐고, 주별 비율 2.5536 과 표본
배열이 운영자 메시지 본문에 그대로 실려 있었다.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import operational_alerts as cli  # noqa: E402
import operational_monitoring as monitor  # noqa: E402


T0 = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)

# 운영자 문장에 있으면 안 되는 것들. 코드명·예외·해시·임계값은 사람이 읽는
# 문장이 아니라 디버깅 재료다 — 기술 상세 줄이나 실행 로그에만 있어야 한다.
DEVELOPER_TOKENS = (
    "=", "::", "exception", "Exception", "traceback", "hash", "sha256",
    "threshold", "outcome", "None", "null", "quarantine", "sanitize", "bozo",
    "consecutive_", "min_occurrences", "observation_id", "pipeline_status",
)


def gate_record(*, quarantined=21, sanitized=142, observation="github-run:1",
                quarantine_samples=None, flow_ratio=2.5536, flow_visible=False,
                totals=None, generated_at="2026-08-22T04:40:48+09:00"):
    """실측 data_quality_gate 레코드 모양 그대로."""
    samples = quarantine_samples if quarantine_samples is not None else [
        {"hash": f"a8c1cdc2ca{index:02d}"} for index in range(min(quarantined, 20))]
    return {
        "record_type": "data_quality_gate", "date": "2026-08-22",
        "generated_at": generated_at, "observation_id": observation,
        "tracking": {"applicable": False},
        "archive_quality": {
            "checked": 5602, "quarantined": quarantined, "sanitized": sanitized,
            "quarantine_samples": samples,
            "sanitize_samples": [{"hash": "86fd8470109c"}],
        },
        "topic_weeks": {
            "weeks": ["W1", "W2", "W3", "W4", "W5"],
            "totals": totals if totals is not None else [49, 56, 111, 143, 118],
            "limit": 2.0, "flow_ratio": flow_ratio, "slope_ratio": 1.19,
            "flow_visible": flow_visible, "slope_visible": True,
        },
    }


class OperatorSentenceTests(unittest.TestCase):
    """무슨 일이 있었나 / 영향 / 조치 — 세 줄이 전부 있어야 한다."""

    def signals(self, record, previous=None):
        return monitor.data_gate_signals(record, previous)

    def test_technical_values_never_reach_the_operator_sentences(self):
        """사례 2: `2.5536`, `[49, 56, ...]`, 기준 2.0 은 본문에서 사라져야 한다."""
        catalog = (
            self.signals(gate_record()) +
            monitor.web_pipeline_signals(
                {"web_build": "failure", "data_gate": "skipped", "web_deploy": "skipped"},
                observation_id="daily-brief:1") +
            monitor.collection_pipeline_signals("failure", observation_id="crawl:1") +
            monitor.source_health_signals({"sources": {"산업부 보도자료": {
                "kind": "official", "consecutive_failures": 3,
                "last_error": "ConnectTimeout: HTTPSConnectionPool(host='www.motir.go.kr')",
                "last_checked_at": "2026-08-22T04:00:00+00:00"}}}, now=T0)
        )
        self.assertTrue(catalog)
        for signal in catalog:
            signal = signal.normalized()
            with self.subTest(key=signal.key):
                operator_text = " ".join(
                    (signal.title, signal.detail, signal.impact, signal.action))
                for token in DEVELOPER_TOKENS:
                    self.assertNotIn(token, operator_text)
                self.assertNotIn("2.5536", operator_text)
                self.assertNotIn("[49, 56", operator_text)
                self.assertNotIn("a8c1cdc2ca", operator_text)
                # 그러나 값이 사라지면 안 된다 — 기술 상세로 내려갔을 뿐이다.
                self.assertTrue(signal.impact, "서비스 영향을 말하지 않는 알림")
                self.assertTrue(signal.action, "할 일을 말하지 않는 알림")

    def test_a_protected_metric_is_not_an_outage(self):
        """사례 2: 통계 신뢰도가 낮아 지표만 숨긴 것은 장애가 아니다."""
        signal = next(row for row in self.signals(gate_record())
                      if row.key == "quality:topic-weeks").normalized()
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertTrue(signal.impact.startswith("없음"))
        self.assertIn("필요 없음", signal.action)
        self.assertIn("자동", signal.detail)
        # 값 자체는 보존한다.
        self.assertIn("2.5536", signal.technical)
        self.assertIn("[49, 56, 111, 143, 118]", signal.technical)

    def test_an_automatic_exclusion_says_no_impact_and_no_action(self):
        """사례 1 · 3: 문제 데이터를 빼고 서비스가 계속 도는 상황."""
        signal = next(row for row in self.signals(gate_record())
                      if row.key == "quality:archive-integrity").normalized()
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertTrue(signal.impact.startswith("없음"))
        self.assertIn("필요 없음", signal.action)

    def test_a_real_failure_says_what_to_do(self):
        signal = monitor.web_pipeline_signals(
            {"web_build": "success", "data_gate": "success", "web_deploy": "failure"},
            observation_id="daily-brief:2")[0].normalized()
        self.assertEqual(monitor.LEVEL_ACTION, signal.level)
        self.assertNotIn("없음", signal.impact)
        self.assertIn("확인", signal.action)

    def test_only_the_metrics_step_failing_is_not_an_outage(self):
        """지표 기록만 실패한 날을 '배포 실패'로 부르면 진짜 배포 실패가 안 읽힌다."""
        signal = monitor.web_pipeline_signals(
            {"web_build": "success", "data_gate": "failure", "web_deploy": "success"},
            observation_id="daily-brief:3")[0].normalized()
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertTrue(signal.impact.startswith("없음"))

    def test_developer_tuning_items_do_not_ask_the_operator_to_act(self):
        """`컷을 올려야 한다` 는 운영자가 할 수 있는 일이 아니다."""
        record = gate_record()
        record["issue_candidates"] = {"applicable": True, "guards": [{
            "id": "issue-candidate:topn-retention", "severity": "warning",
            "title": "기사당 Top-10 이 실제 병합을 놓친다",
            "detail": "LLM 승인 병합 170건 중 1건이 상위 10 밖이다 (보존율 0.994). 컷을 올려야 한다.",
        }]}
        signal = next(row for row in self.signals(record)
                      if row.key == "issue-candidate:topn-retention").normalized()
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertTrue(signal.impact.startswith("없음"))
        self.assertIn("개발자", signal.action)
        self.assertIn("컷을 올려야 한다", signal.technical)


class ArchiveIntegrityRepeatTests(unittest.TestCase):
    """사례 1: 같은 21건이 매 빌드마다 새 사고로 보이던 문제."""

    def evaluate(self, record, previous_record, state, now):
        """아카이브 항목만 돌려준다 — 같은 레코드의 주제 흐름 알림과 섞지 않는다."""
        signals = monitor.data_gate_signals(record, previous_record)
        state, due = monitor.evaluate_alerts(
            signals, state, evaluated_scopes={"data_gate"}, now=now)
        return state, [row for row in due if row.key == "quality:archive-integrity"]

    def test_existing_and_new_exclusions_are_counted_apart(self):
        yesterday = gate_record(quarantined=21, sanitized=100)
        today = gate_record(quarantined=21, sanitized=142)
        signal = next(row for row in monitor.data_gate_signals(today, yesterday)
                      if row.key == "quality:archive-integrity")
        self.assertIn("새로 제외 0건", signal.detail)
        self.assertIn("기존 제외 유지 21건", signal.detail)

    def test_the_same_backlog_is_not_notified_again(self):
        """정제 건수만 늘어난 날은 새 소식이 없다 — 다시 부르지 않는다."""
        first = gate_record(quarantined=21, sanitized=100, observation="run-1")
        state, due = self.evaluate(first, None, None, T0)
        self.assertEqual(["quality:archive-integrity"], [row.key for row in due])
        state = monitor.mark_notified(state, due, T0)

        # 쿨다운이 지난 다음 날. 격리는 그대로 21건, 정제만 142건으로 늘었다.
        second = gate_record(quarantined=21, sanitized=142, observation="run-2")
        state, due = self.evaluate(second, first, state, T0 + timedelta(days=1))
        self.assertEqual([], [row.key for row in due])
        # 그다음 날도 마찬가지다.
        third = gate_record(quarantined=21, sanitized=190, observation="run-3")
        _state, due = self.evaluate(third, second, state, T0 + timedelta(days=2))
        self.assertEqual([], [row.key for row in due])

    def test_a_newly_excluded_article_is_notified_again(self):
        first = gate_record(quarantined=21, sanitized=100, observation="run-1")
        state, due = self.evaluate(first, None, None, T0)
        state = monitor.mark_notified(state, due, T0)

        grown = gate_record(quarantined=24, sanitized=142, observation="run-2")
        _state, due = self.evaluate(grown, first, state, T0 + timedelta(days=1))
        signal = next(row for row in due if row.key == "quality:archive-integrity")
        self.assertIn("새로 제외 3건", signal.detail)
        self.assertIn("기존 제외 유지 21건", signal.detail)

    def test_a_replaced_article_counts_as_new_even_at_the_same_total(self):
        """수는 같아도 **다른 기사**가 걸렸으면 새 문제다."""
        first = gate_record(quarantined=3, observation="run-1", quarantine_samples=[
            {"hash": "aaa1"}, {"hash": "aaa2"}, {"hash": "aaa3"}])
        state, due = self.evaluate(first, None, None, T0)
        state = monitor.mark_notified(state, due, T0)
        swapped = gate_record(quarantined=3, observation="run-2", quarantine_samples=[
            {"hash": "aaa1"}, {"hash": "aaa2"}, {"hash": "bbb9"}])
        _state, due = self.evaluate(swapped, first, state, T0 + timedelta(days=1))
        signal = next(row for row in due if row.key == "quality:archive-integrity")
        self.assertIn("새로 제외 1건", signal.detail)

    def test_a_previous_record_without_samples_falls_back_to_counts(self):
        """표본이 없는 옛 기록과 비교할 때 전부 새것이라고 말하면 안 된다."""
        previous = gate_record(quarantined=21, quarantine_samples=[])
        signal = next(row for row in monitor.data_gate_signals(gate_record(), previous)
                      if row.key == "quality:archive-integrity")
        self.assertIn("새로 제외 0건", signal.detail)
        self.assertIn("기존 제외 유지 21건", signal.detail)

    def test_escalation_from_date_fixes_to_exclusion_still_pages(self):
        """정제만 있던 상태에서 격리가 생기면 쿨다운을 기다리지 않는다."""
        first = gate_record(quarantined=0, sanitized=6, observation="run-1")
        state, due = self.evaluate(first, None, None, T0)
        state = monitor.mark_notified(state, due, T0)
        worse = gate_record(quarantined=2, sanitized=6, observation="run-2")
        _state, due = self.evaluate(worse, first, state, T0 + timedelta(hours=2))
        self.assertEqual(["quality:archive-integrity"], [row.key for row in due])


class RepeatAndRecoveryTests(unittest.TestCase):
    def test_a_hidden_metric_does_not_repeat_while_the_ratio_drifts(self):
        """비율은 매일 움직이지만 숨은 지표가 그대로면 새 소식이 아니다."""
        first = gate_record(flow_ratio=2.8163, observation="run-1")
        state, due = monitor.evaluate_alerts(
            monitor.data_gate_signals(first), None,
            evaluated_scopes={"data_gate"}, now=T0)
        state, due = monitor.evaluate_alerts(
            monitor.data_gate_signals(gate_record(flow_ratio=2.7, observation="run-2")),
            state, evaluated_scopes={"data_gate"}, now=T0 + timedelta(days=1))
        self.assertIn("quality:topic-weeks", [row.key for row in due])
        state = monitor.mark_notified(state, due, T0 + timedelta(days=1))

        drifted = gate_record(flow_ratio=2.5536, totals=[49, 56, 111, 143, 118],
                              observation="run-3")
        _state, due = monitor.evaluate_alerts(
            monitor.data_gate_signals(drifted), state,
            evaluated_scopes={"data_gate"}, now=T0 + timedelta(days=2))
        self.assertNotIn("quality:topic-weeks", [row.key for row in due])

    def test_a_stale_feed_is_reported_once_not_once_a_day(self):
        health = {"sources": {"SFEN": {
            "kind": "feed", "last_checked_at": "2026-08-22T04:00:00+00:00",
            "last_newest_pub": "2026-08-07T12:14:35+00:00"}}}
        state, due = monitor.evaluate_alerts(
            monitor.source_health_signals(health, now=T0), None,
            evaluated_scopes={"source"}, now=T0)
        self.assertEqual(["source:SFEN:stale"], [row.key for row in due])
        state = monitor.mark_notified(state, due, T0)

        later = T0 + timedelta(days=3)
        _state, due = monitor.evaluate_alerts(
            monitor.source_health_signals(health, now=later), state,
            evaluated_scopes={"source"}, now=later)
        self.assertEqual([], due)

    def test_a_new_article_resolves_the_stale_alert_and_says_so(self):
        health = {"sources": {"SFEN": {
            "kind": "feed", "last_checked_at": "2026-08-22T04:00:00+00:00",
            "last_newest_pub": "2026-08-07T12:14:35+00:00"}}}
        state, due = monitor.evaluate_alerts(
            monitor.source_health_signals(health, now=T0), None,
            evaluated_scopes={"source"}, now=T0)
        state = monitor.mark_notified(state, due, T0)

        recovered = T0 + timedelta(days=1)
        state, due = monitor.evaluate_alerts([], state, evaluated_scopes={"source"},
                                             now=recovered)
        self.assertEqual([monitor.LEVEL_RESOLVED], [row.level for row in due])
        message = monitor.format_admin_alerts(due)
        self.assertIn("해결됨", message)
        self.assertIn("SFEN", message)

    def test_a_problem_that_comes_back_is_reported_again(self):
        def signal(observation, severity="warning"):
            return monitor.AlertSignal(
                key="source:X:failure", scope="source", title="X 기사를 가져오지 못하고 있습니다",
                detail="연속 실패", severity=severity, observation_id=observation,
                min_occurrences=1)

        state, due = monitor.evaluate_alerts([signal("r1")], None,
                                             evaluated_scopes={"source"}, now=T0)
        state = monitor.mark_notified(state, due, T0)
        state, due = monitor.evaluate_alerts([], state, evaluated_scopes={"source"},
                                             now=T0 + timedelta(hours=1))
        state = monitor.mark_notified(state, due, T0 + timedelta(hours=1))

        # 쿨다운이 끝나기 전에 다시 났다. 재발은 새 사고다.
        _state, due = monitor.evaluate_alerts([signal("r2")], state,
                                              evaluated_scopes={"source"},
                                              now=T0 + timedelta(hours=2))
        self.assertEqual(["source:X:failure"], [row.key for row in due])
        self.assertEqual([monitor.LEVEL_ATTENTION], [row.normalized().level for row in due])

    def test_an_ongoing_outage_still_reminds_every_cooldown(self):
        """지문이 없는 알림(진행 중인 장애)의 반복은 그대로 살아 있어야 한다."""
        def signal(observation):
            return monitor.AlertSignal(
                key="source:collection-pipeline-failure", scope="collection_pipeline",
                title="뉴스 수집 작업이 끝까지 실행되지 못했습니다", detail="멈췄습니다",
                severity="critical", observation_id=observation, min_occurrences=1)

        state, due = monitor.evaluate_alerts([signal("r1")], None,
                                             evaluated_scopes={"collection_pipeline"}, now=T0)
        state = monitor.mark_notified(state, due, T0)
        state, due = monitor.evaluate_alerts([signal("r2")], state,
                                             evaluated_scopes={"collection_pipeline"},
                                             now=T0 + timedelta(hours=3))
        self.assertEqual([], due)
        _state, due = monitor.evaluate_alerts([signal("r3")], state,
                                              evaluated_scopes={"collection_pipeline"},
                                              now=T0 + timedelta(hours=25))
        self.assertEqual(1, len(due))


class ChannelRenderingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent = Path(self.tmp.name) / "sent.json"
        self.log = Path(self.tmp.name) / "delivery_log.jsonl"
        self.sent.write_text(json.dumps({"sent": {}, "source_yield": {}}),
                             encoding="utf-8")

    def test_the_message_reads_what_impact_action_then_details(self):
        alerts = [
            monitor.AlertSignal(
                key="quality:archive-integrity", scope="data_gate",
                title="신뢰하기 어려운 아카이브 기사를 자동 제외했습니다",
                detail="새로 제외 0건 · 기존 제외 유지 21건.",
                severity="critical", level=monitor.LEVEL_ATTENTION,
                impact="없음 — 나머지 뉴스와 서비스는 정상입니다.",
                action="필요 없음 — 자동으로 처리됐습니다.",
                technical="quarantined=21 sanitized=142"),
            monitor.AlertSignal(
                key="quality:web-pipeline-failure", scope="web_pipeline",
                title="사이트 배포가 실패했습니다", detail="자동 실행이 도중에 멈췄습니다.",
                severity="critical", level=monitor.LEVEL_ACTION,
                impact="새 데이터가 사이트에 반영되지 않았습니다.",
                action="배포 로그를 확인해 주세요.",
                technical="Cloudflare 배포·스모크=failure"),
        ]
        message = monitor.format_admin_alerts(alerts)
        # 조치가 필요한 것이 먼저 온다.
        self.assertLess(message.index("조치 필요"), message.index("확인 필요"))
        self.assertLess(message.index("사이트 배포가 실패했습니다"),
                        message.index("자동 제외했습니다"))
        block = message[message.index("사이트 배포가 실패했습니다"):]
        self.assertLess(block.index("서비스 영향:"), block.index("조치:"))
        self.assertLess(block.index("조치:"), block.index("상세:"))
        self.assertTrue(message.startswith("🚨"))

    def test_a_quiet_run_sends_nothing_and_says_so(self):
        out = cli.run(sent_path=self.sent, log_path=self.log, notify=True,
                      sender=lambda _text: self.fail("정상 회차에 알림이 나갔다"),
                      expected_sources={}, pipeline_outcomes={
                          "web_build": "success", "data_gate": "success",
                          "web_deploy": "success"},
                      pipeline_observation_id="daily-brief:1",
                      collection_outcome="success",
                      collection_observation_id="crawl:1", now=T0)
        self.assertTrue(out["ok"])
        self.assertEqual(0, out["due"])
        self.assertFalse(out["sent"])

    def test_the_run_log_keeps_every_technical_value(self):
        """운영자 문장에서 뺀 값은 실행 로그에 그대로 남아야 한다."""
        alerts = monitor.data_gate_signals(gate_record())
        log = monitor.format_technical_log(alerts)
        self.assertIn("quarantined=21", log)
        self.assertIn("sanitized=142", log)
        self.assertIn("flow_ratio=2.5536", log)
        self.assertIn("[49, 56, 111, 143, 118]", log)
        self.assertIn("fingerprint=", log)
        self.assertIn("level=attention", log)


class DeliveryFailureTests(unittest.TestCase):
    """텔레그램 발송 실패는 '처리 실패'가 아니라 **구독자가 못 받았다**는 뜻이다."""

    def test_a_failed_send_says_subscribers_did_not_get_it(self):
        rows = [{
            "record_type": "selection_stats", "date": "2026-08-22",
            "generated_at": "2026-08-22T05:00:00+09:00", "pipeline_status": "partial",
            "domestic": {"candidate_count": 40, "selected_count": 6},
            "overseas": {"candidate_count": 30, "selected_count": 4},
        }]
        signals, scopes = monitor.daily_quality_signals(rows, "2026-08-22")
        self.assertIn("selection", scopes)
        signal = next(row for row in signals
                      if row.key == "quality:brief-partial").normalized()
        self.assertEqual(monitor.LEVEL_ACTION, signal.level)
        self.assertIn("발송", signal.title)
        self.assertIn("구독자", signal.impact)
        self.assertIn("확인", signal.action)
        self.assertIn("pipeline_status=partial", signal.technical)


class CountFingerprintTests(unittest.TestCase):
    """건수가 흔들리는 알림을 회차마다 다시 부르지 않기 위한 규칙."""

    def test_the_same_scale_is_the_same_situation(self):
        same = {monitor.count_fingerprint("x", value) for value in (2, 4, 5, 9)}
        self.assertEqual(1, len(same))

    def test_a_different_scale_is_a_new_situation(self):
        self.assertNotEqual(monitor.count_fingerprint("x", 5),
                            monitor.count_fingerprint("x", 33))
        self.assertNotEqual(monitor.count_fingerprint("x", 33),
                            monitor.count_fingerprint("x", 500))

    def test_labels_never_collide(self):
        self.assertNotEqual(monitor.count_fingerprint("cards", 3),
                            monitor.count_fingerprint("fallback", 3))


class DeliveryGateEmitterTests(unittest.TestCase):
    """사례 3: 발송 직전 안전 차단이 실제로 어떻게 전달되는가."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "delivery_log.jsonl"
        self.log.touch()

    def outbox(self, integrity_count, *, date="2026-08-22", created="T04:35:17+09:00"):
        return {
            "date": date, "created_at": f"{date}{created}",
            "quality_diag": {"held_before_ranking": [
                {"hash": f"h{index}", "action": "quarantine"}
                for index in range(integrity_count)]},
        }

    def emit(self, outbox):
        import daily_brief
        daily_brief.append_quality_audit(outbox, path=self.log)
        rows = [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in rows if row.get("date") == outbox["date"]]

    def test_a_safe_block_is_not_shown_as_an_outage(self):
        rows = self.emit(self.outbox(33))
        signals, _scopes = monitor.daily_quality_signals(rows, "2026-08-22")
        signal = next(row for row in signals
                      if row.key.endswith("delivery-integrity-quarantine")).normalized()
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertTrue(signal.impact.startswith("없음"))
        self.assertIn("필요 없음", signal.action)
        message = monitor.format_admin_alerts([signal])
        self.assertNotIn("조치 필요", message)

    def test_a_similar_count_tomorrow_is_not_a_new_incident(self):
        today = monitor.daily_quality_signals(self.emit(self.outbox(33)), "2026-08-22")[0]
        state, due = monitor.evaluate_alerts(today, None,
                                             evaluated_scopes={"quality_event"}, now=T0)
        state = monitor.mark_notified(state, due, T0)
        tomorrow = monitor.daily_quality_signals(
            self.emit(self.outbox(29, date="2026-08-23")), "2026-08-23")[0]
        _state, due = monitor.evaluate_alerts(tomorrow, state,
                                              evaluated_scopes={"quality_event"},
                                              now=T0 + timedelta(days=1))
        self.assertEqual([], due)

    def test_a_ten_fold_jump_is_reported_again(self):
        today = monitor.daily_quality_signals(self.emit(self.outbox(33)), "2026-08-22")[0]
        state, due = monitor.evaluate_alerts(today, None,
                                             evaluated_scopes={"quality_event"}, now=T0)
        state = monitor.mark_notified(state, due, T0)
        tomorrow = monitor.daily_quality_signals(
            self.emit(self.outbox(420, date="2026-08-23")), "2026-08-23")[0]
        _state, due = monitor.evaluate_alerts(tomorrow, state,
                                              evaluated_scopes={"quality_event"},
                                              now=T0 + timedelta(days=1))
        self.assertEqual(["quality-event:delivery-integrity-quarantine"],
                         [row.key for row in due])


class QualityEventContractTests(unittest.TestCase):
    """다른 모듈이 남긴 품질 이벤트도 같은 계약으로 렌더링된다."""

    def test_operator_fields_travel_through_the_delivery_log(self):
        rows = [{
            "record_type": "quality_event", "date": "2026-08-22",
            "generated_at": "2026-08-22T04:35:17+09:00",
            "alert_key": "delivery-integrity-quarantine",
            "title": "원문과 다른 기사를 발송 전에 걸렀습니다",
            "detail": "요약이 원문과 다르게 만들어진 기사 33건을 발송 대상에서 뺐습니다.",
            "impact": "없음 — 제외된 기사만 빠지고, 나머지 기사와 서비스는 정상입니다.",
            "action": "필요 없음 — 자동으로 차단됐습니다.",
            "technical": "held_before_ranking action=quarantine count=33",
            "level": "attention", "fingerprint": "integrity=33",
            "severity": "critical", "min_occurrences": 1,
        }]
        signals, scopes = monitor.daily_quality_signals(rows, "2026-08-22")
        self.assertIn("quality_event", scopes)
        signal = signals[0].normalized()
        self.assertEqual(monitor.LEVEL_ATTENTION, signal.level)
        self.assertEqual("integrity=33", signal.fingerprint)
        message = monitor.format_admin_alerts([signal])
        self.assertIn("확인 필요", message)
        self.assertNotIn("조치 필요", message)

    def test_a_record_without_operator_fields_still_renders(self):
        rows = [{
            "record_type": "quality_event", "date": "2026-08-22",
            "generated_at": "2026-08-22T04:35:17+09:00",
            "alert_key": "legacy", "title": "옛 계약", "detail": "본문",
            "severity": "warning", "min_occurrences": 1,
        }]
        signals, _scopes = monitor.daily_quality_signals(rows, "2026-08-22")
        message = monitor.format_admin_alerts(signals)
        self.assertIn("옛 계약", message)
        self.assertIn("본문", message)


if __name__ == "__main__":
    unittest.main()
