"""운영 콘솔(`/admin`)이 남긴 사람 판정을 수집·선정 파이프라인에 얹는다.

왜 파일을 덮어쓰지 않고 '덧칠(overlay)'인가
--------------------------------------------
콘솔에서 `keywords.json` 을 통째로 다시 쓰게 하면, 콘솔이 읽은 것은 **지난 빌드의**
스냅샷이라 그 사이에 저장소에서 손으로 고친 내용을 조용히 되돌린다. 여기서는
기본 파일을 그대로 두고 "무엇을 더하고 무엇을 뺐다"만 적는다 — 덧칠은 손편집과
교환법칙이 성립하고, 한 줄씩 지울 수 있고, 지우면 정확히 원래 동작으로 돌아간다.

그래서 이 파일의 단위는 **항목(entry)** 이다. 항목 하나 = 사람이 한 판단 하나.
`id` 로 지우면 그 판단만 사라진다. 학습된 병합 규칙도 같은 항목이라 "잘못 배웠다"
싶을 때 지우는 경로가 설정 변경과 똑같다.

어디서 오나 — KV 가 버퍼, git 이 여전히 DB
-------------------------------------------
콘솔은 정적 사이트라 저장소에 쓸 수 없다. 그래서 Pages Function 이 판정을
Cloudflare KV 에 넣고(`functions/admin/api/overrides.js`),
`tools/sync_admin_overrides.py` 가 워크플로 시작에서 그것을 끌어와
`admin_overrides.json` 으로 커밋한다. 파이프라인은 KV 를 모른다 — 이 파일만 읽는다.
KV 를 못 읽어도 마지막으로 커밋된 파일이 그대로 살아 있다(조용한 되돌림 금지).

가드레일
--------
* stdlib 만. 외부 의존성 0.
* **절대 예외를 올리지 않는다.** 이 모듈이 죽으면 수집이 통째로 선다. 파일이
  깨졌으면 덧칠 없이 기본 동작으로 물러난다.
* 거부권(`merge_blocked`)은 `event_stage.stage_conflict` 와 같은 보수성을 쓴다 —
  **양쪽 다 말했고 겹치는 축이 하나도 없을 때만** 발동한다. 한쪽이 침묵하면
  판정하지 않는다. '표식이 없다'는 '다르다'가 아니라 '못 읽었다'이기 때문이다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

OVERRIDES_FILE = Path(
    os.environ.get("ADMIN_OVERRIDES_FILE") or Path(__file__).with_name("admin_overrides.json")
)

CONTRACT_VERSION = 1

# 항목 종류. 콘솔·동기화 스크립트·이 모듈이 같은 목록을 봐야 하므로 한 곳에 둔다.
KINDS = (
    # ── 병합 진단 ──
    "story_split",      # 같은 날 두 기사를 앞으로 접지 않는다 (hash 쌍)
    "issue_split",      # 날짜를 넘는 연결을 끊는다 → issue_match_overrides.rejected
    "issue_group_split",  # 한 이슈를 두 사건군으로 가른다 (쌍이 아니라 선)
    "issue_join",       # 끊긴 연결을 잇는다 → issue_match_overrides.approved
    "learned_rule",     # 분리에서 뽑아낸 판별축 — 새 쌍에도 적용된다
    # ── 수집 설정 ──
    "keyword_add", "keyword_remove",
    "anchor_add", "anchor_remove",
    "negative_add", "negative_remove",
    "anti_add", "anti_remove",
    "feed_add", "feed_disable",
    "official_disable",
    "tier_upsert", "tier_remove",
    # ── 학습된 검색어(신규 이슈 탐색) ──
    # 자동으로 생겼다 사라지는 임시 검색어를 사람이 넣고·빼고·붙잡는다.
    # 고정 키워드로 올리는 것은 별도 종류가 아니라 `keyword_add` 다 — 승격은
    # "이 말을 고정 목록에 넣는다"와 같은 판단이고, 통을 나누면 같은 말이 두
    # 목록에 서로 다른 이름으로 남는다.
    "learned_term_add", "learned_term_remove", "learned_term_keep",
)

_SPACE_RE = re.compile(r"\s+")


def _compact(text: object) -> str:
    """공백을 지운 소문자. '고리 2호기' 와 '고리2호기' 를 같게 본다."""
    return _SPACE_RE.sub("", str(text or "")).lower()


def _text(value: object, limit: int = 400) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()[:limit]


def _str_list(value: object, limit: int = 60) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        cleaned = _text(item, 200)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


# ── 파일 읽기 ───────────────────────────────────────────────────────────────
#
# mtime 을 열쇠로 캐시한다. news_bot 한 번 실행에서 수백 번 불리는데(거부권 판정이
# 기사 쌍마다 돈다) 매번 디스크를 치면 그것만으로 느려지고, 영구 캐시로 두면
# 테스트가 한 프로세스 안에서 파일을 갈아 끼울 수 없다.

_cache: dict | None = None
_cache_key: tuple | None = None


def _file_key(path: Path) -> tuple:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path), 0, 0)


def load(path: Path | None = None) -> dict:
    """`admin_overrides.json` 을 읽는다. 없거나 깨졌으면 빈 덧칠."""
    global _cache, _cache_key
    target = Path(path) if path is not None else OVERRIDES_FILE
    key = _file_key(target)
    if _cache is not None and _cache_key == key:
        return _cache

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        raw = {}
    except Exception:  # noqa: BLE001 — 이 모듈은 어떤 이유로도 수집을 세우지 않는다
        raw = {}

    if not isinstance(raw, dict):
        raw = {}
    entries = [e for e in (raw.get("entries") or []) if isinstance(e, dict)]
    data = {
        "version": raw.get("version") or CONTRACT_VERSION,
        "synced_at": _text(raw.get("synced_at"), 40),
        "updated_at": _text(raw.get("updated_at"), 40),
        "source": _text(raw.get("source"), 20),
        "entries": [e for e in entries if e.get("kind") in KINDS and e.get("id")],
    }
    _cache, _cache_key = data, key
    return data


def reload() -> None:
    """캐시를 버린다 — 테스트와 장기 실행 프로세스용."""
    global _cache, _cache_key
    _cache, _cache_key = None, None


def entries(kind: str = "", path: Path | None = None) -> list[dict]:
    rows = load(path)["entries"]
    if not kind:
        return list(rows)
    return [row for row in rows if row.get("kind") == kind]


def _enabled(row: dict) -> bool:
    """`enabled: false` 는 지우지 않고 잠시 꺼 둔 항목이다."""
    return row.get("enabled") is not False


# ── ① 병합 거부권 ───────────────────────────────────────────────────────────


def group_splits(path: Path | None = None) -> list[dict]:
    """"이 묶음은 사실 두 사건이다" — 사람이 그은 **선** 하나.

    항목 하나가 쌍 여러 개로 펼쳐진다. 왜 쌍을 하나씩 저장하지 않는가:

    ① 사람이 한 판단은 하나다. 지울 때도 하나여야 한다. 여덟 줄로 쪼개 두면
       그중 셋만 지운 상태가 만들어지고, 그 상태는 아무 뜻도 아니다.
    ② **쌍 하나로는 갈라지지 않는다.** `build_data.assign_issues` 의 합류는
       멤버 하나만 맞으면 되는 탐욕적 구조라(그 함수의 '클러스터 전체 거부권'
       주석), 막히지 않은 다른 멤버를 통해 같은 이슈로 도로 들어온다. 선을
       그으려면 선을 **가로지르는 쌍을 전부** 막아야 한다.

    ③ 그래서 화면도 쌍을 고르게 하지 않는다. 사람이 아는 것은 "이 넷과 저 둘이
       다른 사건"이지 "3번과 5번이 다른 사건"이 아니다. 쌍은 여기서 나온다.
    """
    out: list[dict] = []
    for row in entries("issue_group_split", path):
        if not _enabled(row):
            continue
        left = [h for h in (_text(v, 64) for v in _str_list(row.get("left_hashes"))) if h]
        right = [h for h in (_text(v, 64) for v in _str_list(row.get("right_hashes"))) if h]
        left = list(dict.fromkeys(left))
        right = [h for h in dict.fromkeys(right) if h not in left]
        if not left or not right:
            # 한쪽이 비면 가를 것이 없다. 저장 창구가 막지만, 손편집도 있다.
            continue
        out.append({
            "id": _text(row.get("id"), 64),
            "issue_id": _text(row.get("issue_id"), 80),
            "note": _text(row.get("note"), 300),
            "created_at": _text(row.get("created_at"), 40),
            "left_hashes": left,
            "right_hashes": right,
        })
    return out


def _group_split_pairs(path: Path | None = None) -> list[tuple[str, str, dict]]:
    """선을 가로지르는 모든 쌍. (왼쪽, 오른쪽, 원래 항목)"""
    return [
        (left, right, split)
        for split in group_splits(path)
        for left in split["left_hashes"]
        for right in split["right_hashes"]
    ]


def blocked_pairs(path: Path | None = None) -> set[frozenset[str]]:
    """관리자가 "이 둘은 다른 사건"이라고 못 박은 hash 쌍.

    사건군을 가른 판정(`issue_group_split`)도 여기 들어온다. 관리자가 말한 것은
    "이 넷과 저 둘은 다른 사건"이고, 그 말은 날짜를 넘는 연결에만 해당하지
    않는다 — 같은 날 나란히 실려도 한 카드로 접히면 안 된다.
    """
    pairs: set[frozenset[str]] = set()
    for row in entries("story_split", path):
        if not _enabled(row):
            continue
        left = _text(row.get("left_hash"), 64)
        right = _text(row.get("right_hash"), 64)
        if left and right and left != right:
            pairs.add(frozenset((left, right)))
    for left, right, _split in _group_split_pairs(path):
        if left != right:
            pairs.add(frozenset((left, right)))
    return pairs


def learned_rules(path: Path | None = None) -> list[dict]:
    """분리 판정에서 뽑아낸 판별축. 원래 쌍이 아닌 새 조합에도 적용된다.

    이것이 '학습'이라고 부를 수 있는 유일한 부분이다. hash 쌍 차단은 그 쌍
    하나에만 듣는 기록이고, 여기서는 사람이 "이 둘이 갈리는 이유는 설비가
    다르기 때문"이라고 말해 준 축을 다음 사건에도 쓴다.
    """
    rules: list[dict] = []
    for row in entries("learned_rule", path):
        if not _enabled(row):
            continue
        left = [_compact(t) for t in _str_list(row.get("left_terms"))]
        right = [_compact(t) for t in _str_list(row.get("right_terms"))]
        left = [t for t in left if t]
        right = [t for t in right if t]
        if not left or not right:
            continue
        rules.append({
            "id": _text(row.get("id"), 64),
            "label": _text(row.get("label"), 120),
            "axis": _text(row.get("axis"), 40) or "custom",
            "note": _text(row.get("note"), 300),
            "left_terms": left,
            "right_terms": right,
        })
    return rules


def article_text(article: dict) -> str:
    """판정에 쓸 기사 문자열. 번역 제목과 원문 제목을 함께 본다.

    `event_stage.article_stages` 와 같은 이유다 — 한쪽만 보면 국내 기사나 원문
    표현 중 하나를 통째로 놓친다. 합쳐 보면 축을 더 많이 말하게 되고, 축을 더
    많이 말할수록 거부권은 **덜** 발동한다(안전한 방향).
    """
    if not isinstance(article, dict):
        return ""
    return _compact(f"{article.get('title_kr') or ''} {article.get('title') or ''}")


def _rule_side(text: str, rule: dict) -> tuple[bool, bool]:
    return (
        any(term in text for term in rule["left_terms"]),
        any(term in text for term in rule["right_terms"]),
    )


def rule_conflict(rule: dict, left_text: str, right_text: str) -> bool:
    """한쪽은 왼쪽 축만, 다른 쪽은 오른쪽 축만 말할 때에만 참.

    `event_stage.stage_conflict` 와 같은 보수성이다. 한쪽이 아무 축도 말하지
    않거나 두 축을 다 말하면 판정하지 않는다 — 애매하면 접는 쪽(기존 동작)에
    맡긴다. 사람이 규칙 하나를 잘못 배워도 무관한 기사를 갈라 놓지 않게 하는
    것이 여기서 제일 중요하다.
    """
    ll, lr = _rule_side(left_text, rule)
    rl, rr = _rule_side(right_text, rule)
    return (ll and not lr and rr and not rl) or (lr and not ll and rl and not rr)


def merge_blocked(left: dict, right: dict, path: Path | None = None) -> dict | None:
    """두 기사를 접으면 안 되는가. 막을 이유가 있으면 진단 레코드, 없으면 None.

    반환 형태는 `event_stage.veto_record` 와 같은 모양이라 운영 콘솔의
    '붙이지 않은 판단' 칸이 두 종류를 한 목록으로 그린다.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    left_hash = _text(left.get("hash"), 64)
    right_hash = _text(right.get("hash"), 64)

    if left_hash and right_hash and frozenset((left_hash, right_hash)) in blocked_pairs(path):
        return _veto(left, right, kind="admin_split", label="관리자 분리",
                     explanation="관리자가 다른 사건으로 판정한 조합입니다")

    rules = learned_rules(path)
    if not rules:
        return None
    left_text, right_text = article_text(left), article_text(right)
    if not left_text or not right_text:
        return None
    for rule in rules:
        if rule_conflict(rule, left_text, right_text):
            return _veto(
                left, right, kind="learned_rule", label=rule["label"] or "학습 규칙",
                explanation=(f"학습된 판별축 — {rule['label']}" if rule["label"]
                             else "학습된 판별축"),
                rule_id=rule["id"],
            )
    return None


def _veto(left: dict, right: dict, *, kind: str, label: str,
          explanation: str, rule_id: str = "") -> dict:
    return {
        "kind": kind,
        "rule_id": rule_id,
        "rule_label": label,
        "left_hash": _text(left.get("hash"), 64),
        "right_hash": _text(right.get("hash"), 64),
        "left_title": _text(left.get("title_kr") or left.get("title"), 120),
        "right_title": _text(right.get("title_kr") or right.get("title"), 120),
        "left_stage_label": label,
        "right_stage_label": label,
        "explanation": explanation,
    }


def issue_pair_overrides(path: Path | None = None) -> dict[str, list[dict]]:
    """이슈 계층 판정 — `issue_match_overrides.json` 과 같은 형태로 낸다.

    저장소 파일과 콘솔 판정이 **같은 자료구조**여야 build_data 가 둘을 구분하지
    않고 합칠 수 있다. 콘솔에서 온 것인지는 `origin` 으로만 구분한다.
    """
    out: dict[str, list[dict]] = {"approved": [], "rejected": []}
    for kind, bucket in (("issue_join", "approved"), ("issue_split", "rejected")):
        for row in entries(kind, path):
            if not _enabled(row):
                continue
            left = _text(row.get("left_hash"), 64)
            right = _text(row.get("right_hash"), 64)
            if not left or not right or left == right:
                continue
            out[bucket].append({
                "left_hash": left,
                "right_hash": right,
                "reviewed_at": _text(row.get("created_at"), 10),
                "note": _text(row.get("note"), 300),
                "origin": "admin_console",
                "entry_id": _text(row.get("id"), 64),
            })
    # 사건군을 가른 판정은 선을 가로지르는 쌍 전부로 펼쳐진다(group_splits 참조).
    for left, right, split in _group_split_pairs(path):
        if left == right:
            continue
        out["rejected"].append({
            "left_hash": left,
            "right_hash": right,
            "reviewed_at": split["created_at"][:10],
            "note": split["note"],
            "origin": "admin_console",
            "entry_id": split["id"],
        })
    return out


# ── ② 수집 설정 덧칠 ────────────────────────────────────────────────────────


def keywords_config(base: dict, path: Path | None = None) -> dict:
    """`keywords.json` 에 그룹별 추가·삭제를 얹는다.

    그룹을 새로 만들지는 않는다. 그룹은 키워드뿐 아니라 앵커(원자력 문맥 확인)와
    제외어를 함께 갖는 한 벌이라, 빈 그룹이 생기면 앵커 없이 검색이 나가고 그건
    잡음을 그대로 통과시킨다.
    """
    if not isinstance(base, dict):
        return {}
    rows = load(path)["entries"]
    if not rows:
        return base

    merged: dict = {}
    for name, group in base.items():
        merged[name] = dict(group) if isinstance(group, dict) else group

    def group_of(name: str) -> dict | None:
        group = merged.get(name)
        return group if isinstance(group, dict) else None

    # 삭제를 먼저 모아 둔다 — 같은 말을 지웠다가 다시 넣은 이력이 있으면
    # '나중 판단이 이긴다'가 되어야 하는데, 항목에는 순서가 있으므로 순차 적용한다.
    for row in rows:
        if not _enabled(row):
            continue
        kind = row.get("kind")
        group = group_of(_text(row.get("group"), 80))
        if group is None:
            continue
        value = _text(row.get("value"), 200)
        if not value:
            continue
        if kind in ("keyword_add", "anchor_add"):
            field = "keywords" if kind == "keyword_add" else "anchors"
            items = list(group.get(field) or [])
            if value not in items:
                items.append(value)
            group[field] = items
        elif kind in ("keyword_remove", "anchor_remove"):
            field = "keywords" if kind == "keyword_remove" else "anchors"
            group[field] = [k for k in (group.get(field) or []) if str(k) != value]
        elif kind in ("negative_add", "negative_remove"):
            terms = _negative_terms(group.get("negative_terms"))
            token = value.lstrip("-").strip()
            if not token:
                continue
            if kind == "negative_add":
                if token not in terms:
                    terms.append(token)
            else:
                terms = [t for t in terms if t != token]
            group["negative_terms"] = " ".join(f"-{t}" for t in terms)
    return merged


def _negative_terms(value: object) -> list[str]:
    """`"-주가 -채용"` → `["주가", "채용"]`. 화면과 저장 형식을 오가는 유일한 지점."""
    out: list[str] = []
    for token in str(value or "").split():
        cleaned = token.lstrip("-").strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def negative_terms_list(value: object) -> list[str]:
    """콘솔이 제외어를 칩으로 그릴 수 있게 공개한다."""
    return _negative_terms(value)


def anti_keywords(base: list[str], path: Path | None = None) -> list[str]:
    """공통 제외어(제목에 걸리면 버린다)."""
    out = [str(k) for k in (base or [])]
    for row in entries(path=path):
        if not _enabled(row):
            continue
        value = _text(row.get("value"), 120)
        if not value:
            continue
        if row.get("kind") == "anti_add" and value not in out:
            out.append(value)
        elif row.get("kind") == "anti_remove":
            out = [k for k in out if k != value]
    return out


def learned_terms(path: Path | None = None) -> dict:
    """학습된 검색어에 대한 사람 판정 — `adaptive_discovery` 가 읽는다.

    반환:
      * `added`   — 사람이 직접 넣은 임시 검색어. 점수 문턱을 거치지 않는다.
      * `blocked` — 뺀 말. **다시 만들지 않는다.** 자동 폐기(냉각)와 다르다:
        냉각은 기간이 지나면 풀리지만, 사람이 뺀 말은 판정을 지우기 전까지
        영영 안 만든다. 안 그러면 관리자가 지운 말이 이틀 뒤에 되살아난다.
      * `pinned`  — 붙잡은 말. 만료·성과 없음으로 자동 폐기되지 않는다.

    같은 말을 넣었다 뺀 이력이 있으면 **나중 판단이 이긴다** — 항목에 순서가
    있으므로 순차 적용한다(`keywords_config` 와 같은 규칙).
    """
    added: dict[str, dict] = {}
    blocked: set[str] = set()
    pinned: set[str] = set()
    for row in load(path)["entries"]:
        if not _enabled(row):
            continue
        kind = row.get("kind")
        if kind not in ("learned_term_add", "learned_term_remove", "learned_term_keep"):
            continue
        value = _text(row.get("value") or row.get("term"), 60)
        if not value:
            continue
        key = _compact(value)
        if kind == "learned_term_add":
            blocked.discard(key)
            added[key] = {
                "term": value,
                "query": _text(row.get("query"), 80),
                "type": _text(row.get("type"), 20),
                "note": _text(row.get("note"), 300),
                "id": _text(row.get("id"), 64),
            }
        elif kind == "learned_term_remove":
            blocked.add(key)
            added.pop(key, None)
            pinned.discard(key)
        else:
            blocked.discard(key)
            pinned.add(key)
    return {"added": list(added.values()), "blocked": blocked, "pinned": pinned}


def _feed_key(row: dict) -> str:
    """수집원 식별자. URL 이 열쇠고, 없으면 이름으로 떨어진다."""
    return _text(row.get("url"), 400) or _text(row.get("name"), 120)


def _same_feed_key(url: str) -> str:
    """중복 판정용으로 URL 을 고른다.

    같은 질의가 **인코딩만 달라** 두 줄로 서는 것을 막는다. 코드가 지은 주소는
    quote_plus 라 공백이 `+` 인데(news_bot.KR_NUCLEAR_ORG_FEEDS), 콘솔에 붙여
    넣은 주소는 브라우저를 거쳐 `%20` 으로 온다. 질의 문자열에서 둘은 같은
    글자다 — 실제로 서울대 NIFTEP 피드가 그렇게 두 줄로 서서 매 수집마다 같은
    피드를 두 번 걸었다(2026-08-29).

    도메인으로 묶지 않는 이유: 같은 도메인에 다른 피드가 정당하게 둘 있다
    (energy.gov 의 DOE 뉴스룸과 DOE 원자력국은 서로 다른 RSS 다). 여기서
    막아야 하는 것은 '같은 도메인'이 아니라 '같은 주소'다.

    질의 순서까지 고르는 것은, 콘솔에서 손으로 옮겨 적으며 순서가 바뀌어도
    같은 요청이기 때문이다. 주소를 못 읽으면 원문을 그대로 열쇠로 쓴다 —
    이 모듈은 예외를 올리지 않는다(머리말 가드레일).
    """
    try:
        parts = urlsplit(url)
        query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
        return urlunsplit((
            parts.scheme.lower(), parts.netloc.lower(), parts.path, query, "",
        ))
    except ValueError:
        return url


def rss_sources(base: list[dict], path: Path | None = None) -> list[dict]:
    """RSS 수집원 목록에 콘솔 추가분을 붙이고 중지분을 뺀다."""
    rows = load(path)["entries"]
    if not rows:
        return list(base or [])
    disabled = {
        _text(row.get("target"), 400)
        for row in rows if row.get("kind") == "feed_disable" and _enabled(row)
    }
    out = [row for row in (base or []) if _feed_key(row) not in disabled]
    # 이미 서 있는 주소. 콘솔 추가분이 여기 겹치면 붙이지 않는다 — 내장 피드를
    # 모르는 사람이 같은 것을 한 번 더 넣어도 수집이 두 배가 되지 않게.
    seen = {_same_feed_key(_text(row.get("url"), 400)) for row in out}
    for row in rows:
        if row.get("kind") != "feed_add" or not _enabled(row):
            continue
        url = _text(row.get("url"), 400)
        if not url.startswith(("http://", "https://")) or _feed_key(row) in disabled:
            continue
        if _same_feed_key(url) in seen:
            continue
        seen.add(_same_feed_key(url))
        feed = {
            "url": url,
            "name": _text(row.get("name"), 120) or url,
            "domain_label": _text(row.get("domain_label"), 120),
        }
        keywords = _str_list(row.get("require_keywords"), 24)
        if keywords:
            # news_bot.passes_source_keyword_gate 는 소문자 부분일치로 본다.
            feed["require_keywords"] = tuple(k.lower() for k in keywords)
        if row.get("resolve_publisher"):
            feed["resolve_publisher"] = True
        out.append(feed)
    return out


def official_sources(base: list[dict], path: Path | None = None) -> list[dict]:
    """기관 직접 수집은 **중지만** 가능하다.

    추가를 막는 이유: 각 기관 게시판은 전용 파서(`kind`)가 코드에 있어야 읽힌다
    (`khnp_html`·`nssc_json`…). 화면에서 URL 만 넣게 하면 파서 없는 항목이 매
    수집마다 조용히 0건을 내고, 그건 '그 기관이 조용한 날'과 구분되지 않는다.
    """
    disabled = {
        _text(row.get("target"), 400)
        for row in entries("official_disable", path) if _enabled(row)
    }
    return [row for row in (base or []) if _feed_key(row) not in disabled]


_TIER_KEYS = {1: "tier1", 2: "tier2", 3: "tier3"}


def sources_config(base: dict, path: Path | None = None) -> dict:
    """`sources.json` 에 등급·성격·근거 역할 수정을 얹는다.

    도메인이 열쇠다. 같은 도메인을 다른 등급으로 옮기면 옛 등급에서 빠지고 새
    등급으로 들어간다 — 두 등급에 동시에 있으면 `sources.credibility` 가 먼저
    만난 쪽을 쓰고, 그건 파일 순서에 따라 달라진다.
    """
    if not isinstance(base, dict):
        return {}
    rows = load(path)["entries"]
    if not rows:
        return base

    merged = dict(base)
    buckets: dict[str, list[dict]] = {}
    for key in _TIER_KEYS.values():
        buckets[key] = [dict(r) for r in (base.get(key) or []) if isinstance(r, dict)]

    def drop(domain: str) -> dict | None:
        found = None
        for key, rows_ in buckets.items():
            for row in list(rows_):
                if _text(row.get("domain"), 200).lower() == domain:
                    found = found or row
                    rows_.remove(row)
        return found

    for row in rows:
        if not _enabled(row):
            continue
        kind = row.get("kind")
        domain = _text(row.get("domain"), 200).lower()
        if kind not in ("tier_upsert", "tier_remove") or not domain:
            continue
        previous = drop(domain)
        if kind == "tier_remove":
            continue
        try:
            tier = int(row.get("tier") or 0)
        except (TypeError, ValueError):
            tier = 0
        if tier not in _TIER_KEYS:
            tier = int((previous or {}).get("rank_tier") or 3)
            tier = tier if tier in _TIER_KEYS else 3
        entry = dict(previous or {})
        entry["domain"] = domain
        entry["name"] = _text(row.get("name"), 120) or entry.get("name") or domain
        entry["rank_tier"] = tier
        for field in ("source_type", "evidence_role"):
            value = _text(row.get(field), 40)
            if value:
                entry[field] = value
        aliases = _str_list(row.get("aliases"), 24)
        if aliases:
            entry["aliases"] = aliases
        entry.setdefault("aliases", [entry["name"]])
        entry.setdefault("source_type", "unknown")
        entry.setdefault("evidence_role", "unknown")
        buckets[_TIER_KEYS[tier]].append(entry)

    for key, rows_ in buckets.items():
        merged[key] = rows_
    return merged


# ── ③ 진단 ─────────────────────────────────────────────────────────────────


def summary(path: Path | None = None) -> dict:
    """콘솔·빌드 로그가 "지금 무엇이 얹혀 있나"를 한 줄로 말할 수 있게."""
    data = load(path)
    counts: dict[str, int] = {}
    for row in data["entries"]:
        kind = str(row.get("kind"))
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "version": data["version"],
        "synced_at": data["synced_at"],
        "updated_at": data["updated_at"],
        "source": data["source"],
        "total": len(data["entries"]),
        "counts": counts,
        "entry_ids": [_text(row.get("id"), 64) for row in data["entries"]],
    }
