// 저장한 이슈의 옛 주소 복구 — `issue_id` 가 옮겨 다닌 시절에 저장된 값을
// 원장 별칭표로 되살린다. 실행: node web/tests/saved_alias_migration.mjs
//
// 왜 검사를 두는가: 이 자리는 **사용자의 의도(저장)** 를 다룬다. 조용히 잃거나
// 남의 저장을 덮으면 되돌릴 방법이 없다. 그래서 순수 함수로 떼어 잠근다.
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 스크립트라 import 가 안 된다.
// 소스에서 함수 블록만 잘라 평가한다 — 이름이 바뀌면 조용히 통과하지 않고
// 여기서 먼저 깨진다(date_window.mjs 와 같은 방식).
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

const { resolveAlias, migrateSavedIds, ALIAS_CHAIN_LIMIT } = new Function(`
  const ALIAS_CHAIN_LIMIT = ${aliasLimit[1]};
  ${extract("resolveAlias")}
  ${extract("migrateSavedIds")}
  return { resolveAlias, migrateSavedIds, ALIAS_CHAIN_LIMIT };
`)();

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  process.stdout.write(`  ok  ${name}\n`);
}

/* ── resolveAlias ─────────────────────────────────────────────── */

check("별칭이 없으면 그대로", () => {
  assert.equal(resolveAlias("issue-a", {}), "issue-a");
  assert.equal(resolveAlias("issue-a", undefined), "issue-a");
});

check("한 번 옮겨간 것", () => {
  assert.equal(resolveAlias("issue-a", { "issue-a": "issue-b" }), "issue-b");
});

check("사슬을 끝까지 따라간다 (A→B→C)", () => {
  assert.equal(
    resolveAlias("issue-a", { "issue-a": "issue-b", "issue-b": "issue-c" }),
    "issue-c");
});

check("고리를 만나면 멈춘다 (무한 루프 금지)", () => {
  assert.equal(
    resolveAlias("issue-a", { "issue-a": "issue-b", "issue-b": "issue-a" }),
    "issue-b");
});

check("사슬 상한을 넘지 않는다", () => {
  const aliases = {};
  for (let i = 0; i < 40; i += 1) aliases[`n${i}`] = `n${i + 1}`;
  assert.equal(resolveAlias("n0", aliases), `n${ALIAS_CHAIN_LIMIT}`);
});

/* ── migrateSavedIds ──────────────────────────────────────────── */

const live = ids => new Set(ids);

check("옛 id 가 현재 id 로 옮겨진다", () => {
  const out = migrateSavedIds(
    new Set(["issue-old"]),
    { "issue-old": { title: "고리2호기 계속운전", last_seen: "2026-09-01" } },
    { "issue-old": "issue-new" },
    live(["issue-new"]));
  assert.deepEqual(out.ids, ["issue-new"]);
  assert.equal(out.moved, 1);
  assert.equal(out.meta["issue-new"].title, "고리2호기 계속운전");
});

check("살아 있는 id 는 별칭표에 있어도 건드리지 않는다", () => {
  // 현재 카탈로그가 원장의 옛 항목보다 이긴다.
  const out = migrateSavedIds(
    new Set(["issue-a"]), { "issue-a": { title: "A" } },
    { "issue-a": "issue-b" }, live(["issue-a", "issue-b"]));
  assert.deepEqual(out.ids, ["issue-a"]);
  assert.equal(out.moved, 0);
});

check("옛 id 와 새 id 를 둘 다 저장했으면 하나로 접힌다", () => {
  const out = migrateSavedIds(
    new Set(["issue-old", "issue-new"]),
    { "issue-old": { title: "옛 스냅샷" }, "issue-new": { title: "새 스냅샷" } },
    { "issue-old": "issue-new" },
    live(["issue-new"]));
  assert.deepEqual(out.ids, ["issue-new"]);
  assert.equal(out.moved, 1);
  // 사용자가 직접 그 id 로 저장한 값을 옮겨 온 것이 덮지 않는다.
  assert.equal(out.meta["issue-new"].title, "새 스냅샷");
});

check("옮겨 온 스냅샷이 기존 것을 덮지 않는다 (순서 반대)", () => {
  const out = migrateSavedIds(
    new Set(["issue-new", "issue-old"]),
    { "issue-new": { title: "새 스냅샷" }, "issue-old": { title: "옛 스냅샷" } },
    { "issue-old": "issue-new" },
    live(["issue-new"]));
  assert.deepEqual(out.ids, ["issue-new"]);
  assert.equal(out.meta["issue-new"].title, "새 스냅샷");
});

check("복구 못 한 id 는 남는다 — 묘비가 서야 한다", () => {
  const out = migrateSavedIds(
    new Set(["issue-gone"]), { "issue-gone": { title: "사라진 것" } },
    { "issue-other": "issue-x" }, live(["issue-x"]));
  assert.deepEqual(out.ids, ["issue-gone"]);
  assert.equal(out.moved, 0);
  assert.equal(out.meta["issue-gone"].title, "사라진 것");
});

check("스냅샷이 없는 저장도 옮겨진다", () => {
  const out = migrateSavedIds(
    new Set(["issue-old"]), {}, { "issue-old": "issue-new" }, live(["issue-new"]));
  assert.deepEqual(out.ids, ["issue-new"]);
  assert.equal(out.moved, 1);
  assert.deepEqual(out.meta, {});
});

check("빈 저장은 빈 결과", () => {
  const out = migrateSavedIds(new Set(), {}, { "a": "b" }, live([]));
  assert.deepEqual(out.ids, []);
  assert.equal(out.moved, 0);
});

check("liveIds 가 없어도 죽지 않는다", () => {
  const out = migrateSavedIds(
    new Set(["issue-old"]), {}, { "issue-old": "issue-new" }, undefined);
  assert.deepEqual(out.ids, ["issue-new"]);
});

check("입력을 제자리에서 고치지 않는다", () => {
  const ids = new Set(["issue-old"]);
  const meta = { "issue-old": { title: "A" } };
  migrateSavedIds(ids, meta, { "issue-old": "issue-new" }, live(["issue-new"]));
  assert.deepEqual([...ids], ["issue-old"]);
  assert.deepEqual(Object.keys(meta), ["issue-old"]);
});

check("멱등 — 한 번 옮긴 결과를 다시 넣어도 같다", () => {
  const aliases = { "issue-old": "issue-new" };
  const first = migrateSavedIds(
    new Set(["issue-old"]), { "issue-old": { title: "A" } }, aliases, live(["issue-new"]));
  const second = migrateSavedIds(
    new Set(first.ids), first.meta, aliases, live(["issue-new"]));
  assert.deepEqual(second.ids, first.ids);
  assert.deepEqual(second.meta, first.meta);
  assert.equal(second.moved, 0);
});

/* ── 별칭표가 실제로 나오는지 (빌드 산출물이 있을 때만) ──────────── */
try {
  const payload = JSON.parse(readFileSync(
    fileURLToPath(new URL("../public/data/issue_aliases.json", import.meta.url)), "utf8"));
  check("issue_aliases.json 의 모양", () => {
    assert.ok(payload.aliases && typeof payload.aliases === "object");
    for (const [from, to] of Object.entries(payload.aliases)) {
      assert.match(from, /^[A-Za-z0-9_-]+$/, `옛 id 형식: ${from}`);
      assert.match(to, /^[A-Za-z0-9_-]+$/, `현재 id 형식: ${to}`);
      assert.notEqual(from, to, "자기를 가리키는 별칭");
    }
  });
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
  process.stdout.write("  --  issue_aliases.json 없음 (빌드 전) — 모양 검사 건너뜀\n");
}

process.stdout.write(`\n저장 별칭 복구 검사 ${passed}건 통과\n`);
