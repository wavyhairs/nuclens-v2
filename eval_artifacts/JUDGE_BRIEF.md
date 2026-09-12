# 독립 판정 지침 (blind)

이 지침은 **생산 모델의 프롬프트가 아니다.** 같은 문장을 다시 주면 같은 규칙을 다시
실행할 뿐이고, 그렇게 얻은 일치율은 독립성의 증거가 아니라 복사의 증거다. 그래서
여기서는 관계를 **기술적으로** 묻는다.

## 무엇을 받는가

`eval_artifacts/shards/blind_NN.jsonl` 한 줄이 한 건이다.

```
{"candidate_id": "...", "stratum": "...",
 "a": {"title","date","publisher","summary","topics","countries","facts"},
 "b": { ... }}
```

`facts` 는 기사에서 뽑아 둔 구조화 사실이다 — `actors`(주체) · `assets`(설비·프로젝트)
· `event_family`(사건군) · `action`(행동·단계) · `drivers`(원인) · `countries`.

여기에 없는 것은 보지 않는다. 특히 **두 기사가 실제로 같은 묶음에 들어갔는지, 어떤
모델이 무엇이라 판정했는지는 주어지지 않는다.** 추측해서 맞히려 하지 말 것 —
재는 것은 정답 맞히기가 아니라 두 판정자가 독립적으로 어디서 갈리는가다.

## 무엇을 답하는가

한 줄에 하나, JSON 한 줄씩.

```
{"candidate_id": "...",
 "relation": "SAME_EVENT" | "FOLLOW_UP_NEW_ACTION" | "FOLLOW_UP_NO_NEW_ACTION"
             | "RELATED_DISTINCT_EVENT" | "UNRELATED" | "INSUFFICIENT",
 "same_thread": true | false | null,
 "confidence": 0.0~1.0,
 "difference_axis": "<아래 목록 중 하나>",
 "decisive_evidence": "판정을 가른 사실 한 조각",
 "reason": "40자 이내"}
```

### relation — 두 기사가 말하는 **사건**의 관계

| 값 | 뜻 |
|---|---|
| `SAME_EVENT` | 하나의 사건이다. 같은 발표·같은 회의·같은 사고를 두 매체가 쓴 것 포함 |
| `FOLLOW_UP_NO_NEW_ACTION` | 같은 사건의 후속인데 **새로 벌어진 일이 없다**. 배경·해설·반응·수치 보강 |
| `FOLLOW_UP_NEW_ACTION` | 같은 사안이 **한 단계 나아갔다**. 신청 → 심사 착수, 협상 → 체결 |
| `RELATED_DISTINCT_EVENT` | 이어져 있지만 별개 사건이다. 같은 대상의 다른 안건, 같은 절차의 다른 호기 |
| `UNRELATED` | 분야·주제만 같다 |
| `INSUFFICIENT` | 주어진 자료로는 가를 수 없다 |

`FOLLOW_UP_NEW_ACTION` 이 핵심 축이다. "새 행동이 있었나"가 사건과 이야기를 가른다.

### same_thread — 두 사건이 **하나의 장기 이야기**인가

`relation` 과 **다른 질문이다.** 서로 다른 사건이어도 같은 이야기일 수 있다.

* 같은 대상(특정 호기·특정 프로젝트·특정 법안)의 한 절차가 단계를 밟아 간다 → `true`
* 같은 대상이지만 다른 사안이다(계속운전 심사 ↔ 계획예방정비) → `false`
* 다른 호기·다른 부지·다른 나라 사업 → `false`
* 주제만 같다 → `false`
* 가를 수 없다 → `null`

`SAME_EVENT` 이면 `same_thread` 는 당연히 `true` 다.

### difference_axis

`different_unit` · `different_project` · `different_occurrence` · `different_year` ·
`different_policy_action` · `different_stage` · `same_entity_different_issue` ·
`multi_source_same_event` · `follow_up_same_matter` · `topic_only` ·
`insufficient_evidence` · `other`

## 판정 원칙

1. **제목이 닮았다는 것은 근거가 아니다.** 무엇이 누구에게 언제 벌어졌는지를 본다.
2. **호기·차수·연도를 먼저 확인한다.** 고리 2호기와 고리 3호기는 절차가 같아도 다르다.
3. 한쪽이 특정 사건이고 다른 쪽이 업계 전반의 전망·의견이면 `UNRELATED` 다.
4. 가를 수 없으면 `INSUFFICIENT` 를 쓴다. 억지로 고르면 일치율이 올라가고 그 숫자는
   아무 말도 하지 않게 된다.
5. 순서대로 읽되 **앞의 판정을 뒤의 기준으로 삼지 않는다.** 매 건을 독립으로 본다.
