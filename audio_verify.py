"""TTS 출력 검증 — 만들어진 음성이 대본을 **한 번씩** 읽었는지 본다.

왜 있는가
---------
2026-10-03 전문가 브리핑에서 TTS 가 한 청크(대사 860자)를 두 번 읽었다.
해외 구간 1분 48초가 그대로 되풀이된 채 발송됐다. 청크는 HTTP 200 으로, 바이트도
넉넉하게 돌아왔다 — 파이프라인의 유일한 출력 검사(`_check_not_truncated`)는
"너무 짧음"만 보기 때문에 두 배 길이 음성은 그대로 통과했다.

Gemini TTS 는 글자를 음성 토큰으로 **생성**하는 언어 모델이라 요약·건너뜀·되풀이가
구조적으로 가능하다(2.5 flash TTS 가 텍스트를 두 번 읽는 사례는 Google 개발자
포럼에 보고돼 있다). 프롬프트로는 확률을 낮출 뿐 막지 못하므로, 출력을 보고
걸러 다시 만드는 수밖에 없다.

무엇을 보나
-----------
1. **자기유사도(self-similarity)** — API 없이 CPU 로만. 음성을 0.2초 블록의
   스펙트럼 특징으로 바꾸고, 5초 구간이 같은 음성의 다른 자리(시차 10초~끝)에
   다시 나오는지 모든 시차에서 본다. 같은 목소리가 같은 문장을 다시 읽으면
   값이 치솟는다. 10-03 실측: 정상 음성 5개(약 31분) 최고 0.30~0.47, 되풀이
   구간 중앙값 0.94. 무음끼리 맞물리면 1.0 이 나오므로 무음 블록은 비교에서 뺀다.
2. **받아쓰기 대조** — 청크 음성을 flash-lite 에 넣어 들리는 그대로 받아쓰게
   하고, 받아쓴 글과 보낸 대본을 **코드로** 맞춘다. 어떤 문장이 두 번 나오면
   되풀이, 절반 넘게 빠지면 누락이다. 받아쓰기 모델이 되풀이를 '실수'로 여겨
   한 번만 적을 수 있으므로, 음성 길이 대비 받아쓴 글자 수가 평소의 70% 미만이면
   받아쓰기를 믿지 않는다(그날 10-03 청크가 정확히 그 모양이다: 217초에 860자).

판정 원칙
---------
- 둘 중 하나라도 되풀이를 보면 실패다. 둘은 독립이라 한쪽이 못 보는 종류를
  다른 쪽이 본다(통째 되풀이 → 유사도, 억양을 바꾼 재낭독 → 받아쓰기).
- 받아쓰기 **호출 실패**는 실패가 아니다. 그날은 유사도만으로 판정한다 —
  받아쓰기 장애 때문에 오디오가 빠지면 안 된다.
- 되풀이를 찾으면 호출자가 다시 만들고, 안 되면 쪼개고, 그래도 안 되면
  `trim_repeat` 로 두 번째 읽기를 잘라낸다. 오디오가 안 나가는 경우를 새로
  만들지 않는다.
"""

from __future__ import annotations

import base64
import io
import re
import struct
import wave

try:  # numpy 는 유사도 검사에만 쓴다. 없으면 그 검사만 '못 봤음'으로 남긴다.
    import numpy as np
except ImportError:  # pragma: no cover - CI 는 requirements 로 설치한다
    np = None  # type: ignore[assignment]

import gemini_client
from gemini_client import GeminiError

# ── 자기유사도 ──────────────────────────────────────────────────────────────
BLOCK_SEC = 0.2          # 특징 블록 길이
RUN_SEC = 5.0            # 이만큼 이어서 닮아야 되풀이로 본다
MIN_LAG_SEC = 10.0       # 이보다 가까운 자리는 같은 문장 안의 운율 반복일 수 있다
REPEAT_SCORE = 0.8       # 정상 최고 0.47 과 되풀이 중앙값 0.94 사이. 표본이 쌓이면 내린다
SILENCE_RMS = 0.01       # 이 아래 블록은 무음 — 비교에서 뺀다
_BANDS = 40
_COEFFS = 20

# ── 받아쓰기 ────────────────────────────────────────────────────────────────
TRANSCRIBE_LABEL = "audio_transcribe"
TRANSCRIBE_MAX_TOKENS = 4096
# 받아쓴 글자 수(normalize 뒤 — 띄어쓰기·문장부호 없음) / 음성 초. 이보다
# 한참 낮으면 받아쓰기가 무언가를 빠뜨렸다는 뜻이라 그 결과를 믿지 않는다.
#
# 4.0 인 이유(2026-10-04 하향): 5.0 은 **정상 청크가 걸리는 값**이었다. 대사
# 7.0~7.7자/초는 띄어쓰기를 센 값이고, 받아쓴 글을 normalize 하면 4.7~5.0 이
# 나온다(10-04 정상 청크 4개: 141초 678자·130초 642자·125초 620자·145초 679자).
# 그날 이 문턱 하나가 멀쩡한 청크를 재생성·분할로 몰아 TTS 요청을 몇 배로 늘렸고,
# 무료 일일 한도(모델당 10건)와 503 과부하 앞에서 전문가 오디오가 죽었다.
# 지켜야 할 모양은 10-03 되풀이다 — 대사 860자를 217초에 두 번 읽고 받아쓰기가
# 한 번만 적으면 3.2 안팎이다. 4.0 은 그 사이다.
TRANSCRIPT_MIN_CHARS_PER_SEC = 4.0
SENTENCE_MIN_CHARS = 10  # 이보다 짧은 문장은 대조에 안 쓴다 — "다음 소식입니다" 류
SHINGLE = 6              # 글자 n-gram 길이
REPEAT_SHINGLE_RATIO = 0.6   # 문장 n-gram 의 이 비율 이상이 2회 이상 나오면 되풀이
MISSING_COVERAGE = 0.5       # 문장 n-gram 의 이 비율 미만만 보이면 누락

TRANSCRIBE_SYSTEM = (
    "당신은 한국어 음성 받아쓰기 담당입니다. 들리는 말을 **들리는 그대로** 적습니다.\n"
    "- 같은 문장이 두 번 들리면 두 번 적습니다. 반복을 실수로 여겨 지우지 마십시오.\n"
    "- 요약·교정·생략·보충을 하지 않습니다. 끊긴 문장은 끊긴 대로 둡니다.\n"
    "- 숫자는 아라비아 숫자로, 영문 약어는 영문 그대로 적습니다.\n"
    "- 화자 표시나 시간 표시를 붙이지 않습니다.\n"
    '출력은 JSON 한 객체만: {"transcript": "..."}'
)


# ── 공통 ─────────────────────────────────────────────────────────────────────
def _samples(pcm: bytes, rate: int):
    """s16le mono → float32 [-1, 1]."""
    usable = pcm[: len(pcm) // 2 * 2]
    return np.frombuffer(usable, dtype="<i2").astype(np.float32) / 32768.0


def duration_sec(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2 / max(1, rate)


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm[: len(pcm) // 2 * 2])
    return buf.getvalue()


# ── 1. 자기유사도 ───────────────────────────────────────────────────────────
def _features(x, rate: int):
    """0.2초 블록마다 로그 대역 에너지의 코사인 변환(에너지항 제외) — 정규화 벡터.

    반환: (X[nb, _COEFFS-1], voiced[nb]). 무음 블록은 0 벡터다.
    """
    win = max(256, int(rate * 0.032))
    hop = max(64, int(rate * 0.010))
    n = (len(x) - win) // hop
    if n < 2:
        return None, None
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx]
    energy = np.sqrt((frames ** 2).mean(1))
    spec = np.abs(np.fft.rfft(frames * np.hanning(win), axis=1)) ** 2
    freqs = np.fft.rfftfreq(win, 1 / rate)
    edges = np.geomspace(80, min(7000, rate / 2 - 1), _BANDS + 1)
    bands = np.stack([
        spec[:, (freqs >= edges[i]) & (freqs < edges[i + 1])].sum(1)
        for i in range(_BANDS)], 1)
    k = np.arange(_BANDS)
    dct = np.cos(np.pi / _BANDS * (k[None, :] + 0.5) * np.arange(_COEFFS)[:, None])
    coeffs = (np.log(bands + 1e-8) @ dct.T)[:, 1:]
    per_block = max(1, int(round(BLOCK_SEC * rate / hop)))
    nb = len(coeffs) // per_block
    if nb < 2:
        return None, None
    blocks = coeffs[: nb * per_block].reshape(nb, per_block, -1).mean(1)
    voiced = energy[: nb * per_block].reshape(nb, per_block).mean(1) > SILENCE_RMS
    if voiced.sum() < 2:
        return blocks, voiced
    mu = blocks[voiced].mean(0)
    sd = blocks[voiced].std(0) + 1e-6
    normed = (blocks - mu) / sd
    normed /= np.linalg.norm(normed, axis=1, keepdims=True) + 1e-9
    normed[~voiced] = 0.0
    return normed, voiced


def self_similarity(pcm: bytes, rate: int) -> dict:
    """음성 안에서 같은 소리가 되풀이되는 자리를 찾는다.

    Returns:
        {"checked": bool, "max_score": float, "repeat": bool,
         "run_sec": float, "segments": [{"start","end","lag","score"}]}
        segments 의 start/end 는 **첫 번째** 등장 자리(초), lag 는 두 번째
        등장까지의 거리(초)다. 두 번째 등장은 [start+lag, end+lag].
    """
    empty = {"checked": False, "max_score": 0.0, "repeat": False,
             "run_sec": 0.0, "segments": []}
    if np is None or not pcm or rate <= 0:
        return empty
    x = _samples(pcm, rate)
    feats, voiced = _features(x, rate)
    if feats is None:
        return {**empty, "checked": True}
    nb = len(feats)
    run = max(2, int(round(RUN_SEC / BLOCK_SEC)))
    min_lag = int(round(MIN_LAG_SEC / BLOCK_SEC))
    if nb - run <= min_lag:
        return {**empty, "checked": True}
    sim = feats @ feats.T
    vf = voiced.astype(np.float32)
    best = np.full(nb, -1.0, dtype=np.float32)
    best_lag = np.zeros(nb, dtype=np.int64)
    kernel = np.ones(run, dtype=np.float32)
    for lag in range(min_lag, nb - run):
        diag = np.diagonal(sim, offset=lag)
        both = vf[: nb - lag] * vf[lag:]
        count = np.convolve(both, kernel, mode="valid")
        score = np.convolve(diag * both, kernel, mode="valid") / np.maximum(count, 1.0)
        score[count < 0.8 * run] = -1.0  # 둘 중 하나가 거의 무음이면 판정 안 함
        better = score > best[: len(score)]
        best[: len(score)][better] = score[better]
        best_lag[: len(score)][better] = lag
    valid = best > -1.0
    if not valid.any():
        return {**empty, "checked": True}
    max_score = float(best[valid].max())
    hot = best >= REPEAT_SCORE
    segments: list[dict] = []
    i = 0
    while i < nb:
        if not hot[i]:
            i += 1
            continue
        j = i
        while j + 1 < nb and hot[j + 1] and abs(int(best_lag[j + 1]) - int(best_lag[i])) <= 2:
            j += 1
        # hot[i] 는 블록 i 에서 시작하는 run 길이 창이 닮았다는 뜻 — 끝은 j+run
        segments.append({
            "start": round(i * BLOCK_SEC, 1),
            "end": round((j + run) * BLOCK_SEC, 1),
            "lag": round(float(np.median(best_lag[i:j + 1])) * BLOCK_SEC, 1),
            "score": round(float(best[i:j + 1].max()), 3),
        })
        i = j + 1
    segments = _merge_segments(segments)
    run_sec = max((s["end"] - s["start"] for s in segments), default=0.0)
    return {"checked": True, "max_score": round(max_score, 3),
            "repeat": bool(segments), "run_sec": round(run_sec, 1),
            "segments": segments}


def _merge_segments(segments: list[dict]) -> list[dict]:
    """같은 시차로 이어지거나 겹치는 구간은 하나다 — 억양이 잠깐 달라 점수가
    문턱 아래로 떨어진 자리에서 끊긴 것뿐이다(10-03 실측: 0~57초와 53~86초)."""
    merged: list[dict] = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        if merged and abs(merged[-1]["lag"] - seg["lag"]) <= 1.0                 and seg["start"] <= merged[-1]["end"] + 2.0:
            merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
            merged[-1]["score"] = max(merged[-1]["score"], seg["score"])
        else:
            merged.append(dict(seg))
    return merged


def trim_repeat(pcm: bytes, rate: int, report: dict) -> bytes | None:
    """유사도가 찾은 되풀이의 **두 번째** 읽기를 잘라낸다. 못 자르면 None.

    두 모양을 가른다.
    - 통째 재낭독: 첫 등장이 음성 머리 근처에서 시작하고 시차가 음성의 절반
      안팎이면, 모델이 끝까지 읽고 처음부터 다시 읽은 것이다(10-03 이 이 모양:
      뒤쪽 재낭독은 억양이 달라 유사도가 끝까지 닿지 않는다). 시차 지점에서
      뒤를 통째로 버린다.
    - 부분 되풀이: 두 번째 등장 구간만 들어낸다.
    마지막 수단이다 — 호출자는 재생성·분할을 먼저 한다.
    """
    segments = list((report or {}).get("segments") or [])
    if not segments or not pcm or rate <= 0:
        return None
    total = duration_sec(pcm, rate)
    longest = max(segments, key=lambda s: s["end"] - s["start"])
    start, end, lag = longest["start"], longest["end"], longest["lag"]
    if start <= 3.0 and abs(lag * 2 - total) <= max(12.0, total * 0.2):
        keep = int(lag * rate) * 2
        return pcm[:keep]
    cut_from = int((start + lag) * rate) * 2
    cut_to = int(min(total, end + lag) * rate) * 2
    if cut_to <= cut_from:
        return None
    return pcm[:cut_from] + pcm[cut_to:]


# ── 2. 받아쓰기 대조 ─────────────────────────────────────────────────────────
_NORM_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")


def normalize(text: str) -> str:
    return _NORM_RE.sub("", str(text or "")).lower()


def sentences(script_text: str) -> list[str]:
    """대본(HOST: 줄들)을 문장으로 나눈다. 라벨은 뗀다."""
    out: list[str] = []
    for line in str(script_text or "").splitlines():
        body = line.split(":", 1)[1] if re.match(r"^(HOST|ANALYST):", line) else line
        for sent in _SENT_SPLIT_RE.split(body.strip()):
            if sent.strip():
                out.append(sent.strip())
    return out


def _shingles(text: str) -> list[str]:
    return [text[i:i + SHINGLE] for i in range(0, max(0, len(text) - SHINGLE + 1))]


_HANGUL_RUN_RE = re.compile(r"[가-힣]+")


def _script_shingles(norm_sentence: str) -> list[str]:
    """대본 문장 쪽 n-gram — **한글 덩어리 안에서만** 뽑는다.

    대본의 영문·숫자는 받아쓰기에서 모양이 바뀐다. TTS 는 'Nuclens' 를 '뉴클렌스'
    로, '4일' 을 '사일' 로 읽을 수 있고 받아쓰기는 들은 대로 적는다. 그 자리를
    걸친 n-gram 은 음성이 멀쩡해도 안 보이므로 누락 판정을 오염시킨다(10-04:
    "10월 4일 일요일 Nuclens 전문가 브리핑입니다." 가 매일 첫 청크에서 '누락' 으로
    찍혀 같은 청크를 재생성·분할·모델 전환까지 몰았다). 한글끼리 이어진 자리는
    받아쓰기와 같은 글자로 돌아오므로 그것만 대조에 쓴다.
    """
    out: list[str] = []
    for run in _HANGUL_RUN_RE.findall(norm_sentence):
        out.extend(_shingles(run))
    return out


def compare_transcript(script_text: str, transcript: str) -> dict:
    """받아쓴 글이 대본을 한 번씩 담았는가.

    Returns:
        {"repeated": [문장...], "missing": [문장...], "ratio": float,
         "sentences": int}
    """
    norm_t = normalize(transcript)
    counts: dict[str, int] = {}
    for sh in _shingles(norm_t):
        counts[sh] = counts.get(sh, 0) + 1
    repeated: list[str] = []
    missing: list[str] = []
    judged = 0
    norm_script_len = 0
    for sent in sentences(script_text):
        norm_s = normalize(sent)
        norm_script_len += len(norm_s)
        if len(norm_s) < SENTENCE_MIN_CHARS:
            continue
        shingles = _script_shingles(norm_s)
        if not shingles:
            continue
        judged += 1
        seen = [counts.get(sh, 0) for sh in shingles]
        coverage = sum(1 for c in seen if c >= 1) / len(seen)
        twice = sum(1 for c in seen if c >= 2) / len(seen)
        if coverage < MISSING_COVERAGE:
            missing.append(sent[:60])
        elif twice >= REPEAT_SHINGLE_RATIO:
            repeated.append(sent[:60])
    ratio = round(len(norm_t) / max(1, norm_script_len), 2)
    return {"repeated": repeated, "missing": missing, "ratio": ratio,
            "sentences": judged}


def transcribe(pcm: bytes, rate: int, *, model: str | None = None) -> str:
    """청크 음성을 들리는 그대로 받아쓴다. 실패는 GeminiError 로 올린다."""
    wav = pcm_to_wav(pcm, rate)
    result = gemini_client.call_json(
        TRANSCRIBE_SYSTEM,
        "다음 음성을 들리는 그대로 받아쓰십시오.",
        inline_data=("audio/wav", wav),
        temperature=0.0,
        max_output_tokens=TRANSCRIBE_MAX_TOKENS,
        thinking_budget=0,
        model=model,
        label=TRANSCRIBE_LABEL,
        timeout=180.0,
        retries=1,
    )
    text = result.get("transcript") if isinstance(result, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise GeminiError("받아쓰기 응답에 transcript 없음")
    return text


def transcript_check(chunk_text: str, pcm: bytes, rate: int, *,
                     model: str | None = None) -> dict:
    """받아쓰기 → 대조. 호출 실패와 불신은 둘 다 `checked=False` 로 남긴다.

    Returns:
        {"checked": bool, "ok": bool, "reason": str, "chars_per_sec": float,
         "repeated": [...], "missing": [...], "ratio": float, "error": str}
    """
    base = {"checked": False, "ok": True, "reason": "", "chars_per_sec": 0.0,
            "repeated": [], "missing": [], "ratio": 0.0, "error": ""}
    if not gemini_client.is_available():
        return {**base, "error": "no_api_key"}
    seconds = duration_sec(pcm, rate)
    try:
        text = transcribe(pcm, rate, model=model)
    except GeminiError as exc:
        return {**base, "error": str(exc)[:200]}
    chars_per_sec = round(len(normalize(text)) / max(1.0, seconds), 2)
    if chars_per_sec < TRANSCRIPT_MIN_CHARS_PER_SEC and seconds >= 20:
        # 받아쓴 글이 음성 길이에 비해 너무 적다 — 받아쓰기가 무언가를 빠뜨렸다.
        # 되풀이를 한 번만 적은 10-03 모양이 정확히 이것이다. 통과로 보지 않는다.
        return {**base, "checked": False, "ok": False,
                "reason": f"받아쓰기 불신 — 음성 {seconds:.0f}초에 {len(normalize(text))}자",
                "chars_per_sec": chars_per_sec, "error": "untrusted"}
    diff = compare_transcript(chunk_text, text)
    reason = ""
    if diff["repeated"]:
        reason = f"되풀이 {len(diff['repeated'])}문장: {diff['repeated'][0][:30]}…"
    elif diff["missing"]:
        reason = f"누락 {len(diff['missing'])}문장: {diff['missing'][0][:30]}…"
    return {**base, "checked": True, "ok": not reason, "reason": reason,
            "chars_per_sec": chars_per_sec, **diff}


# ── 3. 종합 ─────────────────────────────────────────────────────────────────
def verify_chunk(index: int, chunk_text: str, pcm: bytes, rate: int, *,
                 transcribe_model: str | None = None,
                 use_transcript: bool = True) -> dict:
    """청크 하나의 출력 판정. ok=False 면 호출자가 다시 만든다.

    둘 중 하나라도 되풀이·누락을 보면 실패다. 받아쓰기가 **불신**(untrusted)으로
    끝나도 실패다 — 음성이 받아쓴 글보다 한참 길다는 건 그 자체가 신호다.
    받아쓰기 호출 자체가 실패(no_api_key·HTTP)하면 유사도만으로 판정한다.
    """
    similarity = self_similarity(pcm, rate)
    transcript = (transcript_check(chunk_text, pcm, rate, model=transcribe_model)
                  if use_transcript else
                  {"checked": False, "ok": True, "reason": "", "error": "disabled"})
    reasons: list[str] = []
    if similarity["repeat"]:
        seg = similarity["segments"][0]
        reasons.append(f"유사도 되풀이 {similarity['run_sec']:.0f}초 "
                       f"({seg['start']:.0f}s↔{seg['start'] + seg['lag']:.0f}s, "
                       f"{similarity['max_score']:.2f})")
    if not transcript["ok"]:
        reasons.append(transcript["reason"])
    return {
        "index": index,
        "ok": not reasons,
        "reason": " / ".join(reasons),
        "duration_sec": round(duration_sec(pcm, rate), 1),
        "spoken_chars": len(normalize(chunk_text)),
        "similarity": {k: similarity[k] for k in
                       ("checked", "max_score", "repeat", "run_sec", "segments")},
        "transcript": {k: transcript.get(k) for k in
                       ("checked", "ok", "chars_per_sec", "ratio",
                        "repeated", "missing", "error")},
    }


def summarize(reports: list[dict]) -> dict:
    """매니페스트용 요약 — 날마다 쌓여 문턱(REPEAT_SCORE)을 조정할 근거가 된다."""
    chunks = [r for r in reports if isinstance(r, dict)]
    return {
        "version": 1,
        "repeat_score_threshold": REPEAT_SCORE,
        "chunks": [{
            "index": r.get("index"),
            "ok": r.get("ok"),
            "reason": r.get("reason", ""),
            "duration_sec": r.get("duration_sec"),
            "spoken_chars": r.get("spoken_chars"),
            "similarity_max": (r.get("similarity") or {}).get("max_score"),
            "similarity_checked": (r.get("similarity") or {}).get("checked"),
            "transcript_checked": (r.get("transcript") or {}).get("checked"),
            "transcript_cps": (r.get("transcript") or {}).get("chars_per_sec"),
            "transcript_error": (r.get("transcript") or {}).get("error") or "",
        } for r in chunks],
        "rejected": sum(1 for r in chunks if not r.get("ok")),
    }
