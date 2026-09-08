# Gemini callsite 관측 baseline

생성: `python tools/observed_baseline.py`. **손으로 적지 않는다** — 이 표는
`urlopen` 경계에서 가로챈 **실제 직렬화 요청 본문**에서 나온다.
회귀는 `tests/test_observed_baseline.py` 가 막는다.

이전 inventory 는 모든 profile 을 `unspecified` 로 적었다. 그건 틀렸다 —
`expert_dossiers` 와 `expert_verify` 는 `thinkingBudget: 0` 을 실제로 보낸다.
즉 thinking 이 **명시적으로 꺼진 채** 돌고 있고, 그 둘의 reasoning 활성화는
필드 추가가 아니라 **교체**다.

| callsite | 모델 | 관측 baseline | temp | maxTokens | 이번 범위 | 비고 |
|---|---|---|---|---|---|---|
| `audio_brief` | gemini-3.5-flash-lite | 필드 없음 | 0.4 | 8192 | 제외 | narrative Gold 없음 |
| `curation` | gemini-3.1-flash-lite | 필드 없음 | 0.2 | 32768 | 포함 | 배치 replay 가 완결 가능하고 영향이 크다 |
| `dedup` | gemini-3.1-flash-lite | 필드 없음 | 0.05 | 6144 | 포함(안전) | 실패가 fail-open 이라 붕괴가 지표에 안 잡힌다 |
| `dedup_final` | gemini-3.1-flash-lite | 필드 없음 | 0.05 | 6144 | 포함(안전) | 위와 동일 계약, stage 만 다름 |
| `expert_dossiers` | gemini-3.1-flash-lite | **budget:0 (명시적 OFF)** | 0.2 | 8192 | 제외 | extraction Gold 없음 |
| `expert_plan` | gemini-3.5-flash-lite | 필드 없음 | 0.2 | 8192 | 제외 | planning Gold 없음 |
| `expert_repair` | gemini-3.5-flash-lite | 필드 없음 | 0.2 | 8192 | 제외 | narrative Gold 없음 |
| `expert_script` | gemini-3.5-flash-lite | 필드 없음 | 0.2 | 8192 | 제외 | narrative Gold 없음 |
| `expert_verify` | gemini-3.1-flash-lite | **budget:0 (명시적 OFF)** | 0.2 | 8192 | 포함(안전·운영만) | 점수 5개는 라벨 불가. 하루 1회 |
| `fast_verify` | gemini-3.1-flash-lite | 필드 없음 | 0.0 | 6000 | 제외 | 활성 production callsite 아님 (게이트 비활성) |
| `issue_review` | gemini-3.5-flash-lite | 필드 없음 | 0.0 | 16384 | 후순위 | 실측이 악화 방향, 실제 병목은 quota |
| `keei_match` | gemini-3.1-flash-lite | 필드 없음 | 0.0 | 8192 | 제외 | task Gold 없음 |

## 읽는 법

- **필드 없음 ≠ thinking 켜짐.** `gemini-3.5-flash-lite` 는 `thinkingBudget: 0`
  을 거부하므로 그 모델에서만 필드가 빠진다. 코드는 같은데 결과가 갈린다.
- `expert_plan`/`expert_script`/`expert_repair` 는 모델 사다리의 첫 단이
  3.5-flash-lite 라 필드가 빠진다. 사다리가 2단으로 넘어가면 3.1 이 되고,
  그때는 `budget:0` 이 실제로 나간다 — 같은 profile 안에서도 갈린다.
- 어떤 callsite 도 아직 `thinkingLevel` 을 보내지 않는다. 활성화 전 상태다.

## 평가 baseline arm 규칙

reasoning 비교의 baseline 은 `"unspecified"` 같은 이름이 아니라 **이 표의 값**
이다. `expert_verify` 의 baseline arm 은 `thinkingBudget: 0` 을 보내야 하고,
`curation` 의 baseline arm 은 thinking 필드를 보내지 않아야 한다. 이름으로 두면
production 에 존재하지 않는 설정과 비교하게 된다.

`llm_policy.production_contract_fingerprint()` 는 `observed_baseline_thinking`
에 추상 이름이 들어오면 거부한다.
