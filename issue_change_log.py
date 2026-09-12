"""이슈가 회차를 넘기며 **실제로 움직인 자리**만 고른다 → issue.change_log.

왜 필요한가
-----------
`issue_continuity.progression()` 은 발송 **전에** "어제 대비 단계가 넘어갔는가"를
이미 판정하고 있다. 그 답(material·minor·none)이 `delivery_log.jsonl` 의 기사
레코드마다 `continuity` 로 적혀 있다. 2026-08-18 부터 26일치 78건 —

    material 13 · minor 55 · none 10

그런데 웹이 그 키를 한 번도 읽지 않았다. `build_data` 의 멤버 조립부는
delivery_log 의 story 계약(story_id·fingerprint·members …)을 한 줄씩 옮겨 싣는데
`continuity` 만 빠져 있었다. 그래서 이슈 상세의 타임라인은 기사를 날짜순으로
세우기만 할 뿐, **그중 어디가 단계가 넘어간 자리인지** 말하지 못했다.

여기서 LLM 을 새로 부르지 않는다. 판정은 이미 끝나 있고, 없던 것은 배선뿐이다.

무엇을 싣지 않는가 — 게이트가 이 파일의 전부다
-----------------------------------------------
`issue_continuity.same_issue` 는 **감점용**이라 일부러 넓게 잡는다(제목 문턱
0.62, 앵커 단독 매칭 허용). 놓치면 예전과 같아질 뿐이고 잘못 걸어도 점수가
깎일 뿐이라는 비대칭 위에 서 있다. 화면은 그 비대칭을 물려받지 않는다 —
거기서는 잘못 걸린 매칭이 **없는 연결을 주장하는 문장**이 된다.

실측(2026-09-12, delivery_log 801건 · issue_ledger 의 실제 hash→issue_id):

    material 13건 중 같은 이슈 안은 4건.  나머지 9건은 다른 이슈를 가리켰다.
      8/23 테라파워 2호 SMR      ← 현대건설 원전 슈퍼사이클   sim 0.157
      8/25 미 해사청 SMR MOU     ← 한수원 체코 두코바니       sim 0.036
      8/24 NRC 자이언 해체 점검  ← NRC 오라노 농축 인허가     sim 0.246

그래서 **prior 기사가 같은 이슈 클러스터의 멤버일 때만** 싣는다. 이 모듈은
연결을 만들지 않는다 — 클러스터링이 이미 만들어 둔 연결에 "그 회차에 단계가
움직였다"는 판정만 붙인다. `issue_id` 결정에 개입하지 않으므로 `issue_ledger`
의 이동 판정·영구 주소에 영향이 없다(2026-09-12 PR #103).

같은 이유로 `progression_detail` 은 **화면 문장으로 나가지 않는다.** 그것은
`event_stage` 의 어휘 라벨이라 사건 설명이 아니다:

    8/26 「정부, 호남 반도체 산단 전력·용수 인프라 예타 면제」
         → progression_detail = "정지·가동중단"

'예비타당성'이 `permit:심사` 칸에 있어 척도가 흔들린 것이고, 그 라벨을 제목
옆에 세우면 예타 면제 기사가 가동중단 기사가 된다. 판정 당시 입력에는 기사
본문 요지(`detail`)가 있었는데 delivery_log 에는 그 필드가 없어 재현조차 안
된다 — 지금 같은 텍스트로 다시 돌리면 다른 답이 나온다. 그래서 등급(두 칸)과
사실(날짜·제목)만 내보내고, 진단값은 페이로드 안에 감사용으로만 남긴다.

`none` 은 싣지 않는다. 게이트가 "아무것도 안 움직였다"고 말한 회차이고,
변화 이력에 실을 것이 없다는 뜻이다. 전수 기록은 delivery_log 에 그대로 있다.

`minor` 는 싣는다 — 다만 등급을 감추지 않는다. minor 는 "진전이 있었는지 확인이
안 된다"는 뜻이라(`progression()` 의 ④~⑥ 유보) 단계 이동과 같은 칸에 세우면
안 된다. 실측 새울 1호기 세 회차는 같은 재가동 승인을 매체 셋이 다르게 쓴 것이고,
게이트도 그렇게 판정했다. 화면이 그 셋을 '후속 보도'라 부르는 것은 사실이지만
'단계 이동'이라 부르는 것은 거짓이다 — 그래서 라벨이 둘이다.

여기서 유사도 문턱을 새로 세우지 않는다(예: '제목이 0.8 넘게 닮으면 빼자').
"같은 이야기인가"의 판정은 `issue_continuity` 하나가 갖는다. 웹이 제 문턱을
하나 더 들면 두 곳이 금방 어긋난다 — `story_fingerprint` 가 정확히 그래서
생겼다(이 파일과 build_data 가 축 표를 하나씩 들고 있다가 갈라졌다).

가드레일
--------
* stdlib 만. LLM·네트워크·파일 읽기 없음 — 입력은 전부 호출자가 준다.
* 이슈당 상한을 둔다(`MAX_ENTRIES`). issues.json 은 첫 화면에서 통째로 받는다.
"""

from __future__ import annotations

# 이슈 하나가 들고 갈 변화 이력의 상한. 실측 최대가 3건이라 닿는 일이 없지만,
# 이 목록은 이슈가 오래 살수록 길어지는 쪽이라 상한 없는 칸을 두지 않는다.
MAX_ENTRIES = 12

# 화면에 실을 등급. `none` 은 여기 없다(위 docstring).
SHOWN_VERDICTS = ("material", "minor")


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def prior_hash_index(deliveries: dict[str, dict]) -> dict[tuple[str, str], str]:
    """(발송일, 제목) → 기사 hash. **옛 레코드 복원 전용**이다.

    `continuity.prior_hash` 는 2026-09-12 부터 기록된다(daily_brief). 그 전
    26일치 78건에는 `prior_title`·`prior_date` 뿐이라, 그것만으로 직전 발송분을
    되찾아야 한다. 실측 801건에서 (발송일, 제목) 쌍의 충돌이 0건이고 78건 전부
    유일하게 풀린다.

    새 레코드에는 이 인덱스를 쓰지 않는다 — 제목은 표시용 문자열이지 신원이
    아니고, 이 저장소는 이슈 이동 판정에서 같은 결론에 이미 닿았다
    (`issue_ledger`: "판정 재료는 기사 해시뿐이다"). 그래서 이 함수는 시간이
    지나면 아무 일도 하지 않게 되는 것이 정상이다.
    """
    index: dict[tuple[str, str], str] = {}
    collisions: set[tuple[str, str]] = set()
    for article_hash, delivery in deliveries.items():
        key = (_clean(delivery.get("date")), _clean(delivery.get("title_kr")))
        if not key[0] or not key[1]:
            continue
        if key in index:
            # 같은 날 같은 제목이 둘이면 어느 쪽인지 말할 수 없다. 둘 다 버린다 —
            # 반쯤 맞는 연결은 안 잇는 것보다 나쁘다.
            collisions.add(key)
            continue
        index[key] = _clean(article_hash)
    for key in collisions:
        index.pop(key, None)
    return index


def resolve_prior_hash(continuity: dict, index: dict[tuple[str, str], str]) -> str:
    """이 판정이 가리키는 직전 발송분의 hash. 못 찾으면 빈 문자열."""
    if not isinstance(continuity, dict):
        return ""
    direct = _clean(continuity.get("prior_hash"))
    if direct:
        return direct
    return index.get((_clean(continuity.get("prior_date")),
                      _clean(continuity.get("prior_title"))), "")


def _entry(member: dict, continuity: dict, prior_hash: str, prior: dict | None) -> dict:
    # 제목은 **지금 카탈로그가 들고 있는 것**을 쓴다. delivery_log 의 prior_title 은
    # 그 회차의 제목이고, 이슈 원장은 같은 기간 살아남은 이슈의 18.8% 가 제목을
    # 바꿨다고 기록한다(issue_ledger). 화면 두 곳이 같은 기사를 다른 이름으로
    # 부르면 그것대로 사고라, 클러스터 멤버가 있으면 그쪽이 이긴다.
    prior_title = _clean((prior or {}).get("title_kr")) or _clean(continuity.get("prior_title"))
    return {
        # 회차(brief) 와 기사일(article) 을 **둘 다** 싣는다. 판정은 발송과 발송
        # 사이에서 내려지므로 회차가 판정의 단위이고, 화면 아래 타임라인은 기사일로
        # 선다. 한 쪽만 실으면 두 블록이 같은 사건을 하루 어긋나게 말한다
        # (실측 그라블린: 회차 8/28 · 기사일 8/27).
        "date": _clean(member.get("briefing_date")),
        "article_date": _clean(member.get("article_date")),
        "hash": _clean(member.get("hash")),
        "title": _clean(member.get("title_kr")),
        "prior_date": _clean(continuity.get("prior_date")),
        "prior_article_date": _clean((prior or {}).get("article_date")),
        "prior_hash": prior_hash,
        "prior_title": prior_title,
        "days": int(continuity.get("days_ago") or 0),
        "kind": _clean(continuity.get("progression")),
        # 아래 셋은 감사용이다 — 화면 문장으로 쓰지 않는다(docstring).
        "reason": _clean(continuity.get("progression_kind")),
        "similarity": float(continuity.get("similarity") or 0.0),
        "identity_confirmed": bool(continuity.get("identity_confirmed")),
    }


def build(members: list[dict], index: dict[tuple[str, str], str] | None = None) -> list[dict]:
    """이슈 멤버들 → 변화 이력(최신순). 같은 이슈 안에서 확인된 진전만 남는다.

    Args:
        members: 한 이슈의 기사 멤버. `continuity` 는 발송된 기사에만 붙는다
            (근거로 뒤에 매칭된 미발송 보도는 delivery_log 에 줄이 없다).
        index: 옛 레코드용 (발송일, 제목) → hash 인덱스. `prior_hash_index()`.
    """
    index = index or {}
    by_hash = {_clean(member.get("hash")): member for member in members if member.get("hash")}
    entries: list[dict] = []
    for member in members:
        continuity = member.get("continuity")
        if not isinstance(continuity, dict):
            continue
        if _clean(continuity.get("progression")) not in SHOWN_VERDICTS:
            continue
        prior_hash = resolve_prior_hash(continuity, index)
        # 게이트. 직전 발송분이 이 이슈의 멤버가 아니면 싣지 않는다 — 연결을
        # 만드는 것은 클러스터링의 몫이고, 여기는 그 연결에 판정만 붙인다.
        if not prior_hash or prior_hash not in by_hash:
            continue
        if prior_hash == _clean(member.get("hash")):
            continue
        entries.append(_entry(member, continuity, prior_hash, by_hash[prior_hash]))
    # 회차가 같으면 기사 날짜, 그것도 같으면 hash — 빌드마다 순서가 흔들리지
    # 않아야 issues.json 의 diff 가 뜻을 갖는다.
    entries.sort(key=lambda row: (row["date"], row["prior_date"], row["hash"]), reverse=True)
    return entries[:MAX_ENTRIES]
