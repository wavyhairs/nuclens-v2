// VAPID 키 한 쌍을 만든다. 새 의존성 없음 — node 표준 crypto 만 쓴다.
//
//     node web/tools/gen_vapid_keys.mjs
//
// 낸 값을 어디에 넣는가 — **두 곳으로 갈린다.**
//
//   Cloudflare Pages → Settings → Variables and Secrets
//     VAPID_PUBLIC_KEY   공개 변수여도 된다 — 화면이 /push/key 로 받아 간다
//     PUSH_ADMIN_TOKEN   **Secret** — /push/list 를 여는 열쇠(32자 이상)
//
//   GitHub → Settings → Secrets and variables → Actions
//     VAPID_PRIVATE_KEY  **Secret** — 보내는 쪽(tools/push_notify.py)만 쓴다
//     PUSH_ADMIN_TOKEN   **Secret** — Cloudflare 와 **같은 값**
//     VAPID_SUBJECT      Variable — mailto:<연락처>
//
// 개인키가 엣지가 아니라 GitHub 에 있는 이유: 발송이 파이썬으로 옮겨 왔다
// (2026-09-19). Cloudflare 는 이제 서명하지 않으므로 개인키를 쥘 이유가 없다 —
// 이미 넣어 두었다면 지워도 된다.
//
// **키를 갈면 기존 구독은 전부 죽는다.** 구독은 공개키에 묶여 발급되기 때문이다
// (푸시 서비스가 410 을 내고 발송이 걷는다). 이미 운영 중인 배포라면 여기서
// 새로 만들지 말고, Cloudflare 에 있는 VAPID_PRIVATE_KEY 를 그대로 GitHub 로
// 옮긴다 — pywebpush 는 이 도구가 내는 base64url 32바이트를 그대로 받는다.

import { generateKeyPairSync } from "node:crypto";

const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });

// JWK 로 받으면 x·y·d 가 이미 base64url 이다. 공개키는 비압축 점(0x04 ‖ x ‖ y)
// 65바이트로 합쳐야 브라우저의 applicationServerKey 와 같은 모양이 된다.
const pub = publicKey.export({ format: "jwk" });
const priv = privateKey.export({ format: "jwk" });

const bytes = value => Buffer.from(value, "base64url");
const point = Buffer.concat([Buffer.from([0x04]), bytes(pub.x), bytes(pub.y)]);
if (point.length !== 65) throw new Error(`공개 점이 ${point.length}바이트다 — 65여야 한다`);
if (bytes(priv.d).length !== 32) throw new Error("개인키가 32바이트가 아니다");

const token = Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64url");

console.log(`VAPID_PUBLIC_KEY   ${point.toString("base64url")}`);
console.log(`VAPID_PRIVATE_KEY  ${priv.d}`);
console.log(`VAPID_SUBJECT      mailto:여기에-연락처를-적으세요`);
console.log(`PUSH_ADMIN_TOKEN   ${token}`);
console.log("");
console.log("공개키만 화면에 나갑니다. 나머지 셋은 Secret 으로 넣으세요.");
console.log("PUSH_ADMIN_TOKEN 은 Cloudflare 와 GitHub 양쪽에 같은 값으로 넣습니다.");
console.log("VAPID_PRIVATE_KEY 는 GitHub 에만 넣습니다 — 엣지는 더 이상 서명하지 않습니다.");
