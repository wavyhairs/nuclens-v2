"""근거 기사 부착 결과의 **회차 간 캐시** — 새 기사와 바뀐 이슈만 다시 계산한다.

왜 필요한가
-----------
`attach_evidence_articles` 는 미발송 기사(근거 풀) 하나하나를 고정된 카드
이슈들과 대조해 가장 잘 맞는 이슈에 근거로 붙인다. 이 풀은 최근 두 달치
미발송 기사 전부라 2026-09-23 실측 7,404건이고, 기사당 약 0.1초 × 두 패스
(LLM 판정 뒤 다시 묶고 다시 붙인다)라 빌드 30분 중 19분을 여기서 썼다.
그런데 3시간 사이 새로 들어오는 기사는 50~150건이고, 어제 붙인 기사가 오늘
다른 이슈에 붙을 일은 이슈가 바뀌지 않는 한 없다. 매 회차 7,400건을 전부
다시 대보는 것은 같은 답을 매번 다시 사는 일이다 — 표시 제목 캐시가 없던
때와 같은 함정이다(PR #169).

무엇을 기억하나
---------------
기사(해시)마다 **마지막 판정** 하나: 붙은 이슈 id · 점수 · 진단, 그 판정이
전제한 것들의 지문, 그리고 그 기사가 검수 큐에 올린 회색지대 쌍.

    entry = {
        "key":      기사 쪽 전제의 지문 — 이 기사와 얽힌 승인·거부 쌍, 벡터 유무,
                    설비 엔티티, 날짜·중요도. 하나라도 달라지면 다시 계산한다.
        "issue_id": 붙은 이슈 (없으면 None)
        "best_fp":  그 이슈의 지문 (아래 issue_fingerprint)
        "score", "diag": 부착 진단 — match_diagnostics 에 그대로 실린다
        "cands":    검수 후보 행들. 적중 시 다시 큐에 올린다 (안 올리면 판정을
                    못 받은 쌍이 영영 사라진다)
    }

봉투에는 **직전 저장 시점의 이슈 지문 표**(issue_fps)를 같이 둔다. 모든
항목은 그 표와 일관된다 — 매 패스 끝에 풀 밖의 항목을 지우고 표를 갱신하기
때문이다.

언제 다시 계산하나 (건전성의 근거)
-----------------------------------
기사 A 의 판정은 (A 의 특징, 각 이슈의 멤버, A 와 얽힌 override 쌍, 벡터 유무,
설비 엔티티, 임계값) 만으로 정해지고 다른 근거 기사와 무관하다(근거는 연결
기준이 되지 않는다 — attach 함수 docstring). 따라서:

  1. A 의 key 가 다르다             → 전체 재계산
  2. 붙었던 이슈가 사라졌거나 바뀌었다 → 전체 재계산
  3. 그 외                            → 캐시 판정을 쓰되, **직전 저장 이후 바뀌었거나
                                        새로 생긴 이슈(델타)** 에만 A 를 다시 대본다.
                                        델타에서 더 높은 점수가 나오면 그쪽으로 옮긴다.

바뀌지 않은 이슈에 대한 A 의 비교 결과는 캐시 때와 같으므로, 새 최댓값은
max(캐시 판정, 델타 판정) 이다. 유일한 차이는 어휘 예선 컷의 **밀어내기**다 —
전체 재계산이라면 새 이슈에 밀려 컷 밖으로 나갔을 옛 후보를 캐시는 그대로
기억한다. 이것은 손실이 아니라 보존이고, 예선 컷 자체가 감시 대상인
손실(issue-candidate:preselect-headroom)이므로 여기서 따로 흉내내지 않는다.

알려진 흔들림 하나: 로컬 벡터(build_local_embeddings)는 말뭉치 IDF 로 만들어져
기사가 늘면 값이 조금씩 움직인다. 캐시 판정은 그 판정을 만든 회차의 벡터를
전제하므로, 로컬 후보 문턱(0.18) 경계에 걸린 회색지대 쌍은 전체 재계산과
다를 수 있다. 실측(2026-09-23, 12시간 증분): 부착 2,394건은 전부 같았고
검수 후보 7,4xx 행 중 60행이 달랐다 — 전부 local_candidate 이고 점수
0.180~0.182 였다. 이 경계 흔들림은 캐시가 없어도 회차마다 이미 일어나는
현상이다(매 회차 말뭉치가 다르다). 로컬 벡터는 자동 병합에 쓰이지 않으므로
부착 자체는 흔들리지 않는다.

계측(SearchTelemetry)은 **다시 계산한 기사만** 본다. 캐시 적중은 방문도 비교도
아니다. 고정 표본 카나리(EVIDENCE_RETRIEVAL_CANARY)는 항상 다시 계산한다 —
그 표본의 뜻이 '색인이 놓친 것을 전수 대조로 잡는다'라서 캐시가 대신할 수
없다.

정책 지문
---------
임계값·창·엔티티 사전이 바뀌면 모든 판정의 근거가 달라진다. 봉투의
policy_fingerprint 가 다르면 파일 전체를 버리고 처음부터 계산한다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parent
CACHE_FILE = Path(os.environ.get("EVIDENCE_CACHE_FILE") or (ROOT / "evidence_attachments.json"))
CACHE_KEY = "attachments"
CACHE_VERSION = 1
CACHE_COMMENT = ("근거 기사 부착 캐시. 기사(해시)별 마지막 판정과 그 전제의 지문. "
                 "신원이 아니다 — 판정 재료는 build_data 가 매 회차 다시 든다.")
DISABLE_ENV = "NUCLENS_EVIDENCE_CACHE"
OVERRIDE_SETS = ("approved", "llm_approved", "rejected", "llm_rejected")


def enabled() -> bool:
    """환경변수로 끈다 — 검증 회차에서 캐시 없는 빌드와 대조할 때 쓴다."""
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in {"off", "0", "false", "no"}


def _jsonable(value: object) -> object:
    """진단 dict 에 set/tuple 이 섞여도 캐시 저장이 빌드를 세우지 않게 한다."""
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def issue_fingerprint(issue: Mapping, embeddings: Mapping | None,
                      local_embeddings: Mapping | None) -> str:
    """이슈 쪽 전제의 지문.

    부착 판정이 이슈에서 읽는 것: 멤버 전체의 해시(거부권·충돌 검사)와 날짜(창),
    멤버의 story_id(필수 후보 경로), 그리고 **마지막 세 멤버**의 벡터 유무
    (대조와 LSH 색인은 그 셋만 본다). 멤버 내용(제목·태그·지문)은 해시에 묶인
    불변값이라 해시로 대신한다.
    """
    members = list(issue.get("members") or [])
    rows = [
        [
            str(member.get("hash") or ""),
            str(member.get("story_id") or ""),
            str(member.get("article_date") or ""),
            str(member.get("briefing_date") or ""),
        ]
        for member in members
    ]
    vectors = [
        [
            str(member.get("hash") or ""),
            bool((embeddings or {}).get(str(member.get("hash") or ""))),
            bool((local_embeddings or {}).get(str(member.get("hash") or ""))),
        ]
        for member in members[-3:]
    ]
    return digest([rows, vectors])


def override_reverse_index(overrides: Mapping | None) -> dict[str, set[tuple[str, str]]]:
    """해시 → 그 해시가 한쪽인 override 쌍 {(집합 이름, 상대 해시)}. 네 집합 전부."""
    reverse: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for name in OVERRIDE_SETS:
        for pair_id in (overrides or {}).get(name) or ():
            left, separator, right = str(pair_id).partition("--")
            if not separator:
                continue
            reverse[left].add((name, right))
            reverse[right].add((name, left))
    return reverse


def article_key(article: Mapping, reverse: Mapping[str, set[tuple[str, str]]],
                embeddings: Mapping | None, local_embeddings: Mapping | None,
                facility_entities: Mapping | None) -> str:
    """기사 쪽 전제의 지문. 기사 내용은 해시에 묶여 있으니 해시 + 가변 전제만."""
    article_hash = str(article.get("hash") or "")
    return digest([
        article_hash,
        str(article.get("article_date") or ""),
        str(article.get("importance") or ""),
        str(article.get("story_id") or ""),
        bool((embeddings or {}).get(article_hash)),
        bool((local_embeddings or {}).get(article_hash)),
        sorted((facility_entities or {}).get(article_hash, ()) or ()),
        sorted(reverse.get(article_hash, ()) or ()),
    ])


def policy_fingerprint(limits: Mapping, *extra: object) -> str:
    """임계값·창·사전 지문. 다르면 캐시 전체가 무효다."""
    return digest([CACHE_VERSION, dict(limits), list(extra)])


class EvidenceCache:
    """한 빌드가 드는 캐시 상태. load → (attach 패스마다 갱신) → save."""

    def __init__(self, policy: str, path: Path | None = None) -> None:
        self.policy = policy
        self.path = Path(path or CACHE_FILE)
        self.entries: dict[str, dict] = {}
        self.issue_fps: dict[str, str] = {}
        self.stats: Counter = Counter()
        self.loaded_from_file = False
        self.reset_reason = ""

    # ── 파일 ──────────────────────────────────────────────
    @classmethod
    def load(cls, policy: str, path: Path | None = None) -> "EvidenceCache":
        cache = cls(policy, path)
        try:
            raw = json.loads(cache.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache.reset_reason = "missing"
            return cache
        if not isinstance(raw, dict):
            cache.reset_reason = "corrupt"
            return cache
        if raw.get("cache_version") != CACHE_VERSION:
            cache.reset_reason = "version"
            return cache
        if raw.get("policy_fingerprint") != policy:
            cache.reset_reason = "policy"
            return cache
        entries = raw.get(CACHE_KEY)
        fps = raw.get("issue_fps")
        if not isinstance(entries, dict) or not isinstance(fps, dict):
            cache.reset_reason = "corrupt"
            return cache
        cache.entries = {str(k): v for k, v in entries.items() if isinstance(v, dict)}
        cache.issue_fps = {str(k): str(v) for k, v in fps.items()}
        cache.loaded_from_file = True
        return cache

    def save(self, path: Path | None = None) -> None:
        payload = {
            "_comment": CACHE_COMMENT,
            "cache_version": CACHE_VERSION,
            "policy_fingerprint": self.policy,
            "issue_fps": dict(sorted(self.issue_fps.items())),
            CACHE_KEY: self.entries,
        }
        # 들여쓰기 없이 쓴다 — 항목 7천 건이면 들여쓰기만 3MB 다. 사람이 읽는
        # 파일이 아니라 다음 회차가 읽는 파일이다.
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=_jsonable) + "\n"
        target = Path(path or self.path)
        try:
            target.write_text(text, encoding="utf-8")
        except OSError:
            # 캐시는 없어도 되는 것이다. 못 써도 이번 빌드 결과는 온전하다.
            self.stats["save_failed"] += 1

    # ── 패스 안에서 ────────────────────────────────────────
    def delta_issue_ids(self, current_fps: Mapping[str, str]) -> set[str]:
        """직전 저장 이후 바뀌었거나 새로 생긴 이슈."""
        return {issue_id for issue_id, fp in current_fps.items()
                if self.issue_fps.get(issue_id) != fp}

    def lookup(self, article_hash: str, key: str, current_fps: Mapping[str, str]) -> dict | None:
        """전제가 그대로인 항목만 돌려준다. 아니면 None (= 전체 재계산)."""
        entry = self.entries.get(article_hash)
        if not isinstance(entry, dict) or entry.get("key") != key:
            return None
        issue_id = entry.get("issue_id")
        if issue_id is not None and current_fps.get(str(issue_id)) != entry.get("best_fp"):
            return None
        return entry

    def store(self, article_hash: str, *, key: str, issue_id: str | None, best_fp: str | None,
              score: float, diag: Mapping | None, cands: Iterable[Mapping]) -> None:
        self.entries[article_hash] = {
            "key": key,
            "issue_id": issue_id,
            "best_fp": best_fp,
            "score": float(score),
            "diag": dict(diag or {}),
            "cands": [dict(row) for row in cands],
        }

    def finish_pass(self, current_fps: Mapping[str, str], pool_hashes: Iterable[str]) -> None:
        """패스 끝 — 이슈 지문 표를 갱신하고 풀 밖 항목을 지운다.

        이 두 동작이 '모든 항목은 issue_fps 와 일관된다'는 불변식을 지킨다.
        풀 밖 기사(발송됐거나 창을 벗어난 기사)의 항목은 어느 이슈 상태를
        전제했는지 더는 보장할 수 없으므로 버린다 — 다시 들어오면 다시 계산한다.
        """
        keep = set(str(h) for h in pool_hashes)
        dropped = [h for h in self.entries if h not in keep]
        for h in dropped:
            del self.entries[h]
        self.stats["pruned"] += len(dropped)
        self.issue_fps = dict(current_fps)

    def summary(self) -> dict:
        return {
            "loaded": self.loaded_from_file,
            "reset_reason": self.reset_reason,
            "entries": len(self.entries),
            **{key: int(value) for key, value in sorted(self.stats.items())},
        }
