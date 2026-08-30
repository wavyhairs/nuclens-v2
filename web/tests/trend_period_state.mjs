// 흐름 탭의 기간 선택·상태 표시 회귀 방지선.
// 실행: node web/tests/trend_period_state.mjs  (의존성 없음)
//
// 세 가지를 검사한다:
//   1. comparisonModeFor — 키워드 표와 워드 클라우드가 '비교 가능한가'를
//      같은 소스(previous_period_complete)로 판정하는가. 각자 다시 판정하면
//      기간을 바꿨을 때 한쪽만 조용히 배지를 잃는다(2026-08-30 사용자 지적).
//   2. keywordStatusTone — delta<0(감소)이 '이어짐'(변화 없음과 같은 문구)으로
//      묶이던 버그. 옆 칸에는 "−5"가 찍혀 있는데 상태 칸이 "변화 없다"고
//      말하는 모순이 있었다.
//   3. topicFlowPattern — 첫 주·마지막 주만 비교하면 '10 → 4 → 9 → 10'이
//      '변화 없음'이 된다. 중간 굴곡을 실제로 반영하는지 7개 패턴 전부 확인.
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. date_window.mjs 와 같은 방식으로 함수·상수 블록만 잘라 평가한다.
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

// const 하나를 통째로 잘라 온다. 값이 객체({...})면 중괄호 균형까지, 아니면
// 다음 세미콜론까지 — KEYWORD_STATUS 같은 다줄 객체 상수를 그대로 쓴다.
function extractConst(name) {
  const marker = `const ${name} = `;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`app.js 에 ${name} 상수가 없다`);
  const afterEq = start + marker.length;
  if (source[afterEq] === "{") {
    let depth = 0;
    for (let i = afterEq; i < source.length; i += 1) {
      if (source[i] === "{") depth += 1;
      else if (source[i] === "}" && (depth -= 1) === 0) {
        const semi = source.indexOf(";", i);
        return source.slice(start, semi + 1);
      }
    }
    throw new Error(`${name} 객체가 안 닫힌다`);
  }
  const semi = source.indexOf(";", start);
  return source.slice(start, semi + 1);
}

const api = new Function(`
  ${extract("isWeekPeriod")}
  ${extract("comparisonModeFor")}
  ${extractConst("KEYWORD_STATUS")}
  ${extract("keywordStatusTone")}
  ${extractConst("TOPIC_FLOW_STEP_FLOOR")}
  ${extractConst("TOPIC_FLOW_STEP_RATIO")}
  ${extract("topicFlowStepSign")}
  ${extractConst("TOPIC_FLOW_SAMPLE_MIN")}
  ${extract("topicFlowPattern")}
  return { comparisonModeFor, KEYWORD_STATUS, keywordStatusTone, topicFlowPattern };
`)();

const cases = [];
const eq = (label, got, want) => cases.push({ label, got, want, ok: JSON.stringify(got) === JSON.stringify(want) });

// weekMode 인자는 기본값이 isWeekPeriod() 호출인데, 그 함수는 state.period 를
// 본다. 기본 인자는 '생략됐을 때' 평가되므로(값이 쓰이는지와 무관), 여기서는
// 항상 명시 인자로만 불러 그 평가 자체를 건드리지 않는다.
// ── 1. comparisonModeFor — 키워드 표·워드 클라우드가 공유하는 단일 판정원. ──
eq("previous_period_complete=true → 비교 가능",
  api.comparisonModeFor({ previous_period_complete: true }, false), true);
eq("previous_period_complete=false → 비교 불가(기간이 길어도 archive 가 짧으면)",
  api.comparisonModeFor({ previous_period_complete: false }, false), false);
eq("pdata 자체가 없는 구버전 trend.json → weekMode 로 물러난다(7일=true)",
  api.comparisonModeFor(null, true), true);
eq("pdata 없음 · weekMode=false(7일이 아님) → 비교 불가",
  api.comparisonModeFor(null, false), false);

// ── 2. keywordStatusTone — 감소가 '이어짐'으로 묶이던 버그의 재발 방지. ──
eq("신규가 최우선", api.keywordStatusTone({ isNew: true, delta: -5 }), "new");
eq("증가(delta>0)", api.keywordStatusTone({ isNew: false, delta: 4 }), "up");
// 예전 버그: delta 가 3 미만이면 부호와 무관하게 전부 '이어짐' 이었다.
// -1처럼 작은 감소도 '증가'나 '보합'이 아니라 분명히 '감소'여야 한다.
eq("작은 감소도 '감소' — 예전엔 델타<3 이면 부호 무시하고 '이어짐' 이었다",
  api.keywordStatusTone({ isNew: false, delta: -1 }), "down");
eq("큰 감소도 '감소'", api.keywordStatusTone({ isNew: false, delta: -8 }), "down");
eq("delta=0 → 보합", api.keywordStatusTone({ isNew: false, delta: 0 }), "flat");
eq("KEYWORD_STATUS 에 네 상태 모두 아이콘·라벨이 있다",
  ["new", "up", "down", "flat"].every(tone => api.KEYWORD_STATUS[tone]?.icon && api.KEYWORD_STATUS[tone]?.label),
  true);
eq("감소 라벨이 '이어짐'이 아니라 '감소'다",
  api.KEYWORD_STATUS.down.label, "감소");

// ── 3. topicFlowPattern — 4주 전체 흐름을 반영하는 패턴 분류. ──
// 표본이 문턱(8건) 밑이면 어떤 굴곡도 잡음이다.
eq("표본 미달 → 보합", api.topicFlowPattern([2, 1, 2, 1], 6), { tone: "flat", label: "보합" });

// 사용자가 든 그 예시: 첫 주·마지막 주가 같아도(10→10) 가운데 굴곡이
// '변화 없음'을 덮으면 안 된다.
eq("10 → 4 → 9 → 10 = 감소 후 회복 (변화없음이 아니다)",
  api.topicFlowPattern([10, 4, 9, 10], 33), { tone: "mixed", label: "감소 후 회복" });

eq("지속 증가", api.topicFlowPattern([5, 8, 12, 18], 43), { tone: "up", label: "지속 증가" });
eq("지속 감소", api.topicFlowPattern([18, 12, 8, 5], 43), { tone: "down", label: "지속 감소" });
eq("증가 후 둔화 — 끝값이 시작값보다 뚜렷이 낮다",
  api.topicFlowPattern([5, 20, 15, 2], 42), { tone: "mixed", label: "증가 후 둔화" });
eq("일시 급증 — 솟았다가 시작값 언저리로 돌아온다",
  api.topicFlowPattern([5, 25, 6, 5], 41), { tone: "mixed", label: "일시 급증" });
eq("등락 반복 — 방향이 두 번 이상 바뀐다",
  api.topicFlowPattern([5, 15, 6, 16], 42), { tone: "mixed", label: "등락 반복" });
eq("보합 — 값이 문턱 안에서만 흔들린다",
  api.topicFlowPattern([10, 11, 10, 11], 42), { tone: "flat", label: "보합" });
eq("3주(span=3)에서도 동작 — 등락 반복은 아니어도 굴곡은 잡는다",
  api.topicFlowPattern([10, 4, 9], 23), { tone: "mixed", label: "감소 후 회복" });

const failed = cases.filter(row => !row.ok);
for (const row of cases) {
  console.log(`${row.ok ? "ok  " : "FAIL"} ${row.label} → ${JSON.stringify(row.got)}${row.ok ? "" : ` (기대 ${JSON.stringify(row.want)})`}`);
}
if (failed.length) {
  console.error(`\n${failed.length}/${cases.length} 실패`);
  process.exit(1);
}
console.log(`\n${cases.length}건 전부 통과`);
