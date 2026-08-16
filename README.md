# 원자력 뉴스봇 (nuclear-news)

해외 전문지 RSS·한국 기관 보도자료·Google News·ANS 이메일 뉴스레터에서 원자력 뉴스를
3시간마다 수집하고, 매일 아침 텔레그램으로 **국내/해외 투자 관점 카드 브리핑**을 보낸다.
금요일엔 주간 판세 리포트.

## 파이프라인

```
crawl (3시간마다)       news_bot.py    RSS·Naver·이메일 수집 → dedup → Gemini batch
                                       큐레이션(+랭킹 feature) → digest_queue.json 적재
daily-brief (07:25 KST) news_bot.py 로 직전 1회 수집 → daily_brief.py --plan/--send/--confirm
                                       story dedup → 랭킹(ranking.py) → 카드 브리핑 발송
                                       → Nuclens 데이터 빌드 → 빠른/전문가 오디오 → Pages 배포
weekly (금 17:00 KST)   weekly_bot.py  주간 판세 (정책 변화·테마 강약·watchlist)
```

## 파일 구조

| 파일 | 역할 |
|---|---|
| `news_bot.py` | 수집·dedup·batch 큐레이션 (Gemini 1회/10건, feature 추출 포함) |
| `data_quality.py` | URL·발행처·출처 역할·완결문·사건일 공통 품질 계약 |
| `embedding_pipeline.py` | Gemini 임베딩 모델·35일 캐시·최근 21일 브리핑 백필 계약 |
| `news_archive.py` | v2 아카이브 적재·중복 차단·품질 이관 |
| `archive_repairs.json` | 과거 깨진 레코드의 고정 회귀 수선·제외 근거 |
| `daily_brief.py` | 일일 브리핑: story dedup→랭킹→투자 관점→보고서 추천→발송/웹 story 계약 기록 |
| `weekly_bot.py` | 주간 판세 리포트 (Gemini 주 1회 1호출) |
| `ranking.py` + `ranking_config.json` | 설명 가능한 점수식 — **가중치는 JSON 만 편집** |
| `metrics.py` | 오프라인 품질 지표 (`python metrics.py`) — 표본 부족 시 insufficient_data |
| `gemini_client.py` | Gemini REST wrapper (429 백오프) |
| `telegram_send.py` | 텔레그램 발송 (inline keyboard 지원) |
| `sources.py` + `sources.json` | 출처 공신력 tier — JSON 만 편집 |
| `email_ingest.py` | ANS Nuclear News Daily 뉴스레터 외부 링크 추출 (IMAP) |
| `reports_kb.json.example` | 과거 보고서 KB 템플릿 — 채우면 보고서 추천 정밀화 |
| `keywords.json` | Naver 검색 키워드 — JSON 만 편집 |
| `dedup.py` + `story_cluster.py` | 동일 briefing story를 제목·본문요약·fingerprint로 병합하고 보도매체/근거를 보존 |
| `event_stage.py` | 사건 단계(심사·승인·정지·재가동…) 판정 — **단계가 다르면 중복 처리 금지** 거부권 |
| `audio_brief.py` | Nuclens 빠른 브리핑: 1인 라디오형 약 3분, 900자 TTS 청크/무음·음량 보정 |
| `expert_audio_brief.py` | Nuclens 전문가 브리핑: dossier→시간배분→episode plan→1인 전문가 대본→검증/수정→TTS. 길이는 그날 재료가 정한다 (기사 적으면 짧게, 많으면 10분 초과) |
| `web/build_data.py` + `web/public/` | story 계약을 issue/timeline으로 연결하고 7·30·90·180·365일 흐름과 두 오디오를 Cloudflare Pages에 제공 |
| `functions/admin/_middleware.js` | 운영 콘솔(`/admin`) 엣지 접근 통제 — 비밀번호(KV)·서명 세션·시도 제한. KV 미연결이면 **잠근다** |
| `scorer.py` `synthesize.py` `send_research.py` | 소셜(last30days) 경로 — 수동 실행 전용 |

## 상태 파일 (git 이 DB)

| 파일 | 내용 |
|---|---|
| `sent.json` | 수집 dedup (URL hash, 14일 보존) |
| `curated.json` | 큐레이션 캐시 (14일) — weekly 의 입력 |
| `digest_queue.json` | 발송 대기 큐 (발송분만 hash 단위 제거, 3일 자동 정리). 각 항목의 `raw_sources` 는 수집 단계에서 접힌 기사 — 삭제하지 않고 대표에 매단 근거 |
| `outbox.json` | 오늘의 발송 계획·상태 (pending/sent/failed) — 중복 발송 방지 핵심 |
| `delivery_log.jsonl` | 발송 이력 + 점수 내역 + 모든 선정 story의 fingerprint/보도매체/병합 근거 — 뉴스와 Nuclens의 공통 계약 |


## Nuclens 웹·오디오 (2차 통합)

`https://nuclens-v2.pages.dev/`는 Daily Brief가 확정된 뒤 같은 선정 결과를 사용한다.

- **기사(article) → briefing story → 장기 issue**의 3계층을 구분한다. 같은 날 같은 사건을
  여러 매체가 보도하면 story 하나로 합치고, 며칠 뒤 새 승인·재가동 같은 후속 action은
  장기 issue 타임라인으로 연결한다.
- 화면에 한 카드가 서지만 그 카드는 **story 가 완성된 뒤에** 고른 대표다. 수집 단계가 미리
  고르지 않는다 (아래 "사건 중심 파이프라인" 참조).
- 웹 issue 연결도 Daily Brief의 `story_fingerprint`를 보조 증거로 사용하므로 뉴스 선정과
  사이트의 사건 정의가 서로 다른 규칙으로 움직이지 않는다.
- 트렌드는 원문 기사 수가 아니라 **선정된 briefing story 수**를 센다. 7일·30일·분기(90일)·
  반기(180일)·1년(365일)을 선택할 수 있고, archive가 아직 요청기간만큼 쌓이지 않았으면
  실제 축적 일수를 명시한다.
- 오디오는 `audio/audio.json` v2의 `variants.fast` / `variants.expert` 두 형태로 배포한다.
  빠른 브리핑 실패와 전문가 브리핑 실패는 서로 독립적이라 하나가 실패해도 다른 음원과
  웹 배포는 계속된다.
- 장기 archive는 계속 Git에 누적하지만 Pages로는 경량 기간 집계만 내보내므로 1년 타임라인이
  생겨도 브라우저가 전체 원본 archive를 다운로드하지 않는다.

## 사건 중심 파이프라인 (2026-08-16)

"V1 위에 story 기능을 덧붙인 V2"에서 "처음부터 사건 중심으로 움직이는 V2"로 옮긴
변경이다. 뿌리는 두 가지 구조적 약점이고(①②), 나머지는 그것을 고쳤을 때 비로소
가능해지는 것들이다(③④⑤).

**① 수집 단계는 이제 중복을 지우지 않는다.**
`news_bot` 의 URL·제목·퍼지·임베딩 dedup 은 진 쪽을 삭제하는 대신 대표 기사의
`raw_sources` 로 매단다. 예전에는 story 가 만들어지기도 전에 근거가 사라져서,
열 매체가 보도한 사건도 화면에서는 매체 1곳으로 보였다. 이제 그 근거가 큐 →
랭킹 → story 병합 → `delivery_log` 까지 그대로 흐른다.

**② 사건 단계가 다르면 중복으로 접지 않는다.** (`event_stage.py`)
V1 에서 가져온 빠른 제목 중복 알고리즘에는 단계 개념이 없어서
`심사 착수 → 최종 승인`, `가동 중단 → 재가동` 같은 상태 전환이 AI story 판정 전에
접혀 사라졌다 — 하필 가장 중요한 뉴스가. 이제 `ranking.cluster_duplicates()`,
수집 단계 퍼지·임베딩 dedup, 그리고 LLM 이 "단순 재전재"라고 판정한 조합에
거부권이 선다. **양쪽 모두 단계를 말했고 겹치는 단계가 하나도 없을 때만** 발동하므로,
한쪽이라도 단계 표식이 없으면 예전과 똑같이 동작한다.

**③ 화면용 대표는 story 완성 뒤에 고른다.**
`ranking.rank_and_select()` 가 모든 dedup 단계를 마친 뒤 story 별로 대표를 다시
고른다 — 본문 유무 → 출처 등급 → 근거 역할 → 랭킹 점수 순. 동점이면 유지하고,
점수 차가 3.0 을 넘으면 바꾸지 않는다(품질이 중요도를 뒤집지 않게).

**④ 그래서 `story_outlet_count` 가 실제 복수 출처 확인 지표가 된다.**
웹의 검증 상태(`official`/`corroborated`/`partial`/`unverified`)가 이제 카드 개수가
아니라 story 에 접힌 **매체 개수**를 센다. 예전에는 `corroborated`(독립 출처 2곳
이상)가 사실상 도달 불가능했다 — 셀 매체가 수집 단계에서 이미 지워져 있었으므로.

**⑤ 병합/분리 판단은 운영 콘솔에서 되짚는다.** (`/admin`)
"왜 두 기사가 합쳐졌나"와 함께 **"왜 분리됐나"**(단계 충돌)와 "왜 이 기사가 카드의
얼굴인가"(대표 교체)를 같은 화면에서 본다. 분리는 결과물에 아무 흔적을 남기지 않아
`delivery_log` 의 `record_type: "story_audit"` 줄로만 남는다.

회귀 테스트: `python -m unittest tests.test_event_stage tests.test_ranking tests.test_story_dedup`

## 운영 콘솔 접근 (`/admin`)

진단 데이터에는 어떤 기사가 왜 접혔는지, 어떤 매체를 어떤 등급으로 보는지가 전부
들어 있다. 화면만 가리는 것은 가린 게 아니므로 **엣지에서** 막는다
(`functions/admin/_middleware.js` — Cloudflare Pages Function).

- 서비스 화면 우측 상단 톱니바퀴 → `/admin/` → 비밀번호 화면.
  주소를 직접 쳐도 같은 화면이 나온다.
- 콘솔 데이터는 `/data` 가 아니라 `/admin/data` 아래에 둔다. `/data` 는 독자 화면이
  쓰는 공개 경로라 미들웨어가 닿지 않는다.
- 세션은 서명된 HttpOnly 쿠키(8시간). 비밀번호를 바꾸면 서명 키가 갈려서 다른
  기기의 로그인이 전부 끊긴다.

### 최초 설정 (1회)

비밀번호를 화면에서 바꾸려면 바뀐 값을 쓸 곳이 있어야 한다. 환경변수는 Function 이
읽기만 할 수 있으므로 KV 네임스페이스 하나를 붙인다. **설정할 환경변수는 없다** —
세션 서명 키도 KV 가 처음 쓸 때 스스로 만든다.

1. Cloudflare 대시보드 → **Workers & Pages → KV** → Create namespace
2. Pages 프로젝트 → **Settings → Bindings** → KV namespace, 변수명 `ADMIN_KV`
3. 다시 배포

KV 가 안 붙어 있으면 콘솔은 **503 으로 잠긴다.** 통과시키면 '설정을 깜빡한 것'이
곧 '공개'가 되고, 그런 실수는 조용해서 몇 달을 간다.

### 첫 로그인 — `0000` 은 부트스트랩이지 비밀번호가 아니다

첫 비밀번호는 `0000` 이고, 들어가면 **비밀번호 변경 화면에서 못 빠져나온다.**
진단 화면도 데이터 JSON 도 바꾸기 전에는 403 이다.

강제인 이유: 여기서 위험한 것은 유출이 아니라 **추측**이다. 해시는 Cloudflare 밖으로
안 나가지만, `0000` 은 경우의 수가 1만 가지고 `/admin/login` 은 인터넷에 열린 POST
엔드포인트다. 자동화된 시도 앞에서는 잠긴 문이 아니므로, `0000` 이 방치될 수 있는
경로를 아예 만들지 않는다.

- 새 비밀번호는 8자 이상. 이후 콘솔 상단 **비밀번호 변경**에서 언제든 바꾼다.
- 실패가 15분 안에 10회 쌓이면 그 IP 는 15분간 막힌다.
- 비밀번호를 잊었다면 대시보드에서 KV 의 `admin:password` 키를 지운다 → 다시 `0000`.

배포 워크플로는 매번 살아 있는 사이트에 `/admin/` 이 인증 없이 200 을 내는지 물어본다
(`web/tools/verify_admin_gate.sh`). Function 폴더가 업로드에서 빠져도 배포는 조용히
성공하므로, 그 실패를 시끄럽게 만드는 유일한 방법이다.

로컬에서 확인하려면 KV 를 흉내 낸 채로 띄운다:

```bash
npx wrangler@4 pages dev web/public --kv ADMIN_KV
```

## 발송 원자성 (outbox 패턴)

`--plan`(선별·outbox 기록·큐 정리) → **claim push** → `--send`(pending 만 발송) →
`--confirm`(결과·delivery_log push). claim push 가 실패하면 발송 자체를 안 한다 →
"발송했는데 상태 저장 실패 → 다음날 중복" 문제 제거. 같은 날 재실행하면 sent 브리핑은
건너뛴다. 36시간 지난 pending 은 재발송하지 않는다(stale_skipped).

## 랭킹 조정 (비개발자용)

1. `ranking_config.json` 열기 — 모든 가중치에 한국어 설명 주석이 있다.
2. 숫자 수정 → commit → 다음 브리핑부터 적용. 코드 수정 불필요.
3. "왜 이 기사가 뽑혔지?" → `delivery_log.jsonl` 의 `breakdown` 확인.
4. 지표는 `python metrics.py`. (피드백 버튼 기능은 2026-07-16 완전 삭제 — git 히스토리 참조.)

## Secrets (GitHub Actions)

| 이름 | 필수 | 용도 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | ✅ | 발송·피드백 수거 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ✅ | 국내 뉴스 검색 ([NAVER API HUB](https://www.ncloud.com/product/applicationService/naverApiHub) — developers.naver.com 아님) |
| `GEMINI_API_KEY` | ⭕ | 없으면 큐레이션·투자관점 생략(fallback 발송) |
| `IMAP_USER` / `IMAP_PASSWORD` | ⭕ | ANS 뉴스레터 수집 (Gmail 앱 비밀번호, 공백 제거) |

## Variables (GitHub Actions)

| 이름 | 기본 | 용도 |
|---|---|---|
| `AUTOMATION_ENABLED` | 없음(=정지) | 마스터 스위치. `true` 여야 정기 실행이 돈다 |
| `BRIEFING_ENABLED` | 없음(=발송 안 함) | 발송 계열(daily-brief·weekly) 전용. **둘 다 `true`** 여야 텔레그램으로 나간다 |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | 큐레이션 모델 |
| `SITE_URL` / `CLOUDFLARE_PAGES_PROJECT` | 워크플로 기본값 | 배포·스모크 대상 |

스위치를 둘로 나눈 이유: 수집·웹 갱신은 돌리면서 발송은 내용을 검토한 뒤에
켜고 싶은 구간이 있다. 하나뿐이면 수집을 켜는 순간 발송도 같이 나간다.
`workflow_dispatch` 는 둘 다 우회하므로 검토용 수동 실행은 언제든 가능하다.

## 로컬 테스트

```bash
python daily_brief.py --dry-run        # 발송 없이 브리핑+점수 내역 출력
python embedding_pipeline.py --window-days 21 --max-new 150  # 이슈 매칭 캐시 백필
python -m unittest discover tests -v   # 테스트 (외부 호출 0)
python metrics.py                      # 품질 지표
```

## 데이터 품질 게이트

- Google News RSS의 `source`를 우선하고, 없으면 제목의 `- 매체명` 꼬리에서 발행처를 복원한다.
- URL은 추적 파라미터와 이중 슬래시만 정규화하며 기사 식별용 일반 쿼리는 유지한다.
- `/Error/` 경로, 정규화 URL 중복, 제목 완전일치 중복은 저장 전에 차단한다.
- 요약은 80자 이내 완결문이어야 하며 실패 항목만 한 번 재생성한다. 재실패 항목은 격리한다.
- `source_type`과 `evidence_role`을 분리해 전문언론을 공식 원문으로 표시하지 않는다.
- 이슈 임베딩은 `gemini-embedding-2`로 생성하고 모델·차원·입력 지문이 다른 구형 캐시는 폐기한다.
- 신규 큐레이션은 `event_date`와 날짜 의미·정밀도·근거 필드를 함께 기록한다.

과거 아카이브 이관 미리보기와 적용:

```bash
python news_archive.py --migrate-quality
python news_archive.py --migrate-quality --apply
```

웹 빌드는 위 조건을 다시 검사하고 위반이 있으면 배포 전에 실패한다.

## 롤백

- 랭킹만 되돌리기: `ranking_config.json` 을 이전 커밋으로.
- 전체 롤백: 이 커밋 이전으로 revert. 옛 큐 JSON 은 새 코드가 그대로 읽고(features
  없으면 기존 점수식), 새 큐 JSON 의 추가 필드는 옛 코드가 무시하므로 양방향 안전.
- outbox 꼬임: `outbox.json` 삭제 후 daily-brief 워크플로 수동 실행 (그날 큐 기준 재계획).
