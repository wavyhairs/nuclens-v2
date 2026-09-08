"""사람이 실제로 판정을 입력하는 창구. 두 대기열을 한 자리에서 끝낸다.

## 왜 하나로 묶는가

남은 사람 작업은 성격이 다른 둘이다.

- **blind** (15건) — Curation/Semantic 라벨이 AI 판정에 얼마나 끌려갔는지 재려고,
  보관 판정을 완전히 가린 채 다시 받는다.
- **identity** (22건) — `issue_review` 와 `dedup` 의 계약이 서로 달라서 reason code
  하나로는 판정이 갈리지 않는 쌍들이다. 사람이 관계를 읽어 줘야 한다.

둘을 따로 열게 하면 앉는 자리가 둘이 된다. 분량은 합쳐 37건이라 한 번에 끝난다.

## 두 대기열의 규칙이 정반대다

blind 는 **아무 참고정보도 보여 주지 않는다.** 보관 라벨은 물론이고 케이스가 어떻게
만들어졌는지(`candidate_kind`)도 가린다 — 그 한 단어가 정답이기 때문이다.

identity 는 반대로 **판정에 필요한 근거를 다 보여 준다.** 여기서 재는 것은 anchoring
이 아니라 관계다. 다만 모델 판정과 캐시 판정은 여기서도 내보내지 않는다.

한 서버가 두 규칙을 다루므로, 대기열마다 무엇을 내보내는지 테스트로 못 박는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import blind_relabel, gold_provenance, identity_contract_map
from tools.gold_labeler import LocalThreadingHTTPServer, atomic_write_json

ASSET_DIR = Path(__file__).resolve().parent / "review_queue_assets"
SIDECAR_DIR = ROOT / ".eval"
HOST = "127.0.0.1"
DEFAULT_PORT = 8788
MAX_BODY_BYTES = 64 * 1024

IDENTITY_RELATIONS = (
    "SAME_EVENT", "FOLLOW_UP_NEW_ACTION", "FOLLOW_UP_NO_NEW_ACTION",
    "RELATED_DISTINCT_EVENT", "UNRELATED", "INSUFFICIENT",
)
# 관계 → profile 판정. 두 계약이 갈리는 축은 "후속에 새 행동이 있었나" 하나다.
RELATION_TO_VERDICT = {
    "SAME_EVENT": {"issue_review": "MERGE", "dedup": "MERGE"},
    "FOLLOW_UP_NO_NEW_ACTION": {"issue_review": "MERGE", "dedup": "MERGE"},
    # issue_review 는 단계 변화를 같은 사건으로 보고, dedup 규칙 4 는 새 행동을 가른다.
    "FOLLOW_UP_NEW_ACTION": {"issue_review": "MERGE", "dedup": "SEPARATE"},
    "RELATED_DISTINCT_EVENT": {"issue_review": "SEPARATE", "dedup": "SEPARATE"},
    "UNRELATED": {"issue_review": "SEPARATE", "dedup": "SEPARATE"},
    "INSUFFICIENT": {"issue_review": "EXCLUDE", "dedup": "EXCLUDE"},
}


def identity_review_ids(cases: list[dict]) -> list[str]:
    """어느 한 profile 이라도 reason code 로 가르지 못하는 쌍의 합집합."""
    needed: list[str] = []
    for case in cases:
        if not case.get("human_label"):
            continue
        if any(identity_contract_map.verdict(case["reason_code"], profile)
               == identity_contract_map.REVIEW
               for profile in ("issue_review", "dedup")):
            needed.append(case["id"])
    return needed


def _identity_case(case: dict) -> dict:
    """판정에 필요한 근거만. 모델·캐시 판정은 내보내지 않는다."""
    left, right = case.get("a") or {}, case.get("b") or {}
    metadata = case.get("metadata") or {}
    return {
        "id": case["id"],
        "queue": "identity",
        "left": {"title": left.get("title") or case.get("left_title") or "",
                 "publisher": left.get("publisher") or "",
                 "published_at": left.get("published_at") or "",
                 "summary": left.get("summary") or ""},
        "right": {"title": right.get("title") or case.get("right_title") or "",
                  "publisher": right.get("publisher") or "",
                  "published_at": right.get("published_at") or "",
                  "summary": right.get("summary") or ""},
        "context": {"date_gap_days": metadata.get("date_gap_days"),
                    "shared_entities": metadata.get("shared_entities") or []},
        # 왜 이 쌍이 올라왔는지는 알려 준다 — 무엇을 봐야 하는지 힌트이지 정답이 아니다.
        "why_queued": case.get("reason_code"),
    }


def build_state() -> dict:
    payloads = blind_relabel._payloads()
    blind_cases = blind_relabel.build_packet(
        payloads, gold_provenance.select_blind_sample(payloads))["cases"]
    for case in blind_cases:
        case["queue"] = "blind"

    identity_payload = json.loads(
        (gold_provenance.FIXTURES / "identity_candidates.json")
        .read_text(encoding="utf-8"))
    wanted = set(identity_review_ids(identity_payload["cases"]))
    identity_cases = [_identity_case(case) for case in identity_payload["cases"]
                      if case["id"] in wanted]
    return {
        "queues": {
            "blind": {"labels": list(blind_relabel.LABELS),
                      "note": "보관된 판정과 AI 판정은 보이지 않습니다. "
                              "근거만 보고 판정하세요."},
            "identity": {"labels": list(IDENTITY_RELATIONS),
                         "note": "두 기사의 **관계**를 고르세요. MERGE/SEPARATE 는 "
                                 "profile 계약이 정합니다."},
        },
        "cases": blind_cases + identity_cases,
        "labels": {**load(("blind",)), **load(("identity",))},
    }


def sidecar_path(queue: str) -> Path:
    return SIDECAR_DIR / f"{queue}-review.json"


def load(queues=("blind", "identity")) -> dict[str, str]:
    labels: dict[str, str] = {}
    for queue in queues:
        try:
            stored = json.loads(sidecar_path(queue).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        labels.update(stored.get("labels") or {})
    return labels


def save(queue: str, case_id: str, label: str) -> dict[str, str]:
    allowed = (blind_relabel.LABELS if queue == "blind" else IDENTITY_RELATIONS)
    if label not in allowed:
        raise ValueError(f"{queue}: 허용되지 않은 판정 {label!r}")
    path = sidecar_path(queue)
    labels = load((queue,))
    labels[case_id] = label
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {
        "_comment": ("사람 판정 사이드카. canonical Gold 가 아니며 자동 승격되지 "
                     "않는다. .gitignore 대상이므로 따로 챙길 것."),
        "queue": queue,
        "labels": labels,
    })
    return labels


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "NuclensReviewQueue/1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[review-queue] {fmt % args}")

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _asset(self, name: str, content_type: str) -> None:
        path = ASSET_DIR / name
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib 계약
        path = urlparse(self.path).path
        if path == "/":
            self._asset("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._asset("app.js", "text/javascript; charset=utf-8")
        elif path == "/api/state":
            self._json(build_state())
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib 계약
        if urlparse(self.path).path != "/api/label":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("본문 크기가 잘못됨")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            labels = save(payload["queue"], payload["case_id"], payload["label"])
        except Exception as exc:  # noqa: BLE001 — 사용자에게 사유를 그대로 보인다
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._json({"ok": True, "labelled": len(labels)})


def create_server(port: int = DEFAULT_PORT) -> LocalThreadingHTTPServer:
    return LocalThreadingHTTPServer((HOST, port), ReviewHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    state = build_state()
    server = create_server(args.port)
    url = f"http://{HOST}:{args.port}/"
    print(f"[review-queue] {len(state['cases'])}건 대기 · {url}")
    print(f"[review-queue] 판정 저장 위치: {sidecar_path('blind')} / "
          f"{sidecar_path('identity')}")
    print("[review-queue] 중지: Ctrl+C")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[review-queue] 중지했습니다. 진행 상황은 사이드카에 남아 있습니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
