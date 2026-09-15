// 화면 부피 예산 — 넘으면 **실패한다.**
// 실행: node web/tests/ui_budget.mjs   (build_data 이후. 새 의존성 없음)
//
// 왜 표가 아니라 검사인가
// ---------------------------------------------------------------------------
// 지금의 구 화면(오늘 5,994px · 흐름 18,604px)은 이미 세 번 줄인 결과다 —
// 2026-08-03 모바일 UX 감사, 08-05 에디토리얼 개편, PHASE_PLAN S4 '첫 화면
// 재구성'. 매번 줄었다가 다시 불었다. 줄었다는 사실을 PR 설명의 표에만 적으면
// 다음 사람은 그 표를 읽지 않고, 네 번째 개편이 생긴다.
//
// 그래서 숫자를 사람의 주의력이 아니라 CI 에 맡긴다. 예산을 넘기는 변경이
// 필요하면 **여기 값을 함께 고치면서** 왜인지 적게 된다. 그것이 이 파일의 전부다.
//
// 측정은 web/tools/measure_ui.mjs 하나가 한다(390px·시스템 크롬 --dump-dom).
// 브라우저를 못 찾으면 조용히 건너뛰지 않고 실패한다 — 안 도는 검사는 없는
// 검사인데 초록불은 있는 검사처럼 보인다(admin_dom.mjs 와 같은 원칙).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { measure, findBrowser } from "../tools/measure_ui.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.resolve(here, "..", "public", "data");

for (const file of ["briefings.json", "issues.json", "meta.json"]) {
  if (!fs.existsSync(path.join(dataDir, file))) {
    console.error(`ui budget: ${file} 이 없다 — 먼저 python web/build_data.py`);
    process.exit(1);
  }
}
if (!findBrowser()) {
  console.error("ui budget: 크롬/엣지를 못 찾았다 — CHROME_PATH 로 알려 줄 것");
  process.exit(1);
}

// 1단 기본 상태(펼치지 않은 화면)의 예산. 접힌 <details> 안쪽은 세지 않는다 —
// 그건 사용자가 열어야 보이는 것이고, 이 화면의 부피가 아니다.
const BUDGETS = [
  { name: "v3 오늘", query: "?ui=v3", height: 2500, tappables: 40, chars: 1400 },
  { name: "v3 흐름", query: "?ui=v3&view=trend", height: 5000, tappables: 60 },
];

// 구 화면은 예산을 두지 않는다. v3 로 넘어가기 전까지 줄일 계획이 없고, 여기에
// 지금 값을 적으면 '이 크기가 정상'이라는 뜻이 된다.

let failed = 0;
for (const budget of BUDGETS) {
  const result = await measure(budget.query);
  const checks = [
    ["세로 길이", result.height, budget.height, "px"],
    ["누를 수 있는 곳", result.tappableCount, budget.tappables, "개"],
    ...(budget.chars ? [["글자(공백 제외)", result.chars, budget.chars, "자"]] : []),
  ];
  console.log(`\n${budget.name}  (${budget.query})`);
  for (const [label, actual, limit, unit] of checks) {
    const over = actual > limit;
    if (over) failed += 1;
    const pct = Math.round((actual / limit) * 100);
    console.log(`  ${over ? "FAIL" : "ok  "} ${label.padEnd(16)} ${
      String(actual).padStart(6)}${unit} / 예산 ${limit}${unit}  (${pct}%)`);
  }
}

if (failed) {
  console.error(`\nui budget: ${failed}건이 예산을 넘었다.`);
  console.error("줄이거나, 예산을 올려야 할 이유를 이 파일에 적고 값을 함께 고칠 것.");
  process.exit(1);
}
console.log("\n예산 안에 있다.");
