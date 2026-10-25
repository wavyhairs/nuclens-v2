"""발송 원장(told ledger) — "독자가 이미 들은 것"을 한 자리에서 읽는다. LLM 0회.

왜 있는가 (2026-10-25)
----------------------
10/25 국내 1번 『원안위, 고리 3·4호기 및 한빛 1·2호기 사고관리계획서 승인』은 10/15
회의 결과로, 10/17·10/18 에 이미 1번으로 나간 사건이었다. 9/26~10/25 발송 497건을
판정하면 52건(10.5%)이 재탕이었고 10건이 1번 자리였다(`eval_artifacts/repeat_send/`).

'이미 보냈나'를 묻는 자리가 선정 쪽에 일곱 개 있다(freshness·story_echo·continuity·
tracking·cross_day·B5·fresh_first). 각자 다른 기억(큐 3일·아카이브 14일·발송 7일/14일)과
다른 매처를 쓰고, 그 틈으로 재탕이 샌다 — 52건 중 16건은 판정기가 앞선 카드를 아예
받지 못했다. 10/25 는 같은 제목의 10/17 카드가 7일 창 밖이었고, 14일을 보는 continuity
는 '가장 최근 매칭 하나'(10/24 「27일 재심의」)만 봐서 가려졌다.

그런데 웹 이슈 원장(`issue_ledger.json`)은 10/13·16·17·18·24·25 카드를 이미 한 이슈로
묶어 두었다. 지식은 있는데 발송 **뒤**에만 쓰였다. 이 모듈은 그 지식을 발송 **전**에
읽는 단일 창구다. 설계 근거: `docs/2026-10-25-repeat-send-root-cause-review.md`.

무엇을 하는가
-------------
``load_told``  발송 기록(delivery_log 기사 줄)에서 오늘 이전 ``WINDOW_DAYS`` 일의 카드.
``related``    후보 하나에 대해 '같은 이야기로 이미 보낸 카드'를 찾는다. 세 길의 합집합:

    ① 어휘     — continuity 의 same_issue 매칭 **전부**(가장 최근 하나가 아니라)와
                 cross_day 와 같은 어휘 점수 상위 몇 건. 창은 7일이 아니라 WINDOW_DAYS.
    ② 근거     — 후보 story 의 기사 해시가 발송 카드의 story 해시와 겹침.
    ③ 이슈 확장 — ①②로 찾은 카드가 웹 이슈 원장에서 속한 이슈의 **다른 발송 카드 전부**.
                 어휘가 하나만 찾아도 원장이 그 이야기의 지난 카드를 다 데려온다.

무엇을 하지 않는가
------------------
* 판정하지 않는다. '새 사실이 있나'는 ``repeat_shadow`` 가 묻는다.
* 파일을 쓰지 않는다. 원장은 delivery_log 와 issue_ledger 에서 **유도**한다 — 새로
  커밋할 상태가 없으니 잃을 것도, 어긋날 것도 없다. 사실 단위 추출이 필요해지면
  그때 영속화한다(검토 문서 §5.1).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import issue_continuity

ROOT = Path(__file__).resolve().parent
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"
ISSUE_LEDGER_FILE = ROOT / "issue_ledger.json"

# 원장을 보는 날수. 9/26~10/25 재탕 52건의 앞선 발송 간격은 최대 14일이었고,
# 이 창은 프롬프트 길이만 늘린다(관련 카드는 MAX_RELATED 로 자른다).
WINDOW_DAYS = 30
# 어휘 후보. cross_day 의 3건보다 넓게 — 판정 입력이 아니라 이슈 확장의 씨앗이다.
LEXICAL_SEEDS = 5
LEXICAL_MIN_SCORE = 0.25
# 후보 하나에 붙이는 관련 카드 상한(프롬프트 길이).
MAX_RELATED = 8

_FIELDS = ("date", "hash", "title_kr", "title", "summary", "detail", "event_date",
           "region", "brief_rank", "story_article_hashes", "story_id", "story_fingerprint",
           "tags", "story_related_titles")


def load_told(today: str, *, days: int = WINDOW_DAYS, path: Path | None = None) -> list[dict]:
    """오늘(포함 안 함) 이전 ``days`` 일의 발송 카드. 못 읽으면 빈 목록."""
    path = path or DELIVERY_LOG_FILE
    try:
        start = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    except ValueError:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for line in lines:
        if '"record_type"' in line[:200]:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("record_type") or not row.get("hash"):
            continue
        day = str(row.get("date") or "")
        if not (start <= day < today):
            continue
        key = (day, str(row["hash"]))
        if key in seen:
            continue
        seen.add(key)
        rows.append({k: row.get(k) for k in _FIELDS if row.get(k) is not None})
    rows.sort(key=lambda r: (r["date"], r.get("brief_rank") or 99))
    return rows


def issue_index(path: Path | None = None) -> dict[str, str]:
    """기사 hash → 웹 이슈 id(이동했으면 최종 자리). 못 읽으면 빈 dict."""
    path = path or ISSUE_LEDGER_FILE
    try:
        issues = json.loads(path.read_text(encoding="utf-8")).get("issues") or {}
    except (OSError, ValueError, AttributeError):
        return {}

    def final(issue_id: str) -> str:
        hops = 0
        while hops < 10 and isinstance(issues.get(issue_id), dict) and issues[issue_id].get("moved_to"):
            issue_id = str(issues[issue_id]["moved_to"])
            hops += 1
        return issue_id

    index: dict[str, str] = {}
    for issue_id, record in issues.items():
        if not isinstance(record, dict):
            continue
        target = final(str(issue_id))
        for h in record.get("hashes") or ():
            index.setdefault(str(h), target)
    return index


def _story_hashes(row: dict) -> set[str]:
    hashes = {str(h) for h in row.get("story_article_hashes") or () if h}
    if row.get("hash"):
        hashes.add(str(row["hash"]))
    return hashes


def _lexical_score(candidate: dict, row: dict, own_anchors: set[str],
                   generic: frozenset[str]) -> float:
    sim = issue_continuity.title_similarity(candidate, row)
    shared = len((own_anchors & issue_continuity.named_anchors(row)) - generic)
    if sim < LEXICAL_MIN_SCORE and shared < 1:
        return 0.0
    return sim + 0.15 * shared


def related(candidate: dict, told: list[dict], issues: dict[str, str], cfg: dict,
            generic: frozenset[str] = frozenset(), *, limit: int = MAX_RELATED) -> list[dict]:
    """후보와 같은 이야기로 이미 보낸 카드. 날짜순. 각 행에 ``via`` (찾은 길)를 단다."""
    if not told:
        return []
    own_hash = str(candidate.get("hash") or "")
    own_story = _story_hashes(candidate)
    own_anchors = issue_continuity.named_anchors(candidate)
    found: dict[tuple[str, str], dict] = {}

    def add(row: dict, via: str, score: float) -> None:
        key = (row["date"], row["hash"])
        cur = found.get(key)
        if cur is None:
            found[key] = {**row, "via": [via], "_score": score}
        else:
            if via not in cur["via"]:
                cur["via"].append(via)
            cur["_score"] = max(cur["_score"], score)

    scored: list[tuple[float, int]] = []
    for index, row in enumerate(told):
        if row.get("hash") == own_hash:
            continue
        if own_story & _story_hashes(row):
            add(row, "evidence", 2.0)
        try:
            if issue_continuity.same_issue(candidate, row, cfg, generic):
                add(row, "continuity", 1.5)
        except Exception:  # noqa: BLE001 — 매처 하나의 실패가 원장 조회를 막지 않는다
            pass
        score = _lexical_score(candidate, row, own_anchors, generic)
        if score > 0:
            scored.append((score, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    # 어휘 씨앗은 이슈 확장(1.0)보다 먼저 자리를 받는다. 큰 이슈(대미투자처럼 한 달에
    # 스무 장)에서 확장 카드가 상한을 채워 정작 가장 닮은 카드를 밀어낸 일이 재생에서 5건.
    for score, index in scored[:LEXICAL_SEEDS]:
        add(told[index], "lexical", 1.2 + score)

    seed_issues = {issues[h] for row in found.values() for h in _story_hashes(row) if h in issues}
    for h in own_story:
        if h in issues:
            seed_issues.add(issues[h])
    if seed_issues:
        for row in told:
            if row.get("hash") == own_hash:
                continue
            if issues.get(str(row["hash"])) in seed_issues:
                add(row, "issue", 1.0)

    # 점수가 같으면(이슈 확장끼리) 최근 카드부터 — 독자 기억에 가까운 쪽이다.
    rows = sorted(found.values(), key=lambda r: (r["_score"], r["date"]), reverse=True)[:limit]
    rows.sort(key=lambda r: (r["date"], r.get("brief_rank") or 99))
    for row in rows:
        row.pop("_score", None)
    return rows
