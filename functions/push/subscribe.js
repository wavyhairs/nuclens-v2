// 구독 등록·해지 창구.
//
// 이 자리는 **인터넷에 열린 쓰기**다. 콘솔처럼 비밀번호로 막을 수 없다 —
// 독자가 알림을 켜는 길이기 때문이다. 그래서 막는 대신 **쓸 수 있는 것을
// 좁힌다**: 모양이 맞는 구독 하나, IP 당 시간당 몇 번, 그게 전부다.
//
// 해지에 인증을 두지 않는 이유: 해지할 수 있는 최대치는 "그 endpoint 를 아는
// 사람이 그 endpoint 의 알림을 끈다"이고, endpoint 를 아는 사람은 사실상 그
// 브라우저뿐이다. 반대로 인증을 두면 브라우저가 구독을 이미 버린 뒤(권한 철회)
// 서버에 죽은 구독이 영영 남는다 — 그쪽이 더 나쁘다.

import { json, pushStore, endpointKey, RATE_PREFIX, base64UrlToBytes } from "./_shared.js";

const MAX_BODY_BYTES = 4 * 1024;
const RATE_WINDOW_SECONDS = 3600;
const MAX_WRITES_PER_IP = 20;
// 구독은 만료되지 않지만 죽은 endpoint 는 발송이 걷는다(404·410). TTL 을 길게
// 두는 것은 발송이 오래 멈춘 배포에서 목록이 영원히 자라지 않게 하는 안전선이다.
const SUBSCRIPTION_TTL_SECONDS = 400 * 24 * 3600;

function clientKey(request) {
  return RATE_PREFIX + (request.headers.get("CF-Connecting-IP") || "unknown");
}

async function overRate(kv, key) {
  const used = Number(await kv.get(key)) || 0;
  if (used >= MAX_WRITES_PER_IP) return true;
  await kv.put(key, String(used + 1), { expirationTtl: RATE_WINDOW_SECONDS });
  return false;
}

async function readJson(request) {
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

// 푸시 서비스가 준 endpoint 만 받는다. 임의의 주소를 받아 두면 이 서버가
// 남의 서버로 POST 를 날려 주는 도구가 된다(SSRF).
function validEndpoint(value) {
  let url;
  try { url = new URL(String(value)); } catch { return ""; }
  if (url.protocol !== "https:") return "";
  if (url.href.length > 1024) return "";
  return url.href;
}

// p256dh 는 비압축 점 65바이트, auth 는 16바이트. 지금 보내는 알림에는 본문이
// 없어 둘 다 쓰지 않지만, **저장해 둔다** — 나중에 본문 암호화를 붙일 때
// 구독자 전원에게 다시 켜 달라고 할 수는 없다.
function validKeys(keys) {
  if (!keys || typeof keys !== "object") return null;
  try {
    const p256dh = base64UrlToBytes(String(keys.p256dh || ""));
    const auth = base64UrlToBytes(String(keys.auth || ""));
    if (p256dh.length !== 65 || p256dh[0] !== 0x04 || auth.length !== 16) return null;
    return { p256dh: String(keys.p256dh), auth: String(keys.auth) };
  } catch {
    return null;
  }
}

export async function onRequestPost({ request, env }) {
  const kv = pushStore(env);
  if (!kv) return json({ error: "push_store_missing" }, 503);
  const body = await readJson(request);
  const subscription = body && body.subscription;
  if (!subscription) return json({ error: "bad_request" }, 400);
  const endpoint = validEndpoint(subscription.endpoint);
  if (!endpoint) return json({ error: "bad_endpoint" }, 400);
  const keys = validKeys(subscription.keys);
  if (!keys) return json({ error: "bad_keys" }, 400);

  const key = await endpointKey(endpoint);
  // 이미 있는 구독을 다시 켜는 것(브라우저 재시작·재구독)은 한도에 세지 않는다.
  const existing = await kv.get(key);
  if (!existing && await overRate(kv, clientKey(request))) {
    return json({ error: "too_many_requests" }, 429);
  }
  await kv.put(key, JSON.stringify({
    endpoint,
    keys,
    created_at: existing ? JSON.parse(existing).created_at : new Date().toISOString(),
    seen_at: new Date().toISOString(),
  }), { expirationTtl: SUBSCRIPTION_TTL_SECONDS });
  return json({ ok: true });
}

export async function onRequestDelete({ request, env }) {
  const kv = pushStore(env);
  if (!kv) return json({ error: "push_store_missing" }, 503);
  const body = await readJson(request);
  const endpoint = validEndpoint(body && body.endpoint);
  // 이미 없는 구독을 지우는 것도 성공이다 — 화면이 재시도할 이유를 만들지 않는다.
  if (!endpoint) return json({ ok: true });
  await kv.delete(await endpointKey(endpoint));
  return json({ ok: true });
}
