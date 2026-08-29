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

// ── 접히는 칸 ──────────────────────────────────────────────────────────────
//
// 병합 진단은 네 칸을 <details> 로 접어 두고 **펼칠 때** 비로소 그린다(toggle).
// 그래서 흉내 DOM 도 open 이 바뀌면 toggle 을 내야 한다 — 안 내면 화면은 열린
// 채로 비어 있고, 그게 정확히 이 스모크가 잡으려는 실패다. 진짜 details 처럼
// **값이 실제로 바뀔 때만** 낸다(안 그러면 다시 그리기가 두 배로 돈다).
const docListeners = new Map();     // type → handler
const folds = new Map();

function makeFold(name) {
  const summary = { textContent: "" };
  let open = false;
  const box = {
    dataset: { fold: name },
    summary,
    querySelector: selector =>
      (selector.includes('data-role="count"') ? summary : null),
    querySelectorAll: () => [],
    scrollIntoView() {},
    get open() { return open; },
    set open(value) {
      const next = Boolean(value);
      if (next === open) return;
      open = next;
      docListeners.get("toggle")?.({ target: box });
    },
  };
  return box;
}
for (const name of ["story", "stage", "issue", "borderline"]) {
  folds.set(name, makeFold(name));
}

const document = {
  getElementById: byId,
  querySelector: selector => {
    const fold = /\[data-fold="([^"]+)"\]/.exec(String(selector));
    return fold ? folds.get(fold[1]) || null : null;
  },
  querySelectorAll: () => [],
  addEventListener(type, handler) { docListeners.set(type, handler); },
};

const responses = {
  "/admin/data/merges.json": () => fs.readFileSync(path.join(dataDir, "merges.json"), "utf8"),
  "/admin/data/config.json": () => fs.readFileSync(path.join(dataDir, "config.json"), "utf8"),
  // KV 는 로컬에 없다. 판정이 하나도 없는 상태가 콘솔의 기본이므로 그것으로 답한다.
  "/admin/api/overrides": () => JSON.stringify({ version: 1, rev: 0, updated_at: "", entries: [] }),
};

const requested = [];
const replaced = [];
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
  // 회차는 주소에 남아야 한다 — 새로고침해도, 링크를 받아도 같은 날이 열려야
  // 하기 때문이다. 무엇을 남겼는지 아래에서 그대로 확인한다.
  history: { state: null, replaceState(state, title, url) { replaced.push(String(url)); } },
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
//
// 병합 진단의 네 칸은 접혀 있을 수 있어 여기 없다 — 아래 '접히는 칸'에서 따로 본다.
const required = [
  "adminStatus", "roundNav", "roundStats", "roundPriority",
  "keywordStats", "keywordGroups", "learnedTerms", "antiKeywords",
  "feedStats", "feedTables",
  "tierTable", "learnedRules", "entryFilters", "entryList",
];
for (const id of required) {
  const html = written.get(id);
  assert.ok(html !== undefined, `#${id} 을 아예 안 그렸다 — 그 위에서 예외가 났을 것`);
  assert.ok(html.length > 0, `#${id} 이 비었다`);
}

// ── 진단 회차 ──────────────────────────────────────────────────────────────
//
// 이 화면은 이제 "전부"가 아니라 회차 하나를 연다. 그래서 확인할 것이 셋이다 —
// 기본이 최신 회차인가, 화면의 숫자가 **전수**인가(실린 행 수가 아니라),
// 회차를 옮기면 네 칸이 함께 따라가는가.
// 스크립트 최상단의 `const state` 와 함수들은 realm 의 전역 렉시컬 환경에 있어
// 같은 컨텍스트에서 돌린 코드로 닿는다(sandbox 객체의 속성으로는 안 보인다).
const state = vm.runInContext("state", sandbox);
const esc = vm.runInContext("esc", sandbox);
const rounds = state.merges?.rounds;
assert.ok(rounds?.dates?.length, "회차 색인이 없다 — 빌드가 rounds 를 안 실었다");
assert.equal(state.round, rounds.latest, "기본은 최신 회차여야 한다");
const nav = written.get("roundNav");
assert.ok(nav.includes('data-act="round-pick"'), "회차를 고르는 자리가 없다");
assert.ok(nav.includes('data-act="round-go"'), "이전·다음으로 옮길 자리가 없다");
assert.ok(nav.includes(`value="${state.round}"`), "지금 회차가 목록에 없다");
// 기본값은 주소에 박지 않는다 — 박으면 즐겨찾기가 그날에 못 박힌다.
assert.ok(!String(replaced.at(-1) || "").includes("date="),
  `최신 회차인데 주소에 날짜가 남았다: ${replaced.at(-1)}`);

const rowOf = date => rounds.dates.find(row => row.date === date);
function assertStatsAreTotals(date) {
  const row = rowOf(date);
  const stats = written.get("roundStats");
  for (const [label, value] of [["같은 날 병합", `${row.story}건`],
    ["붙이지 않은 판단", `${row.stage}건`], ["날짜 넘는 병합", `${row.issue}개`],
    ["경계선 후보", `${row.borderline}쌍`]]) {
    assert.ok(stats.includes(value),
      `${date} 요약의 '${label}' 이 전수(${value})와 다르다 — 실린 행 수를 세고 있다`);
  }
}
assertStatsAreTotals(state.round);

// ── 접히는 칸 ──────────────────────────────────────────────────────────────
//
// 접기는 화면을 **줄이려고** 넣었다. details 는 닫혀도 자식을 DOM 에 들고 있어서
// 접기만으로는 카드가 하나도 안 줄어든다 — 그래서 닫을 때 비우는지까지 본다.
const FOLD_BODIES = {
  story: ["storyStats", "storyMerges"],
  stage: ["storySplits"],
  issue: ["issueStats", "mergeRules", "issueClusters"],
  borderline: ["borderline"],
};
for (const [name, bodies] of Object.entries(FOLD_BODIES)) {
  const box = folds.get(name);
  box.open = true;
  for (const id of bodies) {
    assert.ok((written.get(id) || "").length > 0, `#${id} 이 펼쳤는데도 비었다`);
  }
  assert.ok(box.summary.textContent.includes("건"),
    `${name} 칸이 건수를 안 적었다: ${box.summary.textContent}`);
  box.open = false;
  for (const id of bodies) {
    assert.equal(written.get(id), "", `#${id} 이 접었는데도 DOM 에 남아 있다`);
  }
  box.open = true;                       // 아래 검사는 펼친 화면을 본다
}

// 회차는 **진입 필터일 뿐**이다. 카드를 열면 이슈의 전체 맥락이 그대로 있어야
// 한다 — 며칠에 걸친 이슈를 날짜 조각으로 쪼개면 그 이슈가 무엇인지 아무
// 화면에서도 못 읽는다.
const roundClusters = vm.runInContext("roundClusters", sandbox);
const spanning = roundClusters().find(cluster =>
  new Set((cluster.members || []).map(member => member.article_date)).size > 1);
if (spanning) {
  const html = written.get("issueClusters");
  for (const member of spanning.members) {
    assert.ok(html.includes(esc(member.title)),
      `회차 밖 멤버가 카드에서 잘렸다: ${member.article_date} ${member.title}`);
  }
  assert.ok(html.includes(`${spanning.member_count}건 연결`),
    "카드가 전체 연결 수를 안 적는다 — 회차만큼만 묶인 것처럼 읽힌다");
}

// ── 회차 옮기기 ────────────────────────────────────────────────────────────
const selectRound = vm.runInContext("selectRound", sandbox);
const roundStory = vm.runInContext("roundStory", sandbox);
const older = rounds.dates.find(row =>
  row.date !== state.round && (row.story || row.stage || row.issue));
assert.ok(older, "지난 회차가 하나도 없다 — 회차 이동을 확인할 수 없다");
selectRound(older.date);
assert.equal(state.round, older.date, "회차가 안 옮겨졌다");
assert.ok(String(replaced.at(-1) || "").includes(`date=${older.date}`),
  `옮긴 회차가 주소에 안 남았다: ${replaced.at(-1)}`);
assertStatsAreTotals(older.date);

// 네 칸이 **함께** 따라가야 한다. 하나라도 옛 회차에 남으면 한 화면 안에서
// 날짜의 뜻이 갈라지고, 그때부터 건수가 조용히 거짓말을 한다.
//
// 센 수를 화면의 필터로 다시 세면 아무것도 검증하지 못한다(필터가 통째로 깨져도
// 양쪽이 같이 틀린다). 기대값은 **빌드가 만든 회차 색인**에서 가져온다.
folds.get("story").open = true;
const drawn = (written.get("storyMerges").match(/class="admin-card"/g) || []).length;
assert.equal(drawn, older.story,
  `${older.date} 회차에 story 카드 ${drawn}장이 섰다 — 색인이 말하는 판단은 ${older.story}건이다`);
assert.equal(roundStory().length, older.story, "화면이 세는 건수가 색인과 다르다");

// ── 창 밖으로 밀린 회차 ────────────────────────────────────────────────────
//
// 창에는 회차별 상한 말고 **합계 상한**도 있어서(CONSOLE_*_TOTAL) 아주 오래된
// 회차는 한 건도 못 실을 수 있다. 그때 화면이 '이 회차에는 없습니다'라고 적으면
// 바로 위 요약이 말한 N쌍이 통째로 증발한다 — 없는 것과 창 밖은 다르다.
const ghost = { date: "2026-01-01", story: 0, stage: 0, issue: 0, borderline: 7, borderline_shown: 0 };
rounds.dates.push(ghost);              // 목록은 최신순이라 맨 뒤가 가장 오래된 회차다
selectRound(ghost.date);
folds.get("borderline").open = true;
const outside = written.get("borderline");
assert.ok(outside.includes("창 밖"), `밀린 회차를 '없음'이라고 적는다: ${outside.slice(0, 120)}`);
assert.ok(outside.includes("7쌍"), "밀린 건수를 안 적는다 — 못 본 것을 못 본 줄도 모르게 된다");
assert.ok(folds.get("borderline").summary.textContent.includes("7건 중 0건"),
  `요약 줄이 전수를 안 적는다: ${folds.get("borderline").summary.textContent}`);
rounds.dates.pop();

// 아래 검사들은 화면 전체를 본다 — 최신 회차로 돌아가 네 칸을 다 펼쳐 둔다.
selectRound(rounds.latest);
for (const box of folds.values()) box.open = true;

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

// ── 방금 저장한 것 ─────────────────────────────────────────────────────────
//
// 이 표들이 읽는 config.json 은 **빌드 산출물**이고 콘솔 편집은 KV 로 바로 간다.
// 그래서 막 넣은 수집원·등급은 다음 빌드까지 목록에 없었다 — 관리자는 저장이
// 먹었는지 알 방법이 없어 같은 것을 두 번 넣었고, 두 번째는 409 로 막혔다.
// 여기서 보는 것은 둘이다: 안 간 판정이 **보이는가**, 그리고 이미 간 판정이
// 빌드 줄과 겹쳐 **두 번 서지 않는가**.
const renderFeeds = vm.runInContext("renderFeeds", sandbox);
const renderTiers = vm.runInContext("renderTiers", sandbox);

state.overrides.entries = [
  {
    kind: "feed_add", id: "feed-pending", name: "한국원자력학회",
    url: "https://news.google.com/rss/search?q=site%3Akns.org+when%3A3d&hl=ko",
    domain_label: "kns.org", require_keywords: [],
  },
  {
    kind: "tier_upsert", id: "tier-pending", domain: "kns.org", name: "한국원자력학회",
    tier: 2, source_type: "official", evidence_role: "primary", aliases: [],
  },
];
renderFeeds();
renderTiers();
const feedHtml = written.get("feedTables");
assert.ok(feedHtml.includes("한국원자력학회"), "방금 넣은 수집원이 표에 없다 — 저장이 먹었는지 알 수 없다");
assert.ok(feedHtml.includes("다음 수집부터"), "안 간 수집원이 이미 걷고 있는 것처럼 보인다");
const tierHtml = written.get("tierTable");
assert.ok(tierHtml.includes("kns.org"), "방금 올린 등급이 표에 없다");
assert.ok(tierHtml.includes("다음 수집부터"), "안 간 등급이 이미 적용된 것처럼 보인다");
// 표에 섰으면 거기서 고칠 수도 있어야 한다 — 수정 버튼이 빌드 목록만 보면
// 방금 올린 줄은 눌러도 빈 폼이 열린다.
const tierOpen = vm.runInContext("effectiveTierRows", sandbox)()
  .find(row => row.domain === "kns.org");
assert.equal(tierOpen?.tier, 2, "방금 올린 등급을 수정 폼이 못 찾는다");

// 이미 빌드를 탄 판정. config.overrides.entries 에 id 가 있으면 파이프라인이
// 듣고 있는 것이고, 그 수집원은 config.feeds.rss 에도 이미 들어 있다.
const built = state.config.feeds.rss[0];
state.overrides.entries = [{
  kind: "feed_add", id: "feed-live", name: built.name, url: built.url,
  domain_label: built.domain, require_keywords: [],
}];
state.config.overrides.entries.push({ kind: "feed_add", id: "feed-live" });
renderFeeds();
const target = `data-target="${esc(built.url)}"`;
assert.equal(written.get("feedTables").split(target).length - 1, 1,
  `이미 반영된 수집원이 표에 두 번 섰다: ${built.name}`);

// 중지도 같은 규칙이다. 아직 안 간 중지는 줄을 지우지 않고 '중지 예정'으로
// 세워야 한다 — 지워 버리면 되돌릴 자리가 화면에서 사라진다.
state.config.overrides.entries.pop();
state.overrides.entries = [{ kind: "feed_disable", id: "feed-stopping",
  target: built.url, label: built.name }];
renderFeeds();
const stopping = written.get("feedTables");
assert.ok(stopping.includes("중지 예정"), "안 간 중지가 표에 안 보인다");
assert.ok(stopping.includes('data-act="feed-restore"'), "중지를 되돌릴 자리가 없다");

console.log(
  `admin render: 화면 ${required.length}칸 + 접히는 칸 ${Object.keys(FOLD_BODIES).length}개 렌더 통과` +
  ` (회차 ${rounds.dates.length}개 · 기본 ${rounds.latest} → ${older.date} 이동 확인` +
  `${spanning ? `, 회차 밖 멤버 ${spanning.member_count}건 유지` : ""}` +
  `, 수동 분리 ${splittable ? "가능" : "단위 없음 — 사유 표시"}` +
  `, 사건 나누기 ${left.length}↔${right.length} 미리보기 확인` +
  ", 저장 직후 수집원·등급 표시 확인)",
);
