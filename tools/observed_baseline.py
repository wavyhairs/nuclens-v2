"""각 production callsite 가 **실제로 직렬화하는** 요청 본문을 추출한다.

왜 이 도구가 필요한가.

`llm_policy` 만 읽으면 모든 profile 이 `thinking_level=None` 이라 baseline 이 전부
"unspecified" 로 보인다. 그런데 호출자가 그 위에 한 겹을 더 얹는다 —
`expert_audio_brief.py` 와 `audio_brief.py` 는 `thinking_budget=(0 if
policy.thinking_level is None else None)` 을 붙인다. 그리고 `gemini_client` 는
`gemini-3.5-flash-lite` 가 `thinkingBudget: 0` 을 거부하므로 그 모델에서만 필드를
생략한다. 결과적으로 **같은 코드가 모델에 따라 explicit OFF 도 되고 필드 없음도
된다.**

그래서 baseline 을 `"unspecified"` 같은 이름으로 정의하면 안 된다. reasoning 비교의
baseline arm 은 이 도구가 뽑은 **본문 그 자체**여야 한다. 이름으로 두면 존재하지도
않는 baseline 과 비교하게 된다.

추출은 HTTP 경계에서 한다. `urlopen` 을 가로채면 production 코드를 한 줄도 바꾸지
않고 실제로 나갈 뻔한 본문을 그대로 본다 — "코드를 읽어 보니 이럴 것이다"가 아니라
직렬화된 사실이다. 네트워크로는 나가지 않는다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gemini_client


class _Intercepted(Exception):
    """요청을 잡았다는 신호. 네트워크로 나가기 직전에 올린다."""

    def __init__(self, url: str, body: dict):
        super().__init__("intercepted")
        self.url = url
        self.body = body


def _probe(thunk) -> dict | None:
    """thunk 을 돌리다 첫 요청 직렬화 시점에 멈추고 그 본문을 돌려준다."""
    real_urlopen = urllib.request.urlopen
    captured: dict = {}

    def fake_urlopen(req, *args, **kwargs):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        raise _Intercepted(captured["url"], captured["body"])

    saved_key = gemini_client.API_KEY
    urllib.request.urlopen = fake_urlopen
    gemini_client.API_KEY = gemini_client.API_KEY or "offline-baseline-probe"
    try:
        thunk()
    except Exception:  # noqa: BLE001 — 어떤 경로로 삼켜지든 본문만 있으면 된다
        pass
    finally:
        urllib.request.urlopen = real_urlopen
        gemini_client.API_KEY = saved_key
    return captured or None


def _summarize(url: str, body: dict) -> dict:
    """결정에 쓰는 값만 남긴다. 프롬프트 전문은 해시로 접는다."""
    config = body.get("generationConfig") or {}
    thinking = config.get("thinkingConfig")
    if thinking is None:
        observed = "absent"
    elif "thinkingLevel" in thinking:
        observed = f"level:{thinking['thinkingLevel']}"
    else:
        observed = f"budget:{thinking.get('thinkingBudget')}"
    system = "".join(part.get("text", "") for part
                     in (body.get("system_instruction") or {}).get("parts") or [])
    return {
        "model": url.rsplit("/", 1)[-1].split(":")[0],
        "observed_baseline_thinking": observed,
        "thinking_config": copy.deepcopy(thinking),
        "temperature": config.get("temperature"),
        "max_output_tokens": config.get("maxOutputTokens"),
        "response_mime_type": config.get("responseMimeType"),
        "response_json_schema_sha": (
            _sha(json.dumps(config["responseJsonSchema"], sort_keys=True))
            if "responseJsonSchema" in config else None),
        "system_prompt_sha": _sha(system),
        "generation_config_keys": sorted(config),
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── callsite 드라이버 ───────────────────────────────────────────────────────
#
# generationConfig 는 호출 인자로만 정해지고 메시지 내용에 좌우되지 않으므로,
# 드라이버는 각 callsite 를 **실제 함수 그대로** 최소 입력으로 한 번 태우면 된다.
# 프롬프트 자체는 sha 로만 남기고 비교는 P2 fidelity 가 맡는다.

_ARTICLE = {
    "hash": "0" * 64, "title": "t", "description": "d",
    "link": "https://example.com/a", "domain": "example.com", "publisher": "p",
}


def _drivers() -> dict[str, object]:
    import audio_brief
    import dedup
    import expert_audio_brief
    import issue_review
    import keei_match
    import news_bot
    import semantic_verifier

    def expert(label: str, primary: str):
        # ``primary`` 는 실제 호출부가 넘기는 값이어야 한다. 이것이 모델 사다리의
        # **첫 단**을 정하고, 첫 단이 3.5-flash-lite 면 `thinkingBudget: 0` 이
        # 모델 제약으로 생략된다 — 즉 같은 코드가 여기서 explicit OFF 와 필드 없음
        # 으로 갈린다. 기본값으로 두면 그 갈림을 통째로 놓친다.
        return lambda: expert_audio_brief._call_structured(
            "s", "m", label=label, primary=primary)

    two = [dict(_ARTICLE), {**_ARTICLE, "hash": "1" * 64}]
    scores = {article["hash"]: 1.0 for article in two}
    tmp = ROOT / ".eval" / "baseline-probe"
    tmp.mkdir(parents=True, exist_ok=True)

    return {
        "curation": lambda: news_bot.curate_batch(
            [dict(_ARTICLE)], [], log_path=tmp / "discard.jsonl"),
        "dedup": lambda: dedup.dedup_articles(list(two), scores),
        "dedup_final": lambda: dedup.editorial_dedup_articles(list(two), scores),
        # 회색지대(REVIEW_BAND_LOW~HIGH) 밖 후보는 select_pairs 가 걸러 내므로
        # 밴드 안 유사도를 준다. 그렇지 않으면 요청이 아예 만들어지지 않는다.
        "issue_review": lambda: issue_review.review_pairs(
            [{"candidate_id": "c", "left_title": "l", "right_title": "r",
              "diagnostics": {"embedding_similarity": 0.88},
              "embedding_similarity": 0.88}],
            cache_path=tmp / "ir.json", client=gemini_client),
        "keei_match": lambda: keei_match.match_pairs(
            [{"pair_id": "p", "issue_title": "i", "keei_item": "k"}],
            cache_path=tmp / "km.json", client=gemini_client),
        "expert_dossiers": expert("expert_dossiers", "curation"),
        "expert_plan": expert("expert_plan", "synthesis"),
        "expert_script": expert("expert_script", "synthesis"),
        "expert_repair": expert("expert_repair", "synthesis"),
        "expert_verify": expert("expert_verify", "curation"),
        "audio_brief": lambda: audio_brief._call_script("m"),
        "fast_verify": lambda: semantic_verifier.verify({}, "HOST: x"),
    }


def audit() -> dict:
    rows: dict[str, object] = {}
    for name, thunk in _drivers().items():
        captured = _probe(thunk)
        rows[name] = (_summarize(captured["url"], captured["body"])
                      if captured else {"error": "no request was serialized"})
    return {"baseline_probe_version": 1, "callsites": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = json.dumps(audit(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
