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
| P2 | Capture + recorded-response fidelity | 0 | `DONE` — curation PROVEN, dedup/dedup_final NOT_PROVEN (2026-09-19) |
| P3 | Independent Gold | 0 | `DONE` — 47건 판정 완료, 재라벨 대상 0 (97019a6) |
| P4 | Sequential reasoning evaluation | 최소 | `HALTED(source-complete protocol 완료, eligible 0/20~30)` |
| P5 | Safety / operational decision | 0 | `PENDING` |
| P6 | Integration → activation | 최소 | `PENDING` |

상태값: `PENDING` / `IN_PROGRESS` / `DONE` / `HALTED(사유)` / `BLOCKED_HUMAN`

## 3. 다음 한 줄

> **P2 판정 유지. P4 source-complete protocol은 구현됐고 live 호출은 여전히 0이다.**
>
> 다음 재개 지점: future evaluation capture에서 exact article/context/request/output/parser state를
> 함께 보존한 `SOURCE_COMPLETE_CALIBRATION_ELIGIBLE` case를 risk-balanced 20~30건 축적한다.
> 현재 eligible은 0건이다. 기존 20 Gold는 `HISTORICAL_ONLY` 및
> `UNSCORABLE_MISSING_EVIDENCE`로 보존되며 active calibration/export/canary gate를 열 수 없다.
> source-complete fixture는 자동 Gold가 아니므로, 별도 승인된 독립 reference truth protocol까지
> 확정되기 전에는 judge 실행, Gemini canary, threshold/prompt/model/gate 변경을 하지 않는다.

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
- [x] profile별 판정 실행 (2026-09-19) — curation `REPLAY_FIDELITY_PROVEN`,
      dedup/dedup_final `REPLAY_FIDELITY_NOT_PROVEN`; live Gemini 호출 0
- [x] 입력 재구성기 `tools/replay_inputs.py` + provenance 판정 (1311a11)
- [x] dedup 재구성기 (6b9019a) — 15개 필드 중 13개가 저장소 복원 가능
- [x] fidelity gate `tools/fidelity_gate.py` — 캡처 → profile 판정 (다음 커밋)
- [ ] issue_review 재구성기 — **이번 범위 제외**(사양 §5: 후순위)
- [x] 자연 데이터 축적 8일 21시간 확인 (artifact 91개, JSONL 840줄, 파싱 오류 0)

### P3 — Independent Gold (API 0)
- [x] #92 Gold 도입 + `HUMAN_REVIEWED_AI_ASSISTED` 재분류 (9b089fb)
- [x] anchoring 측정용 blind 재검증 15건 결정적 선별 — `tools/gold_provenance.py`
- [x] blind 패킷 (화이트리스트 + 층 섞기 + 변이 검증) — `tools/blind_relabel.py` (657ce87)
- [x] Identity 계약 매핑 확정 — `tools/identity_contract_map.py` (51f2036)
- [x] 판정 입력 창구 `tools/review_queue.py` (febd82a) — 37건 한 자리
- [x] 1회차 37건 판정 완료 (2026-09-09) → `4dc1afa` 로 반영
- [x] 2회차 blind 10건 완료 → PASS 층 전수 13건 중 9건(69%) 뒤집힘

### P4 — Sequential evaluation (최소 API)
- [x] case-major 루프 + config 순서 randomize — `plan_jobs()` (21d18a4)
- [x] 지연 분해 `latency_seconds`(API) / `wall_clock_seconds` / `overhead_seconds` (21d18a4)
- [x] 계약 충돌 해소 — actual `curate_batch` 생성과 독립 blind judge를 분리;
      generic `llm_eval.call_contract`는 계속 닫힘
- [x] 수동 ChatGPT judge 질문지/export + strict JSON importer + policy namespace
- [x] calibration 기준 사전 고정(20 Gold × 3 repeat), 새 Human Review 0
- [x] full-size 15건 canary 입력·4 distinct arm·비용/호출 preflight 고정
- [x] judge calibration 실행 — 60/60 strict import, repeat stability/TIE 1.0
- [ ] judge calibration PROVEN — **NOT_PROVEN:** agreement 0.633, false/unsafe PASS 16
- [x] false PASS 16행 전수 감사 — 6 case, ① 1 case/3행, ② 0, ③ 5 case/13행;
      당시 원문 및 5개 canonical Gold 근거 복원 불가
- [x] source-complete evidence protocol + content-addressed dedup + completeness fail-closed 구현
- [x] 기존 20 Gold를 `HISTORICAL_ONLY`/`UNSCORABLE_MISSING_EVIDENCE`로 분리;
      default calibration export 0건, historical PASS도 canary unlock 불가
- [ ] source-complete calibration case 20~30건 축적 — 현재 0건
- [ ] 독립 reference truth protocol 승인 및 동일 evidence calibration dry-run
- [ ] full-size batch canary — calibration PASS 및 명시적 Gemini 호출 승인 전 금지
- [ ] dominated config 제거 → paired dev → finalist repeat → time-block holdout

## 4-H. P4 독립 평가계약 preflight (2026-09-19)

`tools/curation_p4.py`는 실제 `news_bot.curate_batch`와 production prompt/parser/schema,
`BATCH_CHUNK=15`, regeneration/split/quarantine을 그대로 사용한다. wrapper가 바꾸는 것은
비교 arm의 `thinking_level`뿐이며 production reasoning이 나중에 활성화되면 덮어쓰지 않고
실패한다. `tools/curation_p4_judge.py`는 candidate를 A/B/C/D로 익명화하고 case/repeat별
결정적 순서, 10차원 고정 rubric, `PASS|REPAIR|BLOCK`, 모든 pairwise를 요구한다.

judge는 OpenAI API가 아니라 수동 ChatGPT UI packet/import 방식이다. evaluator policy는
`curation-blind-chatgpt-v1`이고 API transport가 코드에 없다. calibration 질문지 3개가
생성됐으며 각각 20 case를 담는다. UI 표시 모델명과 packet/answer SHA-256을 provenance로
저장한다. malformed, case/dimension/pair 누락, verdict-dimension 모순은 packet 전체를
fail-closed로 거부한다.

Canary는 capture `10526987340` sequence 4의 source-backed 15건으로 고정했다. 위험 차원
coverage는 event boundary 13, scope 12, stage 12, date 15, causality 11이다. arm은
current/low/medium/high 4개이고 baseline과 중복된 minimal은 제외했다. 실행 순서는
medium → low → high → current다. 자연 capture 기준 예상 Gemini logical call은 7.0회,
승인 제안 cap은 arm당 3회(총 12회), 예상 비용은 USD 0.089다. 상세 보고서는
`docs/2026-09-19-gemini-reasoning-p4-preflight.md`다.

현재 live Gemini 0회, OpenAI API 0회, 새 Human Review 0건이다. production reasoning,
`FAST_SEMANTIC_GATE_ENABLED`, model routing, 서비스 동작, production cache는 바꾸지 않았다.

## 4-I. P4 manual judge calibration 결과 (2026-09-19)

`GPT-5.6 Sol` UI에서 세 독립 packet을 실행했고 20 case × 3 repeat = 60행이 모두 strict
schema와 provenance를 통과했다. 모든 case의 modal stability, identical-pair TIE,
duplicate consistency는 1.0이고 position bias는 0이었다. 그러나 PASS 대 intervention
일치율은 0.633(38/60), false PASS와 unsafe PASS는 각각 16으로 사전 기준을 실패했다.

Gold source packet이 title/date만 보존하고 원 description/body를 보존하지 않아 제목 밖의
오류를 judge가 `NOT_EVALUABLE`로 둔 것이 주요 한계다. threshold를 낮추거나 결과를 본 뒤
case를 제외하지 않는다. `JUDGE_CALIBRATION_NOT_PROVEN`으로 P4를 HALT하고 Gemini canary는
실행하지 않았다. 상세 결과는 `docs/2026-09-19-gemini-reasoning-p4-calibration.md`다.

## 4-J. P4 false PASS 16건 전수 감사 (2026-09-19)

calibration을 재실행하지 않고 false PASS 16행을 6개 고유 case로 환원해 전수 조사했다.
Gold 근거가 명확한 `curation-f158b9b4c01d6732` 3행은 source title과 생성 summary의 중심
scope 차이를 judge가 놓친 ①이다. 나머지 13행(5 case)은 canonical label이 `PASS→REPAIR`로
바뀌었지만 모든 human dimension이 `PASS`이고 `human_notes`와 `required_repair`도 비어 있어,
Human Gold의 실패 근거 자체를 복원할 수 없는 ③이다. ②로 단독 확정할 case는 0개다.

각 case의 저장소(`curated.json` 및 Gold 이력), archive 전체 revision, production/eval cache,
delivery/eval log를 순서대로 확인했다. 당시 description/body/source excerpt의 실제 텍스트는
없었다. 4건은 빈 문자열의 source excerpt 해시만, 2건은 preimage 없는 비어 있지 않은 해시만
남았다. Sol 보조 판정의 rationale과 source URL은 원문 evidence가 아니며, blind sidecar는
label만 저장한다.

따라서 기존 packet의 기계적 replay는 가능하지만 결손과 불명확한 Gold를 그대로 반복할 뿐,
`NOT_PROVEN`을 해소하는 **동일 조건의 유효한 calibration 재실행은 불가**하다. P4 `HALTED`와
Gemini canary 차단을 유지한다. 상세 표와 경로는
`docs/2026-09-19-gemini-reasoning-p4-false-pass-audit.md`에 기록했다. API 호출 0회이며 불변
설정과 실제 서비스 동작은 변경하지 않았다.

## 4-K. Source-complete evidence protocol (2026-09-19)

`tools/source_complete_evidence.py`에 평가용 case만 영구 승격하는 source-complete 계약을
구현했다. title/description/exact prompt body와 provenance, 호출 당시 article/context/batch,
serialized request, raw/parsed/normalized output, parser·regeneration·split·quarantine·lost 상태,
judge-visible evidence subset이 모두 있어야 한다. 하나라도 없거나 서로 대응하지 않으면
`UNSCORABLE_MISSING_EVIDENCE`이며 case manifest를 만들지 않는다. API key/token/cookie 패턴도
쓰기 전에 거부한다.

body/request/response/raw model output은 SHA-256 content-addressed blob으로 저장한다. 동일
batch request/response 및 동일 body는 한 번만 저장하되 exact bytes를 정규화하거나 유사도로
합치지 않는다. HTML·이미지·DOM은 저장하지 않는다. 일반 capture와 evidence store는 분리하며,
기존 capture retention 14일은 변경하지 않았다. 임시 evidence 후보의 protocol 권장 retention은
21일이고, 실제 calibration/P4에 채택된 case만 영구 보존한다.

기존 20 Gold는 별도 registry에서 `HISTORICAL_ONLY`이자
`UNSCORABLE_MISSING_EVIDENCE`로 고정했다. 기본 judge calibration request/export는 이들을 0건으로
취급하며, 명시적 `historical_only` 모드만 과거 재현에 사용한다. canary gate는 calibration
`PASS` 외에 `calibration_scope=source_complete`도 요구하므로 historical PASS로 열리지 않는다.
threshold, Gold label, judge prompt/model은 바꾸지 않았다.

9/9 이후 capture 중 body/description/output이 있는 2,366개 표본은 저장용량 측정에만 사용했다.
exact article object와 parser lifecycle이 없으므로 eligible로 승격하지 않았다. dedup 후 평균
0.0131 MiB/case, 20건 0.262 MiB, 30건 0.393 MiB, 100건 1.311 MiB 예상이며, body 비중
17.12%, dedup 절감률 83.0%다. 현재 source-complete eligible은 0건이고 P4는 `HALTED`다.
상세 계약은 `docs/2026-09-19-gemini-reasoning-source-complete-evidence.md`에 있다.

source-complete는 판정 가능한 evidence이지 자동 Gold가 아니다. future case 20~30건 축적과
별도 승인된 independent reference truth protocol 없이는 judge calibration과 Gemini canary를
실행하지 않는다. Human 신규 리뷰 0, Gemini/OpenAI live API 호출 0이며 production 동작과
cache는 변경하지 않았다.

## 4-G. P2 자연 capture 및 fidelity 결과 (2026-09-19)

repo variable `NUCLENS_LLM_CAPTURE=on`을 확인했다. 2026-09-09 이후 만료되지 않은
artifact 91개(11,805,298 bytes)를 전부 내려받았고, 빈 파일·JSON 파싱 오류는 0이었다.
artifact 생성 범위는 2026-09-09 15:57:17Z ~ 2026-09-18 13:17:59Z다.

| profile | 캡처 호출 | orchestration | artifact | 캡처 시각 범위(UTC) | 판정 |
|---|---:|---:|---:|---|---|
| `curation` | 580 (최초 337 + 재생성 243) | 337 | 91 | 09-09 15:42:45 ~ 09-18 12:44:01 | `REPLAY_FIDELITY_PROVEN` |
| `dedup` | 20 | 20 | 9 | 09-09 21:03:12 ~ 09-17 19:17:16 | `REPLAY_FIDELITY_NOT_PROVEN` |
| `dedup_final` | 18 | 18 | 9 | 09-09 21:03:17 ~ 09-17 19:17:20 | `REPLAY_FIDELITY_NOT_PROVEN` |

판정은 artifact를 만든 정확한 run commit에서 실행했다. 최신 dedup 포함 brief
artifact `10516558990` / run commit `9e7c9bf`에서 curation은 2/2 orchestration
(3 calls) PROVEN, dedup과 dedup_final은 각각 0/2 (2 calls) NOT_PROVEN이었다.
최신 curation 표본도 별도로 확인했다: brief `10522931660` / `9782592a`는 3/3
(5 calls), crawl `10548691668` / `59abee75`는 4/4 (7 calls) PROVEN이다.
해당 run commit과 현재 `origin/main` 사이 curation 계약 파일 변경은 0이다.

dedup 두 profile은 재구성한 user message가 녹화본과 달랐다. §5-B에 따라 P4에서
제외한다. curation의 `description`/`body` prompt-derived 순환 경계와 429/5xx 미포착
한계는 §4-A의 기존 제한 그대로다.

`llm_eval.TASKS` 연결은 0개다. PROVEN인 curation profile은 새 curation을 생성하는
production 계약(`items`)인 반면 `CURATION` Gold/평가기 계약은 기존 `current_output`의
등급을 판정하는 계약(`verdict`)이다. 둘을 같은 task로 연결하는 것은 active contract
모순이고, 새 judge를 임의 도입하는 것은 P0 봉쇄 및 새로운 product semantics 선택이다.
따라서 §6-5/§6-7로 P4를 정지했다.

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

## 4-F. 최종 Gold 상태 (2회차까지 반영, 확정)

**사람 판정 47건 완료** (1회차 37 + 2회차 10). 재라벨 대상 0.

### curation

| 층 | 표본 | 정정 | 결과 |
|---|---|---|---|
| PASS | 13 | **9 (69%)** | 전부 REPAIR 로. `RESOLVED_WITH_CORRECTIONS` |
| REPAIR | 6 | 0 | `CONFIRMED` (미표본 4) |
| BLOCK | 0 | — | `UNVERIFIED` (1건뿐) |

평가 가능 Gold **20건** (REPAIR 16 / PASS 4), 출처 미검증 제외 5건.

> 1회차 3/3 을 100% 로 외삽했다면 멀쩡한 4건까지 고쳤을 것이다. 전수 재라벨이
> 옳았고, 그 숫자는 테스트로 고정돼 있다.

### semantic — 근거의 종류가 다르다

| 축 | 근거 | 건수 |
|---|---|---|
| safety (PASS 하면 안 된다) | **구성** | 34 |
| severity (BLOCK 인가 REPAIR 인가) | 가려진 사람 판정 | 11 |

`unsupported_inference` 24건과 `controlled_perturbation` 10건은 오류를 **일부러
심어** 만든 것이라 개입 필요성이 사람 라벨과 무관하게 참이다. anchoring 때문에
등급은 못 믿지만 안전 게이트는 돌릴 수 있다. 두 축을 섞으면 쓸 수 있는 지표를
"Gold 부족"이라며 버리게 된다. `llm_eval.evidence_basis()` 가 이 구분을 낸다.

### identity — 검토 잔여 0

| profile | MERGE | SEPARATE |
|---|---|---|
| `issue_review` | 24 | 36 |
| `dedup` / `dedup_final` | 10 | 50 |

issue_review 는 13/47 → 24/36 으로 균형 회복. dedup 은 MERGE 10건이라 여전히
얇으므로 false merge 가 아니라 coverage·붕괴 안전 지표로 읽는다.

증거는 fixture 에 durable 하다(`superseded_label`, `human_relation`).
`.eval/` 사이드카가 없어져도 재구성된다.

## 4-E. 1회차 판정 결과 (2026-09-09, 참고)

**blind 15건 — 일치 11 / 불일치 4, 불일치가 전부 한 방향(보관 라벨이 더 관대).**

| 층 | 표본 | 정정 | 판정 |
|---|---|---|---|
| curation REPAIR | 6 | 0 | `CONFIRMED` — 나머지 4건도 살린다 |
| curation PASS | 3 | **3** | `RELABEL_REQUIRED` — 나머지 10건 재라벨 |
| semantic controlled_perturbation | 6 | 1 | 대체로 견고 |
| semantic BLOCK | 5 | 0 | `CONFIRMED` (미표본 34) |

AI 가 "문제 없음"이라 하면 사람이 그대로 승인했지만, 가리고 보니 셋 다 REPAIR 였다.
anchoring 이 **관대한 방향으로만** 작동했다.

**identity 22건 — 검토 잔여 0.** 두 profile 모두 60건 전부 판정 가능해졌다.

| profile | MERGE | SEPARATE |
|---|---|---|
| `issue_review` | 24 | 36 |
| `dedup` / `dedup_final` | 10 | 50 |

issue_review 는 13/47 이던 균형이 24/36 으로 회복됐다. dedup 은 MERGE 10건이라
여전히 얇다 — false merge 가 아니라 coverage·붕괴 안전 지표로 읽는다.

증거는 fixture 에 durable 하게 남는다(`superseded_label`, `human_relation`).
사이드카가 없어져도 재구성된다.

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

## 4-D. P4 준비 항목 — **완료** (21d18a4)

- [x] `plan_jobs()` — case-major + case 안에서 config 순서를 해시로 섞는다.
- [x] 지연 분해. **정정:** `gemini_client` 의 타이머는 `_pace()` 뒤에 시작하므로
      이미 깨끗했다 — 오염된 것은 평가기의 벽시계였다. production 은 건드리지 않고
      `latency_seconds`(API) / `wall_clock_seconds` / `overhead_seconds` 로 나눴다.
- [x] `redundant_arms()` — thought 0 인 arm 을 관측값으로 잘라낸다. 모델별 표를
      손으로 적지 않는다(모델이 바뀌면 조용히 틀린다). 관측 1건은 자르지 않는다.
- [x] `excluded_by_provenance()` — AI 보조 라벨이 평가에서 빠진 건수를 센다.
      조용히 빠지면 "Gold 가 적다"가 보고서에서 사라진다.

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
| 2026-09-09 | P3 | 판정 입력 창구(두 대기열 37건, 경계 테스트). 전체 1681 passed | `febd82a` |
| 2026-09-09 | — | 중단 후 재개. 사람 판정은 여전히 미실시 | — |
| 2026-09-09 | P4 | 호출 순서 case-major + 지연 분해 + 중복 arm 판정. 변이로 순서 편향 검증. 전체 1697 passed | `21d18a4` |
| 2026-09-09 | P3 | **사람 판정 37건 반영.** anchoring 확인(PASS 층 3/3 뒤집힘), identity 검토 잔여 0. 전체 1714 passed | `4dc1afa` |
| 2026-09-09 | P3 | 무너진 층 재라벨 큐 자동화(10건). 전체 1720 passed | `cebd9f9` |
| 2026-09-09 | P3 | **2회차 10건 반영. PASS 층 9/13 뒤집힘. 안전/등급 근거 분리.** 전체 1728 passed | `97019a6` |
| 2026-09-09 | — | daily-brief 34279339893 완료 확인 후 rebase → push | — |
| 2026-09-09 | P1 | 관측 baseline 확정 — `expert_dossiers`/`expert_verify` 는 `budget:0`(명시적 OFF), 나머지는 필드 없음. contract fingerprint 신설. 전체 1589 passed | `83942f7` |
| 2026-09-19 | P2 | capture artifact 91개·840호출·8일 21시간 확인. 정확한 run commit replay에서 curation PROVEN, dedup/dedup_final NOT_PROVEN. live Gemini 0회 | — |
| 2026-09-19 | P4 | curation 생성 응답(`items`)과 `llm_eval` 판정 응답(`verdict`) 계약 충돌을 API 호출 전에 확인. TASKS 연결·canary 금지, §6-5/7 HALT | — |
| 2026-09-19 | P4 | actual `curate_batch` + deterministic hard gate + 수동 blind ChatGPT judge로 계약 분리. 20 Gold × 3 repeat 질문지·strict importer·15건 canary preflight 구현. live Gemini/OpenAI API/신규 Human Review 모두 0. calibration JSON 3개 대기 | `159dcc7` |
| 2026-09-19 | P4 | 대상 회귀 57 passed. 전체 suite 2670 passed/10 skipped/1 failed — 실패는 기존 live web data 주별 합계 비율 3.77 > 2인 데이터 gate 1건으로 P4 무관, 수정하지 않음 | `159dcc7` |
| 2026-09-19 | P4 | 수동 judge 60/60 strict import. stability/TIE/duplicate 1.0, position bias 0이나 PASS-vs-intervention 0.633·false/unsafe PASS 16으로 NOT_PROVEN. source evidence 부족 확인, canary 차단 회귀 포함 59 passed | `2748072` |
| 2026-09-19 | P4 | false PASS 16행(6 case) 전수 감사. ① 1 case/3행, ② 0, ③ 5 case/13행. 저장소→archive→cache→log에 당시 원문 없음, 5개 canonical Gold 근거 없음으로 동일 조건 유효 재실행 불가. calibration/canary/API 호출 0 | 이번 문서 커밋 |
| 2026-09-19 | P4 | source-complete evidence protocol·content-addressed dedup·fail-closed gate 구현. 기존 20 Gold historical/unscorable 격리, active eligible 0. capture 2,366표본 용량 실측: 20건 0.262 MiB, 30건 0.393 MiB, dedup 83.0%. Human/Gemini/OpenAI 호출 0 | 이번 문서 커밋 |
| 2026-09-19 | P4 | source-complete 관련 76 passed/100 subtests. 전체 2693 passed/10 skipped/1 failed/487 subtests — 실패는 기존 live web data 주별 합계 비율 3.77 > 2 gate로 P4 무관, 수정하지 않음 | 이번 문서 커밋 |
