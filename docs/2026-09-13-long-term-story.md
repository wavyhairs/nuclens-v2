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

## E0. 하니스와 임베딩 — 진행 중

### 무엇을 만들었나

**`web/build_data.py` — 창에 구멍 하나.** 상수를 고치지 않는다. 환경변수
`NUCLENS_ISSUE_WINDOW_DAYS` 가 없으면 production 은 지금과 한 글자도 다르지 않게
돈다(기본값 21). 2,484건의 검사가 이 값에 묶여 있어 상수를 옮기는 변경은 회귀를
대량 생산한다.

**`issue_ledger.py` — 경로에 구멍 하나.** `ISSUE_LEDGER_FILE`. 창 재생이 **운영
원장을 더럽히면 안 된다.**

**`tools/window_replay.py` — 창 재생기.** arm 마다 자기 `OUTPUT_DIR` ·
`ADMIN_OUTPUT_DIR` · 원장 사본을 준다. 원장은 **같은 기준 사본에서 출발**한다 —
arm 끼리 다른 원장에서 시작하면 상속률 차이가 창 때문인지 원장 때문인지 갈리지
않는다.

비교 단위는 **이슈 id 가 아니라 기사 쌍**이다. 창이 바뀌면 이슈 id 는 통째로 갈릴
수 있지만 "이 두 기사가 한 묶음에 있었나"는 창을 건너 비교된다.

### 임베딩

`embedding_pipeline.py --window-days 70` 로 재생 코퍼스를 채웠다. 로컬 캐시가
8/15자라 그대로 돌리면 **remote embedding 경로가 죽은 시스템**을 재게 된다.

> ⚠️ 이 재생은 production 보다 **유리한 조건**이다. 운영에서는 21일 백필 + 35일
> 보존이라 40일 전 기사의 벡터가 이미 없다. 그래서 E3a 가 E1 앞에 온다.

---

## E3a. 보존 정책 — (착수 전)

## E1. 창 재측정 — (착수 전)

## E2. 독립 판정 — (착수 전)

## E3b. 장기 retrieval — (착수 전)

## F. thread 계층 — (착수 전)
