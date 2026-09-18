# -*- coding: utf-8 -*-
"""카드뉴스 실행 구조 — 실패가 어디에 남는지에 대한 계약.

예전 구조는 daily-brief.yml 안의 스텝 다섯 개였고 전부 ``continue-on-error``
였다. 카드가 죽어도 텍스트 브리핑은 나가야 하니 그 판단 자체는 옳았는데,
대가로 **실패가 어디에도 안 남았다** — make_cards.py 가 exit 1 로 알려도
Daily Brief 는 초록불이라 카드가 빠진 날을 사람이 사이트를 열어 보고서야 알았다.

지금의 계약은 둘로 갈린다.

    Daily Brief  카드가 죽어도 초록불 (깨우기만 하고 결과를 안 기다린다)
    Cards        카드가 죽으면 빨간불 (굽기·게시·검사 어느 하나만 실패해도)

이 파일은 그 갈라짐이 도로 붙지 않게 지킨다. 되붙는 방식은 늘 같다 —
'잠깐 넘기려고' continue-on-error 를 하나 얹는 것.
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def steps(workflow, job):
    return workflow["jobs"][job]["steps"]


def run_text(workflow, job):
    """잡이 **실제로 실행하는** 명령만 이어 붙인다.

    원문(raw YAML)을 통째로 grep 하면 주석이 걸린다 — 이 파일의 계약은 대부분
    "무엇을 하지 않는가"라, 하지 않는 이유를 적어 둔 주석이 곧 오탐이 된다.
    """
    return "\n".join(str(step.get("run", "")) for step in steps(workflow, job))


def triggers(workflow):
    # YAML 1.1 에서 `on:` 은 불리언 True 로 읽힌다.
    return workflow.get("on", workflow.get(True))


class DailyBriefHandsCardsOffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load("daily-brief.yml")
        cls.steps = steps(cls.workflow, "brief")
        cls.runs = run_text(cls.workflow, "brief")

    def test_the_brief_no_longer_makes_cards(self):
        """굽기·발송·게시는 전부 cards.yml 의 몫이다."""
        for script in ("make_cards.py", "send_album.py", "publish_cards.py"):
            self.assertNotIn(script, self.runs, f"{script} 가 아직 daily-brief 에서 돈다")

    def test_the_brief_no_longer_commits_card_pngs(self):
        """카드 PNG 는 cards.yml 이 자기 실행에서 커밋한다 — 두 곳이 같은 폴더를
        커밋하면 어느 쪽이 최신인지 알 수 없다."""
        self.assertNotIn("git add web/public/cards", self.runs)

    def test_cards_are_woken_only_after_the_site_deploy_succeeded(self):
        """카드는 **라이브의** briefings.json 을 재료로 쓴다.

        배포 전에 깨우면 어제 순위로 카드를 굽거나, 파일이 없어 그냥 죽는다.
        """
        trigger = next(s for s in self.steps if s.get("name") == "Trigger cards workflow")
        self.assertEqual(trigger["if"], "steps.web-deploy.outcome == 'success'")
        deploy = [i for i, s in enumerate(self.steps)
                  if s.get("id") == "web-deploy"]
        index = self.steps.index(trigger)
        self.assertTrue(deploy and deploy[0] < index, "배포 스텝보다 앞에서 깨운다")

    def test_waking_cards_can_never_fail_the_brief(self):
        """API 가 한 번 흔들렸다고 아침 브리핑을 실패로 만들지 않는다."""
        trigger = next(s for s in self.steps if s.get("name") == "Trigger cards workflow")
        self.assertTrue(trigger.get("continue-on-error"))

    def test_the_send_switch_is_still_cards_send(self):
        """텔레그램 앨범은 vars.CARDS_SEND=true 인 날에만 나간다.

        스위치가 daily-brief 에서 cards.yml 의 입력으로 옮겨 갔을 뿐 계약은 같다.
        채널은 repo 소유자의 자산이라 기본은 꺼짐이어야 한다.
        """
        trigger = next(s for s in self.steps if s.get("name") == "Trigger cards workflow")
        self.assertIn("CARDS_SEND", str(trigger.get("env", {})))
        self.assertIn('-f send="$SEND"', trigger["run"])
        # 자동 호출은 멱등하게 — 이미 만들어 둔 날을 다시 굽지 않는다.
        self.assertIn("-f force=false", trigger["run"])


class CardsWorkflowShowsItsFailuresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load("cards.yml")
        cls.job = cls.workflow["jobs"]["cards"]
        cls.steps = steps(cls.workflow, "cards")
        cls.runs = run_text(cls.workflow, "cards")

    def test_nothing_in_this_workflow_hides_a_failure(self):
        """continue-on-error 가 하나라도 붙으면 이 워크플로의 존재 이유가 사라진다."""
        self.assertNotIn("continue-on-error", self.job)
        for step in self.steps:
            self.assertNotIn("continue-on-error", step,
                             f"'{step.get('name', step.get('uses'))}' 가 실패를 삼킨다")

    def test_no_step_swallows_an_exit_code(self):
        """`|| true` · `|| echo` 로 종료 코드를 0 으로 만들지 않는다."""
        for step in self.steps:
            run = step.get("run", "")
            self.assertNotIn("|| true", run)
            self.assertNotIn("|| echo", run)

    def test_make_publish_and_verify_all_run_in_order(self):
        """굽고 → 게시하고 → **커밋 전에** 결과물을 본다.

        검사가 커밋 뒤로 밀리면 깨진 카드가 이미 main 에 들어간 뒤다.
        """
        order = [next(i for i, s in enumerate(self.steps) if needle in s.get("run", ""))
                 for needle in ("make_cards.py", "publish_cards.py",
                                "tools/verify_cards.py", "git commit")]
        self.assertEqual(order, sorted(order))

    def test_the_manual_rerun_keeps_its_two_switches(self):
        """수동 재생성은 날짜를 고를 수 있고 텔레그램 발송 여부를 고를 수 있다."""
        inputs = triggers(self.workflow)["workflow_dispatch"]["inputs"]
        self.assertIn("date", inputs)
        self.assertIn("send", inputs)
        self.assertFalse(inputs["send"]["default"], "발송은 기본 꺼짐이어야 한다")
        # 복구가 목적이라 손으로 부르면 늘 다시 굽는다.
        self.assertTrue(inputs["force"]["default"])

    def test_it_never_deploys_the_site_itself(self):
        """이 잡에는 build_data 산출물이 없다 — 여기서 wrangler 를 부르면 Pages 가
        라이브의 issue/·brief/·data/ 를 통째로 지운다. 배포는 deploy-web 에 맡긴다.
        """
        self.assertNotIn("wrangler", self.runs)
        deploy = next(s for s in self.steps if s.get("name") == "Trigger site deploy")
        self.assertIn("gh workflow run deploy-web.yml", deploy["run"])

    def test_an_unchanged_day_neither_commits_nor_deploys(self):
        """같은 파일을 다시 올리려고 Pages 배포 횟수(무료 500회/월)를 쓰지 않는다."""
        commit = next(s for s in self.steps if s.get("id") == "commit")
        self.assertIn("committed=false", commit["run"])
        deploy = next(s for s in self.steps if s.get("name") == "Trigger site deploy")
        self.assertEqual(deploy["if"], "steps.commit.outputs.committed == 'true'")

    def test_the_fast_deploy_path_is_one_line_away(self):
        """카드만 바뀐 날의 Fast Deploy 는 아직 안 켠다 — 자리만 만들어 둔다.

        deploy-web 의 fast 는 검증된 production snapshot 을 재사용하는 경로인데
        이 호출자에서 한 번도 안 돌려 봤다. 기본은 full 이고, 바꿀 자리는 한 곳이다.
        """
        modes = triggers(self.workflow)["workflow_dispatch"]["inputs"]["deploy_mode"]
        self.assertEqual(modes["default"], "full")
        self.assertEqual(sorted(modes["options"]), ["fast", "full"])
        deploy = next(s for s in self.steps if s.get("name") == "Trigger site deploy")
        self.assertIn("mode_hint=", deploy["run"])


if __name__ == "__main__":
    unittest.main()
