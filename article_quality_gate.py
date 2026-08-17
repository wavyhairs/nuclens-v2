"""Deterministic safety gates for curated articles and Telegram cards.

The module deliberately does *not* try to fact-check prose.  Without a second
source or an LLM, that would create more false positives than it prevents.
Instead it catches a small set of high-confidence failures that are observable
from data already in the pipeline:

* a translated title/summary switches to a different named entity and topic;
* a key quantity changes (for example 345 MW becomes 500 MW);
* an event date is syntactically or chronologically impossible;
* an unreviewed/fallback record reaches the automatic-delivery boundary; and
* an optional card field introduces a concrete claim absent from the article.

Public gate functions do not mutate their inputs and make no network/LLM calls
(the existing local entity registry is read once and cached).  They return a
copy plus structured diagnostics, so callers can log, quarantine, or drop only
the unsafe optional field.  Ambiguous or evidence-poor inputs are allowed with
a warning; this is a guardrail, not a replacement for editorial review.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from functools import lru_cache
import hashlib
import hmac
import json
import re
from typing import Iterable, Mapping, Sequence

from data_quality import clean_text, is_complete_sentence
import entity_match
import event_stage


CURATION_STATUSES = frozenset({"reviewed", "fallback", "unreviewed", "quarantined"})
EVIDENCE_MANIFEST_VERSION = 2

# These fields are analysis, not the event itself.  Unsupported concrete facts
# are removed field-by-field rather than causing the whole article to disappear.
OPTIONAL_CARD_FIELDS = ("why", "investment", "kr_takeaway")
CORE_CARD_FIELDS = ("headline", "what")


@dataclass(frozen=True)
class Finding:
    """One machine-readable gate observation."""

    code: str
    severity: str
    field: str = ""
    message: str = ""
    details: Mapping[str, object] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict:
        result = {
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class GateResult:
    """Sanitized copy and the action recommended to the caller."""

    value: dict
    action: str = "allow"  # allow | sanitize | quarantine
    removed_fields: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.action != "quarantine"

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "eligible": self.eligible,
            "removed_fields": list(self.removed_fields),
            "findings": [finding.as_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class EligibilityDecision:
    """Automatic-delivery decision for reviewed/fallback/legacy records."""

    status: str
    eligible: bool
    action: str  # auto_send | legacy_allow | hold | quarantine
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "eligible": self.eligible,
            "action": self.action,
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
        }


# Country names are used only as a corroborating signal.  A country mismatch by
# itself never quarantines an article because cross-border contracts routinely
# mention more than one country.
_COUNTRY_TERMS: Mapping[str, tuple[str, ...]] = {
    "KR": ("한국", "대한민국", "south korea", "korean"),
    "US": ("미국", "미 정부", "u.s.", "u.s ", "united states", "american"),
    "CA": ("캐나다", "canada", "canadian"),
    "ES": ("스페인", "spain", "spanish"),
    "FR": ("프랑스", "france", "french"),
    "GB": ("영국", "united kingdom", "britain", "british", "u.k."),
    "CZ": ("체코", "czech"),
    "PL": ("폴란드", "poland", "polish"),
    "HU": ("헝가리", "hungary", "hungarian"),
    "RO": ("루마니아", "romania", "romanian"),
    "SK": ("슬로바키아", "slovakia", "slovak"),
    "UA": ("우크라이나", "ukraine", "ukrainian"),
    "RU": ("러시아", "russia", "russian"),
    "CN": ("중국", "china", "chinese"),
    "JP": ("일본", "japan", "japanese"),
    "AE": ("아랍에미리트", "uae", "united arab emirates"),
    "FI": ("핀란드", "finland", "finnish"),
    "SE": ("스웨덴", "sweden", "swedish"),
    "IN": ("인도", "india", "indian"),
    "AU": ("호주", "australia", "australian"),
    "KZ": ("카자흐스탄", "kazakhstan", "kazakh"),
}


# Broad families, intentionally.  "SMR supply contract" and "SMR project"
# should both be reactor news, while a uranium mine and a lifetime extension are
# substantively different.  Generic policy/contract words are not families.
_TOPIC_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "fuel_supply": (
        "우라늄", "광산", "채굴", "정광", "옐로케이크", "uranium", "yellowcake",
        "mining", "mine project", "enrichment", "농축", "핵연료", "nuclear fuel",
    ),
    "reactor_project": (
        "원전", "원자로", "smr", "sfr", "apr1400", "ap1000", "reactor",
        "nuclear plant", "nuclear power plant", "small modular reactor",
    ),
    "reactor_operation": (
        "계속운전", "수명 연장", "운영 연장", "가동", "운전", "재가동", "정지",
        "lifetime extension", "life extension", "operation", "restart", "shutdown",
    ),
    "waste": (
        "방폐물", "사용후핵연료", "고준위", "저준위", "처분장", "핵폐기물",
        "radioactive waste", "spent fuel", "repository",
    ),
    "safety": (
        "사고", "누출", "누설", "피폭", "방사선", "안전사고", "고장", "화재",
        "radiation", "incident", "accident", "leak", "malfunction",
    ),
    "decommissioning": (
        "해체", "폐로", "영구정지", "decommission", "dismantl",
    ),
    "grid": (
        "송전망", "전력망", "배전망", "변압기", "ess", "grid", "transmission",
        "transformer", "energy storage",
    ),
    "fusion": ("핵융합", "fusion", "tokamak", "토카막"),
}


_FACTUAL_ASSERTION_RE = re.compile(
    r"(?:체결|수주|공급(?!망)|참여|승인|허가|착공|완공|취득|발표|확정|선정|재가동|중단|"
    r"투자(?:했|한|한다|했다|하였다|해|를\s*(?:결정|집행))|진출(?:했|한|한다|했다)|"
    r"설립(?:했|한|한다|했다)|인수(?:했|한|한다|했다)|"
    r"signed|awarded|supplied|participated|approved|licensed|completed|announced|"
    r"invested|invests|acquired|established)",
    re.IGNORECASE,
)
_ANALYTIC_MARKERS = (
    "가능", "전망", "시사", "관점", "수혜", "기회", "리스크", "영향", "검토",
    "필요", "기대", "잠재", "참고", "전략", "것으로 보", "될 것", "수 있다",
    "could", "may", "might", "outlook",
    "potential",
)


_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\d{1,3}(?:[ ,]\d{3})+|\d+(?:[.,]\d+)?)[-\s]*"
    r"(?:%|퍼센트|mw|gw|kw|twh|mwh|억원|억\s*원|조원|조\s*원|만\s*달러|"
    r"억\s*달러|달러|유로|기(?!술)|호기|개|건|명|년|개월|월|일)"
    r"(?=$|[^A-Za-z0-9가-힣]|[은는이가을를로의에와과급까부경께대])",
    re.IGNORECASE,
)
_MODEL_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2,8}[- ]?\d{2,4}(?![A-Za-z0-9])")
_NUMBER_UNIT_RE = re.compile(
    r"(?P<number>(?:\d{1,3}(?:[ ,]\d{3})+|\d+(?:[.,]\d+)?))"
    r"[-\s]*"
    r"(?P<unit>%|퍼센트|mw|gw|kw|twh|mwh|억원|억 원|조원|조 원|달러|유로|기|호기|개|건|명|년|개월|월|일)",
    re.IGNORECASE,
)
_UNIT_LIST_RE = re.compile(
    r"(?P<numbers>\d{1,2}(?:\s*[,·ㆍ･]\s*\d{1,2})+)\s*(?P<unit>호기|기)")
_CRITICAL_UNITS = frozenset({"%", "퍼센트", "mw", "gw", "kw", "twh", "mwh", "억원", "억 원", "조원", "조 원", "달러", "유로", "기", "호기"})
_EXACT_QUANTITY_UNITS = frozenset({"기", "호기", "개", "건", "명", "년", "개월", "월", "일"})
_CANONICAL_UNITS: Mapping[str, tuple[str, Decimal]] = {
    "kw": ("mw", Decimal("0.001")),
    "mw": ("mw", Decimal("1")),
    "gw": ("mw", Decimal("1000")),
    "mwh": ("mwh", Decimal("1")),
    "twh": ("mwh", Decimal("1000000")),
    "억 원": ("억원", Decimal("1")),
    "조 원": ("조원", Decimal("1")),
}
_EN_COUNT_RE = re.compile(
    r"\b(?P<number>\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty)\s+(?:new\s+)?(?:nuclear\s+(?:power\s+)?)?"
    r"(?:reactors?|units?)\b",
    re.IGNORECASE,
)
_EN_UNIT_ID_RE = re.compile(
    r"\b(?:unit|reactor)\s*(?:no\.?\s*)?(?P<number>\d{1,2})\b",
    re.IGNORECASE,
)
_EN_NUMBERS = {
    word: str(number) for number, word in enumerate((
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    ))
}
_DATE_MARKER_RE = re.compile(
    r"(?:(?<!\d)(?:19|20|21)\d{6}(?!\d)"
    r"|(?<!\d)(?:19|20|21)\d{2}(?!\d)"
    r"|(?<!\d)\d{2}\s*년"
    r"|(?<!\d)(?:0?[1-9]|1[0-2])\s*[/.-]\s*(?:0?[1-9]|[12]\d|3[01])(?!\d)"
    r"|\b\d{1,2}\s*월\s*\d{1,2}\s*일"
    r"|\b\d{1,2}\s*일(?:\b|에|부터|까지|께|경)"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre|enero|febrero|marzo|abril|mayo|"
    r"junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b"
    r"|오늘|어제|내일|금일|전날|오는\s|지난\s"
    r"|\b(?:today|yesterday|tomorrow|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday)\b)",
    re.IGNORECASE,
)

_MONTH_NUMBERS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_FULL_NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20|21)\d{2})\s*(?:년|[-/.])\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:월|[-/.])\s*"
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s*일?(?!\d)", re.IGNORECASE)
_COMPACT_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20|21)\d{2})(?P<month>0[1-9]|1[0-2])"
    r"(?P<day>0[1-9]|[12]\d|3[01])(?!\d)")
_MONTH_DAY_RE = re.compile(
    # A bare dot is far more often a decimal/version (1.5) than a date. Full
    # dotted dates remain supported by _FULL_NUMERIC_DATE_RE.
    r"(?<![\d-])(?P<month>0?[1-9]|1[0-2])\s*(?:월|[/-])\s*"
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s*일?(?!\d)")
_EN_MONTH_DAY_RE = re.compile(
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>(?:19|20|21)\d{2}))?\b", re.IGNORECASE)
_EN_DAY_MONTH_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)(?:,?\s+(?P<year>(?:19|20|21)\d{2}))?\b",
    re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(?P<year>(?:19|20|21)\d{2})(?!\d)")
_YEAR_MONTH_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20|21)\d{2})\s*(?:년|[-/.])\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*월?(?![\d/.-])")
_MONTH_ONLY_RE = re.compile(r"(?<!\d)(?P<month>0?[1-9]|1[0-2])\s*월")
_DAY_ONLY_RE = re.compile(
    r"(?<![\d월])(?P<day>0?[1-9]|[12]\d|3[01])\s*일(?:\b|에|부터|까지|께|경)")


def _contains_term(text: str, term: str) -> bool:
    lowered = text.casefold()
    token = term.casefold()
    if re.search(r"[a-z]", token):
        return bool(re.search(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])", lowered))
    return token in lowered


def _countries(text: object) -> frozenset[str]:
    cleaned = clean_text(text)
    found = {
        code for code, terms in _COUNTRY_TERMS.items()
        if any(_contains_term(cleaned, term) for term in terms)
    }
    # Bare acronyms are meaningful only with their original capitalization;
    # lower-casing "US" would confuse the country with the English pronoun us.
    for acronym, code in (("US", "US"), ("USA", "US"), ("UK", "GB"), ("UAE", "AE")):
        if re.search(r"(?<![A-Za-z])" + acronym + r"(?![A-Za-z])", cleaned):
            found.add(code)
    return frozenset(found)


def _topics(text: object) -> frozenset[str]:
    cleaned = clean_text(text)
    found = {
        family for family, patterns in _TOPIC_PATTERNS.items()
        if any(_contains_term(cleaned, pattern) for pattern in patterns)
    }
    # Operations are a reactor subtopic, but keeping both lets a uranium mine and
    # a lifetime-extension hallucination conflict while avoiding a conflict
    # between an SMR supplier and its reactor project.
    if "reactor_operation" in found:
        found.add("reactor_project")
    return frozenset(found)


@lru_cache(maxsize=1)
def _entity_aliases() -> tuple[tuple[str, str, str, str], ...]:
    """(entity id, type, policy, alias) including Korean and English names."""
    rows: list[tuple[str, str, str, str]] = []
    for entity in entity_match.load_entity_registry():
        aliases = list(entity.get("aliases") or [])
        aliases.extend((entity.get("name_kr") or "", entity.get("name_en") or ""))
        seen: set[str] = set()
        for alias in aliases:
            alias = clean_text(alias)
            key = re.sub(r"[^0-9a-z가-힣]", "", alias.casefold())
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append((entity["id"], entity.get("type", ""),
                         entity.get("match_policy", "token"), alias))
    rows.sort(key=lambda row: len(row[3]), reverse=True)
    return tuple(rows)


def _alias_present(text: str, alias: str, policy: str) -> bool:
    if policy == "tag_only":
        return False
    lowered = text.casefold()
    alias_lower = alias.casefold()
    if re.search(r"[가-힣]", alias_lower):
        # Short plant names that overlap ordinary Korean are accepted only next
        # to a unit or an explicit plant marker.
        if policy == "tag_or_unit_adjacent":
            return bool(re.search(
                re.escape(alias_lower) + r"\s*(?:원전|\d+\s*호기)", lowered
            ))
        return alias_lower in lowered
    pieces = [re.escape(piece) for piece in re.findall(r"[0-9a-z]+", alias_lower)]
    if not pieces:
        return False
    pattern = r"(?<![0-9a-z])" + r"[\s.&/\-]*".join(pieces) + r"(?![0-9a-z])"
    return bool(re.search(pattern, lowered))


def _entities(text: object) -> frozenset[str]:
    cleaned = clean_text(text)
    if not cleaned:
        return frozenset()
    return frozenset(
        entity_id for entity_id, _kind, policy, alias in _entity_aliases()
        if _alias_present(cleaned, alias, policy)
    )


def _entity_types(ids: Iterable[str]) -> dict[str, str]:
    wanted = set(ids)
    result: dict[str, str] = {}
    for entity_id, kind, _policy, _alias in _entity_aliases():
        if entity_id in wanted:
            result[entity_id] = kind
    return result


def _normalize_claim(value: object) -> str:
    text = clean_text(value).casefold()
    # European decimal comma: 1,7% == 1.7%. Three following digits remain a
    # thousands group (1,200억원).
    text = re.sub(r"(?<=\d),(?=\d{1,2}(?:\D|$))", ".", text)
    text = text.replace(",", "")
    text = re.sub(r"\s+", "", text)
    return text.replace("퍼센트", "%")


def concrete_claims(text: object) -> tuple[str, ...]:
    """Return deterministic quantities/model identifiers in appearance order."""
    cleaned = clean_text(text)
    claims: list[str] = []
    for match in _QUANTITY_RE.finditer(cleaned):
        claim = _normalize_claim(match.group(0))
        if claim and claim not in claims:
            claims.append(claim)
    for match in _MODEL_ID_RE.finditer(cleaned):
        raw = match.group(0)
        prefix = re.match(r"[A-Za-z]+", raw)
        # Month + day/year ("March 10", "August 2026") is a date, not a
        # reactor/model identifier. Date support is recorded separately below.
        if (prefix
                and prefix.group(0).casefold().rstrip(".") in _MONTH_NUMBERS
                and raw[len(prefix.group(0)):].startswith(" ")):
            continue
        claim = _normalize_claim(raw)
        if claim and claim not in claims:
            claims.append(claim)
    return tuple(claims)


def _canonical_quantity(unit: str, number: str) -> tuple[str, str]:
    """Compare equivalent power/energy units in one deterministic scale."""
    canonical_unit, multiplier = _CANONICAL_UNITS.get(
        unit, (unit, Decimal("1")))
    try:
        value = Decimal(number) * multiplier
    except (InvalidOperation, ValueError):
        return canonical_unit, number
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return canonical_unit, rendered or "0"


def _quantity_map(text: object) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    compact = clean_text(text).casefold()
    for match in _UNIT_LIST_RE.finditer(compact):
        result.setdefault(match.group("unit"), set()).update(
            re.findall(r"\d{1,2}", match.group("numbers")))
    for match in _NUMBER_UNIT_RE.finditer(compact):
        unit = re.sub(r"\s+", " ", match.group("unit").lower()).strip()
        if unit == "퍼센트":
            unit = "%"
        number = match.group("number").replace(" ", "")
        if re.fullmatch(r"\d+,\d{1,2}", number):
            number = number.replace(",", ".")
        else:
            number = number.replace(",", "")
        unit, number = _canonical_quantity(unit, number)
        result.setdefault(unit, set()).add(number)
    for match in _EN_COUNT_RE.finditer(compact):
        number = match.group("number").lower()
        result.setdefault("기", set()).add(_EN_NUMBERS.get(number, number))
    for match in _EN_UNIT_ID_RE.finditer(compact):
        result.setdefault("호기", set()).add(match.group("number"))
    return result


def _numbers_overlap(left: Iterable[str], right: Iterable[str], *, unit: str = "") -> bool:
    """Compare quantities with unit-aware rounding policy.

    Percentages, capacity, energy and money may be rounded by a headline.
    Counts, unit numbers and calendar components are discrete: 100 reactors is
    never evidence for 101, and 2024 is never evidence for 2025.
    """
    exact = clean_text(unit).casefold() in _EXACT_QUANTITY_UNITS
    for left_raw in left:
        for right_raw in right:
            try:
                left_value, right_value = float(left_raw), float(right_raw)
            except (TypeError, ValueError):
                if left_raw == right_raw:
                    return True
                continue
            if left_value == right_value:
                return True
            if exact:
                continue
            scale = max(abs(left_value), abs(right_value))
            if scale and abs(left_value - right_value) / scale <= 0.01:
                return True
    return False


def _detect_stages(text: object) -> frozenset[str]:
    """event_stage plus two unambiguous agreement spellings seen in cards."""
    cleaned = clean_text(text)
    stages = set(event_stage.detect_stages(cleaned))
    compact = re.sub(r"\s+", "", cleaned.casefold())
    if (("협약" in compact or "agreement" in compact)
            and any(marker in compact for marker in ("체결", "sign", "signed"))):
        stages.add("contract")
    return frozenset(stages)


def _critical_quantity_conflicts(left: object, right: object) -> dict[str, dict[str, list[str]]]:
    left_map, right_map = _quantity_map(left), _quantity_map(right)
    conflicts: dict[str, dict[str, list[str]]] = {}
    for unit in sorted(set(right_map) & _CRITICAL_UNITS):
        source_values = left_map.get(unit, set())
        unsupported = {
            output_value for output_value in right_map[unit]
            if not source_values
            or not _numbers_overlap({output_value}, source_values, unit=unit)
        }
        if unsupported:
            supported = set(right_map[unit]) - unsupported
            conflicts[unit] = {
                "source": sorted(source_values),
                "output": sorted(right_map[unit]),
                "unsupported_output": sorted(unsupported),
                "supported_output": sorted(supported),
            }
    return conflicts


def _parse_reference_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, OverflowError):
        return None


def _reference_date(article: Mapping[str, object], source: Mapping[str, object] | None,
                    explicit: object) -> date:
    parsed = _parse_reference_date(explicit)
    if parsed:
        return parsed
    for mapping in (source or {}, article):
        for key in ("published_at", "published", "pub", "queued_at", "cached_at"):
            parsed = _parse_reference_date(mapping.get(key))
            if parsed:
                return parsed
    return datetime.now(timezone.utc).date()


def _source_parts(article: Mapping[str, object], source: Mapping[str, object] | None) -> dict[str, str]:
    raw = source or {}
    return {
        "title": clean_text(raw.get("title") or article.get("title")),
        "description": clean_text(raw.get("description") or article.get("description")),
        "article_text": clean_text(
            raw.get("article_text") or raw.get("body") or raw.get("body_text")
            or article.get("article_text") or article.get("body") or article.get("body_text")
            or article.get("source_excerpt")
        ),
    }


def _mismatch_signals(source_text: str, output_text: str) -> dict[str, object]:
    source_entities, output_entities = _entities(source_text), _entities(output_text)
    source_countries, output_countries = _countries(source_text), _countries(output_text)
    source_topics, output_topics = _topics(source_text), _topics(output_text)
    source_stages = _detect_stages(source_text)
    output_stages = _detect_stages(output_text)
    entity_conflict = bool(source_entities and output_entities
                           and source_entities.isdisjoint(output_entities))
    country_conflict = bool(source_countries and output_countries
                            and source_countries.isdisjoint(output_countries))
    topic_conflict = bool(source_topics and output_topics
                          and source_topics.isdisjoint(output_topics))
    introduced_entities = output_entities - source_entities
    missing_entities = source_entities - output_entities
    introduced_countries = output_countries - source_countries
    missing_countries = source_countries - output_countries
    introduced_topics = output_topics - source_topics
    missing_topics = source_topics - output_topics
    return {
        "source_entities": sorted(source_entities),
        "output_entities": sorted(output_entities),
        "source_countries": sorted(source_countries),
        "output_countries": sorted(output_countries),
        "source_topics": sorted(source_topics),
        "output_topics": sorted(output_topics),
        "source_stages": sorted(source_stages),
        "output_stages": sorted(output_stages),
        "entity_conflict": entity_conflict,
        "country_conflict": country_conflict,
        "topic_conflict": topic_conflict,
        "introduced_entities": sorted(introduced_entities),
        "missing_entities": sorted(missing_entities),
        "introduced_countries": sorted(introduced_countries),
        "missing_countries": sorted(missing_countries),
        "introduced_topics": sorted(introduced_topics),
        "missing_topics": sorted(missing_topics),
        "entity_replacement": bool(introduced_entities and missing_entities),
        "country_replacement": bool(introduced_countries and missing_countries),
        "topic_replacement": bool(introduced_topics and missing_topics),
        "stage_conflict": event_stage.stage_conflict(source_stages, output_stages),
        "quantity_conflicts": _critical_quantity_conflicts(source_text, output_text),
    }


def _gross_mismatch(signals: Mapping[str, object], *, quantity_is_hard: bool = True,
                    directional_is_hard: bool = False) -> bool:
    """Require a hard identifier/quantity contradiction plus corroboration.

    A country or topic signal alone is never a hard block.  A disjoint explicit
    country *and* a disjoint substantive topic is treated as a named-entity
    contradiction (for example Canada/uranium mine -> Spain/reactor lifetime).
    This asymmetry is important for translated titles and cross-border projects.
    """
    quantity_details = signals.get("quantity_conflicts") or {}
    quantity_conflicts = bool(quantity_details)
    quantity_direct_contradiction = any(
        bool(details.get("source")) and not bool(details.get("supported_output"))
        for details in quantity_details.values()
        if isinstance(details, Mapping)
    ) if isinstance(quantity_details, Mapping) else False
    entity_conflict = bool(signals.get("entity_conflict"))
    corroboration = any(bool(signals.get(key)) for key in (
        "country_conflict", "topic_conflict", "stage_conflict",
        "country_replacement", "topic_replacement", "quantity_conflicts",
    ))
    # A key quantity changing while the surrounding subject is recognisably the
    # same is already a concrete contradiction.  A lone entity switch needs a
    # second independent signal because an article may introduce a counterparty.
    country_topic_switch = bool(signals.get("country_conflict")) and bool(
        signals.get("topic_conflict")
    )
    # With retained description/body evidence, an output-only named entity or
    # country is itself directional evidence of invention. On old title-only
    # archives we keep the conservative corroboration rule to avoid hiding a
    # legitimate body-only counterparty.
    directional_addition = directional_is_hard and bool(
        signals.get("introduced_entities") or signals.get("introduced_countries")
    )
    identifier_replacement = bool(signals.get("entity_replacement")) and corroboration
    country_replacement = bool(signals.get("country_replacement")) and any(
        bool(signals.get(key)) for key in (
            "entity_conflict", "entity_replacement", "topic_conflict",
            "topic_replacement", "stage_conflict",
        )
    )
    return ((quantity_is_hard and quantity_conflicts
             and (quantity_direct_contradiction or directional_is_hard))
            or directional_addition
            or (entity_conflict and corroboration)
            or identifier_replacement
            or country_replacement
            or country_topic_switch)


def _date_problem(event_date: object, event_type: object, reference: date) -> str:
    raw = clean_text(event_date)
    if not raw:
        return ""
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return "invalid"
    if parsed.year < 1900 or parsed.year > reference.year + 50:
        return "implausible_year"
    kind = clean_text(event_type).lower()
    # A completed announcement/occurrence cannot sit months in the future.
    # Keep two days for timezone/publication-feed skew, not the old one-year gap.
    if kind in {"announcement", "occurrence"} and parsed > reference + timedelta(days=2):
        return "future_completed_event"
    return ""


def _nearest_year_date(month: int, day: int, reference: date) -> date | None:
    candidates = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    return min(candidates, key=lambda value: abs((value - reference).days)) if candidates else None


def _nearest_month_date(day: int, reference: date) -> date | None:
    candidates = []
    for offset in (-1, 0, 1):
        index = reference.year * 12 + reference.month - 1 + offset
        year, month_index = divmod(index, 12)
        try:
            candidates.append(date(year, month_index + 1, day))
        except ValueError:
            continue
    return min(candidates, key=lambda value: abs((value - reference).days)) if candidates else None


def _explicit_evidence_dates(text: str, reference: date) -> set[date]:
    """Extract only day-level dates that can be compared without an LLM."""
    found: set[date] = set()

    def add(year: object, month: object, day: object) -> None:
        try:
            parsed = date(int(year), int(month), int(day))
        except (TypeError, ValueError):
            return
        found.add(parsed)

    for regex in (_FULL_NUMERIC_DATE_RE, _COMPACT_DATE_RE):
        for match in regex.finditer(text):
            add(match.group("year"), match.group("month"), match.group("day"))

    # A full date also contains a month/day substring. Remove it first so it is
    # not interpreted a second time with the publication year.
    without_full = _FULL_NUMERIC_DATE_RE.sub(" ", _COMPACT_DATE_RE.sub(" ", text))
    for match in _MONTH_DAY_RE.finditer(without_full):
        parsed = _nearest_year_date(int(match.group("month")), int(match.group("day")), reference)
        if parsed:
            found.add(parsed)

    for regex in (_EN_MONTH_DAY_RE, _EN_DAY_MONTH_RE):
        for match in regex.finditer(text):
            month_token = match.group("month").lower().rstrip(".")
            month = _MONTH_NUMBERS.get(month_token) or _MONTH_NUMBERS.get(month_token[:3])
            if not month:
                continue
            year = match.group("year")
            if year:
                add(year, month, match.group("day"))
            else:
                parsed = _nearest_year_date(month, int(match.group("day")), reference)
                if parsed:
                    found.add(parsed)

    without_qualified = _MONTH_DAY_RE.sub(" ", without_full)
    lowered = text.casefold()
    if any(marker in lowered for marker in ("오늘", "금일", "today")):
        found.add(reference)
    if any(marker in lowered for marker in ("어제", "전날", "yesterday")):
        found.add(reference - timedelta(days=1))
    if any(marker in lowered for marker in ("내일", "tomorrow")):
        found.add(reference + timedelta(days=1))
    return found


def _date_evidence_problem(expected: date, precision: object,
                           evidence: str, reference: date) -> str:
    """Return conflict/unsubstantiated when precision exceeds the evidence."""
    explicit_days = _explicit_evidence_dates(evidence, reference)
    years = {int(match.group("year")) for match in _YEAR_RE.finditer(evidence)}
    months = {(value.year, value.month) for value in explicit_days}
    for match in _YEAR_MONTH_RE.finditer(evidence):
        months.add((int(match.group("year")), int(match.group("month"))))
    for match in _MONTH_ONLY_RE.finditer(evidence):
        candidate = _nearest_year_date(int(match.group("month")), 1, reference)
        if candidate:
            months.add((candidate.year, candidate.month))
    years.update(year for year, _month in months)

    level = clean_text(precision).lower()
    if level == "year":
        if expected.year in years:
            return ""
        return "source_conflict" if years else "source_unsubstantiated"
    if level == "month":
        if (expected.year, expected.month) in months:
            return ""
        if months:
            return "source_conflict"
        if years and expected.year not in years:
            return "source_conflict"
        return "source_unsubstantiated"

    # Day/unknown precision requires an actual month and day (or a relative
    # today/yesterday/tomorrow marker resolved against the publication date).
    # A matching year or month alone cannot justify inventing a calendar day.
    if expected in explicit_days:
        return ""
    if explicit_days:
        return "source_conflict"
    if months and (expected.year, expected.month) not in months:
        return "source_conflict"
    if years and expected.year not in years:
        return "source_conflict"
    return "source_unsubstantiated"


def _declared_date_evidence(
    article: Mapping[str, object], source: Mapping[str, object] | None,
    source_kind: object,
) -> tuple[bool, str]:
    """Return whether the exact declared evidence field is actually available."""
    kind = clean_text(source_kind).lower()
    raw = source or {}
    if kind == "title":
        text = clean_text(raw.get("title") or article.get("title"))
        return bool(text), text
    if kind == "description":
        text = clean_text(raw.get("description") or article.get("description"))
        return bool(text), text
    if kind == "article_text":
        # A delivery-time source_excerpt may be deliberately short, so absence of
        # a date there is not proof.  Verify only a body explicitly supplied as
        # article text/body by the collection stage.
        text = clean_text(
            raw.get("article_text") or raw.get("body") or raw.get("body_text")
            or article.get("article_text") or article.get("body") or article.get("body_text")
        )
        return bool(text), text
    return False, ""


def audit_article_integrity(
    article: Mapping[str, object],
    *,
    source: Mapping[str, object] | None = None,
    reference_date: object = None,
) -> GateResult:
    """Audit and sanitize one curated article.

    ``source`` may contain ``title``, ``description``, ``article_text``/``body``
    and ``published_at``.  When source material is sparse the function declines
    to guess and emits no mismatch finding.  Gross title/summary mismatch is a
    quarantine recommendation; an impossible event date is only cleared.
    """
    cleaned = deepcopy(dict(article))
    findings: list[Finding] = []
    removed: list[str] = []
    quarantine = False
    parts = _source_parts(article, source)
    source_title = " ".join(filter(None, parts.values()))
    output_title = clean_text(article.get("title_kr"))

    if source_title and output_title and _normalize_claim(parts["title"]) != _normalize_claim(output_title):
        signals = _mismatch_signals(source_title, output_title)
        if _gross_mismatch(
            signals,
            directional_is_hard=bool(parts["description"] or parts["article_text"]),
        ):
            quarantine = True
            findings.append(Finding(
                "title_source_mismatch", "quarantine", "title_kr",
                "번역 제목이 원문과 다른 핵심 엔티티 또는 수치를 가리킵니다.", signals,
            ))

    summary = clean_text(article.get("summary"))
    # Summary may legitimately use body-only facts, so include every available
    # source part.  No source text means no deterministic summary verdict.
    source_all = " ".join(filter(None, (*parts.values(), output_title)))
    if source_all and summary:
        signals = _mismatch_signals(source_all, summary)
        if _gross_mismatch(
            signals,
            # A summary may legitimately add a second body metric. At delivery
            # and archive time the body is intentionally not retained, so a
            # title-only quantity difference is inconclusive.
            quantity_is_hard=bool(parts["description"] or parts["article_text"]),
            directional_is_hard=bool(parts["description"] or parts["article_text"]),
        ):
            quarantine = True
            cleaned["summary"] = ""
            removed.append("summary")
            findings.append(Finding(
                "summary_source_mismatch", "quarantine", "summary",
                "요약이 원문과 다른 핵심 엔티티 또는 수치를 가리킵니다.", signals,
            ))

    ref = _reference_date(article, source, reference_date)
    date_problem = _date_problem(article.get("event_date"), article.get("event_date_type"), ref)
    if not date_problem and clean_text(article.get("event_date")):
        declared_source = clean_text(article.get("event_date_source")).lower()
        evidence_provided, date_evidence = _declared_date_evidence(
            article, source, declared_source
        )
        if declared_source not in {"title", "description", "article_text"}:
            date_problem = "source_unknown"
        elif not evidence_provided:
            date_problem = "source_unavailable"
        else:
            if not _DATE_MARKER_RE.search(date_evidence):
                date_problem = "source_unsubstantiated"
            else:
                expected = date.fromisoformat(clean_text(article.get("event_date")))
                date_problem = _date_evidence_problem(
                    expected, article.get("event_date_precision"), date_evidence, ref
                )
    if date_problem:
        for key, default in (
            ("event_date", None), ("event_date_type", "unknown"),
            ("event_date_precision", "unknown"), ("event_date_source", "unknown"),
        ):
            cleaned[key] = default
        removed.append("event_date")
        findings.append(Finding(
            f"event_date_{date_problem}", "sanitize", "event_date",
            "검증할 수 없는 사건일을 비웠습니다.",
            {"value": article.get("event_date"), "reference_date": ref.isoformat()},
        ))

    if quarantine:
        action = "quarantine"
    elif removed:
        action = "sanitize"
    else:
        action = "allow"
    return GateResult(cleaned, action, tuple(dict.fromkeys(removed)), tuple(findings))


def infer_curation_status(article: Mapping[str, object]) -> str:
    """Classify explicit new records and old-schema records conservatively."""
    explicit = clean_text(article.get("curation_status")).lower()
    if explicit in CURATION_STATUSES:
        return explicit
    if clean_text(article.get("curation_source")).lower() == "fallback":
        return "fallback"
    if isinstance(article.get("features"), dict):
        return "reviewed"

    title = _normalize_claim(article.get("title"))
    title_kr = _normalize_claim(article.get("title_kr"))
    has_analysis = any(clean_text(article.get(key)) for key in (
        "detail", "implication", "why_important", "open_question",
    ))
    # This is the exact shape produced by the legacy fallback path.  Keep the
    # inference narrow so an intentionally old schema is not silently blocked.
    if title and title == title_kr and clean_text(article.get("summary")) and not has_analysis:
        return "fallback"
    return "unreviewed"


def assess_delivery_eligibility(
    article: Mapping[str, object],
    *,
    integrity: GateResult | None = None,
    legacy_compat: bool = True,
    allow_primary_fallback: bool = False,
) -> EligibilityDecision:
    """Decide whether a record may be sent automatically.

    New callers should persist ``curation_status`` and use ``legacy_compat=False``.
    The default remains compatible with old non-fallback queue records.  A
    fallback is held unless the caller explicitly opts into the narrow primary-
    source exception; even then optional analysis fields must not be sent.
    """
    status = infer_curation_status(article)
    if status == "quarantined" or (integrity and not integrity.eligible):
        return EligibilityDecision(status, False, "quarantine", ("integrity_quarantine",))

    title = clean_text(article.get("title_kr") or article.get("title"))
    summary = clean_text(article.get("summary"))
    if not title or not summary:
        missing = []
        if not title:
            missing.append("headline_missing")
        if not summary:
            missing.append("summary_missing")
        return EligibilityDecision(status, False, "hold", tuple(missing))

    # Existing queues predate both ``curation_status`` and the complete-sentence
    # validator.  They must remain deliverable during migration, while every new
    # record carries an explicit status and therefore takes the stricter path.
    if status == "unreviewed" and legacy_compat:
        return EligibilityDecision(status, True, "legacy_allow", ("legacy_schema",))
    if not is_complete_sentence(summary):
        return EligibilityDecision(status, False, "hold", ("summary_incomplete",))

    if status == "reviewed":
        return EligibilityDecision(status, True, "auto_send")
    if status == "fallback":
        primary = (article.get("evidence_role") == "primary"
                   or article.get("source_type") == "official")
        if allow_primary_fallback and primary:
            return EligibilityDecision(
                status, True, "auto_send", ("primary_source_fallback",),
                ("why_important", "investment", "kr_takeaway", "implication"),
            )
        return EligibilityDecision(status, False, "hold", ("fallback_requires_review",))
    return EligibilityDecision(status, False, "hold", ("unreviewed_requires_review",))


def _card_evidence(article: Mapping[str, object], source: Mapping[str, object] | None) -> str:
    parts = _source_parts(article, source)
    # At delivery time the fetched body is intentionally no longer stored.  The
    # reviewed title/summary/detail are therefore accepted as secondary evidence
    # for checking later enrichments.  ``source_excerpt`` can strengthen this.
    return " ".join(filter(None, (
        *parts.values(),
        clean_text(article.get("title_kr")), clean_text(article.get("summary")),
        clean_text(article.get("source_excerpt")),
    )))


def _digest_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_source_binding(
    article: Mapping[str, object], source: Mapping[str, object] | None,
) -> dict[str, str]:
    """Build only from source identity fields retained in the delivery queue."""
    raw = source or {}
    article_hash = clean_text(
        raw.get("article_hash") or raw.get("hash") or article.get("hash")
    )
    title = clean_text(raw.get("title") or article.get("title"))
    # Crawler and queue both cap this source snippet at 600 characters. The
    # fetched body is excluded so the binding never requires storing it.
    excerpt = clean_text(
        raw.get("description") or article.get("source_excerpt")
        or article.get("description")
    )[:600]
    # Presence matters: an explicit empty ``published_at`` means the source
    # timestamp was invalid and the queue intentionally fell back elsewhere.
    # Do not let a delivery helper's queued_at fallback alter this binding.
    if "published_at" in article:
        published_at = clean_text(article.get("published_at"))
    elif "published_at" in raw:
        published_at = clean_text(raw.get("published_at"))
    else:
        published_at = ""
    return {
        "article_hash": article_hash,
        "title": _normalize_claim(title),
        "source_excerpt": _normalize_claim(excerpt),
        "published_at": published_at,
    }


def _binding_components(binding: Mapping[str, str]) -> dict[str, str]:
    """Hash each retained source component so archives need not store text."""
    return {
        "article_hash": clean_text(binding.get("article_hash")),
        "title": hashlib.sha256(
            clean_text(binding.get("title")).encode("utf-8")
        ).hexdigest(),
        "source_excerpt": hashlib.sha256(
            clean_text(binding.get("source_excerpt")).encode("utf-8")
        ).hexdigest(),
        "published_at": hashlib.sha256(
            clean_text(binding.get("published_at")).encode("utf-8")
        ).hexdigest(),
    }


def _clean_source_components(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    keys = ("article_hash", "title", "source_excerpt", "published_at")
    result = {key: clean_text(value.get(key)) for key in keys}
    if not result["article_hash"] or any(
            len(result[key]) != 64 for key in keys if key != "article_hash"):
        return {}
    return result


def evidence_manifest_source_components(manifest: object) -> dict[str, str]:
    """Return the sealed source-component digests for archive preservation."""
    if not isinstance(manifest, Mapping):
        return {}
    supplied_seal = clean_text(manifest.get("manifest_fingerprint"))
    sealed = {
        str(key): value for key, value in manifest.items()
        if key != "manifest_fingerprint"
    }
    if (not supplied_seal
            or not hmac.compare_digest(supplied_seal, _digest_payload(sealed))):
        return {}
    return _clean_source_components(manifest.get("source_components"))


def evidence_manifest_is_valid(
    manifest: object,
    *,
    article: Mapping[str, object],
    source: Mapping[str, object] | None = None,
) -> bool:
    """Verify version, article/source binding and manifest-content integrity."""
    if not isinstance(manifest, Mapping):
        return False
    if type(manifest.get("version")) is not int:
        return False
    if manifest.get("version") != EVIDENCE_MANIFEST_VERSION:
        return False
    supplied_seal = clean_text(manifest.get("manifest_fingerprint"))
    if not supplied_seal:
        return False
    sealed = {
        str(key): value for key, value in manifest.items()
        if key != "manifest_fingerprint"
    }
    if not hmac.compare_digest(supplied_seal, _digest_payload(sealed)):
        return False

    stored = _clean_source_components(manifest.get("source_components"))
    if not stored or clean_text(manifest.get("article_hash")) != stored["article_hash"]:
        return False
    if clean_text(manifest.get("source_fingerprint")) != _digest_payload(stored):
        return False

    binding = _manifest_source_binding(article, source)
    actual = _binding_components(binding)
    if not binding["article_hash"] or not binding["title"]:
        return False
    if actual["article_hash"] != stored["article_hash"] or actual["title"] != stored["title"]:
        return False

    raw = source or {}
    excerpt_available = bool(
        clean_text(raw.get("description"))
        or clean_text(article.get("source_excerpt"))
        or clean_text(article.get("description"))
        or "source_excerpt" in article
        or "description" in article
    )
    published_available = "published_at" in article or "published_at" in raw
    archived = _clean_source_components(article.get("verified_source_components"))
    for key, available in (
        ("source_excerpt", excerpt_available),
        ("published_at", published_available),
    ):
        if available:
            if actual[key] != stored[key]:
                return False
        elif not archived or archived[key] != stored[key]:
            return False
    return True


def build_evidence_manifest(
    source: Mapping[str, object],
    *,
    article: Mapping[str, object] | None = None,
) -> dict:
    """Persist source fingerprints without retaining copyrighted article text.

    The crawler can see the fetched body, while the later delivery process
    intentionally cannot.  This compact manifest lets final-card validation
    remember which concrete entities, dates and quantities really appeared in
    that body without storing or re-publishing the body itself.
    """
    parts = _source_parts(article or {}, source)
    evidence = " ".join(filter(None, parts.values()))
    binding = _manifest_source_binding(article or {}, source)
    if not binding["article_hash"] or not binding["title"]:
        return {}
    source_components = _binding_components(binding)
    quantity_map = _quantity_map(evidence)
    reference = _reference_date(article or {}, source, source.get("published_at"))
    # English source dates (March 9, 2024) must support the equivalent Korean
    # claims (2024년·9일) without storing the source sentence itself.
    for explicit_date in _explicit_evidence_dates(evidence, reference):
        quantity_map.setdefault("년", set()).add(str(explicit_date.year))
        quantity_map.setdefault("월", set()).add(str(explicit_date.month))
        quantity_map.setdefault("일", set()).add(str(explicit_date.day))
    quantities = {
        unit: sorted(values)[:40]
        for unit, values in sorted(quantity_map.items())
        if values
    }
    manifest = {
        "version": EVIDENCE_MANIFEST_VERSION,
        "article_hash": binding["article_hash"],
        "source_components": source_components,
        "source_fingerprint": _digest_payload(source_components),
        "entities": sorted(_entities(evidence))[:60],
        "countries": sorted(_countries(evidence))[:30],
        "topics": sorted(_topics(evidence))[:30],
        "stages": sorted(_detect_stages(evidence))[:30],
        "claims": list(concrete_claims(evidence))[:100],
        "quantities": quantities,
    }
    manifest["manifest_fingerprint"] = _digest_payload(manifest)
    return manifest


def _evidence_manifest(
    article: Mapping[str, object], source: Mapping[str, object] | None = None,
) -> dict[str, object]:
    raw = article.get("verified_evidence")
    if not evidence_manifest_is_valid(raw, article=article, source=source):
        return {}

    def strings(key: str) -> set[str]:
        values = raw.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return set()
        return {clean_text(value) for value in values if clean_text(value)}

    quantities: dict[str, set[str]] = {}
    raw_quantities = raw.get("quantities")
    if isinstance(raw_quantities, Mapping):
        for unit, values in raw_quantities.items():
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                continue
            clean_unit = clean_text(unit).casefold()
            if clean_unit:
                quantities[clean_unit] = {
                    clean_text(value) for value in values if clean_text(value)
                }
    return {
        "entities": strings("entities"),
        "countries": strings("countries"),
        "topics": strings("topics"),
        "stages": strings("stages"),
        "claims": strings("claims"),
        "quantities": quantities,
    }


def _signals_with_manifest_support(
    signals: Mapping[str, object], manifest: Mapping[str, object],
) -> dict[str, object]:
    """Treat facts from a valid body manifest as source-side core evidence."""
    if not manifest:
        return dict(signals)
    adjusted = deepcopy(dict(signals))

    def recompute_set(kind: str) -> None:
        source = set(adjusted.get(f"source_{kind}") or ()) | set(manifest.get(kind) or ())
        output = set(adjusted.get(f"output_{kind}") or ())
        adjusted[f"source_{kind}"] = sorted(source)
        adjusted[f"introduced_{kind}"] = sorted(output - source)
        adjusted[f"missing_{kind}"] = sorted(source - output)
        singular = {"entities": "entity", "countries": "country",
                    "topics": "topic"}[kind]
        adjusted[f"{singular}_conflict"] = bool(source and output and source.isdisjoint(output))
        adjusted[f"{singular}_replacement"] = bool((output - source) and (source - output))

    for kind in ("entities", "countries", "topics"):
        recompute_set(kind)

    source_stages = set(adjusted.get("source_stages") or ()) | set(
        manifest.get("stages") or ())
    output_stages = set(adjusted.get("output_stages") or ())
    adjusted["source_stages"] = sorted(source_stages)
    adjusted["stage_conflict"] = event_stage.stage_conflict(source_stages, output_stages)

    manifest_quantities = manifest.get("quantities") or {}
    conflicts: dict[str, dict[str, list[str]]] = {}
    for unit, details in (adjusted.get("quantity_conflicts") or {}).items():
        if not isinstance(details, Mapping):
            continue
        source_values = set(details.get("source") or ()) | set(
            manifest_quantities.get(unit) or ())
        output_values = set(details.get("output") or ())
        unsupported = {
            value for value in output_values
            if not source_values
            or not _numbers_overlap({value}, source_values, unit=unit)
        }
        if unsupported:
            conflicts[unit] = {
                "source": sorted(source_values),
                "output": sorted(output_values),
                "unsupported_output": sorted(unsupported),
                "supported_output": sorted(output_values - unsupported),
            }
    adjusted["quantity_conflicts"] = conflicts
    return adjusted


def _unsupported_claims(field_text: str, evidence_text: str,
                        manifest: Mapping[str, object] | None = None) -> list[str]:
    evidence = _normalize_claim(evidence_text)
    evidence_quantities = _quantity_map(evidence_text)
    manifest = manifest or {}
    for unit, values in (manifest.get("quantities") or {}).items():
        evidence_quantities.setdefault(str(unit), set()).update(values)
    manifest_claims = set(manifest.get("claims") or ())
    unsupported: list[str] = []
    for claim in concrete_claims(field_text):
        if claim in evidence or claim in manifest_claims:
            continue
        claim_quantities = _quantity_map(claim)
        supported_by_rounding = any(
            unit in evidence_quantities
            and _numbers_overlap(numbers, evidence_quantities[unit], unit=unit)
            for unit, numbers in claim_quantities.items()
        )
        if not supported_by_rounding:
            unsupported.append(claim)
    return unsupported


def _new_entities(field_text: str, evidence_text: str) -> set[str]:
    return set(_entities(field_text)) - set(_entities(evidence_text))


def _new_countries(field_text: str, evidence_text: str) -> set[str]:
    return set(_countries(field_text)) - set(_countries(evidence_text))


def _asserts_fact(text: str) -> bool:
    # An analytic marker at the end of a sentence must not launder an earlier
    # factual clause ("계약을 체결해 수혜 가능"). Ignore a verb only when the
    # possibility marker directly scopes that very verb ("체결할 가능성").
    for match in _FACTUAL_ASSERTION_RE.finditer(text):
        tail = text[match.end():match.end() + 24].casefold()
        if re.match(
            r"\s*(?:할|될|할\s*수|될\s*수)?\s*"
            r"(?:가능|전망|예상|잠재|검토|수\s*있|could|may|might)",
            tail,
        ):
            continue
        return True
    return False


def validate_final_card(
    card: Mapping[str, object],
    article: Mapping[str, object],
    *,
    source: Mapping[str, object] | None = None,
) -> GateResult:
    """Remove unsupported card fields; quarantine only an unsafe headline.

    Supported card keys are ``headline``, ``what``, ``why``, ``investment`` and
    ``kr_takeaway``.  A headline or ``what`` contradiction makes the card
    unusable.  Optional-field failures remove only that field.  If evidence is
    empty, no claim is removed because absence of retained evidence is not proof
    of a falsehood.
    """
    cleaned = deepcopy(dict(card))
    evidence = _card_evidence(article, source)
    if not clean_text(evidence):
        finding = Finding(
            "card_evidence_insufficient", "warning", "",
            "보존된 기사 근거가 없어 결정적 카드 검증을 생략했습니다.",
        )
        return GateResult(cleaned, "allow", (), (finding,))

    findings: list[Finding] = []
    removed: list[str] = []
    quarantine = False
    evidence_stages = _detect_stages(evidence)
    manifest = _evidence_manifest(article, source)
    evidence_stages = frozenset(set(evidence_stages) | set(manifest.get("stages") or ()))
    source_parts = _source_parts(article, source)
    core_evidence = " ".join(filter(None, source_parts.values()))

    for key in (*CORE_CARD_FIELDS, *OPTIONAL_CARD_FIELDS):
        text = clean_text(card.get(key))
        if not text:
            continue
        if key in CORE_CARD_FIELDS:
            # Headline/what must be checked against retained source material,
            # not against article.title_kr/summary (which are the same values
            # copied into the card).  Use the high-confidence mismatch rule so
            # a legitimate body-only counterparty does not become a false block.
            if core_evidence:
                signals = _mismatch_signals(core_evidence, text)
                signals = _signals_with_manifest_support(signals, manifest)
                if _gross_mismatch(
                    signals,
                    quantity_is_hard=(key == "headline" or bool(
                        source_parts["description"] or source_parts["article_text"])),
                    directional_is_hard=bool(
                        source_parts["description"] or source_parts["article_text"]),
                ):
                    quarantine = True
                    code = ("card_headline_unsupported" if key == "headline"
                            else "card_what_unsupported")
                    if key == "what":
                        cleaned[key] = None
                        removed.append(key)
                    findings.append(Finding(
                        code, "quarantine", key,
                        "기사 원문 근거와 충돌하는 핵심 사실이라 카드 전체를 격리해야 합니다.",
                        signals,
                    ))
            continue
        unsupported = _unsupported_claims(text, evidence, manifest)
        introduced_entities = _new_entities(text, evidence) - set(
            manifest.get("entities") or ())
        introduced_countries = _new_countries(text, evidence) - set(
            manifest.get("countries") or ())
        field_stages = _detect_stages(text)
        unsupported_stages = set(field_stages) - set(evidence_stages) if evidence_stages else set()

        # An analysis field may naturally mention KHNP as the perspective owner.
        # That is allowed unless it asserts KHNP actually signed/supplied/etc.
        if key == "kr_takeaway" and "khnp" in introduced_entities and not _asserts_fact(text):
            introduced_entities.remove("khnp")

        problems: dict[str, object] = {}
        if unsupported:
            problems["unsupported_claims"] = unsupported
        # A named company/plant is itself concrete even in an analysis sentence;
        # do not let enrichment invent a beneficiary.  Countries/stages are
        # stricter only in core fields or factual assertions because comparative
        # analysis may legitimately mention a market/geography as a scenario.
        check_identifiers = key in CORE_CARD_FIELDS or _asserts_fact(text)
        if introduced_entities:
            problems["introduced_entities"] = sorted(introduced_entities)
        if check_identifiers and introduced_countries:
            problems["introduced_countries"] = sorted(introduced_countries)
        if check_identifiers and unsupported_stages:
            problems["unsupported_stages"] = sorted(unsupported_stages)
        if not problems:
            continue

        cleaned[key] = None
        removed.append(key)
        severity = "sanitize"
        code = "card_field_unsupported"
        findings.append(Finding(
            code, severity, key,
            "기사 근거에서 확인되지 않는 구체적 주장이라 카드 필드를 제외했습니다.",
            problems,
        ))

    action = "quarantine" if quarantine else ("sanitize" if removed else "allow")
    return GateResult(cleaned, action, tuple(dict.fromkeys(removed)), tuple(findings))


def sanitize_curation_optional_fields(
    curation: Mapping[str, object],
    *,
    article: Mapping[str, object],
    source: Mapping[str, object] | None = None,
) -> GateResult:
    """Apply final-card evidence rules before curation reaches cache/archive.

    These fields later seed the site, expert audio and card enrichment. Clearing
    an unsupported claim only at Telegram send time leaves the unsafe original
    available to those other consumers, so collection persists the sanitized
    curation itself.
    """
    cleaned = deepcopy(dict(curation))
    findings: list[Finding] = []
    removed: list[str] = []
    field_slots = {
        "detail": "why",
        "why_important": "why",
        # item_to_card consumes implication as the KHNP takeaway, including its
        # narrow non-factual KHNP perspective exception.
        "implication": "kr_takeaway",
        # watch_next is not a KHNP-perspective field; validate it as a normal
        # explanatory assertion instead of inheriting that exception.
        "watch_next": "why",
    }
    for field, slot in field_slots.items():
        value = clean_text(cleaned.get(field))
        if not value:
            continue
        evidence_article = dict(article)
        evidence_article.update(cleaned)
        result = validate_final_card({slot: value}, evidence_article, source=source)
        if slot not in result.removed_fields:
            continue
        cleaned[field] = ""
        removed.append(field)
        for finding in result.findings:
            findings.append(Finding(
                "curation_field_unsupported", "sanitize", field,
                finding.message, {"card_slot": slot, **dict(finding.details)},
            ))
    return GateResult(
        cleaned,
        "sanitize" if removed else "allow",
        tuple(dict.fromkeys(removed)),
        tuple(findings),
    )


def summarize_findings(results: Sequence[GateResult]) -> dict:
    """Small aggregation helper for delivery logs/quality alerts."""
    summary = {"checked": len(results), "allowed": 0, "sanitized": 0, "quarantined": 0,
               "removed_fields": {}, "codes": {}}
    for result in results:
        key = {"allow": "allowed", "sanitize": "sanitized", "quarantine": "quarantined"}[result.action]
        summary[key] += 1
        for removed in result.removed_fields:
            summary["removed_fields"][removed] = summary["removed_fields"].get(removed, 0) + 1
        for finding in result.findings:
            summary["codes"][finding.code] = summary["codes"].get(finding.code, 0) + 1
    return summary
