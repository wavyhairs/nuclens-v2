"""이슈 창을 바꿔 가며 **production 빌드를 그대로** 돌리고 그 차이를 잰다.

## 왜 별도 하네스인가

`tools/recorded_replay.py` 는 이것을 못 한다. 그쪽이 재현하는 것은 **Gemini HTTP
요청열**(curation·dedup·issue_review·keei_match)이고, 이슈 묶음을 다시 만들지
않는다. 창을 재려면 `web/build_data.py` 를 통째로 돌려야 한다.

## 무엇을 격리하는가

창을 바꿔 돌리면 산출물이 달라진다. 그 결과가 **실제 운영 파일을 덮으면 안 된다.**
그래서 arm 마다 자기 디렉터리를 준다.

    OUTPUT_DIR        web/_windows/<label>/data
    ADMIN_OUTPUT_DIR  web/_windows/<label>/admin
    ISSUE_LEDGER_FILE web/_windows/<label>/issue_ledger.json

원장은 **같은 기준 사본에서 출발**해야 한다. arm 끼리 다른 원장에서 시작하면
상속률 차이가 창 때문인지 원장 때문인지 갈리지 않는다.

## LLM 을 켤 것인가

기본은 끈다(키를 빈 값으로 강제). 다만 **끈 결과를 창 비교의 최종 답으로 쓰면
안 된다.** `issue_review` 는 키가 없으면 병합하지 않으므로, 창을 넓혀 새로 생긴
쌍은 전부 "안 붙음"으로 나온다. 그 쌍은 정의상 **한 번도 물어본 적이 없는 쌍**이라
캐시도 비어 있다(캐시 키가 기사 해시 쌍이다).

그래서 두 숫자를 따로 낸다.

    구조적 delta   창 덕분에 후보/부착 게이트를 통과한 쌍      (--no-llm 으로도 잰다)
    판정 delta     그중 실제로 병합까지 간 쌍                  (--live-llm 필요)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import date
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARM_ROOT = ROOT / "web" / "_windows"
BASELINE_LEDGER = ARM_ROOT / "_baseline_issue_ledger.json"
BASELINE_REVIEWS = ARM_ROOT / "_baseline_issue_llm_reviews.json"


def _parse_day(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def arm_dir(label: str) -> Path:
    return ARM_ROOT / label


def seed_ledger(label: str) -> Path:
    """모든 arm 이 같은 원장에서 출발하도록 기준 사본을 깐다."""
    ARM_ROOT.mkdir(parents=True, exist_ok=True)
    if not BASELINE_LEDGER.exists():
        source = ROOT / "issue_ledger.json"
        shutil.copyfile(source, BASELINE_LEDGER)
        print(f"[window_replay] 기준 원장 고정 → {BASELINE_LEDGER.name}")
    target = arm_dir(label) / "issue_ledger.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BASELINE_LEDGER, target)
    return target


def seed_review_cache(label: str, *, reuse: bool = False) -> Path:
    """판정 캐시도 arm 마다 사본을 준다.

    판정 자체는 창과 무관하다(키가 기사 해시 쌍이다). 그래도 운영 파일에 바로
    쓰지 않는다 — 재생이 만든 판정은 검토 전이고, 8 MB 파일이 arm 마다 흔들리면
    무엇이 언제 들어왔는지 못 가린다. 대신 **같은 기준 사본에서 출발**해서
    arm 사이의 적중률 차이가 창 때문이게 한다.
    """
    ARM_ROOT.mkdir(parents=True, exist_ok=True)
    if not BASELINE_REVIEWS.exists():
        shutil.copyfile(ROOT / "issue_llm_reviews.json", BASELINE_REVIEWS)
        print(f"[window_replay] 기준 판정 캐시 고정 → {BASELINE_REVIEWS.name}")
    target = arm_dir(label) / "issue_llm_reviews.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (reuse and target.exists()):
        shutil.copyfile(BASELINE_REVIEWS, target)
    return target


def snapshot_round(label: str, round_index: int) -> None:
    """라운드마다 issues.json 을 따로 남긴다.

    멱등성은 "같은 입력을 두 번 돌리면 같은 id 가 나오는가"다. 산출물을 덮어쓰면
    그 비교가 불가능해진다 — 두 번째 라운드만 남기 때문이다.
    """
    source = arm_dir(label) / "data" / "issues.json"
    if source.exists():
        shutil.copyfile(source, arm_dir(label) / f"round{round_index}_issues.json")


def round_stability(label: str, rounds: int) -> dict:
    """라운드 사이 issue_id 유지율. D 가 3회차 525/525 로 못 박은 그 검사다."""
    sets = []
    for index in range(1, rounds + 1):
        path = arm_dir(label) / f"round{index}_issues.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        sets.append({str(row.get("issue_id") or "") for row in rows})
    if len(sets) < 2:
        return {"rounds": len(sets)}

    def _compare(left: set, right: set) -> dict:
        return {"kept": len(left & right),
                "kept_rate": round(len(left & right) / len(left), 4) if left else 0,
                "gone": len(left - right), "new": len(right - left)}

    out = {"rounds": len(sets), "counts": [len(row) for row in sets],
           "first_to_last": _compare(sets[0], sets[-1])}
    if len(sets) >= 3:
        # 1회차는 판정 캐시를 채우는 회차라 입력이 같지 않다. **진짜 멱등성은
        # 캐시가 데워진 뒤 두 회차 사이**에서 재야 한다.
        out["warm_pair"] = _compare(sets[-2], sets[-1])
    return out


def run_build(label: str, window: int, as_of: str, *, live_llm: bool,
              reuse_ledger: bool = False) -> dict:
    target = arm_dir(label)
    target.mkdir(parents=True, exist_ok=True)
    ledger = (target / "issue_ledger.json") if reuse_ledger else seed_ledger(label)
    reviews = seed_review_cache(label, reuse=reuse_ledger)
    profile = target / "profile.json"

    env = os.environ.copy()
    env.update({
        "NUCLENS_ISSUE_WINDOW_DAYS": str(window),
        "NUCLENS_BUILD_AS_OF": as_of,
        "NUCLENS_BUILD_PROFILE": str(profile),
        "OUTPUT_DIR": str(target / "data"),
        "ADMIN_OUTPUT_DIR": str(target / "admin"),
        "ISSUE_LEDGER_FILE": str(ledger),
        "ISSUE_REVIEW_CACHE_FILE": str(reviews),
        # 빌드가 저장소 **뿌리에** 쓰는 캐시가 더 있다. 첫 재생에서 실제로
        # `issue_insights.json`(463줄)과 `keei_llm_matches.json`(72줄)이 더럽혀졌다 —
        # git status 가 아니었으면 못 봤을 것이다. 격리는 전수여야 한다.
        "ISSUE_INSIGHT_CACHE_FILE": str(target / "issue_insights.json"),
        "KEEI_MATCH_CACHE_FILE": str(target / "keei_llm_matches.json"),
        "GENERATION_ID": f"window-replay-{label}",
    })
    if not live_llm:
        # dotenv 가 로컬 자격증명을 되살리지 못하도록 **있지만 빈 값**으로 둔다.
        env["GEMINI_API_KEY"] = ""
        env["GOOGLE_API_KEY"] = ""
    started = time.time()
    completed = subprocess.run(
        [sys.executable, "-u", str(ROOT / "web" / "build_data.py")],
        cwd=ROOT, env=env, check=False,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - started
    (target / "build.log").write_text(
        (completed.stdout or "") + "\n--- stderr ---\n" + (completed.stderr or ""),
        encoding="utf-8")
    if completed.returncode:
        tail = "\n".join((completed.stdout or "").splitlines()[-15:])
        raise SystemExit(f"[window_replay] {label} 빌드 실패 rc={completed.returncode}\n{tail}")
    return {"label": label, "window": window, "as_of": as_of,
            "live_llm": live_llm, "wall_seconds": round(elapsed, 1)}


# ── 측정 ────────────────────────────────────────────────────────────────────

def _evidence_hashes(issue: dict) -> set[str]:
    """이 이슈가 들고 있는 근거 기사 해시.

    `story_members` 는 쓰지 않는다 — D 단계 실측에서 두 이슈가 함께 가진 해시가
    120건이라 배타적이지 않고, 그래서 신원 근거로도 배제됐다.
    """
    out = {str(issue.get("representative_article", {}).get("hash") or "")}
    for row in issue.get("related_articles") or []:
        value = str(row.get("hash") or "")
        if value:
            out.add(value)
    out.discard("")
    return out


def _evidence_days(issue: dict) -> list[date]:
    """근거 기사의 날짜. **해시로 중복을 제거한다** —

    대표 기사는 `related_articles` 에도 실려 있어서, 그대로 세면 단독 이슈조차
    날짜 두 개를 가진 것처럼 보인다. 첫 재생에서 실제로 554/554 가 'span 표본'
    으로 잡혔다. D 문서의 n=273 과 비교가 안 되는 숫자였다.
    """
    by_hash: dict[str, date] = {}
    rep = issue.get("representative_article") or {}
    rep_day = _parse_day(rep.get("article_date"))
    if rep_day and rep.get("hash"):
        by_hash[str(rep["hash"])] = rep_day
    for row in issue.get("related_articles") or []:
        day = _parse_day(row.get("article_date"))
        if day and row.get("hash"):
            by_hash[str(row["hash"])] = day
    return sorted(by_hash.values())


def measure(label: str) -> dict:
    target = arm_dir(label)
    issues = json.loads((target / "data" / "issues.json").read_text(encoding="utf-8"))
    meta = json.loads((target / "data" / "meta.json").read_text(encoding="utf-8"))
    audit = json.loads((target / "data" / "issue_audit.json").read_text(encoding="utf-8"))
    try:
        profile = json.loads((target / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        profile = {}

    spans: list[int] = []
    evidence_total = 0
    pairs: set[tuple[str, str]] = set()
    members_by_issue: dict[str, list[str]] = {}
    single_briefing = 0
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "")
        hashes = sorted(_evidence_hashes(issue))
        members_by_issue[issue_id] = hashes
        evidence_total += len(issue.get("related_articles") or [])
        if int(issue.get("briefing_count") or 0) <= 1:
            single_briefing += 1
        days = _evidence_days(issue)
        if len(days) >= 2:
            spans.append((max(days) - min(days)).days)
        # 같은 이슈 안에 함께 놓인 기사 쌍. 창 비교의 단위가 이것이다 —
        # 이슈 id 는 창이 바뀌면 통째로 갈릴 수 있지만 기사 쌍은 안정적이다.
        for left, right in combinations(hashes, 2):
            pairs.add((left, right))

    identity = (meta.get("event_identity") or {})
    llm = (audit.get("llm_review") or {})
    return {
        "label": label,
        "issue_window_days": meta.get("issue_window_days"),
        "issue_count": len(issues),
        "single_briefing_count": single_briefing,
        "single_briefing_rate": round(single_briefing / len(issues), 4) if issues else 0,
        "evidence_row_count": evidence_total,
        "span_sample": len(spans),
        "span_median": statistics.median(spans) if spans else 0,
        "span_over_7": sum(1 for value in spans if value > 7),
        "span_over_21": sum(1 for value in spans if value > 21),
        "span_max": max(spans) if spans else 0,
        "pair_count": len(pairs),
        "match_methods": meta.get("issue_match_methods") or {},
        "identity": {key: identity.get(key) for key in
                     ("inherited", "merged", "merged_away_ids", "split",
                      "minted", "inheritance_rate")},
        "review_candidate_count": meta.get("issue_review_candidate_count"),
        "llm_review": {key: llm.get(key) for key in
                       ("candidates", "from_cache", "asked", "calls", "failed",
                        "status") if key in llm},
        "embedding_cache_entries": meta.get("embedding_cache_entries"),
        "remote_embedding_selected_count": meta.get("remote_embedding_selected_count"),
        "embedding_selected_coverage": meta.get("embedding_selected_coverage"),
        "tracking_window_rate": meta.get("tracking_window_rate"),
        "wall_seconds": profile.get("wall_seconds"),
        "_pairs": sorted(f"{a}--{b}" for a, b in pairs),
        "_issue_ids": sorted(members_by_issue),
        "_members": members_by_issue,
    }


def compare(base: dict, later: dict) -> dict:
    base_pairs = set(base["_pairs"])
    later_pairs = set(later["_pairs"])
    gained = later_pairs - base_pairs
    lost = base_pairs - later_pairs
    base_ids = set(base["_issue_ids"])
    later_ids = set(later["_issue_ids"])
    return {
        "from": base["label"], "to": later["label"],
        "pairs_gained": len(gained),
        "pairs_lost": len(lost),
        "issue_count_delta": later["issue_count"] - base["issue_count"],
        "single_briefing_delta": later["single_briefing_count"] - base["single_briefing_count"],
        "issue_id_kept": len(base_ids & later_ids),
        "issue_id_kept_rate": round(len(base_ids & later_ids) / len(base_ids), 4) if base_ids else 0,
        "issue_id_new": len(later_ids - base_ids),
        "issue_id_gone": len(base_ids - later_ids),
        "_gained": sorted(gained),
        "_lost": sorted(lost),
    }


def _thin(metrics: dict) -> dict:
    return {key: value for key, value in metrics.items() if not key.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description="이슈 창 재생 · 비교")
    parser.add_argument("--windows", default="21,28,35,42",
                        help="쉼표로 구분한 창 일수")
    parser.add_argument("--as-of", required=True, help="고정 빌드 시각(ISO-8601)")
    parser.add_argument("--live-llm", action="store_true",
                        help="Gemini 키를 그대로 넘긴다. 새 쌍 판정이 필요할 때만.")
    parser.add_argument("--measure-only", action="store_true",
                        help="빌드를 다시 돌리지 않고 기존 arm 산출물만 잰다")
    parser.add_argument("--repeat", type=int, default=1,
                        help="같은 창을 몇 번 반복 실행할지(멱등성 검사)")
    parser.add_argument("--suffix", default="", help="arm 라벨 접미사")
    args = parser.parse_args()

    windows = [int(value) for value in args.windows.split(",") if value.strip()]
    results: list[dict] = []
    for window in windows:
        label = f"w{window}{args.suffix}"
        if not args.measure_only:
            info = run_build(label, window, args.as_of, live_llm=args.live_llm)
            print(f"[window_replay] {label} 빌드 완료 {info['wall_seconds']}s")
            snapshot_round(label, 1)
            for round_index in range(2, args.repeat + 1):
                run_build(label, window, args.as_of, live_llm=args.live_llm,
                          reuse_ledger=True)
                snapshot_round(label, round_index)
                print(f"[window_replay] {label} 반복 {round_index} 완료")
        row = measure(label)
        row["round_stability"] = round_stability(label, args.repeat)
        results.append(row)

    ARM_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "as_of": args.as_of,
        "live_llm": args.live_llm,
        "arms": [_thin(row) for row in results],
        "deltas": [],
    }
    for base, later in zip(results, results[1:]):
        delta = compare(base, later)
        report["deltas"].append({key: value for key, value in delta.items()
                                 if not key.startswith("_")})
        (ARM_ROOT / f"delta_{delta['from']}_to_{delta['to']}.json").write_text(
            json.dumps(delta, ensure_ascii=False, indent=1), encoding="utf-8")

    out = ARM_ROOT / f"report{args.suffix or ''}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in report["arms"]:
        print(f"  창 {row['issue_window_days']:>3}일  이슈 {row['issue_count']:>4}  "
              f"쌍 {row['pair_count']:>6}  단발 {row['single_briefing_rate']:.3f}  "
              f"span중앙 {row['span_median']:>4}  최대 {row['span_max']:>3}  "
              f"{row['wall_seconds']}s")
    for row in report["deltas"]:
        print(f"  {row['from']} → {row['to']}: 쌍 +{row['pairs_gained']} "
              f"−{row['pairs_lost']} · 이슈 {row['issue_count_delta']:+d} · "
              f"id유지 {row['issue_id_kept_rate']:.3f}")
    print(f"[window_replay] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
