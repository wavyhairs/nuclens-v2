# Curation Gold human labeling sheet

원문 입력과 현재 Nuclens 출력을 비교해 각 차원을 사람이 판정합니다.

39개 pending case는 먼저 독립 GPT-5.6 Sol High provisional package로 판정한 뒤
사람이 검수합니다. Sol 결과는 Human Gold가 아니며 canonical에 자동 반영되지
않습니다. 공식 Sol 실행 경로에 `docs/gold-labeling/sol-review/curation-input.jsonl`과
matching schema를 전달하고, 반환 JSONL을 import한 뒤 UI를 실행합니다.

```powershell
python tools/sol_provisional_gold.py --task curation --import-provisional path\to\curation-sol-output.jsonl
python tools/review_gold_labeler.py --task curation
```

UI의 A는 provisional 승인, C는 사람 수정, U는 추가 검수 보류입니다. 일반 저장은
`.eval/gold-labels/curation_labels.json`에만 기록되고, `human_reviewed=true`인 case만
명시적 `Export / Validate` 때 canonical fixture에 반영됩니다.

## CUR-001 · `saeul-title-merge-4da5b7ab6c225c78`

- 원문: [상업운전 앞둔 새울 3호기 자동정지…사업기간도 10개월 연장](https://www.newsis.com/view/NISX20260814_0003749127)
- 날짜: 2026-08-14T11:35:00+09:00
- 해시: `4da5b7ab6c225c78`

### 현재 출력

- title_kr: 상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장
- summary: 새울 3호기가 지난 11일 시운전 중 원자로 출력 80%에서 자동정지했으며, 새울 3·4호기 건설사업의 실시계획상 시행기간이 10개월 연장되었다.
- detail: 새울 3호기는 10월 상업운전이 예상되었으나, 지난 11일 오후 8시 56분경 원자로 출력 80%에서 송전 차단 시운전 시험 중 자동정지했다. 현재 원자로는 안전 정지 상태이며 방사선 특이사항은 없다. 원자력안전위원회(KINS)는 전문가 조사단을 현장에 파견하여 원인과 설비 안전성을 정밀 점검 중이다. 이번 자동정지로 상업운전 일정에 변동이 예상된다. 또한, 새울 3·4호기 건설사업의 실시계획상 시행기간이 기존 2026년 11월 30일에서 2027년 9월 30일까지 10개월 연장되었다. 이는 운영허가와 시운전 등 사업 마무리 기간이 늘어난 데 따른 것이다.
- implication: 새울 3호기의 상업운전 일정이 지연될 가능성이 있으며, 원안위의 조사 결과에 따라 추가적인 안전성 강화 조치나 일정 조정이 있을 수 있다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [x] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [x] FAIL

**stage**

- [ ] PASS
- [x] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [x] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "scope", "stage", "causality"], "domain": "policy"}

</details>

## CUR-002 · `curation-7d5fb2f4f0dad180`

- 원문: [원전·비료로 영토 확장…해외플랜트 수주액 3년새 169% 급증[K-건설, 플...](https://biz.heraldcorp.com/article/10847958)
- 날짜: 2026-08-21T11:36:00+09:00
- 해시: `7d5fb2f4f0dad180`

### 현재 출력

- title_kr: 해외 플랜트 수주 급증, 원전 등 신성장 동력 확보
- summary: 한국의 해외 플랜트 수주액이 3년 새 169% 급증했으며, 원전 등 신성장 분야가 성장을 견인하고 있다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["scope", "stage", "date"], "domain": "reactor"}

</details>

## CUR-003 · `curation-91c28f4c623f0bf0`

- 원문: [[2026 KEPIC Week_현장+]AI 전력수요 폭증··· 전원믹스 조합으로 대응](http://www.epj.co.kr/news/articleView.html?idxno=39245)
- 날짜: 2026-08-26T16:24:00+09:00
- 해시: `91c28f4c623f0bf0`

### 현재 출력

- title_kr: AI 시대 전력수요 대응 위한 균형 잡힌 전원믹스 필요성 제기
- summary: AI·반도체 산업 성장에 따른 전력수요 폭증에 대응하기 위해 원전, LNG, 재생에너지를 아우르는 전원믹스 구축이 강조되었다.
- detail: 유승훈 서울과학기술대 교수는 26일 '2026 KEPIC-Week'에서 AI 시대의 전력 인프라 중요성을 역설했다. 국내 3대 메가프로젝트 추진에 약 39.7GW의 추가 전력이 필요하며, 특정 발전원에 의존하기보다 기저전원인 원전과 유연한 발전원인 LNG, 재생에너지를 조화롭게 운용해야 한다고 분석했다. 특히 원전은 대규모 기저전력 공급에 적합하나 단기 수요 대응에는 한계가 있어 LNG와의 병행이 필요하다고 설명했다.
- implication: AI 데이터센터 전력 수요 대응을 위해 원전의 기저전원 역할과 LNG의 유연성 보완 전략이 향후 전력 정책의 핵심 논리로 자리 잡을 것으로 보인다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "date", "causality"], "domain": "power_market"}

</details>

## CUR-004 · `curation-a0e2cb7843a93c7c`

- 원문: [푸틴 "원전 30GW 추가 건설"…글로벌 수출 시장 장악 나선다](https://news.google.com/rss/articles/CBMiZEFVX3lxTFBxNDVnWGFkR28zMUhtemRPdzdRenJDY3luWERfRGo4aVNhUXdSNTFRMjlscGs1ODE0ZkJYVFF6cTdlaXQ1VFR1QVpWM3lhVjlkTVlXaW1IYTJ2b2praVFjXzhpOUo?oc=5)
- 날짜: 2026-08-22T03:19:34+00:00
- 해시: `a0e2cb7843a93c7c`

### 현재 출력

- title_kr: 러시아, 원전 30GW 추가 건설 및 글로벌 수출 확대 추진
- summary: 블라디미르 푸틴 러시아 대통령이 30GW 규모의 원전 추가 건설과 해외 수출 확대를 공식화함.
- detail: (없음)
- implication: 러시아가 원전 수출 시장에서의 영향력을 강화하기 위해 대규모 내수 건설과 수출 전략을 병행하고 있어 글로벌 원전 시장 경쟁이 심화될 것임.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date", "causality"], "domain": "policy"}

</details>

## CUR-005 · `curation-6d402bf98f9187b2`

- 원문: [내년 정부 R&D 예산 40兆 돌파 여부 주목](https://www.etnews.com/20260825000262)
- 날짜: 2026-08-25T16:16:00+09:00
- 해시: `6d402bf98f9187b2`

### 현재 출력

- title_kr: 정부, 내년 R&D 예산 40조 돌파 및 SMR·양자 등 SEED 프로젝트 추진
- summary: 정부가 내년 R&D 예산 40조원 돌파를 추진하며, SMR·양자·우주항공 등 미래 유망기술에 투자하는 SEED 프로젝트를 본격화한다.
- detail: 정부는 내년도 R&D 예산을 40조원 규모로 편성할 것으로 기대된다. 주요 투자 분야는 AI, 반도체, 바이오 등 첨단 산업이며, 특히 SMR, 양자, 우주항공 분야에 투자하는 'SEED 프로젝트'와 8대 분야 12대 국가 난제를 해결하는 'K-문샷' 프로젝트가 포함되었다. 정부안은 다음 달 1일 국무회의에서 의결될 예정이다.
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "stage", "date"], "domain": "smr"}

</details>

## CUR-006 · `curation-04b60a0ede3e9484`

- 원문: [중저준위 방폐장 시설 변경…원안위 허가](https://www.yna.co.kr/view/AKR20260821077600017?input=1195m)
- 날짜: 2026-08-21T11:14:00+09:00
- 해시: `04b60a0ede3e9484`

### 현재 출력

- title_kr: 원안위, 중저준위 방폐장 표층처분시설 건설·운영변경허가 의결
- summary: 원자력안전위원회가 한국원자력환경공단의 중저준위 방사성폐기물 표층처분시설에 대한 건설·운영변경허가안을 심의·의결했다.
- detail: 원안위는 20일 제2026-12회 회의를 통해 한국원자력환경공단(KORAD)의 변경허가안을 승인했다. 이번 허가는 지난 3월 사용전 검사 지적사항에 대한 조치 결과와 준공도면 정보를 안전성분석 보고서에 반영하기 위한 것이다. 주요 내용에는 지진감시계통 응답 기준 명확화, 방사선감시 계통 경보기능 변경, 소방 및 공조설비 사양 변경 등이 포함되었다. 원안위는 해당 시설이 법령상 허가기준에 적합함을 확인했다.
- implication: 표층처분시설의 안전성 보완 조치가 완료됨에 따라 향후 운영 안정성이 확보되었으며, 방폐장 운영의 법적 절차가 마무리되었다.
- why_important: 방사성폐기물 처분시설의 안전성 관련 인허가 사항이 공식적으로 의결됨에 따라, 향후 방폐장 운영의 안정적 기반이 마련되었다.

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "scope", "stage", "date", "causality"], "domain": "waste_or_fusion"}

</details>

## CUR-007 · `curation-1ce9bd9cd45a8831`

- 원문: [IsoEnergy and DISA Technologies to Form DISA Uranium Corporation, Creating a Technology-Enabled U.S. Uranium Platform for Production, Processing, and Remediation – Company Announcement - FT.com](https://news.google.com/rss/articles/CBMilgFBVV95cUxNSm1iaDVwdW5haFY0cjF3dGlzcXhNMVJBSFNYN050TzNPS3UteVV4QTMzUUUwZTBOU2ZpRTRpZWN4Y0NZb2F4a3J1QV9iOVJDcDljNHprV0hrVExkdDM3bUk4YnFQdVpheXlYNnJfbDJtSlN1cllYU0FQMmY1S0o2bjJIRU5zb1E0YklfQ0NLN1M3RzFSRUE?oc=5)
- 날짜: 2026-08-04T11:00:00+00:00
- 해시: `1ce9bd9cd45a8831`

### 현재 출력

- title_kr: 아이소에너지·DISA 테크놀로지, DISA 우라늄 합작법인 설립
- summary: 아이소에너지와 DISA 테크놀로지가 미국 우라늄 생산·처리 플랫폼을 위한 DISA 우라늄 합작법인을 설립한다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "supply_chain_or_general"}

</details>

## CUR-008 · `curation-4ee5aef7063ebdda`

- 원문: [Bechtel has taken over management of Poland’s first nuclear power plant site, shifting the project from design to constr](https://finance.yahoo.com/energy/articles/bechtel-assumes-management-polish-nuclear-100000602.html)
- 날짜: 2026-08-10T13:11:17+00:00
- 해시: `4ee5aef7063ebdda`

### 현재 출력

- title_kr: 벡텔, 폴란드 첫 원전 부지 관리 인수…설계에서 건설 단계로 전환
- summary: 벡텔이 폴란드 첫 원전 부지 관리를 인수하며 프로젝트가 설계 단계에서 대규모 부지 준비 및 건설 단계로 전환되었다.
- detail: 벡텔은 폴란드 최초 원전 부지 관리를 폴란드 원자력공사(PEJ)로부터 인수하며 프로젝트를 설계 및 계획 단계에서 대규모 부지 준비 및 건설 단계로 전환했다. 이번 인수는 초체보(Choczewo) 부지에서 토목 공사가 시작되는 시점과 일치하며, 폴란드의 장기 에너지 전략에 중요한 진전을 의미한다. 벡텔은 이제 건설 운영을 감독하고, 계약업체를 조율하며, 현장 활동을 감독하고, 프로젝트 전반에 걸쳐 안전 기준을 보장할 예정이다. 현재 400명 이상의 인력이 초기 현장 작업에 참여하고 있으며, 이 중 약 3분의 1은 폴란드 현지에서 채용되었다.
- implication: 폴란드 최초 원전 건설 프로젝트가 부지 준비 및 토목 공사 착수로 본격적인 물리적 건설 단계에 진입하며 에너지 전략 이행에 속도를 내고 있다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date", "causality"], "domain": "reactor"}

</details>

## CUR-009 · `curation-e000f116b94e6aa9`

- 원문: [HD현대일렉트릭, 美 재가동 원전에 '262톤급' 초대형 변압기 공급…대형...](https://www.theguru.co.kr/news/article.html?no=106029)
- 날짜: 2026-08-20T13:44:00+09:00
- 해시: `e000f116b94e6aa9`

### 현재 출력

- title_kr: HD현대일렉트릭, 美 크레인 원전 재가동 프로젝트에 변압기 공급
- summary: HD현대일렉트릭이 미국 크레인 청정에너지센터(구 TMI-1) 재가동 프로젝트에 262톤급 주 변압기를 공급했다.
- detail: 컨스텔레이션 에너지는 16억 달러를 투자해 내년 835MW 규모의 무탄소 전력 생산 재개를 목표로 하고 있다. NRC의 연료 면허 승인 등 재가동 절차가 진행 중이며, 생산 전력은 마이크로소프트 데이터센터에 공급될 예정이다.
- implication: 미국 내 원전 재가동 프로젝트가 구체화됨에 따라 국내 전력기기 업체의 북미 시장 진출이 가속화되고 있다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "scope", "stage", "date", "causality"], "domain": "power_market"}

</details>

## CUR-010 · `curation-92f35626180b393c`

- 원문: [8월 26일 모닝뉴스 헤드라인](https://www.radiokorea.com/news/article.php?uid=502498)
- 날짜: 2026-08-26T23:56:00+09:00
- 해시: `92f35626180b393c`

### 현재 출력

- title_kr: 미국, 사우디 원자력협정안 의회 제출…우라늄 농축 허용 논란
- summary: 트럼프 대통령이 사우디아라비아와의 원자력협정안을 의회에 제출하며 사우디 내 우라늄 농축 허용 여부가 쟁점으로 부상했다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "policy"}

</details>

## CUR-011 · `curation-f158b9b4c01d6732`

- 원문: [25주년 맞은 한수원노조, '한국원자력공사 설립' 비전 제시](http://www.e-platform.net/news/articleView.html?idxno=104414)
- 날짜: 2026-08-06T21:36:00+09:00
- 해시: `f158b9b4c01d6732`

### 현재 출력

- title_kr: 한수원노조 25주년, '한국원자력공사 설립' 비전 제시
- summary: 한수원노조 25주년 기념식에서 김회천 사장이 신한울 3,4호기, 영덕 대형원전, 기장 SMR 건설 등 주요 사업의 성공적 추진을 강조했다.
- detail: (없음)
- implication: 한수원(KHNP) 노조는 한국원자력공사 설립 비전을 제시하며, 신규 원전 및 SMR 건설 등 주요 사업의 성공적 추진을 위한 노사 협력을 강조했다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["scope", "date", "causality"], "domain": "smr"}

</details>

## CUR-012 · `curation-4355a09e720fdfd5`

- 원문: [원자력환경공단, 방사성폐기물 관리에 로봇·AI 도입… 노동강도 줄인다](https://www.segye.com/newsView/20260809512571?OutUrl=naver)
- 날짜: 2026-08-09T23:02:00+09:00
- 해시: `4355a09e720fdfd5`

### 현재 출력

- title_kr: 원자력환경공단, 방사성폐기물 관리에 로봇·AI 시스템 도입
- summary: 한국원자력환경공단이 클로봇 등 3개 기관과 협약하여 내년 11월까지 방사성폐기물 처분시설에 6종의 로봇과 AI 통합관제시스템을 도입한다.
- detail: 한국원자력환경공단은 9일 경북 경주 본사에서 클로봇, 티로보틱스, 경북ICT융합산업진흥협회와 중·저준위방사성폐기물 처분시설(방폐장) 로봇 실증사업을 위한 업무협약을 체결했다. 협약 기관들은 내년 11월까지 방폐장에 자율주행 로봇, 무인 지게차 로봇 등 6종의 로봇과 AI 통합관제시스템을 도입하여 방폐물 관리 절차 자동화 및 국가보안시설 물리적 방호 강화를 추진한다. 공단은 이를 통해 노동강도를 줄이고 작업자의 방사선 피폭량을 40% 감소시킬 것으로 기대하며, 향후 원자력발전소, 원전 해체 현장, 고준위 방폐물 처분시설 등 고위험 시설로의 확산을 예상하고 있다.
- implication: 한국원자력환경공단이 로봇 및 AI 기술을 방폐물 관리에 도입하여 작업자 안전과 효율성을 높이고, 향후 원전 운영 및 해체 현장에도 적용될 가능성을 열었다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date", "causality"], "domain": "waste_or_fusion"}

</details>

## CUR-013 · `curation-18448786536ce1cd`

- 원문: [IsoEnergy and DISA Technologies Announce Closing of Transaction to Form DISA Uranium Corporation](https://news.google.com/rss/articles/CBMimAFBVV95cUxPSjdMU2gxQmc2a181M09Ta2RjLWRmSktTWmxZU2YtUVU3N3d0aXV0Nk52RUE4RnNrUDdMTHVVTWtsRnRLWlBsUW9vSkpRYXlmT09nblMyMGdnR0tJbHVyaTlweHZBMkV2dExqT3RBQ2Rna1ZJZy1RYmlqU1JiXzgzUGo0SVV3QkpIWlNucVdiMEM3UmxkQV9oOQ?oc=5)
- 날짜: 2026-08-19T21:00:00+00:00
- 해시: `18448786536ce1cd`

### 현재 출력

- title_kr: IsoEnergy와 DISA Technologies, DISA Uranium Corporation 설립 완료
- summary: IsoEnergy와 DISA Technologies가 합병을 통해 DISA Uranium Corporation을 설립했다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "supply_chain_or_general"}

</details>

## CUR-014 · `curation-3f0c0de0eae91573`

- 원문: [한수원, 필리핀 에너지기업과 원전 협력…동남아 시장 공략 본격화](https://www.kyongbuk.co.kr/news/articleView.html?idxno=4080500)
- 날짜: 2026-08-06T18:04:00+09:00
- 해시: `3f0c0de0eae91573`

### 현재 출력

- title_kr: 한수원, 필리핀 에너지기업과 원전 협력 MOU 체결
- summary: 한국수력원자력(한수원)이 필리핀 아보이티즈 파워와 원전 사업 협력을 위한 양해각서(MOU)를 체결했다.
- detail: (없음)
- implication: 이번 MOU는 필리핀 정부의 신규 원전 도입 계획에 발맞춰 동남아 시장 진출을 위한 첫 단계이다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["scope", "date", "causality"], "domain": "reactor"}

</details>

## CUR-015 · `curation-74c753527a5e88ed`

- 원문: [독일 재생에너지 248GW 증설…계통 병목, 韓 원전·SMR 시사점](https://news.google.com/rss/articles/CBMiZEFVX3lxTE5Yb1h3X2pDVGdHSXhfcjk3SEN2MTlrSFd1amUyUldqNzFvMHJXTFB6SDIxNHRGczlrV1pQWDFaUEZZaVk3a1JZZ3JIWE5veVc4aWs1NmhacWJzT3F0a1lNOWFubVQ?oc=5)
- 날짜: 2026-08-17T23:53:44+00:00
- 해시: `74c753527a5e88ed`

### 현재 출력

- title_kr: 독일 재생에너지 248GW 증설에 따른 계통 병목 현상과 시사점
- summary: 독일의 재생에너지 248GW 증설 계획에 따른 전력 계통 병목 현상과 이에 대한 시사점을 다룬 일반 보도임.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "power_market"}

</details>

## CUR-016 · `curation-f19d023d5c9863e4`

- 원문: [영덕에 들어설 신규 원전 2기…기후평가 첫 적용 사례](https://www.hankyung.com/article/202608126756i)
- 날짜: 2026-08-12T15:31:00+09:00
- 해시: `f19d023d5c9863e4`

### 현재 출력

- title_kr: 영덕 신규 원전 2기, 기후평가 첫 적용…9월 전략환경영향평가서 제출
- summary: 영덕에 들어설 신규 원전 2기에 기후평가가 처음으로 적용되며, 한국수력원자력은 오는 9월 전략환경영향평가서 초안을 제출하고 12월 본 평가서를 제출할 예정입니다.
- detail: 영덕에 들어설 신규 원전 2기에 기후평가가 처음으로 적용되며, 한국수력원자력은 오는 9월 전략환경영향평가서 초안을 제출하고 12월 본 평가서를 제출할 예정입니다. 정부는 전략환경영향평가를 통해 원전 건설에 따른 생태계와 해양환경 영향도 살필 계획입니다.
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "policy"}

</details>

## CUR-017 · `curation-159ee56b79e02a3a`

- 원문: [[더벨][범한메카텍 IPO] 홍콩·싱가포르 NDR…글로벌 투자자 스킨십 확대](https://www.thebell.co.kr/free/content/ArticleView.asp?key=202608141530211160109361)
- 날짜: 2026-08-19T10:02:00+09:00
- 해시: `159ee56b79e02a3a`

### 현재 출력

- title_kr: 범한메카텍, 홍콩·싱가포르 NDR 통해 글로벌 투자자 스킨십 확대
- summary: 범한메카텍이 홍콩·싱가포르 NDR에서 가스터빈 부품 및 차세대 SMR 등 고부가가치 발전기기 중심의 포트폴리오를 공유했다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["stage", "date"], "domain": "smr"}

</details>

## CUR-018 · `curation-6a779c07bd7dffaf`

- 원문: [Center for Used Fuel Research: Building confidence in storage and transport](https://www.ans.org/news/2026-08-21/article-8225/center-for-used-fuel-research-building-confidence-in-storage-and-transport)
- 날짜: 2026-08-21T20:02:22+00:00
- 해시: `6a779c07bd7dffaf`

### 현재 출력

- title_kr: 미국 에너지부, 사용후핵연료 연구센터(CUFR) 설립
- summary: 미국 에너지부(DOE)는 사용후핵연료의 장기 저장 및 운송 기술 개발을 위해 아이다호 국립연구소 내에 CUFR을 설립했다.
- detail: (없음)
- implication: 미국 정부가 사용후핵연료의 장기 관리 및 차세대 연료 저장 안전성 확보를 위해 국가 차원의 연구 거점을 공식화했다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "scope", "date", "causality"], "domain": "waste_or_fusion"}

</details>

## CUR-019 · `curation-6491a01762f37f00`

- 원문: [Uranium : la Namibie attire toujours plus d’investissements, sur fond d'instabilité au Niger](https://news.google.com/rss/articles/CBMi3AFBVV95cUxNczJ4QWNlMmU4dEg5ZERraEp2T19TS0tFb2R0YlR3cU5USlUzUy13TjJGbEQ0eXVreFVDY196RmZ4dFF2anRVSExaNURpNnNkeGMzaUtLeURqZnYzaER0eDZ5X3dCb0hzUXNtZDZ6Qm5ZbUMxWUZyLUJzNzh0T01pWE9lbmMwYnRDTVl3elpfYmZrOEM0dWNDQjMtNEtZYmVVeGlpazNidk1JenpGeUxDcWE1WV9IOHJPUGRxYXlLMWpOc3NiUmxWWFhteUJXMzQwbENuM2xBWmVtX25I?oc=5)
- 날짜: 2026-09-04T06:45:01+00:00
- 해시: `6491a01762f37f00`

### 현재 출력

- title_kr: 나미비아, 니제르 정세 불안 속 우라늄 투자 유치 확대
- summary: 니제르의 정치적 불안정으로 인해 우라늄 공급망 다변화를 꾀하는 글로벌 투자자들이 나미비아로 유입되고 있음.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "supply_chain_or_general"}

</details>

## CUR-020 · `curation-664deb91e21ba138`

- 원문: [전기산업연합회 “SMR 등 신기술 성능·위험도 기반 기술기준 확대”](https://news.google.com/rss/articles/CBMibkFVX3lxTFBwOWVWTlBIbl9Ca0l6bTBjcHJDbnNCeXhPNl9hRVVrelhQTWlHQlVfcjFEZi1FMDlIVFZjbkltT3dzOHFaVzlidXh1RU4ycTVraEJ3X1ZMMVNXb2lxT3phcDJoajNJdXBQa0pIUEpB?oc=5)
- 날짜: 2026-07-29T10:26:58.141009+00:00
- 해시: `664deb91e21ba138`

### 현재 출력

- title_kr: 전기산업연합회, SMR 등 신기술 기반 기술기준 확대 필요성 제기
- summary: 전기산업연합회가 SMR 등 신기술의 성능 및 위험도 기반 기술기준 확대 필요성을 주장했습니다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "reactor"}

</details>

## CUR-021 · `curation-d2f01ee0bf2a4d0e`

- 원문: [[농정춘추] AI 국가경쟁력, 농촌의 희생 위에 세울 것인가](http://www.ikpnews.net/news/articleView.html?idxno=71283)
- 날짜: 2026-08-30T18:18:00+09:00
- 해시: `d2f01ee0bf2a4d0e`

### 현재 출력

- title_kr: AI 데이터센터 전력 수요 급증에 따른 에너지 인프라 부담 논의
- summary: AI 데이터센터 확산으로 전력과 물 등 자원 소비가 급증함에 따라 에너지 인프라 부담에 대한 우려가 제기됨.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["stage", "date"], "domain": "power_market"}

</details>

## CUR-022 · `curation-b2a7f008bcd4a986`

- 원문: [엔비디아, 오픈AI에 149조 보증…‘순환 금융’ 논란 재점화](https://www.sedaily.com/article/20080831)
- 날짜: 2026-08-19T13:24:00+09:00
- 해시: `b2a7f008bcd4a986`

### 현재 출력

- title_kr: 정부, 원전 등 전략산업 투자 위한 국부펀드 신설 법안 발의
- summary: 정부가 원전, 반도체, AI 등 전략산업에 투자하는 한국판 전략형 국부펀드 신설 법안을 이달 중 발의한다.
- detail: (없음)
- implication: 정부의 전략산업 국부펀드 신설은 원전 산업에 대한 공적 자금 지원 및 해외 공급망 확보를 위한 정책적 기반이 될 것으로 보인다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["stage", "date", "causality"], "domain": "policy"}

</details>

## CUR-023 · `curation-39d2da80812a25d1`

- 원문: [Corpus Christi seaport latest to partner with MARAD on maritime reactors](https://www.ans.org/news/2026-08-24/article-8332/corpus-christi-seaport-latest-to-partner-with-marad-on-maritime-reactors)
- 날짜: 2026-08-24T15:23:28+00:00
- 해시: `39d2da80812a25d1`

### 현재 출력

- title_kr: 미 해사청(MARAD), 코퍼스 크리스티 항만과 SMR 개발 협력 MOU 체결
- summary: 미국 해사청이 코퍼스 크리스티 항만과 SMR, 마이크로그리드 등 해양 에너지 시스템 개발을 위한 협력 양해각서(MOU)를 체결함.
- detail: 이번 협약은 상업용 선박과 항만 시설에 SMR을 적용하기 위한 두 번째 협력 사례다. 양측은 SMR 기술, 항만 마이크로그리드, 육상 전력 아키텍처 및 차세대 선박 추진 시스템 연구를 공동 추진한다. 미 해사청은 SMR이 미국의 해양 부문을 재편하고 공급망을 강화할 것으로 기대하고 있다.
- implication: SMR의 활용 범위가 육상 전력망을 넘어 항만 및 해양 운송 분야로 공식 확장됨.
- why_important: SMR의 해양 분야 적용을 위한 미국 정부 주도의 실질적인 인프라 협력 모델이 구축됨.

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date", "causality"], "domain": "smr"}

</details>

## CUR-024 · `curation-241326f7fce25ba2`

- 원문: [Fusion fuel tech makes commercial leap](https://news.google.com/rss/articles/CBMifEFVX3lxTE5VNzRBVkRHSkprdm9pZFgxZ2xubG02VFRSRndxM3ZSMXM1enNQaTR2MEtCcll3MmpqNm5QcENOWF9lVEdWWFNjZHpsVGdUNG03aDd5cWtYMmp6Q3FhYjZ5VG5SSkJQRWJuU2tNQ0Nla1d4dWNydUNCTEIxNUo?oc=5)
- 날짜: 2026-08-11T23:35:02+00:00
- 해시: `241326f7fce25ba2`

### 현재 출력

- title_kr: 핵융합 연료 기술, 상업화 도약
- summary: 핵융합 연료 기술이 상업화를 위한 중요한 진전을 이루었다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "waste_or_fusion"}

</details>

## CUR-025 · `curation-4c60761f7385897c`

- 원문: [LIST takes first steps toward FUEL](https://news.google.com/rss/articles/CBMid0FVX3lxTE1yVmZQYVZOemQ4MDFTRlFkck0zTkJlMndmQW1xNTV5ZXFjSjlRV0JqU0dZU2I1NFRxU2VqLTlMYWxLbTZVbXBhUVI4WGJhd01MZUF6Mkw0bDhDNWlCUFlKd2VNeHR2RmNwY2FJdzBUNzQ4Q0NvblFR?oc=5)
- 날짜: 2026-09-02T09:25:37+00:00
- 해시: `4c60761f7385897c`

### 현재 출력

- title_kr: LIST, 핵연료 관련 초기 단계 연구 착수
- summary: 룩셈부르크 과학기술연구소(LIST)가 핵연료 주기와 관련된 초기 연구 단계에 돌입했다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "supply_chain_or_general"}

</details>

## CUR-026 · `curation-68c4e2d9638bbef7`

- 원문: [[마켓 브리핑] 투심 불안 속 올해도 '9월의 징크스' 반복 주목](https://biz.sbs.co.kr/article_hub/20000331885?division=NAVER)
- 날짜: 2026-09-01T07:18:00+09:00
- 해시: `68c4e2d9638bbef7`

### 현재 출력

- title_kr: 스페이스X, 데이터센터 전력난 대응 위해 가스터빈 부품 자체 생산
- summary: 스페이스X가 데이터센터 전력난 해결을 위해 가스터빈용 블레이드와 베인을 자체 생산하여 가동 시기를 18개월 앞당길 계획이다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "date"], "domain": "reactor"}

</details>

## CUR-027 · `curation-50e0432378c2f453`

- 원문: [임이자 "용산공원 주택공급 논의 중단하고 원전산업지원특별법 논의하...](https://www.news1.kr/politics/assembly/6265269)
- 날짜: 2026-08-21T09:47:00+09:00
- 해시: `50e0432378c2f453`

### 현재 출력

- title_kr: 임이자 의원, 신규 원전 추가 건설 계획 촉구
- summary: 임이자 국민의힘 정책위의장은 제12차 전력수급기본계획에 급증하는 전력 수요를 반영하고 신규 원전 추가 건설 계획을 준비할 것을 촉구했다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["scope", "date"], "domain": "power_market"}

</details>

## CUR-028 · `curation-f1fcccfcb6d646cb`

- 원문: [[시시비비] 기금은 쉽게, 원칙은 어렵게](https://view.asiae.co.kr/article/2026082415591135233)
- 날짜: 2026-08-25T09:00:00+09:00
- 해시: `f1fcccfcb6d646cb`

### 현재 출력

- title_kr: 전략수출금융기금 제정안 논란
- summary: 방산·원전·플랜트 수출 지원을 위한 전략수출금융기금 제정안이 국회에 제출된 가운데, 필요할 때마다 기금을 신설하는 방식의 적절성에 대한 논의가 제기되고 있다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "policy"}

</details>

## CUR-029 · `curation-0e654033f53fa112`

- 원문: [[사설] 김민석 민주당 대표, 공공기관 부산 이전 적극 지원해야](https://www.busan.com/view/busan/view.php?code=2026081918080780242)
- 날짜: 2026-08-20T05:12:00+09:00
- 해시: `0e654033f53fa112`

### 현재 출력

- title_kr: 부산 지역사회, SMR 및 전력반도체 투자 로드맵 촉구
- summary: 부산 지역에서 정부의 전력반도체 특구 및 SMR 관련 투자 계획과 로드맵 수립을 촉구하는 목소리가 커지고 있다.
- detail: 부산은 2차 공공기관 이전과 함께 산업은행 이전, SMR 및 전력반도체 특구 지정에 따른 구체적인 투자 계획을 요구하고 있다. 김민석 민주당 대표의 부산 방문을 계기로 지역 현안 해결을 위한 정치권의 적극적인 지원이 필요하다는 지적이 제기되었다.
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "stage", "date"], "domain": "smr"}

</details>

## CUR-030 · `curation-5a7829b49165e570`

- 원문: [General Atomics receives a $20M tax credit to build a fusion blanket testing facility in California. KPBS](https://www.kpbs.org/news/science-technology/2026/08/03/general-atomics-awarded-20m-california-tax-credit-for-nuclear-fusion-testing-center)
- 날짜: 2026-08-04T13:07:17+00:00
- 해시: `5a7829b49165e570`

### 현재 출력

- title_kr: 제너럴 아토믹스, 핵융합 블랭킷 시험 시설 건설에 2천만 달러 세액 공제
- summary: 제너럴 아토믹스(General Atomics)가 캘리포니아에 핵융합 블랭킷 시험 시설 건설을 위해 2천만 달러의 세액 공제를 받았다.
- detail: (없음)
- implication: 미국 정부의 핵융합 기술 개발 지원 동향을 파악하여 미래 에너지 기술 투자 방향을 가늠할 수 있습니다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date", "causality"], "domain": "waste_or_fusion"}

</details>

## CUR-031 · `curation-83466c9d90c5bc37`

- 원문: [Antares Nuclear Announces Multi-Year TRISO Fuel Supply Agreement with Standard Nuclear](https://news.google.com/rss/articles/CBMipgFBVV95cUxON0hNdnZHM0hLaEVqb2hrdVlhTWdLQVBMTUVJUldNS08zQlJUeHpwV0t0c3JXc2xJWUJBanZyMm1ZUUJCYWwyS0kxOG0zWGVjLWFTLWdQT21vZlZVOVhWUWNXenkzSUlZcFRMV0E1bXhPaVNnOTRkV3BzaEZ5dEtNYm9welVFQjd0RG0wcHd6OU9rWTZZSGFaZGpYRzRkdTc3aXlqYmdn?oc=5)
- 날짜: 2026-08-27T13:00:00+00:00
- 해시: `83466c9d90c5bc37`

### 현재 출력

- title_kr: 안타레스 뉴클리어, 스탠다드 뉴클리어와 TRISO 연료 공급 계약 체결
- summary: 안타레스 뉴클리어가 스탠다드 뉴클리어와 다년간의 TRISO 연료 공급 계약을 체결했습니다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "supply_chain_or_general"}

</details>

## CUR-032 · `curation-9caacb9fe51ac742`

- 원문: [[원자재 시황] 유가·금리 상승에 시장 혼조](https://www.ebn.co.kr/news/articleView.html?idxno=1720677)
- 날짜: 2026-08-18T09:46:00+09:00
- 해시: `9caacb9fe51ac742`

### 현재 출력

- title_kr: 원자재 시황: 유가·금리 상승에 시장 혼조
- summary: 원전 수요 확대와 에너지 안보 강화 움직임이 지속되는 가운데 원자재 시장이 혼조세를 보이고 있다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "date"], "domain": "reactor"}

</details>

## CUR-033 · `curation-af2db12d3bea65b0`

- 원문: [관세보다 수요·쿼터가 갈랐다… K-철강 美·EU 수출 '희비'](https://it.chosun.com/news/articleView.html?idxno=2023092168987)
- 날짜: 2026-08-28T06:30:00+09:00
- 해시: `af2db12d3bea65b0`

### 현재 출력

- title_kr: 미국 AI 데이터센터 전력 인프라 투자 확대로 한국산 철강 수출 증가
- summary: 미국 내 AI 데이터센터 및 전력 인프라 구축 수요가 급증하며 한국산 철강재의 대미 수출량이 전년 대비 186.2% 증가했다.
- detail: 지난달 대미 철강 수출량은 53만 8,415톤으로 전년 동월 대비 186.2% 급증했다. 미국 내 AI 데이터센터 건설과 전력 인프라 투자 확대로 철근, H형강 등 관련 철강재 수요가 크게 늘어난 결과다. 반면 EU 시장은 새로운 수입 쿼터 규제 시행으로 수출량이 31.4% 감소했다. 미국은 고율 관세에도 불구하고 현지 수요 확대에 따른 가격 경쟁력 확보로 수출이 지속 성장하고 있다.
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "stage", "date"], "domain": "power_market"}

</details>

## CUR-034 · `curation-42f55252997bbbc3`

- 원문: [Site preparations under way for planned Kola units](https://www.world-nuclear-news.org/articles/site-preparations-under-way-for-planned-kola-units)
- 날짜: 2026-07-31T09:27:55+00:00
- 해시: `42f55252997bbbc3`

### 현재 출력

- title_kr: 러시아 콜라 II 원전 부지 준비 작업 착수, 규제기관 인허가 후 진행
- summary: 러시아 규제기관 로스텍흐나드조르(Rostekhnadzor)의 부지 인허가 발급에 따라 콜라 II 원전 건설을 위한 부지 준비 작업이 시작되었다.
- detail: (없음)
- implication: 러시아의 신규 원전 건설 프로젝트가 구체적인 이정표를 달성하며 추진되고 있음을 시사합니다.
- why_important: 러시아 규제기관의 부지 인허가 발급 및 신규 원전 건설을 위한 부지 준비 작업 착수는 주요국 원전 건설 프로젝트의 핵심 이정표입니다. 이는 글로벌 원전 시장 동향 및 러시아의 에너지 정책 방향을 이해하는 데 중요한 정보입니다.

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date", "causality"], "domain": "policy"}

</details>

## CUR-035 · `curation-f1f4a9733a1bafa4`

- 원문: [미국 원전 인허가 대수술…‘규제 속도’도 경쟁력 됐다](https://news.google.com/rss/articles/CBMiZEFVX3lxTE9POGFSYXhxWkk1NjFHNEtDd0c2RnVqRm40b2M1WTRrMmt3czRlSzlwWmllbl9FTHNNbmdMVWRTMmpvQXBUVUF4OFVUVUFxdEFLSjZPMGJYZTlnaUxBTUhOcjlqRUY?oc=5)
- 날짜: 2026-08-30T02:24:48+00:00
- 해시: `f1f4a9733a1bafa4`

### 현재 출력

- title_kr: 미국 원자력 인허가 체계, 규제 속도 경쟁력 강화 방향으로 전환
- summary: 미국이 첨단원자로와 SMR 확산에 대응하여 인허가 체계를 규제 절차 중심에서 경쟁력 강화 인프라로 개편 중임.
- detail: 미국 원자력 인허가 체계가 ‘규제 절차’에서 ‘원전 경쟁력’을 좌우하는 인프라로 바뀌고 있다. 첨단원자로와 SMR, 인공지능(AI), 디지털 엔지니어링이 확산함에 따라 규제 속도가 산업 경쟁력의 핵심 요소로 부상하고 있다.
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["stage", "date"], "domain": "smr"}

</details>

## CUR-036 · `curation-96f34278cf3bb363`

- 원문: [보상을 넘어 존중으로, 건식저장시설의 사회적 해법 [정책발언대]](https://www.etoday.co.kr/news/view/2615294)
- 날짜: 2026-08-19T06:00:00+09:00
- 해시: `96f34278cf3bb363`

### 현재 출력

- title_kr: 사용후핵연료 건식저장시설의 사회적 수용성 확보 방안
- summary: 사용후핵연료의 안전한 관리를 위해 지상 건식저장시설 확충이 필수적이나, 지역사회와의 신뢰 구축 및 사회적 해법 마련이 선행되어야 한다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "date"], "domain": "waste_or_fusion"}

</details>

## CUR-037 · `curation-5e405c7025aea9be`

- 원문: [LIST acquires Oak Ridge isotope facility](https://news.google.com/rss/articles/CBMif0FVX3lxTE14SElLMGZRVWg3YWgzS0g1WkFEZnJjalVnbnpkX2VEM2ZraTBSbFFkV2d3Mi1XOWo2eUxaZmZjQzdJU182NGtUSmxlMEZySXRoeU93d3V3NHZkaU54LXI0MEU4Z1pSeVpZQ1BSdGdHOEQtcDZSc3ZGQ0M2TVcwTGs?oc=5)
- 날짜: 2026-08-19T00:19:07+00:00
- 해시: `5e405c7025aea9be`

### 현재 출력

- title_kr: LIST, 오크리지 동위원소 시설 인수
- summary: LIST(Life Isotope Science and Technology)가 오크리지의 동위원소 생산 시설을 인수했다.
- detail: LIST는 오크리지에 위치한 동위원소 시설을 인수하여 의료 및 산업용 동위원소 생산 역량을 강화할 예정이다. 해당 시설은 기존의 핵연료 및 동위원소 관련 인프라를 활용하여 운영될 것으로 보인다.
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "supply_chain_or_general"}

</details>

## CUR-038 · `curation-2771dd084320e4f0`

- 원문: [영국 원전, ‘제때·예산 안에’ 짓는 능력 되찾아야…원전 경쟁력 핵심은 ‘비용과 공기’](https://news.google.com/rss/articles/CBMiZEFVX3lxTE1XUUpUV1ZSQVNVVXFOSG5FeDYtTl84Q3VIUVNMSjlsLWFmUmF6eGE4U1ZBRW1HOUc0YlNaUFBNQ2dXSnZJYzNQdUUwUC1xUHhHWXlNaThUZXNIWjBOLTdYZlBEZXM?oc=5)
- 날짜: 2026-08-21T20:12:48+00:00
- 해시: `2771dd084320e4f0`

### 현재 출력

- title_kr: 영국 싱크탱크, 원전 건설 경쟁력 복원 강조
- summary: 영국 싱크탱크 온워드(Onward)가 원전 건설 경쟁력 회복을 위해 예산과 공기 준수 능력이 핵심이라고 지적했다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["date"], "domain": "reactor"}

</details>

## CUR-039 · `curation-6364054d79b02839`

- 원문: [반도체 기업 전기료 3~5년치 선납 제안…"한전채 25조 대체"](http://www.newstomato.com/ReadNews.aspx?inflow=N&no=1312263)
- 날짜: 2026-09-02T12:42:00+09:00
- 해시: `6364054d79b02839`

### 현재 출력

- title_kr: 반도체 기업 전기료 선납 제안 등 전력 인프라 자금조달 논의
- summary: 반도체 기업의 전기료 선납을 통해 전력망 투자 재원을 마련하자는 제안이 제기되었다.
- detail: (없음)
- implication: (없음)
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["event_boundary", "scope", "stage", "date"], "domain": "power_market"}

</details>

## CUR-040 · `curation-c2977c29c598a71d`

- 원문: [“신규 원전 반대” 전국 탈핵순례, 고리원전 출발](https://news.kbs.co.kr/news/pc/view/view.do?ncd=8651899)
- 날짜: 2026-09-01T19:45:00+09:00
- 해시: `c2977c29c598a71d`

### 현재 출력

- title_kr: 탈핵단체, 제12차 전력수급기본계획 신규 원전 포함 반대 순례 시작
- summary: 핵없는사회를 위한 전국행동이 제12차 전력수급기본계획 내 신규 원전 추가에 반대하며 고리원전에서 출발하는 도보 순례를 시작했다.
- detail: 순례단은 고리, 영광, 울진 원전에서 각각 출발하여 9월 16일 청와대에 탈핵 요구서를 전달할 예정이다. 이들은 정부의 원전 확대 기조를 재검토할 것을 촉구하고 있다.
- implication: 정부의 차기 전력수급기본계획 수립 과정에서 신규 원전 부지 선정 및 건설 계획을 둘러싼 시민사회의 반대 여론이 조직화되고 있다.
- why_important: (없음)

### 사람 판정

**event_boundary**

- [ ] PASS
- [ ] FAIL
- [ ] AMBIGUOUS

**scope**

- [ ] PASS
- [ ] FAIL

**stage**

- [ ] PASS
- [ ] FAIL

**date**

- [ ] PASS
- [ ] FAIL

**causality**

- [ ] PASS
- [ ] FAIL
- [ ] UNVERIFIABLE

**Overall**

- [ ] PASS
- [ ] REPAIR
- [ ] BLOCK

메모:


<details><summary>선정 참고정보(정답 아님)</summary>

{"risk_dimensions": ["scope", "date", "causality"], "domain": "policy"}

</details>
