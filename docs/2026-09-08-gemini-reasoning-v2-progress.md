# Gemini reasoning validation v2 — 실행 상태 원장

> **재개하는 에이전트는 이 파일을 먼저 읽는다.** 사양은
> `docs/2026-09-08-gemini-reasoning-v2.md`, 상태·다음 명령은 이 파일이 유일한 출처다.
> 두 파일이 어긋나면 **이 파일이 맞다**(사양은 의도, 원장은 사실).

## 0. 재개 프로토콜 (질문 금지)

1. 이 파일의 §2 상태표에서 `IN_PROGRESS` 또는 첫 `PENDING` Phase를 찾는다.
2. 그 Phase의 §4 체크리스트에서 첫 미완료 항목부터 이어간다.
3. **사용자에게 묻지 않는다.** 판단이 필요하면 §5 사전 결정표를 적용한다.
   사전 결정표에 없는 경우에만 §6 HALT 사유로 정지하고 원장에 기록한다.
4. 작업 후 반드시 §2·§3·§4를 갱신하고 커밋한다. **원장 갱신 없는 커밋 금지.**
5. 컨텍스트가 부족해지면 진행 중이던 항목을 `IN_PROGRESS`로 남기고
   §3에 "다음 한 줄"을 정확히 적은 뒤 정지한다.

```
재개 명령:  git checkout feat/gemini-reasoning-v2 && cat docs/2026-09-08-gemini-reasoning-v2-progress.md
```

## 1. 불변 사실 (변경 금지)

| 항목 | 값 |
|---|---|
| 브랜치 | `feat/gemini-reasoning-v2` |
| 분기 기준 | `1a9eb7f` (main) |
| production reasoning | 전 profile 미활성 — 이 작업이 끝날 때까지 유지 |
| Fast semantic gate | `FAST_SEMANTIC_GATE_ENABLED = False` 유지 |
| 누적 신규 live API 호출 | **0** |
| PR #92 | 별도 브랜치. 이 작업은 main에서 새로 시작한다(§5-D 참조) |

## 2. Phase 상태

| Phase | 이름 | API | 상태 |
|---|---|---|---|
| P0 | Evaluator lockdown | 0 | `PENDING` |
| P0.5 | Behavior-neutral seam refactor | 0 | `PENDING` |
| P1 | Observed baseline audit | 0 | `PENDING` |
| P2 | Capture + recorded-response fidelity | 0 | `PENDING` |
| P3 | Independent Gold | 0 | `PENDING` |
| P4 | Sequential reasoning evaluation | 최소 | `PENDING` |
| P5 | Safety / operational decision | 0 | `PENDING` |
| P6 | Integration → activation | 최소 | `PENDING` |

상태값: `PENDING` / `IN_PROGRESS` / `DONE` / `HALTED(사유)` / `BLOCKED_HUMAN`

## 3. 다음 한 줄

> P0 시작: `tools/llm_eval.py`의 generic judge 경로 제거 + policy-namespaced key 강제.

## 4. Phase별 체크리스트

### P0 — Evaluator lockdown (API 0)
- [ ] `tools/llm_eval.py`: `SYSTEMS` generic judge 프롬프트 제거
- [ ] `call_contract()`가 production 계약 없는 task에 대해 `raise`
- [ ] `result_key()`가 evaluator policy 없이는 `raise`
- [ ] `completed_keys()`가 policy prefix 없는 legacy key를 completed로 인정하지 않음
- [ ] 회귀 테스트: generic judge 실행 시도가 실패함을 증명
- [ ] 기존 결과 파일 무변경 확인 (immutable)

### P0.5 — Seam refactor (API 0)
- [ ] `news_bot.curate_batch(client=None, log_path=None)` 추가, 기본 동작 동일
- [ ] `dedup._dedup_articles_impl(client=None)` 추가, 기본 동작 동일
- [ ] from-import 바인딩 주의사항 코드 주석에 명시
- [ ] **수용 기준: refactor 전후 frozen request fixture 바이트 동일**
- [ ] 전체 pytest 통과

### P1 — Observed baseline audit (API 0)
- [ ] 실제 직렬화 request body 추출기 작성 (`tools/observed_baseline.py`)
- [ ] `thinking_budget=0`, 모델별 필드 생략, model ladder 반영
- [ ] `production_contract_fingerprint()` 신설 (§사양 참조)
- [ ] `docs/...task-inventory.md` 를 추출값으로 재작성
- [ ] 회귀 테스트: inventory 값과 추출값 불일치 시 실패

### P2 — Capture + fidelity (API 0)
- [ ] passive capture 배선 (curation / dedup / issue_review)
- [ ] recorded-response offline replay 하네스
- [ ] **불변식: `_CALL_LOG` 증가 0 AND repo 파일 변경 0**
- [ ] profile별 `REPLAY_FIDELITY_PROVEN` / `NOT_PROVEN` 판정 기록
- [ ] 자연 데이터 축적 대기 (7~14일) — 이 항목은 시간 대기이며 HALT 아님

### P3 — Independent Gold (API 0)
- [ ] anchoring 측정용 blind 재검증 15건 선별 (경계층 집중)
- [ ] blind 라벨링 UI (Sol 판정 완전 은닉)
- [ ] Identity 충돌 14건 재검토 대상 확정
- [ ] **BLOCKED_HUMAN 지점 — 준비 완료 후 사용자에게 1회 요청**

### P4 — Sequential evaluation (최소 API)
- [ ] case-major 루프 + config 순서 randomize
- [ ] `pacing_wait_seconds` 분리 기록
- [ ] full-size batch canary
- [ ] dominated config 제거 → paired dev → finalist repeat → time-block holdout

### P5 — Decision (API 0)
- [ ] config-dependent failure vs provider noise 분류 적용
- [ ] paired 검정 (McNemar / exact sign / bootstrap CI)
- [ ] profile별 결정 기록

### P6 — Integration → activation (최소 API)
- [ ] 입력 분포 동등성 검사 (veto 전용)
- [ ] contract fingerprint 결합 활성화
- [ ] 자동 머지 금지 — PR 준비까지만

## 5. 사전 결정표 (질문 대신 이것을 적용한다)

| 상황 | 결정 |
|---|---|
| A. 자연 데이터가 아직 부족 | 대기. HALT 아님. 원장에 축적량 기록 후 다음 항목 진행 |
| B. fidelity 불일치 발견 | 해당 profile `REPLAY_FIDELITY_NOT_PROVEN`, live 평가 금지, 다른 profile 계속 |
| C. 결과가 불확실 | `VALIDATED_KEEP_BASELINE`. 임의 선택 금지 |
| D. PR #92와 충돌 | v2 브랜치가 우선. #92는 참조용으로만 두고 병합하지 않는다 |
| E. 503 / 네트워크 / 429 | provider noise. 재시도. config 탈락 사유 아님 |
| F. 특정 config에서만 반복되는 truncation/schema 실패 | config-dependent. 즉시 해당 config 탈락 |
| G. 테스트 실패 | 원인 수정. 테스트를 약화시켜 통과시키지 않는다 |
| H. Gold와 모델 결과 불일치 | Gold를 고치지 않는다. 불일치를 그대로 기록 |
| I. 어떤 profile도 활성화 근거 없음 | 정상 결과다. 전부 baseline 유지로 보고 |

## 6. HALT 사유 (이때만 정지하고 사용자에게 보고)

1. 새로운 Human labeling이 실제로 필요한 시점 (P3)
2. production 아키텍처를 크게 바꿔야만 replay가 가능한 경우
3. API 예상 사용량이 사전 상한을 초과
4. 반복 schema/quota 장애
5. active production contract 자체가 서로 모순
6. irreversible external action 또는 production merge가 필요
7. 사전 결정표에 없는 새로운 product semantics 선택

**시간이 오래 걸린다는 이유로는 정지하지 않는다.**

## 7. 실행 로그 (append-only)

| 일시 | Phase | 내용 | 커밋 |
|---|---|---|---|
| 2026-09-08 | — | v2 원장·사양 생성, 브랜치 분기 | (작성 중) |
