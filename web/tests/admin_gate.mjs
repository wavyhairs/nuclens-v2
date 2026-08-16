// 운영 콘솔 자물쇠 회귀 — 배포 경로에서 돈다.
//
// 파이썬 테스트가 못 잡는 자리다. 자물쇠는 엣지에서 도는 JS 이고, 해시·세션·
// 비밀번호 규칙이 전부 그 안에 있다. 여기가 조용히 어긋나면 증상은 둘 중 하나다 —
// 맞는 비밀번호로도 못 들어가거나, 반대로 아무거나 통과한다. 어느 쪽이든 배포
// 뒤에야 알게 된다.
//
//     node web/tests/admin_gate.mjs

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");

// Windows 에서는 절대 경로를 그대로 import 할 수 없다 — file:// URL 로 바꾼다.
const {
  hashPassword, verifyPassword, passwordProblem,
  signSession, sessionIsValid, safeNext, PBKDF2_ITERATIONS,
} = await import(pathToFileURL(path.join(root, "functions", "admin", "_middleware.js")).href);

// ── ① 해시 왕복 ────────────────────────────────────────────────────────────
const encoded = await hashPassword("a-real-password");
assert.match(encoded, /^pbkdf2-sha256\$\d+\$[\w-]+\$[\w-]+$/, "저장 형식이 깨졌다");
assert.equal(await verifyPassword("a-real-password", encoded), true,
  "방금 만든 해시를 자기가 검증하지 못한다");
assert.equal(await verifyPassword("a-real-passwore", encoded), false);
assert.equal(await verifyPassword("", encoded), false);

// 같은 비밀번호라도 salt 가 달라 매번 다른 문자열이어야 한다.
assert.notEqual(encoded, await hashPassword("a-real-password"), "salt 가 고정돼 있다");

// 반복 횟수는 무료 플랜 Worker 의 CPU 예산(10ms) 안에 있어야 한다. 값을 크게 올리면
// 증상은 '로그인 화면이 오류를 뱉는다' 하나뿐이라 원인을 찾기 어렵다.
assert.ok(PBKDF2_ITERATIONS >= 1000 && PBKDF2_ITERATIONS <= 20000,
  `반복 횟수가 CPU 예산 밖이다: ${PBKDF2_ITERATIONS}`);

// 형식이 깨진 값으로 통과하면 안 된다 — 저장 값 오염이 곧 무인증이 되는 길.
for (const bad of ["", "hunter2", "pbkdf2-sha256$5000$only-three", "md5$5000$a$b",
                   "pbkdf2-sha256$10$c2FsdA$c2FsdA", "pbkdf2-sha256$abc$c2FsdA$c2FsdA",
                   null, undefined]) {
  assert.equal(await verifyPassword("a-real-password", bad), false,
    `깨진 해시 문자열이 통과했다: ${bad}`);
}

// ── ② 새 비밀번호 규칙 ─────────────────────────────────────────────────────
//
// 0000 은 부트스트랩이지 비밀번호가 아니다. 다시 0000 으로 바꿀 수 있으면 강제
// 변경 화면이 아무것도 강제하지 않은 것이 된다.
assert.equal(passwordProblem("0000", "0000") !== "", true, "0000 으로 되돌릴 수 있다");
assert.equal(passwordProblem("short7", "short7") !== "", true, "8자 하한이 없다");
assert.equal(passwordProblem("longenough1", "longenough2") !== "", true, "확인 입력이 달라도 통과한다");
assert.equal(passwordProblem("", "") !== "", true);
assert.equal(passwordProblem("longenough1", "longenough1"), "", "정상 비밀번호가 거부된다");

// ── ③ 세션 쿠키 ────────────────────────────────────────────────────────────
const secret = "test-session-secret";
const future = Math.floor(Date.now() / 1000) + 600;
const token = await signSession(secret, future);
assert.equal(await sessionIsValid(token, secret), true);
assert.equal(await sessionIsValid(token, "other-secret"), false,
  "서명 키를 갈아 끼워도 옛 세션이 산다 — 비밀번호 변경이 다른 기기를 못 끊는다");
assert.equal(await sessionIsValid(token, ""), false, "서명 키가 비어도 통과한다");
assert.equal(await sessionIsValid("", secret), false);
assert.equal(await sessionIsValid("v1.9999999999.deadbeef", secret), false,
  "서명 없이 만료만 고친 쿠키가 통과한다");

const past = Math.floor(Date.now() / 1000) - 1;
assert.equal(await sessionIsValid(await signSession(secret, past), secret), false,
  "만료된 세션이 통과한다");

// 만료 시각을 늘려 쓰면 서명이 깨져야 한다.
const [, , signature] = token.split(".");
assert.equal(await sessionIsValid(`v1.${future + 3600}.${signature}`, secret), false,
  "만료 시각 변조가 통과한다");

// ── ④ 열린 리다이렉트 ──────────────────────────────────────────────────────
assert.equal(safeNext("/admin/?panel=config"), "/admin/?panel=config");
assert.equal(safeNext("https://evil.example/admin"), "/admin/");
assert.equal(safeNext("//evil.example/admin"), "/admin/");
assert.equal(safeNext("/"), "/admin/");
assert.equal(safeNext(null), "/admin/");

console.log("admin gate: 해시·비밀번호 규칙·세션·리다이렉트 검사 통과");
