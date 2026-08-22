// '한 주의 원자력' 블록이 어느 리포트를 붙이는가 — 토요일마다 화면이 비던 회귀의 방지선.
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

// ── 7. 오늘 화면의 Weekly 블록은 **하나**이고, 그 하나가 selector 를 지난다.
//        2026-08-22 이전에는 renderTodayAgenda(주간 3분)와
//        renderHomeIntelligence(이번 주 해설) 둘이었다. 같은 리포트를 각자
//        고르는 구조라 규칙이 갈라지면 한 화면이 두 주를 말할 수 있었고, 실제로
//        읽는 사람에게는 별개 기능 둘로 보였다. 통합으로 그 위험 자체를 없앴다 —
//        이제 검사할 것은 '남은 하나가 selector 를 지나는가'와 '두 번째 블록이
//        돌아오지 않았는가' 둘이다.
const agendaCall = source.slice(source.indexOf("function renderTodayAgenda("))
  .slice(0, 900);
const homeBody = source.slice(source.indexOf("function renderHomeIntelligence("))
  .slice(0, 1600).replace(/\/\/.*/g, "");   // 주석은 뺀다 — 실행되는 코드만 본다
eq("한 주의 원자력이 selector 사용", /weeklyReportFor\(briefing\.date\)/.test(agendaCall), true);
eq("두 번째 Weekly 블록이 돌아오지 않았다", /weeklyReportFor|weekly_intro|so_what/.test(homeBody), false);
eq("블록이 briefingWeek 를 다시 계산하지 않는다",
  /briefingWeek/.test(source), false);

// ── 7-1. 네 영역의 순서가 곧 읽는 순서다: 무엇이 바뀌었나 → 이번 주 흐름 →
//        그래서 어떤 의미 → 다음에 볼 것. DOM 순서가 이 논리를 뒤집으면
//        통합의 목적(한 자리에서 논리적으로 읽힌다)이 사라진다.
const agendaFull = source.slice(source.indexOf("function renderTodayAgenda("))
  .slice(0, 3200);
const order = ["agendaConclusions", "agendaNarrative", "agendaSoWhat", "agendaWatch"]
  .map(id => agendaFull.indexOf(id));
eq("네 영역이 전부 렌더된다", order.every(at => at >= 0), true);
eq("순서: 한눈에 보기 → 한 주 해설 → 왜 중요한가 → 지금 확인할 것",
  order.every((at, i) => i === 0 || at > order[i - 1]), true);

const markup = readFileSync(
  fileURLToPath(new URL("../public/index.html", import.meta.url)), "utf8")
  .replace(/<!--[\s\S]*?-->/g, "");
const domOrder = ['id="agendaConclusions"', 'id="agendaNarrative"',
  'id="agendaSoWhat"', 'id="agendaWatch"'].map(id => markup.indexOf(id));
eq("DOM 순서도 같다", domOrder.every((at, i) => at >= 0 && (i === 0 || at > domOrder[i - 1])), true);
// 합친 블록이 둘로 다시 갈라지지 않았는가 — 04 섹션은 제거됐다.
eq("04 '3분이면 이해되는 한 주의 원자력' 이 없다", /homeWeeklyStory/.test(markup), false);

// ── 7-2. 실제로 한 번 그려 본다 — 네 재료가 네 자리에 가는가.
//
//        source 검사만으로는 '순서가 맞다'까지밖에 못 본다. what 과 so_what 은
//        같은 policy_shifts 행에서 나오므로 둘을 맞바꿔 넣어도 정규식은 통과한다.
//        흉내 DOM 으로 충분한 이유: 여기 조회는 전부 getElementById 라 실제
//        문서에서만 생기는 선택자 모호함(admin_dom.mjs 주석 참조)이 없다.
const renderWith = (report, briefing) => {
  const nodes = new Map();
  const node = () => ({ hidden: false, innerHTML: "", textContent: "" });
  const doc = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, node());
      return nodes.get(id);
    },
  };
  const run = new Function("state", "document", `
    ${extractConst("ISO_DATE_RE")}
    ${extract("shiftDate")}
    ${extract("dateLabel")}
    ${extract("esc")}
    ${extract("weeklyReportEnd")}
    ${extract("weeklyReportFor")}
    ${extract("weekRangeLabel")}
    ${extract("issueChangeText")}
    ${extract("dropTextsAlreadyOnCards")}
    ${extract("renderTodayAgenda")}
    return renderTodayAgenda;
  `);
  run({ trend: { weekly_reports: report ? { [report.week_start]: report } : {} } }, doc)(briefing);
  return nodes;
};

const full = {
  week_start: "2026-08-15", week_end: "2026-08-21",
  weekly_intro: "이번 주는 계속운전 심사와 SMR 계약이 같은 방향을 가리켰다.",
  policy_shifts: [
    { what: "원안위가 고리 3호기 계속운전을 승인했다", so_what: "후속 6기 심사 일정이 앞당겨진다" },
    { what: "산업부가 12차 전기본 초안을 냈다", so_what: "신규 노형 물량이 확정 국면에 든다" },
  ],
  watchpoints: ["12차 전기본 공청회 일정"],
};
const drawn = renderWith(full, { date: "2026-08-22", issues: [] });
const html = id => drawn.get(id).innerHTML;

eq("제목 = 한 주의 원자력 · 고른 리포트의 구간",
  drawn.get("todayAgendaTitle").textContent, "한 주의 원자력 · 8월 15일–21일");
eq("한눈에 보기 ← policy_shifts[].what",
  /고리 3호기 계속운전을 승인/.test(html("agendaConclusionList")), true);
eq("한 주 해설 ← weekly_intro",
  /같은 방향을 가리켰다/.test(html("agendaNarrativeBody")), true);
eq("왜 중요한가 ← policy_shifts[].so_what",
  /후속 6기 심사 일정/.test(html("agendaSoWhatList")), true);
eq("지금 확인할 것 ← watchpoints",
  /공청회 일정/.test(html("agendaWatchList")), true);
// what 과 so_what 이 뒤바뀌면 위 넷은 다 통과한다 — 서로의 자리를 침범하지
// 않는지까지 봐야 그 실수가 잡힌다.
eq("what 이 '왜 중요한가'로 새지 않는다",
  /계속운전을 승인/.test(html("agendaSoWhatList")), false);
eq("so_what 이 '한눈에 보기'로 새지 않는다",
  /후속 6기/.test(html("agendaConclusionList")), false);
eq("해설은 산문 클래스로 나간다",
  /class="agenda-narrative"/.test(html("agendaNarrativeBody")), true);
eq("네 영역 전부 열려 있다",
  ["agendaConclusions", "agendaNarrative", "agendaSoWhat", "agendaWatch"]
    .every(id => drawn.get(id).hidden === false), true);
eq("리포트가 있으면 '집계 중'은 안 뜬다", drawn.get("agendaPending").hidden, true);
eq("블록 자체는 보인다", drawn.get("todayAgenda").hidden, false);

// 내용이 없는 영역은 라벨만 남기지 않고 통째로 접는다.
const partial = renderWith({
  week_start: "2026-08-15", week_end: "2026-08-21",
  policy_shifts: [{ what: "결론만 있는 주" }], watchpoints: [],
}, { date: "2026-08-22", issues: [] });
eq("해설이 없으면 그 영역을 접는다", partial.get("agendaNarrative").hidden, true);
eq("so_what 이 없으면 그 영역을 접는다", partial.get("agendaSoWhat").hidden, true);
eq("watchpoints 가 없으면 그 영역을 접는다", partial.get("agendaWatch").hidden, true);
eq("남은 영역은 그대로 뜬다", partial.get("agendaConclusions").hidden, false);
eq("한 영역이라도 있으면 블록은 보인다", partial.get("todayAgenda").hidden, false);

// 리포트 자체가 없는 날 — 기존 '집계 중' 처리는 그대로다.
const none = renderWith(null, { date: "2026-08-22", issues: [] });
eq("리포트가 없으면 사유를 말한다", none.get("agendaPending").hidden, false);
eq("네 영역은 전부 접힌다",
  ["agendaConclusions", "agendaNarrative", "agendaSoWhat", "agendaWatch"]
    .every(id => none.get(id).hidden === true), true);

// 카드가 이미 낸 문장은 여기서 다시 내지 않는다 — 통합해도 그 철학은 그대로다.
const deduped = renderWith(full, {
  date: "2026-08-22",
  issues: [{ title: "원안위가 고리 3호기 계속운전을 승인했다" }],
});
eq("카드에 있는 문장은 한눈에 보기에서 빠진다",
  /고리 3호기 계속운전을 승인/.test(deduped.get("agendaConclusionList").innerHTML), false);
eq("남은 결론은 그대로 있다",
  /12차 전기본 초안/.test(deduped.get("agendaConclusionList").innerHTML), true);

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
