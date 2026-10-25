"""원문 대조 격리 재검(integrity_shadow)의 '원문과 맞음' 판정을 원문으로 다시 확인한다 — 일회성.

왜
--
원문 대조 게이트가 격리한 기사를 원문 검사기(summary_verify)에 다시 물은 그림자 기록이
10/10~10/25 에 78건 쌓였고, 검사기는 67건을 '원문과 맞음'이라 했다. 그 판정을 격리에
반영하려면 '맞음'이 정말 게이트 오탐인지 원문으로 봐야 한다(tools/integrity_shadow_report.py
머리말의 기준 1). 개발 환경에서는 언론사에 접속할 수 없어 Actions 에서 돌린다.

무엇을 하나
-----------
기사마다 원문을 `article_body.fetch_one` 으로 받고(수집과 같은 경로·같은 Google News 우회),
게이트가 걸고 넘어진 구절마다 원문에서 그 값·이름이 나오는 문장을 몇 개 뽑아 찍는다.

    수치  — 값의 핵심 숫자(쉼표·단위 제외)가 나오는 자리
    대상  — 이름과 entity_registry 의 별칭이 나오는 자리

판정은 사람(또는 리뷰어)이 이 출력을 읽고 한다. 이 도구는 판정하지 않는다.

저작권 계약
-----------
`article_body` 머리말대로 본문은 **저장하지 않는다.** 저장소에 아무것도 쓰지 않고, 로그와
짧은 보존 기간의 산출물에는 구절 주변 짧은 발췌(SNIPPET_CHARS)만 남긴다.

    python tools/integrity_source_audit.py --out audit.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNIPPET_CHARS = 70
MAX_SNIPPETS = 3

_CONCERN_RE = re.compile(r"^\s*(제목|요약|상세)?\s*:?\s*(수치|대상)\s+(.+?)(?:\(원문에서 읽은 값 (.+)\))?\s*$")


def latest_rows(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not row.get("called"):
            continue
        cur = latest.get(row.get("hash"))
        if cur is None or str(row.get("checked_at")) >= str(cur.get("checked_at")):
            latest[row["hash"]] = row
    return latest


def link_index() -> dict[str, dict]:
    """hash → {link, title}. 보류 상태(sent.json) → 아카이브 → curated 순."""
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(ROOT / "archive" / "2026-*.jsonl"))):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            link = a.get("resolved_url") or a.get("url")
            if a.get("hash") and link:
                out.setdefault(a["hash"], {"link": link, "title": a.get("title") or ""})
    try:
        curated = json.loads((ROOT / "curated.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        curated = {}
    for h, a in (curated.items() if isinstance(curated, dict) else []):
        link = isinstance(a, dict) and (a.get("link") or a.get("url"))
        if link:
            out.setdefault(h, {"link": link, "title": a.get("title") or ""})
    try:
        holds = json.loads((ROOT / "sent.json").read_text(encoding="utf-8")).get("integrity_holds") or {}
    except (OSError, ValueError):
        holds = {}
    for h, row in holds.items():
        if isinstance(row, dict) and row.get("link"):
            out[h] = {"link": row["link"], "title": row.get("title") or ""}
    return out


def aliases() -> dict[str, set[str]]:
    try:
        ents = json.loads((ROOT / "entity_registry.json").read_text(encoding="utf-8")).get("entities") or []
    except (OSError, ValueError):
        ents = []
    table: dict[str, set[str]] = {}
    for e in ents:
        names = {str(n) for n in [e.get("name_kr"), e.get("name_en"), *(e.get("aliases") or [])] if n}
        for n in names:
            table.setdefault(n.lower(), set()).update(names)
    return table


def parse_concern(text: str) -> dict | None:
    m = _CONCERN_RE.match(text or "")
    if not m:
        return None
    return {"field": m.group(1) or "", "kind": m.group(2), "value": m.group(3).strip(),
            "gate_read": (m.group(4) or "").strip()}


def number_keys(value: str) -> list[str]:
    """'1,688억원' → ['1,688', '1688'] · '1.2조원' → ['1.2'] · '173,000,000달러' → ['173,000,000', '173000000', '173']."""
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", value)
    keys: list[str] = []
    for n in nums:
        plain = n.replace(",", "")
        for k in (n, plain):
            if k and k not in keys:
                keys.append(k)
        # 0 이 길게 붙은 값은 앞자리만으로도 찾는다(173,000,000달러 ↔ $173 million)
        stripped = re.sub(r"(,?000)+$", "", n)
        if stripped and stripped not in keys and stripped != n:
            keys.append(stripped)
    keys += [k for k in amount_variants(value) if k not in keys]
    return [k for k in keys if len(k.replace(",", "").replace(".", "")) >= 1]


_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}


def amount_variants(value: str) -> list[str]:
    """같은 금액의 다른 표기. '1.2조원' → ['1조2000억', '1조 2000억', '1조2천억', '1조 2천억'],
    '7,600,000,000,000달러' → ['7.6', '7조6천억', '7조 6천억'] (조·trillion 의 앞자리)."""
    m = re.match(r"\s*(\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천)?", value)
    if not m:
        return []
    amount = float(m.group(1).replace(",", "")) * _UNIT.get(m.group(2) or "", 1.0)
    out: list[str] = []
    jo, rest = divmod(round(amount / 1e8), 10000)        # 조 · 억 단위로 쪼갠다
    if amount >= 1e12 and rest:
        thousands = rest / 1000
        out += [f"{jo}조{rest}억", f"{jo}조 {rest}억"]
        if thousands == int(thousands):
            out += [f"{jo}조{int(thousands)}천억", f"{jo}조 {int(thousands)}천억"]
    for scale in (1e12, 1e9, 1e6):                       # trillion · billion · million 의 앞자리
        if amount >= scale:
            mantissa = f"{amount / scale:g}"
            if "." in mantissa or amount / scale >= 10:
                out.append(mantissa)
            break
    return out


def snippets(body: str, keys: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[int] = set()
    for key in keys:
        for m in re.finditer(re.escape(key), body, flags=re.IGNORECASE):
            bucket = m.start() // SNIPPET_CHARS
            if bucket in seen:
                continue
            seen.add(bucket)
            lo, hi = max(0, m.start() - SNIPPET_CHARS), min(len(body), m.end() + SNIPPET_CHARS)
            out.append(re.sub(r"\s+", " ", body[lo:hi]).strip())
            if len(out) >= MAX_SNIPPETS:
                return out
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="integrity_source_audit.jsonl")
    parser.add_argument("--verdicts", default="ok,contradiction,unsupported,quote_unverified")
    args = parser.parse_args()

    import requests
    import article_body

    wanted = set(args.verdicts.split(","))
    rows = latest_rows(ROOT / "integrity_shadow.jsonl")
    links = link_index()
    alias = aliases()
    session = requests.Session()
    results = []
    for h, row in sorted(rows.items(), key=lambda kv: kv[1].get("checked_at", "")):
        if row.get("verdict") not in wanted:
            continue
        src = links.get(h) or {}
        body, status = ("", "no_link")
        if src.get("link"):
            body, status = article_body.fetch_one(src["link"], session, title=src.get("title", ""))
        rec = {"hash": h, "verdict": row.get("verdict"), "title_kr": row.get("title_kr"),
               "claim": row.get("claim"), "reason": row.get("reason"),
               "source_title": row.get("source_title") or src.get("title"),
               "source_kind_at_check": row.get("source_kind"), "fetch": status,
               "body_chars": len(body), "concerns": []}
        for text in row.get("concerns") or []:
            c = parse_concern(text)
            if c is None:
                rec["concerns"].append({"raw": text, "unparsed": True})
                continue
            if c["kind"] == "수치":
                keys = number_keys(c["value"])
            else:
                keys = sorted(alias.get(c["value"].lower(), {c["value"]}), key=len, reverse=True)
                if c["value"] not in keys:
                    keys.insert(0, c["value"])
            hits = snippets(body, keys) if body else []
            rec["concerns"].append({**c, "keys": keys[:6], "found": bool(hits), "snippets": hits})
        results.append(rec)
        print(f"\n### {rec['verdict']} {h[:8]} 「{(rec['title_kr'] or '')[:60]}」")
        print(f"    원문: {(rec['source_title'] or '')[:70]} · 수집 {status} · 본문 {len(body)}자"
              f" · 검사 당시 원문 {rec['source_kind_at_check']}")
        if rec["claim"]:
            print(f"    걸린 문장: {rec['claim'][:120]}")
        for c in rec["concerns"]:
            if c.get("unparsed"):
                print(f"    - (해석 불가) {c['raw']}")
                continue
            read = f" · 게이트가 읽은 값 {c['gate_read']}" if c["gate_read"] else ""
            print(f"    - {c['field']} {c['kind']} {c['value']}{read} · 찾는 말 {c['keys'][:4]} · "
                  f"{'원문에 있음' if c['found'] else '원문에서 못 찾음'}")
            for s in c["snippets"]:
                print(f"        ▸ …{s}…")
    Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
                              encoding="utf-8")
    fetched = sum(1 for r in results if r["body_chars"])
    found = sum(1 for r in results for c in r["concerns"] if c.get("found"))
    total = sum(1 for r in results for c in r["concerns"] if not c.get("unparsed"))
    print(f"\n요약: 기사 {len(results)}건 · 원문 확보 {fetched}건 · 걸린 구절 {total}개 중 원문에서 찾음 {found}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
