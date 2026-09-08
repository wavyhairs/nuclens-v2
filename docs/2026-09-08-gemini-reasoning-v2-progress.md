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
| 브랜치 | `feat/gemini-reasoning-v2` (푸시됨) |
| 분기 기준 | `1a9eb7f` (main) |
| production reasoning | 전 profile 미활성 — 이 작업이 끝날 때까지 유지 |
| Fast semantic gate | `FAST_SEMANTIC_GATE_ENABLED = False` 유지 |
| 누적 신규 live API 호출 | **0** (P0/P0.5 확인, `_CALL_LOG` 불변식 테스트로 강제) |
| PR #92 | 별도 브랜치. 이 작업은 main에서 새로 시작한다(§5-D 참조) |

## 2. Phase 상태

| Phase | 이름 | API | 상태 |
|---|---|---|---|
| P0 | Evaluator lockdown | 0 | `DONE` (fb25171) |
| P0.5 | Behavior-neutral seam refactor | 0 | `DONE` (ca39d89) |
| P1 | Observed baseline audit | 0 | `DONE` (83942f7) |
| P2 | Capture + recorded-response fidelity | 0 | `IN_PROGRESS` — 배선 완료, 스위치 대기 |
| P3 | Independent Gold | 0 | `IN_PROGRESS` |
| P4 | Sequential reasoning evaluation | 최소 | `PENDING` |
| P5 | Safety / operational decision | 0 | `PENDING` |
| P6 | Integration → activation | 최소 | `PENDING` |

상태값: `PENDING` / `IN_PROGRESS` / `DONE` / `HALTED(사유)` / `BLOCKED_HUMAN`

## 3. 다음 한 줄

> **사람이 해야 하는 일 1건 — capture 스위치.** 이 브랜치를 머지한 뒤 repo variable
> `NUCLENS_LLM_CAPTURE` 를 `on` 으로 둔다 (Settings → Secrets and variables →
> Actions → Variables). 그때부터 7~14일 자연 데이터가 쌓인다. 값을 지우면 꺼진다.
>
> **capture 대기와 병행할 다음 작업 — 입력 재구성기.**
> `tools/recorded_replay.py` 는 완성됐고 입력만 있으면 판정한다. 남은 것은 캡처
> 시각의 입력(기사 목록·본문·reports_kb·scores)을 복원하는 쪽이다. 후보 출처는
> 봇이 커밋하는 `curated.json` / `archive/` / `sent.json` 이다.
> 재구성이 틀리면 프롬프트가 달라져 하네스가 NOT_PROVEN 을 낸다 — 즉 **정확성을
> 따로 증명할 필요 없이 하네스가 채점해 준다.** 맞출 때까지 반복하면 된다.
>
> **먼저 확인:** `python -m pytest tests/test_recorded_replay.py
> tests/test_production_request_fixture.py tests/test_observed_baseline.py
> tests/test_llm_capture.py -q` (34 passed).

## 4. Phase별 체크리스트

### P0 — Evaluator lockdown (API 0)
- [x] `tools/llm_eval.py`: `SYSTEMS` generic judge 프롬프트 제거
- [x] `call_contract()`가 production 계약 없는 task에 대해 `raise`
- [x] `result_key()`가 evaluator policy 없이는 `raise`
- [x] `completed_keys()`가 policy prefix 없는 legacy key를 completed로 인정하지 않음
- [x] 회귀 테스트: generic judge 실행 시도가 실패함을 증명
- [x] 기존 결과 파일 무변경 확인 (immutable)

### P0.5 — Seam refactor (API 0)
- [x] `news_bot.curate_batch(client=None, log_path=None)` 추가, 기본 동작 동일
- [x] `dedup._dedup_articles_impl(client=None)` 추가, 기본 동작 동일
- [x] from-import 바인딩 주의사항 코드 주석에 명시
- [x] **수용 기준: refactor 전후 frozen request fixture 바이트 동일**
- [x] 전체 pytest 통과

### P1 — Observed baseline audit (API 0)
- [x] 실제 직렬화 request body 추출기 작성 (`tools/observed_baseline.py`)
- [x] `thinking_budget=0`, 모델별 필드 생략, model ladder 반영
- [x] `production_contract_fingerprint()` 신설 (§사양 참조)
- [x] `docs/...task-inventory.md` 를 추출값으로 재작성
- [x] 회귀 테스트: inventory 값과 추출값 불일치 시 실패

### P2 — Capture + fidelity (API 0)
- [x] passive capture 배선 — `gemini_client._capture` + workflow 4곳 (0cb60ff, a007a4d)
- [x] capture 안전 불변식 (꺼짐 기본 / 실패 무해 / 키 유출 시 폐기) — `tests/test_llm_capture.py`
- [x] recorded-response replay 하네스 (`tools/recorded_replay.py`, 3d4e285)
- [x] **replay 불변식(정정)**: replay 는 `call_json` 을 실제로 지나므로 `_CALL_LOG`
      는 늘어난다. 옳은 불변식은 `call_log_delta == replayed_calls` (transport 를
      우회한 호출이 없다) + repo 파일 변경 0 이다. 앞서 적은 '증가 0' 은 offline
      **capture 검증**에는 맞고 replay 에는 맞지 않았다.
- [ ] profile별 판정 실행 — **실데이터 대기**. 도구는 준비 완료:
      `python tools/fidelity_gate.py --capture <llm_capture.jsonl>`
- [x] 입력 재구성기 `tools/replay_inputs.py` + provenance 판정 (1311a11)
- [x] dedup 재구성기 (6b9019a) — 15개 필드 중 13개가 저장소 복원 가능
- [x] fidelity gate `tools/fidelity_gate.py` — 캡처 → profile 판정 (다음 커밋)
- [ ] issue_review 재구성기 — **이번 범위 제외**(사양 §5: 후순위)
- [ ] 자연 데이터 축적 대기 (7~14일) — 이 항목은 시간 대기이며 HALT 아님

### P3 — Independent Gold (API 0)
- [x] #92 Gold 도입 + `HUMAN_REVIEWED_AI_ASSISTED` 재분류 (9b089fb)
- [x] anchoring 측정용 blind 재검증 15건 결정적 선별 — `tools/gold_provenance.py`
- [x] blind 패킷 (화이트리스트 + 층 섞기 + 변이 검증) — `tools/blind_relabel.py` (657ce87)
- [x] Identity 계약 매핑 확정 — `tools/identity_contract_map.py` (51f2036)
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

## 4-A. 재구성 순환 경계 (P2 — 반드시 유지)

`description` 과 `body` 는 **저장소 어디에도 없다**(archive/curated/digest_queue 확인).
body 는 저작권 판단으로 저장하지 않고, description 은 큐레이션 뒤 summary 로 대체된다.
따라서 이 둘은 캡처된 프롬프트가 유일한 출처이고, 그 부분에 대한 요청 일치는 순환이다.

| 필드 | 출처 | 요청 일치가 검증인가 |
|---|---|---|
| `title`, `publisher`, `domain`, `hash` | archive / curated | **예** |
| batch 구성, 호출 순서, 재생성 여부 | orchestration | **예** |
| `description`, `body` | 캡처된 프롬프트 | **아니오 (순환)** |

`replay_inputs.independence()` 가 이 구분을 판정에 싣는다. 최종 보고서에서
이 경계를 지우고 PROVEN 만 인용하면 안 된다.

P4 reasoning 비교에서는 순환이 문제가 아니다 — 프롬프트에서 뜯은 값은 production 이
실제로 보낸 것과 바이트가 같아 "입력 고정, config 만 변경"에 오히려 정확하다.

## 4-0. P1 결과 (확정)

12개 callsite 중 **2개만** baseline 이 unspecified 가 아니다.
전체 표: `docs/2026-09-09-gemini-observed-baseline.md` (생성물, 손으로 고치지 않는다).

| 관측값 | callsite |
|---|---|
| `budget:0` — 명시적 OFF (3.1) | `expert_dossiers`, `expert_verify` |
| 필드 없음 (3.5-flash-lite 제약) | `expert_plan`, `expert_script`, `expert_repair`, `audio_brief` |
| 필드 없음 | `curation`, `dedup`, `dedup_final`, `issue_review`, `keei_match`, `fast_verify` |

`thinkingLevel` 을 보내는 callsite는 0개다 — 활성화 전 상태가 관측으로 확인됐다.

## 4-1. P1 상세 (완료 — 참고용)

추출기가 반드시 재현해야 하는 것 — 이걸 못 잡으면 baseline 이 틀린다:

| callsite | 관측되어야 할 baseline |
|---|---|
| `expert_verify` / `expert_dossiers` (3.1) | `thinkingConfig.thinkingBudget: 0` — **명시적 OFF** |
| `expert_plan` 외 expert 서술 (3.5-flash-lite) | budget 0 이 모델 때문에 **생략** → 필드 없음 |
| `audio_brief` (모델 사다리) | 사다리 어느 단이냐에 따라 위 둘이 갈린다 |
| `curation` / `dedup` / `issue_review` / `keei_match` | thinking 필드 없음 |

`production_contract_fingerprint()` 최소 입력은 사양 §4 P1 참조. `observed_baseline_thinking` 을 상수로 적으면 P1 은 실패한 것이다.

## 4-B. 사람에게 요청할 작업 — 한 번에 몰아 둔 전부

이 셋 말고는 사람 개입이 필요 없다. 도구·선별·순서는 모두 준비됐다.

| # | 작업 | 분량 | 명령 |
|---|---|---|---|
| 1 | capture 스위치 | 설정 1개 | repo variable `NUCLENS_LLM_CAPTURE=on` |
| 2 | blind 재검증 | **15건** | `python tools/blind_relabel.py --packet <경로>` → 판정 → `--compare` |
| 3 | Identity 케이스 검토 | **22건** | `other` 9 + `different_action` 8 + `follow_up_same_event` 5 |

3번이 22건인 이유: reason code 하나로 두 계약을 모두 가를 수 없는 칸이다.
issue_review 는 17건(`different_action` 8 + `other` 9), dedup 은 14건
(`other` 9 + `follow_up_same_event` 5)을 못 가르고, 합집합이 22건이다.

## 4-C. Identity 계약 충돌 (확정 사실)

`issue_review` 와 `dedup` 이 **같은 예시로 반대 판정을 요구한다**(협상 → 계약 체결).
버그가 아니라 서로 다른 질문이지만, **단일 Identity 정답지는 성립하지 않는다.**

| profile | 사용 가능 | 케이스 검토 | 계약으로 뒤집힘 | MERGE/SEPARATE |
|---|---|---|---|---|
| `issue_review` | 43 | 17 | **6** (전부 `different_stage`) | 19 / 24 |
| `dedup` / `dedup_final` | 46 | 14 | 0 | 8 / 38 |

기존 60건은 **dedup 계약에 맞춰져 있었다**(뒤집힘 0). 폐기할 이유가 없다.
issue_review 계약으로 옮기면 13/47 → 19/24 로 클래스 균형이 회복된다.
dedup 쪽 MERGE 8건은 merge recall 을 재기에 얇다 — coverage·붕괴 안전 지표로 읽는다.

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
| 2026-09-08 | — | v2 원장·사양 생성, 브랜치 분기 | `feat/gemini-reasoning-v2` |
| 2026-09-09 | P0 | 평가기 봉쇄. 네 task 모두 요청 생성 불가, policy-namespaced key 강제. 테스트 14 passed | `fb25171` |
| 2026-09-09 | P0.5 | 이음매 리팩터링 + `frozen_requests.json` 으로 바이트 중립성 증명. 전체 1579 passed | `ca39d89` |
| 2026-09-09 | P2 | capture 훅 + workflow 배선(기본 꺼짐). 전체 1597 passed | `0cb60ff`, `a007a4d` |
| 2026-09-09 | P2 | replay 하네스 + 왕복/부정 테스트. 고장 3종 주입으로 검사기 유효성 확인. 전체 1607 passed | `3d4e285` |
| 2026-09-09 | P2 | curation 입력 재구성기 + provenance. description/body 가 저장소에 없음을 확인하고 순환 경계를 명시. 전체 1617 passed | `1311a11` |
| 2026-09-09 | P2 | dedup 재구성기(13/15 저장소 복원) + fidelity gate. 전체 1630 passed | `6b9019a`, `9f576cb` |
| 2026-09-09 | P3 | Gold 출처 재분류(라벨 불변) + blind 15건 선별. 전체 1645 passed | `9b089fb` |
| 2026-09-09 | P3 | blind 패킷(가림 변이 검증). 전체 1656 passed | `657ce87` |
| 2026-09-09 | P3 | Identity 계약 충돌 발견 + profile별 매핑. 전체 1668 passed | `51f2036` |
| 2026-09-09 | — | daily-brief 34279339893 완료 확인 후 rebase → push | — |
| 2026-09-09 | P1 | 관측 baseline 확정 — `expert_dossiers`/`expert_verify` 는 `budget:0`(명시적 OFF), 나머지는 필드 없음. contract fingerprint 신설. 전체 1589 passed | `83942f7` |
