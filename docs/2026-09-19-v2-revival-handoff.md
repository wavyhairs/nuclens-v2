# V2 기능 되살리기 — 인수인계

작성: 2026-09-19 · 대상 저장소: `wavyhairs/nuclens-v2` · 기준 커밋: `origin/main` @ `f951411`

이 문서를 받은 사람(또는 AI)이 **처음부터 다시 조사하지 않게** 하려고 쓴다.
아래 숫자는 전부 이 날짜에 **라이브(`https://nuclens-v2.pages.dev`)와
`origin/main`** 에서 직접 잰 값이고, 잰 방법을 같이 적어 두었다.

---

## 0. 먼저 읽을 것 — 이 저장소의 함정 셋

**① 로컬 체크아웃을 믿지 마라.**
`C:\AI\nuclens-v2` 의 로컬 `main` 은 `origin/main` 보다 크게 뒤처져 있었고
(2026-09-18 실측 120 커밋), 작업트리에는 무관한 미커밋 작업이 얹혀 있다.
로컬 파일만 읽고 "이 기능은 없다 / 이렇게 동작한다"를 판단하면 통째로 틀린다.

```bash
git fetch origin && git rev-list --left-right --count main...origin/main
# 뒤처져 있으면 로컬을 건드리지 말고 워크트리를 판다
git worktree add .worktrees/<이름> origin/main -b <브랜치>
```

`git stash` 는 쓰지 마라 — 워크트리끼리 스택을 공유한다. 치워 둘 것이 있으면
임시 WIP 커밋을 써라.

**② 지표는 라이브에서 재라.**
`web/public/data/` 는 `.gitignore` 라 체크아웃에 없다. 로컬에 남아 있는
`web/public/data` 는 최대 며칠 낡은 찌꺼기다. 재려면 받아서 채워라:

```bash
S=https://nuclens-v2.pages.dev
mkdir -p web/public/data
for f in meta briefings issues trend insights entities today threads issue_aliases; do
  curl -fsS "$S/data/$f.json?cb=$(date +%s)" -o "web/public/data/$f.json"
done
```

**③ 화면 파이썬 테스트는 빌드 산출물을 요구한다.**
빈 워크트리에서 `web/tests/test_prototype.py` 를 돌리면 **114건 오류 + 4건
실패**가 기본값이다(`web/public/data/*.json` 없음). 이건 회귀가 아니다.
기준선을 먼저 잡고 비교해라 — `origin/main` 워크트리를 하나 더 파서 같은
명령을 돌리면 된다.

---

## 1. 배경 — 무슨 일이 있었나

2026-09-17 PR #122 + 2026-09-18 PR #123 에서 **프런트만** v1(policy174) 것으로
통째 교체했다. 사용자가 원한 것은 "UI 만 v1 형태로"였고 그 의도대로 됐다.

파이프라인 파이썬은 0줄 변경이다. 새 화면은 v2 가 굽는 JSON 을 그대로 읽는다.

**문제는 v1 에 없던 화면이 함께 사라졌고, 그 화면이 읽던 데이터는 계속
생성된다는 것이다.** 잃은 게 아니라 **소비처가 없는 상태**라, 되살리려면
렌더러만 다시 붙이면 된다.

커밋 `df3e4ae "design: 화면 층을 v1 과 통일한다"` 가 지운 것:

```
web/public/ui-v3.js
web/tests/event_calendar.mjs      web/tests/issue_merge_redirect.mjs
web/tests/long_term_gate.mjs      web/tests/saved_alias_migration.mjs
web/tests/ui_budget.mjs           web/tests/ui_v3_rows.mjs
web/tests/weekly_sections.mjs     web/tests/weekly_selector.mjs
```

`StoryScopeTests`(web/tests/test_issue_ledger.py)도 함께 내렸다 — 그 자리에
이유를 적은 주석이 남아 있다(파일 256행). **원장·백엔드 검사는 전부 유지됐다.**

되살릴 때 그 화면의 계약 검사도 같이 되살려라. 지운 파일은
`git show df3e4ae^:web/tests/<이름>.mjs` 로 그대로 꺼낼 수 있다.

---

## 2. 현재 상태 한 장

### 굽는데 아무도 안 읽는다

| 산출물 | 라이브 크기(gzip) | 상태 | 되살리기 |
|---|---:|---|---|
| `data/today.json` | **87 KB** | 매일 구움. `app.js` 에 읽는 코드 0 | **A** — 가장 큰 효과 |
| `data/issue_aliases.json` | 3.7 KB (212건) | 매일 구움. 인앱 처리 0 | **B** — 가장 싼 값 |
| `data/threads.json` | 17 KB (89건) | 매일 LLM 판정까지 태움. 화면 없음 | **C** — 선행 조건 있음 |
| `issues[].change_log` | (issues.json 안) | 한 줄만 읽음. 이력 목록 미구현 | **D** — 작은 값 |

### 읽는데 아무도 안 굽는다 ← **이번에 새로 찾은 것**

`khnp_domain`. `app.js` 가 **세 곳**에서 읽는다:

- `tocChips()` (1839~1851행) — 목차 행의 분류 칩
- `renderBriefing()` (1978·1995행) — 홈의 제자리 분류 필터
- 탐색 필터 (2226행) — `state.archiveDomain`

그런데 **빌더가 그 필드를 안 만든다.** 라이브 `issues.json` 587건 중 보유
**0건**, `today.json` 18건 중 **0건**. `grep -rn khnp_domain --include=*.py` 는
`make_cards.py`(사이트 칩을 받아 쓰는 쪽)와 테스트만 잡힌다 —
`khnp_relevance.py` 가 내는 것은 `domains`(한수원 관련성 판정용)로 **다른
값**이다.

결과: 분류 칩이 한 번도 안 뜨고, 홈 분류 필터와 '지난 기사까지' 경로는
**도달 불가능한 죽은 코드**이며, 탐색의 분류 필터는 항상 0건이다.

→ 판단이 필요하다: **필드를 굽든가(v1 에서 어떻게 채웠는지 확인), 읽는 코드
셋을 걷든가.** 지금처럼 두면 죽은 코드 세 덩이가 계속 남는다.

### 이미 살아 있다 — 오진하지 마라

- `why_short` → 빌더가 `card_why` 로 태우고(`web/build_data.py` 3689~3720행)
  v1 이 그걸 읽는다(`app.js` 1090행). **죽지 않았다.**
- `change_display` · `change_kind` · '직전까지' 한 줄 → v1 도 읽는다
  (`app.js` 402~416행).
- 이벤트 캘린더 · 워드클라우드 → v1 판이 서 있다(`index.html` 16곳 ·
  `app.js` 35곳). 잃은 게 아니라 **구현이 바뀐 것**이다.
- 정적 `/issue/<옮겨간 id>/` 딥링크 → 산다. 다만 HTTP 308 이 아니라
  **정적 HTML 리다이렉트 페이지**다(`tools/render_static_pages.py` 의
  `REDIRECT_TEMPLATE` — `<meta http-equiv=refresh>` + `location.replace`).
  라이브 확인: 옛 id·새 id 모두 `200`.

---

## 3. 되살릴 후보

### A. `today.json` 으로 부팅한다 — 첫 화면 6.01 MB → 87 KB

**무엇이 문제인가.** `app.js` 는 `init()` 에서 `await Promise.all([...])` 로
아래를 **전부 받은 뒤에야** 첫 렌더를 한다(5723행 부근).

| 부팅에 받는 것 | gzip |
|---|---:|
| `news/000~003.json` (4 샤드) | 3.34 MB |
| `briefings.json` | 1.30 MB |
| `issues.json` | 1.30 MB |
| trend · meta · insights · publications · entities | 67 KB |
| **합계** | **6.01 MB** |

`today.json` 하나는 **87 KB** 다. 69분의 1.

**되살릴 가치가 있다는 근거.** `today.json` 은 첫 화면에 필요한 필드를
**이미 거의 다 들고 있다.** 실측으로 대조했다:

```
첫 화면(pickCard·tocRow·pickDetail)이 읽는 필드
  first_seen · implication · issue_id · khnp_domain · latest_change ·
  open_question · region · representative_article · summary · title ·
  tracked_briefings · why_important

today.json 에 없는 것 → khnp_domain 하나뿐
```

그리고 그 하나는 **issues.json 에도 없다**(위 2절). 즉 today.json 쪽 결함이
아니다. 날짜 이동 칸도 이미 산다 — `today.json.dates` 에 60일치가 들어 있다.

**작업 범위.** "한 줄 연결"이 아니다. today.json 으로 **먼저 그리고**, 나머지
(이슈 상세·탐색·흐름·보고서)를 화면 진입 시 지연 로드하도록 부팅 경로를
바꿔야 한다. `web/build_data.py` 의 `build_today_payload()` 주석(3742행~)에
설계 의도가 남아 있으니 먼저 읽어라.

**함께 되살릴 검사.** `git show df3e4ae^:web/tests/ui_budget.mjs` — 첫 렌더
차단 바이트를 CI 예산으로 잠그던 검사다. 되살리지 않으면 부피는 반드시 다시
분다(이 저장소는 같은 화면을 이미 세 번 줄였다).

**난이도** 중~상 · **효과** 최상 · **위험** 중(부팅 순서를 건드린다. 데이터가
늦게 도착하는 창에서 칸이 비는 증상이 재현이 어렵다 — `TODAY_DROP_FIELDS`
주석이 그 경고를 적어 두었다).

---

### B. `issue_aliases.json` — 저장한 이슈의 옛 주소 복구 (212건)

**무엇이 문제인가.** 이슈 `id` 는 재클러스터링으로 옮겨 다닌다. 원장이 그
이동을 `issue_aliases.json` 에 212건 기록해 매일 굽는데, **`app.js` 는 그
파일을 읽지 않는다.** (`grep alias app.js` 가 잡는 4건은 전부 **엔티티**
별칭 — 검색용이라 무관하다.)

결과: 사용자가 저장해 둔 이슈가 옮겨 가면 인앱에서 해소되지 않고
톰스톤으로 떨어진다 — "이 이슈는 재구성되어 현재 목록에 없습니다 /
제목으로 다시 찾기"(`app.js` 747~753행). 정적 딥링크는 리다이렉트
페이지가 살리지만, **앱 안의 저장 목록·`?issue=` 파라미터는 못 산다.**

**왜 값이 싼가.** 지운 검사 `saved_alias_migration.mjs` 가 **계약을 그대로
들고 있다.** 꺼내서 읽어라:

```bash
git show df3e4ae^:web/tests/saved_alias_migration.mjs
```

그 파일 머리말이 이 자리의 성격을 이미 정의해 두었다 — *"이 자리는 사용자의
의도(저장)를 다룬다. 조용히 잃거나 남의 저장을 덮으면 되돌릴 방법이 없다.
그래서 순수 함수로 떼어 잠근다."*

**난이도** 하 · **효과** 중 · **위험** 하 — 이것부터 하는 것을 권한다.

---

### C. `threads.json` — 장기 스토리 화면 (89건). **선행 조건 있음**

**현재 상태(라이브 실측).**

```
version: thread-web-v1    visible: true    status: ok    degraded: false
generated_at: 2026-09-18T22:20:58+09:00
stale_hours: 72           hide_after: 2026-09-21T07:36:25+09:00
build: {candidates: 3472, events: 388, threads: 132, asked: 7, failed: 0}
payload: 89건
```

게이트는 **열려 있다**(`visible: true`). 판정은 daily-brief 에서 **매일 LLM 을
태운다**(회차당 `asked: 7`). 그런데 `app.js` 의 `VIEW_IDS` 는
`["news","trend","search","report"]` 뿐이라 **탭도 `?view=longterm&th=`
딥링크도 오늘 화면으로 떨어진다.** 즉 매일 돈을 쓰고 아무도 안 본다.

**되살리기 전에 반드시 읽을 것: `docs/nuclens-ui-v3-backlog.md` 2절.**
`threads.json` 과 `issues.json` 이 같은 사건을 다르게 말한다. 2026-09-13 실측
101건 중 **97건(96%)** 이 이슈 원장과 시작일 또는 건수가 다르고, 원장에 없는
`event_id` 를 가리키는 스토리가 다수다. 원인은 원장이 id 를 옮길 때
(`moved_to`) 스토리가 옛 주소를 계속 들고 있는 것 — **유령 id**.

장기 스토리 β 화면에서 **링크 46% 가 죽어 있던** 것이 이 원인이었다.
그 상태로 화면만 다시 붙이면 같은 일이 반복된다.

방향은 그 문서에 이미 결정돼 있다: `threads.json` 이 사건을 **독립 보관**하지
말고 `issues.json` 의 사건 id 를 **정본으로 참조**하게 바꾼다.

**그래서 선택지가 둘이다.**

- **C-1 제대로** — 원장 일원화(backlog 2절) → 마이그레이션 → 화면 재구현 →
  `long_term_gate.mjs` 복원. 난이도 상.
- **C-2 지금 당장의 정직한 선택** — 되살릴 계획이 없다면 **판정을 꺼서 매일
  LLM 을 아낀다.** 데이터도 화면도 없는 상태로 비용만 내는 지금이 제일 나쁘다.

둘 중 무엇을 고를지는 사람이 정해야 한다. **기본값으로 C-1 을 시작하지 마라.**

---

### D. `change_log` 여러 건 이력

한 줄(`change_display` · `change_kind` · '직전까지')은 v1 도 읽는다. **여러 건
이력을 목록으로 그리는 코드만** 없다. 데이터는 `issues[].change_log` 에 이미
실려 있다(`today.json` 의 이슈 레코드에도 있다).

**난이도** 하 · **효과** 하. 커버리지가 원래 낮다(11.8%가 정상 — 게이트가
'같은 이슈 멤버일 때만'으로 좁다). 앞의 셋을 끝낸 뒤에 손대라.

---

## 4. 권하는 순서

```
0) 기준선 확보      워크트리 + 라이브 데이터 + 테스트 기준선(114 errors/4 failed)
1) B  issue_aliases  작고 명확하다. 지운 검사가 계약을 들고 있다
2) 판단  khnp_domain  굽든가 걷든가 — 죽은 코드 세 덩이를 정리
3) A  today.json     가장 큰 효과. ui_budget.mjs 를 같이 되살릴 것
4) C  장기 스토리    사람이 C-1/C-2 를 고른 뒤에만
5) D  change_log     남으면
```

1·2 를 먼저 두는 이유: 둘 다 **범위가 닫혀 있고** 실패해도 첫 화면을 안
건드린다. 3 은 부팅 경로를 바꾸므로 그 앞에서 기준선과 감이 잡혀 있어야 한다.

---

## 5. 검증 방법

```bash
# 파이썬 회귀 (CI 와 같은 명령) — 현재 2081건 OK
python -m unittest discover -s tests

# 화면 파이썬 — 빌드 산출물이 없으면 114 errors/4 failed 가 기본값이다
python -m pytest web/tests/test_prototype.py -q

# 화면 계약(.mjs) — 시스템 크롬을 쓴다. 새 의존성 없음
node web/tests/date_window.mjs
node web/tests/trend_period_state.mjs
node web/tests/admin_dom.mjs

# 첫 화면 부피 실측 (A 작업의 성적표)
node web/tools/measure_ui.mjs "?ui=v3"   # blockingBytes 를 본다

# 라이브 페이로드 크기 (gzip 전송량)
curl -fsS -H "Accept-Encoding: gzip" -o /dev/null -w "%{size_download}\n" \
  "https://nuclens-v2.pages.dev/data/today.json?cb=$(date +%s)"
```

브라우저 렌더 확인은 시스템 크롬을 headless 로 부르면 된다(이 저장소의
`web/tools/measure_ui.mjs` · `web/tests/admin_dom.mjs` 와 같은 방식,
playwright 를 들이지 마라 — 배포 경로에 브라우저 내려받기 2분이 붙는다):

```bash
chrome --headless=new --disable-gpu --hide-scrollbars \
  --virtual-time-budget=30000 --window-size=1280,2000 \
  --screenshot=out.png "http://127.0.0.1:8787/"
```

---

## 6. 하지 말 것

- **PyYAML 같은 새 의존성을 `requirements.txt` 에 넣지 마라.** 런타임 의존성이
  셋뿐인 것이 의도다(requests · google-genai · feedparser). 워크플로 계약 검사도
  주석을 걷은 원문을 직접 파싱해서 만들었다(`tests/test_cards_workflow.py`).
- **반복 요소에 그림자를 얹지 마라.** `web/public/style.css` 머리말의 팔레트
  규칙 3 — 지면에 박힌 패널·카드는 프레임만 쓴다. 화면당 색은 셋까지.
- **12.5px 미만 글자 금지.** `test_rendered_text_has_12_5px_minimum` 이
  리터럴·`font` 축약형·`--t-*` 토큰 정의값·clamp 최소값까지 전부 본다.
- **폭에 따라 읽는 순서를 바꾸지 마라.** 2026-09-19 에 `placeCardStrip()` 을
  걷어 냈다 — 넓은 화면과 폰이 구역 순서를 다르게 세우고 있었다. 지금 홈의
  순서는 마크업 하나가 정본이다: 카드뉴스 → 먼저 볼 3건 → 오디오 → 그 밖의 이슈.
- **`web/public/*` 는 소스가 곧 배포본이다.** 번들러가 없다. 코드 분할을
  하려면 그 규약부터 세워야 한다(`docs/nuclens-ui-v3-backlog.md` 1절).

---

## 6.5. 이 문서 뒤에 실제로 한 것 (2026-09-19 저녁)

사용자 보고 세 건에서 출발했고, 셋 다 **되살리기**였다.

| 무엇 | 원인 | 한 일 |
|---|---|---|
| 폰에 장기 스토리 탭이 없다 | `#mobileTabs` 에 그 칸이 아예 없었다. `.main-tabs` 는 좁은 화면에서 `display:none` 이라 상단 탭만으로는 **폰에서 존재 자체가 안 보인다** | 하단 탭에 칸을 세웠다. 열 수를 상수로 안 박는다(게이트가 4·5칸을 오간다) |
| 탐색의 '두 칸'이 사라졌다 | `df3e4ae` 가 `#archiveScope` 를 통째로 걷었다 | '추적 중인 이슈 / 모든 이슈' 복원. 라이브 실측 237 / 587건 |
| 아침 알림 버튼이 없다 | `ab9e706` 이 내렸다 — **v2 에 `functions/push/*` 가 한 번도 없었다** | 창구부터 지었다. `docs/2026-09-19-web-push-setup.md` 참조 |

세 번째가 앞의 둘과 성격이 다르다. 앞의 둘은 코드가 이력에 남아 있어 꺼내
붙이면 됐지만, 푸시는 v2 에 **존재한 적이 없다**(전체 이력 검색 0건). 화면만
되살리면 2026-09-18 과 같은 자리로 돌아간다 — 보이는데 눌러도 되는 게 없는 버튼.

그래서 지금은 순서가 뒤집혀 있다: 서버 설정이 없으면 **버튼이 안 뜬다.**
브라우저 실측으로 두 상태를 다 확인했다(`/push/key` 404 → `display:none`,
200 → 보임).

### 2절의 '되살릴 후보' 갱신

- **B(issue_aliases)** · **C(장기 스토리)** · **D(change_log)** 는 PR #130~#132
  로 이미 머지됐다. 이번에 한 것은 C 의 **폰 통로**다 — 화면은 서 있었는데
  들어갈 문이 데스크톱에만 있었다.
- **A(today.json 부팅)** 도 PR #128 로 끝났다(45.8MB → 1.2MB).
- **khnp_domain 판단**은 아직 남아 있다. 이번 작업에서 건드리지 않았다 —
  `archiveIssueMatches` 의 `state.archiveDomain` 분기는 여전히 도달 불가다.

### 남은 판단 하나 — 닫혔다 (2026-09-19)

푸시 알림에 **본문을 싣지 않는다**(RFC 8291 암호화 없음)는 판단이었다.
서비스워커가 `/data/push.json` 을 읽어 제목을 붙이는 것으로 대신했고, 구독
저장에 `p256dh`·`auth` 를 이미 넣어 두었으므로 나중에 붙여도 구독자는 그대로
간다고 적어 두었다.

그 "나중"이 같은 날이었다. 발송을 엣지에서 파이썬(`pywebpush`)으로 옮기면서
본문이 푸시 안에 실린다 — CPU 예산이 문제였는데 러너에는 그 예산이 없다.
받아 둔 `p256dh`·`auth` 가 그대로 쓰였고, 구독자는 한 명도 다시 켜지 않았다.
`functions/push/send.js` 와 `/data/push.json` 은 사라졌다. 지금 구조는
`docs/2026-09-19-web-push-setup.md` 5절에 있다.

---

## 7. 참고 문서

| 문서 | 무엇이 들어 있나 |
|---|---|
| `docs/nuclens-ui-v3-backlog.md` | 1절 코드 분할 · **2절 원장 일원화(C의 선행 조건)** |
| `docs/nuclens-ui-v3-instructions-rev.md` | v3 화면의 설계 의도(되살릴 때의 원본) |
| `web/build_data.py` 3720~3760행 | `today.json` 설계 의도와 `TODAY_DROP_FIELDS` 경고 |
| `tools/render_static_pages.py` | 정적 이슈·브리프 페이지와 이동 리다이렉트 |
| `web/tests/test_issue_ledger.py` 256행 | `StoryScopeTests` 를 내린 이유 |
| `git show df3e4ae^:web/tests/<이름>.mjs` | 함께 내려간 계약 검사 8종의 원본 |
| `docs/2026-09-19-web-push-setup.md` | 아침 알림 설정(운영자가 넣을 값 넷)과 설계 메모 |
