# Object 기반 Event Identity — 가설 검증 결과 (GO/NO-GO)

> 2026-09-20. 브랜치 `exp/object-event-identity` (origin/main `80748d8` 위). **production 에
> 연결하지 않았다.** 코드는 `experiments/object_identity/` 에만 있고, 어떤 production 모듈도
> 그것을 import 하지 않는다(`tests/test_object_identity_experiment.py::IsolationTests` 가 잠근다).
> LLM 0회 · 네트워크 0회 · 저장소 파일 변경 0개. 재생 30초.

## 0. 결론 먼저 — **NO-GO** (이 형태로는)

가설: *Event 의 신원 키는 (object_ref, transition, date_bucket) 셋이고 셋이 다 맞아야 같은 Event
다. 하나라도 모르면 "같다"가 아니라 "모른다"이며 현재 경로로 후퇴한다.*

과거 데이터 전체(7/17~9/20, 3,168건)에 결정적으로 구현해 production 분할과 같은 쌍 위에서
대조한 결과, **가설이 production 보다 나은 자리는 없다.** 세 지표 모두에서 두 극단 사이를
왕복할 뿐 둘을 동시에 잡지 못한다.

| 지표 (gold 343쌍) | production | 명세대로 (`v0-spec`) | 느슨하게 (`v0-loose`) |
|---|---:|---:|---:|
| false merge (다른 사건 222쌍 중 같은 Event) | 44 (19.8%) | **13 (5.9%)** | 63 (28.4%) |
| false split (같은 사건 48쌍 중 갈라 둠) | 38 (79.2%) | 44 (91.7%) | **33 (68.8%)** |
| false split recovery (production 이 가른 38쌍 중 되붙임) | — | 2 (5.3%) | 10 (26.3%) |
| 그 대가로 새로 생긴 false merge | — | 2 | **47** |
| material 보존 (새 행동 73쌍을 다른 Event 로) | 60 (82.2%) | **67 (91.8%)** | 56 (76.7%) |

명세대로 돌리면 false merge 와 material 보존은 좋아지지만 **거의 아무것도 붙이지 않아서** 그렇다
(같은 사건 48쌍 중 4쌍만 붙임, 회복 2쌍). 느슨하게 돌리면 회복이 늘지만 새 false merge 47쌍을
낸다 — production 의 전체 false merge(44)보다 많다. 버킷 14/30/60일은 결과를 한 건도 바꾸지
않았다.

이유는 셋이고, 셋 다 **키의 재료** 문제다. 규칙을 조정해서 풀리는 문제가 아니다.

1. **Object 커버리지 12.6%.** 결정적으로 Object 를 아는 기사가 3,168건 중 398건(호기 123 ·
   전기본 178 · 포천양수 9 · 발전소만 88)이다. 단계까지 아는 "키 셋 완비"는 212건(6.7%).
   나머지 87%는 정의상 production 을 그대로 따른다. 어휘표를 손으로 16개 적어 넣어도 완비
   459건(14.5%)이다.
2. **transition 은 닫힌 값 하나가 아니다.** `event_stage` 가 제목에서 뽑는 것은 **집합**이고,
   같은 사건의 헤드라인끼리 집합이 다르다. 호기 기사가 든 production 묶음 32개 중 **17개**가
   단계 집합을 둘 이상 갖는다. `한빛 2호기 설계수명 만료 가동 정지` 한 사건이 매체마다
   `{shutdown}` · `{approval, review, shutdown}` · `{approval, restart, review, shutdown}` 로
   갈려 명세 arm 은 이것을 **5개 Event** 로 세웠다. 집합 동일(엄격)은 같은 사건을 조각내고,
   교집합(느슨)은 다른 사건을 잇는다. 그 사이 값이 없다.
3. **정책문서 Object 는 Event 의 Object 가 아니다.** gold 에서 Object 를 아는 쌍 117개 중
   **113개가 12차 전기본**이다. 전기본 위에서 토론회 7차 · 수요전망 공개 · 공론화 착수 ·
   초안 마련은 서로 다른 사건인데 transition 어휘(원자로 생애주기)가 하나도 가르지 못한다.
   느슨하게 돌리면 전기본 기사 178건이 **Event 하나(190건)** 로 접히고(false merge 32/66),
   엄격하게 돌리면 39개로 갈리되 같은 사건도 못 붙인다(false split 24/27). 필요한 것은
   Object × **Process × Occurrence** 인데 그것을 결정적으로 뽑는 추출기가 없다.

가설이 실제로 옳게 작동한 자리도 있다 — **호기 수준, 사고·정지·재가동 계열.** 새울 3호기
8/12 자동정지 → 9/4 원인 발표 → 9/4 재가동 승인 → 9/6 수동정지 → 9/7 조사 착수를 Object
하나 위의 단계별 Event 로 세웠고, production 이 단독 묶음으로 떨어뜨린 8/12 발생 기사를 23일
뒤 원인 발표와 되이었다(`key_match`). 호기 모순은 전수 0건이다. 그러나 이 계열은 호기 기사
123건(3.9%) 안의 일부이고, gold 로 판정할 쌍이 4개뿐이라 **수치로 증명되지 않는다.**

**권고.** 이 설계로 장기 shadow · migration · UI 에 들어가지 말 것. 다시 열 조건은 §6.

---

## 1. 무엇을 만들었나

```
experiments/object_identity/
  objects.py    Object 추출. v0 결정적(호기 asset_alias · 태그/제목의 레지스트리 plant · project) ·
                v0e(큐레이션 게이트 엔티티 포함) · v0-x(실험용 어휘표 16개 — 레지스트리 아님)
  resolver.py   Event resolver. 계층 0 거부권(호기·국가·사람 rejected·단계 모순) → 1 Object →
                2 transition(제목 단계, 없으면 게이트 단계)·발행일 → 3 무창 조회
  replay.py     전체 재생 · production 분할 대조 · 지표 · 검토표
  inspect_run.py 사례 추출(사람 판정 22쌍 · 호기 Event 전수 · 단계 분열 자리)
tests/test_object_identity_experiment.py   16건 (규칙 계약 + 격리)
eval_artifacts/object_identity/
  metrics.json       arm 14개 전부의 수치
  review_table.md / .csv   검토표 180쌍 (사람 판정 칸 비어 있음)
web/_shadow/object_identity/assignments.jsonl   기사별 배정 전수 (gitignore)
```

grade 넷: `object`(키 셋 완비) · `partial`(Object 는 알고 단계는 모름) · `entity`(발전소만) ·
`lexical`(Object 없음 → production 묶음을 그대로 따른다). 가설은 `object` 에서만 다르게
행동하므로 성적도 거기서 따로 냈다(§3).

arm 은 리뷰 §10 의 명세를 축으로 삼고 한 축씩 풀어 어디서 무엇이 깨지는지 봤다.

| 축 | 명세 (`spec`) | 대안 |
|---|---|---|
| transition 비교 | 집합 동일 (`strict`) | 교집합 (`loose`) |
| 키로 못 붙었을 때 | 키 셋 중 하나를 **모를 때만** production 묶음을 다리로 (`partial`) | 항상 (`bridge`) / 없음 (`nobridge`) |
| Object 출처 | 제목·태그 (`v0`) | + 게이트 엔티티 (`v0e`) / + 어휘표 (`v0x`) |
| 버킷 | 30일 | 14 / 60 |
| 절차 힌트 | 없음 | topics 교집합 (`process`) |

## 2. 재료 — 무엇을 재생했고 무엇으로 채점했나

**모집단** 원장(`issue_ledger.json`, 911묶음·3,200해시) ∪ 라이브 `issues.json`(9/20, 624묶음·
3,026해시) ∪ 발송 카드 941건 = 3,225해시. 아카이브(`archive/2026-0{7,8,9}.jsonl`)에 3,168건.
발행일 범위 2026-07-17 ~ 09-20.

**production 기준선** 해시 → 묶음을 production 자신의 소유권 규칙(`event_identity.owner_index`,
`last_written` 최신)으로 풀고 `moved_to` 를 끝까지 따라갔다. 동값으로 버려진 608건은 라이브
묶음으로 채웠다(원장 ↔ 라이브 불일치 11건). 결과 **660묶음**, 단독 42%, 최대 70건.

**날짜** 발행일. 게이트를 지난 `event_date` 를 먼저 써 봤더니 `2028-01-01`(목표연도) ·
`2026-12-01`(예정 발표)이 들어와 8월 기사가 연말 자리에 섰다. 사건일은 타임라인 노드의
날짜이지 신원 버킷의 날짜가 아니다. 7월 수집분 116건은 `pub` 이 비어 `archived_at` 을 썼다.

**gold** 374쌍 — 사람 22(`.eval/identity-review.json`, 로컬 사이드카) + Claude blind 352
(`eval_artifacts/event_judgments_claude.jsonl`, 창 재생 delta 의 가린 판정). 모집단 안 343쌍.
관계 분포(374쌍): 다른 사건 242(RELATED_DISTINCT 147 · UNRELATED 95) · 같은 사건 52(SAME 20 ·
FOLLOW_UP_NO_NEW_ACTION 32) · 새 행동 80(FOLLOW_UP_NEW_ACTION). 모집단 안 343쌍은 222 · 48 · 73.
간격: 0~7일 146 · 8~21일 133 · 22~35일 56 · 36일+ 8.

> **gold 의 편향을 알고 읽어야 한다.** 이 쌍들은 production 의 회색지대·창 확대 delta 에서
> 나왔다. 그래서 (a) Object 를 아는 쌍 117개 중 113개가 전기본이고 호기는 4개, (b) 같은-사건
> 쌍의 79%를 production 이 갈라 둔 상태다(창 밖이거나 회색지대라 뽑힌 쌍이니까). 이 표는
> "production 이 이미 어려워한 쌍" 위의 성적이고, 호기 계열은 여기서 판정되지 않는다.

**silver** `issue_llm_reviews.json` 의 Gemini 쌍 판정 14,661쌍(모집단 안). production 자신의
신호라 정답이 아니고 방향만 본다.

**지표 정의**
- false merge: gold 가 다른 사건이라 한 쌍을 같은 Event 로 둔 것.
- false split: gold 가 같은 사건이라 한 쌍을 갈라 둔 것. recovery = production 의 false split
  가운데 resolver 가 붙인 것. 그 대가 = production 은 갈라 뒀는데 resolver 가 붙인 다른-사건 쌍.
- material 보존: gold 가 새 행동(FOLLOW_UP_NEW_ACTION)이라 한 쌍을 **다른 Event** 로 둔 것.
  전수에서는 production 의 `issue_continuity.progression()` 이 material 이라 한 인접 쌍 159개를
  resolver 가 갈라 두는 비율로도 잰다.

## 3. 결과

### 3.1 arm 전체 (gold 343쌍)

| arm | Event 수 | FM /222 | FS /48 | material 보존 /73 | recovery /38 | 새 FM | prod-material 갈라둠 /159 | Gemini 일치 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| production | 660 | 44 | 38 | 60 | — | — | 0 (정의상) | 0.851 |
| v0-spec | 891 | 13 | 44 | 67 | 2 | 2 | 64 (40%) | 0.797 |
| v0-spec-b14 / b60 | 897 / 890 | 13 | 44 | 67 | 2 | 2 | 64 | 0.797 |
| v0-strict-nobridge | 922 | 11 | 44 | 67 | 2 | 1 | 66 | 0.796 |
| v0-loose | 818 | 63 | 33 | 56 | 10 | 47 | 41 | 0.796 |
| v0-loose-nobridge | 868 | 32 | 43 | 65 | 3 | 17 | 45 | 0.796 |
| v0-loose-title (제목 단계만) | 718 | 55 | 31 | 59 | 9 | 36 | 37 | 0.823 |
| v0e-spec (게이트 엔티티) | 929 | 8 | 45 | 68 | 1 | 1 | 74 | — |
| v0e-loose | 807 | 37 | 36 | 56 | 8 | 26 | 47 | — |
| v0x-spec (어휘표) | 955 | 16 | 45 | 72 | 1 | 6 | 78 | 0.779 |
| v0x-loose | 793 | 61 | 29 | 57 | 14 | 46 | 47 | 0.786 |
| v0x-spec-process (topics 힌트) | 957 | 16 | 45 | 72 | 1 | 6 | 78 | — |

멱등성은 전 arm 1.000. 호기 모순은 전 arm 0쌍(production 1쌍).

읽는 법: 세로로 내려가면 **FM 과 FS 가 반대로 움직인다.** 엄격할수록 FM↓ FS↑, 느슨할수록
FM↑ FS↓. production 은 그 사이 어딘가에 있고, 어느 arm 도 production 의 오른쪽 위(FM 도 FS 도
낮은 자리)에 서지 못했다. 버킷은 무관하고, topics 절차 힌트는 한 쌍도 바꾸지 않았다.

### 3.2 가설이 손을 댄 자리만 — object grade 쌍 117개

| | production | v0-spec | v0-loose |
|---|---:|---:|---:|
| false merge /67 | 9 | **3** | 33 |
| false split /28 | 21 | 25 | **15** |
| material 접힘 /22 | 3 | **0** | 11 |

이 117쌍은 전기본 113 + 호기 4다. 즉 이 표는 **전기본 위에서의 성적**이다. 느슨한 키는
전기본 기사 178건을 Event 하나(190건, lexical 추종 포함)로 접었고, 엄격한 키는 39개로 가르되
같은 토론회 보도조차 못 붙였다.

### 3.3 전수 구조 — Object 종류별 Event

`v0-spec` 기준.

| Object 종류 | Event | 기사 | production 묶음을 넘어 붙인 횟수 | 21일 초과 span | 최대 | 같은 Object 위 단계 분열 |
|---|---:|---:|---:|---:|---:|---:|
| 호기(unit) | 49 | 162 | 11 | 5 | 11건 | 28 |
| 전기본(project) | 39 | 297 | 55 | 7 | 43건 | 29 |
| 포천양수(project) | 4 | 12 | 0 | 0 | 8건 | 2 |
| 발전소만(entity) | 40 | 182 | 3 | 3 | 16건 | 23 |

production 의 material 인접 쌍 159개 중 두 기사가 다 object grade 인 것은 24개이고 `v0-spec`
은 그중 23개를 다른 Event 로 뒀다(96%). 느슨한 arm 은 7개(29%). 즉 **material 보존은 엄격함의
함수**이고, 엄격함은 같은 사건도 가른다(§3.4).

### 3.4 사례 — 호기 계열에서 실제로 일어난 일

전수 목록은 `inspect_run.py` 출력과 검토표 F·E·I 범주에 있다. 대표 셋만 적는다.

**되이음 (가설이 맞은 자리).** 새울 3호기. production 은 8/12 자동정지 기사 둘을 단독
묶음으로 두고 8/14 카드부터 9/9 까지 15건을 한 묶음(`issue-bdf0fa9bc581aaa0`)에 넣어
change_log 로 9/6·9/7·9/9 material 을 기록했다. `v0-spec` 은 같은 기사를 Object 하나 위의
Event 로 세웠다 — 자동정지(8/12~9/4, 원인 발표까지 `key_match` 23일) · 조사 착수(8/12,
9/7) · 재가동 승인(9/4~9/5, 6건) · 수동정지(9/6~9/7, 5건) · 조사 착수(9/7). 창 밖 되이음과
단계별 Event 가 둘 다 일어났다.

**조각남 (가설의 재료가 깨진 자리).** 한빛 2호기 설계수명 만료 가동 정지(9/8~9/14). production
은 한 묶음(`story-4d181084fd09b33f`, 28건, 한빛2 23건). `v0-spec` 은 **5개 Event**: `{shutdown}`
6건 · `{review, shutdown}` 7건 · `{approval, shutdown}` 2건 · `{approval, restart, review,
shutdown}` 4건 · `{application, approval}` 1건(계속운전 허가 신청 — 이건 진짜 다른 Event 다).
앞의 넷은 같은 정지를 헤드라인이 다르게 쓴 것이다. 새울 1호기 계획예방정비 완료 100% 출력
도달도 같은 날 3개 Event 로 갈렸다(제목 단계가 비어 게이트 단계로 후퇴했는데 그것이 매체마다
다르다).

**잘못 붙음 (느슨한 키의 자리).** `한빛 1·2호기 계속운전, 내년 상반기 심의 상정 예정`(8/4)
↔ `원안위, 고리 3·4호기 및 한빛 1·2호기 계속운전 심의 착수`(9/10). gold: 다른 사건(단계 —
예정 vs 착수). 호기 같음 · 단계 `{review}` 같음 · Event 의 마지막 날짜 기준 버킷 안 → 붙었다.
transition 어휘에 확정도(예정/착수/보류)가 없다 — `issue_continuity.PROGRESSION_SCALES` 의
certainty 축이 바로 그것인데 Event 키에는 들어 있지 않다.

**추출기 잡음.** `kori-34`(4건, '고리3·4호기' 태그의 붙여쓰기) · `shin-hanul-34` · `dukovany-2029`
(영문 제목의 연도) · `wolsong-1`(포스코 PPA 기고문의 언급). production 의 `asset_alias` 그대로다.
Object 를 신원 키로 올리면 이 잡음이 주소가 된다.

### 3.5 사람 판정 22쌍

모집단 안 15쌍. production 11/15 · `v0-spec` 13/15. 갈린 4쌍은 전부 lexical grade 다 —
Object 가 판정한 것이 아니라, production 묶음이 단계 분열로 여러 Event 로 갈리면서 lexical
기사가 최근 Event 에 붙는 부수 효과다. 이 수치로 가설을 지지할 수 없다.

## 4. 왜 "규칙을 더 다듬으면" 이 아닌가

세 재료 문제를 하나씩 보면:

- **커버리지**는 레지스트리 큐레이션의 문제다. 어휘표 16개를 손으로 적어 커버리지를 12.6 →
  25.4%로 올렸을 때 성적은 좋아지지 않았다(`v0x-loose` 새 FM 46). 어휘표 항목(`차등요금제`
  · `반도체 클러스터 전력`)은 대상이 아니라 **주제**였고, 주제를 Object 로 올리면 tags 경로와
  같은 오병합이 난다. 정말 Object 인 것(딜·법안·절차)은 기사마다 약칭이 달라 결정적 추출이
  되지 않았다.
- **transition** 은 `event_stage` 의 설계 목적과 어긋난다. 그 모듈은 **거부권**용으로 "양쪽
  다 말했고 하나도 안 겹칠 때만" 발동하도록 넓게 잡혀 있다(모듈 머리말). 넓게 잡힌 집합을
  **동일성 키**로 쓰면 같은 사건이 갈리고, 교집합으로 쓰면 다른 사건이 붙는다. 이건 임계값이
  아니라 어휘의 성격이다. 값 하나로 닫힌 transition 을 만들려면 새 추출기(또는 LLM)가
  필요하고, 그러면 "결정적 키"라는 전제가 사라진다.
- **정책문서** 는 Event 가 아니라 Thread 의 Object 다. 리뷰가 Thread 키를 Object × Process 로
  둔 것은 맞았지만, Event 키에도 Process(와 회차)가 필요하다는 것이 이 재생의 결과다.
  전기본 위의 사건들은 "어느 절차의 몇 번째 회차"로만 갈린다.

## 5. 검토표 — 180쌍

`eval_artifacts/object_identity/review_table.md`(읽기용) · `.csv`(사람 판정 칸 `human_verdict` ·
`human_note` 비어 있음). 열: 두 기사(날짜·제목) · 간격 · Object · 단계 집합 · production ·
resolver(`v0-spec`) · loose(`v0-loose`) · grade · 배정 이유 · gold.

| 범주 | 쌍 | 뜻 |
|---|---:|---|
| A. 사람 판정 | 15 | 이미 사람이 라벨한 쌍 전부 |
| B. gold 위에서 두 시스템이 갈림 | 50 | 결정적 증거 — 어느 쪽이 맞는지 사람이 확정 |
| C. resolver 가 gold 와 어긋남 | 51 | 위험 사례 |
| D/E. 전수에서 resolver 만 붙임 (장기/단기 간격) | 4 / 15 | 창 밖 되이음 후보 |
| F. 같은 production 묶음을 단계로 가름 | 25 | material 보존 vs 조각남의 경계 |
| G. 그 밖의 가름 | 20 | |

두 시스템 · 두 arm 의 조합: (production=same, spec=split, loose=split) 53 · (same, split, same) 40 ·
(split, split, split) 37 · (split, same, split) 14 · (split, split, same) 10 · (split, same, same) 9 ·
(same, same, same) 14 · (same, same, split) 3. **B·C·F 의 126쌍**이 사람 판정이 결론을 바꿀 수
있는 자리다 — 특히 F 25쌍: 사람이 "다른 Event 가 맞다"고 다수 판정하면 §3.4 의 "조각남"이
일부 "보존"으로 바뀐다. 그래도 §0 의 커버리지·transition 문제는 그대로다.

## 6. 다시 열 조건

이 결과가 뒤집히려면 아래 셋 중 **둘 이상**이 먼저 증명돼야 한다. 셋 다 production 을 건드리지
않고 오프라인으로 잴 수 있다.

1. **transition 안정성.** 같은 사건의 다출처 보도(production 묶음 안 같은 날 카드+근거)에서
   transition 값이 하나로 나오는 비율. 지금 호기 묶음 기준 15/32 (47%). 목표는 90% 이상 —
   그 전에는 어떤 키도 같은 사건을 가른다.
2. **Object 커버리지.** 발송분에서 결정적 Object 가 있는 비율. 지금 12.6%. 레지스트리를
   딜·법안·절차 kind 로 넓혀 50%를 넘기고, 100건 표본에서 오할당 0 을 보여야 한다.
3. **Occurrence 축.** 정책문서·절차 Object 에서 "몇 번째 회차·어느 안건"을 결정적으로 뽑는
   추출기. 전기본 토론회 회차가 시험대다.

이 셋이 없는 상태에서 값이 있는 것은 하나다 — **호기 Object 를 거부권으로 쓰는 것**인데, 그건
production `_cluster_facility_conflict` 가 이미 하고 있다(전수 호기 모순 1쌍).

## 7. 재현

```bash
# 워크트리에서. 라이브 issues.json 은 production 분할 보정용(없어도 돈다 — 원장만으로).
S=https://nuclens-v2.pages.dev; mkdir -p /tmp/live
curl -fsS "$S/data/issues.json" -o /tmp/live/issues.json
PYTHONIOENCODING=utf-8 python -m experiments.object_identity.replay \
    --live-issues /tmp/live/issues.json \
    --human-labels ../../.eval/identity-review.json      # 로컬 사이드카. 없으면 Claude 판정만
PYTHONIOENCODING=utf-8 python -m experiments.object_identity.inspect_run /tmp/live/issues.json \
    ../../.eval/identity-review.json '{"strict_transition": true, "production_bridge": "partial"}'
python -m unittest tests.test_object_identity_experiment
```

## 8. 하지 않은 것 (지시대로)

장기 shadow 운영 · migration · UI · gold 확장 · production 연결. 검토표의 사람 판정 칸은 비어
있다 — 그것을 채우는 일은 gold 확장이라 범위 밖이다.
