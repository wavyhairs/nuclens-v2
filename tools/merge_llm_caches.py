"""배포(deploy-web)가 새로 얻은 LLM 판정을 main 의 캐시에 채워 넣는다.

배경 (2026-09-27):
    web/** 를 건드린 PR 이 머지될 때마다 deploy-web 이 full 빌드로 이슈 병합 검수를
    12~19회 부른다. 그런데 이 워크플로는 상태를 커밋하지 않아서 받은 답을 러너와
    함께 버렸다. 그날 09:02·09:15 배포는 13분 간격으로 같은 225쌍을 똑같이 12회씩
    물었고, 10:41 배포와 15:37 크롤이 그 쌍을 또 물었다 — 같은 질문이 네 번 나갔다.
    그날 유료 3.5-flash-lite 82회 가운데 72회가 배포에서 나왔다.

왜 통째로 커밋하지 않는가:
    배포는 머지 커밋(과거의 SHA)을 짓는다. 그 사이 크롤·브리핑이 같은 파일에 답을
    더 적어 main 에 올렸을 수 있다. 배포의 파일로 덮으면 그 답이 사라지고, git
    병합에 맡기면 수만 줄짜리 JSON 에서 충돌이 난다. 그래서 항목 단위로 합친다.

규칙 (항목 단위 3자 병합):
    base   배포가 체크아웃한 커밋의 캐시   (git show <base-ref>:<file>)
    ours   빌드가 끝난 작업 트리의 캐시     (--source)
    theirs 지금 main 의 캐시               (--target, origin/main 워크트리)

    · ours 가 base 와 같은 항목은 배포가 건드리지 않은 것이다 — 보지 않는다.
    · theirs 에 없으면 넣는다 — 배포가 처음 물은 답.
    · theirs 가 base 그대로면 ours 로 바꾼다 — 배포가 다시 물어 고친 답.
    · theirs 가 따로 바뀌었으면 theirs 를 둔다 — **main 이 이긴다.**
    · base 에 있었는데 theirs 에서 사라졌으면 되살리지 않는다.
    · 항목의 prompt_version 이 main 파일의 prompt_version 과 다르면 넣지 않는다 —
      배포가 main 보다 옛 프롬프트로 돈 경우다. 읽는 쪽이 어차피 버린다.
    · 지우지 않는다. 가지치기는 캐시 주인(크롤·브리핑)의 일이다.

쓰기는 각 모듈과 같은 llm_cache.save 로 한다 — 형식이 다르면 파일 전체가 diff 로
뜬다(issue_insights.json 만 sort_keys=False 인 것도 그대로 따른다).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_cache  # noqa: E402 — stdlib 만 쓰는 봉투 모듈

# 파일 → (항목 사전 키, sort_keys). 크롤의 "Commit issue review cache" 목록 가운데
# LLM 판정 캐시만. briefing_snapshots.json 은 판정이 아니라 발송 기록이라 뺀다.
CACHES = {
    "issue_llm_reviews.json": ("reviews", True),
    "keei_llm_matches.json": ("matches", True),
    "issue_insights.json": ("insights", False),
    "issue_headlines.json": ("headlines", True),
}
ENVELOPE = {"_comment", "prompt_version"}


@dataclass
class Result:
    name: str
    added: int = 0
    updated: int = 0
    main_wins: int = 0
    stale_prompt: int = 0
    skipped: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated)

    def line(self) -> str:
        if self.skipped:
            return f"[cache-merge] {self.name}: 건너뜀 — {self.skipped}"
        return (f"[cache-merge] {self.name}: 추가 {self.added} · 갱신 {self.updated}"
                f" · main 우선 {self.main_wins} · 옛 프롬프트 {self.stale_prompt}")


def _read_json(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _git_show(source: Path, ref: str, name: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=source,
                          capture_output=True, text=True, encoding="utf-8")
    return proc.stdout if proc.returncode == 0 else None


def merge_entries(base: dict, ours: dict, theirs: dict, prompt_version: object,
                  result: Result) -> dict:
    merged = dict(theirs)
    for key, value in ours.items():
        if key in base and base[key] == value:
            continue
        if isinstance(value, dict) and value.get("prompt_version") != prompt_version:
            result.stale_prompt += 1
            continue
        if key not in theirs:
            if key in base:
                continue  # main 이 지운 것 — 되살리지 않는다
            merged[key] = value
            result.added += 1
        elif theirs[key] == value:
            continue
        elif key in base and theirs[key] == base[key]:
            merged[key] = value
            result.updated += 1
        else:
            result.main_wins += 1
    return merged


def merge_file(name: str, *, source: Path, target: Path, base_ref: str) -> Result:
    key, sort_keys = CACHES[name]
    result = Result(name)
    ours_doc = _read_json(_read_text(source / name))
    theirs_doc = _read_json(_read_text(target / name))
    if ours_doc is None:
        result.skipped = "빌드 결과에 파일이 없거나 깨졌다"
        return result
    if theirs_doc is None:
        result.skipped = "main 에 파일이 없거나 깨졌다"
        return result
    if set(theirs_doc) - ENVELOPE - {key}:
        # 봉투에 모르는 칸이 생겼다. llm_cache.save 로 다시 쓰면 그 칸이 사라진다.
        result.skipped = f"봉투에 모르는 칸 {sorted(set(theirs_doc) - ENVELOPE - {key})}"
        return result
    base_doc = _read_json(_git_show(source, base_ref, name)) or {}
    ours = ours_doc.get(key) if isinstance(ours_doc.get(key), dict) else {}
    theirs = theirs_doc.get(key) if isinstance(theirs_doc.get(key), dict) else {}
    base = base_doc.get(key) if isinstance(base_doc.get(key), dict) else {}
    merged = merge_entries(base, ours, theirs, theirs_doc.get("prompt_version"), result)
    if result.changed:
        llm_cache.save(merged, target / name, key=key,
                       prompt_version=theirs_doc.get("prompt_version"),
                       comment=theirs_doc.get("_comment", ""),
                       sort_keys=sort_keys, swallow_errors=False)
    return result


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-ref", default="HEAD",
                        help="배포가 체크아웃한 커밋 (기본 HEAD)")
    parser.add_argument("--source", type=Path, default=ROOT,
                        help="빌드가 끝난 작업 트리 (기본 저장소 루트)")
    parser.add_argument("--target", type=Path, required=True,
                        help="origin/main 을 펼친 워크트리 — 여기에 합쳐 쓴다")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # 한글 로그가 cp1252 콘솔에서 예외를 내면 파일만 쓰고 커밋 전에 죽는다.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    changed = []
    for name in CACHES:
        result = merge_file(name, source=args.source, target=args.target,
                            base_ref=args.base_ref)
        print(result.line())
        if result.changed:
            changed.append(name)
    print(f"[cache-merge] 바뀐 파일 {len(changed)}개: {' '.join(changed) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
