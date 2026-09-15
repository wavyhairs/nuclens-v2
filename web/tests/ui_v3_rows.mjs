// 화면 v3 의 3단 컴포넌트 — 필드를 어떻게 읽는가.
// 실행: node web/tests/ui_v3_rows.mjs  (의존성 없음)
//
// 여기서 잠그는 것은 네 가지이고, 넷 다 **틀리기 쉬운 쪽으로 틀렸던 것**이다.
//
//   ① 1단의 한 줄은 card_why 하나만 읽는다. 비면 줄이 없다.
//      폴백을 프론트에 두면 build_data 의 계약(제목 재진술·change_display 중복
//      필터)을 통째로 우회한다(build_data.py:3711). summary 로 메우는 것은
//      '왜 중요한가' 라벨 아래 '무슨 일이 있었나'를 세우는 일이다.
//   ② 이전 보도는 change_log[0] 에서 온다. issue_change_log.build() 가
//      reverse=True 로 끝나 목록이 최신순이라, 마지막 항목은 가장 오래된 변화다.
//   ③ 변화 라벨은 app.js 가 이미 배포한 두 칸뿐이고, 로그가 없으면 라벨도 없다.
//      '세부 변화' 같은 새 낱말은 minor 의 뜻(진전 확인 불가)을 뒤집는다.
//   ④ verification.label 이 없으면 배지를 안 붙인다 — verificationState() 의
//      폴백 객체에는 label 이 없다.
//
// app.js·ui-v3.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라
// import 가 안 된다. weekly_sections.mjs 와 같은 방식으로 함수 블록만 잘라 평가한다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const appSource = readFileSync(
  fileURLToPath(new URL("../public/app.js", import.meta.url)), "utf8");
const v3Source = readFileSync(
  fileURLToPath(new URL("../public/ui-v3.js", import.meta.url)), "utf8");

function extract(source, name, where) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${where} 에 ${name}() 이 없다 — 이름이 바뀌었으면 이 검사도 같이 고쳐라`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && (depth -= 1) === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name}() 블록이 안 닫힌다`);
}
const fromApp = (name) => extract(appSource, name, "app.js");
const fromV3 = (name) => extract(v3Source, name, "ui-v3.js");

// 라벨 표와 문구는 ui-v3.js 에서 **그대로** 가져온다. 여기에 값을 다시 적으면
// 검사가 자기가 쓴 값을 확인하게 된다.
const labelDecl = /const V3_CHANGE_LABELS = \{[^}]*\};/.exec(v3Source);
if (!labelDecl) throw new Error("ui-v3.js 에 V3_CHANGE_LABELS 선언이 없다");
const stringsDecl = /const UI_V3_STRINGS = \{[\s\S]*?\n\};/.exec(v3Source);
if (!stringsDecl) throw new Error("ui-v3.js 에 UI_V3_STRINGS 선언이 없다");

const api = new Function(`
  ${fromApp("esc")}
  ${fromApp("safeUrl")}
  ${fromApp("dateLabel")}
  const TOPIC_LABELS = { smr: "SMR" };
  const state = { savedIds: new Set() };
  ${labelDecl[0]}
  ${stringsDecl[0]}
  ${fromV3("v3CardWhy")}
  ${fromV3("v3IsDomainLike")}
  ${fromV3("v3Publisher")}
  ${fromV3("v3TimelineItems")}
  ${fromV3("v3RelatedItems")}
  ${fromV3("v3ChangeLog")}
  ${fromV3("v3LatestChange")}
  ${fromV3("v3ChangeLabel")}
  ${fromV3("v3PriorReport")}
  ${fromV3("v3VerificationLabel")}
  ${fromV3("v3TrackLine")}
  ${fromV3("v3Tier2")}
  ${fromV3("v3Row")}
  ${fromV3("v3Section")}
  ${fromV3("v3Timeline")}
  ${fromV3("v3Related")}
  ${fromV3("v3Outlets")}
  ${fromV3("v3SheetBody")}
  ${fromV3("v3PickToday")}
  return { v3PickToday, v3Row, v3CardWhy, v3PriorReport, v3ChangeLabel, v3SheetBody,
           v3TimelineItems, v3RelatedItems, v3Publisher, V3_CHANGE_LABELS };
`)();

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`ok   ${name}`);
}

// 1단만 떼어 본다. summary 는 2단에 정당하게 들어가므로, 행 전체를 보면
// "1단에 새어 나왔나"를 물을 수 없다.
const face = (issue, variant, index = 0) =>
  api.v3Row(issue, variant, index).split('<div class="v3-panel"')[0];

const base = {
  issue_id: "issue-1",
  title: "한빛 2호기 운영허가 만료로 가동 정지",
  summary: "한빛 2호기가 40년 운영허가 만료로 11일 가동을 정지했다.",
  card_why: "계속운전 심의 결과에 따라 국내 원전 운영 계획이 갈림",
  region: "국내",
  verification: { label: "독립 출처 2곳+" },
  related_articles: [],
  tracked_briefings: 1,
  first_seen: "2026-09-13",
};

// ── ① 1단의 한 줄 ─────────────────────────────────────────────────────────

check("rank 행은 card_why 를 한 줄로 낸다", () => {
  const html = api.v3Row(base, "rank", 0);
  assert.ok(html.includes("계속운전 심의 결과에 따라"), "card_why 가 안 보인다");
  assert.ok(html.includes('class="v3-why"'), "한 줄 칸이 없다");
});

check("card_why 가 비면 한 줄을 생략한다 — summary 로 메우지 않는다", () => {
  const issue = { ...base, card_why: "" };
  const html = face(issue, "rank");
  assert.ok(!html.includes('class="v3-why"'), "빈 값인데 한 줄 칸이 남았다");
  assert.ok(!html.includes("40년 운영허가 만료로 11일"),
    "summary 가 1단으로 새어 나왔다 — 폴백은 빌드(finalize_card_fields)의 몫이다");
});

check("why_important 가 있어도 1단은 쓰지 않는다", () => {
  const issue = { ...base, card_why: "", why_important: "이 문장은 시트 전용이다" };
  const html = face(issue, "rank");
  assert.ok(!html.includes("이 문장은 시트 전용이다"),
    "why_important 가 1단으로 새어 나왔다 — 프론트 폴백 금지");
});

check("plain 행에는 한 줄이 없다", () => {
  const html = face(base, "plain");
  assert.ok(!html.includes('class="v3-why"'));
  assert.ok(html.includes("국내"), "지역이 없다");
});

// ── ② 이전 보도는 change_log[0] ───────────────────────────────────────────

const threeChanges = {
  ...base,
  change_log: [
    { kind: "material", hash: "h3", prior_article_date: "2026-09-10", prior_title: "가장 최근의 이전 보도", date: "2026-09-12" },
    { kind: "minor", hash: "h2", prior_article_date: "2026-09-02", prior_title: "가운데 보도", date: "2026-09-05" },
    { kind: "minor", hash: "h1", prior_article_date: "2026-08-20", prior_title: "가장 오래된 이전 보도", date: "2026-08-22" },
  ],
};

check("이전 보도는 change_log[0] 에서 온다 (마지막 항목이 아니다)", () => {
  const prior = api.v3PriorReport(threeChanges);
  assert.equal(prior.title, "가장 최근의 이전 보도");
  assert.equal(prior.date, "2026-09-10");
  const html = api.v3Row(threeChanges, "change", 0);
  assert.ok(html.includes("가장 최근의 이전 보도"));
  assert.ok(!html.includes("가장 오래된 이전 보도"),
    "목록이 최신순인데 마지막 항목을 읽었다 — issue_change_log.build() 는 reverse=True 로 끝난다");
});

check("change_log 가 없으면 타임라인의 바로 앞 card 기사로 물러난다", () => {
  const issue = {
    ...base,
    related_articles: [
      { member_role: "card", article_date: "2026-09-13", title_kr: "오늘 기사", publisher: "연합뉴스" },
      { member_role: "card", article_date: "2026-09-06", title_kr: "직전 회차 기사", publisher: "전기신문" },
      { member_role: "evidence", article_date: "2026-09-12", title_kr: "근거 기사", publisher: "매일경제" },
    ],
  };
  const prior = api.v3PriorReport(issue);
  assert.equal(prior.title, "직전 회차 기사", "card 가 아닌 근거 기사를 집었다");
  assert.equal(prior.date, "2026-09-06");
});

check("이전 보도가 둘 다 없으면 그 줄이 아예 없다", () => {
  const html = face(base, "change");
  assert.ok(!html.includes('class="v3-prior"'));
});

// ── ③ 변화 라벨 ───────────────────────────────────────────────────────────

check("변화 라벨은 app.js 가 배포한 두 칸을 그대로 쓴다", () => {
  assert.deepEqual(api.V3_CHANGE_LABELS, { material: "단계 이동", minor: "후속 보도" });
  // app.js 의 표와 같은지 원본에서 확인한다 — 두 파일이 갈라지면 같은 이슈가
  // 구 화면과 v3 에서 다른 이름으로 불린다.
  const appTable = /const CHANGE_LOG_LABELS = \{([^}]*)\}/.exec(appSource);
  assert.ok(appTable, "app.js 에 CHANGE_LOG_LABELS 가 없다");
  assert.ok(appTable[1].includes("단계 이동") && appTable[1].includes("후속 보도"),
    "app.js 의 라벨과 v3 의 라벨이 갈라졌다");
});

check("material 은 '단계 이동', minor 는 '후속 보도'", () => {
  assert.equal(api.v3ChangeLabel(threeChanges), "단계 이동");
  assert.equal(api.v3ChangeLabel({ ...base, change_log: [{ kind: "minor", hash: "h" }] }), "후속 보도");
});

check("change_log 가 없으면 라벨을 붙이지 않는다", () => {
  assert.equal(api.v3ChangeLabel(base), "");
  const html = face(base, "change");
  assert.ok(!html.includes("후속 보도"),
    "판정이 없는데 '후속 보도'라고 불렀다 — 그 말은 minor 판정을 뜻한다");
  assert.ok(!html.includes("세부 변화"), "배포된 적 없는 낱말이 들어왔다");
});

check("표에 없는 kind(none)는 버린다", () => {
  assert.equal(api.v3ChangeLabel({ ...base, change_log: [{ kind: "none", hash: "h" }] }), "");
});

// ── ④ 출처 배지 ───────────────────────────────────────────────────────────

check("verification.label 이 있으면 배지를 붙인다", () => {
  assert.ok(api.v3Row(base, "rank", 0).includes("독립 출처 2곳+"));
});

check("verification.label 이 없으면 배지를 생략한다", () => {
  // verificationState() 의 폴백 경로가 만드는 객체에는 label 이 없다.
  const issue = { ...base, verification: { status: "unverified", source_count: 1 } };
  const html = face(issue, "rank");
  assert.ok(!html.includes('class="v3-ok"'), "빈 배지가 섰다");
});

check("verification 자체가 없어도 죽지 않는다", () => {
  const issue = { ...base };
  delete issue.verification;
  assert.ok(api.v3Row(issue, "rank", 0).includes(issue.title));
});

// ── 2단 ───────────────────────────────────────────────────────────────────

check("2단은 세 변형에서 모두 같다", () => {
  const panel = (variant) => api.v3Row(base, variant, 0).split('class="v3-panel"')[1];
  assert.equal(panel("rank"), panel("change"));
  assert.equal(panel("change"), panel("plain"));
});

check("추적 정보는 tracked_briefings 로 갈린다", () => {
  assert.ok(api.v3Row(base, "plain", 0).includes("오늘 처음 잡힌 이슈"));
  const tracked = { ...base, tracked_briefings: 4, first_seen: "2026-09-01" };
  assert.ok(api.v3Row(tracked, "plain", 0).includes("9월 1일부터 추적 중, 브리핑 4회"));
});

check("저장 버튼은 기존 위임 훅(data-save-issue)을 쓴다", () => {
  assert.ok(api.v3Row(base, "plain", 0).includes('data-save-issue="issue-1"'));
});

// ── 3단 ───────────────────────────────────────────────────────────────────

const rich = {
  ...base,
  why_important: "설계수명 만료 원전의 계속운전 절차는 국내 원전 운영의 핵심 정책 사안이다.",
  implication: "한수원의 계속운전 신청 일정에 직접 영향을 준다.",
  detail: "원안위는 사고관리계획서 심의를 진행 중이다.",
  topics: ["smr"],
  tags: ["한빛", "계속운전", "원안위", "버려질태그"],
  related_articles: [
    { member_role: "card", article_date: "2026-09-13", title_kr: "오늘 기사", publisher: "연합뉴스", hash: "h3", url: "https://example.com/a" },
    { member_role: "card", article_date: "2026-09-06", title_kr: "직전 회차", publisher: "전기신문", hash: "h2", url: "https://example.com/b" },
    { member_role: "evidence", article_date: "2026-09-12", title_kr: "근거 1", publisher: "investchosun.com", url: "https://example.com/c" },
  ],
  change_log: [{ kind: "material", hash: "h3", prior_article_date: "2026-09-06", prior_title: "직전 회차", date: "2026-09-13" }],
};

check("시트의 '왜 중요한가'는 why_important 를 쓴다 (card_why 가 아니다)", () => {
  const html = api.v3SheetBody(rich, false);
  assert.ok(html.includes("설계수명 만료 원전의 계속운전 절차"));
  assert.ok(!html.includes("계속운전 심의 결과에 따라 국내 원전 운영 계획이 갈림"),
    "1단 전용 문장인 card_why 가 시트에 들어왔다");
});

check("일반 경로에서는 빈 섹션을 숨긴다", () => {
  const bare = { ...rich, why_important: "", implication: "" };
  const html = api.v3SheetBody(bare, false);
  assert.ok(!html.includes("아직 작성되지 않음"), "빈칸이 사용자에게 노출됐다");
  assert.ok(!html.includes("왜 중요한가"), "빈 섹션의 머리글만 남았다");
});

check("검수 모드(qa)에서는 빈 섹션을 드러낸다", () => {
  const bare = { ...rich, why_important: "", implication: "" };
  const html = api.v3SheetBody(bare, true);
  assert.ok(html.includes("왜 중요한가"), "검수 모드인데 섹션이 없다");
  assert.ok(html.includes("아직 작성되지 않음"));
});

check("타임라인은 card 만 세우고 최신순이다", () => {
  const items = api.v3TimelineItems(rich);
  assert.equal(items.length, 2);
  assert.equal(items[0].article_date, "2026-09-13");
  const html = api.v3SheetBody(rich, false);
  assert.ok(html.includes("타임라인 2단계"));
  assert.ok(html.includes("최신"));
  assert.ok(html.includes("단계 이동"), "change_log 가 가리키는 행에 라벨이 없다");
});

check("card 가 1건뿐이면 사과문 대신 사실 한 줄을 낸다", () => {
  const single = { ...rich, related_articles: [rich.related_articles[0], rich.related_articles[2]], change_log: [] };
  const html = api.v3SheetBody(single, false);
  assert.ok(html.includes("브리핑 한 회차에 실렸습니다"));
  assert.ok(!html.includes("아직 이어진 보도가 없습니다"),
    "78% 의 시트에 붙을 사과문이다 — 한 회차짜리 이슈는 결함이 아니다");
});

check("관련 기사는 비-card 만, 도메인꼴 매체명은 생략한다", () => {
  const items = api.v3RelatedItems(rich);
  assert.equal(items.length, 1);
  assert.equal(api.v3Publisher(items[0]), "", "investchosun.com 이 매체명으로 나갔다");
  const html = api.v3SheetBody(rich, false);
  assert.ok(!html.includes("investchosun.com"));
});

check("매체명이 정상이면 그대로 쓴다", () => {
  assert.equal(api.v3Publisher({ publisher: "연합뉴스" }), "연합뉴스");
  assert.equal(api.v3Publisher({ publisher: "전기신문" }), "전기신문");
  assert.equal(api.v3Publisher({ publisher: "jtbc.co.kr" }), "");
});

check("tags 는 3개까지만 머리에 세운다", () => {
  const html = api.v3SheetBody(rich, false);
  assert.ok(html.includes("#한빛") && html.includes("#원안위"));
  assert.ok(!html.includes("#버려질태그"));
});

check("관련 기사가 0건이면 그 사실을 적는다", () => {
  const none = { ...rich, related_articles: rich.related_articles.filter(a => a.member_role === "card") };
  assert.ok(api.v3SheetBody(none, false).includes("대표 기사 외에 연결된 기사가 없습니다"));
});

// ── 플래그가 주소에서 살아남는가 ─────────────────────────────────────────
//
// syncUrl() 은 주소를 state 에서 **매번 다시 만든다.** 여기서 ui 를 도로 써넣지
// 않으면 탭을 한 번 옮기는 순간 v3 가 구 화면으로 돌아간다 — 증상이 "가끔 안
// 된다"로 나타나서 원인을 찾기 어렵다.
const urlApi = new Function(`
  const calls = [];
  const state = {
    ui: "", uiQa: false, briefingDate: "2026-09-13", region: "전체", topic: "전체",
    view: "news", archiveQuery: "", archiveEntity: "", archiveRegion: "전체",
    archiveTopic: "전체", archivePeriod: "all", archiveVerification: "전체",
    archiveScope: "stories", threadId: "", issueId: "",
  };
  const history = { state: {}, pushState: (s, t, url) => calls.push(url),
                    replaceState: (s, t, url) => calls.push(url) };
  const briefRouteOwned = false;
  const issuePath = (id) => "/issue/" + id;
  const briefPath = (d) => "/brief/" + d;
  ${extract(appSource, "syncUrl", "app.js")}
  return { state, calls, syncUrl };
`)();

check("플래그가 없으면 주소에 ui 가 안 붙는다", () => {
  urlApi.calls.length = 0;
  urlApi.syncUrl();
  assert.ok(!urlApi.calls[0].includes("ui="), urlApi.calls[0]);
});

check("ui=v3 는 syncUrl() 을 거쳐도 살아남는다", () => {
  urlApi.state.ui = "v3";
  urlApi.calls.length = 0;
  urlApi.syncUrl();
  assert.ok(urlApi.calls[0].includes("ui=v3"),
    `주소에서 플래그가 사라졌다: ${urlApi.calls[0]}`);
});

check("탭을 옮겨도(view 변경) 플래그가 남는다", () => {
  urlApi.state.view = "trend";
  urlApi.calls.length = 0;
  urlApi.syncUrl("push");
  assert.ok(urlApi.calls[0].includes("ui=v3") && urlApi.calls[0].includes("view=trend"),
    urlApi.calls[0]);
  urlApi.state.view = "news";
});

check("검수 모드(qa=1)도 함께 남는다", () => {
  urlApi.state.uiQa = true;
  urlApi.calls.length = 0;
  urlApi.syncUrl();
  assert.ok(urlApi.calls[0].includes("qa=1"), urlApi.calls[0]);
  urlApi.state.uiQa = false;
});

// ── 오늘 화면의 세 목록 (A2) ─────────────────────────────────────────────
//
// 한 issue_id 가 화면에 두 번 서면 머리줄의 개수 표시도 실제 행 수와 어긋난다.
// 구 화면이 같은 이유로 changedIds 집합을 들고 있다.
const mk = (id, extra = {}) => ({ issue_id: id, title: id, summary: "s", card_why: "w", ...extra });

check("한 issue_id 는 오늘 화면 전체에서 한 번만 선다", () => {
  const issues = [
    mk("a", { status: "ongoing", change_kind: "change" }),
    mk("b"), mk("c"), mk("d", { status: "ongoing", change_kind: "change" }), mk("e"),
  ];
  const briefing = { highlight_issues: [{ issue_id: "a" }, { issue_id: "b" }, { issue_id: "c" }] };
  const { top, changed, rest } = api.v3PickToday(briefing, issues);
  const all = [...top, ...changed, ...rest].map(i => i.issue_id);
  assert.equal(all.length, new Set(all).size, `중복: ${all}`);
  assert.equal(all.length, issues.length, "빠진 이슈가 있다");
  assert.deepEqual(top.map(i => i.issue_id), ["a", "b", "c"]);
  assert.deepEqual(changed.map(i => i.issue_id), ["d"], "핵심 3건에 든 a 가 변화 목록에 또 섰다");
});

check("핵심 3건은 highlight_issues 의 순서를 따른다", () => {
  const issues = [mk("x"), mk("y"), mk("z")];
  const briefing = { highlight_issues: [{ issue_id: "z" }, { issue_id: "x" }, { issue_id: "y" }] };
  assert.deepEqual(api.v3PickToday(briefing, issues).top.map(i => i.issue_id), ["z", "x", "y"]);
});

check("highlight_issues 는 {issue_id, title} 뿐이라 본 목록에서 조인한다", () => {
  const issues = [mk("x", { card_why: "진짜 한 줄" })];
  // 실측: 58회차 전부 이 두 키만 들고 있다. 배열에서 바로 그리면 한 줄이 없다.
  const briefing = { highlight_issues: [{ issue_id: "x", title: "옛 제목" }] };
  const top = api.v3PickToday(briefing, issues).top;
  assert.equal(top[0].card_why, "진짜 한 줄");
});

check("조인에 실패한 id 는 건너뛰고 3칸을 채운다", () => {
  const issues = [mk("x"), mk("y"), mk("z"), mk("w")];
  const briefing = { highlight_issues: [{ issue_id: "없는id" }, { issue_id: "y" }] };
  const top = api.v3PickToday(briefing, issues).top;
  assert.equal(top.length, 3, "화면이 빈 칸을 들고 섰다");
  assert.ok(top.map(i => i.issue_id).includes("y"));
});

check("highlight_issues 가 아예 없어도 3칸이 선다", () => {
  const issues = [mk("x"), mk("y"), mk("z"), mk("w")];
  assert.equal(api.v3PickToday({}, issues).top.length, 3);
});

check("'진행 중 이슈의 변화'는 change_kind === \"change\" 만 세운다", () => {
  const issues = [
    mk("t1"), mk("t2"), mk("t3"),
    mk("chg", { status: "ongoing", change_kind: "change" }),
    mk("prev", { status: "ongoing", change_kind: "previous" }),
    mk("none", { status: "ongoing", change_kind: "" }),
    mk("new", { status: "new", change_kind: "change" }),
  ];
  const { changed, rest } = api.v3PickToday({}, issues);
  assert.deepEqual(changed.map(i => i.issue_id), ["chg"]);
  // previous 는 문장이 '바뀌기 전' 상태를 말한다는 표시다. 오늘의 변화 자리에
  // 세우면 옛 상태가 오늘 일로 읽힌다(라이브 10/160 에서 한 번 고친 자리).
  assert.ok(rest.map(i => i.issue_id).includes("prev"),
    "change_kind=previous 가 '오늘의 변화'로 올라갔다");
  assert.ok(rest.map(i => i.issue_id).includes("new"), "status=new 가 진행 중으로 올라갔다");
});

console.log(`\n${passed}건 전부 통과`);
