# 아침 알림(웹 푸시) 설정

작성: 2026-09-19 · 대상: 이 저장소를 운영하는 사람

알림은 **07:00 KST** 에 하루 한 번 나간다. 코드는 전부 들어갔고, 아래 값 넷을
넣기 전까지는 **버튼이 화면에 뜨지 않는다** — 눌러도 되는 게 없는 버튼을 보이지
않으려는 의도다(2026-09-18 에 이 기능을 통째로 내리게 한 것이 그 상태였다).

---

## 1. 값 만들기

```bash
node web/tools/gen_vapid_keys.mjs
```

네 줄이 나온다. `VAPID_SUBJECT` 만 직접 채운다 — `mailto:<연락처>` 또는
`https://nuclens-v2.pages.dev`. 푸시 서비스가 문제가 생겼을 때 연락할 곳이다.

**키는 한 번 정하면 오래 쓴다.** 구독은 공개키에 묶여 발급되므로, 키를 갈면
그때까지의 구독이 전부 죽는다(푸시 서비스가 410 을 내고 발송이 걷는다).

---

## 2. Cloudflare Pages

**Settings → Variables and Secrets**

| 이름 | 종류 | 값 |
|---|---|---|
| `VAPID_PUBLIC_KEY` | 일반 변수 | 생성기의 첫 줄. 비밀이 아니다 — 화면이 `/push/key` 로 받아 간다 |
| `VAPID_PRIVATE_KEY` | **Secret** | 생성기의 둘째 줄 |
| `VAPID_SUBJECT` | 일반 변수 | `mailto:…` |
| `PUSH_SEND_TOKEN` | **Secret** | 생성기의 넷째 줄. 32자 이상 |

**Settings → Bindings → KV namespace**

구독을 담을 곳이 하나 필요하다. **이미 `ADMIN_KV` 를 연결해 뒀다면 그대로 쓰므로
아무것도 안 해도 된다** — 구독 키에는 `push:` 접두사가 붙어 콘솔 키와 섞이지
않는다. 따로 두고 싶으면 네임스페이스를 하나 더 만들어 변수명 `PUSH_KV` 로
연결한다(있으면 그쪽이 우선이다).

---

## 3. GitHub

**Settings → Secrets and variables → Actions**

| 이름 | 값 |
|---|---|
| `PUSH_SEND_TOKEN` | Cloudflare 에 넣은 것과 **같은 값** |

---

## 4. 확인

```bash
# 공개키가 서는가 (404 면 아직 설정 전이다)
curl -s https://nuclens-v2.pages.dev/push/key

# 오늘 알림 카드가 구워졌는가 (다음 아침 빌드부터 선다)
curl -s https://nuclens-v2.pages.dev/data/push.json
```

둘 다 200 이면 사이트의 '오늘' 화면에 🔔 버튼이 뜬다. 한 번 눌러 켜고,
Actions → **Push notify (아침 알림)** 을 `force` 로 한 번 돌려 보면 끝이다.

---

## 5. 설계 메모 — 왜 이렇게 됐나

**서명과 발송이 엣지에 있다.** 웹 푸시는 P-256 ECDSA 서명을 요구하는데, 이
저장소의 런타임 의존성은 셋으로 잠겨 있다(requests · google-genai · feedparser).
서명 하나 때문에 `cryptography` 를 들이는 대신, WebCrypto 가 이미 있는 Worker
런타임에 그 일을 두었다. 파이썬은 "언제 보낼지"만 판단한다.

**알림에 본문을 싣지 않는다.** 본문을 실으려면 구독마다 ECDH + HKDF + AES-GCM
을 돌려야 한다(RFC 8291). 무료 플랜 Worker 의 요청당 CPU 예산이 10ms 인데 그
비용은 구독자 수에 비례해 붙고, 무엇보다 실제 구독 없이는 끝까지 검증할 수 없다.
대신 빈 알림을 보내고 **서비스워커가 받는 순간 `/data/push.json`(1KB 미만)을
읽어** 오늘 제목을 붙인다. 구독 저장에는 `p256dh`·`auth` 를 이미 넣어 두므로,
나중에 본문 암호화를 붙여도 구독자에게 다시 켜 달라고 하지 않아도 된다.

**예약이 Daily Brief 와 따로다.** Daily Brief 는 04:25 KST 예약인데 도착이
04:45~05:40 사이로 흔들린다. 그 끝에 발송을 붙이면 알림 시각이 그 흔들림을 그대로
물려받는다. 알림은 사람이 정한 시각에 와야 하는 것이라 예약을 떼어 놓았고,
`tools/push_notify.py` 가 07:00~09:30 KST 창과 **오늘 브리핑이 실제로 라이브에
있는지**를 각각 본다. 둘 중 하나라도 아니면 보내지 않고 조용히 끝낸다 —
점심에 도착한 어제 브리핑 알림은 알림이 아니라 방해고, 되돌릴 수 없다.

**검사는 어디에 있나.** `web/tests/vapid_token.mjs`(서명을 다른 구현으로 풀어
본다) · `web/tests/push_contract.mjs`(열린 쓰기의 방어선과 발송 동작) ·
`tests/test_push_notify.py`(창·날짜 판단) · `web/tests/test_push_card.py`(알림
한 줄). 앞의 둘은 `python-tests.yml` 에서 돈다. 푸시는 틀려도 조용하다 —
푸시 서비스가 401 을 내고 실패 숫자 하나가 늘 뿐이라, 계약을 검사로 잠가 둔다.
