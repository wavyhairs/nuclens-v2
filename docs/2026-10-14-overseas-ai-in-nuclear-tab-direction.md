# 해외원전 AI 활용 탭 — 방향성 검토 (코드 변경 없음)

> 2026-10-14. 요청: "오늘" 메뉴처럼 **해외 원전의 AI 활용 관련 뉴스만** 매일 모아 보여주는
> 신규 메뉴탭이 가능한지, 어떤 방향으로 접근해야 하는지 검토.
> 결론 먼저: **가능하다. 단 "오늘"의 복제가 아니라 다른 모양이어야 한다.** 이유는 양이다.

---

## 1. 실측 — 양은 얼마나 되나

아카이브 `archive/2026-08~10.jsonl`(약 70일, 23,700건)에서 AI 어휘 ∧ 원자력 어휘 ∧
`scope=overseas` 로 걸러 **손으로 가려낸** 결과다. (스크립트는 정규식 1차 필터, 판정은 제목 눈검사.)

| 구분 | 건수(70일) | 비고 |
|---|---|---|
| 정규식 1차 통과 (AI∧원자력∧해외) | 월 130~270건 | 대부분 아래 둘 중 하나 |
| ├ AI 데이터센터 **전력수요** 축 | 월 95~180건 | 이미 `topics=datacenter_ai` 로 잡힘. **이번 탭의 대상이 아니다** |
| ├ 오탐 (한불 정상회담 "AI·원자력 협력", 오픈AI 안전 논쟁, 반도체, 드론 공격) | 다수 | 제목에 두 단어가 같이 있을 뿐 |
| └ **진짜 "원전 현장의 AI 활용"** | **약 15~20건** | 주 1.5~2건, 하루 평균 0.3건. **0건인 날이 대부분** |

진짜 건의 예(전부 기존 수집원에서 이미 들어온 것):

- WNN 「Atomic Canyon launches virtual assistant for US nuclear fleet」
- NEI 「NIVA takes nuclear AI fleetwide」 / 「Tianwan puts AI to work」 / 「AI set to transform fuel recycling」 / 「NuScale adopts AI for reactor development」
- FT(전재) 「NuScale Power Deploys Nuclear-Specific AI from NPX and Nuclearn」
- POWER 「Condenser Chemistry Management: … Predictive Digital Twins」
- ANS 「ANS and INMM host webinar on digital twins and safeguards」 / 「How advanced NDE and repair technologies are changing nuclear asset management」
- IEEE Spectrum 「AI is moving into U.S. nuclear plants, with tools like Atomic Canyon's NIVA and Nuclearn…」
- 글로벌이코노믹 「日 도쿄전력, 'AI 원전 시스템' 구축」 (국내 매체가 해외 사례를 다룸 → scope=overseas)
- WNN/NucNet/NEI 「UK Immersive Facility … decommissioning」(로봇·VR), NEI 「New robot cuts weld inspection times」

### 1-1. 더 중요한 발견 — 수집은 되는데 **분류가 버린다**

위 진짜 건 중 상당수가 현재 큐레이션에서 `importance=noise` 다
(「Tianwan puts AI to work」·「NIVA takes nuclear AI fleetwide」·「AI set to transform fuel recycling」·
「Immersive robotics transform decommissioning」·「Predictive Digital Twins」 전부 noise).
현재 프롬프트는 **정책·투자 관점의 중요도**를 묻기 때문에 "기술 적용 사례" 기사는 낮게 본다.
`topic_gate` 는 원자력 어휘가 있으면 통과시키므로 문제는 게이트가 아니라 LLM 등급이다.

→ 따라서 이 탭의 핵심은 **새 수집이 아니라 새 분류 축**이다. `importance` 와 독립된
"AI 활용" 판정이 있어야 하고, noise 등급이어도 그 축이 맞으면 탭에 실려야 한다.

---

## 2. 방향 — 세 가지 결정

### 결정 A. 분류 축을 분리한다 (`importance` 와 별개)

후보 두 가지:

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| A-1 | 큐레이션 프롬프트 `topics` 통제 목록에 `ai_ops` 추가 | 호출 0회 추가, 탐색/흐름 필터에 바로 연결 | 프롬프트 변경 → `llm_cache` HARD_STALE. `topic_gate.py` 가 같은 이유로 프롬프트 변경을 피했다. 또 noise 판정 기사는 큐에 안 들어가므로 등급 체계와 얽힌다 |
| **A-2 (추천)** | **결정적 프리필터(AI 어휘 ∧ 원자력 어휘) → 후보만 소형 2차 LLM 판정** | 프롬프트·캐시 불변. 후보가 하루 5~15건이라 호출 1~2회/일(8건 배치). 정밀도를 독립적으로 조정·측정 가능 | 모듈 하나 추가 |

A-2 판정 스키마(안):

```
is_ai_in_nuclear: bool            # 원전(설계·건설·운영·정비·규제·해체·핵연료·안전조치)에 AI/ML/디지털트윈/로봇을 '적용'하는 기사인가
exclude_reason: datacenter_power | ai_industry_general | diplomatic_mention | weapons | none
application_area: operations | maintenance_inspection | licensing_regulation | design_engineering | fuel_cycle | decommissioning | safeguards_security | workforce_training | other
actor_type: utility | vendor | regulator | national_lab | ai_startup | other
maturity: research | pilot | deployed | announced
country: ISO-2
```

- `datacenter_ai`(전력수요)는 **명시적으로 제외**한다. 사용자가 원하는 건 "원전이 AI 를 쓰는 현황"이지 "AI 가 전력을 쓰는 현황"이 아니다. 둘을 섞으면 탭이 다시 전력수요 뉴스로 덮인다.
- `maturity` 를 두는 이유: 이 분야는 벤더 보도자료(PR Newswire 전재, evidence_role=distributed_claim)가 많다. "운영사 실적용"과 "스타트업 발표"를 한 줄에 섞지 않으려면 축이 필요하다.
- 판정 결과는 아카이브 레코드에 필드로 남긴다(`ai_application` 객체). 소급 셈이 가능해야 한다 — `topic_gate` 가 `topic_verdict` 를 남기는 것과 같은 이유.

### 결정 B. 수집원은 "보강"이지 "신설"이 아니다

이미 WNN·NEI·ANS·NucNet·POWER 가 핵심 공급원이고 진짜 건의 대부분이 거기서 왔다. 보강 후보:

| 후보 | 경로 | 기대 |
|---|---|---|
| Google News 쿼리 피드 | `(nuclear OR reactor) ("artificial intelligence" OR "machine learning" OR "digital twin" OR robot) when:1d` 영어/일본어/프랑스어 3개 | 전문지 밖 기사(IEEE Spectrum·Utility Dive·지역지) 회수. `require_keywords` 게이트 필수(종합지 피드는 괄호 키워드를 무시하는 실측이 있다) |
| 규제기관 | NRC AI 페이지·뉴스, IAEA(AI 관련 이벤트·보도), OECD NEA | 저빈도·고가치. `evidence_role=primary` |
| 국립연구소·연구기관 | INL·ORNL·ANL·EPRI 뉴스룸 | 연구 단계 사례. RSS 유무 실측 필요 |
| 벤더·운영사 | Westinghouse·EDF·Framatome·Rolls-Royce SMR·TEPCO·Constellation 보도자료, Nuclearn·Atomic Canyon 블로그 | `stakeholder`/`distributed_claim` 역할. 대표 기사 불가, 근거로만 |

- 추가 피드는 먼저 `admin_overrides` 의 `feed_add` 로 **코드 수정 없이** 2주 섀도 수집해 양을 잰 뒤 `RSS_SOURCES` 로 승격한다. 이 레포는 수집원마다 실측 주석을 남기는 관행이 있다(`news_bot.py` 303~360행).
- Naver 검색 피드(`keywords.json`)에 "AI활용" 피드를 더하는 것은 **후순위**다. 해외 사례를 국내 매체가 다루는 비율은 낮고, 이미 `한수원 AI` 키워드가 있다.

### 결정 C. 화면은 "오늘"의 복제가 아니라 **스트림 + 주간 묶음**

하루 0~3건, 0건인 날이 많은 흐름을 "오늘"처럼 일자 브리핑(이슈 N건, 날짜 이동)으로 만들면
빈 날이 더 많이 보인다. 대신:

- **기본 화면 = 최근 7일 스트림**(최신순, 날짜 구분선). "오늘 N건 · 이번 주 N건" 카운터.
- **칩 필터** = 적용 영역 / 국가 / 성숙도 / 행위자. 기존 탐색 탭의 topics 필터 문법을 재사용.
- **주 1회 묶음** = "이번 주 해외원전 AI 동향" 3~5줄. 기존 `weekly_bot` 에 섹션으로 얹거나 별도 1호출.
- **탭 노출 게이트** = `longterm` 탭과 같은 패턴(`hidden` + 데이터 게이트). 누적 N건(예: 20건) 전에는 탭을 숨긴다. 빈 탭을 사용자에게 보여주지 않는다.
- 산출물은 `build_data.py` 의 outputs 튜플에 `ai_feed.json` 하나를 더한다. today.json 과 같은 원칙 — 이미 지은 레코드에서 **잘라 담기만** 하고 새 값을 계산하지 않는다.
- 기사 상세는 기존 이슈 카드 상세를 그대로 쓴다. 이슈 원장(issue_ledger)·스토리 클러스터는 **처음엔 쓰지 않는다** — 하루 0~3건에 클러스터링은 과하다.

---

## 3. 단계안

| 단계 | 내용 | 산출 | 판단 기준 |
|---|---|---|---|
| **0. 소급 라벨링** (코드 전, 1~2일) | A-2 판정기를 아카이브 3개월에 돌린다. 30~50건 손라벨 gold 와 정밀도/재현율 비교 | 진짜 건수·오탐 유형 확정 | **주 5건 이상**이면 스트림 탭, 미만이면 주간 섹션으로 축소 |
| **1. 축 + 수집 보강** | `ai_application` 판정 모듈, 아카이브 필드 저장, 섀도 피드 `feed_add`. **화면 없음** | 운영 요약(📋)에 "AI 활용 후보 N건/판정 M건" 한 줄 | 2주 관측 후 양·정밀도 보고 |
| **2. 화면** | `ai_feed.json` 산출 + 탭(hidden 게이트) + 탐색 필터 연동 + 모바일 하단 탭 | 탭 | 게이트 통과 시 자동 노출 |
| **3. 묶음·발송** | 주간 요약, 텔레그램 발송 여부 | 선택 | 1~2단계 데이터 보고 결정 |

---

## 4. 리스크·주의

- **오탐 세 유형**: ① AI 데이터센터 전력수요, ② AI 기업 일반 뉴스(오픈AI·앤트로픽), ③ 정상회담 "AI·원자력 협력" 나열. 프리필터가 아니라 2차 판정의 `exclude_reason` 에서 자른다. 프리필터는 재현율 쪽으로 넓게 둔다.
- **"해외" 의 정의**: `scope=overseas` 를 그대로 쓴다. 국내 매체가 해외 사례를 다룬 기사(TEPCO AI)는 포함되고, 한수원 AI 는 빠진다. 한수원 사례를 "비교 참고"로 넣을지는 별도 결정.
- **벤더 발표 편중**: Nuclearn·Atomic Canyon·NPX 등 소수 벤더의 발표가 반복된다. `maturity`·`actor_type` 으로 구분하고, 같은 벤더 발표의 단순 재탕은 기존 `story_echo`/cross-run dedup 이 접는지 확인.
- **Gemini 한도**: 기본 모델 500 RPD 중 2차 판정은 하루 1~3회. 무시해도 될 수준. 프롬프트 변경(A-1)은 피한다.
- **양이 끝내 안 나오면**: 탭이 아니라 "흐름" 탭의 섹션이나 주간 판세의 한 단락으로 내려앉힌다. 0단계가 그 결정을 데이터로 내리는 자리다.

---

## 5. 열린 질문 (착수 전 결정)

1. 범위: 로봇·드론·VR 해체 훈련처럼 "AI" 가 아닌 디지털 기술도 넣을 것인가. (실측 진짜 건의 1/3 이 이 부류)
2. 핵융합(PPPL tokamak ML 제어)·방산(핵잠·핵무기 AI)은 제외하는가. (제안: 핵융합은 포함, 방산은 제외)
3. 한수원·국내 사례를 비교 참고로 함께 보여줄 것인가.
4. 1단계 2주 관측 결과를 어디서 볼 것인가 — 운영 요약 한 줄이면 충분한가, 관리자 콘솔 표가 필요한가.
