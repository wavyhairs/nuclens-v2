// 흡수된 이슈를 앱 안에서 열었을 때 — 묘비가 아니라 **현재 이슈**로 넘어가는가.
// 실행: node web/tests/issue_merge_redirect.mjs
//
// 왜 검사를 두는가: 이 구멍은 오래 조용했다. 정적 주소 `/issue/<id>/` 는 원장의
// 별칭표로 현재 이슈에 넘겨 주는데(빌드가 리다이렉트 쪽지를 만든다) 앱 안의
// 상세만 그 표를 안 읽어서, 링크는 살아 있는데 클릭은 죽어 있었다. 실측
// 2026-09-13 라이브: 장기 스토리 타임라인 317행 중 147행(46%)이 "이 이슈를 찾을
// 수 없습니다" 로 끝났고, 101개 스토리 중 68개는 그게 **맨 아래 행**이었다.
//
// 회귀가 나도 화면은 멀쩡해 보인다 — 목록도 카드도 그대로 서고 누른 사람만 빈
// 토스트를 본다. 그래서 눈이 아니라 검사로 잠근다.
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 스크립트라 import 가 안 된다.
// 소스에서 함수 블록만 잘라 평가한다(saved_alias_migration.mjs 와 같은 방식).
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

const aliasLimit = /const ALIAS_CHAIN_LIMIT = (\d+);/.exec(source);
assert.ok(aliasLimit, "ALIAS_CHAIN_LIMIT 상수가 app.js 에 없다");

/* 실제 함수는 `openArchivedIssueDialog` 하나만 쓴다. 성공 경로는 dialog·
   requestAnimationFrame 까지 끌고 오므로 여기서는 **스냅샷이 없을 때**만 본다 —
   고쳐야 했던 자리가 정확히 거기다. */
function harness({ snapshots, aliases }) {
  const toasts = [];
  const opened = [];
  const state = { issueId: "" };
  const api = new Function("snapshots", "aliases", "toasts", "opened", "state", `
    const ALIAS_CHAIN_LIMIT = ${aliasLimit[1]};
    const showToast = (text) => toasts.push(text);
    const loadArchivedIssue = async (id) => snapshots[id] || null;
    const loadIssueAliases = async () => aliases;
    // 카탈로그에 있으면 애초에 이 경로로 오지 않는다. 넘겨받은 id 만 기록한다.
    const openIssueDialog = (id, updateUrl, viaAlias) => {
      opened.push(id);
      state.issueId = id;
      if (!snapshots[id]) openArchivedIssueDialog(id, updateUrl, viaAlias);
    };
    ${extract("resolveAlias")}
    ${extract("openArchivedIssueDialog")}
    return { openArchivedIssueDialog };
  `)(snapshots, aliases, toasts, opened, state);
  return { ...api, toasts, opened, state };
}

// `openArchivedIssueDialog` 는 promise 를 돌려주지 않는다(호출자가 기다릴 일이
// 없다). 그래서 검사는 마이크로태스크를 직접 비운다 — 사슬로 한 번 더 들어가는
// 경우까지 재려면 한 틱으로는 모자란다.
async function flush(ticks = 4) {
  for (let i = 0; i < ticks; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
}

let passed = 0;
async function check(name, fn) {
  await fn();
  passed += 1;
  process.stdout.write(`  ok  ${name}\n`);
}

/* ── 흡수된 이슈 ───────────────────────────────────────────────── */

await check("스냅샷이 없는 id 는 별칭표가 가리키는 현재 이슈로 넘어간다", async () => {
  const app = harness({
    snapshots: { "story-new": { issue_id: "story-new", title: "한빛 2호기 운영허가 만료" } },
    aliases: { "story-old": "story-new" },
  });
  app.state.issueId = "story-old";
  await app.openArchivedIssueDialog("story-old");
  await flush();
  assert.deepEqual(app.opened, ["story-new"]);
  // 조용히 다른 제목을 띄우지 않는다 — 누른 줄과 열린 이슈가 다르면 그 자체가
  // 새로운 버그로 읽힌다.
  assert.match(app.toasts.join(" "), /합쳐졌/);
});

await check("사슬은 끝까지 따라간다 (A→B→C)", async () => {
  const app = harness({
    snapshots: { "story-c": { issue_id: "story-c" } },
    aliases: { "story-a": "story-b", "story-b": "story-c" },
  });
  app.state.issueId = "story-a";
  await app.openArchivedIssueDialog("story-a");
  await flush();
  assert.deepEqual(app.opened, ["story-c"]);
});

/* ── 진짜로 없는 이슈 ──────────────────────────────────────────── */

await check("별칭이 없으면 예전처럼 묘비를 띄운다", async () => {
  const app = harness({ snapshots: {}, aliases: {} });
  app.state.issueId = "story-gone";
  await app.openArchivedIssueDialog("story-gone");
  await flush();
  assert.deepEqual(app.opened, []);
  assert.deepEqual(app.toasts, ["이 이슈를 찾을 수 없습니다."]);
  // 열지 못한 이슈를 주소에 남겨 두면 새로고침이 같은 실패를 반복한다.
  assert.equal(app.state.issueId, "");
});

await check("별칭이 가리킨 곳마저 비어 있으면 거기서 멈춘다", async () => {
  // 별칭표와 스냅샷이 한 빌드 어긋난 경우. 되돌아가며 다시 찾으면 무한히 돈다.
  const app = harness({ snapshots: {}, aliases: { "story-old": "story-new" } });
  app.state.issueId = "story-old";
  await app.openArchivedIssueDialog("story-old");
  await flush();
  assert.deepEqual(app.opened, ["story-new"]);
  assert.equal(app.toasts.filter(text => /찾을 수 없습니다/.test(text)).length, 1);
});

await check("기다리는 동안 다른 이슈를 열었으면 그 화면을 덮지 않는다", async () => {
  const app = harness({ snapshots: {}, aliases: { "story-old": "story-new" } });
  app.state.issueId = "story-other";
  await app.openArchivedIssueDialog("story-old");
  await flush();
  assert.deepEqual(app.opened, []);
  assert.deepEqual(app.toasts, []);
});

/* ── 빌드 산출물 ───────────────────────────────────────────────── */
// 별칭표가 실제로 지어졌을 때만 본다. python-tests 에는 이 파일이 없다.

const aliasPath = fileURLToPath(new URL("../public/data/issue_aliases.json", import.meta.url));
let built = null;
try { built = JSON.parse(readFileSync(aliasPath, "utf8")); } catch { built = null; }
if (built) {
  await check("지어진 별칭표는 자기를 가리키지 않는다", async () => {
    const aliases = built.aliases || {};
    for (const [from, to] of Object.entries(aliases)) {
      assert.notEqual(from, to, `${from} 이 자기를 가리킨다`);
    }
  });
} else {
  process.stdout.write("  --  data/issue_aliases.json 없음 (빌드 전) — 모양 검사 건너뜀\n");
}

process.stdout.write(`\n${passed}개 통과\n`);
