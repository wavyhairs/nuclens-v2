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

YAML 파서를 안 쓴다. requirements.txt 는 런타임에 실제로 필요한 것만 담고
(requests · google-genai · feedparser · pywebpush), 검사 하나를 위해 PyYAML 을
거기 얹지 않는다.
대신 **주석을 걷은 원문**을 본다 — 이 파일의 계약은 대부분 "무엇을 하지
않는가"라, 하지 않는 이유를 적어 둔 주석이 그대로 오탐이 되기 때문이다.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

COMMENT_LINE = re.compile(r"^\s*#")
STEP_SPLIT = re.compile(r"^      - ", re.M)
NEXT_JOB = re.compile(r"^  [A-Za-z_][\w-]*:\s*$", re.M)


def body(name):
    """주석 줄을 걷은 워크플로 원문."""
    return "\n".join(line for line in (WORKFLOWS / name).read_text(encoding="utf-8").splitlines()
                     if not COMMENT_LINE.match(line))


def job_text(name, job):
    """jobs.<job> 아래 본문. 뒤에 다른 잡이 오면 거기서 자른다."""
    text = body(name)
    head = f"\n  {job}:\n"
    tail = text[text.index(head) + len(head):]
    following = NEXT_JOB.search(tail)
    return tail[:following.start()] if following else tail


def step_blocks(name, job):
    """스텝 덩어리를 문서 순서대로. 각 덩어리는 `- ` 를 뗀 본문이다."""
    return STEP_SPLIT.split(job_text(name, job))[1:]


def step(blocks, title):
    match = [b for b in blocks if b.startswith(f"name: {title}\n")]
    assert len(match) == 1, f"'{title}' 스텝을 하나로 못 찾았다 ({len(match)}건)"
    return match[0]


def index_of(blocks, needle):
    return next(i for i, b in enumerate(blocks) if needle in b)


class DailyBriefHandsCardsOffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = body("daily-brief.yml")
        cls.blocks = step_blocks("daily-brief.yml", "brief")

    def test_the_brief_no_longer_makes_cards(self):
        """굽기·발송·게시는 전부 cards.yml 의 몫이다."""
        for script in ("make_cards.py", "send_album.py", "publish_cards.py"):
            self.assertNotIn(script, self.text, f"{script} 가 아직 daily-brief 에서 돈다")

    def test_the_brief_no_longer_commits_card_pngs(self):
        """카드 PNG 는 cards.yml 이 자기 실행에서 커밋한다 — 두 곳이 같은 폴더를
        커밋하면 어느 쪽이 최신인지 알 수 없다."""
        self.assertNotIn("git add web/public/cards", self.text)

    def test_cards_are_woken_only_after_the_site_deploy_succeeded(self):
        """카드는 **라이브의** briefings.json 을 재료로 쓴다.

        배포 전에 깨우면 어제 순위로 카드를 굽거나, 파일이 없어 그냥 죽는다.
        """
        trigger = step(self.blocks, "Trigger cards workflow")
        self.assertIn("if: steps.web-deploy.outcome == 'success'", trigger)
        self.assertLess(index_of(self.blocks, "id: web-deploy"),
                        self.blocks.index(trigger),
                        "배포 스텝보다 앞에서 깨운다")

    def test_cards_are_woken_after_todays_snapshot_is_saved(self):
        """FAST 배포가 어제 snapshot 과 경주하지 않게 (2026-09-19).

        Cards 의 마지막 배포는 캐시에 저장된 **가장 최근 검증 snapshot** 을
        복원하고 `guard-live --policy equal` 로 라이브와 같은 세대일 때만
        올린다. 오늘 snapshot 이 아직 저장 전이면 어제 것이 복원되고 — 낡은
        데이터가 올라가지는 않지만 Cards 가 애먼 빨간불이 된다.
        """
        trigger = self.blocks.index(step(self.blocks, "Trigger cards workflow"))
        self.assertLess(index_of(self.blocks, "id: production-snapshot-save"), trigger)

    def test_the_deploy_mode_follows_whether_that_snapshot_exists(self):
        """오늘 snapshot 이 저장됐으면 fast, 아니면 full.

        full 은 build_data 를 다시 돌린다 — 6~19분에 Gemini 호출까지. 오늘
        snapshot 이 있는 날 그 값을 치를 이유가 없고, 없는 날은 그게 유일한 길이다.
        """
        trigger = step(self.blocks, "Trigger cards workflow")
        self.assertIn("steps.production-snapshot-save.outcome == 'success' "
                      "&& 'fast' || 'full'", trigger)
        self.assertIn('-f deploy_mode="$DEPLOY_MODE"', trigger)

    def test_waking_cards_can_never_fail_the_brief(self):
        """API 가 한 번 흔들렸다고 아침 브리핑을 실패로 만들지 않는다."""
        self.assertIn("continue-on-error: true", step(self.blocks, "Trigger cards workflow"))

    def test_the_send_switch_is_still_cards_send(self):
        """텔레그램 앨범은 vars.CARDS_SEND=true 인 날에만 나간다.

        스위치가 daily-brief 에서 cards.yml 의 입력으로 옮겨 갔을 뿐 계약은 같다.
        채널은 repo 소유자의 자산이라 기본은 꺼짐이어야 한다.
        """
        trigger = step(self.blocks, "Trigger cards workflow")
        self.assertIn("vars.CARDS_SEND == 'true'", trigger)
        self.assertIn('-f send="$SEND"', trigger)
        # 자동 호출은 멱등하게 — 이미 만들어 둔 날을 다시 굽지 않는다.
        self.assertIn("-f force=false", trigger)


class CardsWorkflowShowsItsFailuresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = body("cards.yml")
        cls.blocks = step_blocks("cards.yml", "cards")

    def test_nothing_in_this_workflow_hides_a_failure(self):
        """continue-on-error 가 하나라도 붙으면 이 워크플로의 존재 이유가 사라진다."""
        self.assertNotIn("continue-on-error", self.text)

    def test_no_step_swallows_an_exit_code(self):
        """`|| true` · `|| echo` 로 종료 코드를 0 으로 만들지 않는다."""
        self.assertNotIn("|| true", self.text)
        self.assertNotIn("|| echo", self.text)

    def test_make_publish_and_verify_all_run_in_order(self):
        """굽고 → 게시하고 → **커밋 전에** 결과물을 본다.

        검사가 커밋 뒤로 밀리면 깨진 카드가 이미 main 에 들어간 뒤다.
        """
        order = [index_of(self.blocks, needle)
                 for needle in ("make_cards.py", "publish_cards.py",
                                "tools/verify_cards.py", "git commit")]
        self.assertEqual(order, sorted(order))

    def test_the_manual_rerun_keeps_its_two_switches(self):
        """수동 재생성은 날짜를 고를 수 있고 텔레그램 발송 여부를 고를 수 있다."""
        inputs = self.text.split("workflow_dispatch:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("      date:", inputs)
        send = inputs.split("      send:", 1)[1].split("      force:", 1)[0]
        self.assertIn("default: false", send, "발송은 기본 꺼짐이어야 한다")
        # 복구가 목적이라 손으로 부르면 늘 다시 굽는다.
        force = inputs.split("      force:", 1)[1].split("      deploy_mode:", 1)[0]
        self.assertIn("default: true", force)

    def test_it_never_deploys_the_site_itself(self):
        """이 잡에는 build_data 산출물이 없다 — 여기서 wrangler 를 부르면 Pages 가
        라이브의 issue/·brief/·data/ 를 통째로 지운다. 배포는 deploy-web 에 맡긴다.
        """
        self.assertNotIn("wrangler", self.text)
        self.assertIn("gh workflow run deploy-web.yml",
                      step(self.blocks, "Trigger site deploy"))

    def test_an_unchanged_day_neither_commits_nor_deploys(self):
        """같은 파일을 다시 올리려고 Pages 배포 횟수(무료 500회/월)를 쓰지 않는다."""
        self.assertIn("committed=false", step(self.blocks, "Commit cards"))
        self.assertIn("if: steps.commit.outputs.committed == 'true'",
                      step(self.blocks, "Trigger site deploy"))

    def test_the_card_deploy_reuses_the_verified_snapshot(self):
        """카드만 바뀐 날은 FAST 로 올린다 (2026-09-19).

        full 은 낭비이기 전에 **위험**이었다. deploy-web 의 full 은
        web/build_data.py 를 다시 돌리는데, 그 이슈 병합 회색지대 판정은 Gemini 를
        부르고 결정적이지 않다. 카드는 첫 배포의 briefings.json 순위로 구워졌으므로,
        두 번째 full 빌드가 순위를 다시 정하면 화면과 카드가 다른 얘기를 한다 —
        카드가 사이트 순위를 그대로 받아 쓰는 이유가 바로 그걸 막으려는 것이었다.

        fast 는 build_data 를 안 돌리고, snapshot 세대가 라이브와 다르면 올리지
        않고 실패한다. 그래서 그 갈라짐이 구조적으로 불가능하다.
        """
        mode = self.text.split("      deploy_mode:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("default: fast", mode)
        self.assertIn("options: [fast, full]", mode)
        deploy = step(self.blocks, "Trigger site deploy")
        self.assertIn("mode_hint=", deploy)
        # 입력이 비어 오는 경로에서도 full 로 떨어지지 않는다.
        self.assertIn("inputs.deploy_mode || 'fast'", deploy)


class WorkflowShapeTest(unittest.TestCase):
    """위 검사들이 딛고 선 전제 — 깨지면 조용히 0건을 검사하게 된다."""

    def test_both_jobs_yield_their_steps(self):
        brief = step_blocks("daily-brief.yml", "brief")
        cards = step_blocks("cards.yml", "cards")
        self.assertGreater(len(brief), 20, "brief 잡의 스텝을 못 읽었다")
        self.assertGreater(len(cards), 8, "cards 잡의 스텝을 못 읽었다")
        self.assertTrue(all(b.startswith(("name:", "uses:", "run:")) for b in cards),
                        [b.splitlines()[0] for b in cards])


if __name__ == "__main__":
    unittest.main()
