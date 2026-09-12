"""창 확대가 만든 연결을 **독립 모델이 가린 채** 다시 판정하게 한다.

## 왜 새 어휘를 만들지 않는가

`tools/review_queue.py` 에 이미 있다.

    SAME_EVENT · FOLLOW_UP_NEW_ACTION · FOLLOW_UP_NO_NEW_ACTION
    RELATED_DISTINCT_EVENT · UNRELATED · INSUFFICIENT

    RELATION_TO_VERDICT["FOLLOW_UP_NEW_ACTION"]
        = {"issue_review": "MERGE", "dedup": "SEPARATE"}

마지막 줄이 중요하다. **same_event 와 same_story 를 가르는 축이 이미 코드에 있다** —
"후속에 새 행동이 있었나". `same/different/uncertain` 3값으로 물으면 그 축이 뭉개지고,
F 가 쓸 재료가 사라진다. 그래서 판정은 이 어휘로 받고, F 를 위해 한 칸만 더 묻는다
(`same_thread`: 두 사건이 같은 장기 스토리에 속하는가).

## 무엇을 가리는가

화이트리스트로 내보낸다. 블랙리스트는 필드가 하나 늘 때마다 조용히 새는데, 새는
것을 알아차릴 방법이 없다 — `tools/blind_relabel.py` 가 같은 이유로 같은 선택을
했다.

가리는 것:

* production 판정(`same_event`) · 그 이유 · 모델명 · `prompt_version`
* 코사인 유사도 — 그것 자체가 production 의 신호다
* `llm_approved` / `issue_match_overrides` 여부
* 두 기사가 실제로 같은 이슈에 묶였는지
* 사람이 이미 붙인 라벨

내보내는 것: 제목 · 날짜 · 요약 · 매체 · 주제 · 국가 · 지문의 구조화 사실
(`actors` · `assets` · `event_family` · `action` · `drivers`).

## 순서를 도구가 강제한다

`build` 가 blind 파일을 쓸 때 **정답 파일을 함께 쓰지 않는다.** 정답은 `join` 이
그때 가서 원본에서 다시 읽는다. 그래서 판정자가 어떤 경로로도 정답에 먼저 닿을 수
없다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import issue_review  # noqa: E402
from tools.review_queue import IDENTITY_RELATIONS, RELATION_TO_VERDICT  # noqa: E402

ARM_ROOT = ROOT / "web" / "_windows"
EVAL_DIR = ROOT / "eval_artifacts"
BLIND_FILE = EVAL_DIR / "event_pairs_blind.jsonl"
JUDGMENT_FILE = EVAL_DIR / "event_judgments_claude.jsonl"
AGREEMENT_FILE = EVAL_DIR / "event_judge_agreement.json"

# 판정자가 고를 수 있는 값. review_queue 의 것을 그대로 쓴다.
RELATIONS = IDENTITY_RELATIONS

# 지문에서 내보내는 축. `story_fingerprint.AXES` 의 별칭까지 받는다.
FINGERPRINT_FIELDS = {
    "actors": ("actors", "actor", "operator", "organization"),
    "assets": ("assets", "asset", "facility", "project", "plant"),
    "event_family": ("event_family", "event_type", "event"),
    "action": ("action", "decision", "stage"),
    "drivers": ("drivers", "driver", "cause"),
    "countries": ("countries", "country"),
}


def _first(source: object, names: tuple[str, ...]) -> object:
    if not isinstance(source, dict):
        return ""
    for name in names:
        value = source.get(name)
        if value:
            return value
    return ""


def _article_view(article: dict) -> dict:
    """판정에 필요한 원자료만. 여기 없는 것은 나가지 않는다."""
    fingerprint = article.get("story_fingerprint") or {}
    return {
        "title": str(article.get("title_kr") or article.get("title") or ""),
        "date": str(article.get("article_date") or "")[:10],
        "publisher": str(article.get("publisher") or ""),
        "summary": str(article.get("summary") or article.get("detail") or "")[:600],
        "topics": list(article.get("topics") or [])[:6],
        "countries": list(article.get("countries") or [])[:4],
        "facts": {key: _first(fingerprint, names)
                  for key, names in FINGERPRINT_FIELDS.items()},
    }


def index_articles(labels: list[str]) -> dict[str, dict]:
    """arm 산출물에서 해시 → 기사. 넓은 창일수록 많이 들고 있으므로 다 훑는다."""
    out: dict[str, dict] = {}
    for label in labels:
        path = ARM_ROOT / label / "data" / "issues.json"
        if not path.exists():
            continue
        for issue in json.loads(path.read_text(encoding="utf-8")):
            rows = [issue.get("representative_article") or {}]
            rows.extend(issue.get("related_articles") or [])
            for row in rows:
                key = str(row.get("hash") or "")
                if key and key not in out:
                    out[key] = row
    return out


def _stable_order(candidate_id: str) -> str:
    """층을 흩는다. 무작위가 아니라 결정적이라 다시 열어도 같은 순서다."""
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()


def collect_pairs(deltas: list[Path], *, cap_per_delta: int, seed: int) -> list[dict]:
    picked: dict[str, dict] = {}
    for path in deltas:
        payload = json.loads(path.read_text(encoding="utf-8"))
        gained = list(payload.get("_gained") or [])
        stratum = f"{payload.get('from')}→{payload.get('to')}"
        if cap_per_delta and len(gained) > cap_per_delta:
            # 층화 표본. **창끼리 같은 규칙**이어야 비교가 성립한다.
            rng = random.Random(f"{seed}:{stratum}")
            gained = sorted(rng.sample(gained, cap_per_delta))
        for candidate_id in gained:
            picked.setdefault(candidate_id, {"candidate_id": candidate_id,
                                             "stratum": stratum})
    return list(picked.values())


def collect_known_edges() -> list[dict]:
    """사람이 이미 판정한 쌍. **세 번째 축**이다 —

    두 모델의 일치는 정확도가 아니다. 같은 방식으로 틀릴 수 있다. 사람 라벨이
    섞여 있어야 일치율이 무엇을 뜻하는지 말할 수 있다.
    """
    path = ROOT / ".eval" / "identity-review.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"candidate_id": key, "stratum": "human_labeled"}
            for key in (payload.get("labels") or {})]


def build(args) -> int:
    labels = [value.strip() for value in args.arms.split(",") if value.strip()]
    articles = index_articles(labels)
    deltas = sorted(ARM_ROOT.glob("delta_*.json"))
    rows = collect_pairs(deltas, cap_per_delta=args.cap, seed=args.seed)
    rows.extend(collect_known_edges())

    out: list[dict] = []
    missing = 0
    for row in rows:
        left_hash, _, right_hash = row["candidate_id"].partition("--")
        left, right = articles.get(left_hash), articles.get(right_hash)
        if not left or not right:
            missing += 1
            continue
        out.append({
            "candidate_id": row["candidate_id"],
            "stratum": row["stratum"],
            "a": _article_view(left),
            "b": _article_view(right),
        })
    out.sort(key=lambda row: _stable_order(row["candidate_id"]))

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with BLIND_FILE.open("w", encoding="utf-8") as handle:
        for row in out:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    strata = Counter(row["stratum"] for row in out)
    print(f"[blind] {len(out)}쌍 → {BLIND_FILE.relative_to(ROOT)}  "
          f"(기사 못 찾음 {missing})")
    for name, count in sorted(strata.items()):
        print(f"    {name}: {count}")
    return 0


# ── join ────────────────────────────────────────────────────────────────────

def production_verdicts(candidate_ids: set[str]) -> dict[str, dict]:
    """production 판정을 **판정이 끝난 뒤에** 원본에서 읽는다.

    arm 별 캐시를 합쳐서 본다 — 넓은 창에서만 물어본 쌍이 있기 때문이다.
    """
    out: dict[str, dict] = {}
    sources = [ROOT / "issue_llm_reviews.json"]
    sources.extend(sorted(ARM_ROOT.glob("*/issue_llm_reviews.json")))
    for path in sources:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key, entry in (payload.get("reviews") or {}).items():
            if key in candidate_ids and isinstance(entry, dict):
                if entry.get("prompt_version") != issue_review.PROMPT_VERSION:
                    continue
                out[key] = entry
    return out


def human_labels() -> dict[str, str]:
    path = ROOT / ".eval" / "identity-review.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: str(value) for key, value in (payload.get("labels") or {}).items()}


def _merges(relation: str) -> bool | None:
    """이 관계가 `issue_review` 계약에서 병합인가. production 과 같은 축으로 맞춘다."""
    verdict = (RELATION_TO_VERDICT.get(relation) or {}).get("issue_review")
    if verdict == "MERGE":
        return True
    if verdict == "SEPARATE":
        return False
    return None


def join(args) -> int:
    judgments = {}
    with JUDGMENT_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            judgments[str(row["candidate_id"])] = row
    blind = {}
    with BLIND_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                blind[row["candidate_id"]] = row

    gemini = production_verdicts(set(judgments))
    humans = human_labels()

    buckets = Counter()
    by_stratum: dict[str, Counter] = {}
    axes = Counter()
    disagreements: list[dict] = []
    human_rows: list[dict] = []
    for key, row in judgments.items():
        relation = str(row.get("relation") or "")
        claude = _merges(relation)
        axes[str(row.get("difference_axis") or relation)] += 1
        stratum = (blind.get(key) or {}).get("stratum", "?")
        counter = by_stratum.setdefault(stratum, Counter())

        entry = gemini.get(key)
        if entry is None:
            bucket = "gemini_absent"
        elif claude is None:
            bucket = "claude_uncertain"
        elif bool(entry.get("same_event")) == claude:
            bucket = "agreement_same" if claude else "agreement_different"
        else:
            bucket = "disagreement"
            disagreements.append({
                "candidate_id": key, "stratum": stratum,
                "gemini_same_event": bool(entry.get("same_event")),
                "gemini_reason": str(entry.get("reason") or ""),
                "claude_relation": relation,
                "claude_reason": str(row.get("reason") or ""),
                "claude_axis": str(row.get("difference_axis") or ""),
                "a_title": (blind.get(key) or {}).get("a", {}).get("title", ""),
                "b_title": (blind.get(key) or {}).get("b", {}).get("title", ""),
            })
        buckets[bucket] += 1
        counter[bucket] += 1

        if key in humans:
            human_relation = humans[key]
            human_rows.append({
                "candidate_id": key,
                "human": human_relation,
                "claude": relation,
                "gemini_same_event": None if entry is None else bool(entry.get("same_event")),
                "claude_matches_human": relation == human_relation,
                "gemini_matches_human": (
                    None if entry is None
                    else bool(entry.get("same_event")) == _merges(human_relation)),
            })

    judged = sum(buckets[name] for name in
                 ("agreement_same", "agreement_different", "disagreement"))
    report = {
        "judged": len(judgments),
        "comparable": judged,
        "buckets": dict(buckets),
        "agreement_rate": round(
            (buckets["agreement_same"] + buckets["agreement_different"]) / judged, 4
        ) if judged else 0,
        "by_stratum": {name: dict(counter) for name, counter in sorted(by_stratum.items())},
        "claude_relations": dict(Counter(str(row.get("relation")) for row in judgments.values())),
        "difference_axes": dict(axes.most_common()),
        "human_cross_check": {
            "n": len(human_rows),
            "claude_matches_human": sum(1 for row in human_rows if row["claude_matches_human"]),
            "gemini_matches_human": sum(1 for row in human_rows
                                        if row["gemini_matches_human"] is True),
            "rows": human_rows,
        },
        "same_thread": dict(Counter(str(row.get("same_thread")) for row in judgments.values())),
        "disagreements": disagreements[:80],
        "disagreement_total": len(disagreements),
    }
    AGREEMENT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("judged", "comparable", "buckets", "agreement_rate",
                       "difference_axes", "same_thread")},
                     ensure_ascii=False, indent=1))
    print(f"[join] → {AGREEMENT_FILE.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Event 독립 판정 — blind 생성·대조")
    sub = parser.add_subparsers(dest="command", required=True)

    builder = sub.add_parser("build", help="blind 데이터셋을 만든다")
    builder.add_argument("--arms", default="w21,w28,w35,w42")
    builder.add_argument("--cap", type=int, default=250,
                         help="delta 하나당 최대 표본. 0 이면 전수")
    builder.add_argument("--seed", type=int, default=20260913)
    builder.set_defaults(func=build)

    joiner = sub.add_parser("join", help="판정을 production 결과와 대조한다")
    joiner.set_defaults(func=join)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
