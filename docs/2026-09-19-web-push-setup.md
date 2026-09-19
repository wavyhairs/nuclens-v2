# 아침 알림(웹 푸시) 설정

작성: 2026-09-19 · 대상: 이 저장소를 운영하는 사람

알림은 **아침 브리핑이 나가고 사이트가 배포된 뒤** 하루 한 번 이어서 나간다.
전용 예약은 없다 — Daily Brief 워크플로의 마지막 부분에 붙어 있다.

아래 값들을 넣기 전까지는 **화면에 버튼이 뜨지 않는다**(공개키가 없으면 숨긴다).
눌러도 되는 게 없는 버튼을 보이지 않으려는 의도다 — 2026-09-18 에 이 기능을
통째로 내리게 한 것이 정확히 그 상태였다.

---

## 0. 이미 설정된 배포라면 (2026-09-19 구조 변경)

발송이 엣지에서 파이썬으로 옮겨 왔다. **키를 새로 만들지 마라** — 공개키를 갈면
그때까지의 구독이 전부 죽는다. 할 일은 값을 **옮기는 것** 둘뿐이다.

| 해야 할 일 | 어디서 어디로 |
|---|---|
| `VAPID_PRIVATE_KEY` | Cloudflare 에 있는 값을 **그대로** GitHub Secret 으로 복사 |
| `PUSH_ADMIN_TOKEN` | 새로 만들어 Cloudflare·GitHub **양쪽에 같은 값**으로 |
| `PUSH_SEND_TOKEN` | 더 이상 안 쓴다 — 양쪽에서 지워도 된다 |

`VAPID_PRIVATE_KEY` 는 base64url 32바이트다. `pywebpush` 가 그 모양을 그대로
받는다(PEM 도 받는다). Cloudflare 쪽 `VAPID_PRIVATE_KEY` 는 이제 아무도 안
읽으므로 지워도 되고, 남겨 둬도 해는 없다.

**구독은 그대로 산다.** 저장 위치(`push:sub:` + endpoint SHA-256)도 저장 모양도
바뀌지 않았다. 다시 켜 달라고 할 필요가 없다.

---

## 1. 값 만들기 (처음 설정하는 경우만)

```bash
node web/tools/gen_vapid_keys.mjs
```

네 줄이 나온다. `VAPID_SUBJECT` 만 직접 채운다 — `mailto:<연락처>`. 푸시
서비스가 문제가 생겼을 때 연락할 곳이다.

**키는 한 번 정하면 오래 쓴다.** 구독은 공개키에 묶여 발급되므로, 키를 갈면
그때까지의 구독이 전부 죽는다(푸시 서비스가 410 을 내고 발송이 걷는다).

---

## 2. Cloudflare Pages

**Settings → Variables and Secrets**

| 이름 | 종류 | 값 |
|---|---|---|
| `VAPID_PUBLIC_KEY` | 일반 변수 | 생성기의 첫 줄. 비밀이 아니다 — 화면이 `/push/key` 로 받아 간다 |
| `PUSH_ADMIN_TOKEN` | **Secret** | `/push/list` 를 여는 열쇠. 32자 이상 |

개인키는 여기 없다. 엣지는 더 이상 서명하지 않는다.

**Settings → Bindings → KV namespace**

구독을 담을 곳이 하나 필요하다. **이미 `ADMIN_KV` 를 연결해 뒀다면 그대로 쓰므로
아무것도 안 해도 된다** — 구독 키에는 `push:` 접두사가 붙어 콘솔 키와 섞이지
않는다. 따로 두고 싶으면 네임스페이스를 하나 더 만들어 변수명 `PUSH_KV` 로
연결한다(있으면 그쪽이 우선이다).

---

## 3. GitHub

**Settings → Secrets and variables → Actions**

| 이름 | 종류 | 값 |
|---|---|---|
| `PUSH_ADMIN_TOKEN` | Secret | Cloudflare 에 넣은 것과 **같은 값** |
| `VAPID_PRIVATE_KEY` | Secret | 생성기의 둘째 줄(또는 Cloudflare 에 있던 그 값) |
| `VAPID_SUBJECT` | Variable | `mailto:…` — 없으면 기본값으로 나간다 |

`PUSH_ADMIN_TOKEN` 이 양쪽에서 다르면 증상은 하나다: 매일 아침 `/push/list` 가
401 을 내고 아무에게도 안 간다. 그 경우 Daily Brief 로그에 `::error::` 가 남는다.

---

## 4. 확인

```bash
# 공개키가 서는가 (404 면 아직 설정 전이다 — 버튼도 안 뜬다)
curl -s https://nuclens-v2.pages.dev/push/key

# 목록 창구가 토큰을 받는가 (401 이면 토큰 불일치, 503 이면 미설정)
curl -s -H "Authorization: Bearer $PUSH_ADMIN_TOKEN" \
  https://nuclens-v2.pages.dev/push/list
```

`/push/key` 가 200 이면 사이트의 '오늘' 화면에 🔔 버튼이 뜬다. 한 번 눌러 켜고,
`/push/list` 가 그 구독을 돌려주면 창구는 다 선 것이다.

발송만 따로 시험하려면 로컬에서:

```bash
PUSH_ADMIN_TOKEN=... VAPID_PRIVATE_KEY=... python tools/push_notify.py --dry-run
```

`--dry-run` 은 보낼 제목·본문만 찍는다. 실제로 한 번 보내려면 Actions 에서
**Daily Brief** 를 돌린다(그날 이미 나갔으면 `--force` 없이는 건너뛴다).

---

## 5. 설계 메모 — 왜 이렇게 됐나

**발송은 파이썬이 한다.** `tools/push_notify.py` 가 `/push/list` 에서 구독자를
받아 `pywebpush` 로 각 endpoint 에 직접 보낸다. VAPID 서명도 RFC 8291 본문
암호화도 그 라이브러리 몫이다. 엣지에 남은 일은 셋뿐이다 — 구독을 받고
(`/push/subscribe`), 목록을 내주고(`/push/list`), 공개키를 알려 준다(`/push/key`).

**본문이 푸시 안에 실린다.** 예전 판은 엣지에서 손으로 서명했고, 본문을 실으려면
구독마다 ECDH + HKDF + AES-GCM 을 돌려야 하는데 그 비용이 무료 플랜 Worker 의
요청당 CPU 예산(10ms) 위에 구독자 수만큼 얹혔다. 그래서 **빈 알림**을 보내고
서비스워커가 받는 순간 `/data/push.json` 을 다시 읽었다. 그 왕복이 실패하면 —
폰이 지하철에 있거나 배포가 늦으면 — 알림은 매번 일반 문구로만 떴고 발송 로그에는
'보냄'으로 남았다. 러너에는 CPU 예산이 없으므로 그 우회가 통째로 사라졌다.

**예약이 Daily Brief 와 하나다.** 예전엔 07:00 KST 전용 워크플로가 따로 돌았고,
"지금이 아침인가 · 오늘 브리핑이 라이브에 있는가"를 발송기가 매번 되물었다.
둘 다 브리핑 워크플로 안에서는 **이미 아는 사실**이다. 그래서 배포 성공
(`steps.web-deploy.outcome == 'success'`)에 걸었다 — 브리핑이 안 나간 날에는
알림도 안 가고, 알림이 가리키는 `/?src=push` 가 오늘 것을 보여 주는 것도 그
자리라야 참이다. 알림은 브리핑보다 이르게 올 수 없다.

**중복은 outbox 가 막는다.** 같은 Daily Brief 를 다시 돌리면 알림이 한 번 더
간다. 새 상태 시스템을 만드는 대신 `outbox.json` 의 `push` 칸에 보낸 날짜를
적고, 이미 있는 커밋 스텝이 그것을 싣는다. 복구·점검은 `--force` 로 넘는다.

**실패는 조용하지 않다.** 스텝은 `continue-on-error: true` 라 알림이 실패해도
텔레그램 브리핑·카드·웹은 그대로 나간다. 다만 로그에서는 갈린다:

| 상황 | 로그 |
|---|---|
| 구독자 0명 | 평문 — 정상이다 |
| 일부 실패 | `::warning::` |
| 구독자가 있는데 전원 실패 | `::error::` |
| `/push/list` 401·503 | `::error::` |
| 시크릿·키 누락 | `::error::` |

**검사는 어디에 있나.** `web/tests/push_contract.mjs`(열린 쓰기의 방어선 ·
목록 창구의 401/503 · 커서) 는 `python-tests.yml` 에서 돌고,
`tests/test_push_notify.py` 는 진짜 VAPID 키와 진짜 구독 키로 **암호화해 보내고
풀어서** 제목·본문·주소·tag 를 확인한다(루트 검사). 푸시는 틀려도 조용하다 —
실패 숫자 하나가 늘 뿐이라, 계약을 검사로 잠가 둔다.
