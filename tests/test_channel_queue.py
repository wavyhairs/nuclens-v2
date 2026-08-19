"""구독 채널 일괄 공개 큐 — 순서·멱등·격리 계약.

이 테스트가 지키는 것은 '구독자가 보는 화면'이다. 순서가 흔들리면 오디오가
기사보다 먼저 뜨고, 멱등이 깨지면 같은 카드가 두 번 뜨고, 폴백이 열리면
채널이 빈 날에 아무도 그걸 모른다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import _fake_tg  # noqa: E402,F401 — 공용 fake telegram_send 선등록
import channel_queue  # noqa: E402

NOW = datetime(2026, 8, 17, 19, 30, tzinfo=timezone.utc)


class RecordingSender:
    """발송된 항목을 순서대로 기록하는 가짜 채널."""

    def __init__(self, fail_names: set[str] | None = None):
        self.sent: list[tuple[str, str]] = []
        self.fail_names = fail_names or set()

    def send_text(self, item):
        if item["name"] in self.fail_names:
            raise RuntimeError("telegram down (모의)")
        self.sent.append(("text", item["name"]))
        return True

    def send_audio(self, item):
        if item["name"] in self.fail_names:
            return False
        self.sent.append(("audio", item["file_id"]))
        return True


def _outbox(date="2026-08-17", names=("보고서추천", "국내", "해외")):
    return {
        "date": date, "status": "pending",
        "briefs": [{"name": n, "text": f"<b>{n}</b> 본문", "status": "pending"}
                   for n in names],
    }


class ChannelQueueTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "channel_outbox.json"
        self.addCleanup(self._tmp.cleanup)

    def _load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _stage_full_day(self, date="2026-08-17"):
        channel_queue.sync_daily_batch(_outbox(date), path=self.path, now=NOW)
        channel_queue.record_audio(date, name="빠른 브리핑", file_id="fast-id",
                                   duration=170, path=self.path, now=NOW)
        channel_queue.record_audio(date, name="전문가 브리핑", file_id="expert-id",
                                   duration=526, path=self.path, now=NOW)

    # ---- 순서 ----------------------------------------------------------------

    def test_the_batch_keeps_the_briefing_order(self):
        """구독자에게 보고서추천 → 국내 → 해외 → 빠른 → 전문가 순으로 간다.

        DM 은 흩어져 도착해도 각자 시각이 다르니 순서가 저절로 드러나지만,
        채널은 한 번에 올라가므로 큐에 적힌 순서가 곧 화면 순서다.
        """
        self._stage_full_day()
        sender = RecordingSender()
        channel_queue.publish(path=self.path, now=NOW, sender=sender, gap_sec=0)
        self.assertEqual(sender.sent, [
            ("text", "보고서추천"), ("text", "국내"), ("text", "해외"),
            ("audio", "fast-id"), ("audio", "expert-id"),
        ])

    def test_a_day_without_report_recommendations_just_has_fewer_items(self):
        """보고서추천은 있는 날만 만들어진다 — 없다고 배치가 깨지면 안 된다."""
        channel_queue.sync_daily_batch(_outbox(names=("국내", "해외")),
                                       path=self.path, now=NOW)
        sender = RecordingSender()
        channel_queue.publish(path=self.path, now=NOW, sender=sender, gap_sec=0)
        self.assertEqual(sender.sent, [("text", "국내"), ("text", "해외")])

    def test_briefs_marked_stale_never_reach_the_channel(self):
        """DM 에서 '보내지 않기로 한' 브리핑은 채널로도 가면 안 된다."""
        outbox = _outbox()
        outbox["briefs"][1]["status"] = "stale_skipped"
        channel_queue.sync_daily_batch(outbox, path=self.path, now=NOW)
        names = [i["name"] for i in self._load()["batches"][0]["items"]]
        self.assertEqual(names, ["보고서추천", "해외"])

    def test_a_rejected_outbox_stages_nothing(self):
        """품질 게이트가 막은 outbox 는 채널 배치도 만들지 않는다."""
        outbox = _outbox()
        outbox["status"] = "quality_rejected"
        self.assertIsNone(channel_queue.sync_daily_batch(outbox, path=self.path,
                                                         now=NOW))
        self.assertFalse(self.path.exists())

    # ---- 멱등 ----------------------------------------------------------------

    def test_restaging_does_not_duplicate_items(self):
        """plan 은 claim push 충돌 때 최대 5회까지 다시 돈다."""
        for _ in range(3):
            channel_queue.sync_daily_batch(_outbox(), path=self.path, now=NOW)
            channel_queue.record_audio("2026-08-17", name="빠른 브리핑",
                                       file_id="fast-id", path=self.path, now=NOW)
        batches = self._load()["batches"]
        self.assertEqual(len(batches), 1)
        self.assertEqual([i["name"] for i in batches[0]["items"]],
                         ["보고서추천", "국내", "해외", "빠른 브리핑"])

    def test_restaging_never_reopens_a_sent_item(self):
        """되돌리면 그게 곧 중복 발송이다."""
        self._stage_full_day()
        channel_queue.publish(path=self.path, now=NOW, sender=RecordingSender(),
                              gap_sec=0)
        channel_queue.sync_daily_batch(_outbox(), path=self.path, now=NOW)
        channel_queue.record_audio("2026-08-17", name="빠른 브리핑",
                                   file_id="fast-id", path=self.path, now=NOW)
        statuses = {i["name"]: i["status"] for i in self._load()["batches"][0]["items"]}
        self.assertTrue(all(s == "sent" for s in statuses.values()), statuses)

    def test_publishing_twice_sends_nothing_the_second_time(self):
        """같은 날 워크플로 재실행이 채널에 두 번 뜨면 안 된다."""
        self._stage_full_day()
        channel_queue.publish(path=self.path, now=NOW, sender=RecordingSender(),
                              gap_sec=0)
        second = RecordingSender()
        channel_queue.publish(path=self.path, now=NOW, sender=second, gap_sec=0)
        self.assertEqual(second.sent, [])

    def test_audio_added_after_the_texts_went_out_still_publishes(self):
        """텍스트가 먼저 나가 배치가 닫힌 뒤 붙인 오디오도 반드시 나가야 한다.

        2026-08-20 실사고 회귀: ffmpeg 부재로 오디오만 실패해 텍스트 3건이 sent 로
        배치를 닫았고, 복구 회차가 오디오를 만들어 붙였다. ensure_batch 는 기존
        배치의 status 를 건드리지 않으므로 배치는 sent 인 채였고, publish 는
        pending/partial/failed 만 훑으니 그 오디오는 큐에 앉은 채 사라질 참이었다.
        """
        date = "2026-08-17"
        channel_queue.sync_daily_batch(_outbox(date), path=self.path, now=NOW)
        first = RecordingSender()
        channel_queue.publish(path=self.path, now=NOW, sender=first, gap_sec=0)
        self.assertEqual([n for _, n in first.sent], ["보고서추천", "국내", "해외"])
        self.assertEqual(self._load()["batches"][0]["status"], "sent")

        # 뒤늦게 도착한 오디오
        channel_queue.record_audio(date, name="빠른 브리핑", file_id="fast-id",
                                   duration=170, path=self.path, now=NOW)
        self.assertEqual(self._load()["batches"][0]["status"], "partial",
                         "sent 로 닫힌 배치가 새 오디오로 다시 열리지 않았다")

        second = RecordingSender()
        channel_queue.publish(path=self.path, now=NOW, sender=second, gap_sec=0)
        self.assertEqual([n for _, n in second.sent], ["fast-id"],
                         "오디오만 나가야 한다 — 텍스트 재발송은 중복이다")

    # ---- 실패·경계 -----------------------------------------------------------

    def test_one_failed_item_does_not_block_the_rest(self):
        self._stage_full_day()
        sender = RecordingSender(fail_names={"국내"})
        channel_queue.publish(path=self.path, now=NOW, sender=sender, gap_sec=0)
        self.assertEqual([n for _, n in sender.sent],
                         ["보고서추천", "해외", "fast-id", "expert-id"])
        batch = self._load()["batches"][0]
        self.assertEqual(batch["status"], "partial")
        failed = [i["name"] for i in batch["items"] if i["status"] == "failed"]
        self.assertEqual(failed, ["국내"])

    def test_a_failed_item_is_retried_on_the_next_publish(self):
        self._stage_full_day()
        channel_queue.publish(path=self.path, now=NOW,
                              sender=RecordingSender(fail_names={"국내"}), gap_sec=0)
        retry = RecordingSender()
        channel_queue.publish(path=self.path, now=NOW, sender=retry, gap_sec=0)
        self.assertEqual(retry.sent, [("text", "국내")])

    def test_yesterdays_batch_is_not_published_today(self):
        """어제 아침 자료가 오늘 아침 채널에 뜨는 것은 지연이 아니라 오배송이다."""
        self._stage_full_day()
        later = NOW + timedelta(hours=channel_queue.STALE_H + 1)
        sender = RecordingSender()
        channel_queue.publish(path=self.path, now=later, sender=sender, gap_sec=0)
        self.assertEqual(sender.sent, [])
        self.assertEqual(self._load()["batches"][0]["status"], "stale_skipped")

    def test_audio_without_a_file_id_is_not_staged(self):
        """mp3 는 git 에 없다 — file_id 가 없으면 채널에서 만들 방법이 없다."""
        channel_queue.sync_daily_batch(_outbox(), path=self.path, now=NOW)
        self.assertFalse(channel_queue.record_audio(
            "2026-08-17", name="빠른 브리핑", file_id="", path=self.path, now=NOW))
        kinds = [i["kind"] for i in self._load()["batches"][0]["items"]]
        self.assertNotIn("audio", kinds)

    def test_old_batches_are_pruned(self):
        channel_queue.sync_daily_batch(_outbox(date="2026-07-01"), path=self.path,
                                       now=NOW - timedelta(days=40))
        channel_queue.sync_daily_batch(_outbox(), path=self.path, now=NOW)
        ids = [b["id"] for b in self._load()["batches"]]
        self.assertEqual(ids, ["daily-2026-08-17"])

    def test_a_corrupt_queue_file_does_not_crash_the_pipeline(self):
        self.path.write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(channel_queue.load_queue(self.path)["batches"], [])

    # ---- 주간 판세 -----------------------------------------------------------

    def test_the_weekly_report_goes_out_alone_and_immediately(self):
        """일일 배치를 기다리면 '주간'이라는 말이 무색해진다."""
        self._stage_full_day()
        sender = RecordingSender()
        channel_queue.publish_weekly("<b>주간 판세</b>", date="2026-08-21",
                                     path=self.path, now=NOW, sender=sender,
                                     gap_sec=0)
        self.assertEqual(sender.sent, [("text", channel_queue.WEEKLY_ITEM_NAME)])
        pending = [b["id"] for b in self._load()["batches"] if b["status"] == "pending"]
        self.assertEqual(pending, ["daily-2026-08-17"])

    def test_the_weekly_report_keeps_previews_disabled(self):
        """DM 과 같은 모양이어야 한다 — 링크 미리보기가 켜지면 판세가 밀린다."""
        channel_queue.publish_weekly("<b>주간 판세</b>", date="2026-08-21",
                                     path=self.path, now=NOW,
                                     sender=RecordingSender(), gap_sec=0)
        item = self._load()["batches"][0]["items"][0]
        self.assertTrue(item["disable_preview"])


class ChannelTargetTest(unittest.TestCase):
    """폴백 금지 — 채널 설정이 빠진 날 조용히 DM 으로 새면 안 된다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "channel_outbox.json"
        self.addCleanup(self._tmp.cleanup)
        self._orig = channel_queue.channel_id

        def restore():
            channel_queue.channel_id = self._orig
        self.addCleanup(restore)

    def test_a_missing_channel_id_leaves_the_batch_pending(self):
        channel_queue.channel_id = lambda: None
        channel_queue.sync_daily_batch(_outbox(), path=self.path, now=NOW)
        self.assertEqual(channel_queue.publish(path=self.path, now=NOW, gap_sec=0), [])
        queue = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(queue["batches"][0]["status"], "pending")

    def test_the_channel_id_never_falls_back_to_the_dm_chat(self):
        source = (Path(__file__).parent.parent / "channel_queue.py").read_text(
            encoding="utf-8")
        resolved = [line for line in source.splitlines()
                    if "resolve_setting(" in line and "TELEGRAM_" in line]
        self.assertTrue(resolved)
        self.assertNotIn("TELEGRAM_CHAT_ID", "\n".join(resolved))


class WorkflowWiringTest(unittest.TestCase):
    """자리가 곧 계약이다 — 스텝 순서가 도착 순서를 만든다."""

    ROOT = Path(__file__).parent.parent

    def _daily(self) -> str:
        return (self.ROOT / ".github" / "workflows" / "daily-brief.yml").read_text(
            encoding="utf-8")

    def test_the_channel_is_published_after_the_expert_audio(self):
        yml = self._daily()
        audio = yml.index("python expert_audio_brief.py")
        publish = yml.index("python channel_queue.py --publish")
        self.assertLess(audio, publish,
                        "전문가 오디오가 그날 배치의 마지막 재료다")

    def test_the_channel_is_published_before_the_cloudflare_deploy(self):
        """배포가 느린 날 구독자 도착이 같이 밀리면 안 된다."""
        yml = self._daily()
        self.assertLess(yml.index("python channel_queue.py --publish"),
                        yml.index("wrangler@4 pages deploy"))

    def test_the_publish_step_cannot_reach_the_dm_chat(self):
        yml = self._daily()
        step = yml.split("- name: Publish to subscriber channel", 1)[1]
        env = step.split("run:", 1)[0]
        self.assertIn("TELEGRAM_CHANNEL_ID", env)
        self.assertNotIn("TELEGRAM_CHAT_ID", env)

    def test_the_deploy_step_no_longer_carries_telegram_secrets(self):
        """wrangler·npx 가 도는 긴 프로세스에 발송 시크릿을 넘기지 않는다."""
        yml = self._daily()
        deploy = yml.split("- name: Deploy web to Cloudflare Pages", 1)[1]
        env = deploy.split("run: |", 1)[0]
        self.assertNotIn("TELEGRAM_", env)

    def test_the_batch_text_is_committed_before_any_reset(self):
        """claim push 가 본문을 못 박지 않으면 뒤 스텝의 reset --hard 가 지운다."""
        yml = self._daily()
        claim = yml.split("- name: Push claim", 1)[1].split("- name:", 1)[0]
        self.assertIn("channel_outbox.json", claim)

    def test_the_delivery_state_is_committed_at_the_end(self):
        """상태를 안 남기면 같은 날 재실행이 이미 나간 자료를 다시 올린다."""
        yml = self._daily()
        tail = yml.split("- name: Commit issue review cache", 1)[1]
        self.assertIn("channel_outbox.json", tail)

    def test_the_weekly_workflow_has_the_channel_and_commits_its_state(self):
        yml = (self.ROOT / ".github" / "workflows" / "weekly.yml").read_text(
            encoding="utf-8")
        self.assertIn("TELEGRAM_CHANNEL_ID", yml)
        self.assertIn("channel_outbox.json", yml)


if __name__ == "__main__":
    unittest.main()
