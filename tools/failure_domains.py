"""Separate the collection/briefing failure domain from the web build/deploy one.

Why this exists
---------------

`crawl` runs 컬렉션과 웹 배포를 **한 잡 안에서** 이어서 돌린다.  그래서 뉴스
수집이 끝까지 성공하고 상태 커밋까지 push 된 회차라도, 뒤에 붙은
`python web/build_data.py` 가 죽으면 워크플로 전체가 `failure` 로 뜬다.

실측 2026-09-01(run 33508671552 외 4건): `Collect news` 성공 → `Commit state`
성공 → `Deploy web to Cloudflare Pages` 에서 `validate_issue_catalog_ids` 가
`duplicate issue_id ... 팰리세이즈 vs 자포리자` 로 죽음 → 크롤 실패.  정상적으로
들어온 기사가 다섯 회차 연속 '수집 실패'로 읽혔고, 반대로 **웹이 깨졌다는
사실은 운영 알림으로 한 번도 나가지 않았다** — 크롤의 `operational_alerts`
호출은 웹 스텝보다 앞에 있고 `--web-build-outcome` 을 넘기지 않기 때문이다.

이 모듈이 하는 일은 하나다: 각 subsystem 의 step outcome 을 받아
`success / degraded / failure / skipped` 로 **분류**하고 그 판정을 워크플로
summary·output 으로 내보낸다.  게이트가 아니다 — 잡을 빨갛게 만드는 것은
여전히 `continue-on-error` 가 붙지 않은 코어 스텝들이다.  판정 로직을 YAML
표현식이 아니라 여기 두는 이유는 그래야 테스트가 가능하기 때문이다.

`degraded` 를 따로 두는 것이 핵심이다.  `web/build_data.py` 는 국소 issue_id
충돌을 fallback ID 로 격리하고 빌드를 계속하므로 step outcome 은 `success` 다.
그 회차를 `ok` 로 부르면 격리가 일어났다는 사실이 어디에도 남지 않고,
`failure` 로 부르면 fail-closed 와 구별되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# 판정을 한글로 찍는다.  Actions 러너는 UTF-8 이지만 로컬 Windows 콘솔은
# cp1252 라 그대로 두면 **보고자가 UnicodeEncodeError 로 죽는다** — 실패
# 도메인을 알리려고 만든 것이 도메인 하나를 더 만드는 셈이다.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


# GitHub step outcome 값.  스텝이 아예 실행되지 않아 표현식이 빈 문자열을
# 돌려준 경우를 `missing` 으로 구분한다 — `skipped` 는 '조건이 걸러 냈다'는
# 뜻이고 `missing` 은 '그 스텝을 물어본 적이 없다'는 뜻이라 서로 다르다.
OUTCOME_MISSING = "missing"
_NEUTRAL_OUTCOMES = frozenset({"skipped", OUTCOME_MISSING})

STATUS_SUCCESS = "success"
STATUS_DEGRADED = "degraded"
STATUS_FAILURE = "failure"
STATUS_SKIPPED = "skipped"

BUILD_MODE_DEGRADED = "degraded"

# subsystem 별 코어 스텝.  여기 있는 스텝만 잡을 빨갛게 만들 자격이 있다.
_CORE_STAGES = {
    "crawl": (("collect", "뉴스 수집"), ("state", "수집 상태 커밋")),
    "daily-brief": (("plan", "브리핑 선별"), ("claim", "발송 전 커밋"),
                    ("send", "텔레그램 발송"), ("confirm", "발송 결과 기록")),
}
_CORE_LABELS = {"crawl": "collection", "daily-brief": "briefing"}

_WEB_STAGES = (("web_build", "웹 데이터 빌드"), ("web_deploy", "Cloudflare 배포"),
               ("web_smoke", "라이브 스모크"))


def normalize(outcome: object) -> str:
    value = str(outcome or "").strip().lower()
    return value or OUTCOME_MISSING


def _classify(stages, outcomes: dict) -> tuple[str, list[str]]:
    """Return ``(status, failed stage keys)`` for one subsystem.

    A stage that never ran is not a success and not a failure.  Collapsing the
    two into a boolean is what made a skipped deploy window read like a healthy
    deploy in the old single-step layout.
    """
    failed = [key for key, _label in stages
              if normalize(outcomes.get(key)) not in _NEUTRAL_OUTCOMES
              and normalize(outcomes.get(key)) != STATUS_SUCCESS]
    if failed:
        return STATUS_FAILURE, failed
    ran = [key for key, _label in stages
           if normalize(outcomes.get(key)) == STATUS_SUCCESS]
    if not ran:
        return STATUS_SKIPPED, []
    return STATUS_SUCCESS, []


def classify(domain: str, outcomes: dict, *, build_mode: str = "",
             identity_quarantined: int = 0) -> dict:
    """Judge the core subsystem and the web subsystem independently.

    The two verdicts never overwrite each other.  A collection that finished and
    committed its state stays ``success`` no matter what the web build did, and a
    broken web build stays visible even though the job as a whole is green.
    """
    if domain not in _CORE_STAGES:
        raise ValueError(f"unknown domain: {domain!r}")
    core_status, core_failed = _classify(_CORE_STAGES[domain], outcomes)
    web_status, web_failed = _classify(_WEB_STAGES, outcomes)

    mode = str(build_mode or "").strip().lower()
    quarantined = max(0, int(identity_quarantined or 0))
    # degraded 는 **빌드가 끝까지 돈 회차에만** 의미가 있다.  실패한 빌드가 남긴
    # 낡은 build_mode 를 degraded 로 승격하면 fail-closed 가 경고로 내려앉는다.
    if web_status == STATUS_SUCCESS and mode == BUILD_MODE_DEGRADED:
        web_status = STATUS_DEGRADED

    return {
        "domain": domain,
        "core_name": _CORE_LABELS[domain],
        "core_status": core_status,
        "core_failed_stages": core_failed,
        "web_status": web_status,
        "web_failed_stages": web_failed,
        "build_mode": mode or "",
        "identity_quarantined": quarantined,
        # 잡을 빨갛게 만들 자격이 있는 것은 코어뿐이다.  웹은 별도 축으로 보고된다.
        "job_should_fail": core_status == STATUS_FAILURE,
    }


def _stage_label(key: str) -> str:
    for stage_key, label in (*_CORE_STAGES["crawl"], *_CORE_STAGES["daily-brief"],
                             *_WEB_STAGES):
        if stage_key == key:
            return label
    return key


def render_summary(verdict: dict, outcomes: dict) -> str:
    """Render the two domains as separate lines, never as one workflow verdict."""
    core_name = "수집" if verdict["core_name"] == "collection" else "브리핑"
    lines = [f"### Failure domains ({verdict['domain']})", ""]
    lines.append(f"- {core_name}: `{verdict['core_status']}`")
    if verdict["core_failed_stages"]:
        detail = ", ".join(f"{_stage_label(key)}={normalize(outcomes.get(key))}"
                           for key in verdict["core_failed_stages"])
        lines.append(f"  - 실패 단계: {detail}")
    lines.append(f"- 웹 빌드·배포: `{verdict['web_status']}`")
    if verdict["web_failed_stages"]:
        detail = ", ".join(f"{_stage_label(key)}={normalize(outcomes.get(key))}"
                           for key in verdict["web_failed_stages"])
        lines.append(f"  - 실패 단계: {detail}")
    if verdict["web_status"] == STATUS_DEGRADED:
        lines.append(f"  - build_mode: `degraded` — 이슈 클러스터 "
                     f"{verdict['identity_quarantined']}건을 fallback ID 로 격리")
    elif verdict["build_mode"]:
        lines.append(f"  - build_mode: `{verdict['build_mode']}`")
    if verdict["web_status"] in (STATUS_FAILURE, STATUS_DEGRADED):
        lines.append(f"- {core_name} 결과는 웹 상태와 무관하게 위 값 그대로입니다.")
    lines.append("")
    return "\n".join(lines)


def annotations(verdict: dict) -> list[str]:
    """Workflow annotations so a web problem stays visible on a green run.

    `::error::` 는 잡 상태를 바꾸지 않는다.  잡이 초록이어도 실행 화면 맨 위
    주석 목록에는 남으므로, "수집은 성공했지만 웹은 깨졌다" 를 숨기지 않고
    말할 수 있는 유일한 자리다.
    """
    out: list[str] = []
    if verdict["web_status"] == STATUS_FAILURE:
        stages = ", ".join(_stage_label(key) for key in verdict["web_failed_stages"])
        out.append(f"::error::웹 빌드·배포 실패 ({stages}) — "
                   f"{verdict['core_name']} 결과는 정상으로 보존됩니다.")
    elif verdict["web_status"] == STATUS_DEGRADED:
        out.append(f"::warning::degraded 빌드 — 이슈 클러스터 "
                   f"{verdict['identity_quarantined']}건이 fallback ID 로 격리됐습니다.")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="collection/briefing 과 web build/deploy 의 실패 도메인을 분리 보고")
    parser.add_argument("--domain", required=True, choices=sorted(_CORE_STAGES))
    for key, _label in (*_CORE_STAGES["crawl"], *_CORE_STAGES["daily-brief"],
                        *_WEB_STAGES):
        parser.add_argument(f"--{key.replace('_', '-')}-outcome", default="",
                            dest=f"{key}_outcome")
    parser.add_argument("--build-mode", default="")
    parser.add_argument("--identity-quarantined", default="0")
    args = parser.parse_args()

    outcomes = {
        key: getattr(args, f"{key}_outcome", "")
        for key, _label in (*_CORE_STAGES["crawl"], *_CORE_STAGES["daily-brief"],
                            *_WEB_STAGES)
    }
    try:
        quarantined = int(str(args.identity_quarantined).strip() or 0)
    except ValueError:
        quarantined = 0
    verdict = classify(args.domain, outcomes, build_mode=args.build_mode,
                       identity_quarantined=quarantined)

    print(f"[failure-domains] {verdict['core_name']}={verdict['core_status']} "
          f"web={verdict['web_status']} build_mode={verdict['build_mode'] or '-'}")
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    for line in annotations(verdict):
        print(line)

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"core_status={verdict['core_status']}\n")
            handle.write(f"web_status={verdict['web_status']}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(render_summary(verdict, outcomes))
    # 보고자는 게이트가 아니다.  잡을 빨갛게 만드는 것은 코어 스텝 자신이고,
    # 여기서 exit 1 을 더하면 그 판정을 두 번 하게 된다.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
