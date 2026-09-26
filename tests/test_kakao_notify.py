"""카카오톡 아침 브리핑 — 무엇을 보내고, 언제 보내지 않고, 토큰을 어떻게 잇는가.

카카오 '나에게 보내기'는 틀려도 조용하다. 200자를 넘기면 말풍선이 '…'로 잘릴
뿐이고, 리프레시 토큰을 못 이으면 두 달 뒤 어느 아침부터 그냥 안 온다. 그래서
검사가 사람 대신 본다.

1. **말풍선 모양.** 텔레그램 카드 번호 순서 그대로, 한 말풍선 200자(보수적으로
   UTF-16 단위) 안쪽. 상한을 넘으면 '외 N건'으로 **몇 건이었는지는** 지킨다.
2. **보내지 않는 경우.** 미설정은 조용히, 반쪽 설정은 오류로. 재실행은 중복 없이.
3. **토큰.** 새 리프레시 토큰이 오면 Secret 을 갈아 끼우거나, 못 하면 경고하고
   카톡으로도 알린다. 어떤 경우에도 토큰 값은 로그에 찍지 않는다.

외부 호출은 0 — requests.post 와 gh 를 목으로 바꾼다.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import kakao_notify  # noqa: E402

REFRESH = "refresh-token-not-real-0001"
NEW_REFRESH = "refresh-token-not-real-0002"
ACCESS = "access-token-not-real"
WRITER = "writer-pat-not-real"


def item(region: str, rank: int, title: str) -> dict:
    return {"brief_region": region, "region": region, "brief_rank": rank,
            "title_kr": title, "hash": f"{region}-{rank}"}


def outbox(**extra) -> dict:
    base = {
        "date": "2026-09-26",
        "status": "sent",
        "briefs": [{"name": "국내", "text": "…", "status": "sent"},
                   {"name": "해외", "text": "…", "status": "sent"}],
        # 일부러 섞어 둔다 — 순서는 brief_rank 가 정한다.
        "items": [item("해외", 2, "니제르 우라늄 프로젝트 재개"),
                  item("국내", 2, "울진군, 신한울 3·4호기 상생 논의"),
                  item("해외", 1, "미국 팰리세이즈 원전 연료 재장전"),
                  item("국내", 1, "정부, 대미 전략투자 첫 사업 확정")],
    }
    base.update(extra)
    return base


class FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class MessageTests(unittest.TestCase):
    def test_titles_follow_the_telegram_numbering_per_region(self):
        messages = kakao_notify.build_messages(outbox())
        self.assertEqual(messages, [
            "🇰🇷 국내 브리핑 · 9/26 (2건)\n"
            "1. 정부, 대미 전략투자 첫 사업 확정\n"
            "2. 울진군, 신한울 3·4호기 상생 논의",
            "🌐 해외 브리핑 · 9/26 (2건)\n"
            "1. 미국 팰리세이즈 원전 연료 재장전\n"
            "2. 니제르 우라늄 프로젝트 재개",
        ])

    def test_every_bubble_fits_and_no_title_is_lost(self):
        titles = [f"{n}번째 원자력 정책 기사 제목이 상당히 길게 이어지는 경우를 가정" for n in range(1, 11)]
        box = outbox(items=[item("해외", n, t) for n, t in enumerate(titles, 1)])
        messages = kakao_notify.build_messages(box)
        self.assertGreater(len(messages), 1, "10건이 한 말풍선에 들어갈 리 없다")
        for text in messages:
            self.assertLessEqual(kakao_notify.text_units(text), kakao_notify.TEXT_LIMIT, text)
        joined = "\n".join(messages)
        for n, title in enumerate(titles, 1):
            self.assertIn(f"{n}. {title}", joined)
        self.assertTrue(messages[1].startswith("🌐 해외 브리핑 (이어서)\n"))

    def test_overflow_keeps_the_count_honest(self):
        titles = ["가" * kakao_notify.TITLE_CHARS for _ in range(30)]
        lines = [f"{n}. {t}" for n, t in enumerate(titles, 1)]
        messages = kakao_notify.pack_lines("머리", "이어서", lines)
        self.assertEqual(len(messages), kakao_notify.MAX_MESSAGES_PER_REGION)
        for text in messages:
            self.assertLessEqual(kakao_notify.text_units(text), kakao_notify.TEXT_LIMIT)
        tail = messages[-1].splitlines()[-1]
        self.assertRegex(tail, r"^… 외 \d+건은 웹에서$")
        shown = sum(1 for text in messages for line in text.splitlines()
                    if line[:1].isdigit())
        hidden = int(tail.split("외 ")[1].split("건")[0])
        self.assertEqual(shown + hidden, 30, "보인 것 + 외 N건 = 그날 전체")

    def test_long_titles_are_cut_with_an_ellipsis(self):
        box = outbox(items=[item("국내", 1, "나" * 200)])
        line = kakao_notify.build_messages(box)[0].splitlines()[1]
        self.assertEqual(line, "1. " + "나" * (kakao_notify.TITLE_CHARS - 1) + "…")

    def test_flags_are_counted_conservatively(self):
        # 국기 이모지는 UTF-16 으로 4 단위 — 짧게 세면 말풍선 끝이 잘린다.
        self.assertEqual(kakao_notify.text_units("🇰🇷"), 4)
        self.assertEqual(kakao_notify.text_units("가나"), 2)

    def test_empty_region_is_skipped(self):
        box = outbox(items=[item("해외", 1, "하나")])
        messages = kakao_notify.build_messages(box)
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("🌐 해외"))


class LinkAndSkipTests(unittest.TestCase):
    def test_button_opens_todays_page_only_after_deploy(self):
        site = "https://nuclens-v2.pages.dev"
        self.assertEqual(kakao_notify.brief_link(site, "2026-09-26", deployed=True),
                         f"{site}/brief/2026-09-26")
        self.assertEqual(kakao_notify.brief_link(site, "2026-09-26", deployed=False),
                         f"{site}/")
        self.assertEqual(kakao_notify.brief_link(site, "bogus", deployed=True), f"{site}/")

    def test_nothing_goes_out_for_blocked_or_empty_outboxes(self):
        self.assertTrue(kakao_notify.skip_reason({}))
        self.assertTrue(kakao_notify.skip_reason(outbox(status="empty")))
        self.assertTrue(kakao_notify.skip_reason(outbox(status="quality_rejected")))
        self.assertTrue(kakao_notify.skip_reason(outbox(quality_gate_error={"code": "x"})))
        self.assertEqual(kakao_notify.skip_reason(outbox()), "")
        # 텔레그램이 실패한 날(pending)에도 카톡은 간다 — 두 창구는 독립이다.
        self.assertEqual(kakao_notify.skip_reason(outbox(status="pending")), "")

    def test_extract_code_from_a_pasted_redirect(self):
        self.assertEqual(kakao_notify.extract_code(
            "http://localhost:8765/callback?code=abc123&state=x"), "abc123")
        self.assertEqual(kakao_notify.extract_code("  abc123 "), "abc123")
        self.assertEqual(kakao_notify.extract_code("http://localhost:8765/callback"), "")

    def test_authorize_url_asks_only_for_talk_message(self):
        url = kakao_notify.authorize_url("KEY", "http://localhost:8765/callback")
        self.assertIn("scope=talk_message", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback", url)


class SendFlowTests(unittest.TestCase):
    """main() 을 끝에서 끝까지. requests.post 는 URL 로 토큰/발송을 가른다."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outbox_path = Path(self.tmp.name) / "outbox.json"
        self.outbox_path.write_text(json.dumps(outbox(), ensure_ascii=False), encoding="utf-8")
        self.settings: dict[str, str] = {
            "KAKAO_REST_API_KEY": "rest-key", "KAKAO_REFRESH_TOKEN": REFRESH}
        self.token_body: dict = {"access_token": ACCESS, "expires_in": 21599}
        self.memo_status = (200, {"result_code": 0})
        self.calls: list[tuple[str, dict]] = []
        for target, value in (("OUTBOX_FILE", self.outbox_path), ("SEND_GAP_S", 0)):
            patcher = patch.object(kakao_notify, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(kakao_notify, "resolve_setting",
                               side_effect=lambda key: self.settings.get(key))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(kakao_notify.requests, "post", side_effect=self.fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict("os.environ", {"SITE_URL": "https://nuclens-v2.pages.dev",
                                            "WEB_DEPLOY_OUTCOME": "success",
                                            "GITHUB_REPOSITORY": "owner/repo"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, {"data": data, "headers": headers}))
        if url == kakao_notify.TOKEN_URL:
            if "access_token" in self.token_body:
                return FakeResponse(200, self.token_body)
            return FakeResponse(400, self.token_body)
        if url == kakao_notify.MEMO_URL:
            return FakeResponse(*self.memo_status)
        raise AssertionError(f"예상 밖 호출: {url}")

    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = kakao_notify.main(list(argv))
        return rc, out.getvalue()

    def memo_calls(self) -> list[dict]:
        return [json.loads(c["data"]["template_object"])
                for url, c in self.calls if url == kakao_notify.MEMO_URL]

    def saved(self) -> dict:
        return json.loads(self.outbox_path.read_text(encoding="utf-8"))

    def test_unconfigured_is_a_quiet_skip(self):
        self.settings.clear()
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])
        self.assertNotIn("::error::", out)

    def test_half_configured_is_an_error(self):
        del self.settings["KAKAO_REFRESH_TOKEN"]
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertEqual(self.calls, [])

    def test_sends_each_bubble_and_records_the_day(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        token_call = self.calls[0]
        self.assertEqual(token_call[0], kakao_notify.TOKEN_URL)
        self.assertEqual(token_call[1]["data"]["grant_type"], "refresh_token")
        templates = self.memo_calls()
        self.assertEqual([t["text"] for t in templates],
                         kakao_notify.build_messages(outbox()))
        for template in templates:
            self.assertEqual(template["object_type"], "text")
            self.assertEqual(template["link"]["web_url"],
                             "https://nuclens-v2.pages.dev/brief/2026-09-26")
            self.assertEqual(template["link"]["mobile_web_url"], template["link"]["web_url"])
        for _url, call in self.calls[1:]:
            self.assertEqual(call["headers"], {"Authorization": f"Bearer {ACCESS}"})
        record = self.saved()["kakao"]
        self.assertEqual(record["date"], "2026-09-26")
        self.assertEqual(record["messages"], 2)
        # 발송 기록 외의 outbox 는 그대로다 — 품질 digest 가 보는 칸을 건드리지 않는다.
        self.assertEqual(self.saved()["items"], outbox()["items"])

    def test_a_rerun_the_same_day_sends_nothing(self):
        self.run_main()
        self.calls.clear()
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [], "재실행이 카톡을 한 번 더 보냈다")
        self.assertIn("이미 나갔다", out)
        rc, _ = self.run_main("--force")
        self.assertEqual(len(self.memo_calls()), 2)

    def test_failed_deploy_points_the_button_at_the_front_page(self):
        with patch.dict("os.environ", {"WEB_DEPLOY_OUTCOME": "failure"}):
            self.run_main()
        self.assertTrue(all(t["link"]["web_url"] == "https://nuclens-v2.pages.dev/"
                            for t in self.memo_calls()))

    def test_dry_run_touches_no_network(self):
        self.settings.clear()
        rc, out = self.run_main("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])
        self.assertIn("🇰🇷 국내 브리핑", out)

    def test_expired_refresh_token_asks_for_reauth(self):
        self.token_body = {"error": "invalid_grant", "error_code": "KOE322",
                           "error_description": "authorization code not found"}
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertIn("--auth", out)
        self.assertEqual(self.memo_calls(), [])
        self.assertNotIn("kakao", self.saved())

    def test_missing_scope_says_which_consent(self):
        self.memo_status = (403, {"msg": "insufficient scopes.", "code": -402})
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("talk_message", out)
        self.assertNotIn("kakao", self.saved(), "하나도 못 보냈는데 보냈다고 적었다")

    def test_rotated_refresh_token_is_written_back_without_printing_it(self):
        self.token_body = {"access_token": ACCESS, "refresh_token": NEW_REFRESH,
                           "refresh_token_expires_in": 5183999}
        self.settings["KAKAO_SECRET_WRITER"] = WRITER

        class Done:
            returncode = 0
            stdout = stderr = ""

        with patch.object(kakao_notify.shutil, "which", return_value="/usr/bin/gh"), \
                patch.object(kakao_notify.subprocess, "run", return_value=Done()) as run:
            rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/gh", "secret", "set", "KAKAO_REFRESH_TOKEN",
                                   "--repo", "owner/repo"])
        self.assertEqual(kwargs["input"], NEW_REFRESH, "값은 표준입력으로만 넘긴다")
        self.assertEqual(kwargs["env"]["GH_TOKEN"], WRITER)
        self.assertNotIn(NEW_REFRESH, " ".join(args[0]))
        for secret in (REFRESH, NEW_REFRESH, ACCESS, WRITER):
            self.assertNotIn(secret, out, "토큰이 로그에 찍혔다")
        self.assertNotIn(kakao_notify.REAUTH_NOTICE, [t["text"] for t in self.memo_calls()])

    def test_unsaved_rotation_warns_here_and_in_kakaotalk(self):
        self.token_body = {"access_token": ACCESS, "refresh_token": NEW_REFRESH}
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("::warning::", out)
        self.assertIn("KAKAO_SECRET_WRITER", out)
        self.assertEqual(self.memo_calls()[-1]["text"], kakao_notify.REAUTH_NOTICE)
        self.assertLessEqual(kakao_notify.text_units(kakao_notify.REAUTH_NOTICE),
                             kakao_notify.TEXT_LIMIT)
        self.assertNotIn(NEW_REFRESH, out)

    def test_blocked_outbox_never_reaches_kakao(self):
        self.outbox_path.write_text(json.dumps(outbox(status="quality_rejected")),
                                    encoding="utf-8")
        rc, _ = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief = (ROOT / ".github" / "workflows" / "daily-brief.yml").read_text(
            encoding="utf-8")

    def step(self) -> str:
        return self.brief.split("id: kakao\n", 1)[1].split("      - name:", 1)[0]

    def test_kakao_runs_only_from_the_daily_brief(self):
        for path in (ROOT / ".github" / "workflows").glob("*.yml"):
            if "python tools/kakao_notify.py" in path.read_text(encoding="utf-8"):
                self.assertEqual(path.name, "daily-brief.yml", f"{path.name} 이 카톡을 따로 보낸다")

    def test_step_hangs_off_the_claim_and_never_fails_the_brief(self):
        step = self.step()
        self.assertIn("python tools/kakao_notify.py", step)
        self.assertIn("always() && steps.claim.conclusion == 'success'", step)
        self.assertIn("continue-on-error: true", step)
        self.assertIn("WEB_DEPLOY_OUTCOME: ${{ steps.web-deploy.outcome }}", step)
        for name in ("KAKAO_REST_API_KEY", "KAKAO_REFRESH_TOKEN",
                     "KAKAO_CLIENT_SECRET", "KAKAO_SECRET_WRITER"):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", step)

    def test_step_runs_after_deploy_and_before_the_record_is_committed(self):
        deploy = self.brief.index("id: web-deploy")
        kakao = self.brief.index("id: kakao")
        commit = self.brief.index("- name: Commit issue review cache + data gate metrics")
        self.assertLess(deploy, kakao, "버튼이 여는 오늘 페이지는 배포 뒤에야 있다")
        self.assertLess(kakao, commit, "outbox 의 kakao 칸이 커밋되지 않으면 재실행이 중복 발송한다")
        commit_step = self.brief[commit:].split("      - name:", 1)[0]
        self.assertIn("git add outbox.json", commit_step)


if __name__ == "__main__":
    unittest.main()
