# Gemini reasoning validation v2 — 실행 사양

- 작성일: 2026-09-08
- 상태 원장: `docs/2026-09-08-gemini-reasoning-v2-progress.md` (**상태는 원장이 유일한 출처**)
- 폐기: 2026-09-06 34항 실행 지시어. 방향은 옳았으나 아래 §2의 경로를 봉쇄하지 못했다.

## 0. 성공 기준

성공은 "medium/high를 많이 넣는 것"이 아니다.

> **실제 production과 동일한 시험에서 더 높은 reasoning이 확실히 도움이 되는 profile만
> 최소한으로 올리고, 확인되지 않은 profile은 baseline을 유지하는 것.**

모든 profile이 baseline 유지로 끝나는 것도 **정상 결과**다.

## 1. 이번 검증의 판정 기준 — 한 문장

> "같은 prompt를 썼다"도 "production adapter를 썼다"도 증명이 아니다.
> **serialized request + orchestration + final behavior가 동일함**을 먼저 증명해야 하고,
> 그 증명은 코드 리뷰가 아니라 **관측 가능한 불변식**으로 한다:
> *offline replay 동안 실제 API 호출 0회, repo 파일 변경 0개.*

## 2. 코드로 확인된 사실 (재감사 불필요 — 여기서부터 시작한다)

기준 커밋 `1a9eb7f`. 각 항목은 파일·행으로 검증됨.

| # | 사실 | 근거 |
|---|---|---|
| F1 | Identity 평가기가 아직 간이 judge다. production 계약을 쓰지 않는다 | `tools/llm_eval.py` `SYSTEMS["IDENTITY_REVIEW"]`, `user_message()`가 제목 두 줄만 만든다. `call_contract()`는 CURATION만 차단 |
| F2 | Identity checkpoint key에 evaluator policy가 없다 → 무효 결과가 completed로 인정될 수 있다 | `EVALUATOR_POLICY`에 CURATION/SEMANTIC만 존재. `result_key(policy=None)` → prefix 없는 legacy 형식 |
| F3 | production baseline은 전부 unspecified가 아니다 | `expert_audio_brief.py:230`, `audio_brief.py:599` — `thinking_budget=(0 if policy.thinking_level is None else None)` |
| F4 | 그 결과가 모델별로 갈린다 | `gemini_client.py:338` `_THINKING_BUDGET_ZERO_UNSUPPORTED={"gemini-3.5-flash-lite"}` → 3.1은 explicit OFF, 3.5는 필드 생략 |
| F5 | 평가된 semantic 계약이 활성 경로가 아니다 | `semantic_verifier.verify()`는 `run_fast_semantic_gate` 안에서만 호출, 그 게이트는 `FAST_SEMANTIC_GATE_ENABLED=False` |
| F6 | 실제 활성 verifier는 expert 쪽이고 합격 조건이 더 많다 | `expert_audio_brief.py:181` `VERIFY_SYSTEM`, `:1084` `verification_passed()` — 5개 점수 임계 + critical claim 0 |
| F7 | 그 5개 점수는 사람이 정답을 만들 수 없다 | 모델 자가 생성 수치. 대응 human referent 없음 |
| F8 | Human Gold가 AI 판정에 anchoring됐다 | `tools/sol_provisional_gold.py` + `gold_labeler_assets/review.js:113-122` — Sol verdict/reason 선표시 + `A` 승인 |
| F9 | 그 흔적이 데이터에 남아 있다 | `semantic_gold`: `unsupported_inference` 24/24 라벨 vs `source_aligned` 1/24 라벨 — 라벨 완료율이 정답 클래스와 상관 |
| F10 | 기존 Identity 60건은 representative가 아니다 | `candidate_stratum`: `disagreement_probe` 10 + `known_edge_case` 7 = 28%가 모델 불일치 기반 선별 |
| F11 | production 배치 크기가 replay보다 훨씬 크다 | curation `BATCH_CHUNK=15`, issue_review/keei `BATCH_SIZE=20`, dedup `max(k*3,12)` → 국내 12 / 해외 18 |
| F12 | dedup은 fail-open이다 | `dedup.py` `except GeminiError` → 전량 유지. 실패 시 병합 0. **false merge 지표가 만점이 된다** |
| F13 | dedup 출력 예산이 좁다 | `max_output_tokens=6144`에 12~18건. 계획 문서 실측 high thought 약 5,100/배치 |
| F14 | issue_review의 실제 병목은 판단 품질이 아니라 quota다 | `issue_review.py:156` 실측: candidates 205 / from_cache 185 / asked 0 / failed 20 (quota) → 팍스 원전 분리 |
| F15 | client 주입이 현재 불가능하다 | `dedup.py:34`, `news_bot.py:22`가 **from-import 바인딩**. `gemini_client.call_json` patch는 먹지 않는다 |
| F16 | curate_batch가 production 파일에 쓴다 | `news_bot.py:2241,2245,2295` — 세 로깅 함수가 `path` 인자 없이 호출되어 `delivery_log.jsonl`에 기록 |
| F17 | latency/retry가 config 순서에 오염된다 | `llm_eval.py` 루프가 config-major + `gemini_client._pace()`가 전역 `_CALL_LOG` 기반 `time.sleep()`. high가 항상 마지막 → 체계적으로 느리게 측정됨 |
| F18 | fingerprint가 계약 변화를 못 잡는다 | `llm_policy.py:124` — model/thinking/sampling/prompt_version 4개뿐. parser·schema·batch·temperature 변경을 감지 못함 |
| F19 | dedup 입력은 curation 산출물에 의존한다 | `ranking.py:1055-1060` — curation features 기반 점수로 정렬된 head가 dedup 입력 |
| F20 | soft-stale 갱신은 이미 제한돼 있다 | `POLICY_REFRESH_BUDGET_PER_RUN=20`, `BATCH_SIZE=20` → run당 +1콜. 추가 장치 불필요 |
| F21 | `AMBIGUOUS`에 production 대응이 없다 | fixture `label_contract`에는 있으나 `issue_review`는 bool만 반환. 현재 0건(잠복) |

## 3. 절대 원칙

1. Human Gold를 모델 결과에 맞춰 수정하지 않는다.
2. 비교 변수는 **오직 reasoning level**이다. model·prompt·temperature·max tokens·timeout·retries·schema·parser·normalization·cache·fallback을 동시에 바꾸지 않는다.
3. production 데이터·cache·Telegram·배포 결과를 evaluation이 변경하지 않는다.
4. production shadow call을 상시 추가하지 않는다.
5. evaluator가 바뀌면 이전 결과를 새 결과와 **섞지 않는다.** 삭제·이동도 하지 않는다 — **in-place로 policy namespace를 붙여 보존**한다.
6. 증거가 불충분하면 활성화하지 않는다. "일단 medium" 금지.
7. 점수 최고가 아니라 **안전·신뢰성을 만족하는 가장 낮은 reasoning**을 고른다. 동률이면 baseline.

## 4. Phase 사양

### P0 — Evaluator lockdown (API 0) — 다른 모든 것에 선행

F1·F2를 봉쇄한다.

- `SYSTEMS`의 generic judge 프롬프트를 제거한다. task 목록은 계약 상태를 선언하는 레지스트리로 대체한다.
- `call_contract()`는 **production 계약이 증명된 task에만** 요청을 만든다. 나머지는 즉시 실패.
- `result_key()`는 evaluator policy 없이 호출되면 실패. 모든 task가 policy를 가져야 한다.
- `completed_keys()`는 **현재 policy prefix로 시작하는 key만** completed로 인정한다.
  legacy key(prefix 없음)는 보존하되 결코 skip 근거가 되지 않는다.
- 기존 `results.jsonl`은 **바이트 단위로 변경하지 않는다.**

수용 기준: generic judge를 실행하려는 시도가 테스트에서 실패한다.

### P0.5 — Behavior-neutral seam refactor (API 0)

F15·F16을 해소한다. **P2 capture보다 먼저 들어가야** capture가 refactor 후 코드를 반영한다.

- `news_bot.curate_batch(..., client=None, log_path=None)`
- `dedup._dedup_articles_impl(..., client=None)`
- from-import 바인딩 주의를 코드 주석에 남긴다 (patch 대상은 모듈 속성이지 `gemini_client`가 아니다).
- reasoning·프롬프트·파라미터는 손대지 않는다.

수용 기준: **refactor 전후 frozen request fixture가 바이트 동일** + 전체 pytest 통과.

### P1 — Observed baseline audit (API 0)

F3·F4·F18을 해소한다.

- baseline을 `unspecified` 같은 **추상 이름으로 정의하지 않는다.**
  각 callsite가 실제로 직렬화하는 request body를 추출해 그것을 baseline arm으로 쓴다.
- `production_contract_fingerprint()` 신설. 최소 입력:
  `resolved_model, system_prompt_sha, user_builder_sha, response_schema_sha,
  temperature, max_output_tokens, timeout, retries, batch_size, split_budget,
  parser_sha, normalizer_sha, observed_baseline_thinking`
- `observed_baseline_thinking`은 추출된 body에서 읽는다(상수 금지).
- task inventory를 추출값으로 재작성한다. **F3 때문에 현행 inventory의 expert_* 표기는 틀렸다.**

### P2 — Capture + recorded-response fidelity (API 0)

- 자연 production 실행에서 Gemini 호출 직전 상태를 passive capture한다.
  해석된 프롬프트 문자열 자체 + `sources.json`/`reports_kb.json`/`ranking_config.json`의 sha를 함께 남긴다(진단용).
- 캡처한 실제 응답을 recorded client로 **production orchestration에** 넣어 재생한다.
  별도 replay 구현을 복제하지 않는다.
- 일치 대상: request 순서·batch 구성·regeneration·split·quarantine·최종 정규화 산출물.
- **하드 불변식:** replay 전후 `gemini_client._CALL_LOG` 증가 0, repo 파일 변경 0.
- secret은 저장하지 않는다. 모델에 실제 전달된 범위만 보존한다.
- 결과를 profile별 `REPLAY_FIDELITY_PROVEN` / `NOT_PROVEN`으로 기록한다.
  `NOT_PROVEN`이면 그 profile의 live 호출을 금지한다.

### P3 — Independent Gold (API 0 + 사람 1회)

- **anchoring 크기를 먼저 측정한다.** 전면 재라벨하지 않는다.
  경계 판정 층에 집중해 15건을 Sol 완전 은닉 상태로 재라벨:
  `controlled_perturbation` 6 / Curation `REPAIR` 6 / Curation `PASS` 3.
  일치율보다 **불일치 방향**을 본다 — 전부 "AI가 더 관대" 방향이면 anchoring, 양방향이면 판정 난이도.
- 기존 Gold 상태값을 `HUMAN_REVIEWED_AI_ASSISTED`로 재분류한다(폐기하지 않는다).
- Identity 60건은 **challenge/reference로 보존**(F10). 계약 충돌 가능 케이스만 재검토:
  `different_stage` 6 + `different_action` 8 = **14건**.
  `follow_up_same_event` 5건은 이미 MERGE라 issue_review 계약과 충돌하지 않고,
  `broader_topic` 23건은 어느 profile 계약에서도 SEPARATE다. → **재검수 42건이 아니라 14건.**
- Semantic 클래스 균형: 미라벨 `source_aligned` 23건 라벨 → PASS 4 → 27.
- `AMBIGUOUS`(F21)는 1차 지표에서 제외하고 별도 보고한다.
- **expert_verify는 accuracy benchmark로 만들지 않는다**(F6·F7). §5 참조.

### P4 — Sequential reasoning evaluation (최소 API)

- 후보: **각 callsite의 실제 observed baseline**, low, medium, high.
  모델별로 중복인 arm은 canary의 `thought_tokens`로 잘라낸다.
- **case-major 루프 + case 내부 config 순서 randomize**(F17).
- `pacing_wait_seconds`를 `latency_seconds`에서 분리 기록한다.
- 순서: full-size batch canary → dominated 제거 → paired dev → finalist repeat → **time-block holdout**.
  (같은 소규모 세트를 dev/holdout으로 쪼개지 않는다. 시간 분할을 쓴다.)
- 모든 config를 처음부터 3회씩 돌리지 않는다.

### P5 — Safety / operational decision (API 0)

게이트 순서: **Reliability → Safety → Quality → 최소 충분.**

- 실패 분류: **같은 case를 다른 config로 돌렸을 때 baseline에서도 나오면 provider noise(재시도),
  특정 config에서만 반복되면 config-dependent(탈락).** 단발 503으로 탈락시키지 않는다.
- Identity: false merge뿐 아니라 **merge coverage · unjudged pairs · false split**을 본다(F12·F14).
  dedup은 truncation으로 fail-open되면 false merge가 만점이 되므로 coverage를 동급 critical로 둔다.
- Curation: 최종 PASS/REPAIR/BLOCK + regeneration·quarantine·call amplification.
- 통계: paired McNemar / exact sign test / bootstrap paired CI.
  **고정 threshold를 미리 두지 않는다.** 불확실하면 baseline 유지.

### P6 — Integration → activation (최소 API)

- **입력 분포 동등성 검사(veto 전용)** — F19 때문에 필요하다.
  baseline vs candidate curation으로 같은 frozen 기사 집합을 처리한 뒤,
  dedup이 실제로 소비하는 값(hash별 score, `features` 존재율, 길이 분포, `event_type` 분포,
  head 12/18 진입 집합)만 비교한다. 파이프라인 전체 재구성은 분포가 실제로 이동할 때만.
- 실패 시 composed replay를 더 돌려 이분탐색하지 않는다. profile 하나씩 활성화 + 자연 run 관찰로 전환.
- 활성화는 profile별. reasoning 결정을 `validated_model` + `production_contract_fingerprint`에 묶는다.
  runtime resolved model이 다르면 조용히 적용하지 않고 baseline 유지 + 명시 로그.
- activation 커밋에서 reasoning 외 production 동작을 바꾸지 않는다.
  request diff 테스트는 **`thinkingLevel` 키 단위**로 확인하고 `thinkingBudget` 제거를 별도 확인한다
  (둘 다 `thinkingConfig` 안이라 뭉뚱그리면 통과해버린다).
- 자동 머지 없음.

## 5. profile별 범위 결정

| profile | 이번 범위 | 이유 |
|---|---|---|
| `curation` | **포함** | 배치 replay가 완결 가능하고 영향이 크다 |
| `dedup` / `dedup_final` | **포함(안전 목적)** | fail-open 붕괴 위험 검증 자체가 가치(F12·F13) |
| `expert_verify` | **포함, 단 안전·운영 평가로만** | F6·F7. 5개 점수는 라벨 불가. 하루 1회라 n은 14 안팎. 합성 perturbation으로 unsafe PASS 0만 확인하고, 자연 스냅샷은 repair-loop 횟수·최종 실패율·call 증폭만 관측 |
| `issue_review` | **후순위 / 이번 제외 권고** | 실측이 악화 방향(병합 7배 감소)이고 실제 병목은 quota(F14). reasoning보다 모델 버킷 분리가 우선 |
| `fast_verify` / `fast_semantic_repair` / `dedup_legacy` | **제외** | 활성 production callsite 아님(F5) |
| 나머지 전부 | **제외** | task-specific Human Gold 없음. `INSUFFICIENT_EVIDENCE`로 명시 보류 |

## 6. 최종 상태값

`REPLAY_FIDELITY_NOT_PROVEN` / `DATA_CAPTURE_IN_PROGRESS` / `HUMAN_LABEL_REQUIRED` /
`INSUFFICIENT_EVIDENCE` / `VALIDATED_KEEP_BASELINE` / `VALIDATED_AND_ACTIVATED` /
`IMPLEMENTED_BUT_NOT_ACTIVATED` / `BLOCKED_BY_API_OR_TECHNICAL_ISSUE` /
**`NOT_A_PRODUCTION_CALLSITE`** (신설 — fast_verify 등)
