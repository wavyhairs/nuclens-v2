// 발송 창구 — 아침 워크플로가 부른다.
//
// 본문을 싣지 않는다
// ------------------
// 웹 푸시로 **본문**을 보내려면 구독마다 ECDH 키합의 + HKDF + AES-GCM 을 돌려야
// 한다(RFC 8291). 두 가지가 걸린다. 하나는 Worker 무료 플랜의 요청당 CPU 예산
// 10ms 인데, 그 암호화는 구독자 수에 비례해 붙는다(VAPID 서명은 출처당 한 번이라
// 비례하지 않는다). 다른 하나는 **여기서 끝까지 검증할 수 없다**는 것 — 실제
// 구독과 실제 키 없이는 "보냈다"까지만 확인되고 "열렸다"는 확인되지 않는다.
//
// 그래서 빈 알림을 보내고, 서비스워커가 받는 순간 `/data/push.json` 을 읽어
// 오늘의 제목을 붙인다(web/public/sw.js). 결과는 같고, 검증할 수 없는 암호
// 구현을 배포 경로에 두지 않는다. 구독 저장에는 p256dh·auth 를 이미 넣어 두므로
// 나중에 본문을 실어도 구독자에게 다시 켜 달라고 하지 않는다.
//
// 한 번에 다 보내지 않는다
// ------------------------
// KV 목록을 커서로 끊어 받고, 부르는 쪽이 커서가 빌 때까지 다시 부른다
// (tools/push_notify.py). 한 요청의 일이 구독자 수와 함께 무한정 자라지 않게
// 하는 것이 목적이다.

import {
  json, pushStore, KEY_PREFIX, importVapidKey, vapidToken, vapidConfig,
} from "./_shared.js";

const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 200;
const DEFAULT_TTL = 6 * 3600;   // 아침 알림은 반나절 지나면 보낼 이유가 없다

// 같은 길이일 때 바이트마다 조기 종료하지 않는다 — 콘솔 미들웨어와 같은 조심성.
function timingSafeEqual(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

function authorized(request, env) {
  const expected = String((env && env.PUSH_SEND_TOKEN) || "");
  if (expected.length < 16) return false;   // 짧은 토큰은 설정 안 된 것으로 본다
  const header = String(request.headers.get("Authorization") || "");
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  return timingSafeEqual(supplied, expected);
}

export async function onRequestPost({ request, env }) {
  if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);

  const kv = pushStore(env);
  if (!kv) return json({ error: "push_store_missing" }, 503);
  const { publicKey, privateKey, subject } = vapidConfig(env);
  if (!publicKey || !privateKey || !subject) return json({ error: "vapid_not_configured" }, 503);

  let body = {};
  try { body = await request.json(); } catch { body = {}; }
  const limit = Math.min(Number(body.limit) || DEFAULT_LIMIT, MAX_LIMIT);
  const ttl = Math.min(Math.max(Number(body.ttl) || DEFAULT_TTL, 60), 24 * 3600);
  // Topic 은 base64url 32자 이하여야 한다(RFC 8030). 같은 주제의 못 받은 알림은
  // 뒤엣것 하나로 접힌다 — 폰을 이틀 꺼 뒀다가 켜면 알림 둘이 아니라 하나다.
  const topic = /^[A-Za-z0-9_-]{1,32}$/.test(String(body.topic || "")) ? String(body.topic) : "";

  const listing = await kv.list({ prefix: KEY_PREFIX, limit, cursor: body.cursor || undefined });
  const subscriptions = [];
  for (const entry of listing.keys) {
    const raw = await kv.get(entry.name);
    if (!raw) continue;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.endpoint) subscriptions.push({ key: entry.name, endpoint: parsed.endpoint });
    } catch { await kv.delete(entry.name); }
  }

  // 서명은 **푸시 서비스 출처마다 한 번**이다. 구독자마다 서명하면 그 비용이
  // 구독자 수에 비례해 붙는다 — 이 함수가 CPU 예산을 넘기는 유일한 길이다.
  const signingKey = subscriptions.length ? await importVapidKey(publicKey, privateKey) : null;
  // 캐시에 담는 것은 **약속(Promise)이지 결과가 아니다.** 아래 발송은 전부
  // 동시에 출발하므로, 결과를 담으면 모두가 "아직 없다"를 보고 각자 서명한다 —
  // 구독자 수만큼 서명하는 그 상태가 정확히 이 캐시가 막으려던 것이다
  // (web/tests/push_contract.mjs 가 실제로 잡았다: fcm 하나에 토큰 3개).
  const tokens = new Map();
  function tokenFor(endpoint) {
    const audience = new URL(endpoint).origin;
    if (!tokens.has(audience)) tokens.set(audience, vapidToken(signingKey, audience, subject));
    return tokens.get(audience);
  }

  let sent = 0;
  let pruned = 0;
  const failures = [];
  const results = await Promise.allSettled(subscriptions.map(async subscription => {
    const token = await tokenFor(subscription.endpoint);
    const response = await fetch(subscription.endpoint, {
      method: "POST",
      headers: {
        Authorization: `vapid t=${token}, k=${publicKey}`,
        TTL: String(ttl),
        Urgency: "normal",
        "Content-Length": "0",
        ...(topic ? { Topic: topic } : {}),
      },
    });
    // 404·410 은 "이 구독은 이제 없다"는 뜻이다. 남겨 두면 매일 같은 실패를
    // 다시 사고, 목록은 실제 독자 수를 말하지 않게 된다.
    if (response.status === 404 || response.status === 410) {
      await kv.delete(subscription.key);
      return "pruned";
    }
    if (!response.ok) throw new Error(`${response.status}`);
    return "sent";
  }));
  for (const result of results) {
    if (result.status === "rejected") failures.push(String(result.reason).slice(0, 60));
    else if (result.value === "pruned") pruned += 1;
    else sent += 1;
  }

  return json({
    ok: true,
    sent,
    pruned,
    failed: failures.length,
    // 실패 사유는 몇 개만 — 로그가 구독자 수만큼 길어지면 아무도 안 읽는다.
    reasons: [...new Set(failures)].slice(0, 5),
    cursor: listing.list_complete ? "" : listing.cursor,
    done: Boolean(listing.list_complete),
  });
}
