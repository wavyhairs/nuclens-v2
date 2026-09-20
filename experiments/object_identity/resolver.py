"""Event resolver — (object_ref, transition, date_bucket) 키로 기사를 Event 에 배정한다.

결정 계층 (architecture review 2026-09-20 §10 그대로)

    Article
    ↓ 0. 거부권 (결정적)   호기 모순 · 국가 모순 · 사람 판정(rejected) · 단계 모순(양쪽 다 알 때만)
    ↓ 1. Object 해석       objects.extract → key. 없으면 None, 추측 금지
    ↓ 2. Transition · 날짜  event_stage 제목 단계(없으면 큐레이션 게이트의 stages) · 게이트 통과 event_date 또는 발행일
    ↓ 3. 원장 조회 (무창)   같은 (object, transition, bucket) 의 Event 가 있나
         있음               → 그 Event 의 Evidence
         없음, object 같음   → 새 Event (같은 Object 위의 다른 단계 = material 보존)
         object 없음         → 현재 경로(production 의 묶음) 로 후퇴. grade=lexical. 상속·장기 결합 금지

grade 는 넷이다.

    object    키 셋이 다 알려졌고 키로 붙었거나 키로 갈렸다
    partial   Object 는 알지만 transition 을 한쪽이라도 모른다 → 키만으로 붙이지 않고 production 이
              같은 묶음이라 했거나 제목이 강하게 닮았을 때만 붙는다 ("모름은 다름이 아니다")
    entity    발전소만 알고 호기를 모른다 → partial 과 같은 규칙, 결합 범위는 같은 발전소 안
    lexical   Object 없음 → production 이 준 묶음을 그대로 따른다

이 모듈은 순수하다. 입력은 전부 호출자가 준다. LLM · 네트워크 · 파일 없음.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import event_stage
import issue_continuity

from experiments.object_identity import objects as objmod

RESOLVER_VERSION = "object-event-identity-exp-1"

# 같은 Object · 같은 transition 이라도 이만큼 떨어지면 다른 사건이다(두 번째 정지, 두 번째 심의).
# 코퍼스가 10주라 이 값이 결론을 흔들지 않는지 replay 가 14/30/60 으로 민감도를 잰다.
DEFAULT_BUCKET_DAYS = 30

# partial/entity grade 가 키 없이 붙을 때 요구하는 제목 유사도. production 의
# TITLE_TAGS_MATCH_RATIO(0.55) 와 같은 높이 — 여기서 새 문턱을 발명하지 않는다.
TITLE_JOIN_RATIO = 0.55

# 나라를 특정하지 못하는 범위 값. build_data.NON_COUNTRY_SCOPES 와 같다.
NON_COUNTRY_SCOPES = frozenset({"OTHER", "UNSPECIFIED", "GLOBAL", "EUROPE", "EU"})


def _day(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class Item:
    """resolver 가 보는 기사 한 건. replay 가 아카이브 레코드에서 만든다."""
    hash: str
    pub_date: str                       # 발행일 (YYYY-MM-DD)
    event_date: str = ""                # 큐레이션 게이트를 지난 사건일. 없으면 ""
    title_kr: str = ""
    title: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    gate_stages: frozenset[str] = frozenset()   # verified_evidence.stages
    production_issue: str = ""          # production 이 이 기사를 넣은 묶음(원장 소유권)
    signals: objmod.ObjectSignals = field(default_factory=objmod.ObjectSignals)

    @property
    def effective_date(self) -> str:
        """버킷·정렬에 쓰는 날짜. **발행일이다.**

        게이트를 지난 `event_date` 를 먼저 써 봤더니 `2028-01-01`(계획 목표연도) ·
        `2026-12-01`(예정된 발표) 같은 값이 들어와 8월 기사가 12월 자리에 섰다. 사건일은
        타임라인 노드의 날짜지 신원 버킷의 날짜가 아니다 — 여기서는 주석으로만 남긴다.
        """
        return self.pub_date

    @property
    def date_kind(self) -> str:
        return "published_at"

    def stages(self, *, title_only: bool = False) -> frozenset[str]:
        """제목이 말한 단계. 제목이 침묵하면 게이트가 제목+요약에서 뽑은 단계로 후퇴한다.

        후퇴 방향은 안전하다 — 집합이 커지면 교집합이 생겨 '다르다'가 덜 나온다.
        `title_only` 면 후퇴하지 않는다(요약의 배경 단계가 키를 뭉개는지 재는 arm).
        """
        titled = event_stage.detect_stages(self.title_kr, self.title)
        if title_only:
            return titled
        return titled or self.gate_stages

    def as_row(self) -> dict:
        return {"title_kr": self.title_kr, "title": self.title, "summary": self.summary}


@dataclass
class Event:
    event_id: str
    object_key: tuple[str, frozenset[str]] | None
    grade: str
    members: list[Item] = field(default_factory=list)
    stages: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    production_issues: set[str] = field(default_factory=set)
    first_date: str = ""
    last_date: str = ""
    decisions: list[dict] = field(default_factory=list)

    def add(self, item: Item, *, reason: str, grade: str, stages: frozenset[str] = frozenset()) -> None:
        self.members.append(item)
        self.stages |= set(stages)
        self.countries |= set(item.countries) - NON_COUNTRY_SCOPES
        if item.production_issue:
            self.production_issues.add(item.production_issue)
        day = item.effective_date
        self.first_date = min(self.first_date or day, day)
        self.last_date = max(self.last_date or day, day)
        self.decisions.append({"hash": item.hash, "reason": reason, "grade": grade})


@dataclass
class Resolution:
    events: list[Event]
    assignment: dict[str, str]          # hash → event_id
    grade_of: dict[str, str]            # hash → grade
    reason_of: dict[str, str]           # hash → 배정 이유
    stats: dict


class Resolver:
    def __init__(self, *, bucket_days: int = DEFAULT_BUCKET_DAYS, use_lexicon: bool = False,
                 rejected_pairs: set[frozenset[str]] | None = None,
                 approved_pairs: set[frozenset[str]] | None = None,
                 process_hint: bool = False, strict_transition: bool = False,
                 title_only_stages: bool = False, production_bridge: bool = True):
        self.bucket_days = bucket_days
        self.use_lexicon = use_lexicon
        self.rejected = rejected_pairs or set()
        self.approved = approved_pairs or set()
        # 같은 Object · 같은 단계라도 topics 가 하나도 안 겹치면 다른 절차로 본다(선택).
        self.process_hint = process_hint
        # transition 을 집합의 교집합(느슨)으로 볼지, 집합이 같아야(엄격) 같은 키로 볼지.
        self.strict_transition = strict_transition
        self.title_only_stages = title_only_stages
        # Object 를 아는 기사가 키로 못 붙었을 때 production 묶음을 다리로 삼아 붙일 수 있는가.
        #   True      항상 (가장 관대한 후퇴 — 키 매치와 연쇄해 눈덩이가 되는지 본다)
        #   "partial" 키 셋 중 하나를 **모를 때만** (리뷰 §10 의 명세: 모르면 현재 경로, 알면 키 아니면 새 Event)
        #   False     없음 (Object 를 아는 기사는 키 아니면 새 Event)
        self.production_bridge = production_bridge
        self.events: list[Event] = []
        self._by_object: dict[tuple[str, frozenset[str]], list[Event]] = {}
        self._by_production: dict[str, list[Event]] = {}
        self._minted = 0

    def _bridge_allowed(self, grade: str, stages: frozenset[str], event: Event) -> bool:
        if self.production_bridge is True:
            return True
        if self.production_bridge == "partial":
            full_key = grade == "object" and bool(stages) and bool(event.stages)
            return not full_key
        return False

    # ---- 계층 0: 거부권 --------------------------------------------------------------

    def _vetoed(self, item: Item, event: Event) -> str:
        """비어 있으면 통과. 아니면 어느 거부권인지."""
        for member in event.members:
            if frozenset((item.hash, member.hash)) in self.rejected:
                return "human_rejected"
            if objmod.unit_conflict(item.signals, member.signals):
                return "unit_conflict"
        mine = set(item.countries) - NON_COUNTRY_SCOPES
        if mine and event.countries and mine.isdisjoint(event.countries):
            # 두 나라를 함께 명시한 멤버(브리지)가 있으면 국경을 넘는 하나의 사건이다.
            bridged = any(
                (set(m.countries) & mine) and (set(m.countries) & event.countries)
                for m in event.members
            )
            if not bridged:
                return "country_conflict"
        stages = self._stages(item)
        if stages and event.stages and event_stage.stage_conflict(stages, event.stages):
            return "stage_conflict"
        return ""

    def _stages(self, item: Item) -> frozenset[str]:
        return item.stages(title_only=self.title_only_stages)

    def _same_transition(self, stages: frozenset[str], event: Event) -> bool:
        if not stages or not event.stages:
            return False
        if self.strict_transition:
            return set(stages) == event.stages
        return bool(set(stages) & event.stages)

    # ---- 계층 3: 조회 ----------------------------------------------------------------

    def _within_bucket(self, item: Item, event: Event) -> bool:
        day = _day(item.effective_date)
        first, last = _day(event.first_date), _day(event.last_date)
        if not day or not first or not last:
            return False
        return (day - last).days <= self.bucket_days and (first - day).days <= self.bucket_days

    def _process_ok(self, item: Item, event: Event) -> bool:
        if not self.process_hint:
            return True
        mine = set(item.topics)
        if not mine:
            return True
        theirs = {t for m in event.members for t in m.topics}
        return not theirs or bool(mine & theirs)

    def _title_join(self, item: Item, event: Event) -> bool:
        row = item.as_row()
        return any(
            issue_continuity.title_similarity(row, m.as_row()) >= TITLE_JOIN_RATIO
            for m in event.members[-5:]
        )

    def _production_join(self, item: Item, event: Event) -> bool:
        return bool(item.production_issue) and item.production_issue in event.production_issues

    def _approved_join(self, item: Item, event: Event) -> bool:
        return any(frozenset((item.hash, m.hash)) in self.approved for m in event.members)

    def _new_event(self, item: Item, key, grade: str, reason: str) -> Event:
        self._minted += 1
        event = Event(event_id=f"evt-{item.hash}", object_key=key, grade=grade)
        event.add(item, reason=reason, grade=grade, stages=self._stages(item))
        self.events.append(event)
        if key is not None:
            self._by_object.setdefault(key, []).append(event)
        if item.production_issue:
            self._by_production.setdefault(item.production_issue, []).append(event)
        return event

    def _attach(self, item: Item, event: Event, *, reason: str, grade: str) -> Event:
        event.add(item, reason=reason, grade=grade, stages=self._stages(item))
        if item.production_issue:
            bucket = self._by_production.setdefault(item.production_issue, [])
            if event not in bucket:
                bucket.append(event)
        if event.object_key is None and item.signals.key(use_lexicon=self.use_lexicon) is not None:
            # lexical 로 세운 Event 에 Object 있는 기사가 production 경로로 들어오면 Object 를 얻는다.
            event.object_key = item.signals.key(use_lexicon=self.use_lexicon)
            event.grade = grade
            self._by_object.setdefault(event.object_key, []).append(event)
        return event

    def _same_object_events(self, key) -> list[Event]:
        out: list[Event] = []
        for other_key, events in self._by_object.items():
            if objmod.same_object(key, other_key):
                out.extend(e for e in events if e not in out)
        return out

    # ---- 본체 ------------------------------------------------------------------------

    def resolve_one(self, item: Item) -> tuple[Event, str, str]:
        key = item.signals.key(use_lexicon=self.use_lexicon)
        stages = self._stages(item)

        # ---- Object 없음 → 현재 경로 ----
        if key is None:
            for event in reversed(self._by_production.get(item.production_issue, [])):
                if self._vetoed(item, event):
                    continue
                return self._attach(item, event, reason="production_issue", grade="lexical"), "lexical", "production_issue"
            return self._new_event(item, None, "lexical", "new:no_object"), "lexical", "new:no_object"

        grade = "entity" if key[0] == "plant" else "object"
        candidates = [e for e in self._same_object_events(key) if self._within_bucket(item, e)]
        candidates.sort(key=lambda e: (e.last_date, e.event_id), reverse=True)

        def stage_split(event: Event) -> bool:
            """알려진 단계가 서로 다르다 — 같은 Object 위의 다른 Event."""
            if not stages or not event.stages:
                return False
            if self.strict_transition:
                return set(stages) != event.stages
            return not (set(stages) & event.stages)

        # ---- 키 셋이 다 알려진 경우: 결정적 조회 ----
        if grade == "object" and stages:
            for event in candidates:
                if not self._same_transition(stages, event):
                    continue                      # Event 쪽 단계를 모르거나 단계가 다르다
                if not self._process_ok(item, event):
                    continue
                if self._vetoed(item, event):
                    continue
                return self._attach(item, event, reason="key_match", grade="object"), "object", "key_match"

        # ---- 키로 못 붙였다. 모르는 것은 다른 것이 아니다 → 약한 결합만 허용 ----
        for event in candidates:
            if self._vetoed(item, event) or stage_split(event):
                continue
            if self._approved_join(item, event):
                return self._attach(item, event, reason="human_approved", grade=grade), grade, "human_approved"
            if self._bridge_allowed(grade, stages, event) and self._production_join(item, event):
                return self._attach(item, event, reason="production_bridge", grade=grade), grade, "production_bridge"
            if self._title_join(item, event):
                return self._attach(item, event, reason="title_join", grade=grade), grade, "title_join"

        # production 이 이 기사를 넣은 묶음이 Object 없는 Event 로 이미 서 있으면 거기 붙는다
        # (현재 경로). Object 가 서로 어긋나는 Event 에는 붙지 않는다 — 그것이 이 가설의 전부다.
        for event in reversed(self._by_production.get(item.production_issue, [])):
            if not self._bridge_allowed(grade, stages, event):
                continue
            if event.object_key is not None and not objmod.same_object(key, event.object_key):
                continue
            if self._vetoed(item, event) or stage_split(event):
                continue
            return self._attach(item, event, reason="production_issue", grade=grade), grade, "production_issue"

        same_object_known = any(stage_split(e) for e in candidates)
        reason = "new:stage_split" if same_object_known else "new:no_candidate"
        return self._new_event(item, key, grade, reason), grade, reason

    def resolve(self, items: list[Item]) -> Resolution:
        ordered = sorted(items, key=lambda i: (i.effective_date, i.pub_date, i.hash))
        assignment: dict[str, str] = {}
        grade_of: dict[str, str] = {}
        reason_of: dict[str, str] = {}
        for item in ordered:
            event, grade, reason = self.resolve_one(item)
            assignment[item.hash] = event.event_id
            grade_of[item.hash] = grade
            reason_of[item.hash] = reason
        stats = {
            "version": RESOLVER_VERSION,
            "bucket_days": self.bucket_days,
            "use_lexicon": self.use_lexicon,
            "process_hint": self.process_hint,
            "strict_transition": self.strict_transition,
            "title_only_stages": self.title_only_stages,
            "production_bridge": self.production_bridge,
            "items": len(items),
            "events": len(self.events),
            "grades": _count(grade_of.values()),
            "reasons": _count(reason_of.values()),
        }
        return Resolution(self.events, assignment, grade_of, reason_of, stats)


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))
