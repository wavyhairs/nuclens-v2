// 운영 콘솔 렌더 스모크 — 실제 빌드 산출물로 화면을 한 번 그려 본다.
//
// 파이썬 테스트가 못 잡는 자리다. 저쪽은 JSON 의 모양을 보고, 여기는 그 JSON 을
// 받은 admin.js 가 **끝까지 그리는가**를 본다. 콘솔이 중간에 예외를 던지면 증상은
// 흰 화면 하나뿐이고 브라우저 콘솔을 열기 전에는 원인이 안 보인다 — 그리고 관리자는
// 그걸 '데이터가 없다'로 읽는다(독자 앱에서 2026-08-01 에 실제로 겪은 실패).
//
// 브라우저는 안 띄운다. 필요한 것은 DOM 의 겉모습뿐이라 최소한만 흉내 내고,
// fetch 는 빌드가 방금 쓴 파일로 답한다. 3초짜리라 배포 경로에 둘 수 있다.
//
//     node web/tests/admin_render.mjs

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(here, "..", "public");
const adminDir = path.join(publicDir, "admin");
const dataDir = path.join(adminDir, "data");

for (const name of ["merges.json", "config.json"]) {
  if (!fs.existsSync(path.join(dataDir, name))) {
    console.error(`admin render: ${name} 이 없다 — 먼저 python web/build_data.py`);
    process.exit(1);
  }
}

// ── 최소 DOM ───────────────────────────────────────────────────────────────
//
// 콘솔이 건드리는 것만 흉내 낸다. 진짜 DOM 이 아니라 '있는 척'이므로 레이아웃은
// 검증하지 못한다 — 여기서 잡는 것은 **예외**와 **빈 화면**이다.

const written = new Map();          // id → 마지막으로 그린 HTML
const listeners = new Map();

function makeElement(id) {
  return {
    id,
    hidden: false,
    className: "",
    textContent: "",
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute() {},
    addEventListener(type, handler) { listeners.set(`${id}:${type}`, handler); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {},
    set innerHTML(value) { written.set(id, String(value)); },
    get innerHTML() { return written.get(id) || ""; },
  };
}

const elements = new Map();
function byId(id) {
  if (!elements.has(id)) elements.set(id, makeElement(id));
  return elements.get(id);
}

const document = {
  getElementById: byId,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
};

const responses = {
  "/admin/data/merges.json": () => fs.readFileSync(path.join(dataDir, "merges.json"), "utf8"),
  "/admin/data/config.json": () => fs.readFileSync(path.join(dataDir, "config.json"), "utf8"),
  // KV 는 로컬에 없다. 판정이 하나도 없는 상태가 콘솔의 기본이므로 그것으로 답한다.
  "/admin/api/overrides": () => JSON.stringify({ version: 1, rev: 0, updated_at: "", entries: [] }),
};

const requested = [];
async function fakeFetch(input) {
  const url = new URL(String(input), "https://console.test");
  requested.push(url.pathname);
  const body = responses[url.pathname];
  if (!body) return { ok: false, status: 404, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => JSON.parse(body()) };
}

const sandbox = {
  document,
  fetch: fakeFetch,
  console,
  crypto: webcrypto,
  URL,
  URLSearchParams,
  setTimeout,
  clearTimeout,
  location: { href: "https://console.test/admin/", search: "" },
  history: { state: null, replaceState() {} },
  confirm: () => true,
  prompt: () => "테스트 사유",
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;

const script = fs.readFileSync(path.join(adminDir, "admin.js"), "utf8");
vm.createContext(sandbox);
new vm.Script(script, { filename: "admin.js" }).runInContext(sandbox);

// start() 는 비동기다 — fetch 두 번과 그 뒤의 렌더가 끝날 때까지 기다린다.
await new Promise(resolve => setTimeout(resolve, 50));

// ── 검사 ───────────────────────────────────────────────────────────────────

assert.deepEqual(
  [...new Set(requested)].sort(),
  ["/admin/api/overrides", "/admin/data/config.json", "/admin/data/merges.json"],
  `콘솔이 예상 밖의 경로를 불렀다: ${[...new Set(requested)].join(", ")}`,
);

// 상태 줄이 오류 문구로 끝나면 데이터를 못 읽은 것이다.
const status = written.get("adminStatus") || "";
assert.ok(!status.includes("불러오지 못했습니다"), `데이터 로드 실패: ${status}`);

// 화면 셋이 전부 무언가를 그려야 한다. 조용히 빈 칸이 되는 것이 이 콘솔의 대표
// 실패 방식이라(예외 하나면 그 아래가 통째로 안 그려진다) 칸마다 확인한다.
const required = [
  "adminStatus", "storyStats", "storyMerges", "storySplits",
  "issueStats", "mergeRules", "issueClusters", "borderline",
  "keywordStats", "keywordGroups", "antiKeywords", "feedStats", "feedTables",
  "tierTable", "learnedRules", "entryFilters", "entryList",
];
for (const id of required) {
  const html = written.get(id);
  assert.ok(html !== undefined, `#${id} 을 아예 안 그렸다 — 그 위에서 예외가 났을 것`);
  assert.ok(html.length > 0, `#${id} 이 비었다`);
}

// 편집 컨트롤이 실제로 붙었는지. 이게 없으면 '고칠 수 있는 콘솔'이 아니다.
const keywords = written.get("keywordGroups");
assert.ok(keywords.includes('data-act="chip-remove"'), "키워드 삭제 버튼이 없다");
assert.ok(keywords.includes('data-act="value-add"'), "키워드 추가 폼이 없다");
assert.ok(written.get("feedTables").includes('data-act="feed-add"'), "수집원 추가 폼이 없다");
assert.ok(written.get("tierTable").includes('data-act="tier-open"'), "출처 등급 수정 버튼이 없다");

// 분리 단위. 옛 회차는 hash↔제목 짝이 없어 물러나는 것이 정상이므로, 둘 중
// 하나는 반드시 나와야 한다 — 둘 다 없으면 화면이 아무 말도 안 한 것이다.
const story = written.get("storyMerges");
const splittable = story.includes('data-act="split-open"');
assert.ok(splittable || story.includes("분리 단위를"),
  "분리도 못 하고 왜 못 하는지도 안 적혀 있다");

console.log(
  `admin render: 화면 ${required.length}칸 렌더 통과` +
  ` (수동 분리 ${splittable ? "가능" : "단위 없음 — 사유 표시"})`,
);
