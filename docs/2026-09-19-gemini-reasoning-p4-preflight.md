# Gemini reasoning P4 — Curation preflight (2026-09-19)

이 문서는 API 호출 전 승인 보고서다. 기준 원장은
`docs/2026-09-08-gemini-reasoning-v2-progress.md`이며, 이 문서는 원장의 P4 실행 근거를
상세화할 뿐 상태를 대체하지 않는다.

## 1. 만든 evaluation contract

Production 후보 생성과 품질 판정을 서로 다른 계약으로 분리했다.

- 후보 생성: `tools/curation_p4.py`가 실제 `news_bot.curate_batch`를 호출한다.
- 기계 판정: 같은 도구의 deterministic hard gate가 item 대응, schema,
  `curation_errors`, lost/quarantine, transport failure, retry/truncation을 계측한다.
- 의미 판정: `tools/curation_p4_judge.py`의 독립 blind GPT judge가 고정 rubric과 strict
  JSON Schema로 후보별 `PASS|REPAIR|BLOCK` 및 모든 pairwise 비교를 반환한다.
- evaluator policy: `curation-blind-chatgpt-v1`. 결과 key와 resume namespace에 항상
  포함되며 다른 policy/legacy 결과는 completed로 인정하지 않는다.

## 2. Production path 보존

별도 Gemini 평가 프롬프트를 만들지 않았다. `curate_batch`, production prompt/parser,
`items` schema, `BATCH_CHUNK=15`, regeneration/split/quarantine을 그대로 사용한다. 주입
wrapper는 current arm에서 요청을 그대로 전달하고, 나머지 arm에서 오직
`thinking_level`만 추가한다. production이 추후 reasoning 인자를 보내기 시작하면
덮어쓰지 않고 즉시 실패한다. frozen request fixture도 그대로 통과했다.

## 3. Judge calibration 구조

API 대신 최신 ChatGPT UI에 질문지 파일을 넣고 strict JSON 답변을 받아 import한다.
UI에 표시된 모델명, 질문지·답변 SHA-256을 provenance로 보존한다. 차원은
`event_boundary`, `scope`, `stage`, `date`, `causality`, `factual_support`,
`unsupported_inference`, `important_omission`, `certainty`, `core_distortion`이다.
후보는 A/B/C/D로 익명화하고, policy·case·repeat 해시로 순서를 결정한다. judge에는
Gemini 모델, reasoning config, baseline 표시를 보내지 않는다.

Calibration은 같은 output의 익명 duplicate 두 개를 함께 넣어 20 case × 3 repeat =
60회 수행한다. 동일 후보 pair의 non-TIE는 position bias로, 두 verdict 불일치는
within-call inconsistency로 센다. case별 3회 modal verdict가 2회 미만이면
`JUDGE_UNSTABLE`이다. 기준은 결과를 보기 전에 코드에 고정했다.

- PASS 대 intervention 일치율 ≥ 0.85
- false PASS = 0, unsafe PASS = 0
- repeat modal stability ≥ 0.90
- identical pair TIE ≥ 0.95, position bias ≤ 0.05
- duplicate verdict consistency ≥ 0.95
- schema/verdict-dimension 논리 오류 = 0

하나라도 실패하거나 60행이 모두 유효하지 않으면 Gemini canary를 거부한다.

## 4. 기존 Human Gold 재사용

독립/blind 또는 user-specified 상태인 Curation Gold 20건(PASS 4, REPAIR 16)의
`current_output`만 judge calibration에 사용한다. 이를 새 Gemini reasoning output의
정답으로 재사용하지 않는다. 저장된 source evidence가 title/url/date로 제한된 case는
judge가 해당 차원을 `NOT_EVALUABLE`로 두도록 명시했으며, 부족한 근거를 외부 지식으로
채우지 않는다.

## 5. 새 Human Review

0건이다. 신규 labeling·검토 큐를 만들지 않았다.

## 6. Live Gemini 호출 수

0회다. preflight와 테스트에서 transport를 호출하지 않았다.

## 7. OpenAI judge 호출 수

API 호출은 0회다. judge 모듈에는 OpenAI API transport가 존재하지 않는다. 대신
calibration 질문지 3개를 각기 새 ChatGPT 대화에서 실행하고 JSON 답변을 import한다.

## 8. 예상 호출량·token·비용·시간·quota 영향

### 수동 ChatGPT judge calibration

- case/model/config/repeat: 20 case, GPT model 1, 동일 후보 control 2개, repeat 3
- ChatGPT 대화: repeat별 새 대화 3개(각 질문지 안에 20 case)
- API 호출·API token·API 비용·API quota 영향: 모두 0
- 질문/답변 규모 추정: input 21,912 / output 약 108,000 token(3개 합계)
- 사용자의 기존 ChatGPT 구독 범위에서 수행하며 UI의 메시지/출력 한도는 사전 보장할 수 없다.

### Gemini full-size canary — calibration PASS 뒤 별도 승인 필요

- case/model/config/repeat: 15 case, Gemini model 1, distinct reasoning arm 4, repeat 1
- arm: current, low, medium, high. `minimal`은 관측상 baseline과 중복되어 제외
- 논리 호출: 최소 4회, 자연 capture 재생성률 기준 예상 7.0회
- production 알고리즘상 극단 상한: 80회. 실제 승인 제안 cap은 arm당 3회, 총 12회
- token: 예상 input 94,969 / output 43,477; thought는 arm별 실제 usage로 계측
- 비용: 예상 USD 0.0890
- 시간: 순수 직렬 API 약 123초, 계획 범위 2~4분 + pacing/backoff
- quota: repo pacing cap 12 RPM. 계정 RPD/RPM은 provider dashboard 확인 필요

Gemini 가격은 2026-09-19 공식 표준 요금인 input USD 0.25/1M, output(사고 토큰 포함)
USD 1.50/1M을 사용했다. GPT judge는 같은 날짜 공식 표준 short-context 요금인 input
USD 10/1M, output USD 50/1M을 사용했다.

### Canary blind judge — Gemini hard gate PASS 뒤

- ChatGPT 대화: 1개 질문지(15기사 × 4후보, 기사별 6 pair)
- OpenAI API 호출·비용: 0
- recorded baseline proxy: input 26,575 / output 예상 54,000, 상한 60,000 token
- 실제 4-arm output으로 호출 직전에 다시 계산한다.

## 9. 현재 실제 실행 가능 상태

구현과 offline 검증은 완료됐지만 live 실행 승인은 나지 않았다. 현재 상태는
`BLOCKED_PENDING_MANUAL_CHATGPT_CALIBRATION_IMPORT`이다. 수동 calibration이 PASS한
뒤에만 Gemini canary 명령이 열리고, 네 arm hard gate가 모두 PASS한 뒤에만 canary
blind judge가 열린다.

## 10. 남은 blocker

1. 세 질문지를 서로 독립된 새 ChatGPT 대화에서 실행하고 JSON 답변 3개 업로드
2. 답변 import 및 calibration PASS
3. 그 뒤 Gemini 최대 12 logical call 승인과 Gemini quota 확인
4. 네 arm hard gate PASS
5. canary 질문지 1개 수동 실행·업로드

## 11. 수정 파일

- `tools/curation_p4.py`
- `tools/curation_p4_judge.py`
- `tools/llm_eval.py`
- `tests/test_curation_p4.py`
- `tests/test_llm_eval.py`
- 이 문서
- `docs/2026-09-08-gemini-reasoning-v2-progress.md`

Production reasoning, `FAST_SEMANTIC_GATE_ENABLED`, model routing, crawl/daily/weekly 동작,
배포 설정은 수정하지 않았다.

## 12. 테스트 결과

P4 대상 회귀 57건이 통과했다. 포함 범위는 anonymity, deterministic randomization,
config leak, policy namespace, legacy 결과 미재사용, malformed/missing dimension
fail-closed, hard gate/judge 분리, frozen production request 불변, live call 0,
production data/cache 무변경이다. 전체 suite는 2,670 passed, 10 skipped, 1 failed였다.
실패는 기존 live web data의 주별 합계 `[44, 51, 105, 144, 144, 166, 165, 150]`가
최대/최소 비율 gate 2를 넘은 3.77이어서 발생한
`TopicWeekAggregationTests.test_live_data_weeks_are_within_the_front_end_gate` 한 건이다.
P4 코드와 무관한 실데이터 gate이며 이 작업에서는 데이터나 테스트를 고치지 않았다.

## 고정 canary 표본

capture artifact `10526987340`, sequence 4의 15건을 선택했다. reports_kb가 필요 없는
최초 curation 호출이며, balance-first/coverage-second/recency-third 규칙으로 output을
보기 전에 고정했다. 위험 차원별 포함 건수는 event boundary 13, scope 12, stage 12,
date 15, causality 11이며 나머지 source-alignment 차원은 각 15다. arm 실행 순서는
medium → low → high → current로 결정적으로 고정됐다.

Gemini 가격 근거: https://ai.google.dev/gemini-api/docs/pricing
