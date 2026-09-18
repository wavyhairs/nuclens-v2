# Gemini reasoning P4 source-complete evidence producer

작성일: 2026-09-19

상태: producer 구현 완료, 실제 eligible 0건, P4 `HALTED` 유지

## 1. 범위

PR #134의 `source_complete_evidence.py`는 완성된 payload를 검증·저장하지만 실제
curation 실행에서 payload를 만드는 caller가 없었다. 이번 producer는 동일한
production-equivalent `curate_batch()` 실행에서 다음 두 관측점을 in-memory로 결합한다.

- `gemini_client.call_json`: API 직전 serialized request, provider response, raw model text,
  parsed output, direct/salvage parser 경로, retry detail
- `news_bot.curate_batch`: exact article object, reports/context, batch 순서/position,
  body, normalized output, validation, regeneration/split lifecycle

기본 인자는 모두 `None`이며 capture mode가 꺼진 production 요청·응답은 바뀌지 않는다.
producer는 production 호출에 편승하므로 추가 Gemini 호출을 만들지 않는다.

## 2. 쓰기 경계

완성 전 candidate JSON을 파일로 쓰지 않는다.

```text
transport trace + curation trace
→ 메모리에서 candidate 조립
→ completeness_gate()
→ complete candidate metadata만 risk balancing
→ 선정 case만 promote_case()
```

누락·secret·transport/curation mismatch는 case ID와 오류 코드만 메모리 report에 남고,
body/request/response/candidate 파일은 생성하지 않는다. `promote_case()`는 최종 선정된
case에만 호출된다.

## 3. opt-in capture

capture는 다음 세 환경변수가 명시됐을 때만 생성된다.

```powershell
$env:NUCLENS_SOURCE_COMPLETE_CAPTURE='on'
$env:NUCLENS_SOURCE_COMPLETE_STORE='.eval/gemini-reasoning-v2/source-complete-candidates'
$env:NUCLENS_SOURCE_COMPLETE_TARGET='30'
python news_bot.py
```

store는 저장소의 `.eval` 아래만 허용한다. target은 1~30만 허용한다. 위 명령은 실제
production-equivalent 수집 실행이므로 **실행 전에 해당 회차의 정상 curation 예상 호출 수,
token, 비용, hard cap을 보고하고 명시적 승인을 받아야 한다.** 이번 구현·검증에서는 실행하지
않았다.

사전 상태 확인은 API 0회다.

```powershell
python tools/source_complete_producer.py plan `
  --store .eval/gemini-reasoning-v2/source-complete-candidates `
  --target 30
```

## 4. 여러 실행 통합

각 임시 store는 completeness를 통과한 case만 담는다. 여러 실행에서 모은 임시 store를
하나의 최종 최대 30건 store로 합칠 때는 다음 명령을 사용한다.

```powershell
python tools/source_complete_producer.py consolidate `
  --input-store <run-1-store> `
  --input-store <run-2-store> `
  --store <final-source-complete-store> `
  --target 30
```

모든 입력 case/blob을 다시 검증하고, 기존 최종 store의 risk coverage를 초기값으로 사용해
`select_balanced_candidates()`로 부족한 bucket을 우선 선택한다. 복사 후에도
`validate_case_document()`를 통과해야 한다. 이 명령도 API 0회다.

## 5. 공개 저장소 저장 금지

`wavyhairs/nuclens-v2`는 public repository다. source-complete store에는 기사 body가
포함되므로 공개 GitHub Actions artifact로 업로드하지 않는다. workflow 배선과 repo variable
활성화는 이번 PR에 포함하지 않았다. 향후 자동 수집은 다음 중 하나가 먼저 승인돼야 한다.

1. 접근이 제한된 private artifact/storage
2. client-side 암호화 후 업로드하고 키는 artifact와 분리
3. 승인된 로컬 수집·보존 절차

기존 `NUCLENS_LLM_CAPTURE`의 14일 경량 artifact는 변경하지 않았다.

## 6. risk-balanced target

producer는 다음 bucket을 answer/Gold 없이 계산한다.

`event_boundary`, `scope`, `stage`, `date`, `causality`,
`unsupported_inference`, `omission`, `normal_pass_like`, `historical_error_prone`

기존 최종 store가 있으면 그 coverage를 반영해 새 case를 선택하며 전체를 30건에서 멈춘다.
완전한 case가 30건보다 많아도 비선정 candidate를 최종 store에 쓰지 않는다.

## 7. 검증 경계

- synthetic/offline fixture에서 실제 `curate_batch()` 경로 사용
- 정상 1건 → `SOURCE_COMPLETE_CALIBRATION_ELIGIBLE` 저장 및 store 재검증
- body 누락 → 서비스 output 유지, evidence case/blob 0
- secret 포함 → evidence case/blob 0
- transport trace 누락 → 과거 자료로 복원하지 않고 evidence case/blob 0
- 임시 store → 최종 store consolidation 후 integrity 재검증
- production request fixture 및 기존 curation 회귀 유지
- Gemini live API 0회
- OpenAI API 0회
- Gold/judge calibration/canary 0회

실행 결과:

- producer·evidence·capture·curation request 관련: 200 passed, 94 subtests passed
- 비-web 전체: 2,134 passed, 9 skipped, 489 subtests passed
- 전체 suite는 2,610 passed, 12 skipped까지 통과했고, 격리 worktree에 Git 비추적
  `web/public/data/*.json`이 없어 4 failed/114 setup errors가 발생했다. 생성 데이터를 다른
  worktree에서 복사하거나 web gate를 변경하지 않았다.
- `py_compile` 통과
- `plan --target 30`: eligible 0, remaining 30, 추가/live API 0회

## 8. 다음 재개 지점

1. 수집 직전 실제 예정 batch와 과거 token 실측으로 호출 수·비용·hard cap 보고
2. 명시적 승인 및 비공개/암호화 보존 위치 확정
3. capture mode를 제한된 실행에서 활성화
4. 최종 store eligible 20~30 및 risk coverage 확인
5. 그 다음에만 독립 reference-truth protocol로 이동

P4 canary, threshold, Gold, judge prompt/model, production reasoning,
`FAST_SEMANTIC_GATE_ENABLED`는 변경하지 않았다.
