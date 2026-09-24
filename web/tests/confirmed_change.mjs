// '달라진 점'은 확인된 전이(change_log)만 말한다 (2026-09-24 결정).
// 실행: node web/tests/confirmed_change.mjs  (의존성 없음)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const source = readFileSync(fileURLToPath(new URL("../public/app.js", import.meta.url)), "utf8");
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`app.js 에 ${name}() 이 없다`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && (depth -= 1) === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name}() 블록이 안 닫힌다`);
}
const constLine = source.slice(source.indexOf("const CHANGE_LOG_LABELS"),
  source.indexOf(";", source.indexOf("const CHANGE_LOG_LABELS")) + 1);
const api = new Function(`${constLine}
  ${extract("changeLog")} ${extract("confirmedChange")} ${extract("issueChangeText")} ${extract("issueChangeLabel")}
  return { issueChangeText, issueChangeLabel };`)();

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); } catch (e) { failures += 1; console.error(`  ✗ ${name}\n    ${e.message}`); }
}

check("요약 비교 문장(latest_change)만 있으면 아무 말도 하지 않는다", () => {
  assert.equal(api.issueChangeText({ latest_change: "A → B.", change_display: "A" }), "");
});

check("확인된 전이는 두 기사의 실제 제목을 잇는다 — 가장 최근 것", () => {
  const issue = { change_log: [
    { date: "2026-09-18", kind: "minor", prior_title: "옛 제목", title: "후속 제목" },
    { date: "2026-09-23", kind: "material", prior_title: "대미투자 국회 보고", title: "지분 인수 협상 진통" },
  ] };
  assert.equal(api.issueChangeText(issue), "대미투자 국회 보고 → 지분 인수 협상 진통");
  assert.equal(api.issueChangeLabel(issue, "최근 변화"), "최근 변화 · 단계 이동");
});

check("라벨이 없는 판정(none 등)은 쓰지 않는다", () => {
  assert.equal(api.issueChangeText({ change_log: [{ date: "2026-09-23", kind: "none", title: "x" }] }), "");
});

if (failures) { console.error(`\n${failures}건 실패`); process.exit(1); }
console.log("\n모두 통과");
