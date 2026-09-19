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
