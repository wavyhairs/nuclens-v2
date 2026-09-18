#!/usr/bin/env python3
"""카드 산출물 계약 검사 — 사이트에 올라간 카드가 실제로 볼 수 있는 것인지.

왜 있는가
---------------------------------------------------------------------------
카드 생성은 오래 **조용히** 실패했다. daily-brief 안의 카드 스텝이 전부
``continue-on-error`` 라, make_cards 가 exit 1 로 정직하게 알려도 워크플로는
초록불이었다. 그래서 카드가 빠진 날을 사람이 사이트를 열어 보고서야 알았다.

카드를 별도 워크플로(cards.yml)로 뗀 지금은 실패가 빨간불로 선다. 다만 종료
코드만으로는 부족하다 — ``publish_cards.py`` 는 album.json 이 없으면 "스킵"
하고 0 을 돌려주고, 렌더가 반쯤 끝난 PNG 도 파일로는 존재한다. **커밋 직전에
결과물 자체를 본다.**

무엇을 보는가
---------------------------------------------------------------------------
1. ``web/public/cards/index.json`` 이 있고 읽힌다
2. 그날 카드가 **최소 1장** 올라 있다
3. index 가 가리키는 PNG 가 전부 디스크에 있다 (모든 날짜치)
4. 그 PNG 들이 비어 있거나 깨지지 않았다 — 서명·IHDR 크기·IEND·최소 바이트
5. ``latest`` 가 실제 최신 날짜다 (홈의 띠가 이걸 보고 그린다)

'검사할 날짜'는 어떻게 정하는가
---------------------------------------------------------------------------
카드가 **나왔어야 하는 날**에만 2번을 요구한다. 아니면 텍스트 브리핑이 없던
날이나 카드로 낼 이슈가 없던 날에 거짓 경보가 난다.

    cards/album.json 이 있다        → make_cards 가 이번에 구웠다. 그 날짜를 검사
    없는데 index 에 outbox 날짜가 있다 → 이미 만들어 둔 날(멱등 스킵). 그 날짜를 검사
    둘 다 아니다                     → 나올 것이 없던 날. 3·4·5 만 보고 통과

    python tools/verify_cards.py
    python tools/verify_cards.py --date 2026-09-18   # 날짜를 직접 고정
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = ROOT / "web" / "public" / "cards"
ALBUM_FILE = ROOT / "cards" / "album.json"
OUTBOX_FILE = ROOT / "outbox.json"

# make_cards.MIN_PNG_BYTES 와 같은 값이다 — 1080×1080 그라디언트 빈 카드가 대략
# 20KB 고, 그 아래면 빈 렌더다. make_cards 를 import 하면 gemini_client 까지
# 딸려 와 키 없는 환경에서 죽으므로 값을 옮겨 적고, 둘이 갈라지지 않는지는
# tests/test_verify_cards.py 가 지킨다.
MIN_PNG_BYTES = 20_000

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
# 브리핑이 나간 날만 카드를 기대한다. outbox 의 이 상태들이 '나갔다'는 뜻이다.
SENT_STATUSES = ("sent", "partial")


def label(path: Path) -> str:
    """로그에 쓸 짧은 경로. 저장소 밖(테스트 임시 폴더)이면 그대로 쓴다."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def png_problem(path: Path) -> str | None:
    """PNG 한 장의 문제를 한 줄로. 멀쩡하면 None."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"열 수 없다 ({exc.__class__.__name__})"
    if size < MIN_PNG_BYTES:
        return f"너무 작다 {size}B < {MIN_PNG_BYTES}B (빈 렌더 의심)"
    try:
        head = path.read_bytes()[:24]
        with path.open("rb") as handle:
            handle.seek(-len(PNG_IEND), 2)
            tail = handle.read()
    except OSError as exc:
        return f"읽을 수 없다 ({exc.__class__.__name__})"
    if not head.startswith(PNG_SIGNATURE):
        return "PNG 서명이 아니다 (다른 형식이거나 앞이 잘렸다)"
    if head[12:16] != b"IHDR":
        return "IHDR 청크가 없다 (헤더 손상)"
    width, height = struct.unpack(">II", head[16:24])
    if not width or not height:
        return f"크기가 0 이다 ({width}×{height})"
    if tail != PNG_IEND:
        # 렌더가 중간에 끊기면 여기서 잡힌다 — 앞부분만 있는 파일도 열리긴 한다.
        return "IEND 로 끝나지 않는다 (렌더가 중간에 끊겼다)"
    return None


def expected_date(index: dict, date_arg: str | None) -> tuple[str, str]:
    """(검사할 날짜, 그렇게 정한 이유). 기대할 날짜가 없으면 ('', 이유)."""
    if date_arg:
        return date_arg, "--date 로 지정"
    album = read_json(ALBUM_FILE)
    if isinstance(album, dict) and album.get("date"):
        return str(album["date"]), "cards/album.json — 이번 실행이 구웠다"
    outbox = read_json(OUTBOX_FILE)
    if not isinstance(outbox, dict) or not outbox.get("date"):
        return "", "outbox.json 없음 — 브리핑이 아직 안 돌았다"
    status, day = outbox.get("status"), str(outbox["date"])
    if status not in SENT_STATUSES:
        return "", f"텍스트 브리핑 상태 '{status}' — 카드를 낼 날이 아니다"
    if day in (index.get("dates") or {}):
        return day, "이미 만들어 둔 날 — outbox 날짜가 index 에 있다"
    return "", f"{day} 카드 없음 — 낼 이슈가 없었거나 이번에 굽지 않았다"


def verify(site_dir: Path, date_arg: str | None) -> list[str]:
    """문제를 전부 모아 돌려준다. 빈 목록이면 통과."""
    index_file = site_dir / "index.json"
    if not index_file.exists():
        return [f"{label(index_file)} 없음 — 게시된 카드가 하나도 없다"]
    index = read_json(index_file)
    if not isinstance(index, dict):
        return [f"{label(index_file)} 를 읽을 수 없다 (JSON 깨짐)"]

    problems: list[str] = []
    dates = index.get("dates")
    if not isinstance(dates, dict):
        return ["index.json 에 dates 가 없다"]

    day, why = expected_date(index, date_arg)
    if day:
        files = dates.get(day) or []
        print(f"[verify-cards] 검사 날짜 {day} ({why})")
        if not files:
            problems.append(f"{day} 카드가 index 에 0 장 — 그날 카드가 사이트에 없다")
    else:
        print(f"[verify-cards] 기대할 날짜 없음 ({why}) — 게시본 무결성만 본다")

    # index 가 가리키는 모든 PNG. 옛 날짜까지 보는 이유: 보관 정리(KEEP_DAYS)가
    # 폴더를 지웠는데 index 가 그 날짜를 계속 들고 있으면 홈의 띠가 깨진 이미지를
    # 그린다 — 그 어긋남은 오늘 카드를 아무리 잘 구워도 안 잡힌다.
    checked = 0
    for folder, names in sorted(dates.items()):
        if not names:
            problems.append(f"{folder} 가 index 에 빈 목록으로 남아 있다")
            continue
        for name in names:
            png = site_dir / folder / name
            if not png.exists():
                problems.append(f"index 가 가리키는 파일 없음: cards/{folder}/{name}")
                continue
            bad = png_problem(png)
            if bad:
                problems.append(f"cards/{folder}/{name}: {bad}")
            checked += 1

    latest = index.get("latest")
    if dates and latest != max(dates):
        problems.append(f"latest 가 최신이 아니다: '{latest}' ≠ '{max(dates)}' "
                        "(홈의 카드뉴스 띠가 이 값을 본다)")

    print(f"[verify-cards] {len(dates)}일치 · PNG {checked}장 확인")
    return problems


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="검사할 브리핑 날짜 YYYY-MM-DD (비우면 스스로 정한다)")
    ap.add_argument("--site-dir", type=Path, default=SITE_DIR)
    args = ap.parse_args()

    problems = verify(args.site_dir, args.date)
    if problems:
        for problem in problems:
            print(f"::error::[cards] {problem}")
        print(f"[verify-cards] 실패 — 문제 {len(problems)}건")
        return 1
    print("[verify-cards] 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
