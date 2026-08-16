// 집계 창 산술 검사 — '최근 7일'이 8일이 되는 off-by-one 재발 방지.
// 실행: node web/tests/date_window.mjs  (의존성 없음)
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. 그래서 소스에서 해당 함수 블록만 잘라 평가한다 — 함수 이름이 바뀌면
// 조용히 통과하지 않고 여기서 먼저 깨지도록 이름으로 찾는다.
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

const {
  shiftDate, weekRange, briefingWeek, weekRangeLabel, trendStart, timeLabel,
  periodLabel, previousPeriodLabel, previousPeriodRange,
} = new Function(`
  ${extract("shiftDate")}
  ${extract("weekRange")}
  ${extract("briefingWeek")}
  ${extract("dateLabel")}
  ${extract("weekRangeLabel")}
  ${extract("todayKST")}
  ${extract("dateTimeLabel")}
  ${extract("timeLabel")}
  ${extract("periodLabel")}
  ${extract("previousPeriodLabel")}
  ${extract("previousPeriodRange")}
  // periodLabel/previousPeriodRange 의 기본 인자는 state 를 본다 — 여기서는 항상
  // 명시 인자로만 부르므로 참조만 살려 둔다.
  const state = { period: 7 };
  // trendRange() 는 state/meta 에 얽혀 있어 창 계산부만 같은 헬퍼로 재현한다.
  const trendStart = (end, days) => shiftDate(end, -(days - 1));
  return { shiftDate, weekRange, briefingWeek, weekRangeLabel, trendStart, timeLabel,
           periodLabel, previousPeriodLabel, previousPeriodRange };
`)();

const cases = [];
const eq = (label, got, want) => cases.push({ label, got, want, ok: got === want });

// 핵심 회귀: KST 자정을 UTC 로 되돌리며 하루가 더 빠지던 지점.
eq("weekRange 08-09 시작", weekRange("2026-08-09").start, "2026-08-03");
eq("weekRange 08-09 끝", weekRange("2026-08-09").end, "2026-08-09");
eq("주간 창 길이 = 7일", 1 + (Date.parse("2026-08-09") - Date.parse(weekRange("2026-08-09").start)) / 86400000, 7);

// 월·연 경계에서도 문자열 산술이 성립하는가.
eq("월 경계", weekRange("2026-03-02").start, "2026-02-24");
eq("윤년 2월", shiftDate("2028-03-01", -1), "2028-02-29");
eq("연 경계", shiftDate("2026-01-01", -1), "2025-12-31");

// 주간 리포트 구간은 **토~금**이다 — weekly_bot 이 금요일에 돌며 직전 7일을 묶고
// (week_start = 실행일 -6), 저장된 값도 8/1~8/7 · 8/8~8/14 로 그렇다. ISO 주차
// (월~일)로 계산하면 하루씩 밀려 매칭이 통째로 비고, 화면은 '집계 중'만 뜬다.
eq("수요일 → 그 주 토~금", briefingWeek("2026-08-05").start, "2026-08-01");
eq("수요일 끝", briefingWeek("2026-08-05").end, "2026-08-07");
eq("월요일 → 직전 토 시작", briefingWeek("2026-08-10").start, "2026-08-08");
eq("월요일 끝", briefingWeek("2026-08-10").end, "2026-08-14");
eq("일요일 → 다음 금까지", briefingWeek("2026-08-16").start, "2026-08-15");
eq("일요일 끝", briefingWeek("2026-08-16").end, "2026-08-21");
// 경계 이틀: 금요일은 자기 주의 마지막 날, 토요일은 다음 주의 첫날.
eq("금요일은 그 주 끝", briefingWeek("2026-08-14").end, "2026-08-14");
eq("금요일 시작", briefingWeek("2026-08-14").start, "2026-08-08");
eq("토요일은 새 주 시작", briefingWeek("2026-08-15").start, "2026-08-15");
eq("주간 구간 길이 = 7일", 1 + (Date.parse(briefingWeek("2026-08-16").end)
  - Date.parse(briefingWeek("2026-08-16").start)) / 86400000, 7);
eq("빈 날짜는 빈 구간", briefingWeek("").start, "");

// 제목: 같은 달이면 뒤쪽 달 이름을 뺀다. 달을 넘으면 둘 다 적는다.
eq("같은 달 라벨", weekRangeLabel("2026-08-10"), "8월 8일–14일");
eq("달 넘는 라벨", weekRangeLabel("2026-08-01"), "8월 1일–7일");
eq("월 경계 라벨", weekRangeLabel("2026-07-30"), "7월 25일–31일");
eq("연 경계 라벨", weekRangeLabel("2026-01-01"), "2025년 12월 27일–1월 2일".slice(6));

// 트렌드 토글: 7일은 7일, 30일은 30일.
eq("trend 7일 창", trendStart("2026-08-10", 7), "2026-08-04");
eq("trend 30일 창", trendStart("2026-08-10", 30), "2026-07-12");

// 빈 값이 들어와도 RangeError 로 렌더를 죽이지 않는다(옛 코드는 throw 했다).
eq("빈 날짜는 빈 문자열", shiftDate("", -6), "");

// 낡은 수집 시각이 오늘로 읽히지 않는가 — 시:분만 찍던 자리.
// 오늘 것은 시:분만, 어제 이전 것은 날짜를 달고 나와야 한다.
const nowKST = timeLabel(new Date().toISOString());
const oldStamp = timeLabel("2026-08-09T06:54:30+09:00");
eq("오늘 시각은 HH:MM", /^\d{2}:\d{2}$/.test(nowKST), true);
eq("지난 시각은 날짜 동반", /^\d{2}:\d{2}$/.test(oldStamp), false);
eq("지난 시각에 월·일 포함", /8[.\s]*\s*9/.test(oldStamp), true);

// 키워드 비교의 상대 구간은 기간 토글을 따라간다. 산술은 build_data.py 의
//   previous_end   = requested_start - 1일
//   previous_start = previous_end - (days - 1)
// 과 한 글자도 어긋나면 안 된다 — 어긋나면 화면이 없는 주를 비교 상대로 적는다.
const prev = (days, requestedStart) =>
  previousPeriodRange({ days, requested_start: requestedStart, previous_period_complete: true });
eq("직전 7일 끝", prev(7, "2026-08-10").end, "2026-08-09");
eq("직전 7일 시작", prev(7, "2026-08-10").start, "2026-08-03");
eq("직전 30일 시작", prev(30, "2026-07-18").start, "2026-06-18");
eq("직전 30일 끝", prev(30, "2026-07-18").end, "2026-07-17");
eq("직전 분기 시작", prev(90, "2026-05-19").start, "2026-02-18");
eq("직전 구간 길이 = 기간 길이", 1 + (Date.parse(prev(90, "2026-05-19").end)
  - Date.parse(prev(90, "2026-05-19").start)) / 86400000, 90);
eq("직전 구간이 미축적이면 없음",
  previousPeriodRange({ days: 30, requested_start: "2026-07-18", previous_period_complete: false }), null);
eq("periods 없는 옛 데이터도 죽지 않는다", previousPeriodRange(null), null);

// 라벨도 기간을 따라간다 — '전주'는 7일에서만 맞는 말이다.
eq("30일 현재 라벨", periodLabel(30), "최근 30일");
eq("30일 비교 라벨", previousPeriodLabel(30), "직전 30일");
eq("분기 비교 라벨", previousPeriodLabel(90), "직전 분기");
eq("1년 비교 라벨", previousPeriodLabel("365"), "직전 1년");

const failed = cases.filter(row => !row.ok);
for (const row of cases) {
  console.log(`${row.ok ? "ok  " : "FAIL"} ${row.label} → ${row.got}${row.ok ? "" : ` (기대 ${row.want})`}`);
}
if (failed.length) {
  console.error(`\n${failed.length}/${cases.length} 실패`);
  process.exit(1);
}
console.log(`\n${cases.length}건 전부 통과`);
