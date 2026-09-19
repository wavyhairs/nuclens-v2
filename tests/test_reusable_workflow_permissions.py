# -*- coding: utf-8 -*-
"""재사용 워크플로를 부르는 잡의 권한 계약.

2026-09-18 21:17 KST, deploy-web.yml 이 ``actions: write`` 를 요구하게 됐다
(검증된 production snapshot 저장). crawl·daily-brief·cards 는 워크플로 수준에서
이미 그 권한을 갖고 있었다. **그것을 ``uses:`` 로 부르는 weekly.yml 만
``contents: write`` 하나였다.**

GitHub 은 호출된 워크플로가 호출한 잡보다 큰 권한을 요구하면 실행을 **시작조차
하지 않는다.** 38분 뒤 첫 Weekly 런이 ``startup_failure`` 로 1초 만에 죽었고,
이후 열 번의 트리거가 전부 같은 자리에서 죽었다. 금요일 주간 판세가 통째로
사라지기까지 남은 시간은 엿새였다.

이 실패가 특히 나쁜 이유는 **아무도 못 잡는다**는 것이다. startup_failure 는
preflight 잡이 뜨기 전에 런 전체를 죽이므로 tools/weekly_trigger_gate.py 도,
스텝 안의 어떤 알림도, ``if: always()`` 도 돌지 않는다. 남는 것은 사람이
"금요일에 왜 안 왔지"를 묻는 것뿐이다.

그래서 여기서 잠근다. 검사 대상은 weekly 의 deploy 잡 하나가 아니라
``uses: ./.github/workflows/...`` 를 쓰는 **모든** 잡이다 — 이번 사고의 본질은
권한을 올리면서 호출자 하나를 빠뜨린 것이고, weekly 만 못 박으면 다음 호출자가
생겼을 때 똑같이 난다.

PyYAML 을 쓰지 않는다. 런타임에 필요 없는 의존을 검사 하나 때문에
requirements.txt 에 얹지 않는다 (tests/test_cards_workflow.py 머리말과 같은 이유).
"""
import re
import unittest
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

COMMENT_LINE = re.compile(r"^\s*#")
TRAILING_COMMENT = re.compile(r"\s+#.*$")
JOBS_HEADER = re.compile(r"^jobs:\s*$")
JOB_HEADER = re.compile(r"^  ([A-Za-z_][\w-]*):\s*$")
USES_LOCAL = re.compile(r"^    uses:\s*(\./\S+)")

# none < read < write. GitHub 이 비교하는 순서 그대로.
LEVELS = {"none": 0, "read": 1, "write": 2}

Caller = namedtuple("Caller", "workflow job called granted source")


def _lines(path):
    """주석 줄을 걷은 워크플로 원문."""
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if not COMMENT_LINE.match(line)]


def _value(line):
    """``key: value  # 설명`` 에서 value 만."""
    return TRAILING_COMMENT.sub("", line.split(":", 1)[1]).strip()


def permission_table(lines, index, indent):
    """``permissions:`` 줄(lines[index]) 아래의 스코프 표.

    축약형 ``read-all`` / ``write-all`` 은 ``*`` 한 칸으로, ``{}`` 는 빈 표로
    돌려준다 (빈 표 = 모든 스코프가 none).
    """
    inline = _value(lines[index])
    if inline:
        if inline in ("read-all", "write-all"):
            return {"*": inline.split("-")[0]}
        return {}
    table = {}
    for line in lines[index + 1:]:
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) < indent or ":" not in line:
            break
        table[line.split(":", 1)[0].strip()] = _value(line)
    return table


def workflow_permissions(lines):
    """워크플로 수준 permissions. 없으면 None (= 저장소 기본값에 맡겼다)."""
    for index, line in enumerate(lines):
        if line.startswith("permissions:"):
            return permission_table(lines, index, 2)
    return None


def job_blocks(lines):
    """``jobs:`` 아래의 (잡 이름, 본문 줄 목록)."""
    start = next((i for i, line in enumerate(lines) if JOBS_HEADER.match(line)), None)
    if start is None:
        return []
    blocks, name, buf = [], None, []
    for line in lines[start + 1:]:
        header = JOB_HEADER.match(line)
        if header:
            if name:
                blocks.append((name, buf))
            name, buf = header.group(1), []
            continue
        if line.strip() and not line.startswith(" "):
            break                       # jobs: 섹션이 끝났다
        buf.append(line)
    if name:
        blocks.append((name, buf))
    return blocks


def job_permissions(block):
    """잡 수준 permissions. 없으면 None (= 워크플로 수준을 그대로 물려받는다)."""
    for index, line in enumerate(block):
        if line.startswith("    permissions:"):
            return permission_table(block, index, 6)
    return None


def reusable_callers():
    """``uses: ./.github/workflows/...`` 로 재사용 워크플로를 부르는 모든 잡."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        lines = _lines(path)
        inherited = workflow_permissions(lines)
        for job, block in job_blocks(lines):
            called = next((USES_LOCAL.match(line).group(1)
                           for line in block if USES_LOCAL.match(line)), None)
            if not called:
                continue
            own = job_permissions(block)
            found.append(Caller(
                workflow=path.name, job=job, called=called,
                granted=own if own is not None else (inherited or {}),
                source=(f"jobs.{job}.permissions" if own is not None
                        else "워크플로 수준 permissions(잡에 블록이 없다)")))
    return found


def granted_level(table, scope):
    if scope in table:
        return table[scope]
    return table.get("*", "none")


class ReusableWorkflowPermissionTest(unittest.TestCase):
    def setUp(self):
        self.callers = reusable_callers()

    def test_the_parser_still_finds_the_callers(self):
        """계약 검사는 대상을 못 찾으면 조용히 통과한다. 그 침묵을 먼저 막는다."""
        self.assertTrue(self.callers,
                        "재사용 워크플로 호출자를 하나도 못 찾았다 — 파서를 의심할 것")
        self.assertIn(("weekly.yml", "deploy"),
                      {(c.workflow, c.job) for c in self.callers},
                      "weekly.yml 의 deploy 잡이 안 보인다 — 이 검사가 태어난 자리다")

    def test_caller_grants_every_permission_the_called_workflow_asks_for(self):
        for caller in self.callers:
            self.assertTrue(caller.called.startswith("./"),
                            f"{caller.workflow} 의 {caller.job} 이 로컬 경로가 아니다")
            called_path = ROOT / caller.called[2:]
            with self.subTest(workflow=caller.workflow, job=caller.job):
                self.assertTrue(called_path.exists(),
                                f"{caller.called} 가 없다")
                required = workflow_permissions(_lines(called_path)) or {}
                self.assertTrue(
                    required,
                    f"{caller.called} 의 permissions 를 하나도 못 읽었다 — 파서를 의심할 것")
                for scope, level in required.items():
                    have = granted_level(caller.granted, scope)
                    self.assertGreaterEqual(
                        LEVELS.get(have, 0), LEVELS.get(level, 0),
                        f"{caller.workflow} 의 {caller.job} 잡이 {caller.called} 를 부르는데 "
                        f"'{scope}: {level}' 을 못 준다(지금 '{have}', 출처: {caller.source}). "
                        f"GitHub 은 이 런을 startup_failure 로 시작조차 안 시킨다 — "
                        f"jobs.{caller.job}.permissions 에 '{scope}: {level}' 을 더할 것")

    def test_python_tests_watch_the_workflow_files(self):
        """이 검사가 실제로 도는가.

        2026-09-18 의 변경은 deploy-web.yml 한 줄이었다. 그때 python-tests.yml 의
        paths 는 cards.yml·daily-brief.yml 만 세고 있어서, 워크플로를 고치는 PR 이
        워크플로 계약 검사를 안 돌렸다. 계약은 도는 자리에 있어야 계약이다.
        """
        body = (WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8")
        self.assertEqual(
            body.count('- ".github/workflows/**"'), 2,
            "pull_request 와 push 양쪽 paths 가 워크플로 디렉터리를 봐야 한다")


if __name__ == "__main__":
    unittest.main()
