"""캡처 시각의 orchestration 입력을 복원하고, **무엇이 독립 검증되는지** 밝힌다.

## 왜 provenance 를 같이 내보내는가

`recorded_replay` 는 재현된 요청 본문이 녹화본과 같은지를 본다. 그런데 입력을
캡처된 프롬프트에서 그대로 뜯어 오면 프롬프트를 다시 만들어도 당연히 같다 —
**순환 논증**이다. 그렇게 얻은 PROVEN 은 "프롬프트 빌더가 가역적이다"만 말하고
production fidelity 는 아무것도 말하지 않는다.

그래서 이 모듈은 필드마다 출처를 표시한다. 저장소 상태에서 독립적으로 복원한
필드는 프롬프트 일치가 **진짜 검증**이고, 프롬프트에서 뜯어 온 필드는 아니다.
판정을 읽는 쪽이 그 둘을 구별할 수 있어야 한다.

## 무엇이 어디서 오는가 (실측)

| 필드 | 출처 | 프롬프트 일치가 검증인가 |
| --- | --- | --- |
| `hash` (16자) | archive / curated 를 8자 머리표식으로 조회 | 부분 — 머리 8자만 |
| `title` | archive / curated | **예** |
| `publisher`, `domain` | archive / curated | **예** — `(OFFICIAL)` 표기를 좌우한다 |
| `description` | 캡처된 프롬프트 | 아니오 (순환) |
| `body` | 캡처된 프롬프트 | 아니오 (순환) |

`description` 과 `body` 는 `archive/`·`curated.json`·`digest_queue.json` 어디에도
없다. body 는 저작권 판단으로 저장하지 않고(news_bot 주석), description 은 큐레이션
뒤 `summary` 로 대체되어 원문이 남지 않는다. 즉 이 둘은 원리적으로 프롬프트 말고는
출처가 없다 — 감춰야 할 약점이 아니라 명시해야 할 경계다.

## P4 에서는 순환이 문제가 아니다

reasoning arm 비교는 "같은 입력에 config 만 다르게"가 목적이다. 프롬프트에서 뜯은
description/body 는 production 이 실제로 보낸 것과 바이트가 같으므로 그 목적에는
오히려 정확하다. 순환이 문제가 되는 것은 P2 fidelity 판정 쪽이다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 프롬프트 블록의 머리줄: `[0|a1b2c3d4] (OFFICIAL) 제목`
_HEADER = re.compile(r"^\[(?P<idx>\d+)\|(?P<tag>[0-9a-f]+)\](?P<official> \(OFFICIAL\))? (?P<title>.*)$")
_BLOCK_SEPARATOR = "\n\n---\n\n"

# 항상 한 줄이고 블록 끝에 붙는다(news_bot.build 순서). 그래서 뒤에서부터 떼어 내야
# 본문에 줄바꿈이 있어도 본문 경계를 잃지 않는다.
_TRAILING_SINGLE_LINE = ("관련보고서: ", "이전 출력 오류: ")

REPO_DERIVED = ("hash", "title", "publisher", "domain")
PROMPT_DERIVED = ("description", "body")


def parse_curation_prompt(user_message: str) -> list[dict]:
    """production 이 보낸 user 메시지를 블록 단위로 되읽는다."""
    blocks = []
    for raw in user_message.split(_BLOCK_SEPARATOR):
        lines = raw.split("\n")
        match = _HEADER.match(lines[0])
        if match is None:
            raise ValueError(f"curation 블록 머리줄을 읽을 수 없음: {lines[0][:80]!r}")
        rest = lines[1:]
        block = {
            "idx": int(match["idx"]),
            "tag": match["tag"],
            "title": match["title"],
            "official": bool(match["official"]),
            "description": "",
            "source": "",
            "body": "",
            "related_reports": None,
            "error_notes": None,
        }
        if rest and rest[0].startswith("요약: "):
            block["description"] = rest.pop(0)[len("요약: "):]
        if rest and rest[0].startswith("출처: "):
            block["source"] = rest.pop(0)[len("출처: "):]
        # 꼬리의 한 줄짜리 항목을 뒤에서부터 떼어 낸다.
        while rest and any(rest[-1].startswith(prefix)
                           for prefix in _TRAILING_SINGLE_LINE):
            line = rest.pop()
            if line.startswith("관련보고서: "):
                block["related_reports"] = line[len("관련보고서: "):]
            else:
                block["error_notes"] = line[len("이전 출력 오류: "):]
        if rest and rest[0].startswith("본문: "):
            rest[0] = rest[0][len("본문: "):]
            block["body"] = "\n".join(rest)
        blocks.append(block)
    return blocks


def load_article_index(*, archive_dir: Path | None = None,
                       curated: Path | None = None) -> dict[str, dict]:
    """8자 머리표식 → 저장소에 남아 있는 기사 레코드.

    같은 머리표식이 둘 이상이면 **넣지 않는다.** 아무 쪽이나 고르면 그 순간
    복원이 조용히 틀리고, 프롬프트가 우연히 맞으면 그대로 통과해 버린다.
    """
    index: dict[str, dict] = {}
    collisions: set[str] = set()

    def add(row: dict) -> None:
        digest = str(row.get("hash") or "")
        if len(digest) < 8:
            return
        tag = digest[:8]
        if tag in index and index[tag].get("hash") != digest:
            collisions.add(tag)
            return
        index[tag] = row

    if curated and curated.exists():
        for row in json.loads(curated.read_text(encoding="utf-8")).values():
            if isinstance(row, dict):
                add(row)
    if archive_dir and archive_dir.exists():
        for path in sorted(archive_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    add(json.loads(line))
    for tag in collisions:
        index.pop(tag, None)
    return index


def _article_from(block: dict, stored: dict | None) -> tuple[dict, dict]:
    """블록 + 저장 레코드 → production 이 받았던 기사 dict 와 그 출처 표시."""
    provenance = {field: "prompt" for field in PROMPT_DERIVED}
    if stored is None:
        # 저장소에 없으면 프롬프트가 유일한 출처다. 그 사실을 숨기지 않는다 —
        # 이 기사에 대해서는 요청 일치가 독립 검증이 아니다.
        provenance.update({field: "prompt" for field in REPO_DERIVED})
        article = {
            "hash": block["tag"], "title": block["title"],
            "publisher": block["source"], "domain": block["source"],
        }
    else:
        provenance.update({field: "repo" for field in REPO_DERIVED})
        article = {
            "hash": str(stored.get("hash") or block["tag"]),
            "title": stored.get("title") or "",
            "publisher": stored.get("publisher") or "",
            "domain": stored.get("domain") or "",
            "link": stored.get("link") or stored.get("url") or "",
        }
    article["description"] = block["description"]
    return article, provenance


def reconstruct_curation(record: dict, index: dict[str, dict]) -> dict:
    """한 캡처 레코드에서 `curate_batch` 입력을 복원한다."""
    parts = (record["request_body"].get("contents") or [{}])[0].get("parts") or [{}]
    blocks = parse_curation_prompt(parts[0].get("text") or "")
    articles, bodies, provenance, unresolved = [], {}, [], []
    for block in blocks:
        stored = index.get(block["tag"])
        if stored is None:
            unresolved.append(block["tag"])
        article, fields = _article_from(block, stored)
        articles.append(article)
        if block["body"]:
            bodies[article["hash"]] = block["body"]
        provenance.append({"tag": block["tag"], "fields": fields})
    return {
        "articles": articles,
        "bodies": bodies or None,
        "provenance": provenance,
        "unresolved_tags": unresolved,
        "prompt_needs_reports_kb": any(b["related_reports"] for b in blocks),
        "is_regeneration": any(b["error_notes"] for b in blocks),
    }


# ── dedup ──────────────────────────────────────────────────────────────────
#
# dedup 블록은 라벨-값 형식이라 되읽기는 쉽다. 복원 난이도는 curation 보다 오히려
# 낮다 — 15개 필드 중 13개가 `curated.json`/`archive/` 에 그대로 남아 있다.
# 남는 둘은 브리핑 실행 중에 계산되는 값이라 저장되지 않는다:
#   story_context     : story_cluster 가 그 회차에 묶은 맥락
#   story_fingerprint : 그 회차의 story 지문
# 이 둘만 프롬프트 출처이고 나머지는 저장소에서 독립 복원한다.
#
# 다만 join key 가 약하다. dedup 블록에는 hash 가 없어서 제목으로 맞춰야 한다.
# 제목이 겹치면 아무 쪽이나 고르지 않고 뺀다(머리표식 충돌과 같은 이유).

_DEDUP_LABELS = ("TITLE_KR: ", "TITLE_ORIGINAL: ", "SOURCE: ", "SCOPE_SECTION: ",
                 "EVENT: ", "TAGS: ", "SUMMARY: ", "DETAIL: ", "STORY_CONTEXT: ",
                 "EXISTING_FINGERPRINT: ")
DEDUP_REPO_DERIVED = ("title", "title_kr", "publisher", "domain", "feed",
                      "source_tier", "scope", "section", "features", "event_date",
                      "tags", "summary", "detail")
DEDUP_PROMPT_DERIVED = ("story_context", "story_fingerprint")


def parse_dedup_prompt(user_message: str) -> list[dict]:
    """`dedup._article_block` 이 만든 블록을 되읽는다."""
    blocks = []
    for raw in user_message.split(_BLOCK_SEPARATOR):
        lines = raw.splitlines()
        if not lines or not re.fullmatch(r"\[\d+\]", lines[0]):
            raise ValueError(f"dedup 블록 머리줄을 읽을 수 없음: {lines[0][:80]!r}")
        fields = {}
        for line in lines[1:]:
            for label in _DEDUP_LABELS:
                if line.startswith(label):
                    fields[label[:-2]] = line[len(label):]
                    break
        blocks.append(fields)
    return blocks


def load_title_index(*, archive_dir: Path | None = None,
                     curated: Path | None = None) -> dict[str, dict]:
    """정규화한 원제 → 저장소 레코드. dedup 블록에 hash 가 없어서 필요하다."""
    import dedup as dedup_module

    index: dict[str, dict] = {}
    collisions: set[str] = set()
    for row in load_article_index(archive_dir=archive_dir, curated=curated).values():
        key = dedup_module._trim(row.get("title"), 220)
        if not key:
            continue
        if key in index and index[key].get("hash") != row.get("hash"):
            collisions.add(key)
            continue
        index[key] = row
    for key in collisions:
        index.pop(key, None)
    return index


def _story_context_from(text: str) -> list[dict]:
    """STORY_CONTEXT 문자열을 `_article_block` 이 다시 만들 수 있는 형태로 되돌린다.

    각 조각은 이미 잘려 있고 `_trim` 은 멱등이므로, 같은 구분자로 다시 이으면
    원래 문자열이 그대로 나온다. 조각이 셋을 넘으면 앞의 둘만 두고 나머지를 하나로
    합친다 — `_article_block` 이 story_context[:3] 만 보기 때문이다.
    """
    if not text:
        return []
    parts = text.split(" || ")
    if len(parts) > 3:
        parts = parts[:2] + [" || ".join(parts[2:])]
    return [{"summary": part} for part in parts]


def reconstruct_dedup(record: dict, title_index: dict[str, dict]) -> dict:
    """한 캡처 레코드에서 `dedup_articles` / `editorial_dedup_articles` 입력을 복원한다."""
    parts = (record["request_body"].get("contents") or [{}])[0].get("parts") or [{}]
    blocks = parse_dedup_prompt(parts[0].get("text") or "")
    articles, provenance, unresolved = [], [], []
    for index, block in enumerate(blocks):
        stored = title_index.get(block.get("TITLE_ORIGINAL", ""))
        fields = {field: "prompt" for field in DEDUP_PROMPT_DERIVED}
        if stored is None:
            unresolved.append(index)
            fields.update({field: "prompt" for field in DEDUP_REPO_DERIVED})
            # 저장소에 없으면 프롬프트가 유일한 출처다. 그 사실을 숨기지 않는다.
            article = {"title": block.get("TITLE_ORIGINAL", ""),
                       "title_kr": block.get("TITLE_KR", "")}
        else:
            fields.update({field: "repo" for field in DEDUP_REPO_DERIVED})
            article = {field: stored.get(field) for field in DEDUP_REPO_DERIVED}
        article["story_fingerprint"] = block.get("EXISTING_FINGERPRINT", "")
        article["story_context"] = _story_context_from(block.get("STORY_CONTEXT", ""))
        article["hash"] = (stored or {}).get("hash") or f"unresolved-{index}"
        articles.append(article)
        provenance.append({"index": index, "fields": fields})
    return {
        "articles": articles,
        # scores 는 프롬프트에 실리지 않는다. 대표 기사 선택에만 쓰이므로 요청
        # fidelity 와 무관하고, 최종 산출물 대조에는 별도 복원이 필요하다.
        "scores": {article["hash"]: 0.0 for article in articles},
        "scores_are_placeholders": True,
        "provenance": provenance,
        "unresolved_indices": unresolved,
        "stage": ("dedup_final"
                  if (record.get("detail") or {}).get("task") == "dedup_final"
                  else "dedup"),
    }


def independence(reconstruction: dict) -> dict:
    """요청 일치가 실제로 무엇을 증명하는지 한 줄로 요약한다."""
    rows = reconstruction["provenance"]
    repo_fields = sum(1 for row in rows for value in row["fields"].values()
                      if value == "repo")
    total = sum(len(row["fields"]) for row in rows)
    identity = (DEDUP_REPO_DERIVED if "unresolved_indices" in reconstruction
                else REPO_DERIVED)
    always_prompt = (DEDUP_PROMPT_DERIVED if "unresolved_indices" in reconstruction
                     else PROMPT_DERIVED)
    fully_repo = sum(1 for row in rows
                     if all(row["fields"][field] == "repo" for field in identity))
    return {
        "articles": len(rows),
        "articles_with_repo_identity": fully_repo,
        "independently_reconstructed_fields": repo_fields,
        "prompt_derived_fields": total - repo_fields,
        # description/body 는 저장되지 않으므로 어떤 경우에도 프롬프트 출처다.
        "always_prompt_derived": list(always_prompt),
        "claim": (
            "요청 일치는 title/publisher/domain/hash 머리표식과 batch 구성·호출 순서·"
            "재생성 여부를 독립 검증한다. description/body 는 프롬프트에서 복원되므로 "
            "그 두 필드에 대해서는 순환이며 검증이 아니다."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "archive")
    parser.add_argument("--curated", type=Path, default=ROOT / "curated.json")
    args = parser.parse_args()

    from tools import recorded_replay

    records = recorded_replay.select(
        recorded_replay.load_capture(args.capture), "curation")
    index = load_article_index(archive_dir=args.archive_dir, curated=args.curated)
    out = []
    for record in records:
        reconstruction = reconstruct_curation(record, index)
        out.append({
            "seq": record.get("seq"),
            "label": (record.get("detail") or {}).get("task"),
            "articles": len(reconstruction["articles"]),
            "unresolved_tags": reconstruction["unresolved_tags"],
            "independence": independence(reconstruction),
        })
    print(json.dumps({"index_size": len(index), "records": out},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
