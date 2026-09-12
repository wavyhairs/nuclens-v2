"""이슈(=사건) 신원의 authority. **id 를 내용에서 만들지 않고 원장에서 물려받는다.**

왜 필요한가
-----------
`cluster_selected_articles` 는 매 빌드 카탈로그를 **처음부터 다시 조립한다.** 그리고
id 는 그때 첫 멤버의 해시에서 만든다(`issue-{article['hash']}`). 즉 신원이 그 빌드의
클러스터 모양에 딸려 있다 — 묶음이 한 칸만 달라지면 주소가 바뀐다.

2026-09-12 실측(9/1 빌드 447건 ↔ 9/12 라이브 525건):

    사라진 id 75건(16.8%) · 표본 5건 전부 HTTP 404
    사라진 것들의 first_seen 은 창 한복판(08-26~08-31) — **노화가 아니다**
    발행된 rss.xml 의 고유 이슈 링크 185건 중 33건(17.8%)이 이미 깨져 있었다

그런데 같은 대조에서 **75건 전부** 제 근거 기사를 지금 어느 이슈가 들고 있는지
말할 수 있었다.

    소유자가 하나 → 그대로 물려받을 수 있다        59건
    소유자가 둘 이상 → 병합이므로 규칙이 필요하다   16건
    증거가 창 밖으로 나가 못 찾음                    0건

**id 가 죽은 것이 아니라, 살아 있는 증거를 아무도 묻지 않았다.** `issue_ledger` 는
그 이동을 이미 기록하지만 **사후**에 리다이렉트 쪽지를 만든다. 이 모듈은 같은 증거를
**id 를 정하기 전에** 물어서 이동 자체를 없앤다.

무엇을 증거로 삼는가 — 기사 해시뿐이다
--------------------------------------
제목 유사도로 신원을 판정하면 이 저장소가 여러 번 겪은 오병합을 주소 체계에까지
들인다(`issue_ledger` 머리말과 같은 판단). 그래서 재료는 해시 하나다.

해시가 배타적이어야 이 판정이 성립한다. 2026-09-12 라이브 525건 실측:

    related_articles 의 해시  →  두 이슈가 함께 가진 것 0건   (배타적 ✔)
    story_members 의 해시     →  두 이슈가 함께 가진 것 120건 (공유됨 ✘)

`story_members` 는 수집 dedup 이 접어 둔 원본 묶음이라 여러 이슈에 같이 실린다.
그래서 증거는 `members`(카드) + `evidence_members`(근거) 만 쓴다. 역할별로도
확인했다 — `evidence_role` 이 independent·primary·distributed_claim 어느 쪽이든
한 해시를 두 이슈가 나눠 갖는 경우는 0건이다.

규칙 다섯
---------
① 소유자가 없다 → **새로 발급한다.** 진짜 새 사건이므로 지금 동작 그대로다.
② 소유자가 하나 → **물려받는다.**
③ 소유자가 둘 이상(병합) → **first_seen 이 가장 이른 쪽이 이긴다.**
   큰 쪽이 아니라 이른 쪽인 이유: 묶음 크기는 빌드마다 늘고 줄어 id 가 왕복할 수
   있고, 최초 관측일은 단조롭다. 그리고 오래된 주소일수록 밖에 나가 있다(RSS·인용).
   진 id 들은 `identity_merged_from` 에 남아 원장이 리다이렉트를 세운다.
④ 한 옛 id 를 두 묶음이 함께 주장한다(분열) → **공유 해시가 많은 쪽이 갖는다.**
   동수면 그 옛 id 의 가장 이른 기사를 가진 쪽. 진 쪽은 새로 발급하고
   `identity_split_from` 에 남긴다 — 옛 주소는 이긴 쪽에 그대로 살아 있다.

⑤ 그리고 **발급도 충돌한다.** ④ 는 옛 id 끼리만 다투게 하지만, 새로 발급하는
   묶음의 임시 id(`issue-{대표해시}` 또는 기사가 물고 온 `story_id`)가 다른
   묶음이 물려받은 옛 id 와 같을 수 있다. 실측으로 9/1→9/12 재생에서 실제로
   났다. 그래서 **상속이 우선권을 갖고**, 임시 id 가 이미 쓰였으면 발급하는 쪽이
   대표 해시로 비켜선다(`resolve_local_issue_id_conflicts` 와 같은 규칙).

같은 빌드에서 한 id 를 두 묶음에 주는 일은 없다(④⑤ 가 그것을 막고,
`validate_issue_catalog_ids` 가 배포 전에 다시 확인한다).

무엇을 하지 않는가
------------------
* **클러스터 모양을 건드리지 않는다.** 멤버·대표·정렬은 입력 그대로다.
  `assert_card_clusters_unchanged` 의 서명(대표 해시·대표 제목·카드 해시)에
  id 가 없으므로 이 모듈은 그 게이트와 무관하다.
* LLM·네트워크·파일 읽기 없음. 입력은 전부 호출자가 준다. stdlib 만.
* **긴 공백을 잇지 않는다.** 증거가 60일 창 밖으로 나간 사건은 여기서 되살릴 수
  없다(위 실측에서는 0건이었다 — 창 안의 재조립이 원인이었기 때문이다). 6개월
  뒤 재개를 잇는 것은 이 모듈이 아니라 장기 후보 검색의 몫이다.
* 예외를 올리지 않는다. 원장이 깨졌으면 신원 상속 없이 지금 동작으로 물러난다.

무엇이 달라졌는가 (9/1 원장 → 9/12 라이브 525묶음 재생, 실제 모듈 호출)
-----------------------------------------------------------------------
    상속 319 · 병합 52(흡수된 옛 id 76) · 분열 29 · 신규 125  → 상속률 70.7%
    주소가 바뀐 묶음 19/525 — 나머지는 지금 코드와 같은 id 다
    9/1 이슈 447건 중 사라진 id  75건(16.8%) → **0건(0.0%)**
    중복 id 0 · 멱등성 525/525 · 3회차 id 유지 525/525
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

IDENTITY_VERSION = 1

# 상속 판정에 필요한 최소 공유 해시. 1 로 두는 근거: 해시는 배타적이고(위 실측)
# 한 기사가 두 사건에 동시에 속하지 않는다. 즉 공유 해시 하나는 그 자체로
# "이 기사를 들고 있던 사건"을 유일하게 가리킨다 — 어휘 유사도와 성질이 다르다.
MIN_SHARED_HASHES = 1

ORIGIN_MINTED = "minted"
ORIGIN_INHERITED = "inherited"
ORIGIN_MERGED = "merged"
ORIGIN_SPLIT = "split"


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def cluster_hashes(issue: dict) -> list[str]:
    """한 묶음이 신원의 증거로 내놓는 해시. **배타적인 것만** 넣는다.

    `story_members` 는 넣지 않는다 — 수집 dedup 의 원본 묶음이라 여러 이슈가
    같이 들고 있다(실측 120건). 그것을 증거로 쓰면 한 해시가 두 사건을 가리킨다.
    """
    out: list[str] = []
    representative = issue.get("representative") or issue.get("representative_article") or {}
    seed = _clean(representative.get("hash"))
    if seed:
        out.append(seed)
    for key in ("members", "evidence_members"):
        for member in issue.get(key) or []:
            article_hash = _clean((member or {}).get("hash"))
            if article_hash:
                out.append(article_hash)
    return list(dict.fromkeys(out))


def owner_index(store: dict) -> dict[str, str]:
    """원장의 해시 → 그 해시를 **가장 최근에** 들고 있던 이슈 id.

    원장은 해시를 덮지 않고 쌓으므로(`issue_ledger.merge`) 기사가 재클러스터링으로
    실제로 옮겨 가면 두 항목에 같은 해시가 남는다. 그때 답은 **원장이 더 최근에
    적은 쪽**이다 — 신원은 직전 빌드와 이어져야 하고, 그보다 옛 상태와 이어지면
    주소가 과거로 되돌아간다.

    기준은 `last_written`(원장이 그 항목을 적은 날)이고 `last_seen`(카탈로그가 말한
    이슈의 최종 활동일)이 **아니다.** 저쪽은 여러 이슈가 같은 값을 가져 동값 비교가
    사실상 제비뽑기가 된다.

    그래도 같으면 판정하지 않고 **버린다.** 그 해시로는 어느 쪽인지 말할 수 없고,
    반쯤 맞는 신원은 안 잇는 것보다 나쁘다
    (`issue_change_log.prior_hash_index` 가 같은 이유로 충돌 키를 버린다).

    버리는 쪽이 더 많이 버리고도 더 정확하다 — 2회차 재생 실측:

        last_seen 기준    버린 해시 378/2733 (13.8%)  3회차 id 유지 524/525
        last_written 기준 버린 해시 471/2733 (17.2%)  3회차 id 유지 525/525

    동값을 억지로 가르면 그중 일부가 틀리고, 틀린 소유권 하나가 다음 빌드에서
    주소를 옮긴다. 버린 해시는 그 묶음의 **다른** 해시가 대신 말해 준다.
    """
    best: dict[str, tuple[str, str]] = {}
    dropped: set[str] = set()
    for raw_id, entry in (store.get("issues") or {}).items():
        if not isinstance(entry, dict):
            continue
        issue_id = _clean(raw_id)
        if not issue_id:
            continue
        stamp = (
            _clean(entry.get("last_written"))
            or _clean(entry.get("last_seen"))
            or _clean(entry.get("first_seen"))
        )
        for article_hash in entry.get("hashes") or []:
            key = _clean(article_hash)
            if not key:
                continue
            prior = best.get(key)
            if prior is None:
                best[key] = (stamp, issue_id)
                continue
            if stamp > prior[0]:
                best[key] = (stamp, issue_id)
                dropped.discard(key)
            elif stamp == prior[0] and issue_id != prior[1]:
                dropped.add(key)
    return {
        key: value[1] for key, value in best.items() if key not in dropped
    }


def _first_seen(store: dict, issue_id: str) -> str:
    entry = (store.get("issues") or {}).get(issue_id)
    if not isinstance(entry, dict):
        return ""
    # 빈 값은 '가장 이른'이 되어서는 안 된다 — 정렬에서 뒤로 보낸다.
    return _clean(entry.get("first_seen")) or "9999-12-31"


@dataclass
class Diagnostics:
    version: int = IDENTITY_VERSION
    clusters: int = 0
    minted: int = 0
    inherited: int = 0
    merged: int = 0
    split: int = 0
    preserved_ids: int = 0
    merged_away_ids: int = 0
    mint_collisions: int = 0
    events: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "clusters": self.clusters,
            "minted": self.minted,
            "inherited": self.inherited,
            "merged": self.merged,
            "split": self.split,
            "preserved_ids": self.preserved_ids,
            "merged_away_ids": self.merged_away_ids,
            "mint_collisions": self.mint_collisions,
            "inheritance_rate": (
                round((self.inherited + self.merged) / self.clusters, 4)
                if self.clusters else 0.0
            ),
            "events": self.events[:40],
        }


def _claims(issues: list[dict], owners: dict[str, str]) -> dict[int, Counter]:
    """묶음마다 '옛 id → 공유 해시 수'."""
    out: dict[int, Counter] = {}
    for index, issue in enumerate(issues):
        votes: Counter = Counter()
        for article_hash in cluster_hashes(issue):
            prior = owners.get(article_hash)
            if prior:
                votes[prior] += 1
        out[index] = votes
    return out


def _earliest_article(issue: dict) -> tuple[str, str]:
    """분열 동수를 가르는 결정적 키. 그 묶음의 가장 이른 (기사일, 해시)."""
    rows = [
        (
            _clean((member or {}).get("article_date"))
            or _clean((member or {}).get("briefing_date")) or "9999-12-31",
            _clean((member or {}).get("hash")),
        )
        for key in ("members", "evidence_members")
        for member in issue.get(key) or []
    ]
    return min(rows, default=("9999-12-31", ""))


def _mint(issue: dict, provisional: str, taken: set[str]) -> str:
    """새 신원. 임시 id 가 이미 쓰였으면 비켜선다(규칙 ⑤).

    비켜서는 순서는 `resolve_local_issue_id_conflicts` 와 같다 — 대표 기사 해시가
    먼저다. 해시는 묶음 사이에 배타적이므로 여기서 두 번째 충돌이 나는 일은
    사실상 없지만, 신원을 정하는 자리에 '사실상 없다'로 끝나는 분기를 두지 않는다.
    """
    candidates = [provisional]
    representative = issue.get("representative") or issue.get("representative_article") or {}
    seed = _clean(representative.get("hash"))
    if seed:
        candidates.append(f"issue-{seed}")
    fallback = hashlib.sha256(
        "\0".join(cluster_hashes(issue)).encode("utf-8")
    ).hexdigest()[:16]
    candidates.append(f"issue-{fallback}")
    for candidate in candidates:
        if candidate and candidate not in taken:
            return candidate
    return f"issue-{fallback}-{len(taken)}"


def resolve(issues: list[dict], store: dict) -> dict:
    """묶음들의 `issue_id` 를 원장에서 물려받게 고친다. 제자리 수정.

    Args:
        issues: `cluster_selected_articles` + `attach_evidence_articles` 의 산출물.
            `issue_id` 는 내용 파생 임시값이어도 된다 — 소유자가 없을 때만 남는다.
        store: `issue_ledger.load_store()` 의 저장본.

    Returns:
        진단값. `meta.json` 의 `event_identity` 로 나간다.
    """
    diagnostics = Diagnostics(clusters=len(issues))
    if not issues:
        return diagnostics.as_dict()
    try:
        owners = owner_index(store)
    except Exception:  # 원장이 깨졌으면 신원 상속 없이 지금 동작으로 물러난다.
        return diagnostics.as_dict()
    if not owners:
        diagnostics.minted = len(issues)
        return diagnostics.as_dict()

    claims = _claims(issues, owners)

    # ④ 분열 — 한 옛 id 를 여러 묶음이 주장하면 하나만 갖는다.
    contenders: dict[str, list[int]] = defaultdict(list)
    for index, votes in claims.items():
        for prior_id in votes:
            contenders[prior_id].append(index)
    winner_of: dict[str, int] = {}
    for prior_id, indexes in contenders.items():
        # 공유 해시가 많은 쪽 → 가장 이른 기사를 가진 쪽 → 입력 순서.
        # 세 번째 키는 동수·동일 기사일에서도 빌드마다 같은 답이 나오게 하는
        # 결정적 고정이다(신원은 흔들리면 그 자체로 고장이다).
        winner_of[prior_id] = min(
            indexes,
            key=lambda i: (-claims[i][prior_id], _earliest_article(issues[i]), i),
        )

    # 1단계 — 물려받을 id 를 먼저 확정한다. 발급은 그 뒤에 비켜서야 하므로
    # (규칙 ⑤) 두 단계로 나눈다. 여기서 결정된 id 들은 ④ 로 이미 유일하다.
    plan: list[tuple[Counter, list[str]]] = []
    taken: set[str] = set()
    for index in range(len(issues)):
        votes = Counter({
            prior_id: count
            for prior_id, count in claims[index].items()
            if count >= MIN_SHARED_HASHES and winner_of.get(prior_id) == index
        })
        lost = sorted(
            prior_id for prior_id in claims[index]
            if winner_of.get(prior_id) != index
        )
        plan.append((votes, lost))
        if votes:
            # ③ 병합 — 가장 이른 first_seen 이 이긴다. 동수면 id 로 결정적 고정.
            taken.add(min(votes, key=lambda pid: (_first_seen(store, pid), pid)))

    # 2단계 — 묶음마다 적는다.
    for index, issue in enumerate(issues):
        provisional = _clean(issue.get("issue_id"))
        votes, lost = plan[index]
        issue["story_identity_version"] = IDENTITY_VERSION

        if not votes:
            minted_id = _mint(issue, provisional, taken)
            if minted_id != provisional:
                diagnostics.mint_collisions += 1
            issue["issue_id"] = minted_id
            taken.add(minted_id)
            issue["identity_origin"] = ORIGIN_SPLIT if lost else ORIGIN_MINTED
            issue["identity_evidence"] = {"shared_hashes": 0, "prior_id": ""}
            if lost:
                issue["identity_split_from"] = lost[0]
                diagnostics.split += 1
                diagnostics.events.append({
                    "issue_id": minted_id, "split_from": lost[0],
                    "status": ORIGIN_SPLIT,
                })
            else:
                diagnostics.minted += 1
            continue

        chosen = min(votes, key=lambda pid: (_first_seen(store, pid), pid))
        issue["issue_id"] = chosen
        issue["identity_evidence"] = {
            "shared_hashes": int(votes[chosen]),
            "prior_id": chosen,
            "prior_first_seen": _first_seen(store, chosen),
        }
        if chosen == provisional:
            diagnostics.preserved_ids += 1
        if len(votes) > 1:
            merged_from = sorted(pid for pid in votes if pid != chosen)
            issue["identity_origin"] = ORIGIN_MERGED
            issue["identity_merged_from"] = merged_from
            diagnostics.merged += 1
            diagnostics.merged_away_ids += len(merged_from)
            diagnostics.events.append({
                "issue_id": chosen, "merged_from": merged_from,
                "status": ORIGIN_MERGED,
            })
        else:
            issue["identity_origin"] = ORIGIN_INHERITED
            diagnostics.inherited += 1
        if lost:
            issue["identity_split_from"] = lost[0]

    return diagnostics.as_dict()
