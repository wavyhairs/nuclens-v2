"""무료/유료 키는 **Actions 계층에서 수동으로** 고른다.

계약은 셋이다.

1. 파이썬은 지금까지처럼 `GEMINI_API_KEY` 하나만 읽는다. `GEMINI_FREE_API_KEY`
   도 `GEMINI_PAID_MODE` 도 해석하지 않는다 — 키 선택이 코드로 새면 모드를
   바꾸는 데 커밋·PR·머지가 필요해지고, 그 순간 이 기능의 의미가 없어진다.
2. 실행 중 어떤 오류(quota·400·403·404·429·5xx)에도 다른 키로 자동 전환하지
   않는다. 고른 키 하나로 끝까지 간다.
3. 워크플로의 선택 식은 고른 쪽 시크릿이 비어 있으면 **반대쪽 키로 조용히
   넘어간다.** 그래서 모든 잡이 첫 Gemini 스텝보다 먼저 검증 스텝을 돌려
   어긋나면 멈춘다. 이 파일은 그 검증 스크립트를 실제 bash 로 돌려 고정한다.
"""
import io
import os
import shutil
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client as gc

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# Gemini 를 실제로 부르는 워크플로. python-tests 는 여기 없다 — 그쪽은 키를 고르지
# 않고 빈 값으로 못 박는다(아래 TestWorkflowsPinTheTestRunner).
GEMINI_WORKFLOWS = ("crawl.yml", "daily-brief.yml", "deploy-web.yml", "weekly.yml")

SELECT_EXPR = ("${{ vars.GEMINI_PAID_MODE == 'ON' "
               "&& secrets.GEMINI_API_KEY || secrets.GEMINI_FREE_API_KEY }}")
SELECT_LINE = f"GEMINI_API_KEY: {SELECT_EXPR}"
VERIFY_STEP = "- name: Verify Gemini key selection"

PAID = "paid-secret-not-real"
FREE = "free-secret-not-real"


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def verify_script(name: str) -> str:
    """검증 스텝의 `run:` 블록을 꺼낸다.

    PyYAML 을 쓰지 않는다 — requirements.txt 에 없어서 CI 에는 설치되지 않는다.
    들여쓰기로 블록을 자르는 편이 의존성을 늘리는 것보다 싸다.
    """
    lines = read(name).splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == VERIFY_STEP)
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body_indent = len(lines[run_at]) - len(lines[run_at].lstrip()) + 2
    out = []
    for line in lines[run_at + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < body_indent:
            break
        out.append(line[body_indent:] if line.strip() else "")
    return "\n".join(out)


def github_select(mode, paid: str, free: str) -> str:
    """`vars.GEMINI_PAID_MODE == 'ON' && secrets.A || secrets.B` 의 의미.

    GitHub 식에서 `a && b || c` 는 a 가 참이어도 **b 가 falsy 면 c 로 떨어진다.**
    빈 시크릿이 falsy 이므로 바로 이 자리가 조용한 오선택이 태어나는 곳이다.
    검증 스텝은 이 함수가 내놓는 값을 받아 걸러 내야 한다.
    """
    return (paid or free) if mode == "ON" else free


class TestPythonReadsOneKeyOnly(unittest.TestCase):
    """키 선택은 Actions 계층에서만 한다."""

    def _sources(self):
        for path in sorted(ROOT.glob("*.py")):
            yield path
        for folder in ("tools", "workers", "web", "functions"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                yield path

    def test_no_python_file_interprets_the_new_names(self):
        for name in ("GEMINI_FREE_API_KEY", "GEMINI_PAID_MODE"):
            for path in self._sources():
                self.assertNotIn(
                    name, path.read_text(encoding="utf-8"),
                    f"{path.relative_to(ROOT)} 가 {name} 를 해석한다 — 키 선택이 "
                    f"코드로 새면 모드 전환에 커밋·PR·머지가 필요해진다")

    def test_gemini_client_resolves_exactly_one_key(self):
        self.assertEqual(gc.API_KEY, gc._resolve("GEMINI_API_KEY"))
        self.assertFalse([n for n in dir(gc) if "FREE" in n or "PAID_MODE" in n],
                         "자동 폴백 잔재가 남아 있다")

    def test_availability_is_that_one_key(self):
        with patch.object(gc, "API_KEY", "k"):
            self.assertTrue(gc.is_available())
        with patch.object(gc, "API_KEY", None):
            self.assertFalse(gc.is_available())


DAILY_BODY = """{"error":{"code":429,"message":"You exceeded your current quota",
"details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[
{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}"""

MINUTE_BODY = """{"error":{"code":429,"message":"You exceeded your current quota",
"details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[
{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}"""


class TestNoAutomaticKeySwitching(unittest.TestCase):
    """quota 든 400/403/404/5xx 든, 고른 키 하나로 끝까지 간다.

    자동 전환이 다시 들어오면 무료로 둔 줄 알았던 실행이 유료 키로 넘어가고,
    그 사실은 로그가 아니라 청구서로만 드러난다.
    """

    ONLY_KEY = "the-only-key-not-real"

    def setUp(self):
        gc.reset_call_log()
        self.addCleanup(gc.reset_call_log)

    def _run(self, status: int, body: str, retries: int = 2):
        seen: list[str] = []

        def fake_urlopen(req, *a, **kw):
            seen.append(req.get_header("X-goog-api-key"))
            raise urllib.error.HTTPError("https://example.invalid", status, "err",
                                         {}, io.BytesIO(body.encode("utf-8")))

        with patch.object(gc, "API_KEY", self.ONLY_KEY), \
             patch.object(gc.urllib.request, "urlopen", fake_urlopen), \
             patch.object(gc.time, "sleep", lambda _s: None), \
             patch("sys.stdout", io.StringIO()):
            with self.assertRaises(gc.GeminiError):
                gc.call_json("system", "user", retries=retries)
        return seen

    def test_every_error_class_keeps_the_same_key(self):
        for status, body in ((429, DAILY_BODY), (429, MINUTE_BODY),
                             (400, "bad request"), (403, "forbidden"),
                             (404, "not found"), (500, "boom"), (503, "later")):
            with self.subTest(status=status, body=body[:12]):
                seen = self._run(status, body)
                self.assertTrue(seen, "호출이 아예 안 나갔다")
                self.assertEqual({self.ONLY_KEY}, set(seen),
                                 f"HTTP {status} 에서 키가 바뀌었다")

    def test_daily_quota_still_fails_fast(self):
        """일일 한도를 재시도하면 실패한 호출이 쿼터를 4배로 먹는다 — 기존 판단."""
        self.assertEqual(1, len(self._run(429, DAILY_BODY, retries=3)))

    def test_minute_quota_still_backs_off_and_retries(self):
        self.assertEqual(3, len(self._run(429, MINUTE_BODY, retries=2)))


class TestWorkflowsSelectTheKey(unittest.TestCase):
    def test_every_gemini_step_uses_the_selection_expression(self):
        """스텝 하나를 빠뜨리는 것이 이 기능의 가장 그럴듯한 고장 방식이다.

        실패가 아니라서 눈에 안 띈다 — 그 스텝만 계속 유료를 태우는데 결과도
        로그도 평소와 똑같다.
        """
        found = 0
        for name in GEMINI_WORKFLOWS:
            for number, line in enumerate(read(name).splitlines(), 1):
                text = line.strip()
                if not text.startswith("GEMINI_API_KEY:"):
                    continue
                found += 1
                self.assertEqual(SELECT_LINE, text,
                                 f"{name}:{number} 가 선택 식을 안 쓴다")
        self.assertEqual(15, found, "Gemini 스텝 수가 달라졌다 — 새 스텝을 확인할 것")

    def test_no_step_reaches_a_secret_directly(self):
        """시크릿을 직접 집는 곳은 검증 스텝뿐이다. 나머지는 선택 식만 본다.

        직접 집는 스텝이 하나라도 늘면 그 스텝은 모드를 무시하고 항상 같은 키를
        쓴다 — 그리고 그것은 실패가 아니라 조용한 초과 과금으로만 드러난다.
        """
        paid_ref = "${{ secrets.GEMINI_API_KEY }}"
        free_ref = "${{ secrets.GEMINI_FREE_API_KEY }}"
        allowed = {f"PAID_KEY: {paid_ref}", f"FREE_KEY: {free_ref}",
                   f"SELECTED: {SELECT_EXPR}"}
        for name in GEMINI_WORKFLOWS:
            direct = 0
            for number, line in enumerate(read(name).splitlines(), 1):
                text = line.strip()
                if text.startswith("#"):
                    continue          # 주석은 식을 설명하려고 그대로 인용한다
                if text in allowed:
                    direct += 1
                    continue
                # 선택 식 안의 시크릿 참조는 걷어 내고 본다 — 남는 것이 있으면
                # 모드를 우회해 한쪽 키를 직접 집는 자리다.
                rest = text.replace(SELECT_EXPR, "")
                for ref in (paid_ref, free_ref):
                    self.assertNotIn(ref, rest,
                                     f"{name}:{number} 가 모드를 우회해 시크릿을 "
                                     f"직접 집는다")
            self.assertEqual(3, direct, f"{name}: 검증 스텝이 정확히 1개가 아니다")

    def test_verification_runs_before_the_first_gemini_step(self):
        """검증이 나중에 오면 그 앞 스텝들이 이미 잘못된 키로 나간 뒤다."""
        for name in GEMINI_WORKFLOWS:
            lines = read(name).splitlines()
            verify = [i for i, x in enumerate(lines) if x.strip() == VERIFY_STEP]
            gemini = [i for i, x in enumerate(lines)
                      if x.strip().startswith("GEMINI_API_KEY:")]
            self.assertEqual(1, len(verify), f"{name}: 검증 스텝이 1개가 아니다")
            self.assertTrue(gemini, f"{name}: Gemini 스텝이 없다")
            self.assertLess(verify[0], min(gemini), f"{name}: 검증이 너무 늦다")

    def test_tts_and_embedding_steps_use_the_selected_key(self):
        """TTS·임베딩은 gemini_client.call_json 을 안 거치지만 같은 환경변수를 읽는다.
        워크플로가 이 스텝들에 키를 안 넘기면 그 기능만 조용히 빠진다."""
        wanted = {
            "crawl.yml": ["Backfill briefing embeddings"],
            "daily-brief.yml": ["Backfill briefing embeddings",
                                "Generate audio briefings (빠른 → 전문가)"],
        }
        for name, steps in wanted.items():
            body = read(name)
            for step in steps:
                head = body.index(f"- name: {step}")
                block = body[head:head + 1200]
                self.assertIn(SELECT_LINE, block,
                              f"{name} 의 '{step}' 이 선택된 키를 안 받는다")


class TestWorkflowsPinTheTestRunner(unittest.TestCase):
    def test_python_tests_never_selects_a_real_secret(self):
        body = read("python-tests.yml")
        self.assertIn('GEMINI_API_KEY: ""', body)
        for token in ("secrets.GEMINI_API_KEY", "secrets.GEMINI_FREE_API_KEY",
                      "vars.GEMINI_PAID_MODE"):
            self.assertNotIn(token, body,
                             "회귀 검사는 외부 호출 0 이 전제다 — 시크릿을 고르면 안 된다")


@unittest.skipUnless(shutil.which("bash"), "bash 없음 (Actions 는 항상 bash 다)")
class TestVerificationScript(unittest.TestCase):
    """검증 스크립트를 실제로 돌린다.

    '이런 내용이 적혀 있다'는 검사는 이 스크립트가 실제로 막아 주는지를 말해 주지
    않는다. 막아야 할 것은 **선택 식의 조용한 폴백**이므로, 그 폴백을 그대로
    재현한 입력을 먹여 본다.
    """

    @classmethod
    def setUpClass(cls):
        cls.script = verify_script("crawl.yml")

    def test_all_four_workflows_share_one_script(self):
        for name in GEMINI_WORKFLOWS[1:]:
            self.assertEqual(self.script, verify_script(name),
                             f"{name} 의 검증 스크립트가 갈라졌다")

    def run_script(self, mode, paid=PAID, free=FREE):
        env = {
            **os.environ,
            "PAID_MODE": "" if mode is None else mode,
            "PAID_KEY": paid,
            "FREE_KEY": free,
            "SELECTED": github_select(mode, paid, free),
        }
        done = subprocess.run(["bash", "-c", self.script], env=env,
                              capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace")
        return done.returncode, done.stdout + done.stderr

    def test_on_selects_the_existing_paid_secret(self):
        code, log = self.run_script("ON")
        self.assertEqual(0, code, log)
        self.assertIn("Gemini key mode: paid", log)

    def test_off_selects_the_free_secret(self):
        code, log = self.run_script("OFF")
        self.assertEqual(0, code, log)
        self.assertIn("Gemini key mode: free", log)

    def test_anything_that_is_not_exactly_ON_is_free(self):
        for mode in (None, "", "off", "on", "On", "ON ", " ON", "TRUE", "1",
                     "OFF", "0FF", "paid", "yes"):
            with self.subTest(mode=mode):
                code, log = self.run_script(mode)
                self.assertEqual(0, code, log)
                self.assertIn("Gemini key mode: free", log)
                self.assertNotIn("mode: paid", log)

    def test_paid_mode_without_the_paid_secret_fails_instead_of_going_free(self):
        """여기서 통과시키면 유료를 켠 실행이 조용히 무료로 돈다."""
        code, log = self.run_script("ON", paid="")
        self.assertEqual(1, code, "빈 유료 시크릿이 통과했다")
        self.assertIn("::error::", log)
        self.assertIn("secrets.GEMINI_API_KEY", log)

    def test_free_mode_without_the_free_secret_fails_instead_of_going_paid(self):
        """이쪽이 더 위험하다 — 무료로 둔 줄 알았던 실행이 유료 키를 태운다."""
        code, log = self.run_script("OFF", free="")
        self.assertEqual(1, code, "빈 무료 시크릿이 통과했다")
        self.assertIn("::error::", log)
        self.assertIn("secrets.GEMINI_FREE_API_KEY", log)

    def test_both_secrets_missing_fails(self):
        for mode in ("ON", "OFF"):
            with self.subTest(mode=mode):
                code, _log = self.run_script(mode, paid="", free="")
                self.assertEqual(1, code)

    def test_a_mismatched_selection_is_caught(self):
        """선택 식이 어떤 이유로든 다른 키를 골라 오면 거기서 멈춘다."""
        env_script = self.script
        done = subprocess.run(
            ["bash", "-c", env_script],
            env={**os.environ, "PAID_MODE": "ON", "PAID_KEY": PAID,
                 "FREE_KEY": FREE, "SELECTED": FREE},
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        self.assertEqual(1, done.returncode)
        self.assertIn("::error::", done.stdout + done.stderr)

    def test_no_key_value_ever_reaches_the_log(self):
        cases = [("ON", PAID, FREE), ("OFF", PAID, FREE), (None, PAID, FREE),
                 ("ON", "", FREE), ("OFF", PAID, "")]
        for mode, paid, free in cases:
            with self.subTest(mode=mode, paid=bool(paid), free=bool(free)):
                _code, log = self.run_script(mode, paid=paid, free=free)
                for secret in (PAID, FREE):
                    if secret:
                        self.assertNotIn(secret, log, "로그에 키 값이 찍혔다")


if __name__ == "__main__":
    unittest.main()
