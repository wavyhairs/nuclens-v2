// 콘솔 정정의 앞뒤 — 나누기가 사람 승인과 부딪히면 저장을 막고, 저장한 나누기가
// 다음 빌드에서 실제로 갈렸는지를 hash_index 로 보여 준다.
// 실행: node web/tests/admin_corrections.mjs  (의존성 없음)
//
// 2026-09-24: 6260(전기본) 을 나누면 8/6 사람 승인(6260↔d4658)과 같은 쌍이 승인·기각에
// 동시에 보관될 수 있었다. 콘솔은 그것을 보여 주지 않았고, 저장 뒤 실제로 갈렸는지도
// '적용됨' 배지 하나로만 말했다 — 배지는 판정이 파이프라인에 들어갔다는 뜻이지 갈렸다는
// 뜻이 아니다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const source = readFileSync(
  fileURLToPath(new URL("../public/admin/admin.js", import.meta.url)), "utf8");

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`admin.js 에 ${name}() 이 없다`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && (depth -= 1) === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name}() 블록이 안 닫힌다`);
}

const api = state => new Function("state", `
  ${extract("esc")}
  ${extract("hashHome")}
  ${extract("splitOutcome")}
  ${extract("approvedCrossings")}
  ${extract("groupSplitPreview")}
  return { splitOutcome, approvedCrossings, groupSplitPreview };
`)(state);

const A = { hash: "6260c0799db78ac9", title: "원자력학회 전기본 촉구" };
const B = { hash: "d4658d844ee27556", title: "부처 간 혼선" };
const C = { hash: "c99b7cc176f524d8", title: "공론화 추진" };

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); } catch (error) {
    failures += 1; console.error(`  ✗ ${name}\n    ${error.message}`);
  }
}

const approvedState = { merges: { issue: {
  manual_approved: [{ pair: [A.hash, B.hash].sort().join("--"), note: "같은 사건(8/6)" }],
  hash_index: {},
} } };

check("승인된 쌍을 가로지르는 선은 충돌로 잡힌다", () => {
  const out = api(approvedState).approvedCrossings([A, C], [B]);
  assert.equal(out.length, 1);
  const html = api(approvedState).groupSplitPreview([A, C], [B]);
  assert.ok(html.includes("저장할 수 없습니다"), "충돌 경고가 없다");
  assert.ok(html.includes("같은 사건(8/6)"), "승인 사유가 안 보인다");
});

check("승인 쌍을 같은 쪽에 두면 충돌이 없다", () => {
  assert.equal(api(approvedState).approvedCrossings([A, B], [C]).length, 0);
  assert.ok(!api(approvedState).groupSplitPreview([A, B], [C]).includes("저장할 수 없습니다"));
});

check("저장 버튼이 충돌을 보고 잠긴다", () => {
  assert.ok(extract("updateGroupSplit").includes("approvedCrossings(left, right).length"),
    "충돌이 있어도 저장 버튼이 열린다");
});

const entry = { left_hashes: [A.hash, B.hash], right_hashes: [C.hash] };

check("다음 빌드에서 갈렸으면 양쪽 이슈를 말한다 — 근거로 붙은 기사도 센다", () => {
  const state = { merges: { issue: { hash_index: {
    [A.hash]: ["issue-6260", "card"], [B.hash]: ["issue-6260", "evidence"], [C.hash]: ["issue-c99b", "card"],
  } } } };
  const out = api(state).splitOutcome(entry);
  assert.equal(out.state, "split");
  assert.deepEqual(out.rightIds, ["issue-c99b"]);
  assert.equal(out.evidence, 1);
});

check("아직 한 이슈면 그렇다고 말한다", () => {
  const state = { merges: { issue: { hash_index: {
    [A.hash]: ["issue-6260", "card"], [B.hash]: ["issue-6260", "card"], [C.hash]: ["issue-6260", "card"],
  } } } };
  assert.equal(api(state).splitOutcome(entry).state, "joined");
});

check("학습 체크박스는 기본으로 꺼져 있다(사건군 나누기)", () => {
  const form = extract("groupSplitForm");
  assert.ok(form.includes('name="learn">'), "학습이 기본으로 켜져 있다");
});

if (failures) { console.error(`\n${failures}건 실패`); process.exit(1); }
console.log("\n모두 통과");
