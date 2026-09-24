"""오늘 카드가 사이트 순위와 어긋났는가 — crawl 이 배포 뒤에 묻는다.

어긋났으면 `stale=true` 를 GITHUB_OUTPUT 에 적고 0 으로 끝난다. 판정은
make_cards.stale_against_ranking 하나다 — 여기서 기준을 따로 세우면 깨우는 쪽과
굽는 쪽이 다른 말을 한다(깨웠는데 make_cards 가 스킵하거나, 그 반대).

    python tools/cards_stale.py            # web/public/data 의 today.json 날짜
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import make_cards  # noqa: E402


def main() -> int:
    today_file = ROOT / "web" / "public" / "data" / "today.json"
    try:
        date = str(json.loads(today_file.read_text(encoding="utf-8")).get("date") or "")
    except (OSError, ValueError):
        date = ""
    stale = None
    if date:
        data = make_cards.load_site_data(date)
        stale = make_cards.stale_against_ranking(date, None if data is None else data.issues)
    if stale:
        print(f"[cards-stale] {date} 카드가 사이트 순위와 다르다: 올라간 {stale[0]} / 지금 {stale[1]}")
    else:
        print(f"[cards-stale] {date or '(날짜 없음)'} 카드는 사이트 순위와 같다(또는 판정 불가)")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fp:
            fp.write(f"stale={'true' if stale else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
