"""TTS 안내문 후보를 같은 대본으로 번갈아 불러 400 비율과 음성 품질을 잰다.

왜 있는가 (2026-10-04): 폴백 `gemini-2.5-flash-preview-tts` 가 실제로 쓰인 날마다
"Model tried to generate text, but it should only be used for TTS" 400 을 받았다
(09-09·09-30·10-04). 같은 요청이 다음 번엔 통과하는 표집 실패라 #234 에서
재시도로 받았지만, 근본은 대본 앞에 붙는 안내문(audio_brief.STYLE_INSTRUCTION)을
2.5 가 '글을 써 달라' 로 읽는 데 있다고 본다. 안내문을 바꿔서 줄어드는지는 실제
API 로만 알 수 있다 — force_audio 는 기본 모델(3.1)로 먼저 만들어 2.5 를 거의 안
부르고, 사이트 배포까지 다시 돈다. 그래서 이 도구는 **모델 하나만, 재시도 없이**
부르고 결과만 남긴다. 발송·배포·커밋은 하지 않는다.

판정:
- HTTP 코드별 횟수 (400 표집 실패가 핵심 지표)
- 성공분의 잘림(대사 대비 길이)·자기유사도 되풀이·받아쓰기 대조
  — 400 은 없어졌는데 안내문을 소리 내어 읽거나(받아쓰기 ratio 상승) 대본을
  건너뛰면(누락) 그 후보는 쓸 수 없다.

    python tools/tts_probe.py --model gemini-2.5-flash-preview-tts --rounds 6 \\
        --site-url https://nuclens-v2.pages.dev --out probe/
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import audio_brief  # noqa: E402
import audio_verify  # noqa: E402
import gemini_client  # noqa: E402

# 후보 안내문. 'current' 는 운영값 그대로다 — 비교 기준.
VARIANTS: dict[str, str] = {
    "current": audio_brief.STYLE_INSTRUCTION,
    # 서술문('…브리핑입니다', '…말합니다')을 빼고 명령 한 문장만 남긴다.
    "short_ko": "차분하고 신뢰감 있는 아침 라디오 진행자 목소리로 다음 원고를 그대로 소리 내어 읽으세요:\n\n",
    # 영어 지시 + 한국어 원고. Gemini TTS 문서의 예시('Say cheerfully: …') 모양.
    "short_en": ("Read the following Korean transcript aloud exactly as written, "
                 "in a calm, trustworthy morning-radio voice:\n\n"),
    # 안내문 없이 원고만. 지시가 없으면 '글을 쓸' 이유도 없다는 가설.
    "none": "",
}

SAMPLE_FALLBACK = (
    "HOST: 10월 4일 일요일 Nuclens 전문가 브리핑입니다.\n"
    "HOST: 한미 양국은 223억 달러 규모의 텍사스 가스 복합 발전소 건설을 확정했습니다. "
    "세부 기술 운영 측면에서는 미국형 AP1000과 한국형 APR1400 노형의 혼합 건설이 검토되고 있습니다.\n"
    "HOST: 다음 소식은 전략수출금융기금 법안 통과를 위한 정부의 움직임입니다. "
    "정부는 상생기여금 요율을 낮추는 방안을 검토 중입니다."
)


# Cloudflare Pages 는 Python-urllib 기본 User-Agent 를 403 으로 거절한다
# (tools/daily_brief_trigger_gate.py 의 같은 함정). 첫 실행(10-04)이 이걸 몰라
# 모든 대본을 못 받고 223자 내장 표본으로 돌았다 — 운영 청크(~900자)와 달라
# 400 비교에 쓸 수 없었다.
LIVE_USER_AGENT = "nuclens-smoke/1.0"   # 스모크 검사·오디오 복구 게이트와 같은 값 — 통과가 확인된 UA


def fetch_script(site_url: str, today: datetime, days: int = 4) -> tuple[str, str]:
    """라이브 사이트의 최근 대본(전문가 우선)을 가져온다. 없으면 내장 표본."""
    base = site_url.rstrip("/")
    for back in range(days):
        date = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        for kind in ("expert", "fast"):
            url = f"{base}/data/audio/script-{kind}-{date}.txt?cb={int(time.time())}"
            request = urllib.request.Request(url, headers={"User-Agent": LIVE_USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    text = response.read().decode("utf-8")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"[probe] {url.split('?')[0]} 못 받음 — {exc}")
                continue
            if audio_brief.SPEAKER_RE.search(text.splitlines()[0] if text else ""):
                return text, f"script-{kind}-{date}.txt"
            print(f"[probe] {url.split('?')[0]} 는 대본 모양이 아니다 — 건너뜀")
    print("::warning::라이브 대본을 하나도 못 받았다 — 내장 표본(짧다)으로 돈다. "
          "운영 청크와 길이가 달라 400 비교 근거로는 약하다")
    return SAMPLE_FALLBACK, "builtin-sample"


def call_once(model: str, chunk: str, instruction: str) -> dict:
    """재시도 없이 한 번. 운영과 같은 요청 모양(audio_brief.tts_payload)."""
    url = audio_brief._TTS_ENDPOINT.format(model=model)
    request = urllib.request.Request(
        url,
        data=json.dumps(audio_brief.tts_payload(chunk, instruction=instruction)).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": gemini_client.API_KEY or ""},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read())
        part = payload["candidates"][0]["content"]["parts"][0]
        pcm = base64.b64decode(part["inlineData"]["data"])
        rate = 24000
        mime = part["inlineData"].get("mimeType", "")
        if "rate=" in mime:
            rate = int(mime.split("rate=")[1].split(";")[0])
        return {"status": "ok", "code": 200, "pcm": pcm, "rate": rate,
                "elapsed": round(time.monotonic() - started, 1)}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = ("text_sampling_400" if exc.code == 400
                  and audio_brief._TTS_TEXT_SAMPLING_MARKER in body else f"http_{exc.code}")
        return {"status": status, "code": exc.code, "detail": body[:300],
                "elapsed": round(time.monotonic() - started, 1)}
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        return {"status": "bad_shape", "code": 200, "detail": f"{type(exc).__name__}: {exc}",
                "elapsed": round(time.monotonic() - started, 1)}
    except Exception as exc:  # 연결 끊김 등 — 시험 도구는 끝까지 돈다
        return {"status": "conn_error", "code": 0, "detail": f"{type(exc).__name__}: {exc}",
                "elapsed": round(time.monotonic() - started, 1)}


def judge(chunk: str, pcm: bytes, rate: int, *, transcribe: bool) -> dict:
    spoken = sum(len(m.group(2)) for m in
                 (audio_brief.SPEAKER_RE.match(l) for l in chunk.splitlines()) if m)
    seconds = audio_verify.duration_sec(pcm, rate)
    expected = spoken / audio_brief.SPOKEN_CHARS_PER_SEC
    out = {"seconds": round(seconds, 1), "expected_sec": round(expected, 1),
           "truncated": spoken >= 200 and seconds < expected * audio_brief.TRUNCATION_RATIO}
    sim = audio_verify.self_similarity(pcm, rate)
    out["repeat"] = bool(sim.get("repeat"))
    out["max_score"] = sim.get("max_score")
    if transcribe:
        tr = audio_verify.transcript_check(chunk, pcm, rate)
        out["transcript"] = {k: tr.get(k) for k in
                             ("checked", "ok", "reason", "ratio", "chars_per_sec", "error")}
    return out


def summarize(results: list[dict]) -> dict:
    table: dict[str, dict] = {}
    for r in results:
        row = table.setdefault(r["variant"], {"calls": 0, "ok": 0, "statuses": {},
                                              "clean": 0, "ratios": []})
        row["calls"] += 1
        row["statuses"][r["status"]] = row["statuses"].get(r["status"], 0) + 1
        if r["status"] != "ok":
            continue
        row["ok"] += 1
        q = r.get("quality") or {}
        tr = q.get("transcript") or {}
        if not q.get("truncated") and not q.get("repeat") and tr.get("ok", True):
            row["clean"] += 1
        if tr.get("ratio"):
            row["ratios"].append(tr["ratio"])
    return table


def render_markdown(model: str, source: str, table: dict) -> str:
    lines = [f"### TTS 안내문 시험 — `{model}`", "", f"대본: `{source}`", "",
             "| 안내문 | 호출 | 성공 | 표집 400 | 그 밖의 실패 | 품질 통과 | 받아쓰기/대본 비율 |",
             "|---|---|---|---|---|---|---|"]
    for name, row in table.items():
        sampling = row["statuses"].get("text_sampling_400", 0)
        other = row["calls"] - row["ok"] - sampling
        ratios = row["ratios"]
        ratio = f"{min(ratios):.2f}~{max(ratios):.2f}" if ratios else "-"
        lines.append(f"| {name} | {row['calls']} | {row['ok']} | {sampling} | {other} | "
                     f"{row['clean']}/{row['ok']} | {ratio} |")
    lines += ["", "받아쓰기/대본 비율이 1 보다 뚜렷이 크면 안내문을 소리 내어 읽은 것이다."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="gemini-2.5-flash-preview-tts")
    parser.add_argument("--rounds", type=int, default=6, help="후보당 호출 수")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--chunks", type=int, default=2,
                        help="대본 앞쪽 몇 청크를 번갈아 쓸지 (첫 청크는 Nuclens 인사 포함)")
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL", ""))
    parser.add_argument("--script-file", default="")
    parser.add_argument("--out", default="probe")
    parser.add_argument("--no-transcribe", action="store_true")
    parser.add_argument("--pause", type=float, default=3.0, help="호출 사이 간격(초)")
    args = parser.parse_args()

    if not gemini_client.API_KEY:
        print("::error::GEMINI_API_KEY 미설정")
        return 1
    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        print(f"::error::모르는 안내문 {unknown} — 가능: {list(VARIANTS)}")
        return 1

    if args.script_file:
        script, source = Path(args.script_file).read_text(encoding="utf-8"), args.script_file
    elif args.site_url:
        script, source = fetch_script(args.site_url, datetime.now(audio_brief.KST))
    else:
        script, source = SAMPLE_FALLBACK, "builtin-sample"
    chunks = audio_brief.split_script(script)[: max(1, args.chunks)]
    print(f"[probe] 대본 {source} — 시험 청크 {len(chunks)}개 "
          f"({', '.join(str(len(c)) for c in chunks)}자) / 모델 {args.model}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    kept: set[str] = set()
    # 후보를 번갈아 부른다 — 시간대별 과부하가 한 후보에 몰리지 않게.
    for round_index in range(args.rounds):
        chunk_index = round_index % len(chunks)
        chunk = chunks[chunk_index]
        for name in names:
            res = call_once(args.model, chunk, VARIANTS[name])
            entry = {"variant": name, "round": round_index + 1, "chunk": chunk_index + 1,
                     "status": res["status"], "code": res["code"], "elapsed": res["elapsed"]}
            if res["status"] == "ok":
                entry["quality"] = judge(chunk, res["pcm"], res["rate"],
                                         transcribe=not args.no_transcribe)
                if name not in kept:   # 후보마다 한 개는 들어 볼 수 있게 남긴다
                    (out / f"{name}-r{round_index + 1}.wav").write_bytes(
                        audio_verify.pcm_to_wav(res["pcm"], res["rate"]))
                    kept.add(name)
            else:
                entry["detail"] = res.get("detail", "")
            results.append(entry)
            print(f"[probe] r{round_index + 1} {name:<9} → {res['status']} "
                  f"({res['elapsed']}s){' ' + json.dumps(entry.get('quality'), ensure_ascii=False) if 'quality' in entry else ''}")
            time.sleep(args.pause)

    table = summarize(results)
    (out / "results.json").write_text(json.dumps(
        {"model": args.model, "source": source, "results": results, "summary": table},
        ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_markdown(args.model, source, table)
    print(markdown)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
