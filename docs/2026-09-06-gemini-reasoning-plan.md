# Gemini 3.x reasoning 도입 — Stage 0 실측 기반 재검토 및 실행 계획

- 작성일: 2026-09-06
- 대상 원안: 「Gemini LLM 사용 구조 전면 점검 / reasoning capability 활용」 18개 항 계획서
- 기준 코드: `origin/main` = `ab0a82f` (⚠️ 로컬 `refactor/v5-checkpoint`에는 V5 하네스가 **없다**. §6.0 참조)
- 성격: **구현 명세.** 코덱스 등 에이전트가 그대로 실행할 수 있도록 단계·파일·수용기준·중단조건을 명시했다.
- 근거: 저장소 전수 대조 + **실제 Gemini API 프로브 24콜**(2026-09-06 실행, §1). 추측이 아니다.

---

## 0. 실행 요약

원안의 **원칙은 옳다** — silent downgrade 금지, reasoning/sampling 분리, 전수 shadow 금지, 캐시 일괄 무효화 금지,
출처 사실과 LLM 해석 분리. 이 저장소의 기존 관행과도 잘 맞는다.

문제는 **어디에 무엇을 적용할지에 대한 사실 판단**이다. Stage 0 프로브가 원안의 가정 하나를 확증하고
하나를 뒤집었으며, 그 결과 우선순위가 바뀐다.

| 구분 | 건수 | 위치 |
|---|---|---|
| 🔴 프로브가 **뒤집은** 결론 (원안 채택 시 증상 악화 위험) | 1 | §2.2 |
| 🟢 프로브가 **확증한** 원안 전제 | 2 | §2.1, §2.3 |
| 🔴 원안의 사실 오류 | 5 | §4.1 |
| 🟠 원안이 못 본 실제 결함 | 3 | §4.2 |
| 🟠 위험한 미명세 | 3 | §4.3 |
| ➖ 이번 범위에서 제외 권고 | 3 stage + 2 항목 | §7 |

**한 줄 결론:** `thinkingLevel`은 세 모델 모두에서 동작한다. 그러나 **IDENTITY_REVIEW를 high로 올리면
병합이 7배 줄어든다**(§1.4). 이 시스템의 문서화된 실제 증상은 *과소병합*("팔로잉이 안 된다", 팍스 원전
분리)이므로, 원안의 `IDENTITY_REVIEW = medium/high` 방향은 **증상을 악화시킬 가능성이 높다.**
Gold Set 없이 어떤 profile도 올리지 않는다.

---

## 1. Stage 0 프로브 실측 (2026-09-06)

**총 24콜.** 버킷별: `gemini-3.1-flash-lite` 9콜, `gemini-3.5-flash-lite` 13콜, `gemini-3.5-flash` 2콜.
프로덕션 상태·캐시는 일절 건드리지 않았다. 재현 방법은 부록 A.

### 1.1 thinkingConfig 수용 행렬

프롬프트: 짧은 시간순서 판정 1건. `temperature=0.1`, `maxOutputTokens=2048`.

| 모델 | 변형 | HTTP | thought tokens | latency |
|---|---|---|---|---|
| gemini-3.1-flash-lite | (필드 없음) | 200 | **0** | 0.71s |
| gemini-3.1-flash-lite | `thinkingBudget: 0` | 200 | 0 | 0.74s |
| gemini-3.1-flash-lite | `thinkingBudget: 512` | 200 | 133 | 1.19s |
| gemini-3.1-flash-lite | `thinkingLevel: minimal` | 200 | 0 | 0.93s |
| gemini-3.1-flash-lite | `thinkingLevel: low` | 200 | 133 | 1.10s |
| gemini-3.1-flash-lite | `thinkingLevel: medium` | 200 | 152 | 1.20s |
| gemini-3.1-flash-lite | `thinkingLevel: high` | 200 | 185 | 1.36s |
| gemini-3.5-flash-lite | (필드 없음) | 200 | **0** | 0.83s |
| gemini-3.5-flash-lite | `thinkingBudget: 0` | **400** | — | 0.34s |
| gemini-3.5-flash-lite | `thinkingBudget: 512` | 200 | 0 | 0.79s |
| gemini-3.5-flash-lite | `thinkingLevel: minimal` | 200 | 0 | 0.64s |
| gemini-3.5-flash-lite | `thinkingLevel: low` | 200 | **0** | 0.84s |
| gemini-3.5-flash-lite | `thinkingLevel: medium` | 200 | 325 | 1.60s |
| gemini-3.5-flash-lite | `thinkingLevel: high` | 200 | 290 | 1.70s |
| gemini-3.5-flash | `thinkingLevel: medium` | 200 | 252 | 1.92s |
| gemini-3.5-flash | `thinkingLevel: high` | 200 | 334 | 2.27s |

읽어야 할 것:

1. **`thinkingLevel`은 4개 값(`minimal|low|medium|high`) 모두, 세 모델 모두에서 200이다.** 원안 §2의
   `generationConfig.thinkingConfig.thinkingLevel` 형식이 맞다.
2. **`gemini-3.5-flash-lite`의 400은 `thinkingBudget: 0`에만 국한된다.** `thinkingBudget: 512`도 200이고
   `thinkingLevel`은 전부 200이다. 현재의 **모델 단위 블랙리스트는 과잉 차단**이며,
   그대로 두면 이 모델에 `thinking_level`을 영원히 못 보낸다. → (모델, 필드) 단위로 좁혀야 한다.
3. **`gemini-3.5-flash-lite`에서 `low`는 thought 0이다.** 즉 이 모델에서 `minimal`과 `low`는 사실상 같고,
   `low → medium`이 절벽(0 → 325)이다. 원안의 "BULK_CURATION = minimal/low 후보"는 이 모델에서
   **구분되지 않는 선택지**다.
4. `high(290) < medium(325)`가 나왔다(3.5-flash-lite, n=1). 단일 샘플로 medium/high를 서열화할 수 없다.
   원안 §6의 "반복 측정" 요구가 옳다.

### 1.2 "필드 없음"은 dynamic이 아니라 OFF다 — 어려운 과제로 재확인

1.1의 thought=0이 "질문이 쉬워서"인지 "기본값이 off"인지 가르기 위해, 아카이브 실데이터로
issue_review 형태의 난이도 있는 판정을 다시 걸었다(`temperature=0.0`).

| 모델 | 변형 | thought tokens | output | latency | 판정 |
|---|---|---|---|---|---|
| gemini-3.1-flash-lite | (필드 없음) | **0** | 62 | 0.95s | `same_event: false` (정답) |
| gemini-3.1-flash-lite | `level: high` | 262 | 61 | 1.73s | `same_event: false` (정답) |
| gemini-3.5-flash-lite | (필드 없음) | **0** | 68 | 0.93s | `same_event: false` (정답) |
| gemini-3.5-flash-lite | `level: high` | 554 | 67 | 2.43s | `same_event: false` (정답) |

→ **현행 프로덕션은 thinking이 사실상 전부 꺼진 상태로 돌고 있다.** 원안의
"Flash-Lite 기본 minimal thinking" 전제는 **맞다.**

> ⚠️ 정정: 이 문서의 초기 검토에서 "필드를 안 보내면 모델 기본값(dynamic)으로 돈다"고 적었는데
> 틀렸다. 프로브가 두 모델·두 난이도에서 모두 thought=0을 보였다. 원안이 옳다.

### 1.3 새울 회귀 케이스는 thinking 없이도 통과한다

§1.2의 입력은 아카이브 실제 기사 두 건이다:

- **[A]** 새울 3호기, 출력 80% 시운전 중 자동정지 (2026-08-12 보도, hash `bdf0fa9bc581aaa0`)
- **[B]** 새울 3·4호기 건설사업 실시계획상 시행기간 10개월 연장 (2026-08-14 보도, hash `4da5b7ab6c225c78`)

**4개 config 전부 `same_event: false`로 올바르게 판정했고, 이유도 정확했다**
("A는 자동정지 사고, B는 건설사업 시행기간 연장이라는 행정 조치 — 범위와 성격이 다르다").
§1.1의 순수 시간순서 문제도 16개 config 전부 정답이었다.

**함의: 원안이 헤드라인으로 내건 새울 회귀는 reasoning 상향의 근거가 되지 못한다.**
현재 설정으로도 통과한다. 실제 결함은 다른 곳에 있다 → §4.2.3.

### 1.4 🔴 프로덕션 배치 크기 실측 — high는 병합을 7배 줄인다

`issue_llm_reviews.json`(10,074건)에서 seed 고정 20쌍을 뽑아 실제 `issue_review` 배치 형태로
`gemini-3.5-flash-lite`에 걸었다. `BATCH_SIZE=20`, `maxOutputTokens=16384`, `temperature=0.0`.
각 config 2회 반복.

| config | latency | thought tokens | output tokens | finishReason |
|---|---|---|---|---|
| (필드 없음) run1 | 4.23s | 0 | 1,361 | STOP |
| (필드 없음) run2 | 3.78s | 0 | — | STOP |
| `level: high` run1 | **16.64s** | **5,099** | 1,206 | STOP |
| `level: high` run2 | **18.06s** | **5,121** | — | STOP |

| 측정 | 결과 |
|---|---|
| 재현성 — none run1 vs run2 | 1/20 (5%) |
| 재현성 — high run1 vs run2 | 1/20 (5%) |
| 차이 — none vs high (run1) | 4/20 |
| 차이 — none vs high (run2) | 2/20 |
| **`same_event=true` 총합 (2런 합산)** | **none 7/40 · high 1/40** |

**해석:**

- **비용:** latency ×4.0 (평균 4.0s → 17.4s), thought 0 → 약 5,100 토큰/배치.
  16,384 출력 예산을 thinking이 31% 잠식한다 — `issue_review.MAX_OUTPUT_TOKENS`의 여유가
  그만큼 줄고, 이는 문서화된 실패 모드(잘림 → 분할 → 호출 폭증)에 직접 닿는다.
- **재현성 5%** 이므로 4/20·2/20 차이는 잡음 이상이다. 다만 폭이 크지 않다.
- **방향이 결정적이다.** 뒤집힌 4건이 **전부 `true → false`(분리 방향)** 였고,
  2런 합산 병합률이 17.5% → 2.5%로 **약 7배 줄었다.**

뒤집힌 사례(전부 high가 분리 판정):

| # | A | B |
|---|---|---|
| 8 | 포항 철강노조, 산업용 전기요금 지역별 차등제 조기 시행 촉구 | 정부, 산업용 지역별 차등 전기요금제 설계안 공개 |
| 11 | 기장군, 한수원 사장과 고리원전 계속운전 및 i-SMR 현안 논의 | 기장군, 한수원에 사용후핵연료 건식저장시설 상생안 및 SMR 소통 요구 |
| 14 | 포항 철강노조, 산업용 전기요금 지역별 차등제 조기 시행 촉구 | 지역별 전기요금 차등제 논의 구체화 |
| 16 | 정부, SMR·핵융합 등 '7대 시드' 육성 추진 | 이재명 대통령, SMR 등 미래 핵심 산업에 메가프로젝트급 투자 강조 |

8·14는 high가 옳아 보이고, 11·16은 high가 **틀려 보인다**(같은 회의/같은 발표의 다출처 보도).
**정답지가 없으므로 어느 쪽이 맞는지 이 데이터로는 말할 수 없다.** 캐시의 `same_event`는
과거 모델 자신의 판정이지 정답이 아니다.

**→ 이것이 Gold Set이 절차가 아니라 필수인 이유다.** 그리고 방향이 과소병합 쪽이므로,
이 시스템의 문서화된 증상(과소병합)과 **정면으로 충돌한다.**

---

## 2. 프로브가 바꾼 결론

### 2.1 🟢 확증 — "기본 thinking이 꺼져 있다"

원안이 맞다(§1.2). 현행 프로덕션 생성 호출은 사실상 전부 thought=0이다.
따라서 "reasoning을 쓸 수 있는데 안 쓰고 있다"는 문제 제기 자체는 정당하다.

### 2.2 🔴 뒤집힘 — "관계판단을 medium/high로 올리면 좋아진다"

**실측은 반대 방향을 가리킨다**(§1.4). high는 병합률을 17.5% → 2.5%로 떨어뜨린다.
`issue_review`의 실패 의미론은 이미 *판정 없음 = 병합 안 함(false split)* 이고,
사용자가 보고한 증상도 과소병합이다. **같은 방향으로 편향을 하나 더 얹는 셈이다.**

원안의 초기 후보값 중 다음은 **근거 없음(unsupported)** 으로 강등한다:

| task | 원안 후보 | 이 문서의 판정 |
|---|---|---|
| SIMPLE_EXTRACT | minimal | ✅ 현행과 동일(thought 0). 명시화만 하면 됨 |
| BULK_CURATION | minimal/low | ⚠️ 3.5-flash-lite에서 두 값이 **동일**(§1.1-3). 구분 무의미 |
| IDENTITY_REVIEW | medium | ⛔ **보류.** 실측이 악화 방향(§1.4). Gold Set 필수 |
| CONTEXT_SYNTHESIS | medium | ⛔ 보류. 측정 없음 |
| NARRATIVE_GENERATION | low/medium | ⛔ 보류. 서술 품질은 자동 측정 불가 |
| FINAL_SEMANTIC_VERIFY | high | 🟡 조건부. verify는 "분리·거부 편향"이 안전 방향이므로 유일하게 방향이 맞다. 그래도 Gold Set 후 |

### 2.3 🟢 확증 — 블랙리스트 재검토 필요

`gemini-3.5-flash-lite`는 `thinkingBudget: 0`만 거부하고 `thinkingLevel`은 전부 받는다(§1.1-2).
현재의 모델 단위 블랙리스트를 유지하면 이 모델에 reasoning을 영원히 못 건다. 원안 §2가 옳다.

---

## 3. 현행 LLM 호출 지도 (origin/main = ab0a82f)

### 3.1 프로덕션 생성형 호출 — 14개 지점

`model` 열: `MODEL` = `GEMINI_MODEL`(기본 `gemini-3.1-flash-lite`),
`SYNTH` = `synthesis_model()`(기본 `gemini-3.5-flash-lite`), `REVIEW`/`INSIGHT`/`SCRIPT` 동일 기본값.
`thinking` 열은 **실제 전송되는 것** 기준이다.

| # | 모듈:라인 | label | 분류 | model | temp | maxOut | timeout | retries | thinking | 캐시 | 실패 시 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `news_bot.py:2039` | `curation` / `curation:재생성` | BULK_CURATION | MODEL | 0.2 | 32768 | 150s | 3 | 없음(=off) | 없음 (chunk 15) | chunk 분할 후 전건 fallback |
| 2 | `issue_review.py:485` | `issue_review` | IDENTITY_REVIEW | REVIEW | 0.0 | 16384 | 60s | 3 | 없음 | `issue_llm_reviews.json` (10,074) | **병합 안 함**(false split) |
| 3 | `keei_match.py:208` | `keei_match` | IDENTITY_REVIEW | **MODEL** ⚠️ | 0.0 | 8192 | 60s | 3 | 없음 | `keei_llm_matches.json` (536) | 캐시 안 함, 다음 빌드 재시도 |
| 4 | `issue_insight.py:372` | `issue_insight` | CONTEXT_SYNTHESIS | INSIGHT | 0.1 | 8192 | 60s | 3 | 없음 | `issue_insights.json` (190) | 빈칸 유지 |
| 5 | `dedup.py:404` | `dedup` / `dedup_final` | IDENTITY_REVIEW | MODEL | 0.05 | 6144 | 120s | 3 | 없음 | 없음 | 전량 유지(병합 안 함) |
| 6 | `daily_brief.py:277` | `daily_brief` | CONTEXT_SYNTHESIS | MODEL | 0.2 | 4096 | 120s | 3 | 없음 | 없음 | 투자 줄 생략 |
| 7 | `daily_brief.py:418` | `daily_brief_implication` | CONTEXT_SYNTHESIS | SYNTH | 0.2 | 4096 | 120s | 3 | 없음 | 없음 | 빈칸 유지 |
| 8 | `daily_brief.py:612` | `daily_brief_report` | CONTEXT_SYNTHESIS | SYNTH | 0.2 | 4096 | 90s | 3 | 없음 | 없음 | 섹션 생략 |
| 9 | `daily_lead.py:270,287,311,351` | `daily_lead` | NARRATIVE_GENERATION | SYNTH | 0.2 | 8192 | 60s | 3 | 없음 | 없음 | 빈 lead(웹이 제목 폴백) |
| 10 | `trend_insights.py:139` | `trend_insights` | CONTEXT_SYNTHESIS | SYNTH | 0.2 | 8192 | 60s | 3 | 없음 | 없음 | 기존 파일 유지 |
| 11 | `weekly_bot.py:672` | `weekly_bot` | CONTEXT_SYNTHESIS | SYNTH | 0.3 | 10000 | 120s | 3 | 없음 | 없음 | 결정론 fallback |
| 12 | `pubs_translate.py:175` | `pubs_translate` | SIMPLE_EXTRACT | MODEL | 0.1 | 8192 | 60s | 3 | 없음 | `publications.json` (`translated_version`) | 원문 제목 사용 |
| 13 | `audio_brief.py:496` | `audio_brief` | NARRATIVE_GENERATION | SCRIPT→MODEL 사다리 | 0.4 | 8192 | 120s | **6** | `budget=0` → **3.5에서 삭제됨** | mp3 캐시 | 모델 폴백 후 오디오 생략 |
| 14 | `expert_audio_brief.py:217` (`_call_structured`) | 9종 ↓ | 혼합 | primary별 사다리 | 0.0~0.35 | 6000~20000 | 150s | **4** | `budget=0` → **3.5에서 삭제됨** | mp3 캐시 | 단계별 원본 유지 |

**#14의 9개 라벨** (`primary` = 우선 버킷. `curation`=MODEL, `synthesis`=SYNTH):

| 라벨 | 분류 | primary | temp | maxOut |
|---|---|---|---|---|
| `expert_dossiers_{n}` | SIMPLE_EXTRACT | curation | 0.1 | 20000 |
| `expert_plan` | CONTEXT_SYNTHESIS | synthesis | 0.2 | 2000+800×이슈수 (≤20000) |
| `expert_script_{block}_{part}` | NARRATIVE_GENERATION | — | 0.35 | 12000 |
| `expert_script_retry_{...}` | NARRATIVE_GENERATION | — | 0.35 | 12000 |
| `expert_verify_{block}` | **FINAL_SEMANTIC_VERIFY** | curation | 0.0 | 6000 |
| `expert_repair_{block}` | NARRATIVE_GENERATION | synthesis | 0.15 | 12000 |
| `expert_verify_after_repair_{block}` | **FINAL_SEMANTIC_VERIFY** | curation | 0.0 | 6000 |
| `expert_reorder_{block}` | NARRATIVE_GENERATION | synthesis | 0.1 | 12000 |
| `expert_intro_repair_{block}` | NARRATIVE_GENERATION | synthesis | 0.1 | 12000 |

### 3.2 이번 reasoning 변경에서 제외 (원안 지시대로 목록에만 유지)

| 지점 | 종류 | 비고 |
|---|---|---|
| `embedding_pipeline.py:100` | `google.genai` SDK `embed_content` | `gemini-embedding-2`, dim 768. 정책 변경 대상 아님 |
| `audio_brief.py:636` (`_TTS_ENDPOINT`) | REST `generateContent` (TTS) | `TTS_MODELS` 사다리. voice/format 변경 금지 |

### 3.3 ⛔ 비프로덕션 — **손대지 말 것**

원안이 조사 대상으로 지목했으나 **어느 워크플로에도 없다.** 여기에 policy를 붙이면
검증 불가능한 코드만 늘어난다.

| 지점 | 도달 경로 |
|---|---|
| `scorer.py:90` (`scorer`) | `send_research.py`만 — 워크플로 없음 |
| `dedup.py:142` (`_llm_semantic_groups` / `dedup_clusters`) | `send_research.py`만 |
| `synthesize.py:156,221` (`synthesize`) | `daily_brief --social-raw` 또는 `send_research.py`. 워크플로 미사용 |

---

## 4. 원안 대비 정정

### 4.1 사실 오류 (5건)

**4.1.1 조사 대상에 비프로덕션 3곳 포함 / 프로덕션 9곳을 1곳으로 셈.** §3.3, §3.1-#14.

**4.1.2 "Fast/Expert 공통 final semantic gate를 *추가*"** — Expert에는 **이미 있다.**
`expert_audio_brief.py:1277-1292`: `VERIFY_SYSTEM` → `verification_passed()` → `REPAIR_SYSTEM`
→ verify_after_repair, 그 뒤 결정론적 `final_evidence_audit()`. 새로 만들면 3중이 된다.
**없는 쪽은 Fast뿐이다.**

**4.1.3 원안의 게이트 순서가 기존 설계 판단과 충돌.**
원안: script → 결정론 audit → semantic → 수정 → 결정론 → semantic.
`expert_audio_brief.py:1256` 주석: *"분량 게이트는 **검증 앞에** 둔다. 쓰레기 대본에 구간마다
검증·수정 호출을 쓰고 나서 버리면, 쿼터가 가장 급한 날에 정확히 두 배로 쓴다."*
→ 싼 결정론 게이트를 먼저, LLM verify를 마지막에, repair는 1라운드.

**4.1.4 "과거 날짜 오디오 대량 재생성" 위험은 현 코드에 없다.**
`audio_brief.generate()`는 `briefing["date"]` 하루만 다룬다(`audio_brief.py:953`).
`cache_verdict()`가 발송분을 `stale_sent`로 잡아 재발송을 이미 막는다(`audio_brief.py:227-238`).
잔여 위험은 `--force` 실행뿐. 원안 §13의 스펙을 대폭 줄여도 된다.

**4.1.5 "새울 회귀"가 reasoning 상향의 근거가 되지 못한다.** §1.3 — 현행 설정으로도 통과한다.

### 4.2 원안이 못 본 실제 결함 (3건)

**4.2.1 🔴 `crawl.yml`의 `Build web data`가 `GEMINI_MODEL`을 넘기지 않는다.**
`crawl.yml:405-410`은 `GEMINI_API_KEY`만, `daily-brief.yml:387-390`은 둘 다 넘긴다.
같은 `web/build_data.py`가 두 워크플로에서 다른 모델 해석을 갖는다. 운영자가 repo variable
`GEMINI_MODEL`을 바꾸면 daily-brief만 듣고 crawl은 안 듣는다 — **원안 §14가 경고한 상황이 이미 존재한다.**

**4.2.2 🔴 `GEMINI_REVIEW_MODEL` / `INSIGHT` / `SCRIPT` / `SYNTHESIS_MODEL`이 어느 워크플로에도 없다.**
`.env.example:34-37` 주석에만 있다. Actions에서는 항상 코드 기본값이다.
원안이 전제한 "제한된 model override"는 **지금 존재하지 않는 기능**이며 새로 배선해야 한다.

**4.2.3 🔴 새울 결함의 실제 위치는 `issue_review`가 아니라 `curation`이다.**
아카이브 `4da5b7ab6c225c78`(뉴시스, 2026-08-14):

- `title_kr`: **"상업운전 앞둔 새울 3호기 시운전 중 자동정지 *및* 사업기간 연장"** ← 두 사건이 한 제목
- `detail`: *"…또한, 새울 3·4호기 건설사업의 실시계획상 시행기간이 … 연장되었다.
  **이는 운영허가와 시운전 등 사업 마무리 기간이 늘어난 데 따른 것이다.**"*

**detail의 인과 귀속은 정확하다.** "또한"으로 분리했고 원인도 옳게 짚었다.
결함은 **제목 단계의 사건 병합**이고, 그것이 story/issue identity로 흘러간다.
즉 `BULK_CURATION`의 이벤트 추출 문제이지 `IDENTITY_REVIEW`의 판정 문제가 아니다.

부수 확인: 원안이 지정한 "실시계획 변경/고시 = 2026-07-31" 기사는 **아카이브에 없다**
(새울 75건 중 2026-07-30 ~ 08-19 구간 0건). → 새울 회귀는 아카이브에서 캘 수 없다.
**명시 날짜를 박은 합성 fixture**로 만들어야 하며, 그러면 그것은 "과거 실패 회귀"가 아니라
**계약 테스트**다. 문서에 그렇게 적어야 나중에 오해가 없다.
실 회귀로 쓸 수 있는 것은 `4da5b7ab6c225c78`의 **제목 병합**이다.

### 4.3 위험한 미명세 (3건)

**4.3.1 🔴 캐시 fingerprint 도입 시 "재질의 전"에 무슨 판정을 쓸지가 없다.**
`issue_review`는 판정 부재 = 병합 안 함이고 `MAX_NEW_PAIRS_PER_RUN = None`(회차당 상한 없음).
실측(`issue_review.py:150` 주석, 2026-08-05):
`candidates 205 / from_cache 185 / asked 0 / calls 0 / failed 20 {quota}`.
fingerprint를 바꾸면 1콜이 ~11콜로 뛰고, 그 회차가 429를 맞으면 **회색지대 이슈가
한꺼번에 갈라진다.** 실제로 그 사고가 있었다(팍스 원전 후속 보도 분리).
§1.4에 따르면 high 적용 시 재질의 결과가 캐시와 35% 불일치하므로 위험이 더 크다.

→ **못박을 규칙:**

| 신호 | 의미 | 동작 |
|---|---|---|
| `prompt_version` 불일치 | **hard invalid** (현행 유지) | 옛 판정 폐기, 재질의 |
| `generation_policy_fingerprint` 불일치 | **soft stale** (신규) | **옛 판정 계속 사용.** 회차당 budget 안에서만 재질의, **성공분만** 덮어쓰기, 실패 시 옛 판정 유지 |

**4.3.2 🔴 shadow의 일일 예산은 현 구조에서 강제 불가능.**
`_CALL_LOG`는 프로세스 로컬이고 워크플로 스텝마다 새 프로세스다. per-run 예산은 가능하지만
per-day는 상태 파일을 커밋해야 하는데, 이 저장소는 이미 `.git` 254MB / `curated.json` 16.7MB
압박이 있다. 게다가 shadow는 같은 모델 문자열 = **같은 쿼터 버킷**이라 "production 우선"을
프로세스 내 순서 말고는 보장할 수단이 없다.
→ **production shadow를 이번 범위에서 완전히 뺀다.** 필요하면 별도 워크플로 + 별도 API 키
(다른 프로젝트 = 다른 버킷)로만.

**4.3.3 🔴 V5 characterization과의 관계가 원안에 반대로 서술돼 있다.**
`tests/v5_harness.py:36-47`의 `FrozenUrlOpen`은 응답을
`sha256(model + canonical_json(request_body))`로 키잉한다. **요청 본문이 1바이트라도 바뀌면
`unregistered external request` AssertionError로 죽는다.**
`test_frozen_gemini_request_hash_and_call_count`는 본문에 `thinkingConfig:{thinkingBudget:0}`을
하드코딩한다.

두 fixture는 **규칙이 정반대다:**

| fixture | 규칙 |
|---|---|
| `expected.characterization_sha256` (업무 산출물 digest) | **절대 갱신 금지.** 바뀌면 회귀다 |
| request-hash 응답 맵 | 정책이 본문을 바꾸면 **반드시 갱신.** 안 하면 작업이 진행 불가 |

원안 §15의 "fixture를 무조건 갱신하지 마라"를 그대로 적용하면 둘째에서 막힌다.

---

## 5. 규모를 정하는 실측치

| 항목 | 값 | 출처 |
|---|---|---|
| archive rows | 10,206 | `archive/*.jsonl` |
| **`event_date` null** | **9,494 (93.0%)** | `event_date_precision=unknown` |
| 인과 표현 포함 행 | 496 (4.9%) | 정규식 `때문\|로 인해\|이에 따라\|여파로\|결과로\|영향으로\|탓에\|따른 것\|기인` |
| └ `summary`·`detail`에만 (출처 대조 가능) | 454 (4.45%) | 대부분 단일기사 내부 인과 — 양성 |
| └ **`implication`·`why_important`에만** (출처 문장 없음) | **36 (0.35%)** | ← 진짜 위험 지점 |
| └ 양쪽 | 6 (0.06%) | |
| `issue_llm_reviews.json` | 10,074건 / 회차 candidates ~205 / 신규 ~20 = 1콜 | |
| `keei_llm_matches.json` / `issue_insights.json` | 536 / 190 | |
| 본문 확보 | `MAX_BODY_FETCH_PER_RUN=80`, `MIN_BODY_CHARS=220` — best-effort | `article_body.py:57-65` |
| crawl 런타임 | 완주 최대 29.5분 / 단계 최댓값 합 ~35분 / **timeout 45분 (여유 ~10분)** | `crawl.yml:120-134` |
| daily-brief · weekly | **`timeout-minutes` 없음** (의도적) | `daily-brief.yml:96` |
| expert audio 호출 | ≈ 13~20콜/일 (dossier 3 + plan 1 + script 4~5 + verify 2 + 보정 0~6) | |
| **issue_review high 비용** | **latency ×4.0, thought +5,100/배치** | §1.4 |

**이 숫자들이 원안 §9/§11의 결론을 바꾼다.**
인과 트리거는 4.9%라 폭증하지 않는다. 그러나 4.45%의 대부분은 출처가 지지하는
단일기사 내부 인과("폭염으로 실적 하향")이고, 원안이 겨냥한 위험 class(서로 다른 두 사건
사이의 인과)는 정규식으로 갈리지 않는다. **지금 조건부 relation validation을 붙이면
하루 ~25콜을 대부분 양성에 쓴다.**

그리고 `event_date` 93% null이므로 원안 §12의
*"원인 사건일 > 결과 결정일이면 hard fail"* 결정론 체크는 실효 발동률이 사실상 0이다.
넣는 것은 무해하지만 **causality의 안전망으로 계산하면 안 된다.**

---

## 6. 실행 계획

각 단계는 **독립 커밋**. 각 단계의 수용기준을 통과하지 못하면 다음 단계로 가지 않는다.
문제 발견 시 큰 리팩터링으로 우회하지 말고 최소 수정한다.

### 6.0 전제 — 시작 지점

```bash
git fetch origin && git checkout -b feat/gemini-reasoning origin/main   # ab0a82f
```

로컬 `refactor/v5-checkpoint`에는 `tests/test_v5_characterization.py`, `tests/v5_harness.py`,
`tests/v5_characterize.py`, `tests/fixtures/v5_characterization.json`이 **없다**. main에만 있다.
이 하네스 없이 작업하면 §4.3.3의 계약을 검증할 수 없다.

기준선 확인:

```bash
GEMINI_API_KEY="" python -m unittest discover -s tests
GEMINI_API_KEY="" python -m unittest discover -s web/tests
```

---

### Stage A — wrapper 현대화 + 배선 (동작 보존)

**목표:** 요청 본문을 **하나도 바꾸지 않으면서** `thinking_level`을 받을 수 있게 만든다.

#### A-1. `gemini_client.call_json` 확장

- `thinking_level: str | None = None` 추가. 허용값 `{"minimal","low","medium","high"}`.
- `thinking_level`과 `thinking_budget` 동시 지정 → **API 호출 전** `GeminiConfigError` (신규,
  `GeminiError` 하위). `_record_call`·`_pace`보다 앞에서 던져 쿼터를 태우지 않는다.
- 허용값 밖의 `thinking_level` → 같은 `GeminiConfigError`.
- 전송 형식: `generationConfig.thinkingConfig.thinkingLevel = <값>`.
- **`thinking_level`이 명시된 호출에서 400을 받으면 `thinkingConfig`를 벗고 재시도하지 않는다.**
  `GeminiConfigError`로 즉시 올린다. (legacy `thinking_budget` 경로의 400 fallback은 그대로 둔다.)
- `thinking_level=None`이면 **필드를 아예 만들지 않는다** → 기존 호출자의 본문 무변경.

#### A-2. 블랙리스트를 (모델, 필드) 단위로

```python
# 실측 2026-09-06 (docs/2026-09-06-gemini-reasoning-plan.md §1.1)
#   gemini-3.5-flash-lite  thinkingBudget=0   -> 400 INVALID_ARGUMENT
#   gemini-3.5-flash-lite  thinkingBudget=512 -> 200
#   gemini-3.5-flash-lite  thinkingLevel=*    -> 200 (4개 값 전부)
# 거부되는 것은 '값 0'이지 thinkingConfig 필드 자체가 아니다.
_THINKING_BUDGET_ZERO_UNSUPPORTED = frozenset({"gemini-3.5-flash-lite"})
```

- 기존 `_THINKING_CONFIG_UNSUPPORTED_MODELS`를 위 이름으로 대체하고,
  **`thinking_budget == 0`일 때만** 필드를 생략한다. `thinkingLevel`에는 적용하지 않는다.
- 미지 모델의 400 fallback(`_rejects_thinking_config`)은 **legacy budget 경로에서만** 유지한다.
- 미지 모델에 대해 reasoning level을 임의 승격·강등하지 않는다.

#### A-3. `thinking_budget=0`이 조용히 무시되던 경로 — ⚠️ 별도 커밋

`audio_brief.SCRIPT_MODEL_DEFAULT`와 expert의 `primary="synthesis"`가 모두
`gemini-3.5-flash-lite`라, 현재 `thinking_budget=0`이 삭제된 채 나간다.
프로브(§1.1)상 이 모델의 기본값도 thought=0이므로 **결과는 우연히 같다.**
따라서 **이 단계에서는 동작을 바꾸지 않는다.** 사실만 코드 주석에 기록하고,
Stage F에서 `thinking_level="minimal"`로 명시화한다.

#### A-4. 텔레메트리 (원안 §4)

- `_CALL_LOG` 3-tuple **모양 유지**(기존 테스트 계약). 병렬로 bounded detail 리스트 추가:
  `_CALL_DETAIL: list[dict]`, 상한 `CALL_LOG_LIMIT` 재사용.
- 성공 경로에서 `usageMetadata` 캡처: `promptTokenCount`, `candidatesTokenCount`,
  `thoughtsTokenCount`, `totalTokenCount`. 그 외: task/label, resolved model,
  effective thinking(`level:<v>` / `budget:<n>` / `none`), `temperature_sent: bool`,
  latency, `finishReason`, retry count, truncation 여부, 429 종류(RPM/RPD).
- **금지:** API key, 프롬프트 원문, 기사 본문. **커밋되는 JSON에 쓰지 않는다.**
  출력처는 stdout + `GITHUB_STEP_SUMMARY` + 실패 시 artifact.
- **production JSON schema에 진단 필드를 추가하지 않는다** — 운영 telemetry와 사용자 데이터 계약을 분리.
- `format_call_stats()` 기존 출력 포맷 유지, 뒤에 한 줄 추가.

#### A-5. 배선 수정 (§4.2.1, §4.2.2)

- `.github/workflows/crawl.yml` `Build web data` step env에
  `GEMINI_MODEL: ${{ vars.GEMINI_MODEL || 'gemini-3.1-flash-lite' }}` 추가.
- `crawl.yml`(`Build web data`)·`daily-brief.yml`(`Build web data`, `Generate audio briefings`)·
  `weekly.yml`(`--plan`)에 다음을 추가. **기본값은 코드가 갖고, 워크플로는 빈 값이면 통과시킨다:**

  ```yaml
  GEMINI_REVIEW_MODEL: ${{ vars.GEMINI_REVIEW_MODEL }}
  GEMINI_INSIGHT_MODEL: ${{ vars.GEMINI_INSIGHT_MODEL }}
  GEMINI_SYNTHESIS_MODEL: ${{ vars.GEMINI_SYNTHESIS_MODEL }}
  GEMINI_SCRIPT_MODEL: ${{ vars.GEMINI_SCRIPT_MODEL }}
  ```

  ⚠️ `gemini_client._resolve`는 빈 문자열을 `or default`로 떨어뜨린다(`gemini_client.py:105-118`).
  위 형태는 안전하다. **확인 테스트를 반드시 넣을 것.**
- `.env.example`의 주석 처리된 4줄은 그대로 두되 "Actions에서는 repo variable로 설정" 한 줄 추가.
- **reasoning level을 repository variable로 흩뿌리지 않는다.** 코드 policy + 제한된 model override만.

#### A-6. 진단 로그

각 엔트리 스크립트 시작 시 1줄:
`[gemini] policy — MODEL=… SYNTH=… REVIEW=… INSIGHT=… SCRIPT=… RPM_CAP=…`
로컬과 Actions에서 같은 형식으로 보이게 한다.

#### Stage A 산출물

**수정 파일:** `gemini_client.py`, `.github/workflows/{crawl,daily-brief,weekly}.yml`,
`.env.example`, `tests/test_gemini_thinking_config.py`(확장), `tests/test_gemini_client.py`

**추가 테스트 (전부 오프라인):**

1. `thinking_level="medium"` → 본문에 `generationConfig.thinkingConfig.thinkingLevel == "medium"`
2. 4개 허용값 각각 정확히 전송 / 그 외 값은 `GeminiConfigError`
3. `thinking_level` + `thinking_budget` 동시 → **호출 0회**로 `GeminiConfigError`
   (`_CALL_LOG` 길이 0으로 검증)
4. `thinking_level` 명시 상태에서 400 → `thinkingConfig` 제거 재시도 **없음**, 호출 1회
5. legacy `thinking_budget=0` + `gemini-3.1-flash-lite` → 기존대로 전송 (기존 테스트 유지)
6. legacy `thinking_budget=0` + `gemini-3.5-flash-lite` → 기존대로 생략, retry 라벨 없음 (기존 유지)
7. **`thinking_level="high"` + `gemini-3.5-flash-lite` → 필드가 전송된다** (신규 — 블랙리스트가 안 먹음)
8. 미지 모델 + legacy budget → 기존 400 fallback 유지 (기존 유지)
9. `thinking_level=None` → `generationConfig`에 `thinkingConfig` 키 부재
10. detail telemetry에 API key·프롬프트 원문이 없음
11. `GEMINI_REVIEW_MODEL=""` (빈 env) → 코드 기본값으로 해석됨

**수용기준:**

- `python -m unittest discover -s tests` 및 `web/tests` 전부 통과
- **`expected.characterization_sha256` 불변**
- **`tests/fixtures/v5_characterization.json`의 request-hash 맵 불변** — A는 본문 무변경이 계약이다.
  하나라도 바뀌면 A의 구현이 틀린 것이다.

**중단조건:** request-hash가 바뀌면 되돌아가서 조건부 전송을 고친다. **fixture를 고치지 않는다.**

---

### Stage B — policy layer + Gold Set + 오프라인 평가기 (A와 **병행 착수**)

> ⚠️ **이 단계가 크리티컬 패스다.** 사람의 라벨링 없이는 Stage D/F가 못 간다.

#### B-1. `llm_policy.py` (신규, stdlib만, 도메인 모듈 import 0)

```python
@dataclass(frozen=True)
class TaskProfile:
    task: str                       # SIMPLE_EXTRACT / BULK_CURATION / IDENTITY_REVIEW
                                    # / CONTEXT_SYNTHESIS / NARRATIVE_GENERATION
                                    # / FINAL_SEMANTIC_VERIFY
    model_resolver: Callable[[], str]
    thinking_level: str | None      # None = 필드 미전송 (= 현행)
    sampling_mode: str              # "explicit" (현행) | "default" (필드 미전송)
    strict_reasoning: bool          # True면 downgrade 대신 실패
```

- **초기 배포값은 전부 현행과 동일**: 모든 profile `thinking_level=None`, `sampling_mode="explicit"`.
  즉 **policy layer 도입 자체가 no-op**이어야 한다. 그래야 request hash가 안 바뀐다.
- **RPM/quota와 결합 금지.** `_pace`·`_CALL_LOG`·Retry-After·daily-vs-minute 판정은 손대지 않는다.
- 각 호출 지점은 `llm_policy.profile("issue_review")` 한 줄만 참조한다.
  **파일마다 `thinking_level="medium"`을 흩뿌리지 않는다.**

#### B-2. Gold Set — `tests/fixtures/gold_pairs.json` (신규)

- 모집단: `issue_llm_reviews.json` 10,074건. 밴드 `REVIEW_BAND_LOW=0.84` ~ `REVIEW_BAND_HIGH=0.92`
  층화 표본.
- ⚠️ **캐시의 `same_event`는 정답이 아니다** — 과거 모델 자신의 판정이다.
  그대로 쓰면 "옛 정책과의 일치도"를 재는 것뿐이다. **사람이 재판정해야 한다.**
- 목표 120~200쌍, 원안 §6의 a~g 7카테고리 균형, positive/negative 균형:

  | 코드 | 카테고리 | 기대 |
  |---|---|---|
  | a | 같은 사건 다출처 | merge |
  | b | 같은 원전, 다른 사건 단계 | separate |
  | c | 동일 issue의 실제 후속 보도 | continuity 유지 |
  | d | story vs broader issue 혼동 금지 | separate |
  | e | 팍스 원전 등 과거 실제 over/under merge | 사례별 |
  | f | 고리 계속운전 — 표현 달라도 같은 사건 | merge |
  | g | 새울 3·4 실시계획 변경 vs 새울3 자동정지 | separate |

- **새울(g)은 합성 계약 fixture**로 별도 파일 `tests/fixtures/saeul_contract.json`.
  이유: 2026-07-31 고시 기사가 아카이브에 없다(§4.2.3). 명시 날짜를 박아 만든다.
  - `TEMPORAL_ERROR + CAUSALITY_ERROR` **FAIL** 케이스:
    "8/11 자동정지 때문에 7/31 사업기간이 연장됐다"
  - **PASS** 케이스: "새울 3·4호기 사업기간 변경과 새울3 자동정지는 별도 사안"
  - scope 3분할: 새울3 / 새울4 / 새울3·4 전체사업
- **실제 회귀**로 추가할 것: `4da5b7ab6c225c78`의 제목 병합
  ("자동정지 **및** 사업기간 연장" 한 제목) — 이것은 `BULK_CURATION` 쪽 회귀다.

#### B-3. 오프라인 평가기 — `tools/llm_eval.py` (신규)

```bash
python tools/llm_eval.py --task IDENTITY_REVIEW \
    --config none --config level:medium --config level:high \
    --repeat 3 --out <tmpdir>
```

- `GEMINI_API_KEY`가 있을 때만 실행. **CI 필수 조건으로 만들지 않는다.**
- **프로덕션 상태·캐시를 절대 쓰지 않는다.** 출력은 `--out` 임시 디렉터리로만.
- config별 보고 지표 (원안 §6 그대로 — **전체 accuracy 하나로 합치지 않는다**):
  `false_merge`, `false_split`, `stage_error`, `scope_error`, `causality_error`,
  `temporal_error`, `json_failure`, `latency_p50/p95`, `thought_tokens`,
  `truncation`, `retry`, **`repeat_consistency`**
- §1.4에서 재현성이 5%로 측정됐으므로 `--repeat 3` 이상을 기본으로 한다.

#### B-4. CI 결정론 테스트 (`tests/test_llm_policy.py` 신규)

profile resolution / request body / strict no-downgrade / failure behavior.

**수용기준:** 전체 테스트 통과 + **request-hash fixture 불변**(B도 no-op이어야 한다).

**중단조건:** 사람 라벨링이 끝나기 전에는 Stage D/F로 넘어가지 않는다.

---

### Stage C — cache policy fingerprint (soft-stale)

**C-1.** `llm_policy.generation_policy_fingerprint(profile, prompt_version) -> str`
= `sha256(resolved_model | effective_thinking | sampling_mode | prompt_version)[:16]`

**C-2.** 두 종류의 무효화를 분리한다 — §4.3.1의 표를 그대로 구현.

**C-3.** 회차별 refresh budget: `POLICY_REFRESH_BUDGET_PER_RUN = 20` (모듈 상수, 조정 가능).
`MAX_NEW_PAIRS_PER_RUN = None`(신규 쌍 상한 없음)은 그대로 둔다 — 둘은 다른 예산이다.

**C-4.** task별 기존 무효화 의미론 보존:
`issue_review.REASK_OVERLAP_RISE = 0.10`, `keei_match`의 제목 변경 재질의.
**공통 wrapper가 이것을 덮어쓰면 안 된다**(`llm_cache.py` docstring의 명시적 설계).

**C-5.** 오래된 비활성 cache는 **역사 데이터로 그대로 둔다.** 새 policy로 전부 재작성하지 않는다.
필요하면 별도 manual backfill 도구로 최근 활성 범위만 점진 재검증한다.

**추가 테스트:**

- fingerprint 계산 안정성 (같은 입력 → 같은 값)
- soft-stale 항목이 **재질의 전에도 그대로 반환**된다
- refresh budget 초과 시 **추가 API 호출 0**
- budget 안의 재질의가 실패하면 옛 판정이 살아남는다
- `prompt_version` 불일치는 여전히 hard invalid (기존 테스트 유지)
- legacy cache lazy migration

**수용기준:** 전체 통과 + fingerprint 도입만으로 회차당 호출 수가 늘지 않음(budget=0으로 검증)

---

### Stage D — 관계판단 task 제한 상향 — ⛔ **Gold Set 결과 없이는 실행 금지**

**전제:** §1.4가 high의 방향을 **과소병합 악화** 쪽으로 측정했다. Gold Set에서
`false_split`이 **증가하지 않는다는 것이 확인된 profile만** 올린다.

**적용 순서 (호출량 적은 것부터):**

1. `issue_review` (회차 ~1콜) — `IDENTITY_REVIEW`
2. `keei_match` (하루 ~1콜) — ⚠️ 현재 **`MODEL` 버킷**을 쓴다(§3.1-#3). 버킷 분리 여부를 함께 판단
3. `issue_insight` (하루 ~1콜) — `CONTEXT_SYNTHESIS`
4. `dedup` / `dedup_final` (하루 2콜)

**보존해야 할 실패 의미론:**

- `issue_review` 실패 = 병합 안 함(false split) — 현행 유지
- `issue_insight` 실패 = 빈칸 유지 — 현행 유지
- `keei_match` 실패 = 캐시하지 않음, 다음 빌드 재시도 — 현행 유지

**런타임 확인:**

- crawl 여유는 ~10분뿐이다(§5). `issue_review` high는 배치당 +13초(§1.4).
  정상 회차(1콜) +13초는 무해하나, C의 refresh budget 20건이 겹치면 +2~4분이다.
  **budget과 thinking level을 동시에 올리지 않는다.**
- daily-brief·weekly는 `timeout-minutes`가 없으므로 지연 감시는 **crawl에만** 건다.
- 적용 전후 최근 GitHub Actions 실측 런타임과 429/truncation을 비교한다.
- **크롤 완료율이 떨어지면 해당 profile을 되돌리거나 conditional escalation으로 바꾼다.**

---

### Stage F — Fast/Expert semantic verify — **신규 3중이 아니라 승격 + 신설**

#### F-1. Expert — 기존 verifier 승격 (호출 수 증가 0)

`expert_audio_brief.py`의 `expert_verify_{block}` / `expert_verify_after_repair_{block}`
호출에만 적용:

- `thinking_level`을 `FINAL_SEMANTIC_VERIFY` profile에서 받는다 (초기값은 Gold Set 결과)
- `VERIFY_SYSTEM` 프롬프트에 오류 타입 10종을 명시:
  `FACT_ERROR` / `NUMBER_ERROR` / `ENTITY_ERROR` / `DATE_ERROR` / `SCOPE_ERROR` /
  `STAGE_ERROR` / `CAUSALITY_ERROR` / `TEMPORAL_ERROR` / `CERTAINTY_ERROR` / `UNSUPPORTED_INFERENCE`
- 출력은 장문 대본이 아니라 **compact structured verdict**:
  `{"passed": bool, "findings": [{"type": ..., "line": ..., "why": ...}]}`
- `primary="curation"`(= `gemini-3.1-flash-lite`) 유지. `maxOutputTokens=6000`에
  thinking이 얹히므로 **truncation 여유를 재측정**하고 필요하면 예산을 올린다
  (§1.4에서 16,384 예산에 thought 5,100이 들어갔다 — **6,000은 위험할 수 있다**).

#### F-2. Fast — verifier 신설 (최대 +3콜/일)

`audio_brief.py`에 verify 1 + 최소수정 1 + 재검증 1. Expert와 **같은 프롬프트·같은 스키마**를 쓴다.

#### F-3. 게이트 순서 — 기존 설계 존중 (§4.1.3)

```
script 생성
  → 결정론 audit (article_quality_gate.audit_spoken_script)   ← 싼 것 먼저
  → [Expert] 분량 절대한계 게이트 (기존 위치 유지)
  → semantic verifier (LLM)                                    ← 마지막
  → 지적된 문장/문단만 최소 수정 (repair 1라운드)
  → 결정론 audit 재실행
  → semantic verifier 재실행
  → 둘 다 통과해야 TTS
```

- 기존 `audit_spoken_script`를 **제거·대체하지 않는다.**
- repair는 전체 대본 재창작이 아니라 지적된 부분만.
- causal relation은 구조적 근거가 없으면 지지된 것으로 보지 않는다.
  **"두 사실이 각각 참"과 "A가 B의 원인"을 분리한다.**
- 결정론 chronology check 추가: 알려진 날짜가 양쪽에 있고 원인 사건일 > 결과 결정일이면 hard fail.
  ⚠️ **`event_date` 93% null이므로 실효 발동률은 0에 가깝다(§5). 안전망으로 계산하지 않는다.**
  코드 주석에 이 사실을 남긴다.

#### F-4. 실패 처리

- verifier가 429/timeout/`GeminiConfigError` → **오디오만 생성/발송하지 않는다.**
- explicit high가 적용되지 못했으면 default/minimal로 몰래 재실행하지 않는다(Stage A가 보장).
- 이미 발송한 오디오는 재발송하지 않는다 — `cache_verdict`의 `stale_sent`가 이미 처리한다.
- ✅ **워크플로 변경 불필요.** `daily-brief.yml`에서 텍스트 `Send`(L254)가 오디오(L435)보다
  앞이고, 이후 `Publish to subscriber channel`·`Deploy web`이 `if: always()`이므로
  "verifier 실패해도 텍스트·웹·크롤 계속"은 **이미 구조적으로 보장된다. 건드리지 말 것.**

#### F-5. audio manifest 최소 메타데이터 (원안 §13)

`semantic_gate_version`, `semantic_model`, `semantic_thinking_level`, `semantic_verdict_digest`.
`NARRATIVE_GATE_VERSION` 상향 시:

- `cache_verdict`가 새 검증을 우회하지 못하게 하되
- 발송분은 `stale_sent`로 남아 **자동 재발송되지 않는다**(현행 동작 보존)
- ⚠️ `generate()`가 `briefing["date"]` 하루만 다루므로 **과거 날짜 대량 재생성은 발생하지 않는다**(§4.1.4)

#### 추가 테스트

- semantic verifier 실패가 텍스트/웹/크롤을 막지 않음
- semantic verifier 실패 시 오디오 미발송
- 발송분 재발송 없음 (`stale_sent` 보존)
- 새울 temporal/causality 계약 (`tests/fixtures/saeul_contract.json`)
- scope: 새울3 vs 새울4 vs 새울3·4
- stage 혼동 회귀
- 근거 없는 causal assertion 거부/중립화
- chronology check가 날짜 없을 때 통과시키고, 있을 때만 fail

---

## 7. 이번 범위에서 제외

### 7.1 ⛔ Stage E (source-time verified relation evidence) — 연기

기술적으로는 깨끗하다. 훅이 이미 정확히 존재한다:

- `news_bot.refresh_evidence_manifest(article, curation, body=...)`가
  **본문이 살아 있는 스코프에서** 호출된다(`news_bot.py:1011`)
- `article_quality_gate.build_evidence_manifest`가 이미 source fingerprint 결속을 한다
- `manifest_fingerprint`는 자기 자신에 대한 self-seal이므로
  **`verified_relation` 키를 추가해도 기존 manifest는 무효화되지 않는다.
  `EVIDENCE_MANIFEST_VERSION` bump 불필요** — 원안의 선호안이 성립한다
- 캐시 히트 시 기존 manifest를 반환하므로 **lazy migration이 공짜로 된다**

그럼에도 지금 하지 않는 이유:

1. 본문 확보가 best-effort다 (`MAX_FETCH_PER_RUN=80`, Google News 인터스티셜 우회 의존)
2. 인과 트리거 4.45%의 **대부분이 출처 지지 양성**이다(§5)
3. 진짜 위험 class(두 사건 사이 인과)를 정규식이 못 가른다

→ 지금 붙이면 하루 ~25콜을 대부분 헛돈다.

**대신 지금 할 것 — 추가 LLM 호출 0:**
`implication`·`why_important`에만 인과 표현이 있는 **0.35%(하루 1~2건)를 결정론적으로 중립화**한다.
여기가 "출처 문장이 없는데 인과를 주장한" 정확한 자리다. 효과를 측정한 뒤 E를 다시 판단한다.

### 7.2 ⛔ Stage G / H (Weekly·Trend 최적화, BULK_CURATION 조건부 escalation) — 연기

escalation trigger rate를 먼저 재자는 원안의 판단이 옳다. Stage 0~F 결과 없이는 못 정한다.

⚠️ 단, §4.2.3에 따르면 **새울 결함의 실제 위치가 BULK_CURATION이므로** 이 stage의 우선순위는
원안이 매긴 "마지막"보다 높을 수 있다. Gold Set에 제목 병합 회귀를 넣어 두면 그때 판단할 수 있다.

escalation trigger 후보(측정 후 결정): must_read 경계 / 높은 policy_materiality /
event date·stage 충돌 / source와 summary의 관계 위험 / story identity 애매성 /
causal wording 포함 / quality gate 경고.

### 7.3 ⛔ production shadow — 이번 범위에서 완전 제외 (§4.3.2)

per-day 예산 강제가 불가능하고 같은 쿼터 버킷을 잠식한다.
필요하면 별도 워크플로 + 별도 API 키(다른 프로젝트)로만.

### 7.4 ⛔ temperature 실험 — 이번 범위에서 완전 제외

- 현재 호출 지점의 temperature는 `0.0 / 0.05 / 0.1 / 0.15 / 0.2 / 0.3 / 0.35 / 0.4` **8종 27곳**이다.
  "explicit low temperature 제거"는 하나의 실험이 아니라 **27개의 개별 결정**이다.
- `temperature=None`(필드 미전송) 옵션은 wrapper에 **만들어만 두고**(`sampling_mode="default"`),
  **어떤 profile도 사용하지 않는다.**
- reasoning 변경과 sampling 변경을 같은 실험에 섞지 않는다.
- 2.5 계열·TTS·embedding에 전역 변경을 하지 않는다.

---

## 8. 절대 변경 금지

- 뉴스 수집 source 범위 / crawl 주기·trigger·watchdog
- story similarity threshold (`REVIEW_BAND_LOW=0.84`, `REVIEW_BAND_HIGH=0.92`)
- ranking 점수·선정 기준 (`ranking_config.json`)
- Telegram outbox 원자성 / channel subscription 동작
- web data contract
- embedding 모델·캐시 구조 (`gemini-embedding-2`, dim 768)
- TTS voice·audio format (`TTS_MODELS`, `CHUNK_SPOKEN`)
- Cloudflare 배포 구조 / admin override semantics
- 기존 결정론 quality gate (`article_quality_gate.audit_spoken_script` 외)
- **원문 본문 비저장 원칙** (2026-07-31 판단, 코드·테스트·공개문구 3곳 고정)
- `_CALL_LOG` tuple 모양, RPM pacing, Retry-After, daily-vs-minute quota 판정

**이번 작업 때문에 필요하지 않은 리팩터링은 하지 않는다.**

---

## 9. 완료 판정 체크리스트

구현 종료 선언 전, `origin/main` 기준 전체 diff를 다시 읽고 확인한다.

- [ ] reasoning을 올리지 않아야 할 task까지 바뀌지 않았는가 (§3.3 비프로덕션 3곳 무변경)
- [ ] 프로덕션 LLM 호출 수가 예상치 않게 증가하지 않았는가 (Stage별 예상 delta 명시)
- [ ] cache migration이 폭발적 재호출을 만들지 않는가 (refresh budget 테스트)
- [ ] shadow가 없는가 (§7.3)
- [ ] high 요청이 silent minimal/default로 내려가지 않는가 (Stage A 테스트 4)
- [ ] temperature 변경과 reasoning 변경이 섞이지 않았는가 (§7.4)
- [ ] source-derived fact와 LLM-generated interpretation이 다시 evidence로 세탁되지 않는가
- [ ] causal relation이 "두 사실이 존재한다"는 이유로 승인되지 않는가
- [ ] Fast와 Expert 오디오가 같은 최종 안전 원칙을 적용받는가
- [ ] `expected.characterization_sha256` **불변**
- [ ] request-hash fixture 변경이 **정책 변경으로 설명되는가** (변경 사유를 커밋 메시지에 기록)
- [ ] `python -m unittest discover -s tests` / `web/tests` / node contracts 전부 통과
- [ ] live Gemini API가 CI 성공 조건이 아닌가
- [ ] crawl 예상 런타임이 45분 안인가 (Stage D 적용분 포함)

**최종 보고 형식:**

```
현행 LLM 호출 지도 / 변경 파일 / task별 최종 reasoning policy / Gold Set 결과 /
기존 대비 품질 변화 / 호출·토큰·지연 변화 / cache migration 방식 / 새울 회귀 결과 /
전체 테스트 결과 / production 활성화한 항목 / 아직 보류한 항목 / 잔여 위험
```

> ⚠️ **Gold Set 또는 실측이 충분하지 않은 profile은 production 활성화하지 않는다.**
> "medium/high가 이론적으로 더 좋다"는 이유만으로 적용하지 않는다.
> §1.4는 그 이론이 이 시스템에서 **틀릴 수 있음**을 실측으로 보여 준다.

---

## 부록 A — Stage 0 프로브 재현

`tools/gemini_capability_probe.py`로 커밋해 두면 모델 교체 시마다 재실행할 수 있다.
핵심은 다음 세 가지를 한 번에 재는 것이다:

1. **수용 행렬** — 모델 × {필드 없음, `thinkingBudget` 0/512, `thinkingLevel` 4값}
   → HTTP status + `thoughtsTokenCount`
2. **기본값 판별** — 난이도 있는 **실제 과제**로 "필드 없음"이 off인지 dynamic인지
   (쉬운 질문은 dynamic이어도 thought 0이 나올 수 있어 구분이 안 된다)
3. **프로덕션 배치 비용** — 실제 batch size(20쌍)로 latency·thought·판정 분포,
   **각 config 2회 이상**(재현성 측정)

```python
# 요청 형태 (stdlib urllib, 키는 헤더로 — 쿼리스트링 금지)
body = {
    "system_instruction": {"parts": [{"text": SYS}]},
    "contents": [{"role": "user", "parts": [{"text": USR}]}],
    "generationConfig": {
        "temperature": 0.0,
        "maxOutputTokens": 16384,
        "responseMimeType": "application/json",
        "thinkingConfig": {"thinkingLevel": "high"},   # 또는 {"thinkingBudget": 0}, 또는 생략
    },
}
req = urllib.request.Request(
    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json", "x-goog-api-key": KEY})
# 읽을 것: usageMetadata.{promptTokenCount, candidatesTokenCount,
#                        thoughtsTokenCount, totalTokenCount}
#          candidates[0].finishReason
```

**주의:**

- 호출 간 **4.5초 이상 대기**(15 RPM 한도, `GEMINI_RPM_CAP=12` 페이싱과 별개).
- `gemini-3.5-flash`는 **20 RPD**이므로 프로브에 2콜 이상 쓰지 않는다.
- 프로덕션 캐시·상태 파일을 읽기만 하고 **쓰지 않는다**.

**2026-09-06 실행 비용:** 총 24콜 —
`gemini-3.1-flash-lite` 9 / `gemini-3.5-flash-lite` 13 / `gemini-3.5-flash` 2.
