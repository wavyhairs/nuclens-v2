"""오디오 전달 계약 — 업로드 상한·재시도·상태 모델.

2026-09-10 실사고가 이 파일의 이유다. 전문가 브리핑 774초·12.1MB 가 정상으로
만들어졌는데 텔레그램 업로드가 `ConnectionError('Connection aborted.',
TimeoutError('The write operation timed out'))` 로 죽었고, 같은 회차의 빠른
브리핑 1.5MB 는 멀쩡히 나갔다. 그런데 워크플로 출력은 `expert=success` 였다 —
`generate()` 가 mp3 를 만들었다는 사실만으로 성공을 알렸기 때문이다. 구독
채널에는 보고서추천·국내·해외·빠른 4건만 공개되고 전문가 오디오가 통째로 빠졌다.

여기서 지키는 계약 네 가지:
  ① 업로드 상한은 **파일 크기를 따라간다** (연결 상한과 갈라서).
  ② 일시적 장애(write/read timeout·ConnectionError·429·5xx)만 재시도한다.
     재시도는 이미 만든 mp3 를 다시 올릴 뿐 TTS 를 부르지 않는다.
  ③ 영구 오류(429 아닌 4xx)는 다시 걸지 않는다.
  ④ '만들었다'와 '닿았다'는 다른 칸이다 — 전달 실패가 success 로 뭉개지지 않는다.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import audio_brief
import channel_queue


class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


def ok_payload(file_id="tg-file-id", message_id=7):
    return {"ok": True, "result": {"message_id": message_id,
                                   "audio": {"file_id": file_id}}}


class FakeRequests:
    """`import requests` 를 가로채는 최소 shim.

    예외 클래스는 진짜 requests 것을 그대로 쓴다 — 코드가 잡는 타입과 테스트가
    던지는 타입이 갈리면 이 스위트는 아무것도 증명하지 않는다.
    """
    Timeout = requests.Timeout
    ConnectionError = requests.ConnectionError
    exceptions = requests.exceptions

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append({"url": url, "data": dict(data or {}),
                           "size": len(files["audio"][1]) if files else 0,
                           "timeout": timeout})
        step = self.script.pop(0) if self.script else ok_payload()
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, FakeResponse):
            return step
        return FakeResponse(200, step)


class UploadPolicyTests(unittest.TestCase):
    """send_telegram_audio 단독 — 망 상황별로 몇 번 걸고 얼마나 기다리나."""

    def setUp(self):
        self.tmp = self.enterContext(
            __import__("tempfile").TemporaryDirectory())
        self.mp3 = Path(self.tmp) / "briefing-expert-2026-09-10.mp3"
        self.mp3.write_bytes(b"\x00" * 1024)
        self._resolve = audio_brief.gemini_client._resolve
        audio_brief.gemini_client._resolve = lambda key, default=None: "x"
        self.addCleanup(setattr, audio_brief.gemini_client, "_resolve", self._resolve)
        self.slept = []
        patcher = mock.patch.object(audio_brief.time, "sleep", self.slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_send(self, script, size=1024):
        self.mp3.write_bytes(b"\x00" * size)
        fake = FakeRequests(script)
        with mock.patch.dict(sys.modules, {"requests": fake}):
            result = audio_brief.send_telegram_audio(
                self.mp3, {"date": "2026-09-10", "key": "expert",
                           "label": "전문가 브리핑", "duration_sec": 774})
        return result, fake

    # ── ① 크기에 따른 상한 ──────────────────────────────────────

    def test_a_tiny_mp3_still_gets_the_floor(self):
        """가산이 0 에 가까워도 예전 상한 아래로는 안 내려간다."""
        _, fake = self.run_send([ok_payload()], size=1024)
        connect, upload = fake.calls[0]["timeout"]
        self.assertEqual(audio_brief.TELEGRAM_CONNECT_TIMEOUT, connect)
        self.assertEqual(audio_brief.TELEGRAM_UPLOAD_MIN_SEC, upload)

    def test_a_small_mp3_keeps_at_least_the_old_window(self):
        """1.5MB 는 예전 상한(120초) 안에서 실제로 끝났다 — 좁히지 않는다."""
        _, fake = self.run_send([ok_payload()], size=int(1.5 * 1024 * 1024))
        upload = fake.calls[0]["timeout"][1]
        self.assertGreaterEqual(upload, audio_brief.TELEGRAM_UPLOAD_MIN_SEC)
        self.assertLess(upload, audio_brief.TELEGRAM_UPLOAD_MAX_SEC)

    def test_a_large_mp3_gets_a_longer_upload_window(self):
        """12.1MB 가 120초를 넘겨 죽은 날이 이 값의 근거다."""
        _, fake = self.run_send([ok_payload()], size=int(12.1 * 1024 * 1024))
        connect, upload = fake.calls[0]["timeout"]
        self.assertEqual(audio_brief.TELEGRAM_CONNECT_TIMEOUT, connect)
        self.assertGreater(upload, 120)
        self.assertLessEqual(upload, audio_brief.TELEGRAM_UPLOAD_MAX_SEC)

    def test_connect_and_upload_limits_are_separate(self):
        """스칼라 하나면 연결이 안 되는 날에도 업로드 상한만큼 매달린다."""
        connect, upload = audio_brief.upload_timeout(20 * 1024 * 1024)
        self.assertNotEqual(connect, upload)
        self.assertEqual(audio_brief.TELEGRAM_UPLOAD_MAX_SEC, upload)

    def test_the_upload_window_never_grows_without_bound(self):
        connect, upload = audio_brief.upload_timeout(400 * 1024 * 1024)
        self.assertEqual(audio_brief.TELEGRAM_UPLOAD_MAX_SEC, upload)

    # ── ② 일시적 장애는 재시도한다 ──────────────────────────────

    def test_a_write_timeout_is_retried(self):
        """2026-09-10 이 죽은 정확한 자리."""
        boom = requests.ConnectionError(
            "('Connection aborted.', TimeoutError('The write operation timed out'))")
        result, fake = self.run_send([boom, ok_payload()])
        self.assertEqual("tg-file-id", result["file_id"])
        self.assertEqual(2, result["attempts"])
        self.assertEqual(2, len(fake.calls))

    def test_a_read_timeout_is_retried(self):
        result, fake = self.run_send([requests.Timeout("read timed out"), ok_payload()])
        self.assertTrue(result)
        self.assertEqual(2, len(fake.calls))

    def test_a_server_error_is_retried(self):
        result, fake = self.run_send(
            [FakeResponse(502, {"ok": False, "description": "Bad Gateway"}),
             ok_payload()])
        self.assertTrue(result)
        self.assertEqual(2, len(fake.calls))

    def test_a_rate_limit_is_retried_and_honours_retry_after(self):
        result, fake = self.run_send(
            [FakeResponse(429, {"ok": False, "parameters": {"retry_after": 11}}),
             ok_payload()])
        self.assertTrue(result)
        self.assertEqual([11.0], self.slept)

    def test_an_absurd_retry_after_is_capped(self):
        """텔레그램이 한 시간을 기다리라고 해도 이 스텝은 그럴 예산이 없다."""
        self.run_send(
            [FakeResponse(429, {"ok": False, "parameters": {"retry_after": 9000}}),
             ok_payload()])
        self.assertEqual([float(audio_brief.TELEGRAM_MAX_BACKOFF_SEC)], self.slept)

    def test_retries_back_off_instead_of_hammering(self):
        boom = requests.ConnectionError("aborted")
        self.run_send([boom, boom, ok_payload()])
        self.assertEqual(list(audio_brief.TELEGRAM_BACKOFF_SEC[:2]),
                         [int(value) for value in self.slept])

    def test_the_same_bytes_are_uploaded_on_every_attempt(self):
        """재시도는 **이미 만든 mp3** 를 다시 올릴 뿐이다 — 새 파일을 만들지 않는다."""
        boom = requests.ConnectionError("aborted")
        _, fake = self.run_send([boom, ok_payload()], size=4096)
        self.assertEqual([4096, 4096], [call["size"] for call in fake.calls])

    # ── ③ 영구 오류는 다시 걸지 않는다 ──────────────────────────

    def test_a_permanent_client_error_is_not_retried(self):
        result, fake = self.run_send(
            [FakeResponse(400, {"ok": False, "description": "chat not found"})])
        self.assertIsNone(result)
        self.assertEqual(1, len(fake.calls))
        self.assertEqual([], self.slept)

    def test_a_file_too_large_error_is_not_retried(self):
        result, fake = self.run_send(
            [FakeResponse(413, {"ok": False, "description": "Request Entity Too Large"})])
        self.assertIsNone(result)
        self.assertEqual(1, len(fake.calls))

    def test_every_attempt_failing_gives_up_with_a_reason(self):
        boom = requests.ConnectionError("aborted")
        result, fake = self.run_send([boom, boom, boom])
        self.assertIsNone(result)
        self.assertEqual(audio_brief.TELEGRAM_UPLOAD_ATTEMPTS, len(fake.calls))
        self.assertIn("ConnectionError", audio_brief.last_upload_error())

    def test_a_missing_config_is_not_an_upload_failure(self):
        audio_brief.gemini_client._resolve = lambda key, default=None: None
        result, fake = self.run_send([ok_payload()])
        self.assertIsNone(result)
        self.assertEqual([], fake.calls)
        self.assertEqual("unconfigured", audio_brief.last_upload_error())

    # ── ④ 총 대기 상한 ─────────────────────────────────────────

    def test_the_total_wait_is_bounded(self):
        """업로드가 워크플로 전체를 붙잡으면 채널 공개·배포가 그만큼 밀린다."""
        boom = requests.ConnectionError("aborted")
        clock = iter([0.0, 10_000.0, 10_000.0, 10_000.0])
        with mock.patch.object(audio_brief.time, "monotonic",
                               lambda: next(clock)):
            result, fake = self.run_send([boom, boom, boom])
        self.assertIsNone(result)
        self.assertEqual(1, len(fake.calls), "예산을 넘겼는데 또 걸었다")
        self.assertEqual([], self.slept)


class DeliveryStateTests(unittest.TestCase):
    """deliver() — DM 업로드 → file_id → 채널 배치 적재를 한 상태로 남긴다."""

    def setUp(self):
        tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        base = Path(tmp)
        self._dirs = (audio_brief.AUDIO_DIR, audio_brief.WEB_DATA)
        audio_brief.WEB_DATA = base / "data"
        audio_brief.AUDIO_DIR = base / "data" / "audio"
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        self.addCleanup(self._restore)
        self._queue = channel_queue.QUEUE_FILE
        channel_queue.QUEUE_FILE = base / "channel_outbox.json"
        self._send = audio_brief.send_telegram_audio
        self.mp3 = audio_brief.AUDIO_DIR / "briefing-expert-2026-09-10.mp3"
        self.mp3.write_bytes(b"mp3")
        self.meta = {"date": "2026-09-10", "key": "expert",
                     "label": "전문가 브리핑", "file": self.mp3.name,
                     "duration_sec": 774}

    def _restore(self):
        audio_brief.AUDIO_DIR, audio_brief.WEB_DATA = self._dirs
        channel_queue.QUEUE_FILE = self._queue
        audio_brief.send_telegram_audio = self._send
        audio_brief.LAST_DELIVERY = {}

    def deliver(self, result, *, error=""):
        audio_brief.send_telegram_audio = lambda path, meta: result
        audio_brief._LAST_UPLOAD.update({"error": error, "attempts": 3})
        return audio_brief.deliver("2026-09-10", "expert", self.meta, self.mp3)

    def variant(self):
        manifest = json.loads(
            (audio_brief.AUDIO_DIR / "audio.json").read_text(encoding="utf-8"))
        return manifest["variants"]["expert"]

    def queue_names(self):
        if not channel_queue.QUEUE_FILE.exists():
            return []
        queue = json.loads(channel_queue.QUEUE_FILE.read_text(encoding="utf-8"))
        return [item.get("name") for batch in queue.get("batches", [])
                for item in batch.get("items", [])]

    def test_a_delivered_audio_records_file_id_and_queues_the_channel(self):
        record = self.deliver({"file_id": "tg-expert", "message_id": 2, "attempts": 1})
        self.assertEqual(audio_brief.DELIVERY_DELIVERED, record["state"])
        self.assertEqual("queued", record["channel"])
        variant = self.variant()
        self.assertEqual("tg-expert", variant["telegram_file_id"])
        self.assertIn("telegram_sent_at", variant)
        self.assertEqual(["전문가 브리핑"], self.queue_names())
        self.assertFalse(audio_brief.delivery_failed())

    def test_a_failed_upload_is_recorded_and_never_marked_sent(self):
        """`telegram_sent_at` 이 비어 있어야 다음 실행이 발송만 이어받는다."""
        record = self.deliver(None, error="ConnectionError: write timed out")
        self.assertEqual(audio_brief.DELIVERY_TELEGRAM_FAILED, record["state"])
        self.assertIn("ConnectionError", record["error"])
        self.assertEqual(3, record["attempts"])
        variant = self.variant()
        self.assertNotIn("telegram_sent_at", variant)
        self.assertNotIn("telegram_file_id", variant)
        self.assertEqual([], self.queue_names())
        self.assertTrue(audio_brief.delivery_failed())

    def test_the_failure_survives_in_the_manifest(self):
        """캐시만 보고도 '아직 안 보냈다'와 '보내려다 실패했다'가 갈려야 한다."""
        self.deliver(None, error="Timeout: read timed out")
        self.assertEqual(audio_brief.DELIVERY_TELEGRAM_FAILED,
                         self.variant()["delivery"]["state"])

    def test_an_upload_without_a_file_id_cannot_reach_the_channel(self):
        """DM 은 나갔지만 구독 채널에는 못 실린다 — 성공으로 뭉개면 안 된다."""
        record = self.deliver({"file_id": "", "message_id": 3, "attempts": 1})
        self.assertEqual(audio_brief.DELIVERY_NO_FILE_ID, record["state"])
        self.assertEqual([], self.queue_names())
        self.assertTrue(audio_brief.delivery_failed())

    def test_a_missing_config_is_not_counted_as_a_delivery_failure(self):
        record = self.deliver(None, error="unconfigured")
        self.assertEqual(audio_brief.DELIVERY_UNCONFIGURED, record["state"])
        self.assertFalse(audio_brief.delivery_failed())

    def test_no_send_is_not_a_delivery_failure(self):
        record = audio_brief.deliver("2026-09-10", "expert", self.meta,
                                     self.mp3, send=False)
        self.assertEqual(audio_brief.DELIVERY_SKIPPED, record["state"])
        self.assertFalse(audio_brief.delivery_failed())

    def test_a_channel_queue_failure_is_its_own_state(self):
        def boom(*args, **kwargs):
            raise RuntimeError("큐 파일 잠김")
        with mock.patch.object(channel_queue, "record_audio", boom):
            record = self.deliver({"file_id": "tg-expert", "attempts": 1})
        self.assertEqual(audio_brief.DELIVERY_QUEUE_FAILED, record["state"])
        # DM 은 실제로 나갔다 — 그 사실까지 되돌리면 다음 실행이 중복 발송한다.
        self.assertIn("telegram_sent_at", self.variant())
        self.assertTrue(audio_brief.delivery_failed())


if __name__ == "__main__":
    unittest.main()
