"""보조 근거 두 개 — 정제한 구체 수치, 검증된 레지스트리 actor.

수치 정규화 v2 — `docs/2026-09-20-typed-identity-slot-audit.md` §4 의 오류를 고친다.
    · 억달러 이중 파싱: 금액은 통화 접미까지 한 덩어리로 읽고 한 번만 값을 만든다
    · `TWh` 의 T 가 톤으로 읽힘: 전력량을 먼저 소비하고, 톤은 문자 경계를 요구한다
    · 엔·위안: 통화가 있으면 그 통화로, 없으면 원(국내 정책 기본값)
    · 라벨형 수치: `N대`(7대 SEED) · `N차` · `N회` · `N호(기)` · `N번째` · `제N` · `N위` · `N단계` 는 수치가 아니다
    · 천/만 승수: `4천억` · `650만 대` · `1조 722억`
    · 개·배·사 단위 추가
    · 반올림 허용 오차: 금액·전력은 상대 1% 안이면 같은 값 (`1.07조` ≈ `1조 722억`)
    · 시황 숫자: 원제(`title`)는 보지 않는다 — 주가·매물·지수는 원제에 산다. 큐레이션 제목 `title_kr` 만 쓴다

레지스트리 actor — production 의 `entity_match.entity_ids_for_members` 를 그대로 쓰되 제목만 준다
(요약의 예시 언급을 배제). `한전KDN`·`한전기술`·`한전KPS` 가 `한전` 으로 읽히는 접두 충돌만 걷는다.
"""

from __future__ import annotations

import re
from functools import lru_cache

import entity_match

# ---- 수치 ---------------------------------------------------------------------------

_N = r"\d+(?:[,.]\d+)*"
YEAR_RE = re.compile(r"(?:19|20)\d\d\s*년(?:대|도|간)?|(?<!\d)(?:19|20)\d\d(?!\d)")
DATE_RE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|(?<![\d.])\d{1,2}\s*일(?![\d가-힣])|(?<![\d.])\d{1,2}\s*월(?![\d가-힣])")
# 라벨형 — 순서·회차·번호. `7대 SEED`·`3대 메가프로젝트`·`12차 전기본`·`2차 이전`·`1호 사업`·`제3국`
LABEL_RE = re.compile(r"(?:제\s*)?" + _N + r"\s*(?:차|회차|회(?![가-힣])|호기?|번째|위|단계|국(?=\s*공동)|기(?=\s*이전))")
_PARTICLE = r"(?=$|[^가-힣]|(?:로|으로|를|을|이|가|은|는|의|도|에|와|과|까지|부터|만|씩|이나|나|라도)(?![가-힣]))"
UNIT_ID_RE = re.compile(r"\d+\s*(?:[·,~\-]\s*\d+\s*)*호기")

MONEY_RE = re.compile(
    r"(?:(?P<cur_pre>US\$|USD|\$|€|£|¥)\s*)?"
    r"(?P<num>" + _N + r")\s*(?P<m1>천조|조|천억|백억|억|천만|백만|만|천)?"
    r"(?:\s*(?P<num2>" + _N + r")\s*(?P<m2>천억|백억|억|천만|백만|만|천))?"
    r"(?:\s*(?P<en>billion|million|trillion|bn|mn|m)(?![A-Za-z]))?"
    r"\s*(?P<cur>원|달러|불|유로|엔|위안|파운드|USD|KRW|EUR|JPY|CNY)?",
    re.I,
)
_MULT = {"천조": 1e15, "조": 1e12, "천억": 1e11, "백억": 1e10, "억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4, "천": 1e3, "": 1}
_EN = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "": 1}
_CUR = {"원": "krw", "krw": "krw", "달러": "usd", "불": "usd", "usd": "usd", "$": "usd", "us$": "usd",
        "유로": "eur", "eur": "eur", "€": "eur", "엔": "jpy", "jpy": "jpy", "¥": "jpy", "위안": "cny", "cny": "cny",
        "파운드": "gbp", "£": "gbp"}

ENERGY_RE = re.compile(r"(" + _N + r")\s*(TWh|GWh|MWh|kWh)(?![A-Za-z])", re.I)
POWER_RE = re.compile(r"(" + _N + r")\s*(GWe?|MWe?|kW|㎿|기가와트|메가와트|킬로와트)(?![A-Za-z])", re.I)
MASS_RE = re.compile(r"(" + _N + r")\s*(톤|tU|(?<![A-Za-z])t(?![A-Za-z]))")
PCT_RE = re.compile(r"(" + _N + r")\s*(%|％|퍼센트)")
MULTIPLE_RE = re.compile(r"(" + _N + r")\s*배(?![가-힣])")
COUNT_RE = re.compile(r"(" + _N + r")\s*(만|천)?\s*(기|대|개국|개소|개|곳|건|명|사|가구|척|호)" + _PARTICLE)
DURATION_RE = re.compile(r"(" + _N + r")\s*(년간|년\s*연장|년\s*추가|개월|주간|년\s*만에)")


def _f(text: str) -> float:
    return float(text.replace(",", ""))


def _money(match: re.Match) -> tuple[str, float] | None:
    g = match.groupdict()
    if not (g["m1"] or g["en"] or g["cur"] or g["cur_pre"]):
        return None                                   # 맨 숫자 — 금액이 아니다
    value = _f(g["num"]) * _MULT.get(g["m1"] or "", 1) * _EN.get((g["en"] or "").lower(), 1)
    if g["num2"]:
        value += _f(g["num2"]) * _MULT.get(g["m2"] or "", 1)
    cur = _CUR.get((g["cur"] or g["cur_pre"] or "").lower(), "")
    if not cur:
        cur = "krw" if g["m1"] in ("조", "천억", "백억", "억", "천만", "백만") or g["m2"] else ""
    if not cur:
        return None
    if value < 1e6 and cur == "krw":
        return None                                   # 백만원 아래는 사건 수치가 아니다
    scale = {"krw": 1e8, "usd": 1e6, "eur": 1e6, "jpy": 1e8, "cny": 1e8, "gbp": 1e6}[cur]
    return (cur, value / scale)


def quantities(title_kr: str) -> set[tuple[str, float]]:
    """큐레이션 제목에서 사건 고유 수치 → {(종류, 정규화 값)}."""
    text = str(title_kr or "")
    text = UNIT_ID_RE.sub(" ", text)
    text = YEAR_RE.sub(" ", text)
    text = DATE_RE.sub(" ", text)
    text = LABEL_RE.sub(" ", text)
    out: set[tuple[str, float]] = set()
    spans: list[tuple[int, int]] = []

    def take(m: re.Match) -> bool:
        if any(a <= m.start() < b for a, b in spans):
            return False
        spans.append((m.start(), m.end()))
        return True

    for m in ENERGY_RE.finditer(text):
        if take(m):
            out.add(("mwh", round(_f(m.group(1)) * {"twh": 1e6, "gwh": 1e3, "mwh": 1, "kwh": 1e-3}[m.group(2).lower()], 3)))
    for m in POWER_RE.finditer(text):
        if take(m):
            u = m.group(2).lower()
            scale = 1000 if u in ("gw", "gwe", "기가와트") else 0.001 if u in ("kw", "킬로와트") else 1
            out.add(("mw", round(_f(m.group(1)) * scale, 3)))
    for m in MONEY_RE.finditer(text):
        value = _money(m)
        if value and take(m):
            out.add((value[0], round(value[1], 4)))
    for m in PCT_RE.finditer(text):
        if take(m):
            out.add(("pct", _f(m.group(1))))
    for m in MULTIPLE_RE.finditer(text):
        if take(m):
            out.add(("x", _f(m.group(1))))
    for m in MASS_RE.finditer(text):
        if take(m):
            out.add(("t", _f(m.group(1))))
    for m in COUNT_RE.finditer(text):
        if take(m):
            n = _f(m.group(1)) * {"만": 1e4, "천": 1e3, None: 1}[m.group(2)]
            unit = m.group(3)
            if unit == "대" and n < 10:
                continue                              # 7대 SEED 류 라벨
            out.add(("n:" + unit, n))
    for m in DURATION_RE.finditer(text):
        if take(m):
            out.add(("dur", _f(m.group(1))))
    return out


def shared_quantities(left: set[tuple[str, float]], right: set[tuple[str, float]], *, tol: float = 0.01) -> list[tuple[str, float]]:
    """종류가 같고 값이 상대 오차 안이면 같은 수치. 기간(dur)·배수(x)는 약해서 세지 않는다."""
    out = []
    for kind, a in left:
        if kind in ("dur", "x"):
            continue
        for kind2, b in right:
            if kind != kind2:
                continue
            if a == b or (max(abs(a), abs(b)) > 0 and abs(a - b) / max(abs(a), abs(b)) <= tol):
                out.append((kind, a))
                break
    return out


# ---- 레지스트리 actor ----------------------------------------------------------------

_SUBSIDIARY_RE = re.compile(r"한전(?=[A-Za-z]|기술|KPS|원자력연료|MCS|산업개발)")
_STANDALONE_KEPCO_RE = re.compile(r"한전(?![A-Za-z가-힣])|한국전력(?!기술)")


@lru_cache(maxsize=1)
def _alias_entries():
    return entity_match._entity_alias_entries(entity_match.load_entity_registry())


@lru_cache(maxsize=1)
def _actor_types() -> dict[str, str]:
    return {e["id"]: e.get("type", "") for e in entity_match.load_entity_registry()}


def actors(title_kr: str) -> frozenset[str]:
    """제목이 지목한 레지스트리 회사·기관 id. production 의 매처를 제목만으로 돌린다."""
    title = str(title_kr or "")
    ids, _ = entity_match.entity_ids_for_members([{"title_kr": title, "summary": "", "canonical_tags": []}], _alias_entries())
    types = _actor_types()
    out = {i for i in ids if types.get(i) in ("company", "org")}
    if "kepco" in out and _SUBSIDIARY_RE.search(title) and not _STANDALONE_KEPCO_RE.search(title):
        out.discard("kepco")
    return frozenset(out)


# ---- 근거 판정 -------------------------------------------------------------------------

def evidence(left: dict, right: dict) -> dict:
    """두 기사 사이의 보조 근거. 판정하지 않고 재료만 돌려준다."""
    lq, rq = quantities(left.get("title_kr")), quantities(right.get("title_kr"))
    la, ra = actors(left.get("title_kr")), actors(right.get("title_kr"))
    shared_q = shared_quantities(lq, rq)
    shared_a = sorted(la & ra)
    return {
        "quantities": (sorted(map(str, lq)), sorted(map(str, rq))),
        "shared_quantities": shared_q,
        "actors": (sorted(la), sorted(ra)),
        "shared_actors": shared_a,
        "actors_equal": bool(la) and la == ra,
    }
