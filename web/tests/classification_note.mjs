// 지난 브리핑 카드의 '이후 분류 정정됨' 안내(briefing_snapshot.py 의 화면 쪽).
// 실행: node web/tests/classification_note.mjs  (의존성 없음)
//
// 원장은 발송 당시 문장을 붙잡아 두지만, 화면이 그 표식을 안 읽거나 최신 회차에서
// 카탈로그 행으로 덮어쓰면 조용히 사라진다 — 카드는 멀쩡히 뜨고 문장만 바뀐다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const source = readFileSync(
  fileURLToPath(new URL("../public/app.js", import.meta.url)), "utf8");

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

const api = new Function("state", `
  ${extract("esc")}
  ${extract("classificationNote")}
  ${extract("briefingIssuesForDisplay")}
  ${extract("splitLineageNote")}
  return { classificationNote, briefingIssuesForDisplay, splitLineageNote };
`);

const corrected = {
  issue_id: "issue-6260", title: "전기본 비대 이슈",
  classification_note: { status: "corrected", now: [
    { issue_id: "issue-6260", title: "전기본 확정" },
    { issue_id: "issue-tariff", title: "지역별 요금제" },
  ] },
};

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); } catch (error) {
    failures += 1; console.error(`  ✗ ${name}\n    ${error.message}`);
  }
}

check("정정된 카드는 안내와 지금 사건 링크를 단다", () => {
  const html = api({}).classificationNote(corrected);
  assert.ok(html.includes("이후 분류 정정됨"));
  assert.ok(html.includes("2개 사건으로 나뉘어"));
  assert.ok(html.includes('data-issue-id="issue-tariff"'), "갈라진 쪽으로 가는 링크가 없다");
  assert.ok(!html.includes('data-issue-id="issue-6260"'), "자기 자신을 여는 버튼이 생겼다");
});

check("표식이 없는 카드는 아무 말도 하지 않는다", () => {
  assert.equal(api({}).classificationNote({ issue_id: "x" }), "");
});

check("최신 회차에서도 정정 카드는 카탈로그 행으로 덮이지 않는다", () => {
  const catalogRow = { issue_id: "issue-6260", title: "전기본 확정" };
  const state = { meta: { latest_briefing_date: "2026-09-24" }, issues: [catalogRow] };
  const rows = api(state).briefingIssuesForDisplay({ date: "2026-09-24", issues: [corrected] });
  assert.equal(rows[0], corrected);
});

check("카드와 상세가 둘 다 안내를 그린다", () => {
  assert.ok(extract("issueCard").includes("classificationNote(issue)"), "카드에 안내가 없다");
  assert.ok(extract("openIssueDialog").includes("classificationNote(issue)"), "상세에 안내가 없다");
});

check("옛 주소(이긴 쪽)는 갈라져 나간 사건을 링크한다", () => {
  const html = api({}).splitLineageNote({ issue_id: "issue-6260",
    split_children: [{ issue_id: "issue-c99b", title: "공론화 추진" }], split_parent: {} });
  assert.ok(html.includes('data-issue-id="issue-c99b"'));
  assert.ok(html.includes("갈라져 나간 사건"));
  assert.equal(api({}).splitLineageNote({ issue_id: "x", split_children: [], split_parent: {} }), "");
  assert.ok(extract("openIssueDialog").includes("splitLineageNote(issue)"), "상세에 계보 안내가 없다");
});

if (failures) { console.error(`\n${failures}건 실패`); process.exit(1); }
console.log("\n모두 통과");
