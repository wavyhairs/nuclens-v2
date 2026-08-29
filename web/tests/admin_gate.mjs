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
import { readFile } from "node:fs/promises";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");

// Windows 에서는 절대 경로를 그대로 import 할 수 없다 — file:// URL 로 바꾼다.
const {
  hashPassword, verifyPassword, passwordProblem,
  signSession, sessionIsValid, safeNext, PBKDF2_ITERATIONS,
  bootstrapPassword, BOOTSTRAP_ENV_KEY,
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

// ── ①-b 첫 비밀번호는 코드에 없다 ──────────────────────────────────────────
//
// 저장소를 공개로 돌리면 코드에 박힌 기본 비밀번호는 '추측할 값'이 아니라
// '읽으면 되는 값'이 된다. 그래서 부트스트랩 값도 배포 환경에서 받는다.
// 여기서 지키는 것은 **설정이 없을 때 기본값으로 되돌아가지 않는다**는 것이다.
const middlewareSource = await readFile(
  path.join(root, "functions", "admin", "_middleware.js"), "utf8");
assert.equal(/const\s+BOOTSTRAP_PASSWORD\s*=/.test(middlewareSource), false,
  "부트스트랩 비밀번호가 다시 코드 상수로 돌아왔다");
assert.equal(/=\s*["'`]0000["'`]/.test(middlewareSource), false,
  "0000 이 다시 값으로 박혔다");

assert.equal(BOOTSTRAP_ENV_KEY, "ADMIN_BOOTSTRAP_PASSWORD");
// 설정이 없으면 빈 문자열 — 부트스트랩 경로가 아무 값도 받지 않는다.
for (const env of [undefined, {}, { ADMIN_BOOTSTRAP_PASSWORD: "" },
                   { ADMIN_BOOTSTRAP_PASSWORD: "0000" },
                   { ADMIN_BOOTSTRAP_PASSWORD: "short7" },
                   { ADMIN_BOOTSTRAP_PASSWORD: 12345678 }]) {
  assert.equal(bootstrapPassword(env), "",
    `설정이 없거나 약한데 부트스트랩 값이 생겼다: ${JSON.stringify(env)}`);
}
assert.equal(bootstrapPassword({ ADMIN_BOOTSTRAP_PASSWORD: "a-long-random-value" }),
  "a-long-random-value", "제대로 설정한 값이 무시된다");

// ── ② 새 비밀번호 규칙 ─────────────────────────────────────────────────────
//
// 첫 비밀번호는 부트스트랩이지 비밀번호가 아니다. 그 값으로 되돌릴 수 있으면
// 강제 변경 화면이 아무것도 강제하지 않은 것이 된다.
assert.equal(passwordProblem("a-long-random-value", "a-long-random-value",
                             "a-long-random-value") !== "", true,
  "첫 비밀번호로 되돌릴 수 있다");
assert.equal(passwordProblem("a-long-random-value", "a-long-random-value",
                             "other-bootstrap"), "",
  "다른 값인데 첫 비밀번호로 오인해 거부한다");
// 길이 하한은 부트스트랩 설정과 무관하게 언제나 선다 — 0000 은 여기서 걸린다.
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

// ── ⑤ 쓰기 창구의 입력 검증 ────────────────────────────────────────────────
//
// 콘솔이 쓰기를 얻었으므로(2026-08-16) 신뢰 경계가 하나 늘었다. 화면을 믿으면
// 안 된다 — 저장은 되는데 파이프라인이 못 읽는 항목(축이 빈 학습 규칙, http 가
// 아닌 피드 주소)이 KV 에 남으면 관리자는 자기가 무엇을 고쳤는지 모르는 채로
// 다음 수집을 기다린다. 그 실패는 조용하다.
const { normalizeEntry, sameJudgment } = await import(
  pathToFileURL(path.join(root, "functions", "admin", "api", "overrides.js")).href);

assert.ok(normalizeEntry({ kind: "듣도보도못한종류" }).error, "모르는 종류가 통과한다");

// 학습 규칙: 한쪽 축만으로는 아무것도 가르지 못한다.
assert.ok(normalizeEntry({ kind: "learned_rule", left_terms: ["고리"], right_terms: [] }).error,
  "한쪽 축만 있는 규칙이 저장된다");
// 양쪽에 같은 말이 있으면 rule_conflict 가 영원히 침묵한다 — 저장해도 안 듣는다.
assert.ok(normalizeEntry({
  kind: "learned_rule", left_terms: ["원전", "고리"], right_terms: ["원전"],
}).error, "겹치는 축이 저장된다");
const rule = normalizeEntry({
  kind: "learned_rule", left_terms: ["고리 2호기"], right_terms: ["한빛 3호기"],
}).entry;
assert.equal(rule.label, "고리 2호기 ↔ 한빛 3호기", "라벨을 자동으로 못 만든다");

// 수집원: 수집기가 열 수 있는 주소만.
assert.ok(normalizeEntry({ kind: "feed_add", url: "file:///etc/passwd" }).error);
assert.ok(normalizeEntry({ kind: "feed_add", url: "javascript:alert(1)" }).error);
const feed = normalizeEntry({ kind: "feed_add", url: "https://www.example.com/feed" }).entry;
assert.equal(feed.domain_label, "example.com", "도메인 라벨을 못 만든다");

// 출처 등급: 파이프라인이 아는 값만. 모르는 값이 들어가면 화면 배지가 빈다.
assert.ok(normalizeEntry({ kind: "tier_upsert", domain: "a.com", tier: 9 }).error);
assert.ok(normalizeEntry({
  kind: "tier_upsert", domain: "a.com", tier: 1, evidence_role: "아무거나",
}).error, "근거 역할 값이 검증되지 않는다");
assert.ok(normalizeEntry({ kind: "tier_upsert", domain: "그냥이름", tier: 1 }).error);

// 분리: 같은 기사끼리는 가를 수 없다.
assert.ok(normalizeEntry({ kind: "story_split", left_hash: "a", right_hash: "a" }).error);
assert.ok(normalizeEntry({ kind: "story_split", left_hash: "a" }).error);

// 사건군 나누기: 쌍이 아니라 선이다. 한쪽이 비면 가를 것이 없고, 같은 기사가
// 양쪽에 서면 그 기사는 자기 자신과 다른 사건이 된다.
assert.ok(normalizeEntry({
  kind: "issue_group_split", left_hashes: ["k1", "k2"], right_hashes: [],
}).error, "한쪽이 빈 나누기가 저장된다");
assert.ok(normalizeEntry({
  kind: "issue_group_split", left_hashes: ["k1", "k2"], right_hashes: ["k2"],
}).error, "같은 기사가 양쪽에 선 나누기가 저장된다");
const line = normalizeEntry({
  kind: "issue_group_split", issue_id: "issue-k1",
  left_hashes: ["k1", "k2"], right_hashes: ["m1"],
  left_titles: ["계속운전 심의", "계속운전 결론"], right_titles: ["수출 업무협약"],
  note: "심사와 협약은 다른 사건",
}).entry;
assert.deepEqual(line.left_hashes, ["k1", "k2"]);
assert.deepEqual(line.right_titles, ["수출 업무협약"],
  "제목이 안 실리면 '내 판정' 목록이 16진수 두 줄이 된다");
// 순서만 다른 같은 선을 두 번 저장하면 쌍이 두 벌로 늘고 절반만 지워진다.
assert.equal(sameJudgment(line, { ...line, left_hashes: ["k2", "k1"] }), true);
assert.equal(sameJudgment(line, { left_hashes: ["m1"], right_hashes: ["k1", "k2"], kind: line.kind }),
  true, "좌우만 뒤집은 같은 선이 다른 판정으로 들어간다");
assert.equal(sameJudgment(line, { ...line, right_hashes: ["m2"] }), false);

// 같은 판정을 두 번 누르면 두 줄이 생기고, 지울 때 하나만 지워 절반이 남는다.
assert.equal(sameJudgment(
  { kind: "keyword_add", group: "정책", value: "SMR" },
  { kind: "keyword_add", group: "정책", value: "SMR" }), true);
assert.equal(sameJudgment(
  { kind: "keyword_add", group: "정책", value: "SMR" },
  { kind: "keyword_add", group: "기술", value: "SMR" }), false);

console.log("admin gate: 해시·비밀번호 규칙·세션·리다이렉트·쓰기 검증 통과");
