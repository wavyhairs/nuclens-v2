"""보관된 판정을 완전히 가린 채 15건을 다시 판정받는다.

## 무엇을 재는가

Curation/Semantic 의 라벨은 AI 판정을 먼저 본 뒤의 승인이다(`HUMAN_REVIEWED_AI_ASSISTED`).
그 승인이 독립 판정과 얼마나 다른지를 알아야 기존 Gold 를 얼마나 살릴 수 있는지
정해진다. 전면 재라벨은 비싸고, 안 하면 Gold 전체가 근거 불명으로 남는다.
그래서 경계층 15건만 가린 채 다시 받는다.

## 무엇을 가리는가

보관 라벨과 Sol 판정만 가려서는 부족하다. **정답을 알려 주는 메타데이터**가 더 있다.

- `candidate_kind` — semantic 케이스가 어떻게 만들어졌는지. `unsupported_inference`
  는 구성부터 근거 없는 주장이라 이 한 단어가 답을 말해 준다.
- `selection_metadata_not_gold.review_focus` — 어떤 오류를 심었는지.
- `human_*`, `required_repair`, `error_types` — 보관된 판정 그 자체.

그래서 화이트리스트로 내보낸다. 블랙리스트는 필드가 하나 늘 때마다 조용히 새는데,
새는 것을 알아차릴 방법이 없다.

## 순서

층별로 묶어 보여 주면 "이건 perturbation 묶음"이라는 것이 드러난다. case id 해시로
섞어 층을 흩는다 — 무작위가 아니라 결정적이라 다시 열어도 같은 순서다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import gold_provenance
from tools.gold_labeler import atomic_write_json

SIDECAR = ROOT / ".eval" / "blind-relabel.json"
LABELS = ("PASS", "REPAIR", "BLOCK")

# 화이트리스트. 여기 없는 것은 나가지 않는다.
CURATION_SOURCE = ("title", "url", "published_at")
CURATION_OUTPUT = ("title_kr", "summary", "detail", "implication", "why_important")
SEMANTIC_FIELDS = ("claim", "source_evidence")

# 나가면 안 되는 것 — 테스트가 이 목록으로 payload 를 훑는다.
FORBIDDEN = (
    "human_label", "label_status", "human_error_types", "human_dimensions",
    "human_notes", "required_repair", "error_types", "candidate_kind",
    "selection_metadata_not_gold", "review_focus", "provisional_label",
    "confidence", "prior_llm_verdict_not_gold", "cached_review_not_gold",
    "expected_verdict", "stratum",
)


def _case(task: str, case: dict) -> dict:
    if task == "curation":
        source = case.get("source_input") or {}
        output = case.get("current_output") or {}
        return {
            "id": case["id"],
            "task": task,
            "source": {key: source.get(key) or case.get(f"source_{key}") or ""
                       for key in CURATION_SOURCE},
            "output": {key: output.get(key) or "" for key in CURATION_OUTPUT},
        }
    return {
        "id": case["id"],
        "task": task,
        "claim": case.get("claim") or "",
        "evidence": case.get("source_evidence"),
    }


def build_packet(payloads: dict[str, dict], sample: list[dict]) -> dict:
    """가려진 케이스만 담은 payload. 층 정보도 담지 않는다."""
    by_id = {task: {case["id"]: case for case in payload.get("cases") or []}
             for task, payload in payloads.items()}
    cases = [_case(item["task"], by_id[item["task"]][item["case_id"]])
             for item in sample]
    cases.sort(key=lambda case: hashlib.sha256(
        f"blind-order-v1|{case['id']}".encode("utf-8")).hexdigest())
    return {
        "labels": list(LABELS),
        "cases": cases,
        "instructions": (
            "보관된 판정과 AI 판정은 보이지 않습니다. 근거만 보고 판정하세요. "
            "판정을 마친 뒤에야 기존 라벨과 대조합니다."),
    }


def load_sidecar(path: Path = SIDECAR) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("labels") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_label(case_id: str, label: str, *, path: Path = SIDECAR) -> dict[str, str]:
    if label not in LABELS:
        raise ValueError(f"허용되지 않은 판정: {label!r}")
    labels = load_sidecar(path)
    labels[case_id] = label
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {
        "_comment": "blind 재검증 결과. canonical Gold 가 아니며 자동 승격되지 않는다.",
        "labels": labels,
    })
    return labels


def _payloads() -> dict[str, dict]:
    return {task: json.loads(
        (gold_provenance.FIXTURES / f"{task}_gold.json").read_text(encoding="utf-8"))
        for task in ("curation", "semantic")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path,
                        help="가려진 케이스를 이 경로에 쓴다")
    parser.add_argument("--compare", action="store_true",
                        help="사이드카의 blind 판정을 보관 라벨과 대조한다")
    parser.add_argument("--sidecar", type=Path, default=SIDECAR)
    args = parser.parse_args()

    payloads = _payloads()
    sample = gold_provenance.select_blind_sample(payloads)
    if args.compare:
        report = gold_provenance.compare(
            load_sidecar(args.sidecar), payloads, sample)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    packet = build_packet(payloads, sample)
    rendered = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    if args.packet:
        args.packet.write_text(rendered, encoding="utf-8")
        print(f"{len(packet['cases'])}건을 {args.packet} 에 썼습니다. "
              f"판정은 {args.sidecar} 에 기록하고 --compare 로 대조하세요.")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
