// 웹 푸시 창구가 함께 쓰는 것들 — 저장소 선택, 인코딩, VAPID 서명.
//
// 왜 엣지에서 보내는가
// --------------------
// 보내는 쪽은 매일 아침 워크플로(파이썬)다. 그런데 웹 푸시는 P-256 ECDSA 서명을
// 요구하고, 이 저장소의 런타임 의존성은 셋(requests · google-genai · feedparser)
// 으로 잠겨 있다 — 서명 하나 때문에 `cryptography` 를 들이면 그 계약이 깨진다.
// Worker 런타임에는 WebCrypto 가 이미 있으므로 서명과 발송을 엣지에 두고,
// 파이썬은 "보내라"고 부르기만 한다(tools/push_notify.py).
//
// 구독 저장소
// -----------
// KV 하나면 된다. 전용 바인딩(`PUSH_KV`)이 있으면 그것을 쓰고, 없으면 콘솔이
// 이미 쓰고 있는 `ADMIN_KV` 를 빌린다 — 운영자가 새로 설정할 것을 만들지 않기
// 위해서다. 키에 `push:` 접두사를 붙여 콘솔 키와 섞이지 않는다.
//
// **구독은 비밀이 아니지만 사생활이다.** endpoint 는 그 브라우저를 특정하는
// 주소다. 그래서 목록을 읽는 창구는 만들지 않는다 — 쓰기(subscribe)와
// 보내기(send, 토큰 필요)만 있고, 어디에도 "구독자를 보여 줘"는 없다.

export const KEY_PREFIX = "push:sub:";
export const RATE_PREFIX = "push:rate:";

export function pushStore(env) {
  const kv = (env && env.PUSH_KV) || (env && env.ADMIN_KV) || null;
  return kv && typeof kv.get === "function" ? kv : null;
}

export function json(payload, status = 200, extra = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
      ...extra,
    },
  });
}

const encoder = new TextEncoder();

export function bytesToBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function base64UrlToBytes(value) {
  const normalized = String(value).replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

export async function endpointKey(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(endpoint));
  return KEY_PREFIX + [...new Uint8Array(digest)]
    .map(byte => byte.toString(16).padStart(2, "0")).join("");
}

// ── VAPID ──────────────────────────────────────────────────────────────────
//
// 공개키는 비밀이 아니다 — 구독을 **이 서버로** 묶는 식별자라서 화면이 그대로
// 들고 다닌다(/push/key). 개인키만 Secret 이다.
//
// 공개키는 비압축 점 65바이트(0x04 ‖ x ‖ y), 개인키는 스칼라 32바이트, 둘 다
// base64url 이다. JWK 로 옮겨야 WebCrypto 가 받는다 — x·y 를 공개키에서 갈라
// 쓰므로 운영자가 넣을 값은 두 개로 끝난다(web/tools/gen_vapid_keys.mjs 가 낸다).

export function vapidConfig(env) {
  const publicKey = String((env && env.VAPID_PUBLIC_KEY) || "").trim();
  const privateKey = String((env && env.VAPID_PRIVATE_KEY) || "").trim();
  const subject = String((env && env.VAPID_SUBJECT) || "").trim();
  return { publicKey, privateKey, subject };
}

export async function importVapidKey(publicKey, privateKey) {
  const point = base64UrlToBytes(publicKey);
  if (point.length !== 65 || point[0] !== 0x04) {
    throw new Error("VAPID_PUBLIC_KEY 는 비압축 점 65바이트(base64url)여야 한다");
  }
  const scalar = base64UrlToBytes(privateKey);
  if (scalar.length !== 32) {
    throw new Error("VAPID_PRIVATE_KEY 는 32바이트(base64url)여야 한다");
  }
  return crypto.subtle.importKey("jwk", {
    kty: "EC",
    crv: "P-256",
    x: bytesToBase64Url(point.slice(1, 33)),
    y: bytesToBase64Url(point.slice(33, 65)),
    d: bytesToBase64Url(scalar),
    ext: false,
  }, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
}

// 토큰은 **푸시 서비스 출처(aud)마다** 하나다. 구독자마다 새로 서명하면 서명이
// 구독자 수만큼 늘어 Worker CPU 예산(무료 플랜 요청당 10ms)을 그대로 먹는다.
export async function vapidToken(key, audience, subject, ttlSeconds = 12 * 3600) {
  const head = bytesToBase64Url(new TextEncoder().encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = bytesToBase64Url(new TextEncoder().encode(JSON.stringify({
    aud: audience,
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
    sub: subject,
  })));
  const signingInput = `${head}.${body}`;
  // WebCrypto 의 ECDSA 출력은 r‖s 64바이트 raw 라서 JWS ES256 이 요구하는
  // 형식 그대로다 — DER 로 감싸 오지 않는다.
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" }, key, new TextEncoder().encode(signingInput),
  );
  return `${signingInput}.${bytesToBase64Url(new Uint8Array(signature))}`;
}
