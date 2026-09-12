# 스토리 연결 독립 판정 지침 (blind)

단위가 기사가 아니라 **사건(event)** 이다. 두 사건이 **하나의 장기 이야기**인지만 묻는다.
두 사건이 서로 다른 사건인 것은 전제다.

## 입력

`eval_artifacts/thread_shards/blind_NN.jsonl` 한 줄이 한 건이다.

```
{"pair_id":"...", "stratum":"...",
 "a": {"title","summary","first_seen","last_seen","briefing_count","units","entity_ids","countries","facts"},
 "b": { ... }}
```

`units` 는 호기 토큰이다(`kori-2` = 고리 2호기 = Kori Unit 2). `facts` 는 구조화 사실이다.

## 출력

`eval_artifacts/thread_shards/judg_NN.jsonl` 에 한 줄 JSON.

```
{"pair_id":"...","verdict":"same_thread|different_thread|uncertain",
 "relationship":"stage_progress|cause_effect|same_matter|",
 "confidence":0.0~1.0,"reason":"40자 이내"}
```

## 같은 스토리다 (same_thread)

- 같은 대상(특정 호기·특정 프로젝트·특정 법안/계획)의 한 절차가 단계를 밟는다.
  신청 → 심사 착수 → 의견수렴 → 심의 → 결정
- 같은 계약·사업의 협상 → 체결 → 착공 → 준공
- 같은 사고·고장의 발생 → 원인조사 → 복구 → 재발방지
- 한쪽이 다른 쪽의 직접적인 원인이거나 결과다

## 다른 스토리다 (different_thread)

- **대상이 다르다.** 다른 호기·다른 부지·다른 나라 사업이면 절차가 같아도 다르다
- **같은 대상이지만 다른 사안이다.** 계속운전 심사 ↔ 계획예방정비,
  계속운전 심사 ↔ 부지 내 저장시설 반대
- 같은 정책 이름이지만 다른 연도·다른 차수다
- 같은 정비지만 다른 회차다
- **주제만 같다.** 둘 다 SMR, 둘 다 계속운전 일반론, 둘 다 업계 전망
- 한쪽이 특정 사안이고 다른 쪽은 업계 전반의 동향·전망·의견이다

## 원칙

1. 회사 이름이 같다는 것은 근거가 아니다. 같은 회사의 다른 사업은 다른 이야기다.
2. 호기 토큰을 먼저 본다. `hanbit-1` 과 `kori-3` 은 절차가 같아도 다른 이야기다.
3. 가를 수 없으면 `uncertain`. 억지로 이으면 서로 다른 사안이 한 이야기로 연쇄
   병합되고, 그 이야기는 아무 말도 하지 않게 된다.
4. 매 건을 독립으로 본다. 앞의 판정을 뒤의 기준으로 삼지 않는다.
