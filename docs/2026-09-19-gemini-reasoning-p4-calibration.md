# Gemini reasoning P4 — manual judge calibration result (2026-09-19)

기준 원장: `docs/2026-09-08-gemini-reasoning-v2-progress.md`

## 판정

`JUDGE_CALIBRATION_NOT_PROVEN`. Gemini full-size canary는 실행하지 않는다.

## 입력과 provenance

- evaluator policy: `curation-blind-chatgpt-v1`
- ChatGPT UI 표시 모델: `GPT-5.6 Sol`
- 기존 independent/blind Curation Gold: 20건(PASS 4, REPAIR 16)
- repeat: 3, 총 60 judgment
- 신규 Human Review: 0
- OpenAI API 호출: 0
- Gemini live 호출: 0
- import 결과: 60 rows, unique key 60, schema/provenance 오류 0

수동 답변 SHA-256:

- repeat 0: `647002a861a7dd0752fb6da5cffea18c26e481e0824da97cb758567bcb35df35`
- repeat 1: `da5e442c26606197973e9fac12b5f60345053dcf429cccd6f88d22220741f90f`
- repeat 2: `6d9c1b3f67eb3ff74c93b687c9f8f8d459c41c93cfad3f391ea1b82102ef346d`

## 사전 고정 기준 대비 결과

| 지표 | 기준 | 결과 | 판정 |
|---|---:|---:|---|
| PASS vs intervention 일치율 | ≥ 0.85 | 0.6333 (38/60) | FAIL |
| false PASS | 0 | 16 | FAIL |
| unsafe PASS | 0 | 16 | FAIL |
| repeat modal stability | ≥ 0.90 | 1.00 | PASS |
| identical-pair TIE | ≥ 0.95 | 1.00 | PASS |
| identical-pair position bias | ≤ 0.05 | 0.00 | PASS |
| duplicate verdict consistency | ≥ 0.95 | 1.00 | PASS |
| schema/verdict-dimension 오류 | 0 | 0 | PASS |

judge는 안정적이고 position bias도 없었지만, 안정적으로 Human Gold와 다른 판단을 했다.
따라서 repeat 안정성만으로 신뢰할 수 없다.

## 불일치 분해

PASS Gold 4건 중 2건을 intervention으로 판정했다.

- `curation-7d5fb2f4f0dad180`: 세 번 모두 REPAIR
- `curation-92f35626180b393c`: 세 번 모두 BLOCK

REPAIR Gold 16건 중 5건을 세 번 모두 PASS로, 1건을 한 번 PASS로 판정했다.

- 세 번 모두 PASS: `curation-159ee56b79e02a3a`, `curation-3f0c0de0eae91573`,
  `curation-9caacb9fe51ac742`, `curation-f158b9b4c01d6732`,
  `curation-f19d023d5c9863e4`
- 한 번 PASS: `curation-1ce9bd9cd45a8831`

주된 한계는 Gold `source_input`에 title/url/published_at만 있고 원래 description/body가
저장되어 있지 않다는 점이다. 질문지는 URL을 열거나 외부 지식을 쓰지 못하게 했으므로,
제목에 없는 잘못된 세부를 judge가 `NOT_EVALUABLE`로 두었다. archive에도 원래
description/body/source excerpt는 없고 현재 생성 output만 남아 있어 독립 근거를 복원할
수 없다. 이 상태에서 threshold를 낮추거나 해당 case를 사후 제외하면 결과를 본 뒤 계약을
바꾸는 것이므로 하지 않는다.

## Validator 정정

첫 import는 한 case에서 `BLOCK`인데 모든 차원이 `NOT_EVALUABLE`이라는 이유로 원자적으로
거부됐고 결과 행을 쓰지 않았다. 해당 case는 source 제목 자체가 일반 모닝뉴스 제목이라
고정 prompt의 "근거 부족은 NOT_EVALUABLE" 규칙과 "발행 불가면 BLOCK" 판정이 함께 적용된
경우였다. validator를 다음처럼 좁게 정정했다.

- REPAIR는 계속 최소 1개 ERROR가 필수다.
- BLOCK은 ERROR가 있거나, **10개 차원 전부** NOT_EVALUABLE인 완전 증거부족 abstention만 허용한다.
- PASS+ERROR 및 PASS/NOT_EVALUABLE 혼합 상태의 무오류 BLOCK은 계속 거부한다.

threshold, Gold label, judge answer는 수정하지 않았다. 정정 후 60행 전부 strict import됐다.

## 재개 조건

현재 Gold에 대응하는 독립 source evidence(description/body 또는 당시 저장된 source excerpt)를
복원할 수 있어야 동일 judge를 다시 calibration할 수 있다. 그러한 기존 증거가 없고 신규 Human
Review도 금지된 현재 제약에서는 judge를 `PROVEN`으로 만들 수 없다. 별도의 명시적 의미 선택
없이 deterministic hard gate만으로 semantic P4 winner를 정하거나 Gemini canary를 실행하지
않는다.

## 회귀 확인

P4 대상 59 tests가 통과했다. `NOT_PROVEN` calibration summary가 존재하면 명시적 live-call
flag와 positive Gemini budget을 주더라도 `run_gemini_canary`에 진입하지 않는 회귀 테스트를
포함한다. 전체 suite의 기존 live-data gate 1건 실패 상태는 preflight 보고서와 동일하다.
