// 앞으로 30일 달력이 화면에서 어떻게 서는가. 실행: node web/tests/event_calendar.mjs
// (의존성 없음 — 데이터도 안 읽는 순수 렌더 검사다.)
//
// 여기서 잠그는 것은 다섯이다.
//   ① 격자가 요일에 맞는다 — 첫날 앞의 빈 칸 수가 그날 요일이고, 칸 수는 7의 배수.
//   ② 이미 시작한 기간이 사라지지 않는다 — 창 첫날에 서고, 지나가는 칸이 물든다.
//   ③ 칸당 셋까지만 세우고 나머지는 "+N" 으로 접는다.
//   ④ 달까지만 나온 일정은 **격자 밖**에 선다 — 그 달 1일 칸에 넣던 것이
//      지난 코너를 끄게 만든 오류다(event_calendar.py 머리말 ③).
//   ⑤ 재료가 한 건도 없으면 구역이 통째로 내려간다 — 빈 격자는 고장으로 읽힌다.
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. weekly_sections.mjs 와 같은 방식으로 함수 블록만 잘라 평가한다.
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

// 상수도 app.js 에서 그대로 가져온다. 여기에 값을 다시 적으면 검사가 자기가
// 쓴 값을 확인하게 된다(칸당 상한이 5로 바뀌어도 통과해 버린다).
function constant(name) {
  const found = new RegExp(`const ${name} = [^;]+;`).exec(source);
  if (!found) throw new Error(`app.js 에 ${name} 선언이 없다`);
  return found[0];
}

// 최소한의 DOM. 렌더러가 만지는 노드만 흉내 내고, 나머지는 없는 것으로 둔다 —
// 없는 노드를 찾으면 null 이 나와야 hideCalendarPopover 의 가드가 실제로 검사된다.
function makeDom() {
  const nodes = new Map();
  for (const id of ["eventCalendar", "eventCalendarMeta", "eventCalendarGrid",
                    "eventCalendarMonths", "eventCalendarMonthList"]) {
    nodes.set(id, { id, hidden: false, innerHTML: "", textContent: "" });
  }
  return { nodes, getElementById: id => nodes.get(id) || null };
}

const api = new Function("document", "state", `
  ${constant("CAL_WEEKDAYS")}
  ${constant("CAL_MAX_CHIPS")}
  ${constant("CAL_KIND_LABELS")}
  ${extract("esc")}
  ${extract("safeUrl")}
  ${extract("dateLabel")}
  ${extract("shiftDate")}
  ${extract("calendarData")}
  ${extract("calWeekday")}
  ${constant("CAL_ROLE_TAILS")}
  ${extract("calendarLayout")}
  ${extract("calendarChip")}
  ${extract("calendarCell")}
  ${extract("calendarMonthLabel")}
  ${extract("calendarWhen")}
  ${extract("calendarEventBlock")}
  ${extract("hideCalendarPopover")}
  ${extract("renderEventCalendar")}
  return { CAL_MAX_CHIPS, calendarLayout, calendarCell, calendarWhen,
           calendarEventBlock, renderEventCalendar };
`);

const cases = [];
const check = (label, ok, detail = "") => cases.push({ label, ok, detail });
const has = (label, html, needle) => check(label, html.includes(needle), `없음: ${needle}`);
const hasnt = (label, html, needle) => check(label, !html.includes(needle), `있으면 안 됨: ${needle}`);
const count = (html, needle) => html.split(needle).length - 1;

function event(overrides) {
  return {
    id: "ev-1", date: "2026-09-01", end_date: "2026-09-01", kind: "point",
    label: "한수원 토론회", clause: "한수원이 9월 1일 토론회를 개최할 예정이다.",
    title: "한수원, 토론회 개최", url: "https://example.com/a",
    publisher: "테스트", sources: [], source_count: 1,
    first_seen: "2026-08-20", ...overrides,
  };
}

function render(calendar) {
  const dom = makeDom();
  const bound = api(dom, { trend: { event_calendar: calendar } });
  bound.renderEventCalendar();
  return { dom, api: bound };
}

// 2026-08-29 는 토요일이다 — 창의 첫날 앞에 빈 칸이 여섯 개 선다.
const WINDOW = { start: "2026-08-29", end: "2026-09-28", days: 30 };

// ── ① 격자가 요일에 맞는가 ────────────────────────────────────────────────
{
  const { dom } = render({ ...WINDOW, events: [event({})], month_notes: [] });
  const grid = dom.nodes.get("eventCalendarGrid").innerHTML;
  check("요일 머리 7칸", count(grid, 'class="cal-head') === 7);
  check("토요일로 시작하므로 앞 빈 칸이 여섯",
    count(grid.split("is-today")[0], "is-blank") === 6,
    `실제 ${count(grid.split("is-today")[0], "is-blank")}`);
  const cells = count(grid, 'class="cal-cell');
  check(`칸 수가 7의 배수 (지금 ${cells})`, cells % 7 === 0);
  has("첫 칸이 오늘", grid, "cal-today");
  has("달이 바뀌는 1일은 달을 함께 적는다", grid, ">9/1<");
  hasnt("그 밖의 날에는 달을 안 적는다", grid, ">9/2<");
  check("구역이 서 있다", dom.nodes.get("eventCalendar").hidden === false);
  has("머리말이 창과 건수를 말한다",
    dom.nodes.get("eventCalendarMeta").textContent, "일정 1건");
}

// ── ② 기간은 양끝에 선다 ──────────────────────────────────────────────────
//
// 칸을 물들여 막대를 대신하던 것을 걷어낸 자리다. 실데이터에서 입법예고 하나가
// 9/2~10/13 이라 31칸 중 27칸이 물들었고, 그 순간 달력이 배경색이 됐다.
{
  const running = event({ id: "ev-r", kind: "range", date: "2026-08-23",
                          end_date: "2026-09-02", label: "포항 집회" });
  const { dom, api: bound } = render({ ...WINDOW, events: [running], month_notes: [] });
  const grid = dom.nodes.get("eventCalendarGrid").innerHTML;
  const today = grid.split("is-today")[1].split('<div class="cal-cell')[0];
  has("창 밖에서 시작해도 첫날에 선다", today, "포항 집회");
  has("그 칩은 '진행 중'이라고 말한다", today, "진행 중");
  check("양끝 둘만 선다 — 지나가는 칸은 건드리지 않는다",
    count(grid, "포항 집회") === 2, `실제 ${count(grid, "포항 집회")}`);
  has("끝나는 날에도 선다", grid, "종료");
  const layout = bound.calendarLayout({ ...WINDOW, events: [running] });
  check("layout 은 창 첫날에 앉힌다", layout.byDay.has("2026-08-29"));
  check("끝나는 날에도 앉힌다", layout.byDay.has("2026-09-02"));
  check("둘은 같은 일정 하나다",
    layout.byDay.get("2026-08-29")[0].event.id === layout.byDay.get("2026-09-02")[0].event.id);
  check("기간의 라벨은 양끝을 다 말한다",
    bound.calendarWhen(running) === "8월 23일 ~ 9월 2일");
}
{
  // 끝이 창 밖이면 끝 칩은 서지 않는다 — 없는 칸에 세울 수 없다.
  const long = event({ id: "ev-l", kind: "range", date: "2026-09-02",
                       end_date: "2026-10-13", label: "해수부 입법예고" });
  const { dom } = render({ ...WINDOW, events: [long], month_notes: [] });
  const grid = dom.nodes.get("eventCalendarGrid").innerHTML;
  check("창 안에는 시작 칩 하나뿐", count(grid, "해수부 입법예고") === 1);
  has("시작이라고 말한다", grid, "시작");
}

// ── ③ 칸당 상한과 접기 ────────────────────────────────────────────────────
{
  const many = [1, 2, 3, 4, 5].map(index =>
    event({ id: `ev-${index}`, label: `일정 ${index}` }));
  const { dom } = render({ ...WINDOW, events: many, month_notes: [] });
  const grid = dom.nodes.get("eventCalendarGrid").innerHTML;
  check("칸에 서는 칩은 셋", count(grid, "cal-chip is-") === 3);
  has("나머지는 접는다", grid, ">+2<");
  has("접은 것을 여는 문이 그날이다", grid, 'data-cal-day="2026-09-01"');
  hasnt("접힌 칩은 화면에 없다", grid, "일정 4");
}

// ── ④ 달까지만 나온 일정 ──────────────────────────────────────────────────
{
  const { dom } = render({ ...WINDOW, events: [event({})], month_notes: [
    { month: "2026-09", label: "정부 프로젝트 발표", clause: "9월 중 발표할 예정이다.",
      title: "정부, 9월 발표 예정", url: "https://example.com/m", publisher: "테스트" },
  ] });
  const grid = dom.nodes.get("eventCalendarGrid").innerHTML;
  hasnt("월 정밀도가 날짜 칸에 들어가지 않는다", grid, "정부 프로젝트 발표");
  const strip = dom.nodes.get("eventCalendarMonthList").innerHTML;
  check("스트립이 서 있다", dom.nodes.get("eventCalendarMonths").hidden === false);
  has("'9월 중' 으로 적는다", strip, "9월 중");
  has("출처가 함께 선다", strip, 'href="https://example.com/m"');
}

// ── ⑤ 재료가 없을 때 ──────────────────────────────────────────────────────
{
  const { dom } = render({ ...WINDOW, events: [], month_notes: [] });
  check("한 건도 없으면 구역이 내려간다", dom.nodes.get("eventCalendar").hidden === true);
}
{
  const { dom } = render(null);
  check("payload 자체가 없어도 죽지 않는다", dom.nodes.get("eventCalendar").hidden === true);
}

// ── 상세와 이스케이프 ─────────────────────────────────────────────────────
{
  const { api: bound } = render({ ...WINDOW, events: [event({})], month_notes: [] });
  const block = bound.calendarEventBlock(event({
    label: '<img src=x onerror="alert(1)">',
    sources: [{ title: "출처 기사", url: "https://example.com/a", publisher: "테스트" }],
    issue_id: "issue-9",
  }));
  hasnt("라벨은 마크업이 아니다", block, "<img");
  has("근거 문장이 그대로 실린다", block, "9월 1일 토론회를 개최할 예정이다");
  has("출처 링크", block, 'href="https://example.com/a"');
  has("이슈가 있으면 그리로 건넨다", block, 'data-cal-issue="issue-9"');
  const plain = bound.calendarEventBlock(event({}));
  hasnt("이슈가 없으면 버튼도 없다", plain, "data-cal-issue");
}

const failed = cases.filter(row => !row.ok);
for (const row of cases) console.log(`${row.ok ? "  ok" : "FAIL"}  ${row.label}${row.ok ? "" : ` — ${row.detail}`}`);
console.log(`\n${cases.length - failed.length}/${cases.length} 통과`);
if (failed.length) process.exit(1);
