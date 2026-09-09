"""녹화된 응답으로 production orchestration 을 그대로 돌려 fidelity 를 증명한다.

## 무엇을 증명하는가

"같은 prompt 를 쓴다"도 "production adapter 를 쓴다"도 증명이 아니다. 증명은
**production 이 실제로 보냈던 요청 열이 그대로 재현되는가**이다. 그래서 이 모듈은
별도 replay 구현을 만들지 않는다. `curate_batch` / `dedup_articles` 를 그대로
부르고, 대조는 HTTP 경계에서 한다.

대조 지점을 HTTP 로 잡은 이유가 있다. `client=` 이음매에서 (system, user, kwargs)
를 비교하면 `gemini_client` 가 그 인자를 본문으로 바꾸는 과정이 검증 밖에 남는다 —
`thinkingBudget: 0` 이 모델 때문에 생략되는 것 같은 일이 바로 거기서 일어난다.
직렬화된 본문을 비교하면 그 층까지 함께 잡힌다.

## 입력 재구성도 같이 검증된다

replay 를 돌리려면 그때의 입력(기사 목록 등)이 필요하다. 캡처에는 프롬프트만 있고
원본 dict 는 없으므로 호출자가 입력을 재구성해 넣어야 한다. 그런데 재구성이
틀리면 프롬프트가 달라지고 본문 대조가 실패한다 — 즉 **요청 일치가 입력 재구성의
정확성까지 동시에 증명한다.** 그래서 이 하네스는 "입력이 맞다"를 전제하지 않고
판정 결과로 돌려준다.

## 캡처의 한계 (판정에 반영할 것)

`gemini_client._capture` 는 HTTP 200 payload 를 받은 뒤에 기록한다. 429/5xx 로
실패한 시도는 별도 줄로 남지 않고 `detail.retry_count` 에만 흔적이 있다. 따라서
재현되는 것은 **성공한 요청 열**이다. 재시도 횟수까지 재현하려면 그 값을 따로 본다.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gemini_client

PROVEN = "REPLAY_FIDELITY_PROVEN"
NOT_PROVEN = "REPLAY_FIDELITY_NOT_PROVEN"

# 한 callsite 가 여러 label 을 쓴다. 재생성은 같은 orchestration 안의 두 번째
# 호출이므로 같은 묶음으로 본다 — 재생성 횟수 자체가 재현 대상이기 때문이다.
CALLSITE_LABELS = {
    "curation": ("curation", "curation:재생성"),
    "dedup": ("dedup",),
    "dedup_final": ("dedup_final",),
    "issue_review": ("issue_review",),
    "keei_match": ("keei_match",),
}


def load_capture(path: Path) -> list[dict]:
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(records, key=lambda row: row.get("seq", 0))


def select(records: list[dict], callsite: str) -> list[dict]:
    labels = CALLSITE_LABELS.get(callsite)
    if labels is None:
        raise ValueError(f"unknown callsite: {callsite}")
    return [row for row in records
            if (row.get("detail") or {}).get("task") in labels]


def _diff(expected, actual, path: str = "") -> list[str]:
    """어디가 다른지 경로 단위로 알려 준다.

    "다르다"만 알려 주면 사람이 두 본문을 눈으로 맞춰야 한다. 프롬프트는 수천 자라
    그 방법으로는 원인을 못 찾는다.
    """
    if type(expected) is not type(actual):
        return [f"{path or '<root>'}: type {type(expected).__name__} != "
                f"{type(actual).__name__}"]
    if isinstance(expected, dict):
        out = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            if key not in expected:
                out.append(f"{child}: only in replay")
            elif key not in actual:
                out.append(f"{child}: only in recording")
            else:
                out.extend(_diff(expected[key], actual[key], child))
        return out
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [f"{path}: length {len(expected)} != {len(actual)}"]
        return [line for index, (left, right) in enumerate(zip(expected, actual))
                for line in _diff(left, right, f"{path}[{index}]")]
    if expected != actual:
        return [f"{path}: {_short(expected)} != {_short(actual)}"]
    return []


def _short(value, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return repr(text)
    head, tail = text[: limit // 2], text[-limit // 4:]
    return f"{head!r}…(+{len(text) - limit}자)…{tail!r}"


class _RecordedResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class RecordedTransport:
    """녹화된 응답을 순서대로 돌려주고, 나가는 본문을 녹화본과 대조한다.

    대조에 실패해도 곧바로 멈추지 않고 녹화된 응답을 그대로 돌려준다. 첫 불일치에서
    끊으면 그 뒤의 orchestration(재생성·분할·격리)이 아예 실행되지 않아, 한 번에
    하나씩만 고치게 된다. 판정은 어차피 불일치 하나로도 NOT_PROVEN 이다.
    """

    def __init__(self, records: list[dict], *, ignore_thinking: bool = False):
        self.records = records
        self.ignore_thinking = ignore_thinking
        self.sent: list[dict] = []
        self.mismatches: list[dict] = []
        self.overflow = 0
        self._saved = None

    # ── 컨텍스트 ────────────────────────────────────────────────────────────
    def __enter__(self):
        self._saved = urllib.request.urlopen
        urllib.request.urlopen = self._urlopen
        return self

    def __exit__(self, *exc):
        urllib.request.urlopen = self._saved
        return False

    # ── 대조 ────────────────────────────────────────────────────────────────
    def _expected_body(self, index: int) -> dict | None:
        if index >= len(self.records):
            return None
        return self.records[index].get("request_body")

    def _normalize(self, body: dict) -> dict:
        body = copy.deepcopy(body)
        if self.ignore_thinking:
            # reasoning 비교 arm 을 대조할 때만 쓴다. thinkingConfig 는 **의도적으로**
            # 다른 유일한 축이므로 그것만 빼고 나머지가 같은지를 본다.
            (body.get("generationConfig") or {}).pop("thinkingConfig", None)
        return body

    def _urlopen(self, req, *args, **kwargs):
        index = len(self.sent)
        actual = json.loads(req.data.decode("utf-8"))
        self.sent.append(actual)
        expected = self._expected_body(index)
        if expected is None:
            self.overflow += 1
            raise gemini_client.GeminiError(
                f"replay asked for call #{index + 1} but the recording has "
                f"{len(self.records)}")
        differences = _diff(self._normalize(expected), self._normalize(actual))
        if differences:
            self.mismatches.append({"call_index": index, "differences": differences})
        return _RecordedResponse(
            json.dumps(self.records[index]["response"]).encode("utf-8"))

    # ── 판정 ────────────────────────────────────────────────────────────────
    def report(self) -> dict:
        missing = len(self.records) - len(self.sent)
        status = PROVEN if (not self.mismatches and missing == 0
                            and self.overflow == 0) else NOT_PROVEN
        return {
            "status": status,
            "recorded_calls": len(self.records),
            "replayed_calls": len(self.sent),
            "unreplayed_recorded_calls": max(0, missing),
            "extra_replay_calls": self.overflow,
            "mismatched_calls": len(self.mismatches),
            "mismatches": self.mismatches[:20],
            "recorded_labels": dict(Counter(
                (row.get("detail") or {}).get("task") for row in self.records)),
            "recorded_retries": sum(int((row.get("detail") or {}).get("retry_count") or 0)
                                    for row in self.records),
        }


def verify(records: list[dict], driver, *, ignore_thinking: bool = False) -> dict:
    """driver 를 녹화 위에서 돌리고 fidelity 판정을 돌려준다.

    driver 는 production orchestration 을 그대로 부르는 무인자 콜러블이어야 한다.
    여기서 production 함수를 복제하면 이 하네스가 증명하려던 것이 사라진다.
    """
    calls_before = len(gemini_client._CALL_LOG)
    saved_key = gemini_client.API_KEY
    gemini_client.API_KEY = gemini_client.API_KEY or "offline-replay"
    try:
        with RecordedTransport(records, ignore_thinking=ignore_thinking) as transport:
            try:
                result = driver()
            except Exception as exc:  # noqa: BLE001 — 판정에 실을 사실이다
                result = {"driver_error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        gemini_client.API_KEY = saved_key
    report = transport.report()
    report["production_result"] = result
    # 녹화 위에서 돌았다면 이 값은 replay 호출 수와 같아야 한다. 어긋나면 어딘가가
    # transport 를 우회한 것이다.
    report["call_log_delta"] = len(gemini_client._CALL_LOG) - calls_before
    return report


# ── production orchestration 드라이버 ───────────────────────────────────────
#
# 각 드라이버는 production 함수를 **그대로** 부른다. 부작용 경로만 임시 위치로
# 돌린다 — 그건 재현 대상이 아니라 격리 대상이다.


def curation_driver(articles: list[dict], reports_kb: list[dict],
                    bodies: dict[str, str] | None, log_dir: Path):
    import news_bot

    return lambda: news_bot.curate_batch(
        articles, reports_kb, bodies=bodies,
        log_path=log_dir / "delivery_log.jsonl")


def dedup_driver(articles: list[dict], scores: dict[str, float], *, stage: str):
    import dedup

    call = (dedup.editorial_dedup_articles if stage == "dedup_final"
            else dedup.dedup_articles)
    return lambda: call(list(articles), scores)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--callsite", choices=sorted(CALLSITE_LABELS))
    args = parser.parse_args()
    records = load_capture(args.capture)
    if args.callsite:
        records = select(records, args.callsite)
    # 입력 재구성 없이는 replay 를 돌릴 수 없다. 그래서 CLI 는 무엇이 녹화돼
    # 있는지만 보여 준다 — 여기서 임의 입력을 지어내면 그 순간 fidelity 가 거짓이 된다.
    print(json.dumps({
        "capture": str(args.capture),
        "records": len(records),
        "labels": dict(Counter((row.get("detail") or {}).get("task")
                               for row in records)),
        "models": dict(Counter((row.get("detail") or {}).get("model")
                               for row in records)),
        "requested_thinking": dict(Counter(
            (row.get("detail") or {}).get("requested_thinking") for row in records)),
        "note": "replay 는 그때의 입력 재구성이 필요하다. tools/recorded_replay.verify() 참조.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
