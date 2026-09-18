// 푸시 창구 계약 — 열린 쓰기와 열린 발송을 실제로 돌려 본다.
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
// 이 서버가 남의 서버로 POST 를 날려 주는 도구가 된다.

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const subscribe = await import("../../functions/push/subscribe.js");
const send = await import("../../functions/push/send.js");

// ── 흉내 KV ────────────────────────────────────────────────────────────────
function fakeKv() {
  const store = new Map();
  return {
    store,
    async get(key) { return store.has(key) ? store.get(key) : null; },
    async put(key, value) { store.set(key, value); },
    async delete(key) { store.delete(key); },
    // 커서는 **위치가 아니라 마지막 키 이름**이다. KV 의 커서가 그렇게 동작하고,
    // 그 차이가 여기서 실제로 드러난다: 발송은 돌면서 죽은 구독을 지우므로,
    // 커서가 위치(0,2,4…)면 한 건 지워질 때마다 뒤 페이지가 한 칸씩 밀려
    // **매번 한 건씩 건너뛴다.** 이름 기준이면 지워도 밀리지 않는다.
    async list({ prefix, limit = 1000, cursor }) {
      const names = [...store.keys()].filter(key => key.startsWith(prefix)).sort();
      const rest = cursor ? names.filter(name => name > cursor) : names;
      const page = rest.slice(0, limit);
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
  // 지금 보내는 알림에는 본문이 없어 이 키들을 쓰지 않는다. 그래도 저장한다 —
  // 나중에 본문 암호화를 붙일 때 구독자 전원에게 다시 켜 달라고 할 수는 없다.
  assert.equal(stored.keys.p256dh, POINT, "p256dh 를 버리면 나중에 되돌릴 수 없다");
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
  assert.equal(subs(kv), 1);
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

// ── 발송은 토큰 없이는 한 통도 안 나간다 ───────────────────────────────────
const VAPID = {
  VAPID_PUBLIC_KEY: "BMIWNB0a2cBKAOwhl-UUJCMW_CbRXhk3CIt4sy_yqbZAOhF4eCQEpuPNp0fZ1Q5uhnSb3igw8svjy9I8AzVw3o8",
  VAPID_PRIVATE_KEY: "--PsIvxbqCidsn2i7MRyUaxeWHvvKQYZuG1jb9N83s8",
  VAPID_SUBJECT: "mailto:ops@example.test",
  PUSH_SEND_TOKEN: "x".repeat(32),
};
const sendRequest = (token) => new Request("https://site.test/push/send", {
  method: "POST",
  headers: token ? { Authorization: "Bearer " + token } : {},
  body: JSON.stringify({ limit: 10 }),
});

{
  const kv = fakeKv();
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return new Response("", { status: 201 }); };
  for (const token of ["", "wrong", "x".repeat(31)]) {
    const response = await send.onRequestPost({
      request: sendRequest(token), env: { PUSH_KV: kv, ...VAPID },
    });
    assert.equal(response.status, 401, (token || "(없음)") + " 로 발송이 열렸다");
  }
  assert.equal(calls, 0, "인증 전에 한 통이라도 나가면 안 된다");
  // 설정이 덜 된 배포는 401 이 아니라 503 이다 — 운영자가 어디를 볼지 갈린다.
  const unset = await send.onRequestPost({
    request: sendRequest(VAPID.PUSH_SEND_TOKEN),
    env: { PUSH_KV: kv, PUSH_SEND_TOKEN: VAPID.PUSH_SEND_TOKEN },
  });
  assert.equal(unset.status, 503);
}

// ── 발송: 죽은 구독을 걷고, 커서로 끊어 돈다 ───────────────────────────────
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv, ...VAPID };
  for (let i = 0; i < 5; i += 1) {
    await subscribe.onRequestPost({
      request: post({ subscription: {
        endpoint: "https://fcm.googleapis.com/fcm/send/" + i, keys: goodKeys(),
      } }, "10.0.0." + i), env,
    });
  }
  assert.equal(subs(kv), 5);

  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), headers: init.headers });
    // 가운데 하나는 이미 사라진 구독이고, 하나는 그냥 실패다.
    if (String(url).endsWith("/2")) return new Response("", { status: 410 });
    if (String(url).endsWith("/3")) return new Response("", { status: 500 });
    return new Response("", { status: 201 });
  };

  let cursor = "";
  let sent = 0;
  let pruned = 0;
  let failed = 0;
  let rounds = 0;
  do {
    const response = await send.onRequestPost({
      request: new Request("https://site.test/push/send", {
        method: "POST", headers: { Authorization: "Bearer " + VAPID.PUSH_SEND_TOKEN },
        body: JSON.stringify({ limit: 2, cursor, topic: "nuclens-brief" }),
      }), env,
    });
    assert.equal(response.status, 200);
    const result = await response.json();
    sent += result.sent; pruned += result.pruned; failed += result.failed;
    cursor = result.cursor;
    rounds += 1;
    assert.ok(rounds < 10, "커서가 안 줄고 있다");
  } while (cursor);

  assert.equal(sent, 3, "201 셋이 보낸 것으로 세어져야 한다");
  assert.equal(pruned, 1, "410 은 걷어야 한다");
  assert.equal(failed, 1, "500 은 실패지 정리가 아니다");
  assert.ok(rounds >= 3, "limit 2 인데 한 번에 다 돌았다 — 커서가 안 먹는다");
  assert.equal(subs(kv), 4, "410 이 난 구독만 사라져야 한다");

  // 보내는 모양 — 여기가 틀리면 푸시 서비스가 조용히 401·400 을 낸다.
  const headers = seen[0].headers;
  assert.match(headers.Authorization, /^vapid t=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+, k=[A-Za-z0-9_-]+$/);
  assert.ok(Number(headers.TTL) > 0, "TTL 없이 보내면 일부 서비스가 400 을 낸다");
  assert.equal(headers["Content-Length"], "0", "본문 없는 알림이다");
  assert.equal(headers.Topic, "nuclens-brief");
  assert.equal(headers["Content-Encoding"], undefined,
    "본문이 없는데 Content-Encoding 을 붙이면 서비스가 거절한다");

}

// ── 서명은 구독자마다가 아니라 **출처마다** 한 번이다 ──────────────────────
//
// 무료 플랜의 Worker 는 요청당 CPU 10ms 다. VAPID 서명은 그 예산에서 가장 비싼
// 한 줄이라, 구독자 수에 비례해 붙으면 구독이 늘수록 발송이 통째로 죽는다.
// (ECDSA 는 같은 내용을 서명해도 매번 다른 값이 나오므로, 헤더가 같다는 것은
//  곧 **서명을 다시 하지 않았다**는 뜻이다.)
{
  const kv = fakeKv();
  const env = { PUSH_KV: kv, ...VAPID };
  const endpoints = [
    "https://fcm.googleapis.com/fcm/send/a",
    "https://fcm.googleapis.com/fcm/send/b",
    "https://fcm.googleapis.com/fcm/send/c",
    "https://web.push.apple.com/x",
  ];
  for (const [index, endpoint] of endpoints.entries()) {
    await subscribe.onRequestPost({
      request: post({ subscription: { endpoint, keys: goodKeys() } }, "172.16.0." + index), env,
    });
  }
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), auth: init.headers.Authorization });
    return new Response("", { status: 201 });
  };
  const response = await send.onRequestPost({
    request: new Request("https://site.test/push/send", {
      method: "POST", headers: { Authorization: "Bearer " + VAPID.PUSH_SEND_TOKEN },
      body: JSON.stringify({ limit: 50 }),
    }), env,
  });
  assert.equal((await response.json()).sent, 4);
  assert.equal(seen.length, 4);
  const byOrigin = new Map();
  for (const row of seen) {
    const origin = new URL(row.url).origin;
    if (!byOrigin.has(origin)) byOrigin.set(origin, new Set());
    byOrigin.get(origin).add(row.auth);
  }
  assert.equal(byOrigin.size, 2, "출처 둘을 준비했다");
  for (const [origin, tokens] of byOrigin) {
    assert.equal(tokens.size, 1, origin + " 에 구독자마다 다시 서명했다");
  }
  // 그리고 출처가 다르면 토큰도 달라야 한다 — aud 를 안 갈면 상대가 401 을 낸다.
  assert.equal(new Set(seen.map(row => row.auth)).size, 2, "출처가 달라도 같은 토큰을 썼다");
}

console.log("push contract: ok");
