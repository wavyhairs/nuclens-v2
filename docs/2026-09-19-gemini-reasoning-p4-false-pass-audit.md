# Gemini reasoning P4 false PASS 16건 전수 감사

작성일: 2026-09-19

범위: P4 manual judge calibration의 false PASS 16행

결론: `JUDGE_CALIBRATION_NOT_PROVEN` 유지, calibration 재실행 및 Gemini canary 금지 유지

## 1. 감사 원칙

- calibration은 재실행하지 않았다.
- threshold, Gold label, judge prompt, judge model 표기, canary gate를 변경하지 않았다.
- production reasoning, `FAST_SEMANTIC_GATE_ENABLED`, model routing, 실제 서비스 동작을 변경하지 않았다.
- Gemini/OpenAI API 호출은 0회다.
- 원문 evidence 복원 가능성은 저장소 → archive → cache → log 순서로 확인했다.
- 분류는 서로 배타적으로 적용했다. canonical Gold의 판정 이유 자체가 복원되지 않으면 ③을 우선하고, Gold 근거가 명확할 때만 title로 충분한 ①과 원문이 필요한 ②를 구분했다.

분류:

1. ① title만으로 판정 가능했으나 judge가 놓친 경우
2. ② description/body 부재 때문에 판정 불가능한 경우
3. ③ Human Gold/근거 자체가 불명확한 경우

## 2. 모수와 집계

false PASS 16행은 6개 고유 case의 세 번 반복에서 발생했다.

| 분류 | 고유 case | false PASS 행 | 결과 |
|---|---:|---:|---|
| ① title만으로 판정 가능 | 1 | 3 | `curation-f158b9b4c01d6732` |
| ② description/body 부재로 판정 불가능 | 0 | 0 | 없음 |
| ③ Human Gold/근거 불명확 | 5 | 13 | 아래 표의 나머지 5건 |
| 합계 | 6 | 16 | calibration 집계와 일치 |

②가 0이라는 뜻은 원문 부재가 문제가 아니라는 뜻이 아니다. 5개 case에도 title 밖 주장이 있어 원문이 필요하지만, 그보다 먼저 canonical Gold가 왜 `REPAIR`인지 기록되지 않아 ③으로 분류했다.

## 3. case별 조사표

| case / false PASS | 분류와 판정 근거 | 저장소 | archive | cache | log | 당시 원문 복원 |
|---|---|---|---|---|---|---|
| `curation-f158b9b4c01d6732` / 3회 | **①.** source title의 중심은 한수원노조 25주년과 `한국원자력공사 설립` 비전인데, 생성 summary의 중심은 김회천 사장의 신한울·영덕·기장 사업 발언이다. canonical Gold도 유일하게 `scope=FAIL`, `HUMAN_BLIND_CONFIRMED`로 근거가 명시돼 있다. judge는 세 번 모두 `scope=PASS`, `important_omission=PASS`로 놓쳤다. | `curated.json` 최초 이력과 Gold 입력에는 title/url/생성 출력만 있고 description/body는 없다. Sol 보조 판정은 특정 사업 발언의 귀속이 검증되지 않았다고 기록했지만 원문 인용은 아니다. | `archive/2026-08.jsonl`의 최초 및 모든 변경 이력에 description/source_excerpt/body/content가 없다. `source_excerpt` SHA-256은 빈 문자열 해시다. | `issue_llm_reviews.json` 2개 항목은 title identity 판정뿐이며 기사 원문은 없다. 다른 production cache에는 hash가 없다. | blind sidecar에는 `REPAIR`만 있고 서술 근거는 없다. capture/delivery log에는 없다. | **불가.** 다만 title만으로 이번 scope 오류 분류는 가능하다. |
| `curation-9caacb9fe51ac742` / 3회 | **③.** title은 유가·금리 상승과 시장 혼조를, 생성 summary는 원전 수요·에너지 안보를 전면에 둬 title-only 오류 가능성이 크다. 그러나 canonical Gold는 `PASS→REPAIR`로 바뀌면서 모든 human dimension을 `PASS`로 남겼고 notes/required repair도 없다. 무엇이 Gold의 실패 근거였는지 확정할 수 없다. | 최초 `curated.json`에는 title/url/생성 출력만 있다. Sol 보조 판정은 원전 수요라는 인과 framing이 미입증이라고 했지만 canonical Human Gold의 이유는 아니다. | 최초 archive부터 원문 필드가 없다. 비어 있지 않은 `source_excerpt` 해시만 있고 대응 텍스트(preimage)는 저장되지 않았다. | production cache에서 hash를 찾지 못했다. Sol provisional rationale만 남아 있다. | blind sidecar에는 `REPAIR` label만 있다. capture/delivery log에는 없다. | **불가.** URL 재조회는 새 evidence이며 당시 원문 복원이 아니다. |
| `curation-159ee56b79e02a3a` / 3회 | **③.** 생성 summary의 가스터빈·SMR portfolio 세부는 title만으로 검증할 수 없다. 하지만 canonical Gold는 `PASS→REPAIR`, 모든 dimension `PASS`, notes/required repair 없음이다. Sol 보조 판정은 당시 source subtitle이 오히려 해당 세부를 지지한다고 기록해 Gold 이유가 더 불명확하다. | 최초 `curated.json`과 Gold 입력에는 title/url/생성 출력만 있다. Sol의 subtitle 언급은 원문 자체가 아니다. | 최초 archive부터 원문 필드가 없다. 비어 있지 않은 `source_excerpt` 해시만 있고 대응 텍스트는 없다. | production cache에서 hash를 찾지 못했다. Sol provisional rationale만 남아 있다. | blind sidecar에는 `REPAIR` label만 있다. capture/delivery log에는 없다. | **불가.** hash로 원문 텍스트를 역산할 수 없다. |
| `curation-f19d023d5c9863e4` / 3회 | **③.** 9월/12월 일정과 생태계·해양환경 세부는 title 밖이어서 원문이 필요하다. 동시에 canonical Gold는 `PASS→REPAIR`, 모든 dimension `PASS`, notes/required repair 없음이다. Sol 보조 판정은 원문이 생성 세부를 모두 지지한다고 기록해 REPAIR 이유를 복원할 수 없다. | 최초 `curated.json`에는 title/url과 생성 summary/detail만 있다. `issue_ledger.json`은 issue 소속만 보존한다. | 최초 및 모든 archive 이력에 원문 필드가 없고 `source_excerpt`는 빈 문자열 해시다. | `issue_llm_reviews.json`은 title identity 판정뿐이다. 다른 production cache에는 없다. | blind sidecar에는 `REPAIR`만 있다. capture/delivery log에는 없다. | **불가.** 원문과 canonical 판정 이유가 모두 없다. |
| `curation-3f0c0de0eae91573` / 3회 | **③.** MOU 상대방·필리핀 도입 계획·시장 진출 단계의 세부는 title만으로 완전히 검증되지 않는다. canonical Gold는 `PASS→REPAIR`로 바뀌었지만 모든 dimension `PASS`, notes/required repair 없음이며, Sol 보조 판정은 `CORRECT`였다. | 최초 `curated.json`, Gold 입력, `archive_source_backfill.json`에는 title/url/생성 출력 및 site locator만 있다. | 최초 및 모든 archive 이력에 원문 필드가 없고 `source_excerpt`는 빈 문자열 해시다. | production cache에서 hash를 찾지 못했다. Sol provisional rationale만 남아 있다. | blind sidecar에는 `REPAIR`만 있다. capture/delivery log에는 없다. | **불가.** canonical REPAIR의 구체 근거도 복원되지 않는다. |
| `curation-1ce9bd9cd45a8831` / 1회(나머지 2회 REPAIR) | **③.** source title 자체가 DISA Uranium Corporation 설립 계획과 플랫폼 범위를 명시하며 생성 출력과 겉으로 일치한다. 최초 Human label과 Sol 보조 판정도 `PASS/CORRECT`였으나, 2회차 blind에서 `REPAIR`로 바뀔 때 모든 dimension `PASS`, notes/required repair 없음으로 남았다. | 최초 `curated.json`과 Gold 입력에는 title/url/생성 출력만 있다. `archive_source_backfill.json`에 FT resolved URL이 있으나 당시 본문은 없다. | 최초 및 모든 archive 이력에 원문 필드가 없고 `source_excerpt`는 빈 문자열 해시다. | production cache에서 hash를 찾지 못했다. Sol provisional rationale만 남아 있다. | blind sidecar에는 `REPAIR`만 있다. capture/delivery log에는 없다. | **불가.** 현재 기록만으로 REPAIR 근거를 설명할 수 없다. |

## 4. 공통 복원 조사 결과

### 저장소

- Git 이력의 `curated.json`, `sent.json`, Gold fixture/입력, `archive_source_backfill.json`을 확인했다.
- `curated.json`은 title, link, 생성 output을 보존했지만 원 description/body/source excerpt를 보존하지 않았다.
- `sent.json`은 전송 시각 상태만 보존한다.
- gold-labeler 잔존 자료에는 Sol 보조 판정 이유가 있으나 기사 원문이 아니며 canonical blind Human Gold의 서술 근거도 아니다.
- Gold 이력은 `e29c92f`의 최초 사람 판정, `4dc1afa`의 1회차 blind 반영, `97019a6`의 2회차 전수 재라벨을 확인했다. 5개 blind-corrected case는 label만 `REPAIR`로 바뀌고 실패 차원·메모·required repair가 남지 않았다.

### archive

- 6개 case가 포함된 `archive/2026-08.jsonl` 행의 최초 커밋과 모든 변경 커밋을 검사했다.
- 어느 revision에도 `description`, `source_excerpt`, `body`, `content`, `raw_text`, `article_text`의 실제 텍스트가 없었다.
- 4건의 source excerpt fingerprint는 SHA-256 빈 문자열 해시 `e3b0c442…b855`다.
- 2건은 비어 있지 않은 fingerprint만 남았지만 원문 텍스트가 없어 복원 수단이 아니다.

### cache

- `issue_llm_reviews.json`, `issue_insights.json`, `keei_llm_matches.json`, `thread_llm_reviews.json`, `embeddings.json`, issue/thread ledger를 검사했다.
- f158/f19의 issue cache hit는 title 간 identity 판정뿐이다. 나머지는 source evidence hit가 없다.
- 2026-09-09 이후 `NUCLENS_LLM_CAPTURE` artifact 전체와 replay 작업본에는 이 6개 case의 당시 원문이 없다. calibration/preflight 산출물은 동일한 title-only packet을 되풀이할 뿐이다.

### log

- root 및 replay/worktree의 `delivery_log.jsonl`, gold-labeler server stdout/stderr, eval artifact를 확인했다.
- 6개 hash에 대응하는 기사 description/body는 발견되지 않았다.
- `.eval/blind-review.json`은 최종 label만 저장하며 판정 이유나 원문 evidence를 저장하지 않는다.

## 5. 기존 calibration을 동일 조건으로 재실행할 수 있는가

**유효한 의미에서는 할 수 없다.**

- 보관된 질문 packet과 기존 답변을 다시 import하는 기계적 replay는 가능하지만 새 calibration이 아니다.
- 같은 title-only packet을 judge에게 다시 묻는 것도 입력 결손을 그대로 반복할 뿐이며, 5개 case의 canonical Gold 근거를 복원하지 못한다.
- 당시 description/body는 저장소·archive·cache·log 어디에도 보존되지 않았다. 현재 URL을 다시 조회해 얻는 본문은 새 evidence이고, 당시 evidence와 byte/시점 동등성을 증명할 수 없다.
- 5개 case는 원문 부족에 더해 canonical `REPAIR`의 실패 차원과 서술 근거가 없다. 원문만 새로 구해도 기존 Human Gold와 동일 조건이었다고 주장할 수 없다.

따라서 threshold, Gold, prompt, model, gate를 그대로 둔 상태에서 calibration을 반복해도 `NOT_PROVEN`을 해소하는 검증이 되지 않는다. P4는 `HALTED`를 유지하고 full-size Gemini canary는 실행하지 않는다. 다음 진행에는 별도 승인 아래 source-complete 새 evidence protocol과 5개 case의 독립적이고 이유가 보존되는 Human Gold 재판정 중 적어도 하나가 필요하다.
