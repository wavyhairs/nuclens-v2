# 해외원전 AI 활용 탭 — 방향성 검토 v2 (코드 변경 없음)

> 2026-10-14. 1차 검토 뒤 사용자 결정 5가지를 반영해 다시 썼다.
>
> | # | 결정 |
> |---|---|
> | 1 | 현재 뉴스 수집과 **별도 기준**으로 "해외 원전이 AI 를 활용했다"는 기사를 **매일** 뽑는다. 수집 구조가 그 기사를 못 잡으면 별도 구조를 설계한다. 중복은 현재 Nuclens 알고리즘대로 피하고 **별도의 AI 분야 데이터베이스**를 만든다 |
> | 2 | 수집원이 더 필요하면 추가한다 |
> | 3 | 화면은 "오늘"처럼 가되 카드뉴스·스토리는 빼고, **누적 리스트 + 날짜 구분바 + 연속 스크롤**. 하루 신규가 5건 이상으로 늘면 하루치만 보여주는 방식으로 전환 가능 |
> | 4 | **해외만.** 국내 기사 미고려 |
> | 5 | 로봇·드론·VR 제외. **순수 AI 기술** 해외 동향만 |

---

## 0. 이번 검토에서 확인한 구조적 사실 (설계의 근거)

1. **현재 파이프라인은 "AI 활용" 기사를 수집은 하되 두 번 버린다.**
   - 1차: 큐레이션 LLM 이 정책·투자 중요도 기준으로 `noise` 를 매긴다
     (실측: NEI 「Tianwan puts AI to work」·「NIVA takes nuclear AI fleetwide」·「AI set to transform fuel recycling」, POWER 「Predictive Digital Twins」 전부 noise).
   - 2차: `noise` 판정 즉시 `state["sent"][h]` 가 찍힌다(`news_bot.py` 4095행). 이후 14일간 같은 URL 은 `article_seen` 에서 재수집이 막힌다. **즉 한 번 noise 로 찍힌 AI 기사는 다시 들어오지 않는다.**
   - 다만 noise 기사도 `archive/YYYY-MM.jsonl` 에는 적재된다(제목·한글제목·요약·topics 포함). 아카이브가 "수집된 전체 풀"이다.
2. **따라서 별도 기준은 `importance` 와 무관하게, 같은 크롤 프로세스 안에서 수집 직후의 후보 풀에 걸어야 한다.** 큐(digest_queue) 뒤에 걸면 이미 버린 뒤다.
3. **별도 수집원의 기사는 기존 큐에 넣으면 안 된다.** 넣는 순간 아침 브리핑 구성이 바뀐다. 수집원에 `lane` 표식이 필요하다(기존 `require_keywords`·`resolve_publisher` 와 같은 소스 속성).
4. **중복 제거 부품은 그대로 쓸 수 있다.** URL 정규화·`url_hash`·`title_key`(data_quality), 수집 단계 동일기사 병합(`dedup_exact_candidates`·`fold_cross_run_duplicates`), Gemini 의미 그룹핑(`dedup.py`), 아카이브 멱등(`news_archive.load_recent_hashes` 패턴). 단 **seen 상태는 레인별로 분리**해야 한다 — 메인 레인의 `sent` 마킹이 AI 레인의 수집을 막거나 그 반대가 되면 안 된다.
5. **이 세션 환경에서는 news.google.com 에 닿지 못한다**(프록시 403·DNS 실패). 전용 검색 피드의 실제 건수는 **GitHub Actions 러너에서 재야 한다.** 아래 건수 추정은 전부 아카이브 소급 + 공개 보도 기반이지 라이브 실측이 아니다.

---

## 1. (결정 1) 별도 기준 — "AI 레인" 설계

### 1-1. 한 문장 정의

> **메인 레인**은 "원자력 정책·시장에서 무엇이 중요한가"를 묻고,
> **AI 레인**은 "해외 원전 사업(설계·건설·운영·정비·규제·해체·핵연료·안전조치)에 AI 기술이 실제로 쓰였거나 쓰이기로 했는가"만 묻는다. 두 레인은 입력 풀 일부를 공유하되 판정·캐시·상태·DB·화면이 전부 따로다.

### 1-2. 흐름

```
[메인 RSS·공식·Naver 수집]  ─┐
                              ├─ 후보 풀(이번 run 신규 전건, importance 무관) ─┐
[AI 전용 피드 (lane=ai)]    ─┘                                               │
                                                                              ▼
                                               AI 프리필터 (결정적, 넓게) ─ AI 어휘 ∧ 원자력 어휘
                                                                              ▼
                                               AI 판정+작성 LLM (전용 프롬프트·전용 캐시 ai_judgments.json)
                                                 · in_scope / exclude_reason
                                                 · 해외 여부(country≠KR) · 적용영역 · 행위자 · 성숙도
                                                 · title_kr · summary (한국어 헤드라인·요약, 통과분만)
                                                                              ▼
                                               AI 레인 중복 제거 (url_hash → title_key → 의미 그룹핑)
                                                                              ▼
                                               ai_archive/YYYY-MM.jsonl (append-only, 별도 DB) + ai_state.json
                                                                              ▼
                                               build_data → ai_feed.json → 웹 "AI" 탭
```

- 메인 레인은 **아무것도 바뀌지 않는다.** 프롬프트·캐시·큐·브리핑 그대로. AI 전용 피드 기사는 메인 큐에 들어가지 않는다.
- AI 판정 LLM 은 **판정과 작성을 한 호출**에 한다. 전용 피드 기사는 메인 큐레이션을 안 거치므로 한글 헤드라인·요약을 여기서 만들어야 화면에 올릴 수 있다. 공유 풀에서 온 기사는 메인 큐레이션의 `title_kr`·`summary` 가 있으면 재사용한다(캐시 조인, 호출 절약).
- 판정은 **제목+요약+본문 앞부분**을 본다. AI 언급이 본문에만 있는 기사는 제목 프리필터가 놓친다 — 이건 전용 검색 피드(Google News 는 본문을 색인한다)가 메운다. 두 경로가 서로의 구멍을 막는 구조다.

### 1-3. 판정 기준 (결정 4·5 반영)

**포함 (in_scope):** 해외 원전 사업자·벤더·규제기관·국립연구소·국제기구가 다음 AI 기술을 원전 업무에 적용·시범·계약·승인·발표한 기사.

| 기술 | 예 |
|---|---|
| 기계학습·예측분석 | 예측정비, 이상징후 탐지, 센서 가상계측, 연료 성능 예측 |
| 생성형 AI·LLM | 절차서·인허가 문서 검색/작성 보조(Atomic Canyon NIVA, Nuclearn), 운전원 지식 보조 |
| 컴퓨터 비전 | 검사 영상·용접부·연료봉 결함 자동 판독 |
| AI 기반 디지털 트윈 | 모델이 ML 로 학습·갱신되는 경우만. 단순 3D 모델·시뮬레이터는 제외 |
| AI 와 규제 | NRC·ONR 등의 AI 활용 지침·심사 AI 도입·AI 이용 인허가 신청 |
| AI 거버넌스·인력 | 사업자의 AI 도입 전략, INPO·WANO 가이드, AI 인력 양성 |

**제외 (exclude_reason):**

| 코드 | 내용 | 아카이브 실측 비중 |
|---|---|---|
| `datacenter_power` | AI 데이터센터 **전력수요**·PPA·전력난 (AI 가 전력을 쓰는 이야기) | 정규식 1차 통과분의 과반 |
| `robotics_only` | 로봇·드론·VR/AR·원격조작이 주제이고 AI 가 핵심이 아님 (결정 5) | 진짜 후보의 약 1/3 |
| `ai_industry_general` | 오픈AI·앤트로픽·엔비디아 등 AI 산업 일반 뉴스, "원전처럼 안전하게" 류 비유 | 다수 |
| `diplomatic_mention` | 정상회담·MOU 에서 "AI·원자력 협력" 나열 | 다수 |
| `domestic` | 한국이 주체·무대(한수원 AI 등). **해외 사례를 다룬 국내 매체 기사는 포함** (결정 4) | — |
| `defense_weapons` | 핵무기·핵잠수함·군사 AI | 소수 |
| `fusion` | 핵융합(토카막 ML 제어). 원전이 아니므로 기본 제외 — **열린 질문 ①** | 소수 |
| `academic_only` | 학술 논문·arXiv 만 있는 것. 기사가 아님 — **열린 질문 ②** | — |

경계 규칙: **AI 가 제목이나 첫 단락의 주어·목적어여야 한다.** 로봇 기사에 "AI 탑재"가 한 단어 붙은 것은 `robotics_only`, "AI 비전으로 용접부 판독하는 로봇"처럼 AI 판독이 핵심이면 포함. 이 경계는 판정 프롬프트에 예시로 박고 gold 세트로 잠근다.

### 1-4. 출력 스키마 (AI DB 레코드, 안)

```
hash, url, resolved_url, domain, publisher, source_type, evidence_role, pub, archived_at
lane: "ai"
title, title_kr, summary (한국어)
in_scope: true                       # false 는 DB 에 넣지 않고 ai_judgments 캐시에만 남긴다(소급 셈용)
exclude_reason: none
country: ISO-2 (복수 가능)          # KR 단독이면 domestic 제외
application_area: operations | maintenance_inspection | licensing_regulation | design_engineering
                | fuel_cycle | decommissioning | safeguards_security | workforce_governance
actor_type: utility | vendor | regulator | national_lab | ai_startup | international_org | other
actor_names: [...]                   # Constellation, Atomic Canyon …
maturity: research | pilot | deployed | announced | contract | regulatory
ai_tech: ml_predictive | genai_llm | computer_vision | digital_twin_ai | other
story_key                            # 같은 사건 묶음(중복 제거 결과). 대표 1건 + 동일 사건 다른 매체 n건
judge_model, judge_prompt_version, judged_at
```

- `maturity` 와 `actor_type` 이 이 분야의 핵심 축이다. 벤더 보도자료(PR Newswire 전재, `distributed_claim`)가 많아서 "실적용(deployed)"과 "발표(announced)"를 구분하지 않으면 화면이 홍보로 덮인다.
- `in_scope=false` 도 캐시에는 남긴다 — "기준이 과했나"를 나중에 셀 수 있어야 한다(`topic_gate.topic_verdict` 와 같은 이유).

### 1-5. 후보 건수 — 사용자 지적("하루 5~15건이 아닐 것")에 대한 답

맞다. 1차 검토의 5~15건은 **현재 수집 풀만** 정규식으로 센 숫자다. 전용 피드를 더하면 풀 자체가 달라진다. 라이브 실측이 불가능해 세 층으로 나눠 추정만 적는다. **전부 1단계 섀도 수집으로 대체해야 할 숫자다.**

| 층 | 추정 (하루) | 근거 |
|---|---|---|
| 전용 피드 원시 유입 | 20~60건 | Google News 검색 피드가 "관련도순"으로 돌려주고 종합지가 괄호 키워드를 무시하는 실측(`news_bot.py` 303~330행). 대부분 `datacenter_power`·`ai_industry_general` |
| 프리필터 통과 | 10~30건 | 공유 풀 4~9건 + 전용 피드 |
| **판정 통과(in_scope, 해외, 순수 AI)** | **1~4건** | 아카이브 70일 소급 15~20건(로봇 제외 전) ÷ 70 ≈ 0.25건/일이 현재 수집 기준. 전용 피드로 영어 트레이드·벤더·연구소·일본·프랑스를 더하면 수 배. 주제 자체가 가속 중 — IEEE Spectrum 2026-10-08: 미국 94기 거의 전부에 AI 도구 도입이 제안됐고 다수가 수용, INPO 는 연내 회원사 과반 도입 전망 |
| 중복 제거 후 사건 수 | 1~3건 | 같은 발표를 WNN·NEI·ANS·NucNet·벤더 PR 이 함께 쓴다 |

→ 결정 3 의 분기점(하루 5건)은 **중복 제거 후 사건 수**로 재야 한다. 원시 유입으로 재면 늘 5건을 넘고, 화면은 같은 발표 다섯 벌이 된다.

Gemini 예산: 판정 10~30건/일 ÷ 8건 배치 = 2~4 호출/일. 기본 모델 500 RPD 에서 무시할 수준. 메인 프롬프트는 건드리지 않으므로 캐시 무효화도 없다.

---

## 2. (결정 2) 수집원

기존 수집원(WNN·NEI·ANS·NucNet·POWER·DOE NE·IAEA·SFEN·Le Monde 에너지)이 공유 풀로 들어오므로 **전부 AI 레인의 입력이다.** 아래는 **추가** 후보. 전용 RSS 유무·Google News 색인 여부는 러너에서 실측해야 한다(이 레포는 수집원마다 실측 주석을 남긴다).

| 군 | 후보 | 경로 | evidence_role | 기대 |
|---|---|---|---|---|
| **검색 피드 (핵심)** | Google News EN 일반: `(nuclear OR reactor) ("artificial intelligence" OR "machine learning" OR "generative AI") -"data center" when:1d` | RSS, `lane=ai`, AI∧원자력 게이트 | 매체별 | 트레이드 밖 기사(IEEE Spectrum·Utility Dive·Washington Examiner Daily on Energy·E&E) 회수 |
| | Google News EN 행위자: `(Constellation OR Duke OR TVA OR Dominion OR "Ontario Power" OR Bruce OR EDF OR Westinghouse OR Framatome OR "GE Vernova" OR "Rolls-Royce SMR" OR TEPCO OR Kansai OR Rosatom OR CGN) nuclear ("AI" OR "machine learning") when:1d` | RSS | 매체별 | 사업자 실적용 기사 |
| | Google News EN AI 벤더: `("Atomic Canyon" OR Nuclearn OR "Blue Wave AI" OR NPX) when:2d` | RSS | 매체별 | 소수 벤더 집중 분야. 재탕 많음 → 중복 제거가 흡수 |
| | Google News JA: `(原子力 OR 原発) (AI OR 人工知能 OR 機械学習) when:1d` | RSS | 매체별 | 도쿄전력·간사이전력·규제위 AI 사례(실측 1건: 글로벌이코노믹 TEPCO 기사의 원출처) |
| | Google News FR: `nucléaire ("intelligence artificielle" OR IA) when:1d` | RSS, `_FR` 게이트 | 매체별 | EDF·Framatome·ASN |
| **규제·국제기구** | NRC 뉴스릴리스·AI 전략 페이지, ONR(영), ASN(프), IAEA 뉴스(AI 태그), OECD NEA | 직접 RSS 또는 `site:` 피드 | primary | 저빈도·고가치 |
| **국립연구소·연구기관** | INL·ORNL·ANL·PNNL 뉴스룸, EPRI | RSS 실측 필요 | primary / stakeholder | 연구·시범 단계. 웹 검색에서 ANL·DOE NE 기사 확인 |
| **사업자·벤더 뉴스룸** | Constellation·Duke·TVA·OPG·EDF·Westinghouse·Framatome·GE Vernova·Rolls-Royce SMR | RSS 실측 필요 | stakeholder / distributed_claim | 1차 발표 원문. 대표 기사 불가, 근거로 |
| **AI 벤더** | Atomic Canyon·Nuclearn·Blue Wave AI Labs 블로그 | RSS 실측 필요 | peripheral | 발표 원문. 성숙도 `announced` 기본 |
| **일본·유럽 전문지** | 原子力産業新聞(JAIF)·電気新聞, NIA(영), RGN(프) | 실측 필요 | 매체별 | 비영어권 사례 |

운영 원칙:
- 전용 피드는 `admin_overrides` 의 `feed_add` 로 **코드 수정 없이** 먼저 넣어 2주 섀도 수집한다. 단 `lane=ai` 속성이 `feed_add` 에 없으므로 **그 속성 하나는 코드 추가가 선행**된다(그 전까지 넣으면 메인 큐로 들어간다).
- 종합지 `site:`/키워드 피드는 반드시 게이트를 단다. AI 레인 게이트는 메인의 `NUCLEAR_TITLE_KEYWORDS` 가 아니라 **AI 어휘 ∧ 원자력 어휘** 둘 다다. `tests/test_collect.py` 가 메인 게이트를 잠그듯 AI 게이트도 테스트로 잠근다.
- Naver 검색 피드(`keywords.json`)는 **쓰지 않는다**(결정 4). 국내 매체가 해외 사례를 쓴 기사는 기존 메인 수집(정책·SMR 피드)으로 들어오는 것만 공유 풀에서 받는다.
- 학술(arXiv·학회지)은 기본 제외. 넣더라도 `actor_type=national_lab`·`maturity=research` 로 따로 묶는다 — **열린 질문 ②**.

---

## 3. (결정 3) 화면

### 3-1. 구성

"오늘" 뷰에서 **쓰는 것**: 머리(제목·건수 한 줄), 목차 행 컴포넌트(`.toc` — 번호·지역·분류·제목·한 줄), 기사 상세(원문 링크·요약·근거), 탐색 탭 연동.
**빼는 것**: 카드뉴스 띠, 오디오 브리프, 먼저 볼 3건, 한 주의 원자력, 날짜 선택 셀렉트(스트림이 대신한다), 이슈 원장·스토리 클러스터·장기 스토리.

```
┌ 해외 원전 AI 동향                         오늘 2건 · 이번 주 9건 · 누적 143건 ┐
│ [전체] [운영·정비] [인허가·규제] [설계·엔지니어링] [핵연료] [해체] …        │  ← 적용영역 칩
│ [전체] [실적용] [시범] [계약] [발표] [연구]                                  │  ← 성숙도 칩
├──────────────── 10월 14일 (화) ───────────────────────────────── 2건 ──────┤  ← 날짜 구분바
│ 🇺🇸 운영·정비 · 실적용   Constellation, 21기에 Atomic Canyon NIVA 전면 적용   │
│    사업자 · WNN · 같은 사건 3매체                                            │
│ 🇯🇵 인허가 · 발표        도쿄전력, 서류 업무 80% 감축 'AI 원전 시스템' 재가동  │
├──────────────── 10월 13일 (월) ───────────────────────────────── 1건 ──────┤
│ …                                                                            │
│ (아래로 계속 — 날짜 구분바가 이어진다. 끝에서 다음 월 shard 로드)             │
```

- **날짜 구분바** = 기사 발행일(`pub`, KST) 기준. 그날 건수를 바 오른쪽에 쓴다. 0건인 날은 바를 세우지 않는다(빈 날이 많다는 것을 굳이 보여줄 이유가 없다).
- 행 하나 = 국가 · 적용영역 · 성숙도 · 한글 헤드라인 · (행위자 유형 · 매체 · 같은 사건 n매체). 요약은 행에 안 쓴다 — "오늘" 목차와 같은 원칙. 누르면 상세.
- **연속 스크롤**: `ai_feed.json` 은 최근 90일을 한 파일로, 그 이전은 월별 shard(`news-manifest.json` 과 같은 방식)로 끝에서 더 불러온다. 초기엔 전량이 한 파일에 들어간다(하루 1~4건 × 90일 ≈ 100~360행).
- **전환 스위치(결정 3 후반)**: 표시 모드 `stream` | `daily` 를 데이터 게이트로 둔다. 빌드가 "최근 14일 중복 제거 후 일평균"을 `meta` 에 싣고, 5건 이상이면 화면이 `daily`(오늘 하루치 + 날짜 이동)로 선다. `longterm` 탭의 `hidden` + 데이터 게이트 패턴과 같다. 코드 상수가 아니라 데이터가 결정하므로 양이 줄면 스트림으로 되돌아온다.
- **탭 노출 게이트**: 누적 20건 전에는 탭을 숨긴다. 상단 `main-tabs` 와 하단 `mobileTabs` **둘 다** 추가해야 한다 — 폰에선 하단 탭이 유일한 통로다(index.html 주석).
- 탭 이름·위치(안): `오늘 · 해외 AI · 흐름 · 탐색 · 보고서`. 
- 탐색 탭 연동: `application_area`·`maturity` 를 탐색의 topics 필터 문법에 얹으면 과거 검색이 공짜로 된다. 필수는 아니다.
- 텔레그램·푸시 발송은 **하지 않는다**(요청에 없음). 필요해지면 별도 결정.

### 3-2. 산출물

- `web/public/data/ai_feed.json` — `build_data.py` outputs 튜플에 한 줄. today.json 과 같은 원칙으로 **ai_archive 레코드에서 잘라 담기만** 한다. 값을 새로 계산하지 않는다.
- `meta` 에 `ai: {total, last_14d_daily_avg, mode}`.

---

## 4. 중복 제거 — "현재 알고리즘대로"의 구체화

| 단계 | 재사용 부품 | AI 레인에서의 쓰임 |
|---|---|---|
| URL 정규화·해시 | `data_quality.normalize_url`, `url_hash`, `legacy_url_hash` | 레인 내 재수집 차단. **`ai_state.json` 별도** (메인 `sent` 와 섞지 않는다) |
| 수집 단계 동일 기사 | `dedup_exact_candidates`, `canonical_replacement_allowed`(공식 원문 우선) | 전용 피드 + 공유 풀에서 같은 URL/제목이 겹칠 때 공식·전문지 원문을 대표로 |
| 크로스런 접기 | `fold_cross_run_duplicates` | 3시간 run 사이 재등장 |
| 의미 그룹핑 | `dedup.py` (Gemini 1호출, "같은 사건" 엄격 규칙) | 하루 1회, 당일 통과분 대상. 대표 1건 + `story_key` 로 묶음. 벤더 PR 과 트레이드 기사가 한 사건이면 **트레이드·공식 쪽이 대표**(evidence_role 우선순위 그대로) |
| 아카이브 멱등 | `news_archive.load_recent_hashes` 패턴 | `ai_archive/` 최근 2개월 hash |

메인 아카이브와의 관계: 공유 풀에서 온 기사는 메인 `archive/` 에도 (noise 로) 남고 `ai_archive/` 에도 남는다. **이중 적재를 허용한다** — 두 DB 의 질문이 다르고, hash 로 조인하면 된다. 메인 레코드를 고치지 않는다.

---

## 5. 단계안 (수정)

| 단계 | 내용 | 코드 | 산출·판단 |
|---|---|---|---|
| **0. 기준 고정 + 소급 gold** | §1-3 기준으로 아카이브 3개월에서 후보 ~300건을 뽑아 **손라벨 50~80건** (in/out + exclude_reason + 경계 사례). 판정 프롬프트 초안을 그 gold 로 평가 | 판정 스크립트만(러너 아님, 로컬) | 정밀도·재현율 숫자. 경계(로봇·디지털트윈·핵융합) 결정 확정 |
| **1. 레인 + 섀도 수집** | `lane=ai` 소스 속성, AI 프리필터·판정 모듈, `ai_archive/`·`ai_state.json`·`ai_judgments.json`, 전용 피드 등록. **화면 없음.** 운영 요약(📋)에 "AI 레인: 원시 n · 프리필터 n · 통과 n · 사건 n" 한 줄 | 있음 | **2주 관측.** 하루 사건 수가 결정 3 의 분기 데이터 |
| **2. 화면** | `ai_feed.json`, 탭(상단+하단, hidden 게이트), 날짜 구분바 스트림, 칩 필터, 상세, mode 스위치 | 있음 | 누적 20건 시 자동 노출 |
| **3. 후속** | 탐색 연동, 월별 shard, (요청 시) 주간 요약·발송 | 선택 | 1~2단계 데이터로 결정 |

0단계는 코드 없이 지금 할 수 있고, 그 결과가 1단계의 프롬프트와 테스트 fixture 가 된다.

---

## 6. 리스크

- **재현율 구멍 두 개**: ① 제목에 AI 가 없고 본문에만 있는 기사 — 전용 검색 피드로 메움. ② 메인 레인이 이미 `sent` 로 찍어 재수집이 막힌 과거 기사 — 1단계 시작 시 **아카이브 3개월 소급 1회**로 메움(`news_archive --backfill` 과 같은 성격).
- **벤더 편중**: Atomic Canyon·Nuclearn 두 회사 발표가 반복된다. `maturity`·`actor_type` 축 + 의미 그룹핑으로 "같은 제품의 n번째 고객 발표"가 n행이 되지 않게 한다. 그래도 많으면 성숙도 기본 필터를 `실적용·계약·규제` 로 둔다.
- **전용 피드 잡음**: Google News 검색 피드는 관련도순이라 몇 주 지난 기사가 섞인다(`when:1d` 필수, 메인 주석 441행 실측). `-"data center"` 같은 부정 연산자는 매체에 따라 무시된다 → 게이트와 판정이 막는다.
- **번역 품질**: 전용 피드 기사는 메인 큐레이션을 안 거치므로 `title_kr` 을 AI 판정 호출이 만든다. 메인의 헤드라인 규칙(주체+행동, 수치 보존, 단계 보존)을 그 프롬프트에 그대로 옮긴다.
- **한도**: 2~4 호출/일 + 의미 그룹핑 1회. 문제 없음. 단 메인이 `QUOTA_EXHAUSTED` 면 AI 레인도 멈추고 다음 run 에 재시도(seen 마킹을 판정 **뒤**에 — 메인의 1894행 원칙과 동일).

---

## 7. 열린 질문 (착수 전 결정)

1. **핵융합**(PPPL 토카막 ML 제어 등)을 넣는가. 제안: **제외**. "해외 원전"이 아니다.
2. **학술·연구소 논문**(arXiv, 학회 발표)을 넣는가. 제안: **기사만**. 연구소 뉴스룸 기사는 포함, 논문 자체는 제외.
3. **경계 규칙 확인**: "AI 비전으로 판독하는 검사 로봇"처럼 AI 가 핵심인 로봇 기사는 포함, 로봇이 핵심이고 AI 가 수식어인 기사는 제외 — 이 선이 맞는가.
4. **탭 이름**: `해외 AI` / `원전 AI` / `AI 동향` 중 무엇으로.
5. **국내 매체가 쓴 해외 사례 기사**(글로벌이코노믹 TEPCO)는 포함으로 봤다. 맞는가.
