// 주간 판세의 결정적 코너가 화면에서 어떻게 서는가 — 핵심사건·국가별 단신·
// 발간물·예정. 실행: node web/tests/weekly_sections.mjs  (의존성 없음)
//
// 여기서 잠그는 것은 두 가지다.
//   ① 재료를 다시 고르지 않는다 — 저장본에 있는 줄만, 있는 순서대로 낸다.
//   ② 국가명·날짜는 제목과 **다른 칸**에 선다. 한 줄로 이으면 '한국정부'처럼
//      한 낱말로 읽힌다(참고 구현에서 실제로 그렇게 나갔다).
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. weekly_selector.mjs 와 같은 방식으로 함수 블록만 잘라 평가한다.
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

const api = new Function(`
  ${extract("esc")}
  ${extract("weeklySection")}
  ${extract("weeklyStoryTitle")}
  ${extract("weeklyTopStories")}
  ${extract("weeklyCountryBriefs")}
  ${extract("weeklyPublications")}
  ${extract("weeklyUpcoming")}
  return { weeklySection, weeklyTopStories, weeklyCountryBriefs,
           weeklyPublications, weeklyUpcoming };
`)();

const cases = [];
const check = (label, ok, detail = "") => cases.push({ label, ok, detail });
const has = (label, html, needle) =>
  check(label, html.includes(needle), `없음: ${needle}`);
const hasnt = (label, html, needle) =>
  check(label, !html.includes(needle), `있으면 안 됨: ${needle}`);

// ── 1. 핵심사건 — 저장본 순서 그대로, 보도 폭은 여러 건일 때만.
const top = api.weeklyTopStories([
  { key: "aaaa1111", title: "두산에너빌리티, SMR 주기기 공급계약 체결",
    summary: "공급계약을 체결했다.", articles: 5, outlets: 4,
    issue_id: "issue-1" },
  { key: "bbbb2222", title: "국회, 전력망 3법 통과",
    summary: "", articles: 1, outlets: 1 },
]);
has("첫 사건이 먼저", top.slice(0, top.indexOf("국회")), "두산에너빌리티");
has("이슈가 있으면 상세로 가는 버튼", top, 'data-issue-id="issue-1"');
has("보도 폭은 숫자로", top, "이어지는 이슈 · 5건 · 매체 4곳");
check("한 건짜리에는 보도 폭을 안 붙인다",
  (top.match(/이어지는 이슈/g) || []).length === 1);
has("이슈가 없으면 버튼 대신 제목", top, "<strong>국회, 전력망 3법 통과</strong>");
check("데이터 없으면 빈 문자열", api.weeklyTopStories([]) === "");
check("빈 본문이면 코너 자체가 안 선다",
  api.weeklySection("이번 주", "8월 15일–21일", api.weeklyTopStories([])) === "");

// ── 2. 국가별 단신 — 국가명이 제목과 다른 칸.
const briefs = api.weeklyCountryBriefs([
  { key: "cccc3333", country: "KR", country_kr: "한국",
    title: "정부, 신규 원전 건설 계획 확정", issue_id: "issue-2" },
  { key: "dddd4444", country: "US", country_kr: "미국",
    title: "현대건설, 테라파워 EPC 우선권 확보" },
]);
has("국가명은 라벨 칸에", briefs, '<p class="weekly-brief-country">한국</p>');
hasnt("국가명과 제목이 붙지 않는다", briefs, "한국정부");
hasnt("두 번째 줄도 마찬가지", briefs, "미국현대건설");
check("국가 수만큼 줄", (briefs.match(/weekly-brief-country/g) || []).length === 2);

// ── 3. 발간물 — 기관·제목·원문 링크.
const pubs = api.weeklyPublications([
  { org: "국제원자력기구(IAEA)", title: "원자력 전망 2026",
    url: "https://iaea.org/a", date: "2026-08-18", gist: "설비 신뢰성 지침" },
  { org: "", title: "기관 없는 문서", url: "https://x.test/b", date: "2026-08-17" },
]);
has("발행기관", pubs, "국제원자력기구(IAEA)");
has("원문 링크", pubs, 'href="https://iaea.org/a"');
has("새 탭으로", pubs, 'rel="noopener"');
check("기관이 없으면 바이라인을 만들지 않는다",
  (pubs.match(/weekly-pub-org/g) || []).length === 1);

// ── 4. 예정 — 날짜 칸. 정밀도가 '월'이면 날짜를 지어내지 않는다.
const upcoming = api.weeklyUpcoming([
  { key: "eeee5555", title: "한수원, 건식저장시설 주민 설명회", date: "2026-09-01",
    precision: "day", issue_id: "issue-3" },
  { key: "ffff6666", title: "제12차 전기본 확정", date: "2026-10-01",
    precision: "month" },
]);
has("일 정밀도는 월·일", upcoming, "9월 1일");
has("월 정밀도는 월까지만", upcoming, ">10월<");
hasnt("월 정밀도에 1일을 붙이지 않는다", upcoming, "10월 1일");
hasnt("날짜와 제목이 붙지 않는다", upcoming, "9월 1일한수원");

// ── 5. 코너는 리포트 하나에서 나온다 — 렌더러가 기간을 따로 계산하지 않는다.
const render = source.slice(source.indexOf("function renderWeeklyReport("))
  .slice(0, 2600);
check("코너가 리포트의 week_start/week_end 로 라벨을 만든다",
  /report\.week_start/.test(render) && /report\.week_end/.test(render));
check("코너 넷이 전부 리포트에서 온다",
  ["report.top_stories", "report.country_briefs", "report.publications",
    "report.upcoming"].every(name => render.includes(name)));
check("렌더러가 오늘 날짜로 기간을 다시 만들지 않는다",
  !/new Date\(\)/.test(render));

const failed = cases.filter(row => !row.ok);
for (const row of cases) {
  console.log(`${row.ok ? "ok  " : "FAIL"} ${row.label}${row.ok ? "" : ` — ${row.detail}`}`);
}
if (failed.length) {
  console.error(`\n${failed.length}/${cases.length} 실패`);
  process.exit(1);
}
console.log(`\n${cases.length}건 전부 통과`);
