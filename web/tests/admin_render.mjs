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
  "keywordStats", "keywordGroups", "learnedTerms", "antiKeywords",
  "feedStats", "feedTables",
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

// 학습된 검색어. 자동으로 생기고 사라지는 말이라 **사람이 손댈 수 있는가**가
// 이 칸의 존재 이유다 — 목록만 보이고 뺄 수 없으면 잘못 배운 말을 지켜보는
// 것밖에 못 한다. 목록이 비어 있어도 추가 입구는 늘 있어야 한다.
const learned = written.get("learnedTerms");
assert.ok(learned.includes('data-act="value-add"'), "학습 검색어 추가 폼이 없다");
assert.ok(learned.includes("learned_term_add"), "학습 검색어 판정 종류가 화면에 없다");
if (!learned.includes("empty-state")) {
  assert.ok(learned.includes('data-act="chip-remove"'), "학습 검색어를 뺄 버튼이 없다");
  assert.ok(learned.includes('data-act="learned-promote"'), "고정 키워드 승격 입구가 없다");
  assert.ok(learned.includes("왜 생겼나"), "근거를 안 보여 준다 — 판단할 수 없는 목록이다");
}
assert.ok(written.get("tierTable").includes('data-act="tier-open"'), "출처 등급 수정 버튼이 없다");

// 분리 단위. 옛 회차는 hash↔제목 짝이 없어 물러나는 것이 정상이므로, 둘 중
// 하나는 반드시 나와야 한다 — 둘 다 없으면 화면이 아무 말도 안 한 것이다.
const story = written.get("storyMerges");
const splittable = story.includes('data-act="split-open"');
assert.ok(splittable || story.includes("분리 단위를"),
  "분리도 못 하고 왜 못 하는지도 안 적혀 있다");

// 이슈 묶음에는 '나누기' 입구가 둘이다 — 기사 하나만 잘못 들어왔을 때와
// 서로 다른 사건군이 섞였을 때. 실제 산출물에 묶음이 있으면 둘 다 붙어야 한다.
const clusters = written.get("issueClusters");
if (!clusters.includes("empty-state")) {
  assert.ok(clusters.includes('data-preset="alone"'), "이슈에서 기사 하나를 뺄 입구가 없다");
  assert.ok(clusters.includes('data-preset="manual"'), "두 사건으로 나눌 입구가 없다");
  assert.ok(!clusters.includes('data-act="issue-split"'),
    "상대를 코드가 고르는 옛 분리 버튼이 살아 있다");
}

// ── 사건 나누기 ────────────────────────────────────────────────────────────
//
// 여기가 2026-08-16 사고의 자리다. 예전 [떼어내기]는 상대 기사를 코드가 골라
// (대표 기사) 눌린 즉시 저장했고, 화면은 그 상대를 끝까지 보여 주지 않았다.
// 그래서 검사하는 것은 "무엇을 저장할지 화면이 말하는가"다 — 폼이 세운 두
// 사건군과, 저장될 쌍 목록이 정확히 맞아떨어져야 한다.
//
// 스크립트 최상단의 `const state` 는 realm 의 전역 렉시컬 환경에 있어 같은
// 컨텍스트에서 돌린 코드로 닿는다(sandbox 객체의 속성으로는 안 보인다).
const state = vm.runInContext("state", sandbox);
const groupSplitForm = vm.runInContext("groupSplitForm", sandbox);
const groupSides = vm.runInContext("groupSides", sandbox);
const groupSplitPreview = vm.runInContext("groupSplitPreview", sandbox);
const suggestAxis = vm.runInContext("suggestAxis", sandbox);

const fixture = {
  issue_id: "issue-fixture", title: "고리 3·4호기 계속운전", methods: [],
  member_count: 6, first_seen: "2026-08-01", last_seen: "2026-08-06", matches: [],
  members: [
    { hash: "k1", title: "원안위, 고리 3·4호기 계속운전 하반기 심의 예정", article_date: "2026-08-01", countries: ["KR"] },
    { hash: "k2", title: "원안위, 고리 3·4호기 계속운전 연내 결론 목표", article_date: "2026-08-02", countries: ["KR"] },
    { hash: "k3", title: "원안위, 고리 3·4호기 계속운전 하반기 심사 착수", article_date: "2026-08-03", countries: ["KR"] },
    { hash: "k4", title: "고리 3·4호기 계속운전 심사 진행 상황 점검", article_date: "2026-08-04", countries: ["KR"] },
    { hash: "m1", title: "산업부·원안위, 원전 수출 규제체계 업무협약 체결", article_date: "2026-08-05", countries: ["KR"] },
    { hash: "m2", title: "산업부·원안위, 한국형 원전 해외진출 업무협약", article_date: "2026-08-06", countries: ["KR"] },
  ],
};
state.merges.issue.clusters.push(fixture);

// ① 폼은 묶인 기사를 **전부** 세운다. 하나라도 빠지면 그 기사는 어느 쪽인지
//    아무도 정하지 않은 채 남고, 그러면 선이 그어지지 않는다.
const form = groupSplitForm("issue-fixture", "m1", "manual");
for (const member of fixture.members) {
  assert.ok(form.includes(member.title), `나누기 화면에 ${member.hash} 가 없다`);
}
assert.ok(form.includes('name="side:k1"'), "기사마다 어느 쪽인지 고르는 자리가 없다");
assert.ok(!form.includes('name="side:m1"'), "기준 기사에까지 선택지가 붙었다");

// ② 화면이 세운 두 사건군 = 저장될 쌍. 이 둘이 어긋난 것이 사고의 본체였다.
const picks = { k1: "split", k2: "split", k3: "split", k4: "split", m2: "keep" };
const fakeForm = {
  dataset: { issue: "issue-fixture", anchor: "m1" },
  querySelector: () => null,
  // 진짜 폼처럼 라디오를 통째로 돌려준다 — 콘솔은 선택자에 hash 를 끼워 넣지 않는다.
  querySelectorAll: selector => (selector.includes("radio")
    ? Object.entries(picks).flatMap(([hash, value]) => [
      { name: `side:${hash}`, value: "keep", checked: value === "keep" },
      { name: `side:${hash}`, value: "split", checked: value === "split" },
    ])
    : []),
};
const { left, right } = groupSides(fakeForm);
// 배열은 vm realm 안에서 만들어져 프로토타입이 다르다 — 문자열로 비교한다.
assert.equal(left.map(member => member.hash).join(","), "m1,m2", "기준 기사 쪽이 틀렸다");
assert.equal(right.map(member => member.hash).join(","), "k1,k2,k3,k4");

const preview = groupSplitPreview(left, right);
assert.ok(preview.includes("8쌍"), `쌍 개수를 안 세거나 틀렸다: ${preview.slice(0, 200)}`);
for (const member of [...left, ...right].slice(0, 4)) {
  assert.ok(preview.includes(member.title.slice(0, 12)),
    `저장 전 목록에 ${member.hash} 가 안 보인다`);
}
assert.ok(groupSplitPreview(left, []).includes("아직 아무것도 갈라지지 않습니다"),
  "한쪽이 비었는데 갈라지는 것처럼 말한다");

// ③ 판별축은 **나눈 뒤에** 두 사건군의 제목을 비교해서 나온다.
const axis = suggestAxis(right.map(m => m.title), left.map(m => m.title));
assert.ok(axis.left.includes("계속운전"), `왼쪽 축 후보가 이상하다: ${axis.left}`);
assert.ok(axis.right.some(word => ["업무협약", "수출", "산업부"].includes(word)),
  `오른쪽 축 후보가 이상하다: ${axis.right}`);
assert.equal(axis.left.filter(word => axis.right.includes(word)).length, 0,
  "양쪽에 같은 낱말이 올라왔다 — 그 축으로는 영원히 안 갈린다");

console.log(
  `admin render: 화면 ${required.length}칸 렌더 통과` +
  ` (수동 분리 ${splittable ? "가능" : "단위 없음 — 사유 표시"}` +
  `, 사건 나누기 ${left.length}↔${right.length} 미리보기 확인)`,
);
