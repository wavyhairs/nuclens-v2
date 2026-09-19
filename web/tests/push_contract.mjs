// 푸시 창구 계약 — 열린 쓰기와 토큰으로 막은 목록을 실제로 돌려 본다.
//
//     node web/tests/push_contract.mjs
//
// 이 자리의 성격
// --------------
// `/push/subscribe` 는 **인터넷에 열린 쓰기**다. 콘솔처럼 비밀번호로 막을 수
// 없다 — 독자가 알림을 켜는 길이기 때문이다. 막는 대신 쓸 수 있는 것을 좁혔고,
// 그 좁힘이 실제로 서 있는지는 여기서만 확인된다.
//
// 특히 endpoint 검증은 SSRF 방어다. 임의의 주소를 받아 저장하면 매일 아침
// 발송기가 남의 서버로 POST 를 날려 주는 도구가 된다.
//
// `/push/list` 는 반대쪽이다. endpoint 는 그 브라우저를 특정하는 주소라,
// 목록은 **토큰 없이는 한 줄도 나가면 안 된다.** 발송이 엣지에서 파이썬으로
// 옮겨 오면서(2026-09-19) 생긴 창구고, 그래서 여기서 잠근다.

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const subscribe = await import("../../functions/push/subscribe.js");
const list = await import("../../functions/push/list.js");

// ── 흉내 KV ────────────────────────────────────────────────────────────────
// 한 장에 최대 둘만 돌려준다. 실제 KV 도 limit 보다 적게 주면서 list_complete
// 를 false 로 두는 일이 있고, 그 경우 커서를 안 따라가는 구현은 **조용히 일부
// 구독자에게만 보낸다** — 증상이 "어떤 폰은 알림이 온다" 하나뿐인 종류다.
const PAGE_CAP = 2;
function fakeKv() {
  const store = new Map();
  return {
    store,
    async get(key) { return store.has(key) ? store.get(key) : null; },
    async put(key, value) { store.set(key, value); },
    async delete(key) { store.delete(key); },
    // 커서는 **위치가 아니라 마지막 키 이름**이다. KV 의 커서가 그렇게 동작하고,
    // 이름 기준이면 도는 중에 한 건이 지워져도 뒤 페이지가 밀리지 않는다.
    async list({ prefix, limit = 1000, cursor }) {
      const names = [...store.keys()].filter(key => key.startsWith(prefix)).sort();
      const rest = cursor ? names.filter(name => name > cursor) : names;
      const page = rest.slice(0, Math.min(limit, PAGE_CAP));
      const complete = page.length >= rest.length;
      return {
        keys: page.map(name => ({ name })),
        list_complete: complete,
        cursor: complete ? "" : page[page.length - 1],
      };
    },
  };
}

// 흉내 KV 는 구독과 한도 카운터를 한 통에 담는다(실제 KV 도 같은 네임스페이스다).
// 그래서 "몇 건이 저장됐나"는 접두사로 세야 한다 — store.size 를 세면 카운터가
// 구독으로 잡힌다.
const subKeys = kv => [...kv.store.keys()].filter(key => key.startsWith("push:sub:"));
const subs = kv => subKeys(kv).length;
const oneSub = kv => JSON.parse(kv.store.get(subKeys(kv)[0]));

// base64url 87자 = 65바이트, 첫 바이트 0x04(비압축 점). 86자면 64바이트라
// 거절되는데, 그 한 글자 차이가 바로 이 검사가 잡아야 할 종류의 실수다.
const POINT = "B" + "A".repeat(86);
const AUTH = "A".repeat(22);          // 22자 = 16바이트
const goodKeys = () => ({ p256dh: POINT, auth: AUTH });
const post = (payload, ip = "1.2.3.4") => new Request("https://site.test/push/subscribe", {
  method: "POST", headers: { "CF-Connecting-IP": ip }, body: JSON.stringify(payload),
});

// ── 모양이 맞는 구독만 들어온다 ────────────────────────────────────────────
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv };
  const ok = await subscribe.onRequestPost({ request: post({
    subscription: { endpoint: "https://fcm.googleapis.com/fcm/send/abc", keys: goodKeys() },
  }), env });
  assert.equal(ok.status, 200);
  assert.equal(subs(kv), 1, "구독 하나가 저장돼야 한다");
  const stored = oneSub(kv);
  // 본문을 실어 보내는 지금은 이 둘이 **발송에 직접 쓰인다**(RFC 8291 암호화).
  // 예전 판은 쓰지 않으면서도 저장했는데, 그 판단이 지금 구독자 전원을 살렸다 —
  // 키를 안 받아 뒀다면 여기서 다시 켜 달라고 해야 했다.
  assert.equal(stored.keys.p256dh, POINT, "p256dh 없이는 본문을 못 싣는다");
  assert.equal(stored.keys.auth, AUTH);
}

// ── 푸시 서비스가 준 주소만 받는다 (SSRF) ──────────────────────────────────
for (const endpoint of [
  "http://fcm.googleapis.com/fcm/send/abc",   // https 가 아니다
  "file:///etc/passwd",
  "not a url",
  "",
]) {
  const kv = fakeKv();
  const response = await subscribe.onRequestPost({
    request: post({ subscription: { endpoint, keys: goodKeys() } }), env: { PUSH_KV: kv },
  });
  assert.equal(response.status, 400, endpoint + " 는 거절돼야 한다");
  assert.equal(subs(kv), 0);
}

// ── 키 모양이 틀리면 안 받는다 ─────────────────────────────────────────────
for (const keys of [
  null, {}, { p256dh: POINT }, { p256dh: "AAAA", auth: AUTH }, { p256dh: POINT, auth: "AA" },
]) {
  const kv = fakeKv();
  const response = await subscribe.onRequestPost({
    request: post({ subscription: { endpoint: "https://fcm.googleapis.com/x", keys } }),
    env: { PUSH_KV: kv },
  });
  assert.equal(response.status, 400, JSON.stringify(keys) + " 는 거절돼야 한다");
}

// ── 한 IP 가 목록을 채울 수 없다 ───────────────────────────────────────────
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv };
  let refused = 0;
  for (let i = 0; i < 40; i += 1) {
    const response = await subscribe.onRequestPost({
      request: post({ subscription: {
        endpoint: "https://fcm.googleapis.com/fcm/send/" + i, keys: goodKeys(),
      } }, "10.9.9.9"), env,
    });
    if (response.status === 429) refused += 1;
  }
  assert.ok(refused > 0, "한도가 서 있지 않다");
  assert.ok(subs(kv) <= 21, "한 IP 가 " + subs(kv) + "건을 넣었다");
}

// ── 같은 구독을 다시 켜는 것은 한도에 세지 않는다 ──────────────────────────
// 브라우저는 재시작·재구독 때마다 같은 endpoint 로 다시 등록한다. 그것을 한도에
// 세면 매일 쓰는 사람이 먼저 막힌다.
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv };
  const payload = { subscription: { endpoint: "https://fcm.googleapis.com/fcm/send/same", keys: goodKeys() } };
  for (let i = 0; i < 40; i += 1) {
    const response = await subscribe.onRequestPost({ request: post(payload), env });
    assert.equal(response.status, 200, i + "번째 재등록이 막혔다");
  }
  assert.equal(subs(kv), 1, "같은 endpoint 는 한 칸이다(SHA-256 키)");
}

// ── 해지 ───────────────────────────────────────────────────────────────────
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv };
  const endpoint = "https://fcm.googleapis.com/fcm/send/bye";
  await subscribe.onRequestPost({ request: post({ subscription: { endpoint, keys: goodKeys() } }), env });
  assert.equal(subs(kv), 1);
  const gone = await subscribe.onRequestDelete({
    request: new Request("https://site.test/push/subscribe", {
      method: "DELETE", body: JSON.stringify({ endpoint }) }),
    env,
  });
  assert.equal(gone.status, 200);
  assert.equal(subs(kv), 0);
  // 없는 것을 지우는 것도 성공이다 — 화면이 재시도할 이유를 만들지 않는다.
  const again = await subscribe.onRequestDelete({
    request: new Request("https://site.test/push/subscribe", { method: "DELETE", body: "{}" }),
    env,
  });
  assert.equal(again.status, 200);
}

// ── 설정이 없는 배포는 받았다고 말하지 않는다 ──────────────────────────────
{
  const response = await subscribe.onRequestPost({
    request: post({ subscription: { endpoint: "https://fcm.googleapis.com/x", keys: goodKeys() } }),
    env: {},
  });
  assert.equal(response.status, 503, "저장소가 없으면 받았다고 말하면 안 된다");
}

// ── 목록: 토큰 없이는 한 줄도 안 나간다 ────────────────────────────────────
const TOKEN = "t".repeat(32);
const listRequest = (token) => new Request("https://site.test/push/list", {
  method: "GET", headers: token === null ? {} : { Authorization: "Bearer " + token },
});

{
  const kv = fakeKv();
  await subscribe.onRequestPost({
    request: post({ subscription: {
      endpoint: "https://fcm.googleapis.com/fcm/send/secret", keys: goodKeys(),
    } }), env: { PUSH_KV: kv },
  });
  const env = { PUSH_KV: kv, PUSH_ADMIN_TOKEN: TOKEN };

  for (const token of [null, "", "wrong", TOKEN.slice(0, 31), TOKEN + "x", "T".repeat(32)]) {
    const response = await list.onRequestGet({ request: listRequest(token), env });
    assert.equal(response.status, 401, JSON.stringify(token) + " 로 목록이 열렸다");
    const body = await response.text();
    assert.ok(!body.includes("fcm.googleapis.com"),
      "401 응답이 endpoint 를 흘렸다 — 그 자체가 목록이다");
  }
}

// ── 목록: '설정 안 함'과 '틀린 열쇠'는 다른 답이다 ─────────────────────────
// 운영자가 어디를 볼지 갈린다. 401 만 보면 토큰을 다시 넣어 보다가 시간을 쓴다.
{
  const kv = fakeKv();
  for (const env of [{ PUSH_KV: kv }, { PUSH_KV: kv, PUSH_ADMIN_TOKEN: "" },
                     { PUSH_KV: kv, PUSH_ADMIN_TOKEN: "short" }]) {
    const response = await list.onRequestGet({ request: listRequest(TOKEN), env });
    assert.equal(response.status, 503, "토큰 미설정은 503 이어야 한다");
  }
  // 토큰은 맞는데 저장소가 안 붙은 배포. 이것도 401 이 아니다.
  const noStore = await list.onRequestGet({
    request: listRequest(TOKEN), env: { PUSH_ADMIN_TOKEN: TOKEN },
  });
  assert.equal(noStore.status, 503);
}

// ── 목록: 발송기가 받는 모양, 그리고 커서를 끝까지 따라간다 ────────────────
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv, PUSH_ADMIN_TOKEN: TOKEN };
  const total = 7;                    // PAGE_CAP(2) 로 네 장 이상 나온다
  for (let i = 0; i < total; i += 1) {
    await subscribe.onRequestPost({
      request: post({ subscription: {
        endpoint: "https://fcm.googleapis.com/fcm/send/" + i, keys: goodKeys(),
      } }, "192.168.1." + i), env,
    });
  }
  assert.equal(subs(kv), total);

  const response = await list.onRequestGet({ request: listRequest(TOKEN), env });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.count, total, "커서를 안 따라가 " + body.count + "건만 나왔다");
  assert.equal(body.subscriptions.length, total);

  // 발송기(tools/push_notify.py)가 쓰는 두 칸. 이름이 바뀌면 발송은 조용히
  // 0건이 되고, 로그에는 '구독자 0명 — 정상'만 남는다.
  for (const row of body.subscriptions) {
    assert.ok(row.endpoint.startsWith("https://"), "endpoint 가 없다");
    assert.equal(row.keys.p256dh, POINT);
    assert.equal(row.keys.auth, AUTH);
  }
  // 저장해 둔 나머지(seen_at 등)까지 내보낼 이유는 없다.
  assert.deepEqual(Object.keys(body.subscriptions[0]).sort(), ["endpoint", "keys"]);
}

// ── 목록: 깨진 항목 하나가 나머지를 막지 않는다 ────────────────────────────
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv, PUSH_ADMIN_TOKEN: TOKEN };
  await subscribe.onRequestPost({
    request: post({ subscription: {
      endpoint: "https://fcm.googleapis.com/fcm/send/ok", keys: goodKeys(),
    } }), env,
  });
  await kv.put("push:sub:deadbeef", "{not json");
  const response = await list.onRequestGet({ request: listRequest(TOKEN), env });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).count, 1, "깨진 한 줄이 목록을 죽였다");
  // 읽기 창구는 쓰기를 하지 않는다 — 토큰 하나로 목록을 지울 수 있게 된다.
  assert.ok(kv.store.has("push:sub:deadbeef"), "목록 조회가 KV 를 지웠다");
}

console.log("push contract: ok");
