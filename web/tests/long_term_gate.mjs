// 장기 스토리(Beta) 화면의 **두 번째 안전장치**.
// 실행: node web/tests/long_term_gate.mjs  (의존성 없음)
//
// 첫 번째 안전장치는 빌드가 쥐고 있다(thread_web.gate — tests/test_thread_web.py).
// 그 판정은 파일이 만들어지던 순간의 사실이라, **배포 자체가 멈춰 낡은 파일이
// CDN 에 남는 경우**를 잡을 수 없다. 그래서 화면이 유효기한으로 한 번 더 잰다.
//
// 여기서 회귀가 나면 조용하다 — 탭이 떠 있고 그 안에 몇 주 된 이야기가 '최신'인
// 척 서 있는다. 화면을 봐도 그럴듯해 보이므로 눈으로는 안 잡힌다.
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. trend_period_state.mjs 와 같은 방식으로 함수 블록만 잘라 평가한다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const source = readFileSync(
  fileURLToPath(new URL("../public/app.js", import.meta.url)), "utf8");

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`app.js 에 ${name}() 이 없다 — 이름이 바뀌었으면 이 검사도 같이 고쳐라`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && (depth -= 1) === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name}() 블록이 안 닫힌다`);
}

function extractConst(name) {
  const marker = `const ${name} = `;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`app.js 에 ${name} 상수가 없다`);
  const end = source.indexOf(";", start + marker.length);
  return source.slice(start, end + 1);
}

const { longTermVisible, THREAD_CONTRACT } = (new Function(`
  ${extractConst("THREAD_CONTRACT")}
  ${extract("longTermVisible")}
  return { longTermVisible, THREAD_CONTRACT };
`))();

const NOW = Date.parse("2026-09-13T12:00:00+09:00");

function payload(overrides = {}) {
  return {
    version: THREAD_CONTRACT,
    visible: true,
    hide_after: "2026-09-16T12:00:00+09:00",
    threads: [{ thread_id: "thread-a", title: "한빛 1·2호기 계속운전", events: [] }],
    redirects: {},
    ...overrides,
  };
}

let failures = 0;
function check(name, fn) {
  try { fn(); } catch (error) {
    failures += 1;
    console.error(`  ✗ ${name}\n    ${error.message}`);
    return;
  }
  console.log(`  ✓ ${name}`);
}

console.log("장기 스토리 게이트");

check("정상 데이터는 뜬다", () => {
  assert.equal(longTermVisible(payload(), NOW), true);
});

check("빌드가 숨기라고 하면 숨는다", () => {
  assert.equal(longTermVisible(payload({ visible: false }), NOW), false);
});

check("파일이 아예 없으면 숨는다 — 첫 배포 전이 실제로 그렇다", () => {
  assert.equal(longTermVisible(null, NOW), false);
  assert.equal(longTermVisible(undefined, NOW), false);
});

check("목록이 비면 숨는다 — 빈 화면으로 가는 탭을 남기지 않는다", () => {
  assert.equal(longTermVisible(payload({ threads: [] }), NOW), false);
  assert.equal(longTermVisible(payload({ threads: null }), NOW), false);
});

check("유효기한이 지나면 숨는다 — 배포가 멈춘 경우를 잡는 유일한 자리", () => {
  const stale = payload({ hide_after: "2026-09-12T23:59:00+09:00" });
  assert.equal(longTermVisible(stale, NOW), false);
  // 경계 직전은 아직 산다. 하루 1회 판정 빌드가 한 번 밀렸다고 내리면 안 된다.
  assert.equal(longTermVisible(payload({ hide_after: "2026-09-13T12:00:01+09:00" }), NOW), true);
});

check("유효기한이 없거나 깨졌으면 그 검사만 건너뛴다", () => {
  // 빌드가 이미 visible 을 적어 줬다. 시각 하나를 못 읽었다고 화면을 통째로
  // 내리면, 안전장치가 아니라 시각 파싱 버그 하나로 제품이 사라진다.
  assert.equal(longTermVisible(payload({ hide_after: "" }), NOW), true);
  assert.equal(longTermVisible(payload({ hide_after: "언젠가" }), NOW), true);
});

check("모르는 계약 판본은 그리지 않는다", () => {
  assert.equal(longTermVisible(payload({ version: "thread-web-v2" }), NOW), false);
  assert.equal(longTermVisible(payload({ version: undefined }), NOW), false);
});

check("다른 JSON 을 받아도 조용히 그리지 않는다", () => {
  // SPA 폴백이 되살아나면 index.html 이나 엉뚱한 파일이 이 자리에 온다.
  assert.equal(longTermVisible([], NOW), false);
  assert.equal(longTermVisible("<!doctype html>", NOW), false);
});

console.log("장기 스토리 주소 넘김");

// resolveThreadId 는 state 를 전역으로 읽는다 — 잘라낸 블록에 주입한다.
function withRedirects(redirects) {
  return new Function("state", `
    ${extract("resolveThreadId")}
    return resolveThreadId;
  `)({ threads: payload({ redirects }) });
}

check("흡수된 스토리의 옛 주소가 살아 있는 쪽으로 간다", () => {
  const resolve = withRedirects({ old: "mid", mid: "winner" });
  assert.equal(resolve("old"), "winner");
  assert.equal(resolve("winner"), "winner");
  assert.equal(resolve(""), "");
});

check("넘김 사슬이 고리를 이루어도 멈춘다", () => {
  // 무한 루프로 탭을 얼리지 않는 것이 요점이다. 어디서 멈추든 상관없다.
  assert.ok(["a", "b"].includes(withRedirects({ a: "b", b: "a" })("a")));
});

if (failures) {
  console.error(`\n${failures}건 실패`);
  process.exit(1);
}
console.log("\n모두 통과");
