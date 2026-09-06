# Semantic Gold human labeling sheet

72개 pending case는 먼저 독립 GPT-5.6 Sol High provisional package로 판정한 뒤
사람이 검수합니다. Sol 결과는 Human Gold가 아니며 canonical에 자동 반영되지
않습니다. 공식 Sol 실행 경로에 `docs/gold-labeling/sol-review/semantic-input.jsonl`과
matching schema를 전달하고, 반환 JSONL을 import한 뒤 UI를 실행합니다.

```powershell
python tools/sol_provisional_gold.py --task semantic --import-provisional path\to\semantic-sol-output.jsonl
python tools/review_gold_labeler.py --task semantic
```

UI의 A는 provisional 승인, C는 사람 수정, U는 추가 검수 보류입니다. 일반 저장은
`.eval/gold-labels/semantic_labels.json`에만 기록되고, `human_reviewed=true`인 case만
명시적 `Export / Validate` 때 canonical fixture에 반영됩니다. 선정/생성 metadata는
기본 화면에서 숨겨지며 source evidence로 취급하지 않습니다.

claim을 source evidence만으로 검증하세요. generated context는 현재 출력 참고일 뿐 근거가 아닙니다.

## SEM-001 · `semantic-fa4c10372e606d1e-aligned`

**Claim:** 신한울 3ㆍ4호기 건설 현장 용접사 양성 교육 실시

### Source evidence

- 원문: [신한울 3ㆍ4호기 건설 현장 용접사 양성 교육 실시](https://www.viva100.com/article/20260902500971)
- 날짜: 2026-09-02T14:38:00+09:00
- 해시: `fa4c10372e606d1e`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "한울원자력본부, 신한울 3·4호기 용접사 양성 교육 실시", "summary": "한울원자력본부가 9월 1일부터 11월 30일까지 신한울 3·4호기 건설 현장 용접사 양성 교육을 진행함.", "detail": "한국수력원자력 한울원자력본부는 1일 신한울 3·4호기 건설 현장에서 2026년 하반기 용접사 양성 교육 입교식을 개최했다. 교육은 11월 30일까지 진행되며 지역 인재 양성을 목적으로 한다.", "implication": "", "why_important": ""}

</details>

## SEM-002 · `semantic-fa4c10372e606d1e-perturbed`

**Claim:** 원문이 보도한 '신한울 3ㆍ4호기 건설 현장 용접사 양성 교육 실시' 사건은 실제로 발생하지 않았다.

### Source evidence

- 원문: [신한울 3ㆍ4호기 건설 현장 용접사 양성 교육 실시](https://www.viva100.com/article/20260902500971)
- 날짜: 2026-09-02T14:38:00+09:00
- 해시: `fa4c10372e606d1e`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "FACT_ERROR"}
- generated context: {"title_kr": "한울원자력본부, 신한울 3·4호기 용접사 양성 교육 실시", "summary": "한울원자력본부가 9월 1일부터 11월 30일까지 신한울 3·4호기 건설 현장 용접사 양성 교육을 진행함.", "detail": "한국수력원자력 한울원자력본부는 1일 신한울 3·4호기 건설 현장에서 2026년 하반기 용접사 양성 교육 입교식을 개최했다. 교육은 11월 30일까지 진행되며 지역 인재 양성을 목적으로 한다.", "implication": "", "why_important": ""}

</details>

## SEM-003 · `semantic-fa4c10372e606d1e-unsupported`

**Claim:** '신한울 3ㆍ4호기 건설 현장 용접사 양성 교육 실시'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [신한울 3ㆍ4호기 건설 현장 용접사 양성 교육 실시](https://www.viva100.com/article/20260902500971)
- 날짜: 2026-09-02T14:38:00+09:00
- 해시: `fa4c10372e606d1e`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "한울원자력본부, 신한울 3·4호기 용접사 양성 교육 실시", "summary": "한울원자력본부가 9월 1일부터 11월 30일까지 신한울 3·4호기 건설 현장 용접사 양성 교육을 진행함.", "detail": "한국수력원자력 한울원자력본부는 1일 신한울 3·4호기 건설 현장에서 2026년 하반기 용접사 양성 교육 입교식을 개최했다. 교육은 11월 30일까지 진행되며 지역 인재 양성을 목적으로 한다.", "implication": "", "why_important": ""}

</details>

## SEM-004 · `semantic-aa57a5168dafb9f9-aligned`

**Claim:** 권칠승 "1주택자 세부담 없도록 방안 마련…주거안정 체감 총력"

### Source evidence

- 원문: [권칠승 "1주택자 세부담 없도록 방안 마련…주거안정 체감 총력"](https://www.mt.co.kr/politics/2026/08/25/2026082510473937822)
- 날짜: 2026-08-25T10:59:00+09:00
- 해시: `aa57a5168dafb9f9`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "민주당, 3대 메가프로젝트 지원 및 제12차 전력수급기본계획 점검 예고", "summary": "더불어민주당 권칠승 정책위의장은 반도체 등 3대 메가프로젝트의 차질 없는 추진을 위해 에너지 믹스와 제12차 전력수급기본계획을 지속 점검하겠다고 밝혔다.", "detail": "권칠승 정책위의장은 당정협의 후 브리핑에서 반도체 초격차 유지를 위한 전략산업 지원 의지를 강조하며, 이를 뒷받침하기 위한 에너지 믹스 최적화와 전력망 확충, 제12차 전력수급기본계획의 상황을 면밀히 점검하겠다고 언급했다. 이는 국가 전략산업의 안정적 전력 공급을 위한 정책적 의지를 재확인한 것이다.", "implication": "", "why_important": ""}

</details>

## SEM-005 · `semantic-aa57a5168dafb9f9-perturbed`

**Claim:** 권칠승 "8주택자 세부담 없도록 방안 마련…주거안정 체감 총력"

### Source evidence

- 원문: [권칠승 "1주택자 세부담 없도록 방안 마련…주거안정 체감 총력"](https://www.mt.co.kr/politics/2026/08/25/2026082510473937822)
- 날짜: 2026-08-25T10:59:00+09:00
- 해시: `aa57a5168dafb9f9`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "NUMBER_ERROR"}
- generated context: {"title_kr": "민주당, 3대 메가프로젝트 지원 및 제12차 전력수급기본계획 점검 예고", "summary": "더불어민주당 권칠승 정책위의장은 반도체 등 3대 메가프로젝트의 차질 없는 추진을 위해 에너지 믹스와 제12차 전력수급기본계획을 지속 점검하겠다고 밝혔다.", "detail": "권칠승 정책위의장은 당정협의 후 브리핑에서 반도체 초격차 유지를 위한 전략산업 지원 의지를 강조하며, 이를 뒷받침하기 위한 에너지 믹스 최적화와 전력망 확충, 제12차 전력수급기본계획의 상황을 면밀히 점검하겠다고 언급했다. 이는 국가 전략산업의 안정적 전력 공급을 위한 정책적 의지를 재확인한 것이다.", "implication": "", "why_important": ""}

</details>

## SEM-006 · `semantic-aa57a5168dafb9f9-unsupported`

**Claim:** '권칠승 "1주택자 세부담 없도록 방안 마련…주거안정 체감 총력"'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [권칠승 "1주택자 세부담 없도록 방안 마련…주거안정 체감 총력"](https://www.mt.co.kr/politics/2026/08/25/2026082510473937822)
- 날짜: 2026-08-25T10:59:00+09:00
- 해시: `aa57a5168dafb9f9`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "민주당, 3대 메가프로젝트 지원 및 제12차 전력수급기본계획 점검 예고", "summary": "더불어민주당 권칠승 정책위의장은 반도체 등 3대 메가프로젝트의 차질 없는 추진을 위해 에너지 믹스와 제12차 전력수급기본계획을 지속 점검하겠다고 밝혔다.", "detail": "권칠승 정책위의장은 당정협의 후 브리핑에서 반도체 초격차 유지를 위한 전략산업 지원 의지를 강조하며, 이를 뒷받침하기 위한 에너지 믹스 최적화와 전력망 확충, 제12차 전력수급기본계획의 상황을 면밀히 점검하겠다고 언급했다. 이는 국가 전략산업의 안정적 전력 공급을 위한 정책적 의지를 재확인한 것이다.", "implication": "", "why_important": ""}

</details>

## SEM-007 · `semantic-f6b0a05f1c68e291-aligned`

**Claim:** 미국인 70% AI 데이터센터 건설 반대…중간선거 쟁점 부상

### Source evidence

- 원문: [미국인 70% AI 데이터센터 건설 반대…중간선거 쟁점 부상](https://www.obsnews.co.kr/news/articleView.html?idxno=1534884)
- 날짜: 2026-08-21T20:50:00+09:00
- 해시: `f6b0a05f1c68e291`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "미국 내 AI 데이터센터 건설 반대 여론 확산", "summary": "미국에서 AI 데이터센터 건설에 대한 주민 반대 여론이 70%에 달하며, 중간선거를 앞두고 정치권의 핵심 쟁점으로 부상했다.", "detail": "미국 내 데이터센터 건설이 물·전력 사용 증가 및 소음 등 환경 문제로 주민들의 강한 반대에 직면했다. 이에 따라 민주당과 공화당 후보들은 데이터센터 건설 제한 및 규제 강화를 선거 공약으로 내세우고 있다. 일부 주에서는 행정명령을 통해 규제를 강화하고 있으며, 이는 트럼프 전 대통령의 데이터센터 확충 기조와 대조를 이룬다.", "implication": "", "why_important": ""}

</details>

## SEM-008 · `semantic-f6b0a05f1c68e291-perturbed`

**Claim:** 한국수력원자력이 '미국인 70% AI 데이터센터 건설 반대…중간선거 쟁점 부상'를 직접 결정하고 발표했다.

### Source evidence

- 원문: [미국인 70% AI 데이터센터 건설 반대…중간선거 쟁점 부상](https://www.obsnews.co.kr/news/articleView.html?idxno=1534884)
- 날짜: 2026-08-21T20:50:00+09:00
- 해시: `f6b0a05f1c68e291`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "ENTITY_ERROR"}
- generated context: {"title_kr": "미국 내 AI 데이터센터 건설 반대 여론 확산", "summary": "미국에서 AI 데이터센터 건설에 대한 주민 반대 여론이 70%에 달하며, 중간선거를 앞두고 정치권의 핵심 쟁점으로 부상했다.", "detail": "미국 내 데이터센터 건설이 물·전력 사용 증가 및 소음 등 환경 문제로 주민들의 강한 반대에 직면했다. 이에 따라 민주당과 공화당 후보들은 데이터센터 건설 제한 및 규제 강화를 선거 공약으로 내세우고 있다. 일부 주에서는 행정명령을 통해 규제를 강화하고 있으며, 이는 트럼프 전 대통령의 데이터센터 확충 기조와 대조를 이룬다.", "implication": "", "why_important": ""}

</details>

## SEM-009 · `semantic-f6b0a05f1c68e291-unsupported`

**Claim:** '미국인 70% AI 데이터센터 건설 반대…중간선거 쟁점 부상'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [미국인 70% AI 데이터센터 건설 반대…중간선거 쟁점 부상](https://www.obsnews.co.kr/news/articleView.html?idxno=1534884)
- 날짜: 2026-08-21T20:50:00+09:00
- 해시: `f6b0a05f1c68e291`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "미국 내 AI 데이터센터 건설 반대 여론 확산", "summary": "미국에서 AI 데이터센터 건설에 대한 주민 반대 여론이 70%에 달하며, 중간선거를 앞두고 정치권의 핵심 쟁점으로 부상했다.", "detail": "미국 내 데이터센터 건설이 물·전력 사용 증가 및 소음 등 환경 문제로 주민들의 강한 반대에 직면했다. 이에 따라 민주당과 공화당 후보들은 데이터센터 건설 제한 및 규제 강화를 선거 공약으로 내세우고 있다. 일부 주에서는 행정명령을 통해 규제를 강화하고 있으며, 이는 트럼프 전 대통령의 데이터센터 확충 기조와 대조를 이룬다.", "implication": "", "why_important": ""}

</details>

## SEM-010 · `semantic-a641fc0911aae0b5-aligned`

**Claim:** Prodigy Signs Agreement For Transportable Nuclear Power Plant With Canadian Indigenous Communities

### Source evidence

- 원문: [Prodigy Signs Agreement For Transportable Nuclear Power Plant With Canadian Indigenous Communities](https://www.nucnet.org/news/prodigy-signs-agreement-for-transportable-nuclear-power-plant-with-canadian-indigenous-communities-9-3-2026)
- 날짜: 2026-09-02T08:10:13+00:00
- 해시: `a641fc0911aae0b5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "캐나다 Prodigy, 원주민 공동체와 이동형 원전 건설 협약 체결", "summary": "캐나다의 Prodigy Clean Energy가 뉴브런즈윅주 벨레듄 지역에 이동형 원전 건설을 추진하기 위해 현지 원주민 공동체와 협약을 체결했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-011 · `semantic-a641fc0911aae0b5-perturbed`

**Claim:** 'Prodigy Signs Agreement For Transportable Nuclear Power Plant With Canadian Indigenous Communities' 사건은 2099년 1월 1일 발생했다.

### Source evidence

- 원문: [Prodigy Signs Agreement For Transportable Nuclear Power Plant With Canadian Indigenous Communities](https://www.nucnet.org/news/prodigy-signs-agreement-for-transportable-nuclear-power-plant-with-canadian-indigenous-communities-9-3-2026)
- 날짜: 2026-09-02T08:10:13+00:00
- 해시: `a641fc0911aae0b5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "DATE_ERROR"}
- generated context: {"title_kr": "캐나다 Prodigy, 원주민 공동체와 이동형 원전 건설 협약 체결", "summary": "캐나다의 Prodigy Clean Energy가 뉴브런즈윅주 벨레듄 지역에 이동형 원전 건설을 추진하기 위해 현지 원주민 공동체와 협약을 체결했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-012 · `semantic-a641fc0911aae0b5-unsupported`

**Claim:** 'Prodigy Signs Agreement For Transportable Nuclear Power Plant With Canadian Indigenous Communities'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [Prodigy Signs Agreement For Transportable Nuclear Power Plant With Canadian Indigenous Communities](https://www.nucnet.org/news/prodigy-signs-agreement-for-transportable-nuclear-power-plant-with-canadian-indigenous-communities-9-3-2026)
- 날짜: 2026-09-02T08:10:13+00:00
- 해시: `a641fc0911aae0b5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "캐나다 Prodigy, 원주민 공동체와 이동형 원전 건설 협약 체결", "summary": "캐나다의 Prodigy Clean Energy가 뉴브런즈윅주 벨레듄 지역에 이동형 원전 건설을 추진하기 위해 현지 원주민 공동체와 협약을 체결했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-013 · `semantic-84575d69ed134631-aligned`

**Claim:** 서남권 반도체 전력의 맥 ‘한빛원전’…부지 내 저장시설 조기 구축이...

### Source evidence

- 원문: [서남권 반도체 전력의 맥 ‘한빛원전’…부지 내 저장시설 조기 구축이...](https://www.dailian.co.kr/news/view/1680806?sc=Naver)
- 날짜: 2026-08-24T07:00:00+09:00
- 해시: `84575d69ed134631`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "한빛원전 사용후핵연료 건식저장시설, 2030년 운영 위해 행정 절차 가속화", "summary": "한수원은 한빛원전 부지 내 4810다발 규모 건식저장시설을 2030년 7월 운영하기 위해 인허가 절차를 추진 중이다.", "detail": "한빛원전은 2030년 사용후핵연료 저장수조 포화가 예상되어 건식저장시설 구축이 시급하다. 한수원은 2027년 시설계획 승인, 2028년 설계승인 및 운영변경허가를 목표로 하고 있으나, 원안위의 안전성 검증에만 2년 이상 소요될 것으로 보여 일정이 촉박하다. 지역사회 수용성 확보 또한 주요 과제다.", "implication": "고준위 특별법 통과에도 불구하고 개별 부지 내 저장시설 인허가 및 지역 수용성 문제가 적기 구축의 최대 난관이다.", "why_important": ""}

</details>

## SEM-014 · `semantic-84575d69ed134631-perturbed`

**Claim:** '서남권 반도체 전력의 맥 ‘한빛원전’…부지 내 저장시설 조기 구축이...' 조치는 전 세계 모든 원전에 동일하게 적용된다.

### Source evidence

- 원문: [서남권 반도체 전력의 맥 ‘한빛원전’…부지 내 저장시설 조기 구축이...](https://www.dailian.co.kr/news/view/1680806?sc=Naver)
- 날짜: 2026-08-24T07:00:00+09:00
- 해시: `84575d69ed134631`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "SCOPE_ERROR"}
- generated context: {"title_kr": "한빛원전 사용후핵연료 건식저장시설, 2030년 운영 위해 행정 절차 가속화", "summary": "한수원은 한빛원전 부지 내 4810다발 규모 건식저장시설을 2030년 7월 운영하기 위해 인허가 절차를 추진 중이다.", "detail": "한빛원전은 2030년 사용후핵연료 저장수조 포화가 예상되어 건식저장시설 구축이 시급하다. 한수원은 2027년 시설계획 승인, 2028년 설계승인 및 운영변경허가를 목표로 하고 있으나, 원안위의 안전성 검증에만 2년 이상 소요될 것으로 보여 일정이 촉박하다. 지역사회 수용성 확보 또한 주요 과제다.", "implication": "고준위 특별법 통과에도 불구하고 개별 부지 내 저장시설 인허가 및 지역 수용성 문제가 적기 구축의 최대 난관이다.", "why_important": ""}

</details>

## SEM-015 · `semantic-84575d69ed134631-unsupported`

**Claim:** '서남권 반도체 전력의 맥 ‘한빛원전’…부지 내 저장시설 조기 구축이...'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [서남권 반도체 전력의 맥 ‘한빛원전’…부지 내 저장시설 조기 구축이...](https://www.dailian.co.kr/news/view/1680806?sc=Naver)
- 날짜: 2026-08-24T07:00:00+09:00
- 해시: `84575d69ed134631`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "한빛원전 사용후핵연료 건식저장시설, 2030년 운영 위해 행정 절차 가속화", "summary": "한수원은 한빛원전 부지 내 4810다발 규모 건식저장시설을 2030년 7월 운영하기 위해 인허가 절차를 추진 중이다.", "detail": "한빛원전은 2030년 사용후핵연료 저장수조 포화가 예상되어 건식저장시설 구축이 시급하다. 한수원은 2027년 시설계획 승인, 2028년 설계승인 및 운영변경허가를 목표로 하고 있으나, 원안위의 안전성 검증에만 2년 이상 소요될 것으로 보여 일정이 촉박하다. 지역사회 수용성 확보 또한 주요 과제다.", "implication": "고준위 특별법 통과에도 불구하고 개별 부지 내 저장시설 인허가 및 지역 수용성 문제가 적기 구축의 최대 난관이다.", "why_important": ""}

</details>

## SEM-016 · `semantic-eb147409fff92001-aligned`

**Claim:** PURECORE PROVIDES CORPORATE UPDATE HIGHLIGHTING EXECUTION ACROSS URANIUM, COPPER AND CAPITAL MARKETS – Company Announcement - FT.com

### Source evidence

- 원문: [PURECORE PROVIDES CORPORATE UPDATE HIGHLIGHTING EXECUTION ACROSS URANIUM, COPPER AND CAPITAL MARKETS – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMilgFBVV95cUxQcmN1LUZBN24yLWFpNmFzOU5iQkF3UkpWSDRDMFFERnllNFBwN2puNnJKemJyYm14VzBhUXBCSnJHZUdCeVdOaEs1YmFIeVVrOHhIRm91YW9CelljbjFsclNFWHhieHVZd0dBWXRlU1Zpbm9JWFg3b0RCaFJhVVRPc19vVGNzSnhZbW1xdmFuQmMyR050Q2c?oc=5)
- 날짜: 2026-08-26T16:14:00+00:00
- 해시: `eb147409fff92001`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "퓨어코어(Purecore), 우라늄 및 구리 사업 업데이트 발표", "summary": "퓨어코어가 우라늄, 구리 사업 및 자본 시장 활동에 대한 기업 업데이트를 발표했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-017 · `semantic-eb147409fff92001-perturbed`

**Claim:** 'PURECORE PROVIDES CORPORATE UPDATE HIGHLIGHTING EXECUTION ACROSS URANIUM, COPPER AND CAPITAL MARKETS – Company Announcement - FT.com' 관련 사업은 이미 상업운전과 최종 준공을 완료했다.

### Source evidence

- 원문: [PURECORE PROVIDES CORPORATE UPDATE HIGHLIGHTING EXECUTION ACROSS URANIUM, COPPER AND CAPITAL MARKETS – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMilgFBVV95cUxQcmN1LUZBN24yLWFpNmFzOU5iQkF3UkpWSDRDMFFERnllNFBwN2puNnJKemJyYm14VzBhUXBCSnJHZUdCeVdOaEs1YmFIeVVrOHhIRm91YW9CelljbjFsclNFWHhieHVZd0dBWXRlU1Zpbm9JWFg3b0RCaFJhVVRPc19vVGNzSnhZbW1xdmFuQmMyR050Q2c?oc=5)
- 날짜: 2026-08-26T16:14:00+00:00
- 해시: `eb147409fff92001`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "STAGE_ERROR"}
- generated context: {"title_kr": "퓨어코어(Purecore), 우라늄 및 구리 사업 업데이트 발표", "summary": "퓨어코어가 우라늄, 구리 사업 및 자본 시장 활동에 대한 기업 업데이트를 발표했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-018 · `semantic-eb147409fff92001-unsupported`

**Claim:** 'PURECORE PROVIDES CORPORATE UPDATE HIGHLIGHTING EXECUTION ACROSS URANIUM, COPPER AND CAPITAL MARKETS – Company Announcement - FT.com'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [PURECORE PROVIDES CORPORATE UPDATE HIGHLIGHTING EXECUTION ACROSS URANIUM, COPPER AND CAPITAL MARKETS – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMilgFBVV95cUxQcmN1LUZBN24yLWFpNmFzOU5iQkF3UkpWSDRDMFFERnllNFBwN2puNnJKemJyYm14VzBhUXBCSnJHZUdCeVdOaEs1YmFIeVVrOHhIRm91YW9CelljbjFsclNFWHhieHVZd0dBWXRlU1Zpbm9JWFg3b0RCaFJhVVRPc19vVGNzSnhZbW1xdmFuQmMyR050Q2c?oc=5)
- 날짜: 2026-08-26T16:14:00+00:00
- 해시: `eb147409fff92001`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "퓨어코어(Purecore), 우라늄 및 구리 사업 업데이트 발표", "summary": "퓨어코어가 우라늄, 구리 사업 및 자본 시장 활동에 대한 기업 업데이트를 발표했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-019 · `semantic-dfd27e3bcbe93166-aligned`

**Claim:** 실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]

### Source evidence

- 원문: [실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]](https://www.etoday.co.kr/news/view/2612337)
- 날짜: 2026-08-09T14:12:00+09:00
- 해시: `dfd27e3bcbe93166`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]", "summary": "같은 달 한국수력원자력과 산업은행 등이 공동 출자한 586억원 규모 원전산업 투자펀드의 첫 투자 대상으로 선정돼 100억원을 확보했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-020 · `semantic-dfd27e3bcbe93166-perturbed`

**Claim:** '실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]' 때문에 국내 전기요금이 즉시 인상됐다.

### Source evidence

- 원문: [실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]](https://www.etoday.co.kr/news/view/2612337)
- 날짜: 2026-08-09T14:12:00+09:00
- 해시: `dfd27e3bcbe93166`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "CAUSALITY_ERROR"}
- generated context: {"title_kr": "실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]", "summary": "같은 달 한국수력원자력과 산업은행 등이 공동 출자한 586억원 규모 원전산업 투자펀드의 첫 투자 대상으로 선정돼 100억원을 확보했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-021 · `semantic-dfd27e3bcbe93166-unsupported`

**Claim:** '실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]](https://www.etoday.co.kr/news/view/2612337)
- 날짜: 2026-08-09T14:12:00+09:00
- 해시: `dfd27e3bcbe93166`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "실적 꺾였는데 몸값 1조 거론…범한메카텍, SMR로 승부[IPO 엑스레이]", "summary": "같은 달 한국수력원자력과 산업은행 등이 공동 출자한 586억원 규모 원전산업 투자펀드의 첫 투자 대상으로 선정돼 100억원을 확보했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-022 · `semantic-1d9b3ed6ef4a4de8-aligned`

**Claim:** 생산만으론 부족…저장·전력망도 에너지 경쟁력

### Source evidence

- 원문: [생산만으론 부족…저장·전력망도 에너지 경쟁력](https://news.kbs.co.kr/news/pc/view/view.do?ncd=8654045)
- 날짜: 2026-09-03T19:06:00+09:00
- 해시: `1d9b3ed6ef4a4de8`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "경북연구원, 동해안권 AI 데이터센터 분산 배치 필요성 제기", "summary": "경북연구원은 수도권 전력망 한계를 극복하기 위해 동해안권에 AI 데이터센터를 분산 배치해야 한다고 제언했다.", "detail": "중국은 재생에너지 생산지와 소비지를 연결하는 송전망 확충과 함께 서부에 AI 학습 시설을 배치하는 전략을 추진 중이다. 국내에서도 기존 송전망의 한계를 고려해 잉여전력이 풍부한 동해안 지역에 AI 데이터센터를 유치하고, 가상발전소 및 AI 기반 전력망 운영체계 고도화가 필요하다는 의견이 제시되었다.", "implication": "수도권 전력 계통 포화 문제 해결을 위해 대규모 전력 소비처인 데이터센터를 발전원 인근으로 분산하려는 정책적 논의가 본격화되고 있다.", "why_important": ""}

</details>

## SEM-023 · `semantic-1d9b3ed6ef4a4de8-perturbed`

**Claim:** '생산만으론 부족…저장·전력망도 에너지 경쟁력'의 후속 조치는 원문 보도 전에 모두 완료됐다.

### Source evidence

- 원문: [생산만으론 부족…저장·전력망도 에너지 경쟁력](https://news.kbs.co.kr/news/pc/view/view.do?ncd=8654045)
- 날짜: 2026-09-03T19:06:00+09:00
- 해시: `1d9b3ed6ef4a4de8`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "TEMPORAL_ERROR"}
- generated context: {"title_kr": "경북연구원, 동해안권 AI 데이터센터 분산 배치 필요성 제기", "summary": "경북연구원은 수도권 전력망 한계를 극복하기 위해 동해안권에 AI 데이터센터를 분산 배치해야 한다고 제언했다.", "detail": "중국은 재생에너지 생산지와 소비지를 연결하는 송전망 확충과 함께 서부에 AI 학습 시설을 배치하는 전략을 추진 중이다. 국내에서도 기존 송전망의 한계를 고려해 잉여전력이 풍부한 동해안 지역에 AI 데이터센터를 유치하고, 가상발전소 및 AI 기반 전력망 운영체계 고도화가 필요하다는 의견이 제시되었다.", "implication": "수도권 전력 계통 포화 문제 해결을 위해 대규모 전력 소비처인 데이터센터를 발전원 인근으로 분산하려는 정책적 논의가 본격화되고 있다.", "why_important": ""}

</details>

## SEM-024 · `semantic-1d9b3ed6ef4a4de8-unsupported`

**Claim:** '생산만으론 부족…저장·전력망도 에너지 경쟁력'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [생산만으론 부족…저장·전력망도 에너지 경쟁력](https://news.kbs.co.kr/news/pc/view/view.do?ncd=8654045)
- 날짜: 2026-09-03T19:06:00+09:00
- 해시: `1d9b3ed6ef4a4de8`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "경북연구원, 동해안권 AI 데이터센터 분산 배치 필요성 제기", "summary": "경북연구원은 수도권 전력망 한계를 극복하기 위해 동해안권에 AI 데이터센터를 분산 배치해야 한다고 제언했다.", "detail": "중국은 재생에너지 생산지와 소비지를 연결하는 송전망 확충과 함께 서부에 AI 학습 시설을 배치하는 전략을 추진 중이다. 국내에서도 기존 송전망의 한계를 고려해 잉여전력이 풍부한 동해안 지역에 AI 데이터센터를 유치하고, 가상발전소 및 AI 기반 전력망 운영체계 고도화가 필요하다는 의견이 제시되었다.", "implication": "수도권 전력 계통 포화 문제 해결을 위해 대규모 전력 소비처인 데이터센터를 발전원 인근으로 분산하려는 정책적 논의가 본격화되고 있다.", "why_important": ""}

</details>

## SEM-025 · `semantic-110e0f02d866b27b-aligned`

**Claim:** “계속운전 신청 원전, 신규원전 수준으로 안전성 철저히 확인”

### Source evidence

- 원문: [“계속운전 신청 원전, 신규원전 수준으로 안전성 철저히 확인”](https://news.google.com/rss/articles/CBMiaEFVX3lxTE8wMmFHWVhwTUsyRHM2MnVIQ1hTZjFSaHpocmNUWnRkRFgzUThtVXRlWThLV2hWUjBqUVpKQWhfMkFRTWFSYm4tM05WdmltZ2ZSa2tPN1RNVWhRNzBDMEdOejhwT3RfQkFi?oc=5)
- 날짜: 2026-08-05T05:28:00+00:00
- 해시: `110e0f02d866b27b`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "계속운전 신청 원전, 신규원전 수준 안전성 확인 강조", "summary": "계속운전을 신청하는 원전의 안전성을 신규원전 수준으로 철저히 확인해야 한다는 견해가 제시되었다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-026 · `semantic-110e0f02d866b27b-perturbed`

**Claim:** '“계속운전 신청 원전, 신규원전 수준으로 안전성 철저히 확인”'의 전망과 계획은 예외 없이 확정됐다.

### Source evidence

- 원문: [“계속운전 신청 원전, 신규원전 수준으로 안전성 철저히 확인”](https://news.google.com/rss/articles/CBMiaEFVX3lxTE8wMmFHWVhwTUsyRHM2MnVIQ1hTZjFSaHpocmNUWnRkRFgzUThtVXRlWThLV2hWUjBqUVpKQWhfMkFRTWFSYm4tM05WdmltZ2ZSa2tPN1RNVWhRNzBDMEdOejhwT3RfQkFi?oc=5)
- 날짜: 2026-08-05T05:28:00+00:00
- 해시: `110e0f02d866b27b`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "CERTAINTY_ERROR"}
- generated context: {"title_kr": "계속운전 신청 원전, 신규원전 수준 안전성 확인 강조", "summary": "계속운전을 신청하는 원전의 안전성을 신규원전 수준으로 철저히 확인해야 한다는 견해가 제시되었다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-027 · `semantic-110e0f02d866b27b-unsupported`

**Claim:** '“계속운전 신청 원전, 신규원전 수준으로 안전성 철저히 확인”'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [“계속운전 신청 원전, 신규원전 수준으로 안전성 철저히 확인”](https://news.google.com/rss/articles/CBMiaEFVX3lxTE8wMmFHWVhwTUsyRHM2MnVIQ1hTZjFSaHpocmNUWnRkRFgzUThtVXRlWThLV2hWUjBqUVpKQWhfMkFRTWFSYm4tM05WdmltZ2ZSa2tPN1RNVWhRNzBDMEdOejhwT3RfQkFi?oc=5)
- 날짜: 2026-08-05T05:28:00+00:00
- 해시: `110e0f02d866b27b`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "계속운전 신청 원전, 신규원전 수준 안전성 확인 강조", "summary": "계속운전을 신청하는 원전의 안전성을 신규원전 수준으로 철저히 확인해야 한다는 견해가 제시되었다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-028 · `semantic-c1ddbcc160b82974-aligned`

**Claim:** Holtec's SMR-300 test facility taking shape at INL

### Source evidence

- 원문: [Holtec's SMR-300 test facility taking shape at INL](https://www.world-nuclear-news.org/articles/holtecs-smr-300-test-facility-taking-shape-at-inl)
- 날짜: 2026-08-27T07:34:40+00:00
- 해시: `c1ddbcc160b82974`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "홀텍, 아이다호 국립연구소에 SMR-300 실증 시설 구축", "summary": "홀텍 인터내셔널이 아이다호 국립연구소(INL)에 SMR-300 실증 시설을 구축 중이며 2027년 가동을 목표로 함.", "detail": "", "implication": "실증 시설을 통해 확보된 열수력 데이터는 향후 SMR-300의 출력 증강을 위한 인허가 변경의 핵심 근거로 활용될 예정이다.", "why_important": "미국 내 SMR 설계 검증을 위한 실증 인프라가 구체화됨에 따라 SMR 상용화 일정이 가시화되고 있음."}

</details>

## SEM-029 · `semantic-c1ddbcc160b82974-perturbed`

**Claim:** 'Holtec's SMR-300 test facility taking shape at INL'은 정부의 공개되지 않은 비밀 지시 때문에 발생했다.

### Source evidence

- 원문: [Holtec's SMR-300 test facility taking shape at INL](https://www.world-nuclear-news.org/articles/holtecs-smr-300-test-facility-taking-shape-at-inl)
- 날짜: 2026-08-27T07:34:40+00:00
- 해시: `c1ddbcc160b82974`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "홀텍, 아이다호 국립연구소에 SMR-300 실증 시설 구축", "summary": "홀텍 인터내셔널이 아이다호 국립연구소(INL)에 SMR-300 실증 시설을 구축 중이며 2027년 가동을 목표로 함.", "detail": "", "implication": "실증 시설을 통해 확보된 열수력 데이터는 향후 SMR-300의 출력 증강을 위한 인허가 변경의 핵심 근거로 활용될 예정이다.", "why_important": "미국 내 SMR 설계 검증을 위한 실증 인프라가 구체화됨에 따라 SMR 상용화 일정이 가시화되고 있음."}

</details>

## SEM-030 · `semantic-c1ddbcc160b82974-unsupported`

**Claim:** 'Holtec's SMR-300 test facility taking shape at INL'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [Holtec's SMR-300 test facility taking shape at INL](https://www.world-nuclear-news.org/articles/holtecs-smr-300-test-facility-taking-shape-at-inl)
- 날짜: 2026-08-27T07:34:40+00:00
- 해시: `c1ddbcc160b82974`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "홀텍, 아이다호 국립연구소에 SMR-300 실증 시설 구축", "summary": "홀텍 인터내셔널이 아이다호 국립연구소(INL)에 SMR-300 실증 시설을 구축 중이며 2027년 가동을 목표로 함.", "detail": "", "implication": "실증 시설을 통해 확보된 열수력 데이터는 향후 SMR-300의 출력 증강을 위한 인허가 변경의 핵심 근거로 활용될 예정이다.", "why_important": "미국 내 SMR 설계 검증을 위한 실증 인프라가 구체화됨에 따라 SMR 상용화 일정이 가시화되고 있음."}

</details>

## SEM-031 · `semantic-e00221331ed5b153-aligned`

**Claim:** [‘10번째 도전’ 고준위 방폐장](1) ‘40년 공회전’ 고준위 방폐장 10...

### Source evidence

- 원문: [[‘10번째 도전’ 고준위 방폐장](1) ‘40년 공회전’ 고준위 방폐장 10...](https://www.dnews.co.kr/uhtml/view.jsp?idxno=202609021304489380384)
- 날짜: 2026-09-03T06:22:00+09:00
- 해시: `e00221331ed5b153`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "정부, 내년 고준위 방사성폐기물 처리장 부지 공모 착수", "summary": "고준위방사성폐기물관리위원회가 내년 상반기 부지 공모를 시작으로 37년 장기 로드맵에 따른 고준위 방폐장 건설을 본격 추진한다.", "detail": "고준위 특별법에 따라 2050년 중간저장시설, 2060년 처분시설 운영을 목표로 총사업비 72조 원 규모의 사업이 진행된다. 위원회는 부지 적합성 조사 통과 지자체에 최대 200억 원을 지원하고, 최종 유치 지역에는 조 단위 특별지원금을 지급할 계획이다. 현재 고리·한빛원전의 저장시설 포화 상태를 고려할 때 부지 선정은 시급한 과제이다.", "implication": "", "why_important": "고준위 방폐장 부지 선정은 원전 운영의 지속가능성을 결정짓는 핵심 현안으로, 내년 공모 결과가 향후 원전 정책의 성패를 좌우할 것이다."}

</details>

## SEM-032 · `semantic-e00221331ed5b153-perturbed`

**Claim:** 원문이 보도한 '[‘10번째 도전’ 고준위 방폐장](1) ‘40년 공회전’ 고준위 방폐장 10...' 사건은 실제로 발생하지 않았다.

### Source evidence

- 원문: [[‘10번째 도전’ 고준위 방폐장](1) ‘40년 공회전’ 고준위 방폐장 10...](https://www.dnews.co.kr/uhtml/view.jsp?idxno=202609021304489380384)
- 날짜: 2026-09-03T06:22:00+09:00
- 해시: `e00221331ed5b153`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "FACT_ERROR"}
- generated context: {"title_kr": "정부, 내년 고준위 방사성폐기물 처리장 부지 공모 착수", "summary": "고준위방사성폐기물관리위원회가 내년 상반기 부지 공모를 시작으로 37년 장기 로드맵에 따른 고준위 방폐장 건설을 본격 추진한다.", "detail": "고준위 특별법에 따라 2050년 중간저장시설, 2060년 처분시설 운영을 목표로 총사업비 72조 원 규모의 사업이 진행된다. 위원회는 부지 적합성 조사 통과 지자체에 최대 200억 원을 지원하고, 최종 유치 지역에는 조 단위 특별지원금을 지급할 계획이다. 현재 고리·한빛원전의 저장시설 포화 상태를 고려할 때 부지 선정은 시급한 과제이다.", "implication": "", "why_important": "고준위 방폐장 부지 선정은 원전 운영의 지속가능성을 결정짓는 핵심 현안으로, 내년 공모 결과가 향후 원전 정책의 성패를 좌우할 것이다."}

</details>

## SEM-033 · `semantic-e00221331ed5b153-unsupported`

**Claim:** '[‘10번째 도전’ 고준위 방폐장](1) ‘40년 공회전’ 고준위 방폐장 10...'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [[‘10번째 도전’ 고준위 방폐장](1) ‘40년 공회전’ 고준위 방폐장 10...](https://www.dnews.co.kr/uhtml/view.jsp?idxno=202609021304489380384)
- 날짜: 2026-09-03T06:22:00+09:00
- 해시: `e00221331ed5b153`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "정부, 내년 고준위 방사성폐기물 처리장 부지 공모 착수", "summary": "고준위방사성폐기물관리위원회가 내년 상반기 부지 공모를 시작으로 37년 장기 로드맵에 따른 고준위 방폐장 건설을 본격 추진한다.", "detail": "고준위 특별법에 따라 2050년 중간저장시설, 2060년 처분시설 운영을 목표로 총사업비 72조 원 규모의 사업이 진행된다. 위원회는 부지 적합성 조사 통과 지자체에 최대 200억 원을 지원하고, 최종 유치 지역에는 조 단위 특별지원금을 지급할 계획이다. 현재 고리·한빛원전의 저장시설 포화 상태를 고려할 때 부지 선정은 시급한 과제이다.", "implication": "", "why_important": "고준위 방폐장 부지 선정은 원전 운영의 지속가능성을 결정짓는 핵심 현안으로, 내년 공모 결과가 향후 원전 정책의 성패를 좌우할 것이다."}

</details>

## SEM-034 · `semantic-dcb975d82d845013-aligned`

**Claim:** IsoEnergy and DISA to create new US uranium company

### Source evidence

- 원문: [IsoEnergy and DISA to create new US uranium company](https://www.world-nuclear-news.org/articles/isoenergy-and-disa-to-create-new-us-uranium-company)
- 날짜: 2026-08-06T15:17:52+00:00
- 해시: `dcb975d82d845013`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "아이소에너지와 DISA, 미국 우라늄 신규 합작사 설립 합의", "summary": "아이소에너지와 DISA 테크놀로지스는 생산, 처리, 복원 기술을 갖춘 미국 우라늄 플랫폼인 'DISA 우라늄'을 설립하기로 합의했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-035 · `semantic-dcb975d82d845013-perturbed`

**Claim:** IsoEnergy and DISA to create new US uranium company 관련 공식 수치는 999건이다.

### Source evidence

- 원문: [IsoEnergy and DISA to create new US uranium company](https://www.world-nuclear-news.org/articles/isoenergy-and-disa-to-create-new-us-uranium-company)
- 날짜: 2026-08-06T15:17:52+00:00
- 해시: `dcb975d82d845013`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "NUMBER_ERROR"}
- generated context: {"title_kr": "아이소에너지와 DISA, 미국 우라늄 신규 합작사 설립 합의", "summary": "아이소에너지와 DISA 테크놀로지스는 생산, 처리, 복원 기술을 갖춘 미국 우라늄 플랫폼인 'DISA 우라늄'을 설립하기로 합의했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-036 · `semantic-dcb975d82d845013-unsupported`

**Claim:** 'IsoEnergy and DISA to create new US uranium company'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [IsoEnergy and DISA to create new US uranium company](https://www.world-nuclear-news.org/articles/isoenergy-and-disa-to-create-new-us-uranium-company)
- 날짜: 2026-08-06T15:17:52+00:00
- 해시: `dcb975d82d845013`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "아이소에너지와 DISA, 미국 우라늄 신규 합작사 설립 합의", "summary": "아이소에너지와 DISA 테크놀로지스는 생산, 처리, 복원 기술을 갖춘 미국 우라늄 플랫폼인 'DISA 우라늄'을 설립하기로 합의했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-037 · `semantic-babf4bcdf043c2d5-aligned`

**Claim:** 박정성號 통상교섭본부 출범…대미 투자·301조 관세 대응 '동시 시험대...

### Source evidence

- 원문: [박정성號 통상교섭본부 출범…대미 투자·301조 관세 대응 '동시 시험대...](https://www.news1.kr/economy/idustry-trade/6268015)
- 날짜: 2026-08-25T06:00:00+09:00
- 해시: `babf4bcdf043c2d5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "박정성 통상교섭본부장 취임, 대미 투자 및 관세 대응 과제 직면", "summary": "박정성 통상교섭본부장이 취임하며 반도체, 원전 등 전략산업을 지렛대로 한 통상 협상과 대미 투자 대응을 본격화한다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-038 · `semantic-babf4bcdf043c2d5-perturbed`

**Claim:** 한국수력원자력이 '박정성號 통상교섭본부 출범…대미 투자·301조 관세 대응 '동시 시험대...'를 직접 결정하고 발표했다.

### Source evidence

- 원문: [박정성號 통상교섭본부 출범…대미 투자·301조 관세 대응 '동시 시험대...](https://www.news1.kr/economy/idustry-trade/6268015)
- 날짜: 2026-08-25T06:00:00+09:00
- 해시: `babf4bcdf043c2d5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "ENTITY_ERROR"}
- generated context: {"title_kr": "박정성 통상교섭본부장 취임, 대미 투자 및 관세 대응 과제 직면", "summary": "박정성 통상교섭본부장이 취임하며 반도체, 원전 등 전략산업을 지렛대로 한 통상 협상과 대미 투자 대응을 본격화한다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-039 · `semantic-babf4bcdf043c2d5-unsupported`

**Claim:** '박정성號 통상교섭본부 출범…대미 투자·301조 관세 대응 '동시 시험대...'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [박정성號 통상교섭본부 출범…대미 투자·301조 관세 대응 '동시 시험대...](https://www.news1.kr/economy/idustry-trade/6268015)
- 날짜: 2026-08-25T06:00:00+09:00
- 해시: `babf4bcdf043c2d5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "박정성 통상교섭본부장 취임, 대미 투자 및 관세 대응 과제 직면", "summary": "박정성 통상교섭본부장이 취임하며 반도체, 원전 등 전략산업을 지렛대로 한 통상 협상과 대미 투자 대응을 본격화한다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-040 · `semantic-b32f177b682dce84-aligned`

**Claim:** 대불산단 직주근접·대불초 인접… 10년 민간임대 're100 렉시안 써밋' ...

### Source evidence

- 원문: [대불산단 직주근접·대불초 인접… 10년 민간임대 're100 렉시안 써밋' ...](http://www.ikld.kr/news/articleView.html?idxno=340062)
- 날짜: 2026-09-04T09:02:00+09:00
- 해시: `b32f177b682dce84`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "전남 영암·해남 지역 AI 컴퓨팅센터 및 재생에너지 인프라 구축 본격화", "summary": "전남 해남 솔라시도 데이터센터 파크 내 '국가 AI 컴퓨팅센터'가 착공되었으며, 인근에 대규모 태양광 발전단지 조성도 추진 중이다.", "detail": "과기정통부는 2028년까지 AI 반도체 1만 5000장 규모의 컴퓨팅 인프라를 구축할 계획이다. 또한 한국남동발전은 해남군 일원에 400MW급 재생복합단지 태양광 발전사업을 착공했다. 이는 지역 산업 기반 확대 및 무탄소 전력 공급을 목표로 한다.", "implication": "지역 거점형 AI 데이터센터와 재생에너지 인프라의 결합을 통해 전력 자립형 데이터센터 모델이 추진되고 있다.", "why_important": ""}

</details>

## SEM-041 · `semantic-b32f177b682dce84-perturbed`

**Claim:** '대불산단 직주근접·대불초 인접… 10년 민간임대 're100 렉시안 써밋' ...' 사건은 2099년 1월 1일 발생했다.

### Source evidence

- 원문: [대불산단 직주근접·대불초 인접… 10년 민간임대 're100 렉시안 써밋' ...](http://www.ikld.kr/news/articleView.html?idxno=340062)
- 날짜: 2026-09-04T09:02:00+09:00
- 해시: `b32f177b682dce84`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "DATE_ERROR"}
- generated context: {"title_kr": "전남 영암·해남 지역 AI 컴퓨팅센터 및 재생에너지 인프라 구축 본격화", "summary": "전남 해남 솔라시도 데이터센터 파크 내 '국가 AI 컴퓨팅센터'가 착공되었으며, 인근에 대규모 태양광 발전단지 조성도 추진 중이다.", "detail": "과기정통부는 2028년까지 AI 반도체 1만 5000장 규모의 컴퓨팅 인프라를 구축할 계획이다. 또한 한국남동발전은 해남군 일원에 400MW급 재생복합단지 태양광 발전사업을 착공했다. 이는 지역 산업 기반 확대 및 무탄소 전력 공급을 목표로 한다.", "implication": "지역 거점형 AI 데이터센터와 재생에너지 인프라의 결합을 통해 전력 자립형 데이터센터 모델이 추진되고 있다.", "why_important": ""}

</details>

## SEM-042 · `semantic-b32f177b682dce84-unsupported`

**Claim:** '대불산단 직주근접·대불초 인접… 10년 민간임대 're100 렉시안 써밋' ...'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [대불산단 직주근접·대불초 인접… 10년 민간임대 're100 렉시안 써밋' ...](http://www.ikld.kr/news/articleView.html?idxno=340062)
- 날짜: 2026-09-04T09:02:00+09:00
- 해시: `b32f177b682dce84`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "전남 영암·해남 지역 AI 컴퓨팅센터 및 재생에너지 인프라 구축 본격화", "summary": "전남 해남 솔라시도 데이터센터 파크 내 '국가 AI 컴퓨팅센터'가 착공되었으며, 인근에 대규모 태양광 발전단지 조성도 추진 중이다.", "detail": "과기정통부는 2028년까지 AI 반도체 1만 5000장 규모의 컴퓨팅 인프라를 구축할 계획이다. 또한 한국남동발전은 해남군 일원에 400MW급 재생복합단지 태양광 발전사업을 착공했다. 이는 지역 산업 기반 확대 및 무탄소 전력 공급을 목표로 한다.", "implication": "지역 거점형 AI 데이터센터와 재생에너지 인프라의 결합을 통해 전력 자립형 데이터센터 모델이 추진되고 있다.", "why_important": ""}

</details>

## SEM-043 · `semantic-5e37eb1f2c024a40-aligned`

**Claim:** 원전현장인력양성원, ‘원전정비원자격제도’ 정부인정 취득

### Source evidence

- 원문: [원전현장인력양성원, ‘원전정비원자격제도’ 정부인정 취득](https://www.kbsm.net/news/view.php?idx=529714)
- 날짜: 2026-08-09T12:48:00+09:00
- 해시: `5e37eb1f2c024a40`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "원전현장인력양성원, 한수원과 공동 운영 '원전정비원자격제도' 정부인정 취득", "summary": "원전현장인력양성원이 한국수력원자력과 공동 운영하는 '원전정비원자격제도'가 이달 4일 고용노동부 산하 한국산업인력공단의 정부인정을 취득했습니다.", "detail": "(재)원전현장인력양성원은 한국수력원자력과 공동 운영하는 '원전정비원자격제도'가 이달 4일 고용노동부 산하 한국산업인력공단의 정부인정을 취득했다고 밝혔습니다. 이 자격제도는 원전 정비협력사별로 분산 운영되던 사내자격을 통합한 표준화 모델로, 기계·전기·계측 등 3대 정비분야 15개 자격종목을 표준화하여 원전 정비인력의 전문성을 체계적으로 평가할 수 있도록 했습니다. 지난달 8일 실시된 제1차 원전정비원자격 필기평가에는 133명이 응시하여 25%의 합격률을 기록했습니다.", "implication": "", "why_important": ""}

</details>

## SEM-044 · `semantic-5e37eb1f2c024a40-perturbed`

**Claim:** '원전현장인력양성원, ‘원전정비원자격제도’ 정부인정 취득' 조치는 전 세계 모든 원전에 동일하게 적용된다.

### Source evidence

- 원문: [원전현장인력양성원, ‘원전정비원자격제도’ 정부인정 취득](https://www.kbsm.net/news/view.php?idx=529714)
- 날짜: 2026-08-09T12:48:00+09:00
- 해시: `5e37eb1f2c024a40`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "SCOPE_ERROR"}
- generated context: {"title_kr": "원전현장인력양성원, 한수원과 공동 운영 '원전정비원자격제도' 정부인정 취득", "summary": "원전현장인력양성원이 한국수력원자력과 공동 운영하는 '원전정비원자격제도'가 이달 4일 고용노동부 산하 한국산업인력공단의 정부인정을 취득했습니다.", "detail": "(재)원전현장인력양성원은 한국수력원자력과 공동 운영하는 '원전정비원자격제도'가 이달 4일 고용노동부 산하 한국산업인력공단의 정부인정을 취득했다고 밝혔습니다. 이 자격제도는 원전 정비협력사별로 분산 운영되던 사내자격을 통합한 표준화 모델로, 기계·전기·계측 등 3대 정비분야 15개 자격종목을 표준화하여 원전 정비인력의 전문성을 체계적으로 평가할 수 있도록 했습니다. 지난달 8일 실시된 제1차 원전정비원자격 필기평가에는 133명이 응시하여 25%의 합격률을 기록했습니다.", "implication": "", "why_important": ""}

</details>

## SEM-045 · `semantic-5e37eb1f2c024a40-unsupported`

**Claim:** '원전현장인력양성원, ‘원전정비원자격제도’ 정부인정 취득'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [원전현장인력양성원, ‘원전정비원자격제도’ 정부인정 취득](https://www.kbsm.net/news/view.php?idx=529714)
- 날짜: 2026-08-09T12:48:00+09:00
- 해시: `5e37eb1f2c024a40`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "원전현장인력양성원, 한수원과 공동 운영 '원전정비원자격제도' 정부인정 취득", "summary": "원전현장인력양성원이 한국수력원자력과 공동 운영하는 '원전정비원자격제도'가 이달 4일 고용노동부 산하 한국산업인력공단의 정부인정을 취득했습니다.", "detail": "(재)원전현장인력양성원은 한국수력원자력과 공동 운영하는 '원전정비원자격제도'가 이달 4일 고용노동부 산하 한국산업인력공단의 정부인정을 취득했다고 밝혔습니다. 이 자격제도는 원전 정비협력사별로 분산 운영되던 사내자격을 통합한 표준화 모델로, 기계·전기·계측 등 3대 정비분야 15개 자격종목을 표준화하여 원전 정비인력의 전문성을 체계적으로 평가할 수 있도록 했습니다. 지난달 8일 실시된 제1차 원전정비원자격 필기평가에는 133명이 응시하여 25%의 합격률을 기록했습니다.", "implication": "", "why_important": ""}

</details>

## SEM-046 · `semantic-ef8497efb48823ba-aligned`

**Claim:** ONE Nuclear Executes Binding LOI for Project Cayman, a 2.88 GW Louisiana Energy Project with Co-Located Data Center Campus – Company Announcement - FT.com

### Source evidence

- 원문: [ONE Nuclear Executes Binding LOI for Project Cayman, a 2.88 GW Louisiana Energy Project with Co-Located Data Center Campus – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMipgFBVV95cUxQbmZPUzl4bnNQZm51SHdRLWtZQklWeGNhVll4QlZvVFdPLVoxZU5XbHBJak9vNkczS04zNHhpc1gtYm84dmFJclVKMDdJdWMtWXFfbGNUYVFUbTRGaDRDOEkwdWxtWmlHTFJCSWt0QjcyME9DMHpMQlV5MTVhV1BZM0hJV1VBS1VTSW1wbFpOM0U5ZzF6cFh0ai1KUm02LWtmeUtsd0JR?oc=5)
- 날짜: 2026-08-31T12:00:00+00:00
- 해시: `ef8497efb48823ba`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "ONE Nuclear, 루이지애나 데이터센터 연계 2.88GW 원전 프로젝트 의향서 체결", "summary": "ONE Nuclear가 루이지애나주에 데이터센터와 연계된 2.88GW 규모의 원전 건설 프로젝트 'Project Cayman' 추진을 위한 구속력 있는 의향서를 체결했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-047 · `semantic-ef8497efb48823ba-perturbed`

**Claim:** 'ONE Nuclear Executes Binding LOI for Project Cayman, a 2.88 GW Louisiana Energy Project with Co-Located Data Center Campus – Company Announcement - FT.com' 관련 사업은 이미 상업운전과 최종 준공을 완료했다.

### Source evidence

- 원문: [ONE Nuclear Executes Binding LOI for Project Cayman, a 2.88 GW Louisiana Energy Project with Co-Located Data Center Campus – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMipgFBVV95cUxQbmZPUzl4bnNQZm51SHdRLWtZQklWeGNhVll4QlZvVFdPLVoxZU5XbHBJak9vNkczS04zNHhpc1gtYm84dmFJclVKMDdJdWMtWXFfbGNUYVFUbTRGaDRDOEkwdWxtWmlHTFJCSWt0QjcyME9DMHpMQlV5MTVhV1BZM0hJV1VBS1VTSW1wbFpOM0U5ZzF6cFh0ai1KUm02LWtmeUtsd0JR?oc=5)
- 날짜: 2026-08-31T12:00:00+00:00
- 해시: `ef8497efb48823ba`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "STAGE_ERROR"}
- generated context: {"title_kr": "ONE Nuclear, 루이지애나 데이터센터 연계 2.88GW 원전 프로젝트 의향서 체결", "summary": "ONE Nuclear가 루이지애나주에 데이터센터와 연계된 2.88GW 규모의 원전 건설 프로젝트 'Project Cayman' 추진을 위한 구속력 있는 의향서를 체결했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-048 · `semantic-ef8497efb48823ba-unsupported`

**Claim:** 'ONE Nuclear Executes Binding LOI for Project Cayman, a 2.88 GW Louisiana Energy Project with Co-Located Data Center Campus – Company Announcement - FT.com'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [ONE Nuclear Executes Binding LOI for Project Cayman, a 2.88 GW Louisiana Energy Project with Co-Located Data Center Campus – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMipgFBVV95cUxQbmZPUzl4bnNQZm51SHdRLWtZQklWeGNhVll4QlZvVFdPLVoxZU5XbHBJak9vNkczS04zNHhpc1gtYm84dmFJclVKMDdJdWMtWXFfbGNUYVFUbTRGaDRDOEkwdWxtWmlHTFJCSWt0QjcyME9DMHpMQlV5MTVhV1BZM0hJV1VBS1VTSW1wbFpOM0U5ZzF6cFh0ai1KUm02LWtmeUtsd0JR?oc=5)
- 날짜: 2026-08-31T12:00:00+00:00
- 해시: `ef8497efb48823ba`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "ONE Nuclear, 루이지애나 데이터센터 연계 2.88GW 원전 프로젝트 의향서 체결", "summary": "ONE Nuclear가 루이지애나주에 데이터센터와 연계된 2.88GW 규모의 원전 건설 프로젝트 'Project Cayman' 추진을 위한 구속력 있는 의향서를 체결했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-049 · `semantic-1440fd7c3cd7cdb5-aligned`

**Claim:** Fusion company Shine announces that it was selected by the DOE for two Genesis Mission projects. PR Newswire

### Source evidence

- 원문: [Fusion company Shine announces that it was selected by the DOE for two Genesis Mission projects. PR Newswire](https://www.prnewswire.com/news-releases/shine-selected-for-two-doe-genesis-mission-projects-advancing-ai-in-nuclear-fuel-recycling-302841934.html)
- 날짜: 2026-08-05T12:53:23+00:00
- 해시: `1440fd7c3cd7cdb5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "퓨전 기업 샤인, 미 에너지부 제네시스 미션 프로젝트 선정", "summary": "퓨전 에너지 기업 샤인이 미 에너지부(DOE)의 제네시스 미션 프로젝트 두 건에 선정되었다고 발표했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-050 · `semantic-1440fd7c3cd7cdb5-perturbed`

**Claim:** 'Fusion company Shine announces that it was selected by the DOE for two Genesis Mission projects. PR Newswire' 때문에 국내 전기요금이 즉시 인상됐다.

### Source evidence

- 원문: [Fusion company Shine announces that it was selected by the DOE for two Genesis Mission projects. PR Newswire](https://www.prnewswire.com/news-releases/shine-selected-for-two-doe-genesis-mission-projects-advancing-ai-in-nuclear-fuel-recycling-302841934.html)
- 날짜: 2026-08-05T12:53:23+00:00
- 해시: `1440fd7c3cd7cdb5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "CAUSALITY_ERROR"}
- generated context: {"title_kr": "퓨전 기업 샤인, 미 에너지부 제네시스 미션 프로젝트 선정", "summary": "퓨전 에너지 기업 샤인이 미 에너지부(DOE)의 제네시스 미션 프로젝트 두 건에 선정되었다고 발표했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-051 · `semantic-1440fd7c3cd7cdb5-unsupported`

**Claim:** 'Fusion company Shine announces that it was selected by the DOE for two Genesis Mission projects. PR Newswire'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [Fusion company Shine announces that it was selected by the DOE for two Genesis Mission projects. PR Newswire](https://www.prnewswire.com/news-releases/shine-selected-for-two-doe-genesis-mission-projects-advancing-ai-in-nuclear-fuel-recycling-302841934.html)
- 날짜: 2026-08-05T12:53:23+00:00
- 해시: `1440fd7c3cd7cdb5`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "퓨전 기업 샤인, 미 에너지부 제네시스 미션 프로젝트 선정", "summary": "퓨전 에너지 기업 샤인이 미 에너지부(DOE)의 제네시스 미션 프로젝트 두 건에 선정되었다고 발표했다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-052 · `semantic-ef6fa5fe7c2b1362-aligned`

**Claim:** Mid-year updates from major uranium producers

### Source evidence

- 원문: [Mid-year updates from major uranium producers](https://www.world-nuclear-news.org/articles/mid-year-updates-from-major-uranium-producers)
- 날짜: 2026-08-04T11:24:43+00:00
- 해시: `ef6fa5fe7c2b1362`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "주요 우라늄 생산 기업 상반기 실적 업데이트", "summary": "카자톰프롬의 2026년 상반기 우라늄 생산량은 전년 대비 증가했고, 카메코는 물류 문제로 생산에 영향을 받았다.", "detail": "", "implication": "주요 우라늄 공급사의 생산 동향은 글로벌 핵연료 시장에 영향을 미칠 것입니다.", "why_important": ""}

</details>

## SEM-053 · `semantic-ef6fa5fe7c2b1362-perturbed`

**Claim:** 'Mid-year updates from major uranium producers'의 후속 조치는 원문 보도 전에 모두 완료됐다.

### Source evidence

- 원문: [Mid-year updates from major uranium producers](https://www.world-nuclear-news.org/articles/mid-year-updates-from-major-uranium-producers)
- 날짜: 2026-08-04T11:24:43+00:00
- 해시: `ef6fa5fe7c2b1362`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "TEMPORAL_ERROR"}
- generated context: {"title_kr": "주요 우라늄 생산 기업 상반기 실적 업데이트", "summary": "카자톰프롬의 2026년 상반기 우라늄 생산량은 전년 대비 증가했고, 카메코는 물류 문제로 생산에 영향을 받았다.", "detail": "", "implication": "주요 우라늄 공급사의 생산 동향은 글로벌 핵연료 시장에 영향을 미칠 것입니다.", "why_important": ""}

</details>

## SEM-054 · `semantic-ef6fa5fe7c2b1362-unsupported`

**Claim:** 'Mid-year updates from major uranium producers'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [Mid-year updates from major uranium producers](https://www.world-nuclear-news.org/articles/mid-year-updates-from-major-uranium-producers)
- 날짜: 2026-08-04T11:24:43+00:00
- 해시: `ef6fa5fe7c2b1362`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "주요 우라늄 생산 기업 상반기 실적 업데이트", "summary": "카자톰프롬의 2026년 상반기 우라늄 생산량은 전년 대비 증가했고, 카메코는 물류 문제로 생산에 영향을 받았다.", "detail": "", "implication": "주요 우라늄 공급사의 생산 동향은 글로벌 핵연료 시장에 영향을 미칠 것입니다.", "why_important": ""}

</details>

## SEM-055 · `semantic-c6bcaab0bd3b1bdd-aligned`

**Claim:** 사람 오가던 길은 끊기고, 전기 실어 나르는 길만 굵어지네

### Source evidence

- 원문: [사람 오가던 길은 끊기고, 전기 실어 나르는 길만 굵어지네](https://www.ohmynews.com/NWS_Web/View/at_pg.aspx?CMPT_CD=P0010&CNTN_CD=A0003264753)
- 날짜: 2026-09-05T11:36:00+09:00
- 해시: `c6bcaab0bd3b1bdd`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "원자력 발전 정책 방향에 대한 사회적 논의", "summary": "원전 비중 축소와 확대에 대한 시민사회의 다양한 의견이 공존하며, 숙의를 통한 정책 결정의 중요성이 강조되고 있다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-056 · `semantic-c6bcaab0bd3b1bdd-perturbed`

**Claim:** '사람 오가던 길은 끊기고, 전기 실어 나르는 길만 굵어지네'의 전망과 계획은 예외 없이 확정됐다.

### Source evidence

- 원문: [사람 오가던 길은 끊기고, 전기 실어 나르는 길만 굵어지네](https://www.ohmynews.com/NWS_Web/View/at_pg.aspx?CMPT_CD=P0010&CNTN_CD=A0003264753)
- 날짜: 2026-09-05T11:36:00+09:00
- 해시: `c6bcaab0bd3b1bdd`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "CERTAINTY_ERROR"}
- generated context: {"title_kr": "원자력 발전 정책 방향에 대한 사회적 논의", "summary": "원전 비중 축소와 확대에 대한 시민사회의 다양한 의견이 공존하며, 숙의를 통한 정책 결정의 중요성이 강조되고 있다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-057 · `semantic-c6bcaab0bd3b1bdd-unsupported`

**Claim:** '사람 오가던 길은 끊기고, 전기 실어 나르는 길만 굵어지네'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [사람 오가던 길은 끊기고, 전기 실어 나르는 길만 굵어지네](https://www.ohmynews.com/NWS_Web/View/at_pg.aspx?CMPT_CD=P0010&CNTN_CD=A0003264753)
- 날짜: 2026-09-05T11:36:00+09:00
- 해시: `c6bcaab0bd3b1bdd`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "원자력 발전 정책 방향에 대한 사회적 논의", "summary": "원전 비중 축소와 확대에 대한 시민사회의 다양한 의견이 공존하며, 숙의를 통한 정책 결정의 중요성이 강조되고 있다.", "detail": "", "implication": "", "why_important": ""}

</details>

## SEM-058 · `semantic-e17805472c05ed7e-aligned`

**Claim:** "AI 붐이 전력망 삼킨다"… 美 15개 지역 중 9곳 '전력 부족 고위험' 경...

### Source evidence

- 원문: ["AI 붐이 전력망 삼킨다"… 美 15개 지역 중 9곳 '전력 부족 고위험' 경...](https://www.g-enews.com/view.php?ud=2026083023475340790c8c1c064d_1)
- 날짜: 2026-08-31T06:16:00+09:00
- 해시: `e17805472c05ed7e`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "美 NERC, 2030년까지 전력망 60%가 전력 부족 위험 직면 경고", "summary": "북미전력신뢰성공사(NERC)는 AI 데이터센터 급증과 송전망 확충 지연으로 인해 2030년까지 미국 내 15개 권역 중 9곳이 전력 부족 고위험군에 처할 것이라고 경고했다.", "detail": "NERC의 최신 장기 신뢰성 평가에 따르면, AI 데이터센터가 전력 수요 증가분의 50%를 견인하며 미국 전력 수요가 연 2%씩 급증하고 있다. 그러나 송전망 프로젝트 900개 중 390개가 인허가 및 공급망 문제로 지연되면서, 폭염 등 극한 기후 발생 시 대규모 정전 위험이 커지고 있다. 특히 인구 밀집 지역인 뉴욕과 펜실베이니아 등이 고위험군으로 분류되어 에너지 안보 위협이 가중되는 상황이다.", "implication": "", "why_important": ""}

</details>

## SEM-059 · `semantic-e17805472c05ed7e-perturbed`

**Claim:** '"AI 붐이 전력망 삼킨다"… 美 15개 지역 중 9곳 '전력 부족 고위험' 경...'은 정부의 공개되지 않은 비밀 지시 때문에 발생했다.

### Source evidence

- 원문: ["AI 붐이 전력망 삼킨다"… 美 15개 지역 중 9곳 '전력 부족 고위험' 경...](https://www.g-enews.com/view.php?ud=2026083023475340790c8c1c064d_1)
- 날짜: 2026-08-31T06:16:00+09:00
- 해시: `e17805472c05ed7e`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "美 NERC, 2030년까지 전력망 60%가 전력 부족 위험 직면 경고", "summary": "북미전력신뢰성공사(NERC)는 AI 데이터센터 급증과 송전망 확충 지연으로 인해 2030년까지 미국 내 15개 권역 중 9곳이 전력 부족 고위험군에 처할 것이라고 경고했다.", "detail": "NERC의 최신 장기 신뢰성 평가에 따르면, AI 데이터센터가 전력 수요 증가분의 50%를 견인하며 미국 전력 수요가 연 2%씩 급증하고 있다. 그러나 송전망 프로젝트 900개 중 390개가 인허가 및 공급망 문제로 지연되면서, 폭염 등 극한 기후 발생 시 대규모 정전 위험이 커지고 있다. 특히 인구 밀집 지역인 뉴욕과 펜실베이니아 등이 고위험군으로 분류되어 에너지 안보 위협이 가중되는 상황이다.", "implication": "", "why_important": ""}

</details>

## SEM-060 · `semantic-e17805472c05ed7e-unsupported`

**Claim:** '"AI 붐이 전력망 삼킨다"… 美 15개 지역 중 9곳 '전력 부족 고위험' 경...'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: ["AI 붐이 전력망 삼킨다"… 美 15개 지역 중 9곳 '전력 부족 고위험' 경...](https://www.g-enews.com/view.php?ud=2026083023475340790c8c1c064d_1)
- 날짜: 2026-08-31T06:16:00+09:00
- 해시: `e17805472c05ed7e`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "美 NERC, 2030년까지 전력망 60%가 전력 부족 위험 직면 경고", "summary": "북미전력신뢰성공사(NERC)는 AI 데이터센터 급증과 송전망 확충 지연으로 인해 2030년까지 미국 내 15개 권역 중 9곳이 전력 부족 고위험군에 처할 것이라고 경고했다.", "detail": "NERC의 최신 장기 신뢰성 평가에 따르면, AI 데이터센터가 전력 수요 증가분의 50%를 견인하며 미국 전력 수요가 연 2%씩 급증하고 있다. 그러나 송전망 프로젝트 900개 중 390개가 인허가 및 공급망 문제로 지연되면서, 폭염 등 극한 기후 발생 시 대규모 정전 위험이 커지고 있다. 특히 인구 밀집 지역인 뉴욕과 펜실베이니아 등이 고위험군으로 분류되어 에너지 안보 위협이 가중되는 상황이다.", "implication": "", "why_important": ""}

</details>

## SEM-061 · `semantic-6d619db5c2ade2f3-aligned`

**Claim:** 새울 3호기 정지 원인은 조작 실수…“터빈 밸브 설정값 잘못 입력”

### Source evidence

- 원문: [새울 3호기 정지 원인은 조작 실수…“터빈 밸브 설정값 잘못 입력”](https://www.hani.co.kr/arti/society/environment/1276238.html)
- 날짜: 2026-09-04T11:06:00+09:00
- 해시: `6d619db5c2ade2f3`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "원안위, 새울 3호기 재가동 승인…정지 원인은 운전원 조작 실수", "summary": "원안위가 시운전 중 자동 정지했던 새울 3호기의 재가동을 승인했으며, 정지 원인은 운전원의 터빈제어밸브 설정값 입력 실수로 확인되었다.", "detail": "원자력안전위원회는 4일 새울 3호기의 재가동을 승인했다. 지난달 11일 시운전 중 발생한 자동 정지는 운전원이 터빈제어밸브 위치제한기 설정값을 반대 방향으로 잘못 입력하여 발생한 것으로 조사되었다. 한수원은 재발 방지를 위해 관련 절차서를 보완하고 운전원 대상 특별교육을 실시할 예정이다.", "implication": "시운전 단계에서의 인적 오류가 확인됨에 따라, 향후 신규 원전 시운전 절차 및 운전원 교육 강화가 필수적이다.", "why_important": "신규 원전의 시운전 과정에서 발생한 인적 오류는 안전성 신뢰도에 직결되는 사안으로, 재발 방지 대책의 이행 여부를 철저히 점검해야 한다."}

</details>

## SEM-062 · `semantic-6d619db5c2ade2f3-perturbed`

**Claim:** 원문이 보도한 '새울 3호기 정지 원인은 조작 실수…“터빈 밸브 설정값 잘못 입력”' 사건은 실제로 발생하지 않았다.

### Source evidence

- 원문: [새울 3호기 정지 원인은 조작 실수…“터빈 밸브 설정값 잘못 입력”](https://www.hani.co.kr/arti/society/environment/1276238.html)
- 날짜: 2026-09-04T11:06:00+09:00
- 해시: `6d619db5c2ade2f3`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "FACT_ERROR"}
- generated context: {"title_kr": "원안위, 새울 3호기 재가동 승인…정지 원인은 운전원 조작 실수", "summary": "원안위가 시운전 중 자동 정지했던 새울 3호기의 재가동을 승인했으며, 정지 원인은 운전원의 터빈제어밸브 설정값 입력 실수로 확인되었다.", "detail": "원자력안전위원회는 4일 새울 3호기의 재가동을 승인했다. 지난달 11일 시운전 중 발생한 자동 정지는 운전원이 터빈제어밸브 위치제한기 설정값을 반대 방향으로 잘못 입력하여 발생한 것으로 조사되었다. 한수원은 재발 방지를 위해 관련 절차서를 보완하고 운전원 대상 특별교육을 실시할 예정이다.", "implication": "시운전 단계에서의 인적 오류가 확인됨에 따라, 향후 신규 원전 시운전 절차 및 운전원 교육 강화가 필수적이다.", "why_important": "신규 원전의 시운전 과정에서 발생한 인적 오류는 안전성 신뢰도에 직결되는 사안으로, 재발 방지 대책의 이행 여부를 철저히 점검해야 한다."}

</details>

## SEM-063 · `semantic-6d619db5c2ade2f3-unsupported`

**Claim:** '새울 3호기 정지 원인은 조작 실수…“터빈 밸브 설정값 잘못 입력”'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [새울 3호기 정지 원인은 조작 실수…“터빈 밸브 설정값 잘못 입력”](https://www.hani.co.kr/arti/society/environment/1276238.html)
- 날짜: 2026-09-04T11:06:00+09:00
- 해시: `6d619db5c2ade2f3`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "원안위, 새울 3호기 재가동 승인…정지 원인은 운전원 조작 실수", "summary": "원안위가 시운전 중 자동 정지했던 새울 3호기의 재가동을 승인했으며, 정지 원인은 운전원의 터빈제어밸브 설정값 입력 실수로 확인되었다.", "detail": "원자력안전위원회는 4일 새울 3호기의 재가동을 승인했다. 지난달 11일 시운전 중 발생한 자동 정지는 운전원이 터빈제어밸브 위치제한기 설정값을 반대 방향으로 잘못 입력하여 발생한 것으로 조사되었다. 한수원은 재발 방지를 위해 관련 절차서를 보완하고 운전원 대상 특별교육을 실시할 예정이다.", "implication": "시운전 단계에서의 인적 오류가 확인됨에 따라, 향후 신규 원전 시운전 절차 및 운전원 교육 강화가 필수적이다.", "why_important": "신규 원전의 시운전 과정에서 발생한 인적 오류는 안전성 신뢰도에 직결되는 사안으로, 재발 방지 대책의 이행 여부를 철저히 점검해야 한다."}

</details>

## SEM-064 · `semantic-66d8933507f8bf75-aligned`

**Claim:** 한 총리, 오는 14일 빌 게이츠 회동…SMR 협력 논의 전망

### Source evidence

- 원문: [한 총리, 오는 14일 빌 게이츠 회동…SMR 협력 논의 전망](http://www.yonhapnewstv.co.kr/news/AKR20260811171231s0U)
- 날짜: 2026-08-11T17:13:00+09:00
- 해시: `66d8933507f8bf75`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "한덕수 국무총리, 빌 게이츠와 14일 SMR 협력 방안 논의 예정", "summary": "한덕수 국무총리가 14일 방한하는 빌 게이츠 게이츠재단 이사장과 만나 테라파워 중심의 소형모듈원자로(SMR) 분야 협력 방안을 논의할 예정이다.", "detail": "한덕수 국무총리가 14일 방한하는 빌 게이츠 게이츠재단 이사장과 회동할 예정이며, 이번 면담은 게이츠 이사장 측의 제안으로 알려졌다. 회동에서는 게이츠 이사장이 설립한 차세대 원전기업 테라파워를 중심으로 SMR 분야 협력 방안이 주요 의제로 다뤄질 전망이다. 게이츠 이사장은 방한 기간 SK와 HD현대 경영진도 만나 테라파워 프로젝트에서 한국 기업의 공급망 참여 확대 등 협업 방안을 논의할 것으로 알려졌다. 그의 방한은 지난해 8월 이후 1년 만으로, 당시에도 이재명 대통령과 김정관 산업통상부 장관 등을 만나 AI와 반도체 산업의 전력 수요 대응을 위한 SMR 역할을 논의했다.", "implication": "한덕수 총리와 빌 게이츠 이사장의 회동은 한국 정부 차원에서 SMR 기술 협력 및 국내 기업의 글로벌 SMR 공급망 참여 확대를 모색하는 기회가 될 것이다.", "why_important": ""}

</details>

## SEM-065 · `semantic-66d8933507f8bf75-perturbed`

**Claim:** 한 총리, 오는 21일 빌 게이츠 회동…SMR 협력 논의 전망

### Source evidence

- 원문: [한 총리, 오는 14일 빌 게이츠 회동…SMR 협력 논의 전망](http://www.yonhapnewstv.co.kr/news/AKR20260811171231s0U)
- 날짜: 2026-08-11T17:13:00+09:00
- 해시: `66d8933507f8bf75`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "NUMBER_ERROR"}
- generated context: {"title_kr": "한덕수 국무총리, 빌 게이츠와 14일 SMR 협력 방안 논의 예정", "summary": "한덕수 국무총리가 14일 방한하는 빌 게이츠 게이츠재단 이사장과 만나 테라파워 중심의 소형모듈원자로(SMR) 분야 협력 방안을 논의할 예정이다.", "detail": "한덕수 국무총리가 14일 방한하는 빌 게이츠 게이츠재단 이사장과 회동할 예정이며, 이번 면담은 게이츠 이사장 측의 제안으로 알려졌다. 회동에서는 게이츠 이사장이 설립한 차세대 원전기업 테라파워를 중심으로 SMR 분야 협력 방안이 주요 의제로 다뤄질 전망이다. 게이츠 이사장은 방한 기간 SK와 HD현대 경영진도 만나 테라파워 프로젝트에서 한국 기업의 공급망 참여 확대 등 협업 방안을 논의할 것으로 알려졌다. 그의 방한은 지난해 8월 이후 1년 만으로, 당시에도 이재명 대통령과 김정관 산업통상부 장관 등을 만나 AI와 반도체 산업의 전력 수요 대응을 위한 SMR 역할을 논의했다.", "implication": "한덕수 총리와 빌 게이츠 이사장의 회동은 한국 정부 차원에서 SMR 기술 협력 및 국내 기업의 글로벌 SMR 공급망 참여 확대를 모색하는 기회가 될 것이다.", "why_important": ""}

</details>

## SEM-066 · `semantic-66d8933507f8bf75-unsupported`

**Claim:** '한 총리, 오는 14일 빌 게이츠 회동…SMR 협력 논의 전망'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [한 총리, 오는 14일 빌 게이츠 회동…SMR 협력 논의 전망](http://www.yonhapnewstv.co.kr/news/AKR20260811171231s0U)
- 날짜: 2026-08-11T17:13:00+09:00
- 해시: `66d8933507f8bf75`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "한덕수 국무총리, 빌 게이츠와 14일 SMR 협력 방안 논의 예정", "summary": "한덕수 국무총리가 14일 방한하는 빌 게이츠 게이츠재단 이사장과 만나 테라파워 중심의 소형모듈원자로(SMR) 분야 협력 방안을 논의할 예정이다.", "detail": "한덕수 국무총리가 14일 방한하는 빌 게이츠 게이츠재단 이사장과 회동할 예정이며, 이번 면담은 게이츠 이사장 측의 제안으로 알려졌다. 회동에서는 게이츠 이사장이 설립한 차세대 원전기업 테라파워를 중심으로 SMR 분야 협력 방안이 주요 의제로 다뤄질 전망이다. 게이츠 이사장은 방한 기간 SK와 HD현대 경영진도 만나 테라파워 프로젝트에서 한국 기업의 공급망 참여 확대 등 협업 방안을 논의할 것으로 알려졌다. 그의 방한은 지난해 8월 이후 1년 만으로, 당시에도 이재명 대통령과 김정관 산업통상부 장관 등을 만나 AI와 반도체 산업의 전력 수요 대응을 위한 SMR 역할을 논의했다.", "implication": "한덕수 총리와 빌 게이츠 이사장의 회동은 한국 정부 차원에서 SMR 기술 협력 및 국내 기업의 글로벌 SMR 공급망 참여 확대를 모색하는 기회가 될 것이다.", "why_important": ""}

</details>

## SEM-067 · `semantic-85808a489f14bf7b-aligned`

**Claim:** 새 원전 말하기 전 '나아리의 12년'에 답하라 [왜냐면]

### Source evidence

- 원문: [새 원전 말하기 전 '나아리의 12년'에 답하라 [왜냐면]](https://news.google.com/rss/articles/CBMiYEFVX3lxTE9mcGtpUlppd1FWVTAxc3M3cUpraEM3OFROQWEwNTM4RHdKUDhkNUVpWTZnMTZWSmhIenVNSUVRSU1SeDZ5MzNodjJYenBBeTdzdjJESmdUWEZLMV9RMjVhUg?oc=5)
- 날짜: 2026-08-31T20:05:00+00:00
- 해시: `85808a489f14bf7b`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "월성원전 인근 나아리 주민 이주 요구와 원자력안전법의 한계", "summary": "월성원전 인근 나아리 주민들이 12년간 제기해 온 이주 요구를 조명하며, 원자력안전법상 안전 범위와 신규 원전 정책의 괴리를 비판한 기고문이다.", "detail": "경북 경주 나아리 주민들은 2014년부터 원전 인근 안전 문제로 이주를 요구해 왔으나, 한수원은 2024년 농성장 철거 소송으로 대응했다. 원자력안전법상 제한구역 경계에 위치한 나아리 주민들은 삼중수소 검출 등 건강 피해를 호소하고 있다. 정부는 이러한 기존 지역의 문제를 해결하지 않은 채 11차 전력수급기본계획에 따른 신규 원전 2기 건설과 영덕 부지 선정을 추진하고 있다.", "implication": "", "why_important": ""}

</details>

## SEM-068 · `semantic-85808a489f14bf7b-perturbed`

**Claim:** 한국수력원자력이 '새 원전 말하기 전 '나아리의 12년'에 답하라 [왜냐면]'를 직접 결정하고 발표했다.

### Source evidence

- 원문: [새 원전 말하기 전 '나아리의 12년'에 답하라 [왜냐면]](https://news.google.com/rss/articles/CBMiYEFVX3lxTE9mcGtpUlppd1FWVTAxc3M3cUpraEM3OFROQWEwNTM4RHdKUDhkNUVpWTZnMTZWSmhIenVNSUVRSU1SeDZ5MzNodjJYenBBeTdzdjJESmdUWEZLMV9RMjVhUg?oc=5)
- 날짜: 2026-08-31T20:05:00+00:00
- 해시: `85808a489f14bf7b`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "ENTITY_ERROR"}
- generated context: {"title_kr": "월성원전 인근 나아리 주민 이주 요구와 원자력안전법의 한계", "summary": "월성원전 인근 나아리 주민들이 12년간 제기해 온 이주 요구를 조명하며, 원자력안전법상 안전 범위와 신규 원전 정책의 괴리를 비판한 기고문이다.", "detail": "경북 경주 나아리 주민들은 2014년부터 원전 인근 안전 문제로 이주를 요구해 왔으나, 한수원은 2024년 농성장 철거 소송으로 대응했다. 원자력안전법상 제한구역 경계에 위치한 나아리 주민들은 삼중수소 검출 등 건강 피해를 호소하고 있다. 정부는 이러한 기존 지역의 문제를 해결하지 않은 채 11차 전력수급기본계획에 따른 신규 원전 2기 건설과 영덕 부지 선정을 추진하고 있다.", "implication": "", "why_important": ""}

</details>

## SEM-069 · `semantic-85808a489f14bf7b-unsupported`

**Claim:** '새 원전 말하기 전 '나아리의 12년'에 답하라 [왜냐면]'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [새 원전 말하기 전 '나아리의 12년'에 답하라 [왜냐면]](https://news.google.com/rss/articles/CBMiYEFVX3lxTE9mcGtpUlppd1FWVTAxc3M3cUpraEM3OFROQWEwNTM4RHdKUDhkNUVpWTZnMTZWSmhIenVNSUVRSU1SeDZ5MzNodjJYenBBeTdzdjJESmdUWEZLMV9RMjVhUg?oc=5)
- 날짜: 2026-08-31T20:05:00+00:00
- 해시: `85808a489f14bf7b`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "월성원전 인근 나아리 주민 이주 요구와 원자력안전법의 한계", "summary": "월성원전 인근 나아리 주민들이 12년간 제기해 온 이주 요구를 조명하며, 원자력안전법상 안전 범위와 신규 원전 정책의 괴리를 비판한 기고문이다.", "detail": "경북 경주 나아리 주민들은 2014년부터 원전 인근 안전 문제로 이주를 요구해 왔으나, 한수원은 2024년 농성장 철거 소송으로 대응했다. 원자력안전법상 제한구역 경계에 위치한 나아리 주민들은 삼중수소 검출 등 건강 피해를 호소하고 있다. 정부는 이러한 기존 지역의 문제를 해결하지 않은 채 11차 전력수급기본계획에 따른 신규 원전 2기 건설과 영덕 부지 선정을 추진하고 있다.", "implication": "", "why_important": ""}

</details>

## SEM-070 · `semantic-816ae30c704e67cd-aligned`

**Claim:** Front-end nuclear fuel supply cooperation: Turning allied interdependence into strategic advantage

### Source evidence

- 원문: [Front-end nuclear fuel supply cooperation: Turning allied interdependence into strategic advantage](https://www.ans.org/news/2026-08-14/article-8223/frontend-nuclear-fuel-supply-cooperation-turning-allied-interdependence-into-strategic-advantage)
- 날짜: 2026-08-14T21:04:04+00:00
- 해시: `816ae30c704e67cd`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: source_aligned
- focus: {"review_focus": "normal_claim"}
- generated context: {"title_kr": "핵연료 공급망 협력: 동맹국 간 상호의존성을 전략적 이점으로", "summary": "AI 및 데이터센터 전력 수요 급증에 대응하기 위해 서방 동맹국 간 핵연료 공급망 협력을 강화해야 한다는 분석이다.", "detail": "채굴부터 변환, 농축, 성형가공에 이르는 핵연료 전주기 역량은 현재 캐나다, 프랑스, 일본, 영국, 미국(Sapporo Five)에 분산되어 있다. 이러한 분산 구조는 취약점으로 보일 수 있으나, 전략적으로 조율될 경우 러시아와 중국의 국가 주도 공급망에 대응할 수 있는 경쟁력 있는 연료 주기를 구축할 수 있다. 특히 금융 지원 격차를 줄이는 것이 협력의 핵심 과제이다.", "implication": "서방 동맹국 간 핵연료 공급망 통합은 러시아·중국 중심의 시장 지배력에 대응하기 위한 필수적인 전략적 과제로 부상하고 있다.", "why_important": ""}

</details>

## SEM-071 · `semantic-816ae30c704e67cd-perturbed`

**Claim:** 'Front-end nuclear fuel supply cooperation: Turning allied interdependence into strategic advantage' 사건은 2099년 1월 1일 발생했다.

### Source evidence

- 원문: [Front-end nuclear fuel supply cooperation: Turning allied interdependence into strategic advantage](https://www.ans.org/news/2026-08-14/article-8223/frontend-nuclear-fuel-supply-cooperation-turning-allied-interdependence-into-strategic-advantage)
- 날짜: 2026-08-14T21:04:04+00:00
- 해시: `816ae30c704e67cd`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: controlled_perturbation
- focus: {"review_focus": "DATE_ERROR"}
- generated context: {"title_kr": "핵연료 공급망 협력: 동맹국 간 상호의존성을 전략적 이점으로", "summary": "AI 및 데이터센터 전력 수요 급증에 대응하기 위해 서방 동맹국 간 핵연료 공급망 협력을 강화해야 한다는 분석이다.", "detail": "채굴부터 변환, 농축, 성형가공에 이르는 핵연료 전주기 역량은 현재 캐나다, 프랑스, 일본, 영국, 미국(Sapporo Five)에 분산되어 있다. 이러한 분산 구조는 취약점으로 보일 수 있으나, 전략적으로 조율될 경우 러시아와 중국의 국가 주도 공급망에 대응할 수 있는 경쟁력 있는 연료 주기를 구축할 수 있다. 특히 금융 지원 격차를 줄이는 것이 협력의 핵심 과제이다.", "implication": "서방 동맹국 간 핵연료 공급망 통합은 러시아·중국 중심의 시장 지배력에 대응하기 위한 필수적인 전략적 과제로 부상하고 있다.", "why_important": ""}

</details>

## SEM-072 · `semantic-816ae30c704e67cd-unsupported`

**Claim:** 'Front-end nuclear fuel supply cooperation: Turning allied interdependence into strategic advantage'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.

### Source evidence

- 원문: [Front-end nuclear fuel supply cooperation: Turning allied interdependence into strategic advantage](https://www.ans.org/news/2026-08-14/article-8223/frontend-nuclear-fuel-supply-cooperation-turning-allied-interdependence-into-strategic-advantage)
- 날짜: 2026-08-14T21:04:04+00:00
- 해시: `816ae30c704e67cd`

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: unsupported_inference
- focus: {"review_focus": "UNSUPPORTED_INFERENCE"}
- generated context: {"title_kr": "핵연료 공급망 협력: 동맹국 간 상호의존성을 전략적 이점으로", "summary": "AI 및 데이터센터 전력 수요 급증에 대응하기 위해 서방 동맹국 간 핵연료 공급망 협력을 강화해야 한다는 분석이다.", "detail": "채굴부터 변환, 농축, 성형가공에 이르는 핵연료 전주기 역량은 현재 캐나다, 프랑스, 일본, 영국, 미국(Sapporo Five)에 분산되어 있다. 이러한 분산 구조는 취약점으로 보일 수 있으나, 전략적으로 조율될 경우 러시아와 중국의 국가 주도 공급망에 대응할 수 있는 경쟁력 있는 연료 주기를 구축할 수 있다. 특히 금융 지원 격차를 줄이는 것이 협력의 핵심 과제이다.", "implication": "서방 동맹국 간 핵연료 공급망 통합은 러시아·중국 중심의 시장 지배력에 대응하기 위한 필수적인 전략적 과제로 부상하고 있다.", "why_important": ""}

</details>

## SEM-073 · `saeul-false-causality`

**Claim:** 8월 11일 자동정지 때문에 7월 31일 새울 3·4호기 사업기간이 연장됐다.

### Source evidence

- 계약 사실: ["새울 3·4호기 건설사업의 사업기간 변경일은 2026-07-31이다.", "새울 3호기는 2026-08-11 시운전 중 자동정지했다.", "사업기간 변경은 새울 3·4호기 전체 사업 범위이고 자동정지는 새울 3호기 범위다."]

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [x] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [x] CAUSALITY_ERROR
- [x] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: user_specified_contract
- focus: {"review_focus": "saeul_contract"}
- generated context: null

</details>

## SEM-074 · `saeul-separate-events`

**Claim:** 새울 3·4호기 사업기간 변경과 새울 3호기 자동정지는 별도 사안이다.

### Source evidence

- 계약 사실: ["새울 3·4호기 건설사업의 사업기간 변경일은 2026-07-31이다.", "새울 3호기는 2026-08-11 시운전 중 자동정지했다.", "사업기간 변경은 새울 3·4호기 전체 사업 범위이고 자동정지는 새울 3호기 범위다."]

### 사람 판정

- [x] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: user_specified_contract
- focus: {"review_focus": "saeul_contract"}
- generated context: null

</details>

## SEM-075 · `saeul-unit-scope-3`

**Claim:** 새울 3호기가 자동정지했다.

### Source evidence

- 계약 사실: ["새울 3·4호기 건설사업의 사업기간 변경일은 2026-07-31이다.", "새울 3호기는 2026-08-11 시운전 중 자동정지했다.", "사업기간 변경은 새울 3·4호기 전체 사업 범위이고 자동정지는 새울 3호기 범위다."]

### 사람 판정

- [x] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: user_specified_contract
- focus: {"review_focus": "saeul_contract"}
- generated context: null

</details>

## SEM-076 · `saeul-unit-scope-4`

**Claim:** 새울 4호기가 자동정지했다.

### Source evidence

- 계약 사실: ["새울 3·4호기 건설사업의 사업기간 변경일은 2026-07-31이다.", "새울 3호기는 2026-08-11 시운전 중 자동정지했다.", "사업기간 변경은 새울 3·4호기 전체 사업 범위이고 자동정지는 새울 3호기 범위다."]

### 사람 판정

- [ ] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [x] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [x] ENTITY_ERROR
- [ ] DATE_ERROR
- [x] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: user_specified_contract
- focus: {"review_focus": "saeul_contract"}
- generated context: null

</details>

## SEM-077 · `saeul-project-scope-34`

**Claim:** 사업기간 변경은 새울 3·4호기 건설사업에 적용된다.

### Source evidence

- 계약 사실: ["새울 3·4호기 건설사업의 사업기간 변경일은 2026-07-31이다.", "새울 3호기는 2026-08-11 시운전 중 자동정지했다.", "사업기간 변경은 새울 3·4호기 전체 사업 범위이고 자동정지는 새울 3호기 범위다."]

### 사람 판정

- [x] PASS
- [ ] REPAIR
- [ ] UNVERIFIABLE
- [ ] BLOCK

Error types (해당 항목 모두 선택):

- [ ] FACT_ERROR
- [ ] NUMBER_ERROR
- [ ] ENTITY_ERROR
- [ ] DATE_ERROR
- [ ] SCOPE_ERROR
- [ ] STAGE_ERROR
- [ ] CAUSALITY_ERROR
- [ ] TEMPORAL_ERROR
- [ ] CERTAINTY_ERROR
- [ ] UNSUPPORTED_INFERENCE

메모:


<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>

- kind: user_specified_contract
- focus: {"review_focus": "saeul_contract"}
- generated context: null

</details>
