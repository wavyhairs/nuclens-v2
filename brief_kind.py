"""아침 브리핑 후보의 **글 종류** — 사설·칼럼은 빼고, 기획·분석은 '해설'로 하루 한 건.

왜 있는가
---------
2026-09-26 발송분 점검(`.eval/delivery-audit-2026-09-26`)에서 제목이 틀린 55건 가운데
12건이 칼럼·사설·기고·창간사를 정부·지자체·기관의 조치처럼 옮긴 것이었고, 35건은
기획·해설 기사가 배경으로 든 지난 사실을 오늘 사건처럼 올린 것이었다. 큐레이션
규칙(#196)이 제목을 고쳐도, 글 자체가 오늘 일어난 일을 전하지 않으면 아침 브리핑의
한 자리를 차지할 이유가 약하다.

규칙 (2026-09-26 결정 D2)
-------------------------
- 사설·칼럼·기고·시론·창간사 등(원제목 말머리) 과 `article_type=opinion` 은 후보에서
  뺀다. 인터뷰는 남긴다 — 취재원이 새 사실을 밝히는 경우가 있다.
- 기획·분석·해설(`article_type=analysis` 또는 말머리)은 '해설'로 표시해 **하루 한 건**,
  1번 자리는 주지 않는다. 제목은 논지로 쓴다(news_bot 제목 규칙, #196).

말머리를 따로 보는 이유: 9/13~26 발송분에서 `article_type` 은 창간사를 policy 로,
창간기획을 news 로 적었다. 라벨만 믿으면 절반을 놓친다.
수집 단계의 `[기획]`·`[특집]` 제외(news_bot.ANTI_TITLE_PATTERNS)는 그대로 둔다 —
여기 오는 것은 `[창간기획]` 처럼 그 패턴을 비껴간 것과 라벨로만 분석인 것이다.
"""
from __future__ import annotations

import re

DEFAULTS = {"enabled": True, "exclude_opinion": True, "explainer_per_day": 1}

_TAG = re.compile(r"[\[【〈<]\s*([^\]】〉>\[]{1,24}?)\s*[\]】〉>]")
_OPINION = re.compile(r"(사설|칼럼|기고|시론|논단|기자수첩|취재수첩|창간사|왜냐면|오피니언|"
                      r"발언대|데스크|포럼)$")
_EXPLAINER = re.compile(r"(기획|특집|분석|해설|심층|진단|초점|톺아보기)")
EXPLAINER_LABEL = "[해설]"


def resolve_config(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    section = (cfg or {}).get("article_kind")
    if isinstance(section, dict):
        merged.update({k: v for k, v in section.items() if not str(k).startswith("_")})
    return merged


def _tags(item: dict) -> list[str]:
    title = str(item.get("title") or "")
    return [m.group(1).strip() for m in _TAG.finditer(title)]


def kind(item: dict) -> str:
    """'opinion' / 'explainer' / ''(보도). 원제목 말머리가 라벨보다 먼저다."""
    tags = _tags(item)
    if any("인터뷰" in tag for tag in tags):
        return ""
    if any(_OPINION.search(tag) for tag in tags):
        return "opinion"
    article_type = str(item.get("article_type") or "").strip().lower()
    if article_type == "opinion" and "인터뷰" not in str(item.get("title") or ""):
        return "opinion"
    if article_type == "analysis" or any(_EXPLAINER.search(tag) for tag in tags):
        return "explainer"
    return ""


def split(items: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """(남길 것, 뺀 사설·칼럼). 남긴 기획·분석에는 `brief_kind=explainer` 를 단다."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        item.pop("brief_kind", None)
        if not cfg.get("enabled", True):
            kept.append(item)
            continue
        found = kind(item)
        if found == "opinion" and cfg.get("exclude_opinion", True):
            dropped.append({
                "hash": item.get("hash", ""),
                "title": (item.get("title") or item.get("title_kr") or "")[:80],
                "importance": item.get("importance", ""),
                "reason": "opinion",
            })
            continue
        if found == "explainer":
            item["brief_kind"] = "explainer"
        kept.append(item)
    return kept, dropped


def is_explainer(item: dict) -> bool:
    return item.get("brief_kind") == "explainer"


def label(item: dict) -> str:
    return EXPLAINER_LABEL if is_explainer(item) else ""
