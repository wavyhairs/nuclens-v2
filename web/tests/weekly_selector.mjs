// 주간 3분이 어느 리포트를 붙이는가 — 토요일마다 화면이 비던 회귀의 방지선.
// 실행: node web/tests/weekly_selector.mjs  (의존성 없음)
//
// 규칙은 하나다: **선택한 날짜까지 이미 끝난 리포트 중 가장 최근 것**.
//   · 끝나지 않은 주(week_end > 선택일)는 고르지 않는다 → 7월 화면에 8월 결론 금지
//   · 그 주 리포트가 없다고 비우지 않는다 → 토~목 엿새 '집계 중' 금지
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. date_window.mjs 와 같은 방식으로 함수 블록만 잘라 평가한다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

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

// ISO_DATE_RE 는 함수가 아니라 상수라 이름으로 한 줄을 집어 온다. 상수가 사라지면
// 여기서 먼저 깨진다(조용히 통과하지 않게).
function extractConst(name) {
  const line = source.split("\n").find(row => row.startsWith(`const ${name} = `));
  if (!line) throw new Error(`app.js 에 ${name} 상수가 없다`);
  return line;
}

const build = new Function("state", `
  ${extractConst("ISO_DATE_RE")}
  ${extract("shiftDate")}
  ${extract("dateLabel")}
  ${extract("weeklyReportEnd")}
  ${extract("weeklyReportFor")}
  ${extract("weekRangeLabel")}
  return { weeklyReportFor, weekRangeLabel };
`);

// 실제 weekly_reports.json 과 같은 모양 — build_data.load_weekly_reports 가
// week_start 를 키로 잡고 리포트 안에 week_start/week_end 를 그대로 싣는다.
const report = (start, end) => ({
  week_start: start, week_end: end,
  policy_shifts: [{ what: `결론 ${start}` }],
  watchpoints: [`확인 ${start}`],
});

const REPORTS = {
  "2026-08-01": report("2026-08-01", "2026-08-07"),
  "2026-08-08": report("2026-08-08", "2026-08-14"),
  "2026-08-15": report("2026-08-15", "2026-08-21"),
};

const withReports = reports => build({ trend: { weekly_reports: reports } });

const cases = [];
const eq = (label, got, want) => cases.push({ label, got, want, ok: got === want });

const api = withReports(REPORTS);
const pick = date => api.weeklyReportFor(date)?.week_start ?? null;

// ── 1. 토요일: 새 주차가 막 시작됐고 그 주 리포트는 금요일에야 나온다.
//        원래 코드는 여기서 null 을 돌려주고 화면이 '집계 중'이 됐다.
eq("토요일 8/22 → 직전 완료분", pick("2026-08-22"), "2026-08-15");

// ── 2. 일~목: 계속 같은(가장 최근 완료) 리포트.
eq("일요일 8/23", pick("2026-08-23"), "2026-08-15");
eq("월요일 8/24", pick("2026-08-24"), "2026-08-15");
eq("목요일 8/27", pick("2026-08-27"), "2026-08-15");

// ── 3. 금요일, 새 리포트 생성 **전**: 아직 이전 것.
eq("금요일 8/28 생성 전", pick("2026-08-28"), "2026-08-15");

// ── 4. 금요일, 새 리포트 생성 **후**: 새 것으로 교체.
const after = withReports({ ...REPORTS, "2026-08-22": report("2026-08-22", "2026-08-28") });
eq("금요일 8/28 생성 후", after.weeklyReportFor("2026-08-28")?.week_start, "2026-08-22");
eq("생성 직후 토요일 8/29", after.weeklyReportFor("2026-08-29")?.week_start, "2026-08-22");

// ── 5. 과거 날짜: 미래 리포트는 절대 안 고른다(원래 버그의 반대 방향).
eq("7/30 조회 → 8월 리포트 금지", pick("2026-07-30"), null);
eq("8/7 조회 → 8/1~7", pick("2026-08-07"), "2026-08-01");
eq("8/6 조회 → 아직 안 끝난 주 제외", pick("2026-08-06"), null);
eq("8/14 조회 → 8/8~14", pick("2026-08-14"), "2026-08-08");
eq("8/13 조회 → 8/1~7", pick("2026-08-13"), "2026-08-01");

// ── 6. 리포트가 하나도 없으면 null → 화면은 '집계 중'.
eq("리포트 없음", withReports({}).weeklyReportFor("2026-08-22"), null);
eq("weekly_reports 자체가 없음", build({ trend: {} }).weeklyReportFor("2026-08-22"), null);
eq("빈 날짜", pick(""), null);
eq("깨진 날짜", pick("2026-8-2"), null);

// ── 7. 오늘 화면의 Weekly 블록은 전부 같은 selector 를 지난다.
//        renderTodayAgenda(주간 3분)와 renderHomeIntelligence(이번 주 해설)가
//        각자 다른 규칙으로 고르면 한 화면이 두 주를 말한다.
const agendaCall = source.slice(source.indexOf("function renderTodayAgenda("))
  .slice(0, 900);
const homeCall = source.slice(source.indexOf("function renderHomeIntelligence("))
  .slice(0, 1600);
eq("주간 3분이 selector 사용", /weeklyReportFor\(briefing\.date\)/.test(agendaCall), true);
eq("이번 주 해설이 같은 selector 사용", /weeklyReportFor\(briefing\.date\)/.test(homeCall), true);
eq("두 블록 모두 briefingWeek 를 다시 계산하지 않는다",
  /briefingWeek/.test(source), false);

// ── 8. 제목의 날짜 = 고른 리포트의 week_start/week_end.
eq("라벨은 리포트가 말한다", api.weekRangeLabel(api.weeklyReportFor("2026-08-22")),
  "8월 15일–21일");
eq("8/7 라벨", api.weekRangeLabel(api.weeklyReportFor("2026-08-07")), "8월 1일–7일");
eq("달을 넘으면 둘 다 적는다",
  api.weekRangeLabel({ week_start: "2026-07-25", week_end: "2026-08-01" }),
  "7월 25일–8월 1일");
eq("리포트가 없으면 빈 라벨", api.weekRangeLabel(null), "");
eq("week_end 없는 옛 레코드도 라벨은 비운다",
  api.weekRangeLabel({ week_start: "2026-08-15" }), "");
// week_end 가 없는 옛 레코드는 토~금 7일 규칙으로 메워 선택은 계속 된다.
eq("week_end 없는 옛 레코드 선택",
  withReports({ "2026-08-15": { week_start: "2026-08-15" } })
    .weeklyReportFor("2026-08-22")?.week_start, "2026-08-15");
eq("week_end 없는 옛 레코드도 미래는 금지",
  withReports({ "2026-08-15": { week_start: "2026-08-15" } })
    .weeklyReportFor("2026-08-20"), null);

const failed = cases.filter(row => !row.ok);
for (const row of cases) {
  console.log(`${row.ok ? "ok  " : "FAIL"} ${row.label} → ${row.got}${row.ok ? "" : ` (기대 ${row.want})`}`);
}
if (failed.length) {
  console.error(`\n${failed.length}/${cases.length} 실패`);
  process.exit(1);
}
console.log(`\n${cases.length}건 전부 통과`);
