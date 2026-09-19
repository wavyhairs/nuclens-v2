# Source-complete reference truth와 calibration handoff

## 결과

로컬 source-complete store의 26건을 모두 fingerprint에 묶어 독립 검토했다. 판정은 PASS 19,
REPAIR 7, BLOCK 0이다. 신규 사람 판정이라고 오인하지 않도록 reviewer provenance를
`codex_agent`로 명시했고, 기존 scorer 호환을 위해서만 `human_label` 키를 사용한다.

`tools/source_complete_reference.py`는 다음을 fail-closed로 강제한다.

- store의 모든 case가 `validate_case_document()`를 다시 통과해야 한다.
- export packet은 model/config/risk metadata를 제외하고 source와 익명 current output만 담는다.
- answer는 case set 전체, case fingerprint, reviewer type/display, label, 근거를 요구한다.
- PASS에는 error/repair를 허용하지 않고 REPAIR/BLOCK에는 dimension과 repair를 요구한다.
- 생성 Gold는 source title/description/body와 case fingerprint를 함께 보존한다.

로컬 산출물은 공개 저장소에 commit하지 않고 아래 `.eval` 경로에 둔다.

```text
.eval/gemini-reasoning-v2/p4-source-complete/reference-packet.json
.eval/gemini-reasoning-v2/p4-source-complete/reference-answer.json
.eval/gemini-reasoning-v2/p4-source-complete/source-complete-gold.json
```

## 고정 calibration 연결

`tools/curation_p4.py --gold <path>`만 추가했다. evaluator policy, 10개 dimension, judge prompt,
threshold, repeat 3회, model 선택과 canary gate는 변경하지 않았다. 생성된 질문지는 각각 26건인
세 파일이며, 총 78 blind request다.

```text
.eval/gemini-reasoning-v2/p4-source-complete/calibration/manual-calibration/
  calibration-repeat-0.md  sha256 c91aae0fff45d77419bcb47d2ac3d3b9015058d5598a0325f2f94390486b500e
  calibration-repeat-1.md  sha256 51ad390f8fa6adf0a2021f8fa26b563fece5d6805e584594efa1e0f9ba2bfdfe
  calibration-repeat-2.md  sha256 43bdff9b9e8eabbbaa038bc5ff3f82c3377e68693da2ec077e114bd89260a043
```

예상량은 UI prompt 3회, input 108,972 tokens, expected output 140,400 tokens,
maximum output cap 312,000 tokens이다. OpenAI Responses API transport는 추가하지 않았으며 API
call과 별도 API 비용은 0이다.

## 현재 차단점과 재개 명령

사용자는 26건 기사 본문/context를 OpenAI ChatGPT UI로 보내는 것을 승인했다. 하지만 현재
Codex computer-use에는 in-app browser, Edge, Chrome surface가 모두 비활성이고 환경에도
`OPENAI_API_KEY`가 없다. 따라서 질문지 생성까지 완료했지만 독립 UI 판정은 실행하지 못했다.
세 답변 JSON을 동일 디렉터리에 저장한 뒤 다음 명령으로만 재개한다.

```powershell
python tools/curation_p4.py `
  --gold .eval/gemini-reasoning-v2/p4-source-complete/source-complete-gold.json `
  --out .eval/gemini-reasoning-v2/p4-source-complete/calibration `
  --phase import-calibration `
  --answer <repeat-0-answer.json> `
  --answer <repeat-1-answer.json> `
  --answer <repeat-2-answer.json>
```

strict import 결과가 `PASS`이고 `calibration_scope=source_complete`일 때만 canary 비용·호출량을
다시 보고한다. `NOT_PROVEN`, malformed, incomplete이면 P4는 계속 HALTED이며 Gemini canary는
실행하지 않는다.

## 실제 calibration 결과

사용자가 제공한 세 JSON을 strict import했다. 세 packet 모두 UI 표시 모델은
`GPT-5.6 Sol`이며 26건 × 3회, 총 78 judgment가 schema/logic error 0으로 수락됐다.

| 지표 | 결과 | 고정 기준 | 판정 |
|---|---:|---:|---|
| PASS vs intervention agreement | 0.461538 | >= 0.85 | 실패 |
| false PASS | 3 | 0 | 실패 |
| unsafe PASS | 3 | 0 | 실패 |
| repeat modal stability | 1.0 | >= 0.90 | 통과 |
| identical-pair TIE | 1.0 | >= 0.95 | 통과 |
| position bias | 0.0 | <= 0.05 | 통과 |
| duplicate verdict consistency | 1.0 | >= 0.95 | 통과 |
| schema/logic errors | 0 | 0 | 통과 |

case-major modal 비교는 reference PASS→judge REPAIR 14건, PASS→PASS 5건,
REPAIR→REPAIR 6건, REPAIR→PASS 1건이다. false/unsafe PASS 3행은 동일한 FDC case
`curation-c779c3bae51f041f-bae8001b5a9a`의 세 반복이다. judge는 모두 source 범위 안의
전망으로 보아 PASS했지만 reference는 시장 형성 전망의 certainty를 REPAIR로 두었다.

반대로 reference PASS 중 judge가 일관되게 REPAIR한 case가 많고, 예를 들어 첫 case에서는
한국형 SMR의 1997년 개발 착수를 두산에너빌리티 자체 이력처럼 연결한 factual support 오류를
세 번 모두 지적했다. 따라서 이 결과만으로 judge가 나쁘다고 단정할 수 없고, Codex 단독
reference truth의 신뢰성도 입증되지 않았다. 결과를 본 뒤 Gold, threshold, prompt, model을
바꾸지 않았으며 canary gate는 닫힌 상태다.

최종 calibration status는 `NOT_PROVEN`이다. Gemini canary 호출은 0회다. 다음 재개는 고정된
26건 evidence와 disagreement 15건(14 false intervention case + 1 unsafe PASS case)에 대한
별도 승인된 독립 adjudication protocol이 먼저이며, 기존 결과를 덮어쓰거나 같은 judge로
재실행해서는 안 된다.
