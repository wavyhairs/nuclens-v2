// 웹 푸시 창구가 함께 쓰는 것들 — 저장소 선택, 인코딩, 인증 비교.
//
// 엣지는 더 이상 보내지 않는다
// ----------------------------
// 예전엔 여기에 VAPID 서명(P-256 ECDSA)과 발송이 들어 있었다. 런타임 의존성을
// 셋으로 잠가 둔 탓에 `cryptography` 를 들이지 않으려고 서명을 Worker 의
// WebCrypto 로 민 것이었는데, 대가가 컸다: 본문을 실으려면 구독마다 ECDH+HKDF+
// AES-GCM 을 돌려야 하는데 그 비용이 Worker 요청당 CPU 예산(무료 10ms) 위에
// 구독자 수만큼 얹힌다. 그래서 **빈 알림**을 보내고 서비스워커가 다시
// `/data/push.json` 을 읽는 우회가 붙었다.
//
// 지금은 파이썬(`tools/push_notify.py`)이 pywebpush 로 직접 보낸다 — 러너에는
// CPU 예산이 없고, 암호 구현은 이미 검증된 라이브러리 몫이다. 엣지에 남은 일은
// 셋뿐이다: 구독을 받고(subscribe), 목록을 내주고(list), 공개키를 알려 준다(key).
//
// 구독 저장소
// -----------
// KV 하나면 된다. 전용 바인딩(`PUSH_KV`)이 있으면 그것을 쓰고, 없으면 콘솔이
// 이미 쓰고 있는 `ADMIN_KV` 를 빌린다 — 운영자가 새로 설정할 것을 만들지 않기
// 위해서다. 키에 `push:` 접두사를 붙여 콘솔 키와 섞이지 않는다.

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

// 같은 길이일 때 바이트마다 조기 종료하지 않는다 — 콘솔 미들웨어와 같은 조심성.
export function timingSafeEqual(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

const encoder = new TextEncoder();

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

// 공개키는 비밀이 아니다 — 구독을 **이 서버로** 묶는 식별자라서 화면이 그대로
// 들고 다닌다(/push/key). 개인키는 이제 엣지에 없다: 발송기만 쓴다(GitHub Secret).
export function vapidPublicKey(env) {
  return String((env && env.VAPID_PUBLIC_KEY) || "").trim();
}
