"""data_gate_metrics.py — 배포를 막지 않는 데이터 품질 계측."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import data_gate_metrics as gate


class ConstantsMatchTheFrontEnd(unittest.TestCase):
    """계측은 '화면이 입을 다무는가'를 재는 것이라 화면과 같은 상수를 써야 한다.

    두 곳에 따로 적힌 값은 조용히 어긋난다 — 어긋나면 로그는 '보인다'고 하는데
    화면은 비어 있는 상태가 되고, 그게 정확히 이 계측을 만든 이유(감시가
    어디에도 안 걸려 있어 눈으로 발견했다)를 되풀이한다.
    """

    def setUp(self):
        self.script = (ROOT / "web" / "public" / "app.js").read_text(encoding="utf-8")

    def _const(self, name: str) -> float:
        match = re.search(rf"const {name} = ([0-9.]+);", self.script)
        self.assertIsNotNone(match, f"app.js 에 {name} 이 없다")
        return float(match.group(1))

    def test_sample_ratio_matches(self):
        self.assertEqual(self._const("TOPIC_WEEK_SAMPLE_RATIO"), gate.TOPIC_WEEK_SAMPLE_RATIO)

    def test_flow_span_matches(self):
        self.assertEqual(self._const("TOPIC_FLOW_MIN_WEEKS"), gate.TOPIC_FLOW_MIN_WEEKS)
        self.assertEqual(self._const("TOPIC_FLOW_MAX_WEEKS"), gate.TOPIC_FLOW_MAX_WEEKS)


class TopicWeekMeasurement(unittest.TestCase):
    def _measure(self, totals):
        weeks = [f"2026-W{30 + i}" for i in range(len(totals))]
        original = gate.build_data.build_topic_weeks
        gate.build_data.build_topic_weeks = lambda catalog, dates: (
            weeks, {"smr": list(totals)})
        try:
            return gate.measure_topic_weeks([], [{"date": "2026-08-01"}])
        finally:
            gate.build_data.build_topic_weeks = original

    def test_flow_and_slope_can_disagree(self):
        """2026-08-15 실측: 3주 2.04 는 막히고 마지막 2주 2.00 은 통과했다.

        한쪽만 막히는 날이 실제로 있으므로 둘을 따로 남긴다 — 합쳐 두면
        '그래프가 왜 하나만 보이나'를 로그로 설명할 수 없다.
        """
        out = self._measure([49, 50, 100])
        self.assertEqual(2.0408, out["flow_ratio"])
        self.assertFalse(out["flow_visible"])
        self.assertEqual(2.0, out["slope_ratio"])
        self.assertTrue(out["slope_visible"])

    def test_boundary_is_inclusive_like_the_front_end(self):
        """화면은 `high / low <= 2` 다. 정확히 2.0 이면 보인다."""
        self.assertTrue(self._measure([50, 100])["slope_visible"])
        self.assertFalse(self._measure([50, 101])["slope_visible"])

    def test_fewer_than_three_weeks_has_no_flow_table(self):
        """온전한 주가 3개 미만이면 표 자체가 내려간다 — 비율을 잴 대상이 없다."""
        out = self._measure([50, 60])
        self.assertIsNone(out["flow_ratio"])
        self.assertFalse(out["flow_visible"])
        self.assertEqual(1.2, out["slope_ratio"])   # 슬로프는 2주만 있어도 잰다

    def test_zero_week_does_not_crash(self):
        out = self._measure([0, 40])
        self.assertIsNone(out["slope_ratio"])
        self.assertFalse(out["slope_visible"])


class TrackingMeasurement(unittest.TestCase):
    BASE = {
        "remote_embedding_selected_count": 12,
        "tracking_window_briefings": 7,
        "tracking_window_rate": 0.1081,
        "tracking_window_issue_count": 111,
        "tracking_window_tracked_issue_count": 12,
    }

    def test_below_target_is_flagged(self):
        out = gate.measure_tracking(dict(self.BASE))
        self.assertTrue(out["applicable"])
        self.assertTrue(out["below_target"])

    def test_local_fallback_build_is_not_judged(self):
        """로컬 폴백 벡터는 병합이 구조적으로 보수적이라 낮게 나온다 —
        환경 차이지 코드 결함이 아니다."""
        out = gate.measure_tracking(dict(self.BASE, remote_embedding_selected_count=0))
        self.assertFalse(out["applicable"])
        self.assertFalse(out["below_target"])

    def test_short_window_is_not_judged(self):
        """분모가 작으면 이 지표는 병합기가 아니라 그날 뉴스량을 잰다."""
        out = gate.measure_tracking(dict(self.BASE, tracking_window_briefings=3))
        self.assertFalse(out["applicable"])
        self.assertFalse(out["below_target"])


class NeverBlocksTheDeploy(unittest.TestCase):
    """품질 임계값은 배포 게이트가 아니지만 계측 실패는 outcome으로 남긴다.

    막는 쪽으로 돌리면 2026-08-03·08-11 사고(뉴스가 한산하거나 한 주에 몰린
    것만으로 CSS 오타 수정까지 배포가 막힘)가 그대로 되돌아온다. 산출물 자체가
    없을 때의 1은 continue-on-error step에서 관리자 알림용으로만 사용한다.
    """

    def test_missing_build_output_returns_failure_outcome(self):
        original = gate.WEB_DATA
        gate.WEB_DATA = ROOT / "does-not-exist"
        try:
            self.assertEqual(1, gate.main())
        finally:
            gate.WEB_DATA = original

    def test_metrics_below_target_still_return_zero(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "delivery_log.jsonl"
        original_log, original_record = gate.DELIVERY_LOG, gate.build_record
        gate.DELIVERY_LOG = log
        gate.build_record = lambda now=None: {
            "record_type": gate.RECORD_TYPE, "date": "2026-08-16",
            "generated_at": "2026-08-16T02:00:00+09:00",
            "tracking": gate.measure_tracking(dict(TrackingMeasurement.BASE)),
            "topic_weeks": {"weeks": ["a", "b"], "totals": [50, 101], "limit": 2.0,
                            "flow_ratio": None, "slope_ratio": 2.02,
                            "flow_visible": False, "slope_visible": False},
        }
        try:
            self.assertEqual(0, gate.main())
        finally:
            gate.DELIVERY_LOG, gate.build_record = original_log, original_record
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(1, len(rows))
        # record_type 이 붙은 줄은 기존 리더가 전부 건너뛴다
        # (daily_lead.collect_today · metrics.load_data · build_data).
        self.assertEqual(gate.RECORD_TYPE, rows[0]["record_type"])

    def test_github_run_id_is_a_retry_stable_observation_id(self):
        now = gate.datetime(2026, 8, 17, 0, 0, tzinfo=gate.timezone.utc)
        values = {
            "meta.json": {}, "issues.json": [], "briefings.json": [],
            "issue_audit.json": {},
        }
        with patch.object(gate, "_load", side_effect=lambda name: values[name]), \
                patch.object(gate, "measure_topic_weeks", return_value={}), \
                patch.dict("os.environ", {"GITHUB_RUN_ID": "98765"}):
            record = gate.build_record(now)
        self.assertEqual("github-run:98765", record["observation_id"])


def _candidates(**rest):
    base = {"merge_rate": 0.17, "evidence_attach_rate": 0.19, "evidence_share": 0.70,
            "applicable": True}
    base.update(rest)
    return base


class IssueCandidateMeasurement(unittest.TestCase):
    """후보 생성이 평소와 같은가 — 회차 **사이**의 변화를 보는 자리.

    build_data 는 회차 안의 사실만 볼 수 있다. 병합률이 어제와 크게 다른 것은
    하루치 안에서는 원리상 안 보이고, 그게 여기 있는 이유다.
    """

    def test_the_baseline_is_a_median_not_an_average(self):
        """한 회차의 사고가 기준선을 끌고 가면 다음 회차에 그 사고가 정상이 된다."""
        history = [_candidates(merge_rate=value) for value in (0.17, 0.16, 0.18, 0.02)]
        self.assertEqual(gate.candidate_baseline(history)["merge_rate"], 0.165)

    def test_too_few_records_means_no_baseline(self):
        """이틀치로 표류를 말하면 매일 운다. 표류는 '평소'가 있어야 말할 수 있다."""
        self.assertEqual(gate.candidate_baseline([_candidates(), _candidates()]), {})

    def test_only_applicable_records_feed_the_baseline(self):
        log = Path(tempfile.mkdtemp()) / "delivery_log.jsonl"
        rows = [
            {"record_type": "data_quality_gate",
             "issue_candidates": _candidates(merge_rate=0.17)},
            {"record_type": "data_quality_gate",
             "issue_candidates": {"applicable": False, "reason": "없음"}},
            {"record_type": "briefing", "issue_candidates": _candidates(merge_rate=9.9)},
        ]
        log.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                       encoding="utf-8")
        history = gate.previous_candidate_records(log)
        self.assertEqual([row["merge_rate"] for row in history], [0.17])

    def test_a_build_without_diagnostics_is_not_judged(self):
        measured = gate.measure_issue_candidates({}, [])
        self.assertFalse(measured["applicable"])
        self.assertNotIn("guards", measured)

    def test_drift_is_reported_against_the_recent_median(self):
        audit = {"candidate_diagnostics": {
            "bands": {"total": 32416, "review_band_count": 845, "evidence_share": 0.70},
            "search_space": [], "top_n_retention": {},
            "merge_rate": 0.30, "evidence_attach_rate": 0.19, "evidence_share": 0.70,
        }}
        history = [_candidates(merge_rate=value) for value in (0.17, 0.16, 0.18)]
        measured = gate.measure_issue_candidates(audit, history)
        self.assertTrue(measured["applicable"])
        self.assertEqual(measured["candidate_total"], 32416)
        self.assertEqual([row["id"] for row in measured["guards"]],
                         ["issue-candidate:merge-rate-drift"])

    def test_a_steady_run_produces_no_guards(self):
        """평소엔 조용해야 한다 — 이 테스트가 깨지면 알림이 배경 소음이 된다."""
        audit = {"candidate_diagnostics": {
            "bands": {"total": 32416, "review_band_count": 845, "evidence_share": 0.70},
            "search_space": [], "top_n_retention": {},
            "merge_rate": 0.17, "evidence_attach_rate": 0.19, "evidence_share": 0.70,
        }}
        history = [_candidates() for _ in range(5)]
        self.assertEqual(gate.measure_issue_candidates(audit, history)["guards"], [])


if __name__ == "__main__":
    unittest.main()
