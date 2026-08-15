"""저장된 레코드에 인명 가드를 소급 적용한다.

`news_bot.strip_unsourced_person_names` 는 큐레이션 **시점**에만 돈다. 그 가드가
생기기 전에 큐레이션된 레코드, 그리고 V1 스냅샷으로 이관된 레코드는 틀린 이름을
그대로 지니고 있고 다시 큐레이션되지 않는다.

실측 2026-08-16 라이브: `curated.json` 의 남도일보 919437 이 원문 제목
`李 대통령 "해남 청정에너지…"` 를 **윤석열 대통령**으로 적고 있었다. 같은
착공식을 다룬 뉴시스·YTN·서울경제는 전부 '이재명'이라, 사이트의 한 이슈가 두
대통령을 말했다. 하필 그 레코드가 이슈 대표 제목이어서 흐름 탭에 그대로 떴다.

이 도구는 판단하지 않는다 — 지금 파이프라인이 내놓을 값과 같은 값을 넣을 뿐이다.
원문(`title`)을 대조 기준으로 삼아 생성 필드에 가드를 다시 걸고, 달라진 것만 쓴다.

    python tools/repair_unsourced_names.py            # 무엇이 바뀌는지만 출력
    python tools/repair_unsourced_names.py --write    # 실제로 기록

**가드를 고치기 전에 돌리지 말 것.** 가드에 오탐이 있으면 이 도구가 그 오탐을
저장분에 영구히 새긴다. 실제로 2026-08-16 에 `위한 대통령`(…충족하기 위한
대통령의) 을 이름으로 오인하는 버그가 있었고, 그 상태로 돌렸다면 멀쩡한 문장
하나를 같이 깎았다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

for _key in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_key, "repair")
import news_bot  # noqa: E402

# news_bot.normalize_curation_item 이 가드를 거는 필드와 같아야 한다.
FIELDS = ("title_kr", "summary", "detail", "implication", "why_important")


def repair_record(record: dict) -> dict:
    """달라진 필드만 담은 사전. 바뀔 게 없으면 빈 사전."""
    source = record.get("title") or ""
    if not source:
        return {}
    changed = {}
    for field in FIELDS:
        value = record.get(field)
        if not value:
            continue
        fixed = news_bot.strip_unsourced_person_names(value, source, where=field)
        if fixed != value:
            changed[field] = fixed
    return changed


def repair_curated(write: bool) -> int:
    path = ROOT / "curated.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    hits = 0
    for key, record in data.items():
        if not isinstance(record, dict):
            continue
        changed = repair_record(record)
        if not changed:
            continue
        hits += 1
        print(f"curated.json [{key}] {(record.get('title') or '')[:56]}")
        for field, value in changed.items():
            print(f"    {field}: {str(record[field])[:52]}\n         → {value[:52]}")
        record.update(changed)
    if hits and write:
        # news_bot.save_json 과 같은 직렬화여야 한다 — 형식이 어긋나면 6MB 파일이
        # 통째로 diff 로 잡혀 한 줄 수정이 리뷰 불가능해진다. save_curated 는
        # 쓰지 않는다: 그쪽은 보존 기간이 지난 항목을 함께 **삭제**하므로,
        # 이름 하나 고치러 들어와서 오래된 레코드를 날리게 된다.
        news_bot.save_json(path, data)
    return hits


def repair_archive(write: bool) -> int:
    hits = 0
    for path in sorted((ROOT / "archive").glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        out, touched = [], False
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)
                continue
            changed = repair_record(record)
            if changed:
                hits += 1
                touched = True
                print(f"{path.name} [{record.get('hash')}] {(record.get('title') or '')[:52]}")
                for field, value in changed.items():
                    print(f"    {field}: {str(record[field])[:52]}\n         → {value[:52]}")
                record.update(changed)
                line = json.dumps(record, ensure_ascii=False)
            out.append(line)
        if touched and write:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return hits


def main() -> int:
    write = "--write" in sys.argv
    total = repair_curated(write) + repair_archive(write)
    if not total:
        print("[repair] 고칠 레코드 없음")
        return 0
    print(f"\n[repair] {total}건 " + ("기록함" if write else "— --write 로 실제 기록"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
