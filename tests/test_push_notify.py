"""아침 알림 — 무엇을 보내고, 언제 보내지 않는가.

발송은 이제 파이썬이 한다(`tools/push_notify.py` + pywebpush). 예전엔 엣지가
보냈고 이 스크립트는 "지금이 아침인가"만 판단했다 — 그래서 옛 검사는 시각 창과
날짜만 봤다. 지금 잠글 것은 셋이다.

1. **알림에 실리는 말.** 화면이 고른 제목을 그대로 쓴다. 알림이 화면과 다른
   말을 하면 누른 뒤에 배신당한 기분이 든다.
2. **키 모양.** pywebpush 는 문자열을 '파일 경로 아니면 base64url raw 키'로만
   본다. PEM 본문을 그대로 넘기면 `Could not deserialize key data` 로 죽는데,
   증상은 "알림이 안 온다" 하나뿐이다(2026-09-19 실측).
3. **끝에서 끝까지.** 진짜 VAPID 키와 진짜 구독 키로 암호화해 보내고, 받은
   쪽에서 **풀어서** 제목·본문·주소·tag 가 그대로인지 본다. 서비스워커는
   `event.data.json()` 한 줄이라, 여기까지 맞으면 화면까지 맞는다.

푸시는 틀려도 조용하다 — 발송 로그에 실패 숫자 하나가 늘 뿐이다. 그래서 검사가
사람 대신 봐야 한다.
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import push_notify  # noqa: E402

try:  # 로컬에는 없을 수 있다. CI 는 requirements.txt 를 깐다.
    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid
    HAVE_WEBPUSH = True
except ImportError:  # pragma: no cover
    HAVE_WEBPUSH = False

needs_webpush = unittest.skipUnless(HAVE_WEBPUSH, "pywebpush 계열 미설치")


def briefing(date: str, *titles: str, extra: int = 0) -> dict:
    issues = [{"title": title} for title in titles]
    issues += [{"title": f"기타 {n}"} for n in range(extra)]
    return {"date": date, "issues": issues}


class PayloadTests(unittest.TestCase):
    def test_the_title_counts_the_day_and_the_issues(self):
        payload = push_notify.build_payload([briefing("2026-09-19", "가", "나", "다", extra=16)])
        self.assertEqual(payload["title"], "Nuclens 09/19 브리핑 · 19건")
        self.assertEqual(payload["url"], "/?src=push")
        self.assertEqual(payload["tag"], "nuclens-brief-2026-09-19")

    def test_the_body_is_the_top_three_as_written(self):
        """여기서 문장을 새로 짓지 않는다 — 화면이 정한 순서와 제목 그대로."""
        payload = push_notify.build_payload(
            [briefing("2026-09-19", "원안위 하나로 재가동 승인", "테라파워 협력", "국정감사 대응", "넷째")])
        self.assertEqual(payload["body"],
                         "1. 원안위 하나로 재가동 승인\n2. 테라파워 협력\n3. 국정감사 대응")
        self.assertNotIn("넷째", payload["body"], "상위 3건만 실린다")

    def test_a_long_title_is_cut_not_dropped(self):
        payload = push_notify.build_payload([briefing("2026-09-19", "가" * 120)])
        self.assertEqual(payload["body"], "1. " + "가" * push_notify.TITLE_CHARS)

    def test_the_newest_day_wins_whichever_way_the_file_is_sorted(self):
        """briefings.json 은 최신이 먼저다(V2). policy174 는 반대다. 어느 쪽이
        와도 같은 날을 골라야 한다 — 데이터 순서에 기대면 조용히 어제 것이 간다."""
        newest_first = [briefing("2026-09-19", "오늘"), briefing("2026-09-18", "어제")]
        oldest_first = list(reversed(newest_first))
        for briefings in (newest_first, oldest_first):
            self.assertEqual(push_notify.build_payload(briefings)["body"], "1. 오늘")

    def test_a_day_without_issues_is_not_the_day(self):
        """빌드가 반쯤 실패해 issues 가 빈 회차가 들어오면 그 날을 고르지
        않는다 — 제목 없는 알림이 나가느니 어제 것이 낫다."""
        payload = push_notify.build_payload(
            [{"date": "2026-09-20", "issues": []}, briefing("2026-09-19", "오늘")])
        self.assertEqual(payload["tag"], "nuclens-brief-2026-09-19")

    def test_it_never_raises_on_broken_data(self):
        """이 한 줄 때문에 스텝이 죽으면 안 된다 — 알림은 부가 기능이다."""
        for briefings in ([], [{}], [{"date": None}], [{"date": "x", "issues": [{}]}]):
            payload = push_notify.build_payload(briefings)
            self.assertTrue(payload["title"])
            self.assertTrue(payload["body"])
            self.assertEqual(payload["url"], "/?src=push")

    def test_the_tag_carries_the_date_back(self):
        """중복 판단의 유일한 근거다. tag 모양이 바뀌면 재실행이 알림을 한 번 더 보낸다."""
        payload = push_notify.build_payload([briefing("2026-09-19", "가")])
        self.assertEqual(push_notify.payload_date(payload), "2026-09-19")
        self.assertEqual(push_notify.payload_date({"tag": "nuclens-brief"}), "")


class DuplicateTests(unittest.TestCase):
    """같은 Daily Brief 를 다시 돌리면 알림이 두 번 가는가.

    예전엔 전용 cron 이 따로 돌아 이 질문이 없었다(하루 한 번 예약). 브리핑에
    붙인 지금은 재실행이 곧 재발송이라, outbox 에 한 칸을 빌려 막는다 —
    새 상태 시스템을 만들지 않는다.
    """

    def test_a_rerun_of_the_same_day_does_not_send_again(self):
        outbox = {"date": "2026-09-19", "push": {"date": "2026-09-19", "sent": 3}}
        self.assertTrue(push_notify.already_notified(outbox, "2026-09-19"))

    def test_a_new_day_sends(self):
        outbox = {"push": {"date": "2026-09-18", "sent": 3}}
        self.assertFalse(push_notify.already_notified(outbox, "2026-09-19"))

    def test_an_outbox_without_the_mark_sends(self):
        for outbox in ({}, {"push": None}, {"push": {}}, {"push": "어제"}):
            self.assertFalse(push_notify.already_notified(outbox, "2026-09-19"))

    def test_an_unknown_briefing_date_never_counts_as_sent(self):
        """날짜를 모르는 채 '이미 보냈다'고 판정하면 알림이 영영 안 간다."""
        self.assertFalse(push_notify.already_notified({"push": {"date": ""}}, ""))

    def test_the_mark_survives_as_a_field_of_the_existing_outbox(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.json"
            path.write_text(json.dumps({"date": "2026-09-19", "briefs": [{"name": "국내"}]}),
                            encoding="utf-8")
            push_notify.record_notified(path, "2026-09-19", sent=2, total=3)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["push"]["date"], "2026-09-19")
            self.assertEqual(saved["push"]["sent"], 2)
            self.assertEqual(saved["briefs"], [{"name": "국내"}], "남의 칸을 건드리면 안 된다")
            self.assertTrue(push_notify.already_notified(saved, "2026-09-19"))

    def test_a_missing_outbox_is_not_an_error(self):
        with TemporaryDirectory() as tmp:
            push_notify.record_notified(Path(tmp) / "nope.json", "2026-09-19", sent=1, total=1)


@needs_webpush
class KeyFormatTests(unittest.TestCase):
    """시크릿에 무엇이 들어 있든 발송이 서야 한다.

    이 저장소가 `web/tools/gen_vapid_keys.mjs` 로 낸 키는 base64url 32바이트고,
    그 값이 **이미 Cloudflare 에 들어가 있다** — 그대로 GitHub Secret 으로
    옮겨야 기존 구독이 산다. PEM 을 쓰는 운영자도 있으므로 둘 다 받는다.
    """

    def setUp(self):
        self.private = ec.generate_private_key(ec.SECP256R1())

    def test_a_base64url_scalar_passes_straight_through(self):
        raw = self.private.private_numbers().private_value.to_bytes(32, "big")
        value = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.assertEqual(push_notify.load_vapid_key(value), value)

    def test_a_pem_secret_is_converted_not_handed_over_raw(self):
        """PEM 문자열을 그대로 넘기면 'Could not deserialize key data' 다."""
        pem = self.private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        self.assertIsInstance(push_notify.load_vapid_key(pem), Vapid)
        self.assertIsInstance(push_notify.load_vapid_key("  " + pem + "\n"), Vapid)


# ── 끝에서 끝까지 ───────────────────────────────────────────────────────────
# 진짜 키로 암호화해 진짜 소켓으로 보낸다. 흉내로는 안 잡히는 것들이 여기서만
# 드러난다: 키 모양, 암호화 협상, 404/410 정리, 인증 실패의 종료 코드.

class FakeSite(BaseHTTPRequestHandler):
    """사이트(/push/list · /push/subscribe)와 푸시 서비스(/ep/N)를 한 서버로."""

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        state = self.server.state
        if self.path != "/push/list":
            return self._json(404, {"error": "no"})
        if self.headers.get("Authorization") != f"Bearer {state['token']}":
            return self._json(state["list_status"] if state["list_status"] != 200 else 401,
                              {"error": "unauthorized"})
        if state["list_status"] != 200:
            return self._json(state["list_status"], {"error": "unconfigured"})
        return self._json(200, {"count": len(state["subscriptions"]),
                                "subscriptions": state["subscriptions"]})

    def do_DELETE(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.state["pruned"].append(body.get("endpoint"))
        return self._json(200, {"ok": True})

    def do_POST(self):
        state = self.server.state
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        state["delivered"].append({"path": self.path, "body": body,
                                   "ttl": self.headers.get("TTL"),
                                   "auth": self.headers.get("Authorization") or "",
                                   "encoding": self.headers.get("Content-Encoding")})
        status = state["endpoint_status"].get(self.path, 201)
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@needs_webpush
class EndToEndTests(unittest.TestCase):
    TOKEN = "t" * 32

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.briefings = root / "briefings.json"
        self.outbox = root / "outbox.json"
        self.briefings.write_text(json.dumps(
            [briefing("2026-09-19", "원안위 하나로 재가동 승인", "테라파워 협력", "국정감사 대응",
                      extra=16)], ensure_ascii=False), encoding="utf-8")
        self.outbox.write_text(json.dumps({"date": "2026-09-19", "status": "sent"}),
                               encoding="utf-8")

        vapid = ec.generate_private_key(ec.SECP256R1())
        self.vapid_b64 = base64.urlsafe_b64encode(
            vapid.private_numbers().private_value.to_bytes(32, "big")).decode().rstrip("=")

        self.server = HTTPServer(("127.0.0.1", 0), FakeSite)
        self.server.state = {"token": self.TOKEN, "list_status": 200, "subscriptions": [],
                             "endpoint_status": {}, "delivered": [], "pruned": []}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.site = f"http://127.0.0.1:{self.server.server_address[1]}"

        patcher = patch.multiple(push_notify, BRIEFINGS_FILE=self.briefings,
                                 OUTBOX_FILE=self.outbox)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_subscriber(self, name: str):
        """브라우저가 만드는 구독 한 벌 — 키까지 진짜다."""
        private = ec.generate_private_key(ec.SECP256R1())
        p256dh = base64.urlsafe_b64encode(private.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)).decode().rstrip("=")
        auth_raw = name.encode("utf-8").ljust(16, b"0")[:16]
        endpoint = f"{self.site}/ep/{name}"
        self.server.state["subscriptions"].append({
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh,
                     "auth": base64.urlsafe_b64encode(auth_raw).decode().rstrip("=")},
        })
        return endpoint, private, auth_raw

    def run_main(self, *argv, **env):
        environ = {"SITE_URL": self.site, "PUSH_ADMIN_TOKEN": self.TOKEN,
                   "VAPID_PRIVATE_KEY": self.vapid_b64,
                   "VAPID_SUBJECT": "mailto:ops@example.test"}
        environ.update(env)
        with patch.dict("os.environ", environ, clear=False):
            return push_notify.main(list(argv))

    def test_the_phone_can_read_what_the_briefing_said(self):
        """서비스워커는 `event.data.json()` 한 줄이다. 여기서 풀리는 것이 곧
        화면에 뜨는 것이다 — 예전처럼 빈 알림을 받아 다시 조회하지 않는다."""
        _, private, auth_raw = self.add_subscriber("a")
        self.assertEqual(self.run_main(), 0)

        delivered = self.server.state["delivered"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["encoding"], "aes128gcm", "본문이 암호화되지 않았다")
        self.assertTrue(delivered[0]["auth"].startswith("vapid t="), "VAPID 서명이 없다")
        self.assertEqual(delivered[0]["ttl"], str(push_notify.PUSH_TTL))

        opened = json.loads(http_ece.decrypt(
            delivered[0]["body"], private_key=private, auth_secret=auth_raw,
            version="aes128gcm").decode("utf-8"))
        self.assertEqual(opened["title"], "Nuclens 09/19 브리핑 · 19건")
        self.assertEqual(opened["body"],
                         "1. 원안위 하나로 재가동 승인\n2. 테라파워 협력\n3. 국정감사 대응")
        self.assertEqual(opened["url"], "/?src=push")
        self.assertEqual(opened["tag"], "nuclens-brief-2026-09-19")

    def test_a_dead_subscription_is_swept_and_the_rest_still_go(self):
        """404·410 은 '이 구독은 이제 없다'는 뜻이다. 남겨 두면 매일 같은
        실패를 다시 사고, 목록이 실제 독자 수를 말하지 않게 된다."""
        alive, _, _ = self.add_subscriber("alive")
        gone, _, _ = self.add_subscriber("gone")
        missing, _, _ = self.add_subscriber("missing")
        self.server.state["endpoint_status"] = {"/ep/gone": 410, "/ep/missing": 404}

        self.assertEqual(self.run_main(), 0, "죽은 구독은 실패가 아니라 정리다")
        self.assertEqual(sorted(self.server.state["pruned"]), sorted([gone, missing]))
        self.assertNotIn(alive, self.server.state["pruned"])

    def test_every_subscriber_failing_is_an_operational_error(self):
        """폰 하나가 꺼져 있던 날과, 키가 어긋난 날은 다른 사건이다."""
        self.add_subscriber("a")
        self.add_subscriber("b")
        self.server.state["endpoint_status"] = {"/ep/a": 500, "/ep/b": 500}
        self.assertEqual(self.run_main(), 1)
        self.assertEqual(self.server.state["pruned"], [], "500 은 정리 대상이 아니다")

    def test_a_partial_failure_is_not_a_red_light(self):
        self.add_subscriber("a")
        self.add_subscriber("b")
        self.server.state["endpoint_status"] = {"/ep/b": 500}
        self.assertEqual(self.run_main(), 0)

    def test_nobody_subscribed_is_normal(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.server.state["delivered"], [])

    def test_a_wrong_token_is_loud(self):
        """토큰이 어긋난 배포는 '구독자 0명'과 로그에서 구분이 안 되면
        그 상태로 몇 달이 간다."""
        self.add_subscriber("a")
        self.assertEqual(self.run_main(PUSH_ADMIN_TOKEN="wrong"), 1)
        self.assertEqual(self.server.state["delivered"], [])

    def test_an_unconfigured_site_is_loud(self):
        self.add_subscriber("a")
        self.server.state["list_status"] = 503
        self.assertEqual(self.run_main(), 1)

    def test_missing_secrets_are_loud(self):
        """설정을 깜빡한 것이 조용히 '알림 없는 서비스'가 되면 안 된다."""
        self.add_subscriber("a")
        self.assertEqual(self.run_main(PUSH_ADMIN_TOKEN=""), 1)
        self.assertEqual(self.run_main(VAPID_PRIVATE_KEY=""), 1)
        self.assertEqual(self.server.state["delivered"], [])

    def test_a_broken_key_never_reaches_the_subscribers(self):
        self.add_subscriber("a")
        self.assertEqual(self.run_main(VAPID_PRIVATE_KEY="-----BEGIN EC PRIVATE KEY-----\n쓰레기\n"), 1)
        self.assertEqual(self.server.state["delivered"], [], "키가 깨졌는데 목록을 먼저 불렀다")

    def test_the_second_run_of_the_day_sends_nothing(self):
        self.add_subscriber("a")
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(len(self.server.state["delivered"]), 1)
        self.assertEqual(self.run_main(), 0, "재실행은 조용히 끝나야 한다")
        self.assertEqual(len(self.server.state["delivered"]), 1, "같은 날 알림이 두 번 갔다")

    def test_force_gets_through_the_mark(self):
        """복구·점검용. 운영자가 직접 부를 때만 쓴다."""
        self.add_subscriber("a")
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.run_main("--force"), 0)
        self.assertEqual(len(self.server.state["delivered"]), 2)

    def test_a_failed_send_does_not_claim_the_day(self):
        """전원 실패한 날에 표식이 남으면, 고치고 다시 돌려도 안 나간다."""
        self.add_subscriber("a")
        self.server.state["endpoint_status"] = {"/ep/a": 500}
        self.assertEqual(self.run_main(), 1)
        saved = json.loads(self.outbox.read_text(encoding="utf-8"))
        self.assertNotIn("push", saved)

    def test_dry_run_touches_nothing(self):
        self.add_subscriber("a")
        self.assertEqual(self.run_main("--dry-run"), 0)
        self.assertEqual(self.server.state["delivered"], [])
        self.assertNotIn("push", json.loads(self.outbox.read_text(encoding="utf-8")))


class WorkflowWiringTests(unittest.TestCase):
    """알림은 **브리핑에 딸린 일**이다 — 따로 예약하지 않는다.

    YAML 파서를 안 쓴다. requirements.txt 는 런타임에 필요한 것만 담고,
    검사 하나를 위해 PyYAML 을 거기 얹지 않는다(test_cards_workflow.py 와 같은 이유).
    """

    @classmethod
    def setUpClass(cls):
        cls.workflows = ROOT / ".github" / "workflows"
        cls.brief = (cls.workflows / "daily-brief.yml").read_text(encoding="utf-8")

    def test_the_dedicated_push_cron_is_gone(self):
        """07:00 전용 예약은 "지금이 아침인가"를 발송기가 되묻게 만들었다.
        그 질문의 답은 브리핑이 나갔는지 뿐이고, 그건 브리핑만이 안다."""
        self.assertFalse((self.workflows / "push-notify.yml").exists())
        for path in self.workflows.glob("*.yml"):
            # 실행하는 곳만 본다 — 주석의 언급까지 세면 검사가 문장에 걸린다.
            if "python tools/push_notify.py" in path.read_text(encoding="utf-8"):
                self.assertEqual(path.name, "daily-brief.yml",
                                 f"{path.name} 이 알림을 따로 보낸다")

    def test_the_push_step_hangs_off_a_verified_deploy(self):
        step = self.brief.split("- name: Web push", 1)[1].split("- name:", 1)[0]
        self.assertIn("python tools/push_notify.py", step)
        # 배포 성공은 claim 성공을 이미 포함한다(web-deploy 자신의 if). 브리핑이
        # 안 나간 날에는 알림도 안 가고, /?src=push 가 오늘 것을 보여 준다.
        self.assertIn("steps.web-deploy.outcome == 'success'", step)

    def test_a_failed_notification_never_takes_the_briefing_down(self):
        step = self.brief.split("- name: Web push", 1)[1].split("- name:", 1)[0]
        self.assertIn("continue-on-error: true", step)

    def test_the_step_carries_exactly_what_the_sender_reads(self):
        step = self.brief.split("- name: Web push", 1)[1].split("- name:", 1)[0]
        self.assertIn("PUSH_ADMIN_TOKEN: ${{ secrets.PUSH_ADMIN_TOKEN }}", step)
        self.assertIn("VAPID_PRIVATE_KEY: ${{ secrets.VAPID_PRIVATE_KEY }}", step)
        self.assertIn("VAPID_SUBJECT:", step)
        # SITE_URL 은 워크플로 전역 env 다 — 스텝이 따로 들고 있으면 둘이 갈린다.
        self.assertIn("SITE_URL:", self.brief.split("on:", 1)[0])

    def test_the_mark_is_committed_after_the_notification(self):
        """outbox 의 push 칸을 커밋하는 스텝이 발송 **뒤**에 있어야 한다.
        앞에 있으면 표식이 러너와 함께 사라지고, 재실행이 알림을 또 보낸다."""
        send_at = self.brief.index("- name: Web push")
        commit_at = self.brief.index("- name: Commit issue review cache")
        self.assertLess(send_at, commit_at)
        commit = self.brief[commit_at:].split("- name:", 2)[1]
        self.assertIn("git add outbox.json", commit)

    def test_the_sender_has_its_dependency(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pywebpush", requirements)
        # 파이썬 검사 워크플로가 requirements 를 깔기 때문에 CI 에서 실제로 돈다.
        tests_workflow = (self.workflows / "python-tests.yml").read_text(encoding="utf-8")
        self.assertIn("pip install -r requirements.txt", tests_workflow)


class EdgeSurfaceTests(unittest.TestCase):
    """엣지에 남은 것과 사라진 것."""

    FUNCTIONS = ROOT / "functions" / "push"

    def test_the_edge_no_longer_signs_or_sends(self):
        """서명·발송이 두 곳에 있으면 어느 쪽이 보냈는지 로그로 못 가린다."""
        self.assertFalse((self.FUNCTIONS / "send.js").exists())
        shared = (self.FUNCTIONS / "_shared.js").read_text(encoding="utf-8")
        for gone in ("vapidToken", "importVapidKey", "VAPID_PRIVATE_KEY"):
            self.assertNotIn(gone, shared, f"{gone} 이 엣지에 남아 있다")

    def test_the_list_window_exists_and_is_the_only_authenticated_one(self):
        listing = (self.FUNCTIONS / "list.js").read_text(encoding="utf-8")
        self.assertIn("PUSH_ADMIN_TOKEN", listing)
        self.assertIn("401", listing)
        self.assertIn("503", listing)
        # 구독 창구는 독자가 쓰는 길이라 토큰을 못 건다.
        subscribe = (self.FUNCTIONS / "subscribe.js").read_text(encoding="utf-8")
        self.assertNotIn("PUSH_ADMIN_TOKEN", subscribe)

    def test_the_old_send_token_is_gone_from_the_repo(self):
        """PUSH_SEND_TOKEN 은 /push/send 만의 열쇠였다. 남겨 두면 운영자가
        둘 중 어느 것을 넣어야 하는지 알 수 없다."""
        for path in (*self.FUNCTIONS.glob("*.js"),
                     ROOT / "tools" / "push_notify.py",
                     ROOT / ".github" / "workflows" / "daily-brief.yml"):
            self.assertNotIn("PUSH_SEND_TOKEN", path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    unittest.main()
