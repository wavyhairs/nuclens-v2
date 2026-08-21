"""이슈 병합 검수 후보가 **어디서 몇 개** 나오는지만 센다.

왜 이 파일이 따로 있나
----------------------
2026-08-21 라이브에서 후보가 32,416건이 됐고, 그 93.9% 가 배포 파일 하나를
Cloudflare 상한 위로 밀어 올렸다(#41 · #42). 거기서 나온 질문이 "임계값을
올릴까"였는데, **그 질문에 답할 재료가 없었다.** 배포본은 점수 상위 5,000건만
싣고 그 절단선이 0.8153 이라, 후보의 84.6% 는 존재만 알 뿐 안이 안 보인다.

그래서 세는 것을 먼저 넣는다. 이 모듈은 **판정을 하나도 하지 않는다** —
클러스터링·병합·후보 결정은 전부 build_data 에 그대로 있고, 여기 있는 것은
그 결과를 사후에 집계하는 순수 함수와, 루프가 몇 번 돌았는지 세는 계수기뿐이다.
넣기 전과 넣은 뒤의 산출물이 **바이트 단위로 같아야 한다**(web/tests 가 잠근다).

무엇을 재는가
-------------
    A. 경로      카드 경로 / evidence 경로 각각 몇 건을 만들었나
    B. 점수대    0.70~0.75 … 0.88~0.92 구간별 분포. **배포본이 자르기 전에 센다**
    C. 사전차단  지금 있는 신호(국가·설비·지문·태그·엔티티)로 미리 걸렀다면
                 후보가 몇 건 줄었을지 — 그리고 **실제 병합을 몇 건 죽였을지**.
                 둘을 같이 보지 않으면 "많이 줄었다"가 "많이 잃었다"를 가린다.
    D. 후보 폭   기사 한 건이 몇 개 묶음과 겨루고 후보를 몇 개 남기나

가드레일
--------
    · stdlib + story_fingerprint 만 쓴다. build_data 를 import 하지 않는다
      (issue_review 와 같은 이유 — 순환 방지).
    · 어떤 함수도 입력을 변형하지 않는다.
    · 계측이 빌드를 죽이면 안 된다. 부르는 쪽에서 감싸 준다.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

import story_fingerprint

# 요청받은 구간 그대로. 상한은 자동 병합 문턱(0.92)이고, 그 위는 후보가 되지
# 않는다 — 붙어 버리기 때문이다. 0.84~0.92 두 칸만 LLM 검수가 실제로 집어 든다.
BANDS: tuple[tuple[float, float], ...] = (
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.84),
    (0.84, 0.88),
    (0.88, 0.92),
)
REVIEW_BAND = (0.84, 0.92)
# 기사당 몇 건까지 남길지 실험할 값들. 여기서 고르는 것이 아니라 **보존율을
# 재는 것**이 목적이다 — 자르는 것은 다음 PR 이 데이터를 보고 정한다.
#
# 12·15 는 "승인 병합 100% 를 지키는 가장 작은 N" 을 고르려고 넣었다. 5 와 10
# 사이가 아니라 10 과 20 사이가 비어 있었는데, 러너 실측에서 Top-10 이 165건 중
# 1건을 놓쳤다(99.4%) — 답이 그 사이에 있다는 뜻이다.
#
# **이 계산은 빌드 안에서 해야 한다.** 아티팩트를 받아 나중에 재면 정답지가
# 모자란다: 배포본·전수 덤프의 `clusters` 는 카드 멤버 2건 이상인 이슈만 담아
# 실측 546건 중 385건만 보인다. 빌드 안에서는 `match_diagnostics` 전량을 본다.
TOP_N_CHOICES: tuple[int, ...] = (3, 5, 10, 12, 15, 20)


class SearchTelemetry:
    """후보 생성 루프의 계수기. 세는 것 말고는 아무것도 하지 않는다.

    build_data 의 두 루프(`cluster_selected_articles` ·
    `attach_evidence_articles`)가 각자 하나씩 들고 돈다. 인자가 ``None`` 이면
    루프는 계측 호출 자체를 하지 않으므로 **기존 동작과 비용이 그대로**다.

    LLM 판정 뒤 2차 패스에서는 `review_candidates` 를 비우고 다시 도는데,
    그때 계수기도 **새로 만들어야 한다** — 안 그러면 1차와 2차가 겹쳐 세어진다.
    """

    __slots__ = ("path", "clusters", "pairs", "per_article_clusters", "per_article_pairs",
                 "preselect_ranks", "preselect", "_article_lexical")

    def __init__(self, path: str) -> None:
        self.path = path
        self.clusters: Counter = Counter()
        self.pairs: Counter = Counter()
        self.per_article_clusters: Counter = Counter()
        self.per_article_pairs: Counter = Counter()
        # 어휘 예선 순위의 히스토그램. 지금은 **재기만 한다** — 자르지 않는다.
        self.preselect_ranks: Counter = Counter()
        self.preselect: Counter = Counter()
        self._article_lexical: dict = {}

    # -- 클러스터 단위 -----------------------------------------------------
    def visit(self) -> None:
        """(기사 × 이슈) 방문 하나. 증가 법칙 O(기사 × 이슈)를 재는 분모다."""
        self.clusters["visits"] += 1

    def skip(self, reason: str) -> None:
        """묶음 전체를 건너뛴 이유. 지금 규칙이 이미 얼마나 걷는지 보여 준다."""
        self.clusters[f"skip_{reason}"] += 1

    def compare(self, article_hash: object) -> None:
        """임베딩 비교까지 간 묶음."""
        self.clusters["compared"] += 1
        self.per_article_clusters[str(article_hash or "")] += 1

    def fingerprint_chain_demoted(self) -> None:
        """지문만으로 붙을 뻔했는데 묶음의 다른 멤버와 어긋나 물러난 쌍."""
        self.clusters["fingerprint_chain_demoted"] += 1

    # -- 쌍 단위 -----------------------------------------------------------
    def pair(self, article_hash: object, outcome: str,
             issue_id: object = None, lexical: float = 0.0) -> None:
        """채점한 쌍 하나와 그 결말. 쌍 채점 총계 = outcome 들의 합이다.

        `lexical` 은 `issue_similarity` 가 이미 계산한 어휘 점수를 그대로 다시
        조립한 값이다. 묶음마다 최고값만 남겨 두었다가 `settle` 에서 **정답
        묶음이 몇 위였는지**를 잰다 — 후속 PR 의 예선 컷을 데이터로 정하기 위한
        그림자 측정이고, 여기서는 아무것도 자르지 않는다.
        """
        self.pairs[outcome] += 1
        self.per_article_pairs[str(article_hash or "")] += 1
        if issue_id is not None:
            key = str(issue_id)
            if lexical > self._article_lexical.get(key, -1.0):
                self._article_lexical[key] = lexical

    def settle(self, issue_id: object) -> None:
        """기사 하나가 어느 묶음에 앉았는지 확정됐을 때 부른다.

        붙지 않은 기사(`issue_id is None`)도 반드시 불러야 한다 — 안 부르면
        다음 기사의 예선 표에 앞 기사의 점수가 섞인다.
        """
        scores = self._article_lexical
        self._article_lexical = {}
        if issue_id is None:
            self.preselect["unlanded"] += 1
            return
        key = str(issue_id)
        if key not in scores:
            # 붙은 묶음이 예선 표에 없다 = 계측이 루프와 어긋났다는 뜻이다.
            # 0 이 아니면 이 수치 전체를 믿으면 안 된다.
            self.preselect["not_in_table"] += 1
            return
        won = scores[key]
        self.preselect["landed"] += 1
        self.preselect_ranks[sum(1 for value in scores.values() if value > won)] += 1

    # -- 결과 --------------------------------------------------------------
    def summary(self) -> dict:
        clusters_seen = sorted(self.per_article_clusters.values())
        pairs_seen = sorted(self.per_article_pairs.values())
        return {
            "path": self.path,
            "issue_visits": self.clusters["visits"],
            "clusters_compared": self.clusters["compared"],
            "pairs_scored": sum(self.pairs.values()),
            "skipped": {key[5:]: value for key, value in sorted(self.clusters.items())
                        if key.startswith("skip_")},
            "fingerprint_chain_demoted": self.clusters["fingerprint_chain_demoted"],
            "pair_outcomes": dict(sorted(self.pairs.items())),
            "articles_that_compared": len(self.per_article_clusters),
            "clusters_per_article": _spread(clusters_seen),
            "pairs_per_article": _spread(pairs_seen),
            "preselect_rank": preselect_rank_summary(self.preselect_ranks, self.preselect),
        }


def preselect_rank_summary(ranks: Counter, tally: Counter,
                           cut: int = 0) -> dict:
    """어휘 예선에서 **정답 묶음**이 몇 위였나. 컷을 정하는 유일한 근거다.

    중앙값이 아니라 **꼬리**를 본다 — 컷은 중앙값이 아니라 최악의 정답을 담아야
    하는 값이라서다(2026-08-21 라이브 클러스터 실측: 중앙 0위 · p90 2위 · 최대 14위).

    다만 감시는 `max` 가 아니라 `p99` 와 `beyond_cut` 로 한다. **`max` 는 표본이
    하나만 튀어도 움직인다** — 그것으로 알림을 걸면 매 회차 울리고, 매 회차
    울리는 알림은 아무도 안 본다. 실제로 물어야 할 것은 "컷을 걸었다면 병합을
    몇 건 놓쳤겠나"(`beyond_cut`)이고, 그 앞에서 미리 경고하는 것이 `p99` 다.
    """
    cut = int(cut or GUARD_LIMITS["preselect_cut"])
    flat: list[int] = []
    for rank, count in sorted(ranks.items()):
        flat.extend([rank] * count)
    landed = len(flat)
    within = {}
    for choice in (5, 10, 20, 30, 50):
        within[str(choice)] = (round(sum(1 for r in flat if r < choice) / landed, 4)
                               if landed else 0.0)
    beyond = sum(1 for r in flat if r >= cut)
    return {
        "landed": landed,
        "unlanded": tally.get("unlanded", 0),
        # 0 이 아니면 계측이 루프와 어긋난 것이다 — 경보를 올린다.
        "not_in_table": tally.get("not_in_table", 0),
        "median": int(statistics.median(flat)) if flat else 0,
        "p90": flat[min(landed - 1, int(landed * 0.9))] if flat else 0,
        "p99": flat[min(landed - 1, int(landed * 0.99))] if flat else 0,
        "max": max(flat) if flat else 0,
        "within_cut": within,
        "cut": cut,
        # 계획 컷을 걸었다면 놓쳤을 병합. 이 값이 감시의 본체다.
        "beyond_cut": beyond,
        "beyond_cut_share": round(beyond / landed, 4) if landed else 0.0,
    }


def _spread(values: list[int]) -> dict:
    """평균만 적으면 꼬리가 안 보인다 — 최대값이 실제로 아픈 쪽이다."""
    if not values:
        return {"mean": 0.0, "median": 0, "p90": 0, "max": 0}
    return {
        "mean": round(sum(values) / len(values), 1),
        "median": int(statistics.median(values)),
        "p90": values[min(len(values) - 1, int(len(values) * 0.9))],
        "max": values[-1],
    }


# ---------------------------------------------------------------------------
# 사후 집계 — 후보 목록과 병합 기록만 읽는다
# ---------------------------------------------------------------------------

def origin_of(row: dict) -> str:
    """후보가 어느 경로에서 났나.

    evidence 경로만 행에 `member_role` 을 적는다. 카드 경로 행에 표식을 **더하지
    않는 이유**는 크기다 — 전수 32,416 행에 필드 하나를 더하면 덤프가 0.6 MB
    커지는데, 없다는 사실만으로 이미 구분된다.
    """
    return "evidence" if row.get("member_role") == "evidence" else "card"


def remote_cosine(diagnostics: dict) -> float | None:
    value = (diagnostics or {}).get("embedding_similarity")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def band_of(score: float | None) -> str:
    """점수를 구간 이름으로. 구간 밖도 이름을 준다 — '없음'이 제일 흔한 답일 수 있다."""
    if score is None:
        return "no_remote_embedding"
    for low, high in BANDS:
        if low <= score < high:
            return f"{low:.2f}-{high:.2f}"
    return "gte_0.92" if score >= BANDS[-1][1] else "lt_0.70"


def in_review_band(score: float | None) -> bool:
    return score is not None and REVIEW_BAND[0] <= score < REVIEW_BAND[1]


def band_table(rows: list[dict]) -> dict:
    """B. 구간별 · 경로별 후보 수. **자르기 전 전수**로 센다."""
    total = len(rows)
    per_band: Counter = Counter()
    per_origin: Counter = Counter()
    cross: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        score = remote_cosine(row.get("diagnostics") or {})
        band = band_of(score)
        origin = origin_of(row)
        per_band[band] += 1
        per_origin[origin] += 1
        cross[band][origin] += 1
    order = ["lt_0.70", *(f"{low:.2f}-{high:.2f}" for low, high in BANDS),
             "gte_0.92", "no_remote_embedding"]
    return {
        "total": total,
        "by_origin": dict(per_origin),
        "evidence_share": round(per_origin["evidence"] / total, 4) if total else 0.0,
        "by_band": [
            {
                "band": name,
                "count": per_band[name],
                "share": round(per_band[name] / total, 4) if total else 0.0,
                "evidence": cross[name]["evidence"],
                "card": cross[name]["card"],
            }
            for name in order if per_band[name] or name.startswith("0.")
        ],
        # 이 한 줄이 "후보가 많다"와 "검수할 것이 많다"를 가른다. 지금 실측에서
        # 둘의 차이가 38배다 — 임계값 논의는 항상 여기서 시작해야 한다.
        "review_band_count": sum(1 for row in rows
                                 if in_review_band(remote_cosine(row.get("diagnostics") or {}))),
        "review_band": list(REVIEW_BAND),
    }


# 사전차단 후보들. 이름 → 판정 함수(진단 dict 를 받아 "걸린다"를 돌려준다).
#
# **이걸로 실제로 거르지 않는다.** 각 조건이 후보를 몇 건 줄이는지와, 같은
# 조건이 **이미 채택된 병합을 몇 건 죽이는지**를 나란히 잰다. 후자가 없으면
# 이 표는 "많이 줄었다"만 말하고 대가를 숨긴다.
_IDENTITY = frozenset(story_fingerprint.IDENTITY_AXES)


def _axes(diagnostics: dict, key: str) -> frozenset:
    return _IDENTITY & frozenset(diagnostics.get(key) or ())


def _ratio(diagnostics: dict, key: str) -> float:
    try:
        return float(diagnostics.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


PREFILTERS: tuple[tuple[str, str, object], ...] = (
    (
        "fingerprint_identity_conflict",
        "지문의 신원 축(행위자·대상·행위·원인)이 어긋난 쌍",
        lambda diag, facilities: bool(_axes(diag, "story_fingerprint_contested")),
    ),
    (
        "no_concrete_tag_or_facility",
        "구체 태그도 설비·프로젝트 엔티티도 하나도 공유하지 않는 쌍",
        lambda diag, facilities: not int(diag.get("tag_shared") or 0) and not facilities,
    ),
    (
        "topic_only",
        "통제 어휘 topic 만 같고 태그·제목·토큰 근거가 없는 쌍",
        lambda diag, facilities: (
            not int(diag.get("tag_shared") or 0)
            and int(diag.get("topic_shared") or 0) > 0
            and _ratio(diag, "title_ratio") < 0.28
            and _ratio(diag, "token_ratio") < 0.16
        ),
    ),
    (
        "no_lexical_evidence",
        "태그 0 · 제목 0.20 미만 · 토큰 0.10 미만 — 어휘가 아무 말도 못 하는 쌍",
        lambda diag, facilities: (
            not int(diag.get("tag_shared") or 0)
            and _ratio(diag, "title_ratio") < 0.20
            and _ratio(diag, "token_ratio") < 0.10
        ),
    ),
    (
        "compared_axes_without_identity",
        "지문을 3축 이상 비교했는데 신원 축은 하나도 공유하지 않은 쌍",
        lambda diag, facilities: (
            int(diag.get("story_fingerprint_compared") or 0) >= 3
            and not _axes(diag, "story_fingerprint_shared")
        ),
    ),
)


def _facilities_of(record: dict) -> list:
    return list(record.get("shared_facility_entities")
                or (record.get("diagnostics") or {}).get("shared_facility_entities")
                or ())


def prefilter_shadow(rows: list[dict], merges: list[dict]) -> dict:
    """C. 사전차단을 걸었다면 — 줄어드는 수와 **잃는 병합**을 같이 센다."""
    band_rows = [row for row in rows
                 if in_review_band(remote_cosine(row.get("diagnostics") or {}))]
    out = []
    for name, detail, predicate in PREFILTERS:
        hit_all = _count_hits(rows, predicate, lambda r: r.get("diagnostics") or {})
        hit_band = _count_hits(band_rows, predicate, lambda r: r.get("diagnostics") or {})
        lost = _count_hits(merges, predicate, lambda m: m)
        out.append({
            "id": name,
            "detail": detail,
            "candidates": hit_all,
            "candidate_share": round(hit_all / len(rows), 4) if rows else 0.0,
            "review_band": hit_band,
            "review_band_share": round(hit_band / len(band_rows), 4) if band_rows else 0.0,
            # 여기가 대가다. 0 이 아니면 그 조건은 하드 게이트로 쓸 수 없다.
            "merges_lost": lost,
            "merges_lost_by_method": dict(sorted(Counter(
                str(m.get("method") or "?") for m in merges
                if predicate(m, _facilities_of(m))
            ).items())),
        })
    return {
        "candidate_total": len(rows),
        "review_band_total": len(band_rows),
        "merge_total": len(merges),
        "filters": out,
    }


def _count_hits(records: list[dict], predicate, diag_of) -> int:
    return sum(1 for record in records
               if predicate(diag_of(record), _facilities_of(record)))


def topn_retention(rows: list[dict], merges: list[dict],
                   choices: tuple[int, ...] = TOP_N_CHOICES) -> dict:
    """B안(기사당 Top-N)이 무엇을 지키고 무엇을 버리는지.

    Top-N 은 **기록되는 후보만** 줄인다. 제목·태그·임베딩·지문 병합은 후보
    목록을 거치지 않고 `issue_similarity` 안에서 끝나므로 영향이 없다. 후보
    목록을 반드시 통과해야 하는 경로는 **LLM 검수 하나뿐**이라, 위험을 재는
    자리도 거기다(`llm_approved` 로 채택된 병합).

    순위는 그 기사(`right_hash`)가 만든 후보 점수들 안에서 매긴다.
    """
    scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        scores[str(row.get("right_hash") or "")].append(float(row.get("candidate_score") or 0))

    def rank(article_hash: object, score: float) -> int:
        return sum(1 for other in scores.get(str(article_hash or ""), ()) if other > score)

    band_rows = [row for row in rows
                 if in_review_band(remote_cosine(row.get("diagnostics") or {}))]
    llm_merges = [m for m in merges
                  if m.get("method") == "llm_approved" and remote_cosine(m) is not None]
    out = []
    for n in choices:
        kept_band = sum(1 for row in band_rows
                        if rank(row.get("right_hash"), float(row.get("candidate_score") or 0)) < n)
        kept_llm = sum(1 for m in llm_merges
                       if rank(m.get("hash"), float(remote_cosine(m))) < n)
        out.append({
            "n": n,
            "candidates_kept": sum(min(n, len(values)) for values in scores.values()),
            "review_band_kept": kept_band,
            "review_band_share": round(kept_band / len(band_rows), 4) if band_rows else 0.0,
            "llm_approved_kept": kept_llm,
            "llm_approved_share": round(kept_llm / len(llm_merges), 4) if llm_merges else 0.0,
        })
    return {
        "articles_with_candidates": len(scores),
        "review_band_total": len(band_rows),
        "llm_approved_total": len(llm_merges),
        "levels": out,
    }


def candidate_breadth(rows: list[dict]) -> dict:
    """D. 기사 한 건이 후보를 몇 개 남기나. 꼬리가 어디까지 가는지가 요점이다."""
    per_article: Counter = Counter()
    scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        key = str(row.get("right_hash") or "")
        per_article[key] += 1
        scores[key].append(float(row.get("candidate_score") or 0))
    counts = sorted(per_article.values())
    gaps = []
    for values in scores.values():
        if len(values) >= 6:
            ordered = sorted(values, reverse=True)
            gaps.append(round(ordered[0] - ordered[5], 4))
    return {
        "articles_with_candidates": len(per_article),
        "candidates_per_article": _spread(counts),
        "articles_with_10_or_more": sum(1 for value in counts if value >= 10),
        "articles_with_20_or_more": sum(1 for value in counts if value >= 20),
        "articles_with_one": sum(1 for value in counts if value == 1),
        # 1위와 6위의 점수 차이. 작으면 그 기사의 후보들은 서로 구별되지 않는
        # 덩어리라는 뜻이고, 그때 Top-N 이 버리는 것은 정보가 아니라 중복이다.
        "top1_to_top6_gap": {
            "articles": len(gaps),
            "median": round(statistics.median(gaps), 4) if gaps else 0.0,
            "max": max(gaps) if gaps else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# 감시 — 평소엔 조용하고, 여유가 사라졌을 때만 말한다
# ---------------------------------------------------------------------------
#
# 이 수치들을 사람이 매번 열어 보게 하면 결국 아무도 안 본다. 그래서 **컷이
# 위험해졌을 때만** 워크플로 경고와 운영 알림으로 올라오게 한다. 감시는 판정을
# 바꾸지 않고 종료 코드도 바꾸지 않는다 — data_gate_metrics 가 세운 원칙
# ("측정은 배포를 막지 않는다")을 그대로 따른다.
#
# 컷 값은 다음 PR 이 실제로 쓸 값이다. 여기서는 **그 값이 아직 안전한지**만 잰다.
GUARD_LIMITS: dict[str, object] = {
    # 어휘 예선에서 몇 개 묶음까지 남길 계획인가. 2026-08-21 실측 최대 순위는
    # 14위였고, 20 은 그 위에 6칸 여유를 둔 값이다.
    "preselect_cut": 20,
    # **어느 경로에 그 컷을 걸 계획인가.** 여기 없는 경로는 재기만 하고 알리지
    # 않는다 — 적용하지도 않을 컷을 두고 우는 알림은 배경 소음이 되고, 그러면
    # 진짜일 때도 안 읽힌다.
    #
    # evidence 뿐인 이유: 근거 부착은 설계상 카드 묶음을 바꾸지 못하고
    # (`assert_card_clusters_unchanged` 가 빌드 게이트로 잠가 뒀다) 쌍 채점의
    # 93% 를 차지한다. 카드 경로는 독자가 보는 묶음을 만드는 자리이고 전체의
    # 6.9% 라 얻는 것도 적다.
    #
    # 실측이 그 분리를 지지한다(2026-08-21 러너, 전수):
    #     evidence  정답 순위 p99  8위 · 표본 423건 → 컷 20 에 여유가 있다
    #     card      정답 순위 p99 24위 · 표본 123건 → 같은 컷이면 1.6% 를 잃는다
    # 카드 경로의 예선이 훨씬 약하다는 것 자체가 "카드는 건드리지 말자"의 근거다.
    # 값은 계속 `search_space[].preselect_rank` 에 남으므로 언제든 다시 볼 수 있다.
    "preselect_guarded_paths": ("evidence",),
    # 컷의 70%(=14위)를 **p99 가** 건드리면 여유가 사실상 없다. 그때 알린다 —
    # 컷을 넘긴 뒤에 알리면 이미 병합을 놓친 회차다.
    "preselect_headroom_ratio": 0.70,
    # 컷 밖으로 밀린 정답의 허용 비율. `max` 하나로 알리면 표본 하나가 튈 때마다
    # 울리고, 매 회차 우는 알림은 아무도 안 본다 — 그래서 비율로 본다.
    "preselect_loss_warn": 0.01,
    "preselect_loss_critical": 0.03,
    # 표본이 이보다 적으면 순위 통계를 신뢰하지 않는다(뉴스가 한산한 날의
    # 분모 문제 — TRACKING_WINDOW_BRIEFINGS 주석과 같은 함정이다).
    "preselect_min_sample": 50,
    # 기사당 남길 후보 수 계획값.
    "top_n": 10,
    # `llm_approved` 는 후보 목록을 반드시 거치는 유일한 병합 경로다.
    # 하나라도 못 지키면 그 컷은 쓸 수 없다.
    "top_n_min_retention": 1.0,
    # 최근 회차 중앙값 대비 이만큼 벗어나면 병합기의 성질이 변한 것이다.
    "merge_rate_drift": 0.30,
    "evidence_share_drift": 0.15,
}


def guardrails(diagnostics: dict, *, baseline: dict | None = None,
               limits: dict | None = None) -> list[dict]:
    """진단 한 덩어리를 읽고 **말할 것이 있을 때만** 항목을 돌려준다.

    빈 리스트가 정상이다. 각 항목은 그대로 워크플로 주석과 운영 알림 두 곳에
    쓰이므로, `id` 는 알림 중복 억제 키가 된다(바꾸면 쿨다운이 끊긴다).
    """
    limits = {**GUARD_LIMITS, **(limits or {})}
    baseline = baseline or {}
    out: list[dict] = []

    cut = int(limits["preselect_cut"])
    warn_at = max(1, int(round(cut * float(limits["preselect_headroom_ratio"]))))
    for space in diagnostics.get("search_space") or []:
        rank = space.get("preselect_rank") or {}
        path = space.get("path") or "?"
        if rank.get("not_in_table"):
            out.append(_finding(
                "issue-candidate:telemetry-desync", "critical",
                f"후보 계측이 루프와 어긋났다 ({path})",
                f"붙은 묶음이 예선 표에 없는 기사 {rank['not_in_table']}건. "
                f"이 회차의 예선 순위 수치를 믿으면 안 된다.",
            ))
        landed = int(rank.get("landed") or 0)
        # 계측 무결성(desync)은 경로를 가리지 않는다 — 위에서 이미 봤다.
        # 컷 여유는 **그 컷을 걸 경로에서만** 묻는다.
        if path not in tuple(limits["preselect_guarded_paths"] or ()):
            continue
        if landed < int(limits["preselect_min_sample"]):
            continue
        loss = float(rank.get("beyond_cut_share") or 0)
        lost = int(rank.get("beyond_cut") or 0)
        p99 = int(rank.get("p99") or 0)
        tail = (f"p99 {p99}위 · 중앙 {rank.get('median')}위 · 최대 {rank.get('max')}위 · "
                f"표본 {landed}건")
        if loss > float(limits["preselect_loss_warn"]):
            out.append(_finding(
                "issue-candidate:preselect-headroom",
                "critical" if loss > float(limits["preselect_loss_critical"]) else "warning",
                f"어휘 예선 컷 {cut} 이 실제 병합을 놓친다 ({path})",
                f"정답 묶음이 컷 밖으로 밀린 병합 {lost}건 / {landed}건 ({loss:.1%}). "
                f"컷을 {max(cut + 10, int(rank.get('max') or 0) + 10)} 이상으로 올리거나 "
                f"예선 점수 구성을 다시 봐야 한다. {tail}",
            ))
        elif p99 >= warn_at:
            out.append(_finding(
                "issue-candidate:preselect-headroom", "warning",
                f"어휘 예선 컷 {cut} 의 여유가 줄었다 ({path})",
                f"정답 묶음의 p99 가 경고선 {warn_at}위에 닿았다(컷 밖으로 밀린 것은 "
                f"아직 {lost}건 / {landed}건). {tail}",
            ))

    top_n = int(limits["top_n"])
    floor = float(limits["top_n_min_retention"])
    for level in (diagnostics.get("top_n_retention") or {}).get("levels") or []:
        if int(level.get("n") or 0) != top_n:
            continue
        share = float(level.get("llm_approved_share") or 0)
        total = (diagnostics.get("top_n_retention") or {}).get("llm_approved_total") or 0
        if total and share < floor:
            lost = total - int(level.get("llm_approved_kept") or 0)
            out.append(_finding(
                "issue-candidate:topn-retention",
                "critical" if share < floor - 0.02 else "warning",
                f"기사당 Top-{top_n} 이 실제 병합을 놓친다",
                f"LLM 승인 병합 {total}건 중 {lost}건이 상위 {top_n} 밖이다 "
                f"(보존율 {share:.3f}, 기준 {floor:.3f}). 컷을 올려야 한다.",
            ))

    for key, label, limit_key in (
        ("merge_rate", "카드 병합률", "merge_rate_drift"),
        ("evidence_attach_rate", "근거 기사 부착률", "merge_rate_drift"),
        ("evidence_share", "evidence 후보 비중", "evidence_share_drift"),
    ):
        current = diagnostics.get(key)
        before = baseline.get(key)
        if current is None or not before:
            continue
        # 부호를 살린다 — "줄었다"와 "늘었다"는 원인이 다르고, 절대값만 적으면
        # 알림을 읽는 사람이 어느 쪽인지 다시 열어 봐야 한다.
        drift = (float(current) - float(before)) / float(before)
        if abs(drift) > float(limits[limit_key]):
            out.append(_finding(
                f"issue-candidate:{key.replace('_', '-')}-drift", "warning",
                f"{label}이 최근 회차와 크게 다르다",
                f"이번 {float(current):.4f} vs 최근 중앙값 {float(before):.4f} "
                f"({drift:+.0%}, 허용 ±{float(limits[limit_key]):.0%}). "
                f"수집량·임베딩 커버리지·병합 규칙 중 무엇이 움직였는지 봐야 한다.",
            ))
    return out


def _finding(finding_id: str, severity: str, title: str, detail: str) -> dict:
    return {"id": finding_id, "severity": severity, "title": title, "detail": detail}


def summarize(rows: list[dict], merges: list[dict],
              telemetries: list[SearchTelemetry] | None = None,
              *, selected_count: int = 0, baseline: dict | None = None) -> dict:
    """issue_audit 에 실을 진단 한 덩어리. 크기는 O(1) 이라 배포본에도 그대로 간다."""
    bands = band_table(rows)
    search_space = [telemetry.summary() for telemetry in (telemetries or [])]
    # 병합률은 **분모가 다른 두 가지**라 나눠 잰다. 하나로 합치면 근거 부착이
    # 늘어난 날과 카드가 더 붙은 날이 같은 숫자로 보인다.
    card_merges = [m for m in merges if m.get("member_role") != "evidence"]
    evidence_merges = [m for m in merges if m.get("member_role") == "evidence"]
    evidence_articles = sum(space.get("articles_that_compared") or 0
                            for space in search_space if space.get("path") == "evidence")
    out = {
        "definition_version": "candidate-telemetry-v1",
        "search_space": search_space,
        "bands": bands,
        "prefilter_shadow": prefilter_shadow(rows, merges),
        "top_n_retention": topn_retention(rows, merges),
        "breadth": candidate_breadth(rows),
        # 감시가 다음 회차와 대조하는 값들. 표 안에 묻어 두면 비교할 수 없다.
        "evidence_share": bands["evidence_share"],
        "merge_rate": round(len(card_merges) / selected_count, 4) if selected_count else None,
        "evidence_attach_rate": (round(len(evidence_merges) / evidence_articles, 4)
                                 if evidence_articles else None),
        "merge_total": len(merges),
        "card_merge_total": len(card_merges),
        "evidence_merge_total": len(evidence_merges),
        "selected_count": selected_count,
        "evidence_article_count": evidence_articles,
        "guard_limits": dict(GUARD_LIMITS),
    }
    out["guards"] = guardrails(out, baseline=baseline)
    return out
