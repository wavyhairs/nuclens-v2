"""공론화 레인 → 관리자 텔레그램(TELEGRAM_ADMIN_CHAT_ID) 알림. crawl.yml 이 수집 뒤에 부른다.

왜: 개인 감시기(PC·GH 두 벌, 제목 해시 dedup)는 같은 사건을 매체마다 따로 보냈다 — 「의제 확정」
하나가 20건 넘게 왔다(2026-10-16 사용자). 여기는 레인이 붙인 사건 이름(event_key) 단위로 한 번만 보낸다.

  · 새 사건      한 줄 + 「외 N곳」. 대응 필요(response_needed)면 🔴 와 쟁점·준비할 것.
  · 알린 사건    새 기사가 붙으면 '이어진 보도' 한 줄에 건수만. 대응 필요가 새로 붙으면 🔴 로 다시 올린다.
  · 23~07시 KST  보내지 않고 커서도 그대로 — 아침 첫 회차가 묶어 보낸다.

상태: deliberation_notify.json {"cursor": archived_at ISO, "events": {event_key: {"at", "n", "red"}}}.
없으면 지금을 커서로 잡고 조용히 시작한다(과거 기사 폭탄 방지). 발송이 실패하면 커서를 옮기지 않는다.
채널로 폴백하지 않는다 — 대응 판단 문구는 내부용이다.

로컬 점검: python tools/deliberation_notify.py --dry-run [--since 2026-10-15T00:00]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import deliberation_lane as lane  # noqa: E402
import deliberation_pipeline as pipe  # noqa: E402

KST = timezone(timedelta(hours=9))
STATE_FILE = ROOT / "deliberation_notify.json"
EVENT_KEEP = timedelta(days=14)


def load_state(path: Path = STATE_FILE) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(state: dict, now: datetime, path: Path = STATE_FILE) -> None:
    cutoff = (now - EVENT_KEEP).isoformat()
    state["events"] = {k: v for k, v in state.get("events", {}).items() if v.get("at", "") >= cutoff}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def group(records: list[dict]) -> list[dict]:
    """event_key 로 묶고 대표를 고른다. 대응 필요 사건 먼저, 다음은 중요도순."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r.get("event_key") or r.get("title") or r.get("hash"), []).append(r)
    out = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: (not r.get("response_needed"), -int(r.get("significance") or 0),
                                 int(r.get("source_tier") or 3)))
        outlets = list(dict.fromkeys(r.get("publisher") or r.get("domain") or "" for r in rows))
        out.append({"key": key, "rep": rows[0], "n": len(rows), "outlets": [o for o in outlets if o],
                    "red": any(r.get("response_needed") for r in rows)})
    out.sort(key=lambda g: (not g["red"], -int(g["rep"].get("significance") or 0)))
    return out


def fmt_event(g: dict) -> str:
    r = g["rep"]
    url = html.escape(r.get("resolved_url") or r.get("url") or "", quote=True)
    who = g["outlets"][0] if g["outlets"] else ""
    if len(g["outlets"]) > 1:
        who += f" 외 {len(g['outlets']) - 1}곳"
    tags = " · ".join(x for x in (lane.LABELS_KR.get(r.get("stage"), ""),
                                  lane.LABELS_KR.get(r.get("stance"), ""),
                                  "검토" if r.get("confidence") == "low" else "") if x)
    head = "🔴 <b>대응 필요</b> " if g["red"] else "• "
    line = f"{head}<a href=\"{url}\">{html.escape(r.get('title') or '')}</a> ({html.escape(who)})"
    if tags:
        line += f" · {html.escape(tags)}"
    if r.get("summary"):
        line += f"\n   {html.escape(r['summary'])}"
    # 대표는 대응 필요 기사가 먼저 서도록 골랐다(group) — 쟁점·준비는 대표 것이다.
    if r.get("response_issue"):
        line += f"\n   쟁점: {html.escape(r['response_issue'])}"
    if r.get("response_prep"):
        line += f"\n   준비: {html.escape(r['response_prep'])}"
    return line


def build(records: list[dict], state: dict, now: datetime) -> tuple[str, dict]:
    """(보낼 글, 갱신할 events). 보낼 것이 없으면 빈 문자열."""
    events = dict(state.get("events") or {})
    fresh, follow = [], []
    for g in group(records):
        seen = events.get(g["key"])
        if seen is None or (g["red"] and not seen.get("red")):
            fresh.append(g)
        else:
            follow.append(g)
        prev = seen or {"n": 0, "red": False}
        events[g["key"]] = {"at": now.isoformat(), "n": prev["n"] + g["n"], "red": prev["red"] or g["red"]}
    if not fresh and not follow:
        return "", events
    reds = sum(g["red"] for g in fresh)
    head = f"<b>[공론화] {now.astimezone(KST):%m-%d %H시}</b> 새 사건 {len(fresh)}" + (f" · 🔴 {reds}" if reds else "")
    parts = [head] + [fmt_event(g) for g in fresh]
    if follow:
        parts.append("이어진 보도: " + ", ".join(f"{html.escape(g['key'])} +{g['n']}" for g in follow))
    return "\n\n".join(parts), events


def run(now: datetime, *, dry_run: bool = False, since: str | None = None, sender=None,
        archive_dir: Path = pipe.ARCHIVE_DIR, state_file: Path = STATE_FILE) -> str:
    state = load_state(state_file)
    if state is None and not since:
        if not dry_run:
            save_state({"cursor": now.isoformat(), "events": {}}, now, state_file)
        return "(첫 실행 — 지금부터 새 기사만 알린다)"
    state = state or {"cursor": since, "events": {}}
    cursor = since or state["cursor"]
    records = [r for r in pipe.shared.load_archive(archive_dir) if str(r.get("archived_at") or "") > cursor]
    if not records:
        return "(새 기사 없음)"
    hour = now.astimezone(KST).hour
    if not dry_run and (hour >= 23 or hour < 7):
        return f"(야간 보류 {len(records)}건 — 아침에 묶어 보냄)"
    text, events = build(records, state, now)
    if dry_run:
        return text or "(보낼 것 없음)"
    if text:
        (sender or send_admin)(text)   # 실패하면 예외 — 커서를 옮기지 않아 다음 회차에 다시 보낸다
    state["events"] = events
    state["cursor"] = max(str(r.get("archived_at") or "") for r in records)
    save_state(state, now, state_file)
    return text


def send_admin(text: str) -> None:
    import telegram_send as tg
    admin = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not admin:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID 미설정 — 채널로 폴백하지 않는다")
    tg.send_long_text(text, parse_mode="HTML", disable_preview=True, chat_id=admin)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="이 시각(ISO, UTC) 이후 기사로 미리보기")
    args = ap.parse_args()
    print(run(datetime.now(timezone.utc), dry_run=args.dry_run, since=args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
