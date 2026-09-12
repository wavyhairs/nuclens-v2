# 장기 스토리 — 창을 재고, 판정을 검증하고, thread 를 세운다 (E·F)

> 2026-09-13. `docs/2026-09-12-event-identity.md`(D 정본) 다음 단계.
> **이 문서가 E·F 의 정본이다.** D 의 기록은 덮지 않는다.
>
> 진행 중 문서다. 각 단계가 끝날 때마다 이 파일과 커밋이 함께 움직인다 —
> 작업이 중단돼도 여기부터 이어받을 수 있어야 한다.

## 0. 시작 전에 계획을 코드에 대조했다

계획서는 `E1 → E2 → E3 → F` 였다. 실제 코드에 맞춰 보니 **E1 이 맨 앞에 설 수
없었다.** 뒤집힌 가정이 여덟 개다.

| # | 계획의 가정 | 실제 |
|---|---|---|
| B1 | `tools/recorded_replay.py` 를 창 재생에 재사용한다 | 그 모듈은 **Gemini HTTP 요청열** 재현기다(callsite: curation·dedup·issue_review·keei_match). 이슈 묶음을 다시 만들지 않는다 |
| B2 | 창은 상수 하나 | `ISSUE_WINDOW_DAYS` 는 **게이트 다섯**에 걸린다 — 기사 부착 2곳·후보 검색·canary 감사·카탈로그 cutoff/ceiling. 비용은 cutoff/ceiling 에서 난다 |
| B3 | LLM 캐시 적중 99.8% 라 새 호출 ≈ 0 | 캐시 키가 `_pair_id(기사해시, 기사해시)`다. **창을 넓혀 생기는 쌍은 정의상 한 번도 물어본 적 없는 쌍**이라 적중률 0. 게다가 키가 없으면 `issue_review` 는 병합하지 않는다 → 키를 끄고 재면 "창을 넓혀도 안 붙는다"는 **가짜 결론**이 나온다 |
| B4 | E1 다음에 E3(임베딩 보존) | `EMBEDDING_RETENTION_DAYS = 35` 이고 벡터가 없으면 `in_review_band` 가 False 라 회색지대에 **들어가지도 못한다**. 35·42일 arm 은 보존 정책을 먼저 고치지 않으면 측정 자체가 불가능 |
| B5 | 라이브 임베딩이 정상이니 그대로 잰다 | 맞다. 다만 **이 저장소 로컬에는 없다** — `embeddings.json` 이 8/15자(399 KB)고 임베딩은 git 이 아니라 Actions cache 에 산다 |
| B6 | 영속 `story_id` 를 새로 만든다 | `story_id` 는 **이미 있다**(`story_identity.py` · 기사 중복묶음의 신원). 계획이 `story_contract` 충돌은 잡았는데 이건 놓쳤다. `event_ledger.py` 도 이미 있고 **미래 일정 원장**이다 |
| B7 | Claude judge 어휘를 새로 만든다(`same/different/uncertain`) | `tools/review_queue.py` 에 이미 있다 — `SAME_EVENT · FOLLOW_UP_NEW_ACTION · FOLLOW_UP_NO_NEW_ACTION · RELATED_DISTINCT_EVENT · UNRELATED · INSUFFICIENT`. **same_event 와 same_story 를 가르는 축이 이미 코드에 있다**(`FOLLOW_UP_NEW_ACTION → issue_review MERGE / dedup SEPARATE`) |
| B8 | 6개월·수년 전 Event 재검색을 검증한다 | archive 가 2026-07(399건)·08(8,191)·09(4,425)뿐이다. **실질 코퍼스 6주.** 관측된 최대 근거 span 이 48일인 이유다 |

### 계층 이름표 — 먼저 못 박는다

```
Article  →  briefing story  →  issue / event  →  thread
            (story_id)         (issue_id)        (thread_id)
            중복묶음 신원       D 가 못 박은 사건 신원   장기 스토리 ← 새로 만드는 것
```

| 이미 쓰는 이름 | 현재 뜻 | 장기 계층이 쓸 이름 |
|---|---|---|
| `story_id` | 기사 중복묶음 신원 (`story_identity.py` v2) | `thread_id` |
| `event_ledger.py` | 달력 창 밖 **미래 일정** 원장 | `thread_relations` |
| `story_contract` / `STORY_CONTRACT_VERSION` | 기사 story 계약 v2 | `story_scope` |
| `issue_id` | Event 신원 | 그대로 — 공개 계약을 깨지 않는다 |

### 재배치한 순서

```
E0   하니스와 임베딩         ← 새로 생긴 단계 (B1·B5)
E3a  보존 정책               ← E1 보다 앞으로 (B4)
E1   21/28/35/42 재측정
E2   독립 판정 (기존 어휘로)
E3b  장기 retrieval
F    thread 계층 (shadow)
```

---

## E0. 하네스와 임베딩 — 완료

### 무엇을 만들었나

**`web/build_data.py` — 창에 구멍 하나.** 상수를 고치지 않는다. 환경변수
`NUCLENS_ISSUE_WINDOW_DAYS` 가 없으면 production 은 지금과 한 글자도 다르지 않게
돈다(기본값 21). 2,484건의 검사가 이 값에 묶여 있어 상수를 옮기는 변경은 회귀를
대량 생산한다.

**격리는 전수여야 했다.** 첫 재생을 돌리고 `git status` 를 보니 손대지 않은 파일이
바뀌어 있었다.

```
issue_insights.json    463줄
keei_llm_matches.json   72줄
```

빌드는 `OUTPUT_DIR` 밖에도 쓴다. 원장과 판정 캐시만 격리하고 이 둘을 몰랐다.
`ISSUE_REVIEW_CACHE_FILE` · `ISSUE_INSIGHT_CACHE_FILE` · `KEEI_MATCH_CACHE_FILE` ·
`ISSUE_LEDGER_FILE` 넷을 열고 재생기가 arm 사본을 넘긴다.

**`tools/window_replay.py` — 창 재생기.** 비교 단위는 **이슈 id 가 아니라 기사
쌍**이다. 창이 바뀌면 이슈 id 는 통째로 갈릴 수 있지만 "이 두 기사가 한 묶음에
있었나"는 창을 건너 비교된다. 원장과 판정 캐시는 arm 마다 **같은 기준 사본**에서
출발한다.

### 임베딩 — 로컬은 죽어 있었다

`embeddings.json` 이 **8월 15일자**(399 KB)였고 `local_embeddings.json` 은 없었다.
임베딩은 git 이 아니라 Actions cache 에 산다. 그대로 재면 remote embedding 경로가
통째로 죽은 시스템을 측정한다.

```
embedding_pipeline.py --window-days 70 --max-new 1200
    selected 747 · current 747 · generated 477 · failed 0 · coverage 1.0
```

첫 시도는 **한 줄 때문에 죽었다** — 생성 실패 한 건의 한국어 로그가 Windows
cp1252 stdout 에서 `UnicodeEncodeError` 를 내며 스크립트 전체를 중단시켰다
(311건에서 멈춤). Actions 는 UTF-8 이라 거기서는 안 난다. 로컬 재생은
`PYTHONIOENCODING=utf-8` 이 필요하다.

> ⚠️ 이 재생은 production 보다 **유리한 조건**이다. 운영에서는 21일 백필 + 35일
> 보존이라 40일 전 기사의 벡터가 이미 없다. 그래서 E3a 가 E1 앞에 온다.

---

## E3a. 보존 정책 — 완료

`EMBEDDING_RETENTION_DAYS = 35  # 21일 이슈 창 + 재실행 여유` 는 결합이 **주석에만**
있었다. 창을 옮기는 순간 조용히 깨진다.

```python
ISSUE_WINDOW_DAYS = _positive_int_env("NUCLENS_ISSUE_WINDOW_DAYS", 21)
EMBEDDING_RETENTION_MARGIN_DAYS = 14
EMBEDDING_RETENTION_DAYS = _positive_int_env(
    "EMBEDDING_RETENTION_DAYS", ISSUE_WINDOW_DAYS + EMBEDDING_RETENTION_MARGIN_DAYS)
```

기본값 35 로 **지금과 같다.** 백필 창도 이슈 창을 따라간다 — 워크플로에 박힌
`--window-days 21` 을 뺐다. 둘이 어긋나면 창 안에 있는데 벡터가 없는 기사가 생기고,
그 기사는 `in_review_band` 에서 `embedding_similarity=None` 으로 탈락해 회색지대에
들어가지도 못한다.

---

## E3b. 장기 retrieval — 완료

### 호기 이름이 한국어에서만 통했다

호기를 읽는 자리는 `build_data._UNIT_RE` 하나인데 `호기` 를 요구하고
`_FACILITY_NAMES` 25개가 전부 한글이다. **"Kori Unit 2" 는 호기 검사에 걸리지조차
않았다.** "한빛 3호기와 4호기는 어떤 기사를 경유해도 같은 사건이 아니다"라는 거부권이
영문 기사에는 서지 않고 있었다.

`entity_registry.json` 도 못 메운다 — plant 26건이 발전소 단위까지고 호기가 없다.
`asset_alias.py` 가 레지스트리의 별칭·`name_en` 을 그대로 쓰고 **호기 번호를 붙이는
규칙만** 더한다.

```
고리 2호기 · 고리2호기 · Kori Unit 2 · Kori-2 · Kori 2  →  {"kori-2"}
고리 3·4호기 · Hanbit 3 and 4                            →  두 토큰
신고리 2호기                                              →  shin-kori-2 하나만
```

### 원장이 이미 장기 원본이었다

`issue_ledger.json` 은 지우지 않는다(798건 · 792 KB · git 추적). 들고 있는 것이
제목·요약·해시뿐이라 검색할 거리가 없었을 뿐이다. `catalog_rows` 에 엔티티·호기·
국가·지문·근거일자를 더하고 **덮지 않고 쌓는다**.

이미 쌓인 798건은 `tools/backfill_ledger_retrieval.py` 가 아카이브에서 소급해 채운다.
빌드를 한 번 더 돌려 채우지 않은 이유: 그 방법은 지금 창의 60일 카탈로그만 건드려
7월 사건이 영영 빈 채로 남는다.

```
798건 채움 · 엔티티 291건(36%) · 호기 40건 · 근거 기사 없음 54건
지문 0건 — 아카이브 레코드에 story_fingerprint 가 없다(빌드 단계 산물).
           앞으로 쌓이는 사건은 catalog_rows 가 채운다.
```

### 색인은 파생물이다

```
사건 798건 · 색인 0.03초 · 491 KB · 질의 ~1 ms
```

외부 Vector DB 를 쓰지 않는다. 그리고 **임베딩에 기댈 수도 없다** — 벡터는
`EMBEDDING_RETENTION_DAYS`(창 + 14일) 만큼만 살고, 로컬 폴백 벡터는 빌드마다 다른
IDF 공간이다. 즉 의미 검색은 원리적으로 최근 구간만 덮는다. **장거리는 구조화
신호와 어휘가 진다.**

실측 — 창(21일) 밖에서 같은 호기 사건이 상위에 선다:

```
[2026-07-17] 한빛 1·2호기 계속운전, 원안위 검토 작업 가속화
   14.71  gap=20일  고리 3·4호기 올해, 한빛 1·2호기 내년 계속운전 심사 상정 예정
   14.69  gap=57일  원안위, 고리 3·4호기 및 한빛 1·2호기 사고관리계획 심의 보류
   13.07  gap=50일  한빛 1·2호기 열교환기 보수작업 용접절차서 오적용 6건 확인  ← 다른 이야기
```

마지막 줄이 이 단계의 한계를 그대로 보여준다. 같은 호기지만 계속운전과 정비는
다른 이야기다. **후보 검색은 recall 이고 그것을 가르는 것은 판정의 일이다.**

---

## E1. 창 재측정 — 진행 중

## E2. 독립 판정 — 대기

## F. thread 계층 — 뼈대 완료, 실측 대기
