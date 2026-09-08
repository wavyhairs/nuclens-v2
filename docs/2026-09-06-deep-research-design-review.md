# Nuclens+ Adaptive Deep Research 설계안 — 코드 대조 검토 및 개선안

- 작성일: 2026-09-06
- 대상: 「Nuclens+ Adaptive Deep Research / Feature Intelligence — 진화된 설계안 정리본」(48개 절)
- 성격: 설계 검토 의견서 / 구현 명세 아님. **코드는 한 줄도 수정하지 않았다.**
- 검토 방법: 원 설계안을 읽고, 그 주장이 실제 Nuclens 코드베이스에서 성립하는지 파일 단위로 대조.
  아래의 모든 제약은 추측이 아니라 `refactor/v5-checkpoint` 시점 코드·데이터에서 실측한 것이다.
- 용도: 다른 AI 도구·검토자에게 넘겨 추가 비판을 받기 위한 입력 문서. 이 문서 하나만 읽어도
  맥락이 서도록 §1에 시스템 제약을 먼저 정리했다.

---

## 0. 요약

원 설계안의 사고 수준은 높다. 특히 다음 넷은 이 종류의 시스템이 실패하는 전형적 방식
(예쁜 글은 나오는데 검증이 안 됨)을 정확히 겨냥한다.

1. Story Arc는 결과물이 아니라 조사 재료다
2. 초기 질문은 정답이 아니고, 조사 중 진화해야 한다
3. Research Yield Gate — 새 정보가 없으면 발행하지 않는다
4. UNKNOWN을 정상 결과로 인정한다

문제는 다른 데 있다. **설계안이 Nuclens가 지금 어떤 제약 위에서 돌아가는지를 거의 반영하지
않았다.** 48개 절 중 실제 코드와 충돌하거나 실행 불가능한 것이 8건이고, 그중 4건은 설계를
다시 그려야 하는 수준이다. 반대로 이미 프로덕션에 있는 재료를 새로 만들려는 부분이 10곳 있다.

| 구분 | 건수 | 위치 |
|---|---|---|
| 🔴 설계를 다시 그려야 하는 충돌 | 4 | §2.1 ~ §2.4 |
| 🟠 구현 전에 해결해야 하는 충돌 | 4 | §2.5 ~ §2.8 |
| ♻️ 이미 있는 것을 다시 만들려는 부분 | 10 | §3 |
| ★ 설계 변경 제안 | 6 | §4 |
| ➕ 설계에 아예 없는 항목 | 9 | §5 |

**가장 먼저 할 일 하나만 고른다면:** Expectation Ledger(일정 약속 이력)를 Deep Research 밖에서
단독 기능으로 만들어 이슈 페이지에 붙일 것. 기존 `event_schedule.json` + 아카이브만으로 대부분
가능하고, LLM 호출이 거의 없으며, "우리가 겹쳐 보면 남들이 못 보는 것이 보인다"는 이 제품의
핵심 가설을 2주 안에 검증한다. 참이면 나머지 47개 절을 지을 근거가 생기고, 거짓이면 아주 싸게
알게 된다. (근거: §4.1)

---

## 1. 검증된 시스템 제약 — 설계안이 반드시 전제해야 할 사실

> 이 절은 다른 검토자를 위한 것이다. 원 설계안에는 이 정보가 없어서, 그 상태로 검토하면
> 실행 불가능한 제안이 계속 나온다.

### 1.1 실행 모델

- 모든 파이프라인은 **GitHub Actions cron**에서 돈다. 상시 서버 프로세스가 없다.
  - crawl: `11 */3 * * *` (3시간마다)
  - daily-brief: `25 19 * * *` UTC (KST 04:25, 실제 도착은 04:45~05:40으로 흔들림)
  - weekly: 금 `7 8 * * 5`
- 웹은 **Cloudflare Pages 정적 사이트**. 서버 로직은 Pages Functions(엣지)뿐이다.
- 운영 콘솔(`/admin`)의 **유일한 쓰기 경로는 Cloudflare KV**다.
  `functions/admin/api/overrides.js:4-8`에 이유가 명시돼 있다 — "저장소에 쓰려면 GitHub 쓰기
  토큰을 엣지에 두어야 한다. 콘솔 비밀번호 하나가 저장소 쓰기 권한으로 번지는 구조라 그렇게
  하지 않는다." KV는 버퍼이고 git이 DB다.
- 그래서 관리자 판정은 **다음 워크플로 회차부터** 반영되며, 화면은 "아직 반영 안 됨"을 숨기지
  않는다. KV → git 동기화는 `tools/sync_admin_overrides.py`가 워크플로 시작 시 수행한다.
- KV 쓰기 창구의 하드 리밋: `KV_KEY = "admin:overrides"`, `MAX_ENTRIES = 400`,
  `MAX_BODY_BYTES = 64 * 1024` (`overrides.js:17-19`). 400건 초과 시 409로 거부한다.

**함의: 대화 1턴 = 워크플로 1회차 = 최대 3시간. 실시간 대화형 조사는 현재 구조에서 불가능하다.**

### 1.2 저장 — git이 DB다

실측(2026-09-06):

```
.git                     254 MB   (size-pack 171.9 MB)
curated.json            16.7 MB
issue_llm_reviews.json   5.3 MB
delivery_log.jsonl       4.6 MB
digest_queue.json        3.8 MB
archive/                  28 MB   (2026-07: 399건, 2026-08: 169건)
embeddings.json          399 KB
```

`daily-brief.yml:94-99` 주석: "2026-08-26 웹 데이터 빌드는 **누적 데이터가 커지며 6분을 넘겼고**,
같은 입력이 별도 배포 잡에서는 약 **19분** 뒤 정상 완료됐다."

**함의: 전체를 다시 쓰는 큰 JSON은 리비전마다 통째로 git에 쌓인다. 조사 산출물은 append-only
샤드여야 한다.**

### 1.3 저작권 계약 — 원문 본문을 저장하지 않는다

2026-07-31 판단이고, 코드·테스트·공개 문구 세 곳에 고정돼 있다.

- `article_body.py:20` — "저작권 판단(2026-07-31)을 뒤집지 않는다 — 남는 것은 한국어 요약뿐이다"
- `news_archive.py:17` — "원문 본문은 저장하지 않는다 (저작권 — 제목·요약·링크만)"
- `news_bot.py:1532`, `news_bot.py:2026` — "저장하지 않고 이 호출/프롬프트 안에서만 쓴다"
- `pubs_fetch.py:267` — "상세 페이지에서 목차만 뽑는다 — 제목 줄만, 본문 저장 금지(저작권)"
- 회귀 테스트: `tests/test_archive.py:81`(`assertNotIn("description", r)`),
  `tests/test_no_body_interpretation.py`, `tests/test_keei.py:3`,
  `web/tests/test_prototype.py:3542`
- 공개 문구: 사이트 푸터 "Nuclens는 제목·요약·출처 링크만 제공합니다. 원문 저작권은 각 언론사와
  기관에 있습니다."

### 1.4 "AI 판단 금지" 계약

모든 LLM 산출물에서 예측·권고·투자판단을 금지한다. 세 모듈에 같은 문장이 반복된다.

- `trend_insights.py:14` — "전망·예측·권고·투자판단 금지. 근거 기사에 있는 사실의 '구성과
  방향'만 서술"
- `issue_insight.py:30` — "예측·권고·투자 판단 금지 (trend_insights·daily_lead 와 같은 계약)"
- 이 계약의 기원은 사용자 지적이다 (`issue_insight.py:6-13`, 2026-08-05 "빈껍데기 해석" 사건).

### 1.5 정체성(identity)의 안정성

- `story_id` — `story_identity.py`가 유일한 변경 권한자. 원칙: "dedup은 표시 그룹을 만들 수
  있어도 identity를 회원 사이에 옮길 수는 없다", "legacy 레코드는 읽고 매칭할 수 있으나
  독립 증거 없이는 ID를 상속하지 않는다". `IDENTITY_VERSION = 2`.
- `issue_id` — **안정적이지 않다.** 매 웹 빌드마다 아카이브에서 재계산되며, 웹 빌드는 하루
  12회 이상 돈다. 충돌 시 `resolve_local_issue_id_conflicts()`(`web/build_data.py:4864`)가
  ID를 `issue-{article_hash}`로 강제 교체하고 `identity_status="quarantined"`를 붙인다.
  5건(`max_local`)을 넘으면 `ValueError`로 빌드를 죽인다.
- 실제 사고: `tests/test_failure_domains.py:3` — 2026-09-01,
  `duplicate issue_id ... 팰리세이즈 vs 자포리자`로 **정상 수집 5회차가 '크롤 실패'로 읽혔다.**
- **영구 안정 키는 `hash`(URL 정규화 해시)뿐이다.** append-only 아카이브(29필드)에 보존된다.

### 1.6 LLM 인프라

- `gemini_client.py`. 기본 `MODEL = gemini-3.1-flash-lite`, 별도 `synthesis_model()`.
- `call_json()`은 **tools / Google Search grounding을 지원하지 않는다**
  (`gemini_client.py:388-440` — body에 `system_instruction`, `contents`, `generationConfig`만).
- 무료 티어 쿼터는 **모델별 버킷**이다. `gemini_client.py:411-414` 실측(2026-08-04): "하루
  1회짜리 호출을 상시 파이프라인과 같은 버킷에 두면 저녁마다 굶는다 — 같은 시각 프로브는
  성공하는데 브리핑 체인 끝의 호출만 3연속 429."
- RPM 페이싱 `_pace()`, `RPM_PACING_CAP_DEFAULT = 12`. **프로세스 로컬 `_CALL_LOG` 기반**이라
  별도 프로세스는 서로의 호출을 보지 못한다.
- `thinking_budget` 함정: thinking 토큰이 출력 예산을 잠식해 MAX_TOKENS로 잘린다. 길이가
  비결정적이라 "로컬에서 됐다"가 예산 충분의 근거가 못 된다. 일부 모델은 `thinkingConfig`
  자체를 거부한다(`_THINKING_CONFIG_UNSUPPORTED_MODELS`).

### 1.7 외부 수집 능력의 현재 한계

- 수집원: RSS · Naver · Google News 쿼리 · ANS 이메일 뉴스레터 · 기관 게시판 직접 크롤.
  **범용 웹 검색 API는 없다.**
- `article_body.py`는 **HTML 전용**이다. PDF 파서가 없다. `trafilatura`/`readability` 도입은
  계속 보류 중(`article_body.py:29`).
- Google News 우회: 수집 URL의 51%가 `news.google.com/rss/articles/...`이고, JS 인터스티셜을
  `data-n-a-sg`/`data-n-a-ts` + `batchexecute`로 우회한다. **이 두 속성명이 사라지면 조용히
  전부 실패한다.**
- 쿼리 생성(`adaptive_discovery.build_query()`)은 KR/EN 전제다.

### 1.8 요약 품질의 알려진 한계

`article_body.py:11` 실측: 아카이브 1,007건에서 **요약의 57%가 제목 재진술**이고, 제목에 없는
수치를 담은 요약은 **12%**뿐이었다.

**함의: 아카이브는 "언론이 무엇을 보도했는가"의 신뢰할 만한 기준선이 아니다.**

### 1.9 엔지니어링 문화 (설계가 따라야 할 규범)

- 모든 모듈 docstring이 **그 설계를 낳은 실측 사고**를 기록한다. 근거 없는 규칙이 없다.
- 실패 도메인 분리가 테스트로 고정돼 있다(`tools/failure_domains.py`,
  `tests/test_failure_domains.py`) — 웹 빌드가 죽어도 수집은 success다.
- 공통화의 경계가 명시적이다. `llm_cache.py:14-22` — "공통인 것은 **봉투와 버전 확인**이지
  판단이 아니다. 판단을 억지로 합치면 한쪽에서 맞는 규칙이 다른 쪽에서 조용히 틀린다."
- 두 곳에 같은 표를 두면 갈라진다는 실증이 있다. `story_fingerprint.py:8-22` — 축 별칭표가
  두 곳에 있었고 web 쪽이 `drivers`를 한 번도 읽지 못해, **지문만으로 붙은 쌍 11건 중 10건이
  오병합**이었다. "구체적인 축이 없을수록 점수가 높아지는" 상태였다.
- 중단을 기록하는 문화가 있다: `docs/refactor/V5_CHECKPOINT.md`의 `ABANDON history` 섹션.

---

## 2. 설계를 깨뜨리는 것 — 심각도 순

### 2.1 🔴 Evidence Unit이 저작권 계약과 정면 충돌한다 (원 설계 §16)

설계 §16은 Evidence Unit에 `relevant_passage`(원문 발췌)를 저장한다. §1.3의 계약과 정면으로
어긋난다. Deep Research를 Core와 분리해도 **저작권은 프로세스 경계를 따라 분리되지 않는다.**
그대로 구현하면 7주 된 정책 결정을 조용히 뒤집고, 사이트 공개 문구와 다른 상태가 된다.

**개선안 — Evidence 저장 계약을 3계층으로 나눈다. 지우는 것이 아니라 "누구 것이냐"로 가른다.**

| 계층 | 저장 내용 | 적용 대상 |
|---|---|---|
| **Locator** (항상) | `url`, `content_hash`, `anchor`(문단 인덱스/헤딩 경로), `accessed_at`, `publisher_date` | 전부 |
| **Extracted fact** (항상) | 원문이 아닌 구조화 사실 — `{subject, predicate, value, unit, basis, as_of}` | 전부 |
| **Verbatim quote** (조건부) | 200자 이하, 인용부호·출처 명시 | **Tier A 공문서에 한정** — 규제기관 결정문, 법령, 공시, 기업 공식 발표 |

- 게이트는 코드로 강제한다. `sources.py` / `sources.json`에 이미 tier가 있으므로
  `source_tier`로 자동 판정한다. NRC 결정문 인용은 저널리즘 관행상 정상이고, 로이터 기사 본문
  200자 저장은 현재 계약 위반이다.
- Feature 본문의 모든 문장은 재확인 가능해야 하되, **재확인은 저장된 발췌가 아니라 locator로
  원문을 다시 여는 방식**이다.
- `content_hash`를 남기면 원문이 조용히 바뀐 것도 잡힌다. 발췌 저장보다 오히려 강한 검증이다.

### 2.2 🔴 Research Object가 붙잡을 안정적 ID가 없다 (원 설계 §4)

설계 §4는 "Research Object identity는 Story identity와 별개"라고 옳게 말했지만, **Research
Object가 무엇을 가리키는지**를 정하지 않았다. §1.5가 문제다 — `issue_id`를 외래키로 잡으면
며칠 뒤 조용히 끊어지고, 그 끊어짐은 에러가 아니라 "링크된 story 0건"으로 나타나 아무도 모른다.

**개선안 — 3중 앵커:**

```yaml
research_object:
  slug: "taiwan-maanshan-restart"        # 사람이 정한 불변 키. 유일한 정본
  anchors:
    article_hashes: [...]                # 유일하게 영구 안정적인 키
    entity_ids: [...]                    # entity_registry.json 참조
    fingerprint:                         # story_fingerprint.AXES 와 같은 축
      countries: [...]
      actors: [...]
      assets: [...]
      event_family: "..."
      drivers: [...]
  observed_issue_ids: [...]              # 진단용 only. 재계산되는 값이므로 신뢰하지 않는다
```

**그리고 `story_identity.py`의 규율을 그대로 이식할 것.** 핵심 원칙 두 개 — "identity를 회원
사이에 옮기지 않는다", "충돌 시 승자를 고르지 말고 격리한다"(`resolve_local_issue_id_conflicts`
주석: 임의의 승자를 고르면 한쪽 가지에 오염이 보존된다) — 는 Research Object 병합/분할에도
그대로 필요하다. 새로 발명하지 말 것. **조사 3개월 치가 잘못된 병합 한 번으로 오염되는 것은
story 오병합보다 훨씬 비싸다.**

### 2.3 🔴 Human–AI Research Dialogue가 실행 모델과 맞지 않는다 (원 설계 §19~24)

설계 §19~24는 대화형 세션을 전제하지만 §1.1에 따라 **대화 1턴 = 워크플로 1회차**다.
LOOP 1 → Human → LOOP 2 → Human → LOOP 3은 최소 2~3일이 걸린다.

**개선안 — 실시간 세션이 아니라 비동기 우편함(mailbox)으로 재설계:**

1. **AI가 먼저 끝까지 간다.** Scout는 사람 개입 없이 완주하고, 결과로 "결정 로그 + 되돌릴 수
   있는 분기점"을 남긴다. **사람이 승인해야 다음이 도는 것이 아니라, 사람이 뒤집지 않으면
   계속 도는** 구조다. 관리자 UI의 기본 동작은 승인이 아니라 **뒤집기**다.
2. 관리자 지시는 KV의 **별도 키**(`research:directives`)에 쌓는다.
   **`admin:overrides`에 얹지 말 것** — `MAX_ENTRIES=400` FIFO 상한(§1.1) 때문에 조사 대화가
   편집 판정을 밀어낸다. `MAX_BODY_BYTES=64KB`도 자연어 지시에는 빠듯하다.
3. 조사 워크플로는 **자체 cron + 자체 concurrency group**을 갖고, 회차마다 상태 기계를 **한
   단계만** 전진시킨 뒤 커밋한다. daily-brief/crawl과 같은 파일을 절대 건드리지 않는다.
4. 정말 대화형이 필요하면 유일하게 안전한 경로는 **`repository_dispatch` 전용 토큰(워크플로
   트리거만 가능, `contents:write` 없음)** 을 엣지에 두는 것이다. §1.1의 보안 결정을 깨지
   않으면서 지연을 3시간 → 수 분으로 줄인다. **다만 별도 결정 사안으로 명시하고 들어갈 것.**

**현실적 처리량:** 1인 운영 + 턴당 수 시간 지연이면 **월 1~2건이 상한**이다. 설계 §25의
"월 10~15개 후보 → 2~4개 선택"은 Scout까지 완전 자동으로 돈다는 전제에서만 성립한다.

### 2.4 🔴 이 기능은 "AI 판단 금지" 계약을 깬다 — 의식적으로 깨야 한다

원 설계 어디에도 이 얘기가 없는데, 실제로는 가장 큰 제품 결정이다.

§1.4의 계약과 달리 Deep Research가 만들려는 것은 **정확히 해석·판단·평가**다. "이번 계약은
critical path 중 무엇을 해결했는가", "탈원전을 철회한 것인가 선택지를 복원한 것인가" — 이것은
사실 서술이 아니라 판단이다. 나쁘다는 뜻이 아니라 **다른 제품**이라는 뜻이고, 문제는 같은
브랜드·같은 사이트에서 두 계약이 공존한다는 점이다.

**개선안 — 판단 허용 범위를 계약으로 명시:**

1. **Claim 타입별 발행 규칙을 코드로 강제.** 설계 §17의 `FACT / INFERENCE / JUDGMENT`를 살리되
   문장 단위 표기 의무를 건다.
   - `FACT` → 출처 링크 필수
   - `INFERENCE` → "~로 보인다" 형태 + 근거 claim id 필수
   - `JUDGMENT` → **Nuclens 편집 판단임을 명시**하거나 제3자 인용으로만 허용
2. **금지선 유지**: 투자 권고, 특정 기업 주가·수주 전망, "실패할 것이다" 류 예측.
3. **명예훼손 인접 구간에 별도 게이트.** 실명 기업·기관에 대해 "일정을 놓쳤다", "계약에
   구속력이 없다", "언론이 놓쳤다" 류 문장은 **Tier A 1차자료 근거가 없으면 발행 불가.**
   일일 요약과 달리 특집기사는 주장하는 글이므로 리스크 프로파일이 다르다.
4. 사이트에서 특집 섹션의 성격(사실 보도 ≠ 분석)을 분리 표기.

### 2.5 🟠 git-as-DB 용량 (원 설계 §35, §36)

§1.2에 비추어, Object 하나당 evidence 20~40건 + claim + dialogue + plan 버전을 쌓으면
Object 20개에서 수백 MB가 된다. 특히 위험한 형태는 **전체를 다시 쓰는 큰 JSON 하나**다.

**개선안 — append-only 샤드 레이아웃:**

```
research_data/
  objects/<slug>/
    object.json          # 작다. 앵커·상태·현재 질문만. 자주 바뀌므로 작게 유지
    plan.ndjson          # append-only, 버전마다 한 줄
    evidence.ndjson      # append-only, content_hash 로 중복 제거
    claims.ndjson        # append-only, supersedes 체인
    decisions.ndjson     # AI 가 사람 없이 내린 선택 + 기각 이유 + 되돌리기 방법
    dialogue.ndjson      # append-only, 누가·언제·어느 회차가 소비했는지
  features/<feature_id>.json   # 발행 시점 claim 스냅샷 동결 (§4.4)
  index.json             # slug → 상태 요약. 웹 빌드가 읽는 유일한 파일
```

- **절대 rewrite 하지 않는다.** 정정은 새 줄 + `supersedes`.
- 웹 빌드는 `index.json`만 읽는다 → 빌드 시간 영향 0.
- 보존 정책을 처음부터 쓴다: 미발행 Object의 evidence는 N개월 후 locator만 남기고 축소.
- **SQLite는 권하지 않는다.** git에서 바이너리 blob이라 diff 리뷰가 죽고, 병렬 워크플로
  커밋과 병합이 안 된다. "git이 DB, 모든 상태가 리뷰 가능한 텍스트"라는 이 저장소의 문화와
  정면으로 어긋난다. 벡터 DB는 이 규모에서 불필요하다 — 필요하면 기존 `embeddings.json`
  캐시 방식으로 충분하다.

### 2.6 🟠 "새로운 정보"의 기준선이 없다 — §30이 거짓말을 만든다 (원 설계 §30)

§1.8에 따라 "언론이 안 다뤘다"를 아카이브 부재로 판정하면 **거의 항상 거짓 양성**이다.
실제로는 로이터가 3번째 문단에 썼는데 우리 요약에 안 남았을 뿐이다.

**개선안 — 부재의 범위를 데이터 모델에 박아 넣는다:**

```json
"absence_scope": "nuclens_archive_summary"   // 우리 요약에 없다 (가장 약함)
                | "reviewed_sources"          // 이번 조사에서 읽은 N개 문서에 없다
                | "searched_web"              // 검색으로 못 찾았다
```

그리고 **문구를 스코프에 종속**시킨다. `nuclens_archive_summary`면 "언론이 놓쳤다"는 문장을
쓸 수 없게 코드에서 막는다. 설계 §11의 Absence Verification Gate를 실제로 집행 가능하게 만드는
유일한 방법이다.

### 2.7 🟠 검색 인프라가 없고, grounding에는 함정이 있다 (원 설계 §12~14, §47-14)

§1.7에 따라 Deep Research가 요구하는 웹 검색은 **새 코드 경로**다. 구현 시 반드시 마주칠 것:

1. **Grounding 인용 URL은 원본 주소가 아니다.** Gemini Google Search grounding이 돌려주는 것은
   `vertexaisearch.cloud.google.com/grounding-api-redirect/...` 형태의 프록시이고 **만료된다.**
   그대로 Evidence에 저장하면 몇 주 뒤 전부 죽은 링크가 된다. → 반드시 리다이렉트를 따라
   **canonical URL로 해소한 뒤** 저장하고 `content_hash`를 함께 남길 것.
2. **1차자료의 대부분은 PDF다.** 규제기관 결정문, DOE 보고서, 투자자 자료. `article_body.py`는
   HTML 전용이고 파서 도입은 계속 보류돼 왔다. **"Tier A 중심"이라는 설계의 야심은 PDF
   파이프라인 없이는 실현되지 않는다.** 이것이 Prototype B의 실제 난이도다.
3. **다국어.** 설계의 두 예시가 이미 이 문제를 드러낸다. 마안산 1차자료는 中文이다 — 台電,
   核安會(구 原能會), 立法院 공보. `馬鞍山核能發電廠`으로 검색하지 않으면 Tier A는 한 건도
   안 나온다. 프랑스(ASN/ASNR), 일본(規制委)도 같다. **쿼리 생성이 다국어여야 하고, 대상
   기관별 공식 명칭 사전이 필요하다.**
4. **폴라이트 페치.** 규제기관 사이트를 건당 30문서씩 긁으면 차단된다. 기존 `article_body`의
   UA·타임아웃·조용한 실패 규율을 재사용하고, 도메인별 레이트 리밋과 결과 캐시
   (`llm_cache.py` 봉투)를 붙일 것.

### 2.8 🟠 백테스트에 시간 누출(temporal leakage)이 있다 (원 설계 §41)

살아 있는 검색엔진으로 과거 사건을 조사하면 **결과를 이미 아는 상태로 조사한다.** "2024년
시점에서 이 질문을 잘 설계했는가"가 아니라 "2026년의 답을 2024년 언어로 되쓸 수 있는가"를 재게
된다. 모든 지표가 훌륭하게 나오고 실전에서만 안 된다.

**개선안:**

- 백테스트 실행 시 `as_of` 날짜를 강제 주입하고, **`publisher_date > as_of`인 source를 검색
  후 전량 폐기**한다(검색 시점 필터만으로는 샌다).
- 평가 기준을 "결론이 맞았나"가 아니라 **"그 시점에서 옳은 질문과 옳은 unknown을 지목했나"**로
  잡는다. 그것이 이 시스템의 진짜 능력이다.
- 아카이브가 2026-07부터라 재료가 얇다(568건). 초기 백테스트는 아카이브 밖 사건으로 해야 하고,
  그건 곧 위 필터 없이는 전부 누출된다는 뜻이다.

### 2.9 🟡 쿼터 격리는 선택이 아니라 필수다 (원 설계 §37)

§1.6에 따라 RPM 페이싱은 프로세스 로컬이므로 **격리를 보장하지 못하고, 충돌은 API 레벨에서만
일어난다.** → **별도 프로젝트의 별도 키가 유일한 실질적 격리 수단이다.** 같은 프로젝트의 다른
키로는 안 된다(프로젝트 단위 쿼터).

추가: `call_stats()`의 label 집계(`gemini_client.py:215`)를 그대로 써서 `research:*` 프리픽스로
예산을 강제할 것. 이미 있는 계측이다.

---

## 3. 이미 있는 것을 다시 만들지 마라

두 곳에 같은 표를 두면 갈라진다는 실증이 이 저장소에 있다(§1.9의 `story_fingerprint` 사고).
아래를 무시하면 그 사고가 반복된다.

| 원 설계 항목 | 이미 있는 것 | 처리 |
|---|---|---|
| §5 Story Archetype (14종) | `story_fingerprint.AXES` — countries/actors/assets/event/action/cause + 가중치 | Archetype을 새 분류로 만들지 말고 **fingerprint 축의 조합으로 유도**. 축 표는 한 곳에만 |
| §14 Source Hierarchy (Tier A~E) | `sources.json` tier 1/2/3 + `source_type`(official/specialist_media/general_media/press_release) + `evidence_role` | **재사용·확장.** 새 5단계를 병렬로 두면 반드시 갈라진다 |
| §13 순환 인용·중복 출처 탐지 | `evidence_role: distributed_claim` 이 **이미** "같은 보도자료의 재배포"를 표시한다 | 절반은 이미 풀려 있다. `content_hash` 유사도만 얹으면 된다 |
| §10 Expectation Ledger | `event_schedule.json` + `event_sources.py`(공식 일정 직수집, LLM 0회) + `event_date`/`_type`/`_precision`/`_source` 4필드 | **확장.** "약속된 일정"의 절반은 이미 수집 중 |
| §8·§25 State Change / Trigger 탐지 | `event_stage.py`(심사·승인·정지·재가동 단계 판정 + 거부권), `issue_continuity.py`(전일 대비 단계 이동), `build_data.latest_change_line()`·`change_line_for_card()` | Trigger 탐지를 새로 쓰지 말고 **이 셋 위에 얹는다** |
| §21 Question Tree 씨앗 | `issue_rows[].open_question` + `collect_open_questions()`(`web/build_data.py:4252`) | 이미 생산 중인 Research Gap. 자동 유입시킬 것 |
| §12 Scout 쿼리 관리 | `adaptive_discovery.py` + `discovery_state.json` — 쿼리별 성과·냉각·TTL·예산, LLM 0회 | Scout 검색 인프라가 사실상 이미 있다 |
| LLM 캐시 | `llm_cache.py`(봉투·버전 확인만 공통, 무효화는 모듈별) | 그대로 사용. 무효화 규칙만 조사용으로 따로 |
| §36 실패 도메인 분리 | `tools/failure_domains.py` + `tests/test_failure_domains.py` | Research 도메인을 **4번째 축으로 등록** |
| §34 상태 기계의 원자성 | outbox 패턴(claim → 실행 → confirm), `outbox.json` | 조사 회차 상태 기계에 그대로 적용 |

---

## 4. 핵심 개선안

### 4.1 ★ Yield Gate를 "느낌"에서 3개의 기계적 탐지기로 축소하라 (원 설계 §27, §30, §31)

**이것이 가장 중요한 제안이다.**

설계 §27은 "기존 보도보다 새로 알아낸 것이 있는가"를 묻는다. 이는 **LLM에게 자기 성과를 자기가
평가하라고 시키는 것**이고, LLM은 언제나 "네, 새롭습니다"라고 답한다. 게이트가 게이트로
작동하지 않는다.

동시에 설계안의 가장 취약한 가정(원 §47-1의 답)은 이것이다: **"깊이 조사하면 기존 보도보다
새로운 것이 나온다."** 원자력 정책 도메인에서는 대체로 **틀리다.** WNN·NucNet·NEI·전기신문이
이미 다 쓴다. 검색으로 찾을 수 있는 것은 이미 기사화돼 있다.

그런데 **일관되게 비어 있는 세 자리가 있다.** 저널리즘이 구조적으로 하지 않는 일이다.

1. **시계열 대조** — 2021년에 "2024년 가동", 2023년에 "2026년", 지금은 "2030년". 각 보도는
   그때의 최신 일정만 인용한다. 아무도 겹쳐 보지 않는다.
2. **원문 대 보도의 차이** — 계약서·결정문·공시에 있는 조건(binding 여부, 조건부 조항, 범위
   한정, 유효기간)이 보도에서 사라진다.
3. **비교** — 같은 기준으로 2~3개를 나란히 놓는 표. 개별 보도는 절대 하지 않는다.

**개선안: Yield Gate = 아래 3개 탐지기 중 최소 1개가 정량 임계를 넘을 때만 통과.**

```
D1. Schedule Drift Detector
    입력: Expectation Ledger
    통과: 동일 마일스톤에 대해 서로 다른 시점의 공식 약속이 2건 이상 존재하고,
          최초 약속 대비 현재 목표의 차이가 N개월 이상
    산출: 자동 생성 가능한 표 (약속 시점 / 약속된 날짜 / 출처 / 현재 상태)

D2. Document–Coverage Gap Detector
    입력: Tier A 문서 1건 이상 + 해당 사건의 아카이브·조사 대상 보도 전체
    통과: Tier A 문서에 존재하는 실질 조건(금액·조건절·범위·유효기간·전제)이
          검토한 보도 N건 어디에도 등장하지 않음
    산출: absence_scope = reviewed_sources 로 정직하게 라벨된 항목 (§2.6)

D3. Comparator Table Completeness
    입력: 비교 목적 + 비교 대상 2개 + 비교 항목 5개 이상
    통과: 셀의 70% 이상이 Tier A/B 근거로 채워짐
          (UNKNOWN 도 정직한 셀로 인정하되, UNKNOWN 이 과반이면 미통과)
```

효과:

- Yield Gate가 **판정 가능**해진다 — LLM의 자기평가가 아니라 데이터 조건이다.
- 조사 Blueprint가 저절로 초점을 얻는다. 무엇을 찾아야 하는지가 명확하다.
- §30 "What the News Missed"가 D2로 환원되어 **범위가 정직하게 제한**된다.
- §10 Expectation Ledger가 "있으면 좋은 것"에서 **게이트의 필수 재료**로 승격된다. 이것이
  원 설계에서 가장 저평가된 부분이다. 실제로는 Expectation Ledger 하나가 이 제품의 유일한
  진짜 해자(moat)일 가능성이 높다 — **시간이 지날수록 축적되고, 남이 복제하기 어렵고,
  자동으로 delta를 만들어낸다.**
- D1은 **Feature 없이도 단독 제품 가치가 있다.** 이슈 페이지에 "이 프로젝트의 일정 약속 이력"
  블록으로 먼저 내보내면 Deep Research 전체를 짓기 전에 가정을 검증할 수 있다. (§0의 권고)

### 4.2 ★ Prototype 순서를 뒤집어라 (원 설계 §40)

원 설계는 A(Research Planner) → B(Retrieval) → … 순이다. **반대로 해야 한다.**

- Research Planner는 **가장 검증하기 어렵고**(좋은 계획인지 판정할 기준이 없다) **사람이
  대체하기 가장 쉽다**(YAML 한 장 손으로 쓰면 된다).
- Retrieval / Evidence / 저장 계약은 **가장 어렵고**(PDF, 다국어, 저작권, 링크 만료, 중복)
  **모든 후속 단계가 여기 의존한다.**
- Planner를 자동화하려면 비교할 gold plan이 있어야 하는데, 그건 손으로 2~3개 써본 뒤에만 생긴다.

**개선된 순서:**

```
P0  Expectation Ledger 단독 (LLM 최소, 기존 event_schedule/archive 위)
    → 이슈 페이지에 "일정 약속 이력" 블록. 가치 가설을 2주 만에 검증.

P1  Evidence + Claim Ledger, 대상 1개 (plan.yaml 은 손으로 작성)
    → PDF·다국어·저작권 3계층·링크 해소·content_hash·중복 제거를 전부 여기서 해결.
    → 산출물은 관리자가 읽는 "조사 노트" 1장. 아직 기사 아님.

P2  3개 Detector (D1/D2/D3) + Yield Gate
    → 대상 2개에서 "이 사건은 발행 가치 없음" 이 실제로 나오는지 확인.
       한 번도 NO 가 안 나오면 게이트가 고장난 것이다.

P3  Research Planner 자동화 (P1 에서 손으로 쓴 plan 2~3개를 gold 로)

P4  Feature 생성 + Critic

P5  비동기 Dialogue (mailbox)   ← 마지막. 그전까지는 관리자가 plan.yaml 을 직접 편집
```

P5를 마지막에 두는 것이 중요하다. Dialogue는 **가장 비싸고**(§2.3의 인프라) **자동화가 잘 되면
필요 없어질 수도 있는** 기능이다. 먼저 지으면 그걸 정당화하려고 불필요한 개입 지점을 만들게 된다.

### 4.3 ★ AI가 사람에게 묻는 조건을 결정론적 규칙으로 (원 설계 §23)

설계 §23의 5개 범주는 LLM 재량이다. LLM은 불확실하면 묻고, 즉 **항상 묻는다.** 그러면 원 §47-4가
우려한 대로 사람이 병목이 된다.

**개선안 — 묻는 조건을 4개로 못박고 나머지는 "진행 + 결정 로그":**

```
ASK 조건 (이것만):
  1. Lens 후보 2개 이상이 Scout 근거량 차이 20% 이내     → 편집 선택
  2. Tier A 문서 2건이 직접 모순                          → 사실 충돌
  3. 다음 단계 예상 비용이 남은 예산의 40% 초과           → 예산 승인
  4. 발행 시 실명 주체에 대한 부정적 JUDGMENT 포함        → 법적·편집 책임 (§2.4)

그 외 전부: 진행한다. 단 decisions.ndjson 에
  {선택, 대안, 기각 이유, 되돌리기 방법} 을 남긴다.
```

관리자 UI의 기본 동작은 승인이 아니라 **뒤집기**다. 아무것도 하지 않으면 조사는 계속된다.
이것이 §24 Research Room의 실제 UX여야 한다 — 결재함이 아니라 **감시 로그 + 개입 버튼.**

### 4.4 Claim 버전 관리와 Feature 동결 (원 설계 §47-11의 답)

```yaml
claim:
  id: "claim-...."
  text: "..."
  type: FACT | INFERENCE | JUDGMENT
  confidence: ...
  status: CONFIRMED | PARTIAL | CONFLICTING | NOT_PUBLIC | NOT_FOUND | UNKNOWN
  valid_as_of: "2026-09-06"
  supersedes: "claim-...."     # 정정 체인
  superseded_by: null
```

- **Feature는 발행 시점에 자신이 쓴 claim id + version을 동결 스냅샷으로 저장한다**
  (`research_data/features/<id>.json`). 나중에 claim이 뒤집혀도 발행분은 그대로 남고, 대신
  **"이 특집의 근거 3개 중 1개가 이후 정정됨"** 알림이 뜬다. 정정 기사를 낼지 판단하는 재료다.
- `status: UNKNOWN` 인 claim은 **자동으로 Watchpoint가 되어** 관련 기사가 들어오면 재개된다.
  이것이 §35 Research Memory가 실제로 복리로 쌓이는 유일한 메커니즘이다.

### 4.5 수치와 단위 — 설계에 완전히 빠져 있다

"대만 원전 발전비중의 역사적 변화"를 조사하면 나오는 숫자들: 발전량 기준 vs 설비용량 기준,
gross vs net, MWe vs MWt, 회계연도 vs 역년, 台電 발표 vs IAEA PRIS vs 통계연감 — 전부 다르다.
정규화 없이 Evidence에 넣으면 **자신 있게 틀린 그래프**가 나오고, 그것이 이 제품에서 가장
치명적인 실패 방식이다.

```json
"quantity": {
  "value": 16.9,
  "unit": "percent",
  "basis": "generation_share_net",
  "period": {"kind": "calendar_year", "value": 2015},
  "source_org": "TaiPower",
  "note": "IAEA PRIS 는 같은 해를 16.3 으로 적는다"
}
```

**Feature에 들어가는 모든 수치는 같은 `basis`끼리만 시계열로 묶을 수 있게 코드로 막는다.**
사소해 보이지만 "AI가 쓴 그럴듯한 글"과 "검증 가능한 연구"를 가르는 실질적 경계선이다.

### 4.6 평가지표 13개 → 4개 + 루브릭 1개 + 중단 기준 (원 설계 §42)

§42의 13개 중 대부분은 측정할 수 없다("Editorial Value", "Question Improvement"를 무엇으로
재는가). 측정 못 하는 지표는 무시되고, 무시되는 지표는 게이트를 무력화한다.

| 지표 | 계산 |
|---|---|
| Primary Source Coverage | Tier A/B 근거를 가진 FACT claim 비율 |
| Unsupported Claim Count | 근거 0개인 문장 수 — **목표 0, 아니면 발행 불가** |
| Detector Yield | D1/D2/D3 중 통과한 개수 |
| Human Edit Distance | 발행본 vs AI 초안의 문자 수준 diff 비율 |

**사람이 매기는 것 1개:** 발행 건마다 5점 루브릭 한 줄 — "이 특집이 없었다면 나는 이 사실을
몰랐을 것이다"(1~5).

**중단 기준을 처음부터 쓴다**(이 저장소는 `V5_CHECKPOINT.md`에 `ABANDON history` 섹션을 두는
문화가 있다):

> Feature 5건 발행 후 — Human Edit Distance 중앙값이 40%를 넘거나 루브릭 평균이 3.5 미만이면
> 중단하고 P0(Expectation Ledger)만 남긴다.

---

## 5. 설계에 아예 없는 것

1. **링크 부패(link rot)와 아카이빙.** 조사 근거의 상당수는 3년 뒤 죽는다. 규제기관은 사이트
   개편 때 URL을 통째로 갈아엎는다. → `content_hash` + `accessed_at`은 최소한이고, Tier A
   문서는 **Wayback Machine save API**로 스냅샷을 남기고 그 URL을 함께 보관할 것. 저작권 문제
   없고 비용 0이다.
2. **커밋 경쟁과 재시작 멱등성.** §34 상태 기계가 Actions에서 돌면 preempt·타임아웃·429가
   일상이고, 같은 저장소에 push하므로 rebase 충돌도 난다. → 조사 워크플로는 **`research_data/`
   밖의 파일을 단 하나도 쓰지 않고**, 단계별로 claim → 실행 → confirm(기존 outbox 패턴)을
   적용해 **그 단계만** 재시도되게 한다.
3. **사람 노동 예산.** §42에 "Human Effort"가 지표로는 있는데 **예산으로는 없다.** → Feature
   1건당 관리자 개입 상한을 명시할 것(예: 총 30분, 개입 3회). 넘으면 자동화 실패이지 사람이
   부지런해야 할 일이 아니다.
4. **Object 병합·분할 도구.** 조사가 쌓인 뒤 "Taiwan nuclear policy"와 "Maanshan restart"가
   실은 하나였다/둘이었다가 반드시 발생한다. §2.2의 규율을 적용한 명시적 도구가 필요하다.
   사후에 붙이면 늦다.
5. **개인정보.** 조사 대상에 실명 인물(정치인·CEO)이 들어온다. `tools/redact_emails.py`가
   있는 것을 보면 이 저장소는 이미 한 번 데였다. Evidence에 개인 연락처·비공개 신원이 들어가지
   않게 하는 필터가 필요하다.
6. **Feature의 갱신 정책.** 발행 후 사건이 진행되면 새 Feature인가, 수정인가, 추가인가.
   정하지 않으면 3개월 뒤 같은 Object에 대해 서로 모순되는 특집 3개가 사이트에 공존한다.
7. **조사 대상 사전 필터 `source_availability`.** §6의 소송·비공개 반례 때문에 필요하다.
   1차자료 접근 가능성을 후보 선정 단계에서 점수화하지 않으면, 가장 중요한 주제에 가장 많은
   예산을 쓰고 UNKNOWN만 얻는다.
8. **국제·다국어 기관 사전.** §2.7-3. 기관별 공식 명칭·문서 유형·게시판 구조를 담은 사전이
   Tier A 접근의 전제 조건이다.
9. **Feature 자체의 품질 회귀 테스트.** 이 저장소의 문화상, 발행 계약(근거 0인 문장 없음,
   absence_scope 문구 대응, basis 혼합 금지)은 테스트로 고정돼야 한다.

---

## 6. 설계를 깨뜨릴 반례 주제 (원 설계 §47-20의 답)

설계는 대만(정책)과 TerraPower(프로젝트) 둘로 검증됐다. 아래는 그 구조가 잘 작동하지 않을 것들이다.

| 반례 | 무엇이 깨지나 |
|---|---|
| **HALEU 공급망** | Research Object가 국가도 기업도 프로젝트도 아닌 **횡단 병목**. Backward chaining이 발산한다(러시아 제재 → 우크라이나 전쟁 → …). §9의 종료 조건이 작동하지 않음 |
| **진행 중인 안전 사건·사고** | 조사 중 사실이 계속 바뀐다. `valid_as_of`가 시간 단위로 무의미해지고, "완결된 Feature"라는 산출물 형태 자체가 부적절 |
| **소송·중재 (한수원–웨스팅하우스 유형)** | 1차자료가 **비공개**다. Gap이 전부 D2(구조적 비공개)로 떨어져 산출이 UNKNOWN 뿐. 게이트는 정직하게 NO FEATURE를 내지만, **그럼 이 시스템은 가장 중요한 주제에서 아무것도 못 만든다**는 뜻이다 → §5-7의 사전 필터 필요 |
| **국내 정치 결부 이슈 (원전 정책 국회 공방)** | Lens 선택이 곧 정치적 입장이 된다. §23의 "Editorial Choice"가 실제로는 편집 방침 문제이지 조사 문제가 아님 — 자동화 대상이 아님을 인정해야 한다 |
| **한 회사의 재무·수주 전망** | §2.4의 금지선과 정면 충돌 |
| **표준설계인가·형식승인처럼 문서는 공개인데 수천 페이지인 것** | 검색이 아니라 문서 내 탐색 문제. 현재 설계에 문서 내부 인덱싱 개념이 없다 |

---

## 7. 수정된 전체 구조

원 설계 §46을 위 개선안에 맞춰 다시 그린 것.

```
Daily Nuclens Stories  (읽기 전용 소비)
        ↓
Trigger 탐지  ← event_stage / issue_continuity / latest_change_line 재사용
        ↓
Feature Candidate Pool  + source_availability 사전 점수
        ↓
관리자 주제 선택  (월 1~2건이 현실적 상한)
        ↓
Research Object  (slug + article_hash + entity + fingerprint 3중 앵커)
        ↓
Provisional Question / Lens
        ↓
Adaptive Research Planner        ← P3. 그전까지는 plan.yaml 손으로 작성
        ↓
Scout Research  (다국어 쿼리 · Tier A 우선 · PDF 포함)
        ↓
Evidence Ledger  (locator + extracted fact + 조건부 인용 · content_hash)
        ↓
Claim Ledger  (FACT/INFERENCE/JUDGMENT · supersedes · valid_as_of)
        ↓
Expectation Ledger / Comparator / Quantity 정규화
        ↓
┌───────────── Yield Gate = D1 or D2 or D3 ─────────────┐
│  D1 Schedule Drift   D2 Doc–Coverage Gap   D3 Comparator │
└──────────────────────────────────────────────────────────┘
        ↓ (통과 못하면 NO FEATURE — Research Memory 만 갱신하고 종료)
Premium Reasoning / Counter-analysis / Critic
        ↓
Narrative Completeness Gate
        ↓
발행 게이트: 근거 0 문장 = 0 · absence_scope 문구 대응 · basis 혼합 없음
             · 실명 부정 JUDGMENT 는 Tier A 근거 필수
        ↓
Human Final Review  (기본은 승인이 아니라 뒤집기)
        ↓
Publish  + claim 스냅샷 동결
        ↓
Research Memory Update  (UNKNOWN → Watchpoint 자동 등록)

        ↕  전 구간에서: ASK 조건 4개에 해당할 때만 사람에게 묻는다.
           그 외는 진행하고 decisions.ndjson 에 기각 이유와 되돌리기 방법을 남긴다.
           대화는 KV(research:directives) 우편함 — 1턴 = 워크플로 1회차.
```

---

## 8. 무엇을 바꿔야 하는가 — 정리

**설계에서 그대로 살릴 것:**
Story Arc는 결과물이 아니라 재료 / 질문의 진화 / Evidence–Claim 분리 / UNKNOWN 정상 인정 /
Core와 실패 도메인 분리 / Research Memory가 장기 자산 / 자동 발행 금지 / Discovery와
Verification 검색의 분리

**반드시 바꿀 것:**

| # | 바꿀 것 | 이유 | 절 |
|---|---|---|---|
| 1 | Evidence의 원문 발췌 → locator + 구조화 사실 + Tier A 한정 인용 | 저작권 계약(2026-07-31)과 회귀 테스트 존재 | §2.1 |
| 2 | Research Object 앵커 → slug + article_hash + entity + fingerprint | `issue_id`는 하루 12번 재계산되고 격리 시 바뀜 | §2.2 |
| 3 | Dialogue → 비동기 mailbox, 기본 진행 + 뒤집기 | KV 버퍼 / 워크플로 1회차 = 1턴 | §2.3 |
| 4 | Yield Gate → D1/D2/D3 기계적 탐지기 | LLM 자기평가는 게이트가 아니다 | §4.1 |
| 5 | Prototype 순서 → Ledger·Evidence 먼저, Planner 나중 | Planner는 검증 불가·대체 쉬움, Retrieval은 그 반대 | §4.2 |
| 6 | 저장 → append-only NDJSON 샤드 + index.json | .git 254MB, 웹 빌드 이미 6~19분 | §2.5 |
| 7 | Source Tier / Archetype → 기존 `sources.json`·`story_fingerprint.AXES` 확장 | 두 벌이 되면 반드시 갈라진다(선례 있음) | §3 |
| 8 | 지표 13개 → 4개 + 루브릭 + 중단 기준 | 측정 못 하는 지표는 게이트를 무력화 | §4.6 |

**설계에 추가할 것:**
"AI 판단 금지" 계약의 명시적 예외 정책(§2.4) / PDF·다국어 1차자료 파이프라인(§2.7) /
백테스트 시간 누출 차단(§2.8) / 수치 basis 타입(§4.5) / Claim 버전·Feature 동결(§4.4) /
링크 아카이빙 / Object 병합·분할 도구 / 사람 노동 예산 / `source_availability` 사전 필터

---

## 9. 다음 검토자를 위한 질문

원 설계 §47을 대체·보강한 것. 위 개선안을 전제로 다시 물어야 할 것들이다.

1. D1/D2/D3 세 탐지기로 Yield Gate를 환원한 것이 지나친 축소인가? 네 번째로 필요한 탐지기가
   있는가? (후보: "공식 자료 간 수치 불일치 탐지")
2. D1(Schedule Drift)을 Deep Research 없이 단독 기능으로 먼저 내는 것이 정말 가장 싼 가설
   검증인가? 더 싼 것이 있는가?
3. Evidence 3계층(locator / extracted fact / 조건부 인용)에서 `extracted_fact`의 스키마를
   도메인 무관하게 설계할 수 있는가, 아니면 사건 유형별로 달라야 하는가?
4. Research Object의 3중 앵커가 실제로 rot을 막는가? 앵커 자체가 낡았음을 탐지하는 방법은?
5. 비동기 mailbox 대화에서 관리자 지시가 (지연 때문에) 이미 지나간 조사 단계를 가리킬 때
   어떻게 처리하는가?
6. ASK 조건 4개는 충분한가? 실제로 물어야 했는데 안 물어서 사고가 날 조건은 무엇인가?
7. 실명 주체에 대한 부정적 JUDGMENT의 "Tier A 근거 필수" 규칙을 코드로 판정할 수 있는가?
   판정 불가라면 사람 게이트로 남겨야 하는가?
8. PDF 1차자료 파이프라인을 `trafilatura` 없이(현 정책 유지) 어떻게 만드는가? Gemini file
   input으로 대체 가능한가, 아니면 이 정책을 재검토해야 하는가?
9. 다국어 1차자료 접근에서 기관 사전을 손으로 유지하는 것과 LLM에게 매번 물어보는 것 중
   어느 쪽이 실제로 싼가?
10. 백테스트 시간 누출 차단(`publisher_date > as_of` 전량 폐기)이 실제로 충분한가?
    LLM 파라미터 지식으로 새는 것은 어떻게 막는가?
11. Claim의 `supersedes` 체인과 Feature 동결 스냅샷이 있을 때, "정정 기사를 낼 것인가"를
    판단하는 임계는 무엇인가?
12. Object 20개 · 24개월 운영 시점에서 `research_data/`의 예상 크기와 웹 빌드 영향은?
13. 소송·비공개 주제에서 이 시스템이 아무것도 못 만든다는 것을 받아들일 것인가, 아니면
    "무엇이 비공개인지를 정리하는 것" 자체를 산출물로 인정할 것인가?
14. 월 1~2건이라는 처리량이 이 기능을 지을 근거로 충분한가? 그렇지 않다면 무엇을 자동화해야
    5~10건이 되는가?
15. `repository_dispatch` 전용 토큰을 엣지에 두는 것은 현재 보안 결정의 정신을 지키는가,
    깨는가?
