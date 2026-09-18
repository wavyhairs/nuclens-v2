// VAPID 서명 계약 — 엣지가 만드는 토큰을 여기서 실제로 검증한다.
//
//     node web/tests/vapid_token.mjs
//
// 왜 필요한가
// -----------
// 이 서명이 틀리면 증상이 **조용하다.** 푸시 서비스가 401 을 내고, 발송 로그에
// 실패 숫자만 하나 늘고, 사용자는 알림이 안 온다는 것만 안다. 코드 어디에도
// 예외가 없다. 그래서 "보냈다"가 아니라 **"서명이 맞다"** 를 여기서 잠근다.
//
// 검증에 쓰는 것은 노드의 WebCrypto 다 — 만든 쪽(Worker)과 다른 구현으로 풀어야
// 내가 같은 실수를 두 번 해도 둘 다 통과하는 일이 없다.

import assert from "node:assert/strict";
import { generateKeyPairSync, webcrypto } from "node:crypto";
import { importVapidKey, vapidToken, bytesToBase64Url, base64UrlToBytes } from "../../functions/push/_shared.js";

// 엣지에서는 전역 crypto 가 WebCrypto 다. 노드에서도 같게 맞춰 준다.
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
const pub = publicKey.export({ format: "jwk" });
const priv = privateKey.export({ format: "jwk" });
const point = new Uint8Array(65);
point[0] = 0x04;
point.set(base64UrlToBytes(pub.x), 1);
point.set(base64UrlToBytes(pub.y), 33);
const publicB64 = bytesToBase64Url(point);

// ── 운영자가 넣는 값의 모양을 못 박는다 ──────────────────────────────────
// 여기서 안 막으면 잘못 붙여 넣은 키가 배포까지 가고, 증상은 위의 '조용한 401'이다.
await assert.rejects(() => importVapidKey("bm9wZQ", priv.d), /65바이트/);
await assert.rejects(() => importVapidKey(publicB64, "c2hvcnQ"), /32바이트/);

const key = await importVapidKey(publicB64, priv.d);
const token = await vapidToken(key, "https://fcm.googleapis.com", "mailto:ops@example.test");

// ── 모양 ──────────────────────────────────────────────────────────────────
const [head, body, signature] = token.split(".");
assert.equal(token.split(".").length, 3, "JWS 는 세 도막이다");
const decode = part => JSON.parse(new TextDecoder().decode(base64UrlToBytes(part)));
assert.deepEqual(decode(head), { typ: "JWT", alg: "ES256" });
const claims = decode(body);
assert.equal(claims.aud, "https://fcm.googleapis.com", "aud 는 푸시 서비스의 **출처**다 — endpoint 전체가 아니다");
assert.equal(claims.sub, "mailto:ops@example.test");
assert.ok(claims.exp > Math.floor(Date.now() / 1000), "exp 가 과거면 즉시 401 이다");
assert.ok(claims.exp - Math.floor(Date.now() / 1000) <= 24 * 3600,
  "RFC 8292 는 24시간을 넘는 exp 를 금한다");

// ── 서명 ──────────────────────────────────────────────────────────────────
// ES256 의 서명은 r‖s 64바이트다. DER 로 감싸 나오면 여기서 길이부터 틀린다.
assert.equal(base64UrlToBytes(signature).length, 64, "ES256 서명은 raw r‖s 64바이트다");

const verifier = await webcrypto.subtle.importKey(
  "jwk", { kty: "EC", crv: "P-256", x: pub.x, y: pub.y },
  { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"],
);
const ok = await webcrypto.subtle.verify(
  { name: "ECDSA", hash: "SHA-256" }, verifier,
  base64UrlToBytes(signature), new TextEncoder().encode(`${head}.${body}`),
);
assert.ok(ok, "서명이 공개키로 풀리지 않는다");

// 서명한 내용이 한 글자만 달라도 깨져야 한다 — '검증이 늘 참'인 사고를 막는다.
const tampered = await webcrypto.subtle.verify(
  { name: "ECDSA", hash: "SHA-256" }, verifier,
  base64UrlToBytes(signature), new TextEncoder().encode(`${head}.${body}x`),
);
assert.equal(tampered, false, "아무 내용에나 참을 내면 검증이 아니다");

// ── 토큰은 출처마다 하나다 ────────────────────────────────────────────────
// 구독자마다 서명하면 그 비용이 구독자 수에 비례해 붙는다(Worker CPU 예산).
const again = await vapidToken(key, "https://fcm.googleapis.com", "mailto:ops@example.test");
assert.equal(again.split(".").slice(0, 2).join("."), `${head}.${body}`,
  "같은 초에 같은 출처면 서명 대상이 같아야 한다");

console.log("vapid token: ok");
