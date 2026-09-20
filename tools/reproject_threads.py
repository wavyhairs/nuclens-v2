"""카드가 받은 라이브 재료에 **체크아웃된 원장**을 다시 투영한다.

왜 필요한가 (2026-09-21 실사고)
---------------------------------------------------------------------------
카드는 라이브 사이트(today.json · issues.json · threads.json)를 받아 굽는다.
그 파일은 그날 아침 Daily Brief 가 **스토리 판정보다 먼저** 만들어 배포한
것이다 — 순서가 `Build web data → Deploy → Build long-term stories → 원장 커밋
→ Trigger cards` 라서, 카드가 깨어날 때 원장에는 오늘 판정이 있지만 사이트
파일에는 없다. 판정이 실린 사이트는 다음 크롤 빌드(최대 105분 뒤)에나 나온다.

평소에는 상관없다 — 상위 이슈가 어제부터 이어진 사건이면 어제 원장으로도
thread_id 가 찍힌다. 문제는 **그날 새 id 로 조폐된 이슈**가 1위일 때다.
2026-09-21 의 1위(대미투자 협상 결과 국회 보고)가 그랬다. 04:16 빌드가 새 id
를 만들었고, 04:52 원장이 그 id 를 스레드에 붙였지만, 04:49 배포본의
today.json 에는 thread_id 가 빈칸이었다. 카드는 후보 없음으로 조용히 빠졌다.
같은 원장으로 다시 투영하면 그 스레드(사건 2건 · stage_progress)는 자격이
있었다.

무엇을 하는가
---------------------------------------------------------------------------
1. 사이트 threads.json 의 `source_generated_at` 과 체크아웃 `thread_ledger.json`
   의 `generated_at` 을 비교한다. 체크아웃 쪽이 새로울 때만 손을 댄다 —
   같거나 오래됐으면 받은 파일이 곧 정답이라 아무것도 바꾸지 않는다.
2. `thread_web.build_payload()` 로 threads.json 을 다시 만들고
   `build_data.stamp_thread_ids()` 로 issues.json 의 `thread_id` 를 다시 찍는다.
   투영 코드는 사이트 빌드와 **같은 함수**다 — 여기서 다른 규칙을 쓰면 카드와
   화면이 다른 스토리를 말한다.
3. today.json 의 이슈 행에 issues.json 의 `thread_id` 를 옮겨 적는다.

바꾸지 않는 것: 순위·이슈 id·본문·manifest(세대). LLM 을 부르지 않는다.
`visible=false` 로 투영되면(원장 숨김) 받은 파일을 그대로 둔다 — 카드 쪽
게이트가 그 상태를 따로 판정한다.

    python tools/reproject_threads.py                 # web/public/data 에 적용
    python tools/reproject_threads.py --dry-run       # 무엇이 바뀔지만 본다
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "web"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import thread_ledger  # noqa: E402
import thread_web  # noqa: E402

KST = timezone(timedelta(hours=9))
DATA_DIR = ROOT / "web" / "public" / "data"
TOP_N = 3  # 카드가 보는 상위 건수. 로그 요약에만 쓴다.


def _parse_ts(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed


def ledger_is_newer(site_threads: dict, store: dict) -> tuple[bool, str]:
    """체크아웃 원장이 사이트 threads.json 의 원장보다 새로운가.

    사이트가 새롭거나 같으면 (False, 이유). 원장에 시각이 없으면 손대지 않는다 —
    비교할 수 없는 것을 새것으로 치면 옛 원장으로 덮어쓸 수 있다.
    """
    repo = _parse_ts(store.get("generated_at"))
    if repo is None:
        return False, "체크아웃 원장에 generated_at 이 없다 — 투영 안 함"
    site = _parse_ts(site_threads.get("source_generated_at"))
    if site is not None and repo <= site:
        return False, (f"사이트 원장({site.isoformat(timespec='seconds')})이 체크아웃 "
                       f"원장({repo.isoformat(timespec='seconds')})과 같거나 새롭다 — 그대로 둔다")
    return True, (f"체크아웃 원장 {repo.isoformat(timespec='seconds')} > 사이트 "
                  f"{site.isoformat(timespec='seconds') if site else '없음'}")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


def reproject(data_dir: Path, payload: dict, *, dry_run: bool = False) -> dict:
    """issues.json · today.json · threads.json 에 `payload` 를 투영한다.

    돌려주는 것은 로그용 요약. `dry_run` 이면 파일을 쓰지 않는다.
    """
    import build_data  # noqa: E402 — web/ 의 빌드 모듈. 투영 규칙은 그쪽 것 하나다.

    issues = _read(data_dir / "issues.json")
    today = _read(data_dir / "today.json")
    if not isinstance(issues, list) or not isinstance(today, dict):
        raise ValueError("issues.json 은 목록, today.json 은 객체여야 한다")

    stamped = build_data.stamp_thread_ids(issues, payload)
    thread_of = {str(row.get("issue_id") or ""): str(row.get("thread_id") or "")
                 for row in issues if row.get("issue_id")}

    rows = list(today.get("issues") or [])
    before = sum(1 for row in rows if row.get("thread_id"))
    for row in rows:
        issue_id = str(row.get("issue_id") or "")
        if issue_id in thread_of:
            row["thread_id"] = thread_of[issue_id]
    after = sum(1 for row in rows if row.get("thread_id"))
    top = [(str(row.get("issue_id") or ""), str(row.get("thread_id") or ""))
           for row in rows[:TOP_N]]

    if not dry_run:
        _write(data_dir / "issues.json", issues)
        _write(data_dir / "today.json", today)
        _write(data_dir / "threads.json", payload)
    return {"stamped": stamped, "today_before": before, "today_after": after,
            "today_rows": len(rows), "threads": len(payload.get("threads") or []),
            "top": top}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    data_dir: Path = args.data_dir

    missing = [name for name in ("today.json", "issues.json", "threads.json")
               if not (data_dir / name).exists()]
    if missing:
        print(f"[reproject] 재료 누락 — {', '.join(missing)}. 투영 안 함")
        return 0

    site_threads = _read(data_dir / "threads.json")
    if not isinstance(site_threads, dict):
        raise ValueError("threads.json 이 객체가 아니다")
    store = thread_ledger.load_store()
    newer, why = ledger_is_newer(site_threads, store)
    print(f"[reproject] {why}")
    if not newer:
        return 0

    payload = thread_web.build_payload(store=store, now=datetime.now(KST))
    if not payload.get("visible"):
        print(f"[reproject] 투영 결과가 숨김 상태({payload.get('status')}) — 받은 파일을 그대로 둔다")
        return 0

    summary = reproject(data_dir, payload, dry_run=args.dry_run)
    top = " / ".join(f"#{i} {issue_id[:22]}→{thread_id or '빈칸'}"
                     for i, (issue_id, thread_id) in enumerate(summary["top"], 1))
    print(f"[reproject] {'(dry-run) ' if args.dry_run else ''}장기 스토리 {summary['threads']}개 · "
          f"issues thread_id {summary['stamped']}건 · today.json "
          f"{summary['today_before']}→{summary['today_after']}/{summary['today_rows']}건 · "
          f"상위 {TOP_N}건: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
