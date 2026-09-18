# Gemini reasoning P4 source-complete evidence protocol

작성일: 2026-09-19

상태: protocol 구현 완료, source-complete eligible case 0건, P4 `HALTED` 유지

## 1. false PASS 감사 반영

기존 false PASS 16행/6 case 감사 결과를 그대로 적용했다. 기존 Curation calibration
20 Gold는 삭제하거나 label을 바꾸지 않고 `HISTORICAL_ONLY`로 보존한다. 동시에 당시
description/body, 정확한 production article object, serialized request, raw output,
parser lifecycle이 완전하지 않으므로 전부 `UNSCORABLE_MISSING_EVIDENCE`이며 새 calibration
pool에는 들어가지 않는다.

이 분리는
`tests/fixtures/gemini_reasoning/source_complete/historical_registry.json`에 고정했다.
기존 `curation_gold.json`과 calibration 결과 파일은 변경하지 않았다.

## 2. 계약과 저장 구조

구현: `tools/source_complete_evidence.py`

case는 다음 두 조건을 모두 만족해야 한다.

1. 평가용으로 사전에 선택된 소수 case다.
2. in-memory completeness gate를 통과한 뒤에만 영구 저장한다.

저장소는 content-addressed 구조다.

```text
<evidence-store>/
  cases/<case_id>.json
  blobs/body/<sha256>.txt
  blobs/request/<sha256>.json
  blobs/response/<sha256>.json
  blobs/model-output/<sha256>.json
```

동일 body/request/response는 SHA-256 blob 하나를 공유한다. case manifest에는 정확한
hash·byte size·상대 경로가 남는다. HTML, 이미지, 광고/네비게이션 DOM은 입력 계약에
없고 저장하지 않는다. body는 production prompt에 실제 사용된 정제 텍스트 그대로
저장하며 공백이나 newline을 정규화하지 않는다.

## 3. 영구 저장과 임시 저장

| 구분 | 대상 | 보존 |
|---|---|---|
| 일반 production capture | 기존 경량 request/response capture | 기존 14일 retention 유지 |
| 임시 calibration 후보 | risk-balanced selection 전후 후보 | 21일 권장, 기존 14일보다 길게 운영하려면 별도 artifact로 분리 |
| 영구 evidence | completeness gate를 통과하고 실제 calibration/P4에 채택된 case만 | content-addressed store에 영구 보존 |
| historical Gold | 기존 20건과 calibration 결과 | label/history 보존, active pool에서는 제외 |

현재 workflow의 기존 capture retention은 14일이다. 이번 구현은 workflow나 production
capture 설정을 변경하지 않았다. 21일 임시 후보 정책은 별도 evidence artifact를 도입할
때 적용할 protocol 값이며 production capture retention을 묵시적으로 늘리지 않는다.

## 4. case별 필수 evidence

### Source provenance

- case/source hash, canonical URL, publisher/domain
- published/captured timestamp, source type, provenance version
- title, description, production prompt에 실제 사용된 body
- body origin, extraction method/version, exact body SHA-256

### Production input/request

- 호출 당시 exact article object
- reports/context 전체(빈 배열도 명시)
- batch hash 순서와 case position
- request builder fingerprint
- API 직전 serialized request의 canonical JSON blob과 fingerprint

### Production output

- provider response blob과 raw model output
- parsed `items`, case의 normalized output
- parser result
- regeneration/split/quarantine/lost 상태
- production validation 결과

### Judge-visible evidence

judge가 볼 수 있는 필드는 고정된 `evaluation_evidence.included_fields`에 명시한다.

- source title/description/body
- publisher/domain/published_at
- normalized production output

목록 밖의 정보는 judge 근거로 사용할 수 없다. selection metadata에는 Gold label,
verdict, answer를 넣을 수 없다.

## 5. completeness fail-closed

다음 중 하나라도 성립하면 case는 저장/평가 대상이 아니다.

- 필수 source provenance/content가 비었음
- title만 있고 description 또는 body가 없음
- exact article object, reports/context, batch mapping이 없음
- article hash/position과 serialized request의 tag/title/description/body가 불일치
- request builder fingerprint 또는 request canonical fingerprint가 없음
- raw output의 `items`와 parsed items가 다름
- source hash가 parsed item에 정확히 한 번 대응하지 않음
- parser/production validation이 `PASS`가 아님
- quarantine 또는 lost 상태
- evaluation evidence subset이 고정 목록과 다름
- API key, Authorization, cookie, token 등 secret key/value 패턴 포함
- content-addressed blob의 hash/size 불일치

결과는 `SOURCE_COMPLETE_CALIBRATION_ELIGIBLE` 또는
`UNSCORABLE_MISSING_EVIDENCE`다. 불완전 case에 PASS/REPAIR/BLOCK을 만들지 않는다.

## 6. 기존 calibration 경로 차단

`tools/curation_p4_judge.py`는 기존 title-only 요청 생성을
`historical_calibration_requests()`로 격리했다. 기본 `calibration_requests()`는
`SOURCE_COMPLETE_CALIBRATION_ELIGIBLE` evidence가 없는 기존 Gold를 0건으로 취급한다.

- 기본 manual calibration export: source-complete case가 없으면 fail-closed
- historical export/import: 테스트·과거 결과 재현용 명시적 `historical_only=True`에서만 가능
- calibration summary: `source_complete`와 `historical_only` scope를 분리
- Gemini canary: `status=PASS`뿐 아니라 `calibration_scope=source_complete`도 요구

따라서 과거 calibration이 나중에 PASS로 표시돼도 Gemini canary gate를 열 수 없다.
threshold, judge prompt, model 표기, verdict 계약은 변경하지 않았다.

## 7. 후보 선정

목표 pool은 20~30건이다. `select_balanced_candidates()`는 case ID 기반 deterministic
tie-break와 희소 risk 우선 점수를 사용한다. risk bucket은 다음과 같다.

`event_boundary`, `scope`, `stage`, `date`, `causality`,
`unsupported_inference`, `omission`, `normal_pass_like`, `historical_error_prone`

선정 metadata는 source/output의 구조적 cue만 담고 judge verdict나 Human/AI Gold를 담지
않는다. 선정은 정답이 아니며 이후 독립 judge evidence와 분리된다.

과거 capture에는 body/request/response는 있으나 호출 당시 exact article object와 parser
lifecycle이 완전하지 않다. 따라서 과거 2,366개 body 보유 표본은 크기 측정에만 썼고
eligible case로 승격하지 않았다. 실제 20~30건은 future evaluation harness가 exact inputs와
outputs를 같은 시점에 넘긴 case만 승격할 수 있다.

## 8. 저장공간 실측

측정 명령:

```text
python tools/source_complete_evidence.py estimate --capture-root .eval/gemini-reasoning-v2/captures
```

9/9 이후 capture 중 description, body, 대응 output이 모두 있는 2,366개 표본을 사용했다.
이 표본은 **size-only이며 calibration eligible이 아니다.** MiB는 1,048,576 bytes 기준이다.

| 항목 | dedup 전 | dedup 후 |
|---|---:|---:|
| case 1건 평균 | 0.0771 MiB | 0.0131 MiB |
| 20건 예상 | 1.542 MiB | 0.262 MiB |
| 30건 예상 | 2.314 MiB | 0.393 MiB |
| 100건 예상 | 7.712 MiB | 1.311 MiB |
| 2,366건 실측 | 182.459 MiB | 31.015 MiB |

- unique body 2,217개, unique request/response 각 318개
- dedup 절감률 83.0%
- dedup 후 전체에서 body 비중 17.12%

절감의 대부분은 같은 batch의 request/response를 case마다 복사하지 않는 데서 나온다.
body는 exact text hash로만 dedup하며 fidelity를 위해 유사도 기반 합치기나 정규화를 하지 않는다.

## 9. production 영향과 호출 수

- production Gemini model/reasoning/prompt/routing 변경 없음
- `FAST_SEMANTIC_GATE_ENABLED` 변경 없음
- crawl/daily/weekly/cache/deployment 변경 없음
- production 기본 경로의 write/call 동작 변경 없음
- 명시적 evaluation capture mode에만 동일 실행의 in-memory producer hook 존재
- public workflow/repository variable 배선 및 source body artifact 업로드 없음
- Human 신규 리뷰 0
- Gemini live API 호출 0
- OpenAI live API 호출 0
- 실제 source-complete eligible case 0

promotion helper에 더해 `tools/source_complete_producer.py`가 실제 `curate_batch()` 호출 시점의
exact article/context/request/output/parser state를 in-memory로 직접 전달한다. producer는
기본 off이고 이미 예정된 curation 호출을 관측할 뿐 추가 API 호출을 만들지 않는다. 불완전
candidate는 디스크에 쓰기 전에 거부한다. 과거 capture에서 빠진 값을 archive나 현재 웹으로
추정해 채우는 경로는 여전히 없다. 공개 저장소에는 body 포함 artifact를 올리지 않으며,
실제 수집 전 제한된 저장 위치와 호출 수·token·비용·hard cap 보고 및 명시적 승인이 필요하다.

## 10. P4 상태와 다음 재개 조건

P4는 계속 `HALTED`다. 다음 네 조건이 모두 충족되기 전에는 judge calibration과 Gemini
canary를 실행하지 않는다.

1. future evaluation capture에서 source-complete eligible case 20~30건 축적
2. risk coverage와 store integrity 검증 통과
3. 새 case에 대한 독립 reference truth를 정하는 별도 승인된 protocol 확정
4. 동일 evidence blob으로 judge calibration을 재실행할 수 있음을 dry-run으로 증명

source-complete fixture는 판정 가능한 evidence이지 자동 Gold label이 아니다. 3번 없이
같은 GPT judge를 자기 자신의 Gold로 사용하면 calibration 정확도를 증명할 수 없으므로
P4를 열지 않는다.

## 11. 검증 결과

- 관련 회귀: 76 passed, 100 subtests passed
- 전체 suite: 2,693 passed, 10 skipped, 1 failed, 487 subtests passed
- 유일한 실패: `web/tests/test_prototype.py`의 live data 주별 합계 비율 3.77 > 2
- 해당 실패는 이전 P4 실행에서도 동일하게 기록된 실데이터 품질 gate이며 이번 변경과 무관하다.
- source-complete preflight: active Gold 0, historical-only 20,
  `HALTED_SOURCE_COMPLETE_CALIBRATION_POOL_EMPTY`
- Gemini/OpenAI API 호출: 각 0

테스트를 통과시키기 위해 live data gate를 약화하거나 unrelated 사용자 파일을 수정하지 않았다.

## 12. producer 구현 후 검증

같은 curation 실행에서 exact state를 넘기는 producer는
`docs/2026-09-19-gemini-reasoning-source-complete-producer.md`에 기록했다.

- producer·evidence·capture·curation request 관련: 200 passed, 94 subtests passed
- 비-web 전체: 2,134 passed, 9 skipped, 489 subtests passed
- 전체 suite: 2,610 passed, 12 skipped 후 4 failed, 114 setup errors
- 전체 suite의 실패/error는 격리 worktree에 Git 비추적 생성물인
  `web/public/data/news.json`, `issue_audit.json`, `issues.json`, `publications.json`이 없는 데서 발생
- 생성물을 다른 worktree에서 복사하거나 web gate를 약화하지 않음
- `py_compile` 및 API 0회인 `source_complete_producer.py plan` 통과
- 실제 source-complete eligible은 여전히 0, Gemini/OpenAI API 호출 각 0
