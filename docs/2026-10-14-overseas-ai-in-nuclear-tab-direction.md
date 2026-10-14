# 해외 AI 탭 — 방향성 검토 v3 (코드 변경 없음)

> 2026-10-14. 세 차례 검토의 결정을 모두 반영한 판이다. 이전 판(v1·v2)의 "이슈 원장·스토리
> 클러스터는 쓰지 않는다"는 **철회**한다 — 상세 화면을 "오늘"과 같게 하기로 했으므로 이슈
> 계약을 그대로 타는 쪽이 유일하게 싼 길이다(§3).

## 확정된 결정

| # | 결정 |
|---|---|
| D1 | 현재 뉴스 수집과 **별도 기준**으로 "해외 원전이 AI 를 활용했다"는 기사를 매일 뽑는다. 수집 구조가 못 잡으면 별도 구조. 중복은 현재 Nuclens 알고리즘대로. **별도의 AI 데이터베이스** |
| D2 | 수집원이 더 필요하면 추가 |
| D3 | 화면은 "오늘"처럼. 카드뉴스·스토리 제외. **누적 리스트 + 날짜 구분바 + 연속 스크롤**. 하루 신규 5건 이상이 되면 하루치 모드 전환 가능 |
| D4 | **해외만.** 국내 기사 미고려 (해외 사례를 다룬 국내 매체 기사는 포함) |
| D5 | **순수 AI 기술**만. 로봇·드론·VR 자체는 제외하되, "AI 비전으로 판독하는 검사 로봇"처럼 **AI 기술이 쓰인 경우는 포함** |
| D6 | 핵융합 제외 |
| D7 | 연구소 뉴스룸 기사 포함, 논문 자체는 제외 |
| D8 | 탭 이름 **「해외 AI」** |
| D9 | **탐색 탭에서 "AI" 범위로 검색**되도록 별도 DB 로 구축 |
| D10 | 목록 행은 "오늘 → 그 밖의 이슈"와 같은 형태. 클릭하면 **한 줄 결론 · 기사 내용 · 시사점 · … · 타임라인 · 이 사건의 근거 · 추가 근거 · 관련 이슈**를 보여준다 (= 기존 이슈 상세 전체) |

---

## 0. 설계를 결정한 구조적 사실

1. **현재 파이프라인은 AI 활용 기사를 수집은 하되 두 번 버린다.** 큐레이션 LLM 이 정책·투자 기준으로 `noise` 를 매기고(실측: NEI 「Tianwan puts AI to work」·「NIVA takes nuclear AI fleetwide」, POWER 「Predictive Digital Twins」 전부 noise), 그 즉시 `state["sent"][h]` 가 찍혀(`news_bot.py` 4095행) 14일간 재수집이 막힌다. 다만 noise 도 `archive/` 에는 남는다.
2. 그러므로 별도 기준은 **같은 크롤 프로세스 안, 수집 직후의 후보 풀**에 걸어야 한다. 큐 뒤는 이미 버린 뒤다.
3. **이슈 상세는 "기사"가 아니라 "이슈 레코드"를 읽는다.** `openIssueDialog` 가 읽는 것: `title`·`summary`(한 줄 결론)·`detail`(기사 내용)·`why_important`·`implication`(시사점)·`open_question`·`related_articles`(member_role=card → 이 사건의 근거, evidence → 추가 근거, 날짜 정렬 → 타임라인)·`verification`·`entity_ids`·같은 주제의 다른 이슈(관련 이슈). 이 레코드는 `web/build_data.py` 의 `cluster_selected_articles` → `attach_evidence_articles` → `build_issue_catalog` 가 만든다.
4. **중복 제거·이슈 묶기 부품은 전부 재사용 가능**하고 입력만 바꾸면 된다. 단 상태 파일(seen·원장·캐시)은 레인별로 분리해야 한다.
5. 이 세션 환경은 news.google.com 에 닿지 못한다(프록시 403·DNS 실패). 전용 피드 건수는 **Actions 러너에서** 잰다. 아래 건수는 전부 추정이다.

---

## 1. 전체 그림

```
                 메인 레인 (변경 없음)                      AI 레인 (신설)
─────────────────────────────────────────────   ──────────────────────────────────────────────
RSS·공식·Naver 수집 ──┐                           lane=ai 전용 피드 ──┐
                      ├─ 이번 run 신규 후보 풀 ─────────────────────┤  (importance 무관, 전건)
                      ▼                                              ▼
            큐레이션 LLM (정책 중요도)                    AI 프리필터 (결정적: AI 어휘 ∧ 원자력 어휘, 넓게)
                      ▼                                              ▼
            digest_queue → 아침 브리핑                  AI 판정+작성 LLM (전용 프롬프트 · ai_judgments.json)
                      ▼                                   in_scope / exclude_reason / country≠KR
            archive/ (noise 포함)                         title_kr · summary · detail · implication ·
                      ▼                                   why_important · open_question · 분류 축
      build_data: 이슈 묶기 → issues.json                            ▼
                                                        레인 내 중복 제거 (url_hash → title_key → 의미 그룹핑)
                                                                     ▼
                                                        ai_archive/YYYY-MM.jsonl  +  ai_state.json
                                                                     ▼
                                      build_data: 같은 함수로 이슈 묶기 → ai_issues.json · ai_issue_ledger.json
                                                                     ▼
                                      웹: 「해외 AI」탭 (목차 행 스트림) · 탐색 탭 "AI" 범위 · 기존 이슈 상세
```

세 층의 **별도 DB** (D1·D9):

| 층 | 메인 | AI 레인 | 성격 |
|---|---|---|---|
| 기사 | `archive/YYYY-MM.jsonl` | `ai_archive/YYYY-MM.jsonl` | append-only, 멱등, git 이 DB |
| 이슈 원장 | `issue_ledger.json` | `ai_issue_ledger.json` | 이슈 신원·이동·개정 기억 |
| 웹 산출 | `issues.json`·`news-manifest.json`+shards | `ai_issues.json`·`ai_news-manifest.json`+shards | 탭·탐색·상세가 읽는 것 |

공유 풀에서 온 기사는 **양쪽 아카이브에 다 남는다**(메인엔 noise 로, AI 엔 in_scope 로). 이중 적재를 허용한다 — 두 DB 의 질문이 다르고 hash 로 조인된다. 메인 레코드는 고치지 않는다.

---

## 2. AI 레인 — 수집·판정·중복 제거 (D1·D2·D4~D7)

### 2-1. 입력 두 갈래

- **공유 풀**: 이번 run 에 메인이 수집한 신규 기사 전건(큐레이션 결과와 무관). 메인 큐레이션의 `title_kr`·`summary` 가 있으면 재사용(호출 절약).
- **전용 피드**: 소스 레코드에 `lane: "ai"` 속성. 이 기사는 **메인 큐에 들어가지 않는다**(아침 브리핑 불변). 게이트는 메인의 `NUCLEAR_TITLE_KEYWORDS` 가 아니라 **AI 어휘 ∧ 원자력 어휘**. `tests/test_collect.py` 가 메인 게이트를 잠그듯 테스트로 잠근다.

### 2-2. 전용 피드 후보 (실측 전)

| 군 | 후보 | 비고 |
|---|---|---|
| 검색 피드 (핵심) | Google News EN 일반 `(nuclear OR reactor) ("artificial intelligence" OR "machine learning" OR "generative AI") when:1d` / EN 사업자·벤더명 / EN AI 벤더(`"Atomic Canyon" OR Nuclearn OR "Blue Wave AI" OR NPX`) / JA `(原子力 OR 原発) (AI OR 人工知能 OR 機械学習)` / FR `nucléaire ("intelligence artificielle" OR IA)` | `when:1d` 필수(관련도순). `resolve_publisher` 로 매체 복원 |
| 규제·국제기구 | NRC 뉴스릴리스·AI 페이지, ONR, ASN, IAEA 뉴스, OECD NEA | primary |
| 연구소 (D7) | INL·ORNL·ANL·PNNL 뉴스룸, EPRI | 뉴스룸 기사만. arXiv·학회지 피드는 등록하지 않는다 |
| 사업자·벤더 뉴스룸 | Constellation·Duke·TVA·OPG·EDF·Westinghouse·Framatome·GE Vernova·Rolls-Royce SMR | stakeholder/distributed_claim. 대표 기사 불가 |
| AI 벤더 | Atomic Canyon·Nuclearn·Blue Wave AI Labs | peripheral. 성숙도 기본 `announced` |
| 비영어 전문지 | 原子力産業新聞(JAIF)·電気新聞, NIA, RGN | 실측 필요 |

Naver 피드는 쓰지 않는다(D4). 전용 피드는 `admin_overrides` `feed_add` 로 섀도 등록하되, **`lane` 속성이 `feed_add` 에 먼저 추가돼야 한다** — 없으면 메인 큐로 들어간다.

### 2-3. 판정 기준

**포함**: 해외 원전 사업(설계·건설·운영·정비·검사·인허가·규제·해체·핵연료·안전조치·인력)에 다음 AI 기술을 적용·시범·계약·승인·발표.

| 기술 | 예 |
|---|---|
| 기계학습·예측분석 | 예측정비, 이상징후 탐지, 가상계측, 연료 성능 예측 |
| 생성형 AI·LLM | 절차서·인허가 문서 검색/작성 보조(NIVA·Nuclearn), 운전원 지식 보조 |
| 컴퓨터 비전 | 검사 영상·용접부·연료봉 결함 자동 판독 — **AI 판독 로봇 포함(D5)** |
| AI 디지털 트윈 | 모델이 ML 로 학습·갱신되는 것만. 단순 3D·시뮬레이터 제외 |
| AI 와 규제 | NRC·ONR 의 AI 지침·심사 AI·AI 이용 인허가 신청 |
| AI 거버넌스·인력 | 사업자 AI 전략, INPO·WANO 가이드, AI 인력 양성 |

**제외 코드** (`exclude_reason`, 캐시에 남겨 소급 셈):

| 코드 | 내용 |
|---|---|
| `datacenter_power` | AI 데이터센터 전력수요·PPA·전력난 — 정규식 1차 통과분의 과반 |
| `robotics_only` | 로봇·드론·VR/AR 이 주제이고 AI 가 수식어(D5). "AI 탑재" 한 단어는 여기 |
| `ai_industry_general` | 오픈AI·엔비디아 등 AI 산업 일반, "원전처럼 안전하게" 류 비유 |
| `diplomatic_mention` | 정상회담·MOU 의 "AI·원자력 협력" 나열 |
| `domestic` | 한국이 주체·무대(D4) |
| `fusion` | 핵융합(D6) |
| `academic_only` | 논문·학회 발표 자체(D7) |
| `defense_weapons` | 핵무기·핵잠·군사 AI |

경계 규칙: **AI 기술이 그 기사의 주어·목적어이거나, 보도된 성과를 만든 핵심 수단이어야 한다.** 이 선은 프롬프트 예시 + gold 세트로 잠근다.

### 2-4. 판정+작성 한 호출 — 메인 큐레이션과 같은 필드를 만든다 (D10 의 전제)

상세 화면이 이슈 레코드의 `summary`·`detail`·`implication`·`why_important`·`open_question` 을 읽으므로 AI 판정 호출은 **분류와 작성을 함께** 한다. 메인 프롬프트의 작성 규칙(헤드라인 = 주체+행동, 수치·단계 보존, 본문 없으면 `detail` 빈 값, 해석 과잉 금지)을 그대로 옮기고, 메인의 결정적 정리기(`drop_hollow_implication`·`strip_unsourced_person_names`·`drop_interpretation_without_body` 등)를 그대로 거친다. 본문은 `article_body` 경로를 공유한다.

```
# 메인 레코드와 같은 필드 (이슈 묶기·상세가 읽는다)
hash, url, resolved_url, domain, publisher, source_type, evidence_role, source_tier, pub, archived_at
title, title_kr, summary, detail, implication, why_important, open_question(+source)
scope: "overseas"(고정), countries, topics(메인 통제 목록 그대로 — 탐색 필터 호환), tags, article_type,
event_date(+type/precision/source), verified_evidence
# AI 레인 고유
lane: "ai", in_scope, exclude_reason
ai_tech: ml_predictive | genai_llm | computer_vision | digital_twin_ai | other
application_area: operations | maintenance_inspection | licensing_regulation | design_engineering
               | fuel_cycle | decommissioning | safeguards_security | workforce_governance
actor_type: utility | vendor | regulator | national_lab | ai_startup | international_org | other
actor_names, maturity: research | pilot | deployed | announced | contract | regulatory
judge_model, judge_prompt_version, judged_at
```

`maturity`·`actor_type` 이 이 분야의 핵심 축이다. 벤더 보도자료가 많아 "실적용"과 "발표"를 구분하지 않으면 화면이 홍보로 덮인다.

### 2-5. 중복 제거 — "현재 알고리즘대로"

| 단계 | 재사용 부품 | AI 레인 |
|---|---|---|
| URL 정규화·해시 | `normalize_url`·`url_hash`·`legacy_url_hash` | **`ai_state.json` 별도** |
| 수집 단계 동일 기사 | `dedup_exact_candidates`·`canonical_replacement_allowed` | 공식·전문지 원문 우선 |
| 크로스런 접기 | `fold_cross_run_duplicates` | 3시간 run 사이 |
| 의미 그룹핑 | `dedup.py`(Gemini 1호출, "같은 사건" 엄격) | 하루 1회, 당일 통과분 |
| 아카이브 멱등 | `news_archive.load_recent_hashes` 패턴 | `ai_archive/` 최근 2개월 |
| 이슈 묶기(날짜 넘어) | `cluster_selected_articles` + `attach_evidence_articles` (story_fingerprint·event_identity·임베딩·관리자 override) | §3-1 |

### 2-6. 건수 — 측정 설계

라이브 실측 불가라 추정만 둔다. **1단계 섀도 2주로 대체할 숫자다.**

| 층 | 추정(하루) |
|---|---|
| 전용 피드 원시 유입 | 20~60 (대부분 `datacenter_power`·`ai_industry_general`) |
| 프리필터 통과 | 10~30 |
| 판정 통과(해외·순수 AI) | 1~4 |
| **중복 제거 후 사건 수** | **1~3** |

근거: 아카이브 70일 소급 15~20건 ≈ 0.25건/일이 현재 수집 기준이고, 전용 피드로 영어 트레이드·벤더·연구소·일본·프랑스를 더하면 수 배. 주제는 가속 중(IEEE Spectrum 2026-10-08: 미국 94기 거의 전부에 AI 도구 도입 제안·다수 수용, INPO 연내 회원사 과반 전망). **D3 의 분기점(하루 5건)은 중복 제거 후 사건 수로 잰다.** 원시 유입으로 재면 늘 넘고 화면은 같은 발표 다섯 벌이 된다.

Gemini: 판정 2~4 호출/일 + 그룹핑 1회 + 이슈 해석(issue_insight) 소량. 500 RPD 에서 무시할 수준. 메인 프롬프트 불변이라 캐시 무효화 없음. 메인이 `QUOTA_EXHAUSTED` 면 AI 레인도 멈추고 다음 run 재시도(seen 마킹은 판정 **뒤** — 메인 1894행 원칙).

---

## 3. 이슈 묶기와 상세 — 기존 계약을 그대로 탄다 (D10)

### 3-1. 입력만 바꾼 같은 함수

메인은 `delivery_log`(브리핑 선정분)를 "카드 멤버"로, 미선정 아카이브를 "근거 멤버"로 넣는다. AI 레인에는 브리핑이 없으므로 역할을 이렇게 정한다.

| 메인 | AI 레인 |
|---|---|
| 카드 멤버 = 브리핑 선정 기사 | **대표 기사** = 의미 그룹핑에서 사건마다 고른 1건(공식·전문지 우선) |
| 근거 멤버 = 뒤에 매칭된 미선정 보도 | **같은 사건의 다른 매체 기사** + 날짜 넘어 매칭된 후속 보도 |
| `briefing_date` | `judged_at` 의 날짜(KST). 함수가 요구하는 칸을 이 값으로 채운다 |
| `selection_reasons`·`importance` | 빈 값 / `nice_to_know` 고정. 상세는 이 칸을 쓰지 않는다 |

그 다음은 **메인과 동일**: `build_issue_catalog` → `ai_issues.json`, `issue_ledger.merge/resolve_moves` → `ai_issue_ledger.json`, `issue_insight`(타임라인 2건 이상인 이슈의 해석) · `issue_headline` 그대로. 함수에 `ISSUE_LEDGER_FILE` 같은 경로 주입이 이미 있으므로(진단용 구멍) 레인별 파일은 그 자리에 들어간다.

### 3-2. 상세 화면 섹션 ↔ 데이터

| 상세 섹션 | 읽는 필드 | AI 레인에서 채우는 곳 |
|---|---|---|
| 제목 | `title` | 대표 기사 `title_kr` (issue_headline) |
| 한 줄 결론 | `summary` | AI 판정 호출 |
| 기사 내용 | `detail` | AI 판정 호출(본문 있을 때) |
| 왜 중요한가 / 시사점 | `why_important` / `implication` | AI 판정 호출 → `split_interpretation` → 2건 이상이면 `issue_insight` 가 이슈 단위로 다시 씀 |
| 아직 확정되지 않은 것 | `open_question` | AI 판정 호출 |
| 사건 타임라인 | `related_articles` 날짜 정렬 | 카탈로그 |
| 이 사건의 근거 | `member_role=card` | 대표 기사 (+ 같은 날 같은 사건) |
| 추가 근거 | `member_role=evidence` | 다른 매체·후속 보도 |
| 근거 구성 배지 | `verification` | 카탈로그(공식 원문·독립 출처 수) — 벤더 PR 은 `distributed_claim` 이라 독립 출처로 안 센다. 그대로 둔다 |
| 관련 이슈 | 같은 topics/entity 의 다른 이슈 | **AI 카탈로그 안에서** 우선. 같은 기사 hash 를 공유하는 메인 이슈가 있으면 "원전 이슈에서 보기" 링크 1줄 |
| 에경연 인사이트 | KEEI 매칭 | 그대로(해외 AI 는 대부분 비어 있을 것) |

웹 변경은 작다: `openIssueDialog` 가 이슈를 찾는 자리가 `state.issues` 하나이므로 **레인별 컬렉션에서 찾는 조회 함수 하나**를 끼운다. 섹션 마크업·CSS 는 손대지 않는다.

---

## 4. 화면 (D3·D8·D10)

### 4-1. 「해외 AI」탭

- 탭 순서(안): `오늘 · 해외 AI · 흐름 · 탐색 · 보고서`. 상단 `main-tabs` 와 하단 `mobileTabs` **둘 다**(폰에선 하단이 유일한 통로). `longterm` 처럼 `hidden` + 데이터 게이트(누적 20건) 로 연다.
- 구성 = 머리 한 줄 + 칩 필터 + **목차 행 스트림**. 카드뉴스·오디오·먼저 볼 3건·한 주의 원자력·날짜 셀렉트는 없다.
- 행 = 기존 `tocRow`(번호·지역·대분류·제목·왜 중요한가·화살표)를 그대로 쓰되, 지역 칸은 **국가**, 대분류 칸은 **적용영역**, 꼬리에 **성숙도** 칩. 요약은 행에 안 쓴다("오늘"과 같은 원칙).
- **날짜 구분바** = 이슈 `last_seen`(KST) 기준, 바 오른쪽에 그날 건수. 0건인 날은 세우지 않는다.
- **연속 스크롤** = `ai_issues.json` 은 최근 90일 전량, 그 이전은 월별 shard 를 끝에서 로드(`news-manifest.json` 과 같은 방식). 초기엔 한 파일로 충분(하루 1~3건 × 90일).
- **모드 스위치(D3 후반)**: 빌드가 `meta.ai.last_14d_daily_avg`(중복 제거 후 사건 수)를 싣고, 5 이상이면 화면이 `daily`(오늘 하루치 + 날짜 이동)로 선다. 코드 상수가 아니라 데이터가 정하므로 양이 줄면 스트림으로 돌아온다.
- 클릭 → §3-2 의 기존 이슈 상세.

```
┌ 해외 AI                                     오늘 2건 · 이번 주 9건 · 누적 143건 ┐
│ [전체] [운영·정비] [인허가·규제] [설계·엔지니어링] [핵연료] [해체] [안전조치] [인력·거버넌스]
│ [전체] [실적용] [계약] [시범] [발표] [연구] [규제]
├──────────────── 10월 14일 (화) ───────────────────────────────────── 2건 ─┤
│ 01  🇺🇸 운영·정비   Constellation, 21기에 Atomic Canyon NIVA 전면 적용     실적용 ›│
│     왜 중요한가 한 줄 …                                                          │
│ 02  🇯🇵 인허가·규제  도쿄전력, 서류 업무 80% 감축 'AI 원전 시스템' 재가동   발표  ›│
├──────────────── 10월 13일 (월) ───────────────────────────────────── 1건 ─┤
│ 03  …                                                                            │
```

### 4-2. 탐색 탭의 "AI" 범위 (D9)

- 기존 `archiveScope` 세그먼트(`추적 중인 이슈 | 모든 이슈`)에 **`해외 AI`** 를 더한다. 선택하면 조회 대상이 `ai_issues.json` 으로 바뀌고, 나머지 필터(기간·주제·근거 구성·검색어·정렬)와 `archiveIssueMatches` 는 그대로 동작한다 — 레코드 모양이 같기 때문이다.
- 지역 필터는 이 범위에서 `해외` 고정(숨김). 주제 셀렉트에는 메인 `topics` 외에 **적용영역**을 더 보여준다(AI 레코드의 `application_area` 를 topics 와 같은 문법으로 노출).
- 탐색 허브(지금 많이 등장하는 대상)에 AI 범위의 엔티티(Constellation·Atomic Canyon…)가 서려면 `entity_registry` 에 그 이름들이 등록돼야 한다. 2단계 뒤 보강.
- URL 상태 `as=ai` 로 공유 가능(기존 `as=all` 과 같은 자리).

### 4-3. 산출물

`build_data.py` outputs 에 `ai_issues.json`, `ai_news-manifest.json`(+shards), `meta.ai{total,last_14d_daily_avg,mode}`. today.json 의 원칙대로 **이미 지은 레코드에서 잘라 담기만** 한다.

텔레그램·푸시 발송은 하지 않는다(요청에 없음).

---

## 5. 단계안

| 단계 | 내용 | 코드 | 판단 |
|---|---|---|---|
| **0. 기준 고정 + gold** | §2-3 기준으로 아카이브 3개월 후보 ~300건에서 **손라벨 50~80건**(in/out·exclude_reason·D5 경계 사례). 판정 프롬프트 초안을 gold 로 평가 | 로컬 스크립트만 | 정밀도·재현율. 경계 확정 |
| **1. 레인 + 섀도** | `lane` 소스 속성, 프리필터·판정 모듈, `ai_archive/`·`ai_state.json`·`ai_judgments.json`, 전용 피드 등록, 아카이브 3개월 **소급 1회**(메인 `sent` 가 막은 과거분 복구). **화면 없음.** 운영 요약(📋)에 "AI 레인: 원시 n · 프리필터 n · 통과 n · 사건 n" 한 줄 | 있음 | **2주 관측** → D3 분기 데이터 |
| **2. 이슈 + 화면** | 같은 함수로 `ai_issues.json`·`ai_issue_ledger.json`, 「해외 AI」탭(상단+하단, 게이트, 날짜 구분바, 칩), 상세 조회 레인화, 탐색 `해외 AI` 범위, 모드 스위치 | 있음 | 누적 20건에 자동 노출 |
| **3. 보강** | 엔티티 등록, 월별 shard, (요청 시) 주간 요약·발송 | 선택 | 데이터 보고 결정 |

0단계는 코드 없이 지금 가능하고, 그 결과가 1단계의 프롬프트와 테스트 fixture 가 된다.

---

## 6. 리스크

- **재현율 구멍**: ① AI 가 본문에만 있는 기사 — 전용 검색 피드(Google 은 본문 색인)가 메움. ② 메인 `sent` 가 이미 막은 과거분 — 1단계 소급 1회.
- **벤더 편중**: Atomic Canyon·Nuclearn 발표 반복. `maturity`·`actor_type` + 의미 그룹핑으로 "같은 제품의 n번째 고객"이 n행이 되지 않게. 그래도 많으면 성숙도 기본 필터를 `실적용·계약·규제`.
- **근거 배지가 약하게 나온다**: 이 분야는 단일 매체·벤더 PR 이 많아 "단일 출처·확인 중"이 다수일 것. 배지 기준을 레인별로 낮추지 **않는다** — 근거 구성은 사실이고, 그것이 보이는 게 맞다.
- **이슈 묶기 오병합**: 같은 벤더 제품의 서로 다른 고객 발표가 한 이슈로 접힐 수 있다. 메인의 `event_stage`·관리자 `issue_split` 경로가 그대로 쓰이므로 콘솔 교정이 가능하다. 1단계 관측에서 이 비율을 센다.
- **전용 피드 잡음**: 관련도순·부정 연산자 무시 → `when:1d` + 게이트 + 판정 3중.
- **번역 품질**: 전용 피드 기사의 `title_kr` 은 AI 판정 호출이 만든다. 메인 헤드라인 규칙을 그대로 옮기고 같은 정리기를 거친다.

---

## 7. 남은 결정 (착수 전, 소소함)

1. 적용영역 8종·성숙도 6종의 **한국어 라벨** 확정(칩·탐색 셀렉트에 그대로 쓰인다).
2. "관련 이슈"에서 메인 이슈로 넘어가는 링크를 둘 것인가(제안: 같은 기사 hash 를 공유할 때만 1줄).
3. 탭 노출 게이트 수치(제안: 누적 20건) 와 모드 전환 기준 산정 창(제안: 최근 14일).
