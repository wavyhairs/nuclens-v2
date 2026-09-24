// 이슈 상세의 「주요 사건 타임라인」이 **다른 사건**을 보여주는가.
// 실행: node web/tests/thread_dialog_section.mjs  (의존성 없음)
//
// 왜 이 검사가 있나
// -----------------
// 2026-09-20 까지 이 자리는 같은 이슈의 기사 목록이었다. 이름만 타임라인이고
// 내용은 근거 목록이라, 8/23 SAR 정책 발표와 9/18 SAR 시행이 **이미 같은
// 스토리로 이어져 있는데도** 9/18 상세에서는 그 사실을 볼 길이 없었다
// (`thread-5aaa7dea33e702ac`, stage_progress, 라이브 threads.json 실측).
//
// 회귀가 나면 조용하다 — 상세는 멀쩡히 뜨고 근거 기사도 다 있다. 없는 것은
// "이 사건 앞에 무엇이 있었나" 하나뿐이라 화면을 봐도 고장으로 안 읽힌다.
//
// 아래 픽스처는 **라이브에서 그대로 떠 온 값**이다. 지어낸 모양이 아니라 실제
// 계약이라, 계약이 바뀌면 여기서 먼저 깨진다.
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. long_term_gate.mjs 와 같은 방식으로 함수 블록만 잘라 평가한다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

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

function extractConst(name) {
  const marker = `const ${name} = `;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`app.js 에 ${name} 상수가 없다`);
  const end = source.indexOf(";", start + marker.length);
  return source.slice(start, end + 1);
}

// ── 라이브 실측 픽스처 (2026-09-20, /data/threads.json · /data/issues.json) ──

const SAR_AUG = "issue-099ce0d43f46036a";   // 8/23 정책 발표·예고
const SAR_SEP = "story-9e227f9daff25cd5";   // 9/18 실제 시행 · 이번 브리핑
const SAR_THREAD = "thread-5aaa7dea33e702ac";

const SAR_AUG_TITLE = "기후부, 전력망 효율 위해 9월부터 '계절별 송전용량' 시범 적용";
const SAR_SEP_TITLE = "기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행";

// 화면에 실제로 찍히는 모양. 8/23 제목에는 작은따옴표가 들어 있어 esc() 가
// &#39; 로 바꾼다 — 날것으로 비교하면 검사가 늘 실패한다.
const AUG_SHOWN = SAR_AUG_TITLE.replace(/'/g, "&#39;");
const SEP_SHOWN = SAR_SEP_TITLE;

function threadsPayload(overrides = {}) {
  return {
    version: "thread-web-v2",
    visible: true,
    hide_after: "2026-09-23T00:45:16+09:00",
    redirects: {},
    threads: [{
      thread_id: SAR_THREAD,
      title: SAR_SEP_TITLE,
      first_seen: "2026-08-24",
      last_seen: "2026-09-19",
      lifespan_days: 26,
      event_count: 2,
      units: [], entity_ids: [], unit_labels: [], entity_labels: [],
      events: [
        { event_id: SAR_SEP, title: SAR_SEP_TITLE, date: "2026-09-19", briefing_count: 1 },
        { event_id: SAR_AUG, title: SAR_AUG_TITLE, date: "2026-08-24", briefing_count: 1 },
      ],
      // flow 는 시간순 — 흐름은 처음부터 읽어야 흐름이다.
      flow: [
        { event_id: SAR_AUG, title: SAR_AUG_TITLE, date: "2026-08-24",
          relation_to_next: "stage_progress", relation_label: "다음 단계" },
        { event_id: SAR_SEP, title: SAR_SEP_TITLE, date: "2026-09-19",
          relation_to_next: "", relation_label: "" },
      ],
    }],
    ...overrides,
  };
}

// 9/18 이슈 — 선정 1건 + 추가 근거 5건. 라이브 실측 그대로다.
function sepIssue(overrides = {}) {
  return {
    issue_id: SAR_SEP,
    title: SAR_SEP_TITLE,
    first_seen: "2026-09-19",
    last_seen: "2026-09-19",
    thread_id: SAR_THREAD,
    related_articles: [
      { hash: "9e227f9daff25cd5", member_role: "card" },
      { hash: "9a94d3be8028be9e", member_role: "evidence" },
      { hash: "527b2e1db7613a88", member_role: "evidence" },
      { hash: "110f52bd2668f703", member_role: "evidence" },
      { hash: "7c344963b55f3200", member_role: "evidence" },
      { hash: "346c7125d8123051", member_role: "evidence" },
    ],
    ...overrides,
  };
}

// 8/23 이슈 — 선정 1건 + 근거 2건. 9/18 과 해시가 하나도 겹치지 않는다.
function augIssue(overrides = {}) {
  return {
    issue_id: SAR_AUG,
    title: SAR_AUG_TITLE,
    first_seen: "2026-08-24",
    last_seen: "2026-08-24",
    thread_id: SAR_THREAD,
    related_articles: [
      { hash: "099ce0d43f46036a", member_role: "card" },
      { hash: "eead02e2334a6e47", member_role: "evidence" },
      { hash: "4602974590c8de51", member_role: "evidence" },
    ],
    ...overrides,
  };
}

// 픽스처를 떠 온 날. threadForIssue() 는 longTermVisible() 을 기본 인자로
// 불러 실제 Date.now() 로 hide_after 를 재므로, 벽시계를 그대로 두면 픽스처의
// 유효기한(09-23 00:45 KST)이 지나는 순간 스토리 화면이 숨고 타임라인 검사
// 10건이 한꺼번에 깨진다 — 2026-09-23 deploy web 이 그렇게 죽었다. 검사는
// 판정 논리를 재는 것이지 오늘 날짜를 재는 것이 아니라, 시계를 여기에 고정한다.
const NOW = Date.parse("2026-09-20T12:00:00+09:00");

// state 를 읽는 함수들이라 잘라낸 블록에 주입한다. 카탈로그에 두 이슈가 다 있는
// 것이 기본값 — 그래야 8/23 이 '누를 수 있는' 쪽으로 간다.
function build(state) {
  return new Function("state", "NOW", `
    // 잘라 온 함수들이 보는 Date 만 가린다 — now() 는 픽스처 날짜, 나머지는 그대로.
    const Date = class extends globalThis.Date {
      static now() { return NOW; }
      static parse(text) { return globalThis.Date.parse(text); }
    };
    ${extractConst("THREAD_CONTRACT")}
    ${extract("esc")}
    ${extract("dateLabel")}
    ${extract("currentIssueById")}
    ${extract("longTermVisible")}
    ${extract("longTermThreads")}
    ${extract("resolveThreadId")}
    ${extract("threadEventOpenable")}
    ${extract("threadPeriodText")}
    ${extract("threadForIssue")}
    ${extract("threadStepSources")}
    ${extract("threadStepRow")}
    ${extract("threadDialogSection")}
    return { threadDialogSection, threadForIssue };
  `)(state, NOW);
}

function defaultState(overrides = {}) {
  return {
    view: "search",
    issues: [sepIssue(), augIssue()],
    threads: threadsPayload(),
    ...overrides,
  };
}

let failures = 0;
function check(name, fn) {
  try { fn(); } catch (error) {
    failures += 1;
    console.error(`  ✗ ${name}\n    ${error.message}`);
    return;
  }
  console.log(`  ✓ ${name}`);
}

console.log("SAR 회귀 — 9/18 상세가 8/23 을 보여준다");

check("8/23 과 9/18 은 서로 다른 이슈다", () => {
  assert.notEqual(SAR_AUG, SAR_SEP);
  assert.notEqual(sepIssue().issue_id, augIssue().issue_id);
});

check("둘은 같은 thread_id 를 들고 있다", () => {
  assert.equal(sepIssue().thread_id, SAR_THREAD);
  assert.equal(augIssue().thread_id, SAR_THREAD);
});

check("9/18 상세의 주요 사건 타임라인에 8/23 이 뜬다", () => {
  const html = build(defaultState()).threadDialogSection(sepIssue());
  assert.ok(html.includes("주요 사건 타임라인"), "구역 제목이 없다");
  assert.ok(html.includes(AUG_SHOWN), "8/23 사건 제목이 없다");
  assert.ok(html.includes("8월 24일"), "8/23 사건 날짜가 없다");
});

check("8/23 이 9/18 **앞**에 선다 — 흐름은 처음부터 읽는다", () => {
  const html = build(defaultState()).threadDialogSection(sepIssue());
  const augAt = html.indexOf(AUG_SHOWN);
  assert.ok(augAt >= 0, "8/23 사건이 아예 없다");
  assert.ok(augAt < html.indexOf("is-current"), "지금 사건이 앞선 사건보다 위에 있다");
});

check("이음매의 말은 판정이 준 라벨뿐이다", () => {
  const html = build(defaultState()).threadDialogSection(sepIssue());
  assert.ok(html.includes("다음 단계"), "stage_progress 라벨이 없다");
  // UI 가 관계를 새로 지어내면 안 된다. 판정에 없던 말이 나오면 실패다.
  for (const invented of ["계획 → 시행", "관련", "후속", "연관"]) {
    assert.ok(!html.includes(invented), `없는 관계 문구가 들어갔다: ${invented}`);
  }
});

check("판정이 없는 이음매는 화살표만 남는다", () => {
  const bare = threadsPayload();
  bare.threads[0].flow[0].relation_label = "";
  const html = build(defaultState({ threads: bare })).threadDialogSection(sepIssue());
  assert.ok(html.includes("↓"), "화살표가 없다");
  assert.ok(!html.includes("다음 단계"), "없는 라벨이 남았다");
});

check("지금 보고 있는 사건은 링크가 아니다", () => {
  const html = build(defaultState()).threadDialogSection(sepIssue());
  assert.ok(html.includes("is-current"), "현재 사건 표시가 없다");
  assert.ok(!html.includes(`data-issue-id="${SAR_SEP}"`),
    "자기 자신을 여는 버튼이 생겼다 — 눌러도 아무 일이 없다");
  assert.ok(html.includes(`data-issue-id="${SAR_AUG}"`),
    "8/23 으로 가는 링크가 없다");
});

check("타임라인은 **사건 링크**만 싣는다 — 남의 기사를 끌어오지 않는다", () => {
  const html = build(defaultState()).threadDialogSection(sepIssue());
  // 대표 기사 해시는 사건 id 안에 들어 있는 것이 정상이다(issue-<hash>). 여기서
  // 보는 것은 **근거 기사**가 남의 타임라인으로 샜는가다.
  const evidence = augIssue().related_articles
    .filter(row => row.member_role === "evidence").map(row => row.hash);
  assert.equal(evidence.length, 2, "8/23 근거 기사 수가 바뀌었다 — 픽스처를 다시 떠라");
  for (const hash of evidence) {
    assert.ok(!html.includes(hash), `8/23 의 근거 기사가 9/18 타임라인에 섞였다: ${hash}`);
  }
  // 기사 계층의 표시(member_role·출처)가 아예 들어오지 않는다.
  assert.ok(!html.includes("member_role"), "기사 계층 필드가 타임라인에 들어왔다");
});

check("8/23 상세에서는 9/18 이 뜬다 — 방향이 양쪽이다", () => {
  const html = build(defaultState()).threadDialogSection(augIssue());
  assert.ok(html.includes(SEP_SHOWN), "9/18 사건이 없다");
  assert.ok(html.includes(`data-issue-id="${SAR_SEP}"`), "9/18 로 가는 링크가 없다");
});

console.log("근거 계층 비회귀 — 기사는 제자리에 있다");

check("9/18 의 선정 1건 + 추가 근거 5건이 그대로다", () => {
  const articles = sepIssue().related_articles;
  assert.equal(articles.filter(row => (row.member_role || "card") !== "evidence").length, 1);
  assert.equal(articles.filter(row => row.member_role === "evidence").length, 5);
});

check("8/23 근거와 9/18 근거가 섞이지 않는다", () => {
  const aug = new Set(augIssue().related_articles.map(row => row.hash));
  const sep = new Set(sepIssue().related_articles.map(row => row.hash));
  const shared = [...aug].filter(hash => sep.has(hash));
  assert.deepEqual(shared, [], "두 사건의 근거 기사가 겹친다");
});

check("타임라인이 근거 목록을 대신하지 않는다 — 두 구역은 별개다", () => {
  const html = build(defaultState()).threadDialogSection(sepIssue());
  assert.ok(!html.includes("추가 근거 원문"), "근거 구역이 타임라인 안으로 들어왔다");
  assert.ok(html.includes("각 사건의 근거 기사는 그 사건의 상세에 있습니다"),
    "근거가 어디 있는지 안내가 없다");
  // 기사 목록은 별도 구역으로 남아 있어야 한다 — 이름만 옮겼지 지운 것이 아니다.
  assert.ok(source.includes("이 사건의 근거"), "근거 구역 제목이 app.js 에서 사라졌다");
  assert.ok(source.includes("추가 근거 원문"), "추가 근거 구역이 app.js 에서 사라졌다");
});

console.log("안전장치 — 못 믿을 때는 아무 말도 하지 않는다");

check("스토리 화면이 숨겨지면 타임라인도 같이 사라진다", () => {
  const hidden = threadsPayload({ visible: false });
  assert.equal(build(defaultState({ threads: hidden })).threadDialogSection(sepIssue()), "");
});

check("판정 계약 판본이 다르면 그리지 않는다", () => {
  const future = threadsPayload({ version: "thread-web-v3" });
  assert.equal(build(defaultState({ threads: future })).threadDialogSection(sepIssue()), "");
});

check("2단계 부팅 전(threads 미도착)에는 조용히 없다", () => {
  assert.equal(build(defaultState({ threads: null })).threadDialogSection(sepIssue()), "");
});

check("thread_id 가 없는 이슈는 구역을 세우지 않는다", () => {
  const lone = sepIssue({ thread_id: "" });
  assert.equal(build(defaultState()).threadDialogSection(lone), "");
});

check("원장에 없는 thread_id 를 들고 와도 죽지 않는다", () => {
  const stale = sepIssue({ thread_id: "thread-없는것" });
  assert.equal(build(defaultState()).threadDialogSection(stale), "");
});

check("흡수된 스토리의 옛 주소는 살아 있는 쪽으로 넘어간다", () => {
  const moved = threadsPayload({ redirects: { "thread-old": SAR_THREAD } });
  const issue = sepIssue({ thread_id: "thread-old" });
  const html = build(defaultState({ threads: moved })).threadDialogSection(issue);
  assert.ok(html.includes(AUG_SHOWN), "넘김이 안 먹었다");
});

check("보여 줄 다른 사건이 없으면 구역을 세우지 않는다", () => {
  // 같은 목록을 두 번 두지 않는다 — 근거 구역이 이미 이 사건을 세우고 있다.
  const alone = threadsPayload();
  alone.threads[0].flow = [alone.threads[0].flow[1]];
  alone.threads[0].events = [alone.threads[0].events[0]];
  assert.equal(build(defaultState({ threads: alone })).threadDialogSection(sepIssue()), "");
});

check("카탈로그에서 내려간 사건은 글자로 남고 버튼이 되지 않는다", () => {
  // 장기 스토리는 정의상 옛 사건을 들고 있다. 눌러도 아무 일이 없는 버튼은
  // 고장으로 읽히므로, 열 수 있을 때만 버튼으로 낸다.
  const state = defaultState({ issues: [sepIssue()] });
  const html = build(state).threadDialogSection(sepIssue());
  assert.ok(html.includes(AUG_SHOWN), "기록까지 지워졌다");
  assert.ok(!html.includes(`data-issue-id="${SAR_AUG}"`), "열 수 없는 사건이 버튼이 됐다");
  assert.ok(html.includes("is-closed"), "열 수 없다는 표시가 없다");
});

check("제목에 든 따옴표가 마크업을 깨지 않는다", () => {
  // 8/23 제목에 작은따옴표가 실제로 들어 있다 — '계절별 송전용량'.
  const html = build(defaultState()).threadDialogSection(sepIssue());
  assert.ok(html.includes("&#39;계절별 송전용량&#39;"), "제목이 이스케이프되지 않았다");
});

console.log("현재 행은 신원(source_event_id)으로 고른다 — 라우트가 아니다");

// 흡수된 사건: 라우트(event_id)는 흡수한 이슈의 id 를 달고 오지만 원래는 다른
// 사건이다. 라우트로 고르면 이 행이 '이번 사건'이 되고, 누르면 지금 화면이 다시 열린다.
const SAR_OLD = "issue-0ld0000000000000";
function absorbedPayload() {
  const payload = threadsPayload();
  payload.threads[0].flow = [
    { event_id: SAR_AUG, source_event_id: SAR_AUG, source_event_ids: [SAR_AUG],
      title: SAR_AUG_TITLE, date: "2026-08-24", date_kind: "first_seen",
      relation_to_next: "stage_progress", relation_label: "다음 단계" },
    { event_id: SAR_SEP, source_event_id: SAR_OLD, source_event_ids: [SAR_OLD],
      title: "흡수된 옛 사건", date: "2026-09-01", date_kind: "first_seen",
      relation_to_next: "", relation_label: "" },
    { event_id: SAR_SEP, source_event_id: SAR_SEP, source_event_ids: [SAR_SEP],
      title: SAR_SEP_TITLE, date: "2026-09-19", date_kind: "first_seen",
      relation_to_next: "", relation_label: "" },
  ];
  return payload;
}

check("흡수 행은 '이번 사건'이 아니고, 자기 자신을 여는 버튼도 아니다", () => {
  const html = build(defaultState({ threads: absorbedPayload() })).threadDialogSection(sepIssue());
  assert.equal((html.match(/<li class="is-current"/g) || []).length, 1, "현재 행이 하나가 아니다");
  assert.ok(html.includes("is-absorbed"), "흡수 행 표시가 없다");
  assert.ok(html.includes("이 이슈에 합쳐진 사건"), "흡수 행 라벨이 없다");
  assert.ok(!html.includes(`data-issue-id="${SAR_SEP}"`), "흡수 행이 자기 자신을 여는 버튼이 됐다");
  // 행은 합치지 않는다 — 세 사건이 다 선다.
  assert.equal((html.match(/<li/g) || []).length, 3, "행이 합쳐졌다");
  // 현재 행은 흡수 행이 아니라 9/19 행이다.
  const currentAt = html.indexOf('<li class="is-current"');
  assert.ok(html.indexOf(SEP_SHOWN, currentAt) > currentAt, "현재 표시가 엉뚱한 행에 붙었다");
  assert.ok(html.indexOf("흡수된 옛 사건") < currentAt, "흡수 행이 현재 행으로 칠해졌다");
});

check("접힌 사본(source_event_ids)으로 들어온 이슈도 현재 행을 찾는다", () => {
  const payload = threadsPayload();
  payload.threads[0].flow[1] = { ...payload.threads[0].flow[1],
    event_id: "story-other", source_event_id: "story-other",
    source_event_ids: [SAR_SEP, "story-other"] };
  const html = build(defaultState({ threads: payload })).threadDialogSection(sepIssue());
  assert.ok(html.includes('<li class="is-current"'), "접힌 사본 쪽 이슈에서 현재 행이 사라졌다");
  assert.ok(!html.includes("아직 이 흐름에 자리 잡지"), "현재가 있는데 없다고 말한다");
});

check("흐름에 현재 사건이 없으면 없다고 말한다 — 아무 행에나 붙이지 않는다", () => {
  const issue = sepIssue({ issue_id: "issue-split-new" });
  const html = build(defaultState()).threadDialogSection(issue);
  assert.ok(html.includes("아직 이 흐름에 자리 잡지"), "현재 사건 부재 안내가 없다");
  assert.ok(!html.includes('<li class="is-current"'), "없는 현재 행이 생겼다");
});

check("날짜 축은 브리핑 첫 등장일 하나다 — 무슨 날짜인지는 title 로만", () => {
  const html = build(defaultState({ threads: absorbedPayload() })).threadDialogSection(sepIssue());
  assert.ok(html.includes('title="브리핑에 처음 오른 날"'), "날짜 종류 안내가 없다");
});

check("현재 행 강조가 행을 옆으로 밀지 않는다", () => {
  const css = readFileSync(fileURLToPath(new URL("../public/style.css", import.meta.url)), "utf8");
  const rule = css.split(/\r?\n/).filter(line => line.includes(".dialog-thread li.is-current"));
  assert.ok(rule.length, "현재 행 규칙이 없다");
  for (const line of rule) assert.ok(!/margin-left|padding-left/.test(line), `행을 미는 규칙: ${line}`);
});

console.log("chronicle 계층은 남아 있지 않다");

check("도달 불가능하던 chronicle 함수가 app.js 에 없다", () => {
  // 같은 자리를 노리는 계층이 둘이면 다음 사람이 어느 쪽이 정본인지 모른다.
  for (const dead of ["function chronicleFor(", "function chronicleDialogSection(",
                      "function chronicleNarrativeFor(", "function chronicleEventsDesc(",
                      "CHRONICLE_LIST_MAX"]) {
    assert.ok(!source.includes(dead), `죽은 chronicle 코드가 남아 있다: ${dead}`);
  }
});

check("보고서 복사의 '경과' 줄은 thread 를 읽는다", () => {
  assert.ok(source.includes("const thread = threadForIssue(issue);"),
    "issueReportText 가 스토리를 안 읽는다");
  assert.ok(!source.includes("chronicleLines"), "chronicle 재료가 남아 있다");
});

if (failures) {
  console.error(`\n${failures}건 실패`);
  process.exit(1);
}
console.log("\n모두 통과");
