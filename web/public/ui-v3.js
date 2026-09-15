// 화면 v3 — 3단 공개 구조. `?ui=v3` 일 때만 선다.
//
// 왜 따로 있는가
// ---------------------------------------------------------------------------
// index.html 은 706줄짜리 고정 골격이고 app.js 가 ID 로 채운다. v3 는 그 골격을
// 쓰지 않고 제 컨테이너(#v3Root)에 직접 그리므로, app.js 본문에 섞으면 두 화면의
// 렌더 순서가 한 함수 안에서 엉킨다. 여기 있는 것은 전부 **문자열을 만드는 순수
// 함수**이고, DOM 을 만지는 곳은 맨 아래 붙임부 하나다.
//
// 로드 순서: 이 파일이 app.js **앞에** 온다. app.js 가 분기에서 UI_V3 를 부르는데,
// 뒤에 두면 그 상수가 app.js 파싱 시점에 아직 없다. 반대로 이 파일이 쓰는
// app.js 의 도우미(esc·dateLabel·TOPIC_LABELS)는 전부 **함수 안에서** 참조하므로
// 호출 시점에 이미 정의돼 있다.
//
// 이 파일은 `why_short` 를 모른다. 1단의 한 줄은 `card_why` 하나만 읽고, 그 자리에
// 무엇을 태울지는 빌드(`finalize_card_fields`)가 정한다 — 화면에서 or 폴백을 하면
// 계약이 두 곳에 흩어져 드리프트한다(build_data.py:3711).

// ── 문구 ───────────────────────────────────────────────────────────────────
//
// 한 곳에 모은다. PHASE_PLAN S3(문구 786건 외부화)가 나중에 이 객체 하나만
// 들어내면 되게 하려는 것이다 — S4 가 화면에 하드코딩해 둔 문구를 다시 들어내야
// 하는 대가를 이미 한 번 적어 뒀다.
const UI_V3_STRINGS = {
  todayTitle: "오늘 먼저 볼 3건",
  todayLead: (issueCount, changedCount) =>
    `이슈 ${issueCount}개 중 골랐습니다.` + (changedCount ? ` 진행 중 이슈 ${changedCount}건에 변화가 있습니다.` : ""),
  changedTitle: "진행 중 이슈의 변화",
  changedSub: "지난 브리핑 이후 새로 확인된 것만 적었습니다.",
  restTitle: "그 밖의 이슈",
  showMore: (n) => `${n}개 더 보기`,
  listen: "3분 브리핑 듣기",
  listenSub: "전문가 해설은 재생 화면에서 선택",
  weeklyLink: "이번 주 흐름",
  expand: "자세히",
  openSheet: "전체 내용 보기",
  original: "원문",
  save: "저장",
  saved: "저장됨",
  close: "닫기",
  nextCheck: "다음에 확인할 것",
  trackedSince: (date, count) => `${date}부터 추적 중, 브리핑 ${count}회`,
  trackedNew: "오늘 처음 잡힌 이슈",
  priorPrefix: "이전",
  sheetWhy: "왜 중요한가",
  sheetImplication: "한수원 시사점",
  sheetDetail: "기사 내용",
  sheetTimeline: "타임라인",
  sheetRelated: "관련 기사",
  sheetOutlets: "보도한 매체",
  sheetLatest: "최신",
  notWritten: "아직 작성되지 않음",
  timelineSingle: (date) => `이 이슈는 ${date} 브리핑 한 회차에 실렸습니다.`,
  relatedEmpty: "대표 기사 외에 연결된 기사가 없습니다.",
  reportPick: "보고 검토",
};

// 변화 라벨은 **새로 만들지 않는다.** app.js 가 이미 배포한 표를 그대로 쓴다
// (CHANGE_LOG_LABELS). material 과 minor 를 가르는 이유가 issue_change_log.py 에
// 적혀 있다 — minor 는 "진전이 있었는지 확인이 안 된다"는 뜻이라, '세부 변화'처럼
// 작더라도 변화가 있었다고 주장하는 말을 쓰면 거짓이 된다. 로그가 없으면 라벨도
// 없다(없는 판정을 '후속 보도'라고 부르지 않는다).
const V3_CHANGE_LABELS = { material: "단계 이동", minor: "후속 보도" };

// ── 필드 읽기 ──────────────────────────────────────────────────────────────

// 1단의 한 줄. 폴백이 없다 — 비면 줄을 통째로 생략한다. summary 로 메우면
// '왜 중요한가' 라벨 아래 '무슨 일이 있었나'가 서고, 그건 대개 제목 재진술이다.
function v3CardWhy(issue) {
  return String(issue?.card_why || "").trim();
}

function v3IsDomainLike(value) {
  const text = String(value || "").trim();
  return Boolean(text) && !/\s/.test(text) && /\.[a-z]{2,}$/i.test(text);
}

// 매체명 자리에 도메인이 오는 일이 있다(investchosun.com·jtbc.co.kr). 새 매핑표를
// 만들지 않는다 — 정규화는 이번 범위 밖이고, 여기서는 그 줄에서 이름만 뺀다.
function v3Publisher(article) {
  const name = String(article?.publisher || "").trim();
  return v3IsDomainLike(name) ? "" : name;
}

// 타임라인 = 이 이슈가 브리핑에 실린 회차. member_role === "card" 가 그 표시다.
// 최신순.
function v3TimelineItems(issue) {
  return (issue?.related_articles || [])
    .filter(article => article.member_role === "card")
    .slice()
    .sort((a, b) => String(b.article_date || "").localeCompare(String(a.article_date || "")));
}

function v3RelatedItems(issue) {
  return (issue?.related_articles || [])
    .filter(article => article.member_role !== "card")
    .slice()
    .sort((a, b) => String(b.article_date || "").localeCompare(String(a.article_date || "")));
}

// 화면에 낼 수 있는 변화 이력만. kind 가 표에 없는 값(none 등)은 버린다.
function v3ChangeLog(issue) {
  return (issue?.change_log || []).filter(entry => V3_CHANGE_LABELS[entry?.kind]);
}

// **[0] 이다.** issue_change_log.build() 가 reverse=True 로 끝나므로 목록은
// 최신순이고, 마지막 항목은 가장 오래된 변화다 — 그것을 '이전 보도'로 세우면
// 3회 움직인 이슈에서 3주 전 기사가 선다.
function v3LatestChange(issue) {
  return v3ChangeLog(issue)[0] || null;
}

function v3ChangeLabel(issue) {
  const entry = v3LatestChange(issue);
  return entry ? V3_CHANGE_LABELS[entry.kind] : "";
}

// '이전 보도' 한 줄. Gemini 를 부르지 않는다.
//   ① change_log[0] 의 prior_article_date·prior_title
//   ② 없으면 타임라인에서 최신 **바로 앞** card 기사
//   ③ 둘 다 없으면 이 줄은 없다
function v3PriorReport(issue) {
  const entry = v3LatestChange(issue);
  if (entry && (entry.prior_title || "").trim()) {
    return { date: entry.prior_article_date || entry.prior_date || "", title: String(entry.prior_title).trim() };
  }
  const timeline = v3TimelineItems(issue);
  const prior = timeline[1];
  if (prior && (prior.title_kr || "").trim()) {
    return { date: prior.article_date || "", title: String(prior.title_kr).trim() };
  }
  return null;
}

// 출처 배지. verificationState() 의 폴백 경로가 만드는 객체에는 label 이 없다 —
// 비면 배지를 안 붙인다(빈 칩이 서는 것보다 없는 편이 낫다).
function v3VerificationLabel(issue) {
  return String(issue?.verification?.label || "").trim();
}

function v3TrackLine(issue) {
  const count = Number(issue?.tracked_briefings || 0);
  if (count > 1) return UI_V3_STRINGS.trackedSince(dateLabel(issue.first_seen), count);
  return UI_V3_STRINGS.trackedNew;
}

// ── 1단 ────────────────────────────────────────────────────────────────────
//
// 변형은 셋이고 **보이는 것만 다르다.** 누르면 같은 2단이 열린다.
//   rank    순위·제목·한 줄·출처 배지·지역      오늘 핵심 3건
//   change  제목·이전 보도·변화 라벨·(보고 검토)  진행 중 이슈의 변화
//   plain   제목·지역                            그 밖 / 탐색 / 보고서
function v3Row(issue, variant = "plain", index = 0) {
  const id = String(issue?.issue_id || "");
  const panelId = `v3p-${id || index}`;
  const why = v3CardWhy(issue);
  const region = String(issue?.region || "").trim();
  const badge = v3VerificationLabel(issue);
  const prior = v3PriorReport(issue);
  const changeLabel = v3ChangeLabel(issue);
  const reportPick = String(issue?.report_pick || "").trim();

  let face = "";
  if (variant === "rank") {
    face = `<span class="v3-rank" aria-hidden="true">${index + 1}</span>
      <span class="v3-title">${esc(issue.title)}</span>
      ${why ? `<span class="v3-why">${esc(why)}</span>` : ""}
      <span class="v3-meta">${badge ? `<span class="v3-ok">${esc(badge)}</span>` : ""}${region ? `<span>${esc(region)}</span>` : ""}<span class="v3-hint">${UI_V3_STRINGS.expand}</span></span>`;
  } else if (variant === "change") {
    face = `<span class="v3-title">${esc(issue.title)}</span>
      ${changeLabel ? `<span class="v3-flag">${esc(changeLabel)}</span>` : ""}
      ${reportPick ? `<span class="v3-flag v3-pick">${UI_V3_STRINGS.reportPick}</span>` : ""}
      ${prior ? `<span class="v3-prior">${UI_V3_STRINGS.priorPrefix} ${esc(dateLabel(prior.date))}: ${esc(prior.title)}</span>` : ""}`;
  } else {
    face = `<span class="v3-title">${esc(issue.title)}</span>
      ${reportPick ? `<span class="v3-flag v3-pick">${UI_V3_STRINGS.reportPick}</span>` : ""}
      ${region ? `<span class="v3-rg">${esc(region)}</span>` : ""}`;
  }

  return `<li class="v3-row v3-${variant}" data-v3-issue="${esc(id)}">
    <button class="v3-face" type="button" aria-expanded="false" aria-controls="${esc(panelId)}">${face}</button>
    <div class="v3-panel" id="${esc(panelId)}" hidden>${v3Tier2(issue)}</div>
  </li>`;
}

// ── 2단 ────────────────────────────────────────────────────────────────────
//
// 모든 변형에서 내용이 같다. 변형마다 다른 것을 넣기 시작하면 세 벌이 된다.
function v3Tier2(issue) {
  const id = String(issue?.issue_id || "");
  const url = safeUrl(issue?.representative_article?.url || "");
  const question = String(issue?.open_question || "").trim();
  const saved = typeof state !== "undefined" && state.savedIds?.has(id);
  return `<p class="v3-summary">${esc(issue?.summary || "")}</p>
    ${question ? `<p class="v3-q"><b>${UI_V3_STRINGS.nextCheck}</b>${esc(question)}</p>` : ""}
    <p class="v3-track">${esc(v3TrackLine(issue))}</p>
    <div class="v3-actions">
      <button class="v3-primary" type="button" data-v3-sheet="${esc(id)}">${UI_V3_STRINGS.openSheet}</button>
      ${url ? `<a class="v3-btnlink" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${UI_V3_STRINGS.original}</a>` : ""}
      <button type="button" data-save-issue="${esc(id)}">${saved ? UI_V3_STRINGS.saved : UI_V3_STRINGS.save}</button>
    </div>`;
}

// ── 3단 ────────────────────────────────────────────────────────────────────
//
// **여기 들어오는 issue 는 카탈로그(state.issues) 레코드여야 한다.** briefings.json
// 안에 내장된 같은 이슈는 그 회차 시점의 related_articles 를 들고 있어서(실측
// 734건 중 449건 불일치, 최대 1 vs 54) 타임라인이 조용히 잘린다. 그 증상은 데이터
// 결손과 구분되지 않는다.
//
// qa: 빈 섹션을 감추지 않고 '아직 작성되지 않음'으로 드러내는 검수 모드(`&qa=1`).
// 일반 경로에서는 숨긴다 — why_important 는 82% 가 비어 있어서, 안 숨기면 전형적인
// 시트가 빈칸 세 칸으로 열린다.
function v3Section(heading, text, qa, className = "") {
  const value = String(text || "").trim();
  if (!value && !qa) return "";
  return `<section class="v3-sec ${className}"><h4>${esc(heading)}</h4>${
    value ? `<p>${esc(value)}</p>` : `<p class="v3-empty">${UI_V3_STRINGS.notWritten}</p>`
  }</section>`;
}

function v3Timeline(issue, changeByHash) {
  const items = v3TimelineItems(issue);
  if (items.length <= 1) {
    const only = items[0];
    return `<section class="v3-sec"><h4>${UI_V3_STRINGS.sheetTimeline}</h4>
      <p class="v3-note">${esc(UI_V3_STRINGS.timelineSingle(dateLabel(only?.article_date || issue?.first_seen)))}</p></section>`;
  }
  const rows = items.map((article, index) => {
    const mark = changeByHash.get(article.hash);
    const publisher = v3Publisher(article);
    return `<li class="${index === 0 ? "v3-now" : ""}">
      <div class="v3-tl-date">${esc(dateLabel(article.article_date))}${index === 0 ? `<i>${UI_V3_STRINGS.sheetLatest}</i>` : ""}${mark ? `<i>${esc(V3_CHANGE_LABELS[mark.kind])}</i>` : ""}</div>
      <div class="v3-tl-title">${esc(article.title_kr || "")}</div>
      ${publisher ? `<div class="v3-tl-pub">${esc(publisher)}</div>` : ""}
    </li>`;
  }).join("");
  return `<section class="v3-sec"><h4>${UI_V3_STRINGS.sheetTimeline} ${items.length}단계</h4><ol class="v3-tl">${rows}</ol></section>`;
}

function v3Related(issue) {
  const items = v3RelatedItems(issue);
  if (!items.length) {
    return `<section class="v3-sec"><h4>${UI_V3_STRINGS.sheetRelated}</h4><p class="v3-empty">${UI_V3_STRINGS.relatedEmpty}</p></section>`;
  }
  const rows = items.map((article, index) => {
    const url = safeUrl(article.url || "");
    const publisher = v3Publisher(article);
    const meta = [publisher, dateLabel(article.article_date)].filter(Boolean).join(", ");
    const body = `<div class="v3-rel-title">${esc(article.title_kr || "")}</div><div class="v3-rel-meta">${esc(meta)}</div>`;
    return `<li${index >= 5 ? " hidden" : ""}>${
      url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${body}</a>` : body
    }</li>`;
  }).join("");
  return `<section class="v3-sec"><h4>${UI_V3_STRINGS.sheetRelated} ${items.length}건</h4>
    <ul class="v3-rel">${rows}</ul>
    ${items.length > 5 ? `<button type="button" class="v3-rel-more">${UI_V3_STRINGS.showMore(items.length - 5)}</button>` : ""}</section>`;
}

function v3Outlets(issue) {
  const names = [...new Set((issue?.related_articles || []).map(v3Publisher).filter(Boolean))];
  if (!names.length) return "";
  return `<section class="v3-sec"><h4>${UI_V3_STRINGS.sheetOutlets} ${names.length}곳</h4>
    <div class="v3-outlets">${names.map(name => `<span>${esc(name)}</span>`).join("")}</div></section>`;
}

function v3SheetBody(issue, qa = false) {
  const changeByHash = new Map();
  v3ChangeLog(issue).forEach(entry => { if (entry.hash) changeByHash.set(entry.hash, entry); });
  const topic = (issue?.topics || [])[0];
  const tags = [
    v3VerificationLabel(issue),
    String(issue?.region || "").trim(),
    topic ? (TOPIC_LABELS[topic] || topic) : "",
    ...(issue?.tags || []).slice(0, 3).map(tag => `#${tag}`),
  ].filter(Boolean);
  return `<div class="v3-sheet-tags">${tags.map(tag => `<span>${esc(tag)}</span>`).join("")}</div>
    <h3 id="v3SheetTitle">${esc(issue?.title || "")}</h3>
    <p class="v3-sheet-date">${esc(dateLabel(issue?.representative_article?.article_date || issue?.last_seen))}</p>
    ${v3Section(UI_V3_STRINGS.sheetWhy, issue?.why_important, qa, "v3-lead")}
    ${v3Section(UI_V3_STRINGS.sheetImplication, issue?.implication, qa)}
    ${v3Section(UI_V3_STRINGS.sheetDetail, issue?.detail, false)}
    ${v3Section(UI_V3_STRINGS.nextCheck, issue?.open_question, false)}
    ${v3Timeline(issue, changeByHash)}
    ${v3Related(issue)}
    ${v3Outlets(issue)}`;
}

// ── 붙임부 ─────────────────────────────────────────────────────────────────
//
// DOM 을 만지는 곳은 여기뿐이다. 위의 조립 함수들은 전부 순수 함수라 브라우저
// 없이 검사할 수 있다(web/tests/ui_v3_rows.mjs).
function v3WireRows(container) {
  container.querySelectorAll(".v3-face").forEach(button => {
    if (button.dataset.v3Wired) return;
    button.dataset.v3Wired = "1";
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!open));
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (panel) panel.hidden = open;
      button.closest(".v3-row")?.classList.toggle("open", !open);
    });
  });
}

// ── 오늘 화면 ──────────────────────────────────────────────────────────────
//
// 한 issue_id 는 이 화면 전체에서 **한 번만** 선다. 세 목록이 각자 필터를 들고
// 있으면 같은 이슈가 두 번 서고, 그때 머리줄의 개수 표시도 실제 카드 수와
// 어긋난다(구 화면이 같은 이유로 changedIds 집합을 들고 있다).
//
// 핵심 3건은 `highlight_issues` 의 **순서와 issue_id 만** 쓴다. 그 배열은
// {issue_id, title} 두 키뿐이라(실측 58회차 전부) 나머지 필드는 그날 이슈 목록에서
// 조인해야 한다. 조인이 실패한 자리는 건너뛰고 brief_rank 순서로 메운다 — 화면이
// 3칸을 비운 채 서 있지 않게.
function v3PickToday(briefing, issues) {
  const byId = new Map(issues.map(issue => [issue.issue_id, issue]));
  const used = new Set();
  const top = [];
  for (const pick of briefing?.highlight_issues || []) {
    const issue = byId.get(pick?.issue_id);
    if (issue && !used.has(issue.issue_id)) { top.push(issue); used.add(issue.issue_id); }
    if (top.length >= 3) break;
  }
  if (top.length < 3) {
    for (const issue of issues) {
      if (top.length >= 3) break;
      if (used.has(issue.issue_id)) continue;
      top.push(issue); used.add(issue.issue_id);
    }
  }
  // '진행 중 이슈의 변화'. change_kind 가 "change" 인 것만 — "previous" 는 문장이
  // **바뀌기 전** 상태를 말한다는 표시라(app.js 의 issueChangeLabel 주석), 오늘의
  // 변화 자리에 세우면 옛 상태가 오늘 일로 읽힌다. 라이브 10/160 건에서 한 번
  // 고친 자리다.
  const changed = issues.filter(issue =>
    !used.has(issue.issue_id) && issue.status === "ongoing" && issue.change_kind === "change");
  changed.forEach(issue => used.add(issue.issue_id));
  const rest = issues.filter(issue => !used.has(issue.issue_id));
  return { top, changed, rest };
}

const V3_REST_VISIBLE = 5;

function v3TodayHtml(briefing, issues, options = {}) {
  const { top, changed, rest } = v3PickToday(briefing, issues);
  const dates = options.dates || [];
  const index = dates.indexOf(briefing?.date);
  const restHidden = Math.max(0, rest.length - V3_REST_VISIBLE);
  return `
  <div class="v3-hero">
    <div class="v3-date">
      <button type="button" data-v3-step="1" aria-label="이전 브리핑"${index < 0 || index >= dates.length - 1 ? " disabled" : ""}>‹</button>
      <details class="v3-datepick">
        <summary>${esc(dateWeekdayLabel(briefing?.date))}</summary>
        <ul>${dates.map(date => `<li><button type="button" data-v3-date="${esc(date)}"${date === briefing?.date ? ' aria-current="date"' : ""}>${esc(dateWeekdayLabel(date))}</button></li>`).join("")}</ul>
      </details>
      <button type="button" data-v3-step="-1" aria-label="다음 브리핑"${index <= 0 ? " disabled" : ""}>›</button>
    </div>
    <h1>${UI_V3_STRINGS.todayTitle}</h1>
    <p>${esc(UI_V3_STRINGS.todayLead(issues.length, changed.length))}</p>
  </div>

  <ol class="v3-rows v3-top">${top.map((issue, i) => v3Row(issue, "rank", i)).join("")}</ol>

  <div id="v3AudioSlot" class="v3-audio-slot"></div>

  ${changed.length ? `<section class="v3-block" aria-labelledby="v3ChangedTitle">
    <h2 id="v3ChangedTitle">${UI_V3_STRINGS.changedTitle}</h2>
    <p class="v3-sub">${UI_V3_STRINGS.changedSub}</p>
    <ul class="v3-rows">${changed.map(issue => v3Row(issue, "change")).join("")}</ul>
  </section>` : ""}

  ${rest.length ? `<section class="v3-block" aria-labelledby="v3RestTitle">
    <h2 id="v3RestTitle">${UI_V3_STRINGS.restTitle}</h2>
    <ul class="v3-rows">${rest.map((issue, i) =>
      v3Row(issue, "plain", i).replace("<li ", i >= V3_REST_VISIBLE ? '<li hidden ' : "<li ")).join("")}</ul>
    ${restHidden ? `<button class="v3-showmore" type="button" data-v3-more="rest">${UI_V3_STRINGS.showMore(restHidden)}</button>` : ""}
  </section>` : ""}

  <button class="v3-weekly" type="button" data-v3-go="trend">
    <b>${UI_V3_STRINGS.weeklyLink}</b><span aria-hidden="true">→</span>
  </button>`;
}

// 오디오 플레이어는 **옮겨 쓴다.** 노드를 옮기면 붙어 있던 리스너가 그대로
// 따라오므로 renderAudioBrief 의 로직(빠른·전문가 선택·배속·진행 바)을 한 줄도
// 다시 쓰지 않는다.
//
// 다만 옮긴 뒤 #v3Root 안에 두면 다음 렌더의 innerHTML 재할당이 그 노드를
// **파괴한다** — 날짜를 한 번 옮기는 순간 플레이어가 영영 사라지고, 그 뒤로는
// getElementById 가 null 을 돌려줘 오디오 칸이 조용히 없어진다. 그래서 렌더를
// 시작할 때마다 먼저 화면 밖 보관함으로 빼 둔다.
function v3ParkBox() {
  let parked = document.getElementById("v3Parked");
  if (!parked) {
    parked = document.createElement("div");
    parked.id = "v3Parked";
    parked.hidden = true;
    document.body.appendChild(parked);
  }
  return parked;
}

// 옮겨 쓰는 노드는 전부 이 문을 지난다. 렌더를 시작할 때 화면 밖으로 빼 두고,
// 새 골격을 그린 뒤 제자리에 다시 넣는다.
function v3Park(id) {
  const node = document.getElementById(id);
  if (!node) return null;
  const parked = v3ParkBox();
  if (node.parentElement !== parked) parked.appendChild(node);
  return node;
}

function v3ParkAudio() {
  return v3Park("audioBrief");
}

// v3 가 빌려 쓰는 구 골격 노드. 렌더마다 전부 빼 두지 않으면 innerHTML 재할당이
// 남은 것을 파괴한다 — 한 번 파괴되면 getElementById 가 null 을 돌려줘 그 칸이
// 조용히 없어지고, 증상은 '가끔 안 보인다'로만 나타난다.
const V3_BORROWED = [
  "audioBrief", "trendTopicFlow", "eventCalendarUpcoming", "eventCalendarMonths",
  "trendReadiness", "trendData", "trendWordCloud", "eventCalendarGrid", "briefingTimeline",
  "archiveFilterDrawer",
];

function v3ParkAll() {
  V3_BORROWED.forEach(v3Park);
}

// 빌린 노드를 자리에 꽂는다. 없으면(구 골격이 그 칸을 안 그린 회차) 조용히 넘어간다.
//
// **hidden 을 건드리지 않는다.** 이 칸들은 제 렌더러가 "이번 회차에 보일 것이
// 있는가"를 판정해 스스로 숨는다(워드 클라우드·달력·주제 흐름이 전부 그렇다).
// 여기서 강제로 벗기면 빈 상자가 서고, 빈 제목은 '아직 안 나왔다'가 아니라
// '고장'으로 읽힌다. 렌더러는 이 함수보다 **먼저** 돌아야 한다.
function v3Place(root, slotSelector, id) {
  const slot = root.querySelector(slotSelector);
  const node = document.getElementById(id);
  if (!slot || !node) return false;
  slot.appendChild(node);
  return true;
}

function v3RenderToday(root, briefing, options = {}) {
  // innerHTML 을 건드리기 **전에** 뺀다.
  v3ParkAll();
  const player = document.getElementById("audioBrief");
  if (!briefing) {
    root.innerHTML = `<section class="v3-block"><h2>${UI_V3_STRINGS.todayTitle}</h2>
      <p class="v3-sub">이 날짜에는 브리핑이 없습니다.</p></section>`;
    return;
  }
  const issues = options.issues || [];
  root.innerHTML = v3TodayHtml(briefing, issues, options);
  v3WireRows(root);
  const slot = root.querySelector("#v3AudioSlot");
  if (player && slot) {
    // renderAudioBrief 가 이번 회차에 들려줄 것이 없다고 판정하면 hidden 으로
    // 둔다. 그때는 버튼도 세우지 않는다 — 눌러도 아무 일이 없는 칸을 만들지 않는다.
    const available = !player.hasAttribute("hidden");
    slot.innerHTML = available
      ? `<button class="v3-listen" type="button" data-v3-audio aria-expanded="false"><b>${UI_V3_STRINGS.listen}</b><small>${UI_V3_STRINGS.listenSub}</small></button>`
      : "";
    slot.appendChild(player);
    if (available) player.setAttribute("hidden", "");
  }
}

// ── 오래 이어진 이슈 ──────────────────────────────────────────────────────
//
// 장기 스토리 탭이 v3 에서 내려가면서 "오래 끌고 있는 이슈"를 찾을 길이 하나
// 없어진다. 그 자리를 탐색 탭의 정렬 하나가 맡는다.
//
// 기준은 이슈 원장의 first_seen~last_seen 이다. threads.json 을 읽지 않는다 —
// 두 엔진이 같은 질문에 다른 답을 하는 상태(§C-2-1)를 화면이 물려받지 않게 한다.
function v3IssueSpanDays(issue) {
  const from = String(issue?.first_seen || "");
  const to = String(issue?.last_seen || "");
  if (!from || !to) return 0;
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return 0;
  return Math.round((end - start) / 86400000) + 1;
}

function v3SortBySpan(issues) {
  return issues.slice().sort((a, b) =>
    v3IssueSpanDays(b) - v3IssueSpanDays(a)
    // 같은 기간이면 회차가 많은 쪽 — '오래'는 달력 길이만이 아니라 몇 번
    // 다뤘는가이기도 하다. 그것도 같으면 최근 것.
    || (b.tracked_briefings || 0) - (a.tracked_briefings || 0)
    || String(b.last_seen || "").localeCompare(String(a.last_seen || "")));
}

// ── 흐름 탭 ────────────────────────────────────────────────────────────────
//
// 구 화면은 12개 구역이 같은 내용을 여러 방식으로 되풀이한다 — 주제 추이가 4주
// 흐름 막대·주간 변화 그래프·키워드 표로 세 번 나오고, 국가 지도와 국가별 이슈
// 수도 같은 답을 한다. 실측 18,604px.
//
// v3 는 답을 하나씩만 낸다. 지운 것은 없다 — 되풀이하는 쪽을 '데이터 더 보기'
// 안으로 넣고, 그 칸은 **열 때 처음 그린다**(지연 렌더). 접혀 있는 동안은 부피에
// 들어가지 않는다.
//
// 재료는 app.js 가 만들어 넘긴다. 이 파일이 weeklyReportFor·dropTextsAlreadyOnCards
// 같은 판단을 다시 하면 같은 리포트를 두 화면이 다르게 읽는다.
function v3List(items, cls = "") {
  return `<ul class="v3-mini ${cls}">${items.map(text => `<li>${esc(text)}</li>`).join("")}</ul>`;
}

// 빌린 칸이 비었거나 스스로 숨은 구역은 통째로 접는다. 제목만 남은 구역은
// '아직 안 나왔다'가 아니라 '고장'으로 읽힌다(구 화면의 fill() 이 같은 계약).
function v3HideEmptyBlocks(root) {
  root.querySelectorAll(".v3-block").forEach(block => {
    const slots = [...block.querySelectorAll("[data-v3-slot]")];
    if (!slots.length) return;
    const live = slots.some(slot =>
      [...slot.children].some(child => !child.hidden && child.innerHTML.trim()));
    // details 안의 슬롯만 비었으면 그 details 만 접는다.
    const outside = slots.filter(slot => !slot.closest("details"));
    if (!outside.length) return;
    const outsideLive = outside.some(slot =>
      [...slot.children].some(child => !child.hidden && child.innerHTML.trim()));
    block.hidden = !outsideLive && !live ? true : !outsideLive;
  });
  root.querySelectorAll("details.v3-more").forEach(box => {
    const slot = box.querySelector("[data-v3-slot]");
    if (!slot || box.dataset.v3Lazy) return;
    const live = [...slot.children].some(child => !child.hidden && child.innerHTML.trim());
    box.hidden = !live;
  });
}

function v3TrendHtml(ctx) {
  const { weekLabel = "", conclusions = [], soWhat = [], watch = [], intro = "", changed = [] } = ctx;
  const hasReport = conclusions.length || soWhat.length || watch.length || intro;
  return `
  <div class="v3-hero">
    <h1>이번 주 판세</h1>
    ${weekLabel ? `<p>${esc(weekLabel)}</p>` : ""}
  </div>

  ${hasReport ? `<section class="v3-block" aria-labelledby="v3WeekTitle">
    <h2 id="v3WeekTitle" class="sr-only">주간 판세</h2>
    ${conclusions.length ? `<h3 class="v3-minih">무엇이 바뀌었나</h3>${v3List(conclusions)}` : ""}
    ${soWhat.length ? `<h3 class="v3-minih">그래서 의미는</h3>${v3List(soWhat)}` : ""}
    ${watch.length ? `<h3 class="v3-minih">다음에 볼 것</h3>${v3List(watch)}` : ""}
    ${intro ? `<details class="v3-more"><summary>해설 펼치기</summary><p class="v3-narrative">${esc(intro)}</p></details>` : ""}
  </section>` : `<section class="v3-block"><p class="v3-sub">이번 주 리포트가 아직 없습니다.</p></section>`}

  <section class="v3-block" aria-labelledby="v3FlowTitle">
    <h2 id="v3FlowTitle">최근 몇 주, 어디로 움직였나</h2>
    <div data-v3-slot="topicFlow"></div>
  </section>

  ${changed.length ? `<section class="v3-block" aria-labelledby="v3MovedTitle">
    <h2 id="v3MovedTitle">이번 주 움직인 이슈</h2>
    <ul class="v3-rows">${changed.map(issue => v3Row(issue, "change")).join("")}</ul>
  </section>` : ""}

  <section class="v3-block" aria-labelledby="v3NextTitle">
    <h2 id="v3NextTitle">앞으로 무엇이 있나</h2>
    <div data-v3-slot="upcoming"></div>
    <details class="v3-more"><summary>날짜까지는 안 나온 것</summary><div data-v3-slot="months"></div></details>
  </section>

  <details class="v3-more v3-data" data-v3-lazy="trend">
    <summary>데이터 더 보기</summary>
    <div data-v3-slot="data"></div>
  </details>`;
}

function v3RenderTrend(root, ctx = {}) {
  v3ParkAll();
  root.innerHTML = v3TrendHtml(ctx);
  v3WireRows(root);
  v3Place(root, '[data-v3-slot="topicFlow"]', "trendTopicFlow");
  v3Place(root, '[data-v3-slot="upcoming"]', "eventCalendarUpcoming");
  v3Place(root, '[data-v3-slot="months"]', "eventCalendarMonths");
  // 빌린 칸이 스스로 숨었으면 그것을 감싼 v3 구역도 함께 접는다 — 안 그러면
  // 제목만 남아 '고장'으로 읽힌다.
  v3HideEmptyBlocks(root);

  // 지연 렌더. 여는 순간 처음 그린다 — 접힌 채로 두면 차트·지도·워드클라우드가
  // 아예 계산되지 않는다.
  const box = root.querySelector('[data-v3-lazy="trend"]');
  if (box) {
    box.addEventListener("toggle", () => {
      if (!box.open || box.dataset.v3Filled) return;
      box.dataset.v3Filled = "1";
      if (typeof ctx.onExpandData === "function") ctx.onExpandData();
      ["trendReadiness", "trendData", "trendWordCloud", "eventCalendarGrid", "briefingTimeline"]
        .forEach(id => v3Place(root, '[data-v3-slot="data"]', id));
    }, { once: false });
  }
}

// ── 탐색 탭 ────────────────────────────────────────────────────────────────
//
// 구 화면은 조건이 하나도 없어도 결과 목록 9,133px 를 곧바로 깐다. 아무것도 묻지
// 않았는데 답부터 쌓여 있는 셈이라, 첫 화면에서는 **묻는 자리만** 둔다.
//
// 랜딩 판정은 app.js 가 이미 갖고 있다(renderArchiveSearch 의 isLanding). 여기서
// 같은 판정을 다시 세우면 두 화면이 서로 다른 순간에 허브를 접는다.
function v3SearchHtml(ctx) {
  const { landing, query = "", chips = [], results = [], total = 0, limit = 20,
          sort = "updated", filterCount = 0 } = ctx;
  const head = `
  <div class="v3-hero">
    <h1>탐색</h1>
    <form class="v3-search" data-v3-search role="search">
      <input type="search" name="q" value="${esc(query)}" placeholder="이슈·기관·설비 검색"
             aria-label="이슈 검색" enterkeyhint="search">
      <button type="submit">검색</button>
    </form>
    <div class="v3-searchbar">
      <details class="v3-filter"><summary>필터${filterCount ? ` <b>${filterCount}</b>` : ""}</summary>
        <div data-v3-slot="filters"></div>
      </details>
      <label class="v3-sortsel">정렬
        <select data-v3-sort>
          <option value="updated"${sort === "updated" ? " selected" : ""}>최근 갱신순</option>
          <option value="span"${sort === "span" ? " selected" : ""}>오래 이어진 이슈</option>
          <option value="tracked"${sort === "tracked" ? " selected" : ""}>추적 횟수순</option>
          <option value="sources"${sort === "sources" ? " selected" : ""}>출처 수순</option>
        </select>
      </label>
    </div>
  </div>`;

  if (landing) {
    return `${head}
    <section class="v3-block" aria-labelledby="v3HubTitle">
      <h2 id="v3HubTitle">지금 많이 등장하는 대상</h2>
      <div class="v3-chips">${chips.map(chip =>
        `<button type="button" class="v3-chip" data-v3-ent="${esc(chip.id)}">${esc(chip.label)}<b>${Number(chip.count) || 0}</b></button>`).join("")
        || `<p class="v3-sub">아직 연결된 대상이 없습니다.</p>`}</div>
    </section>`;
  }

  const shown = results.slice(0, limit);
  const more = Math.max(0, total - shown.length);
  return `${head}
  <section class="v3-block" aria-labelledby="v3ResultTitle">
    <h2 id="v3ResultTitle">검색 결과 ${total}건</h2>
    ${shown.length
      ? `<ul class="v3-rows">${shown.map(issue => v3Row(issue, "plain")).join("")}</ul>
         ${more ? `<button class="v3-showmore" type="button" data-v3-page>${more}건 더 보기</button>` : ""}`
      : `<p class="v3-sub">조건에 맞는 이슈가 없습니다. 필터를 줄여 보세요.</p>`}
  </section>`;
}

function v3RenderSearch(root, ctx = {}) {
  v3ParkAll();
  root.innerHTML = v3SearchHtml(ctx);
  v3WireRows(root);
  // 필터 상자는 구 골격의 것을 빌린다 — select 넷과 그 리스너를 다시 만들면
  // 같은 필터가 두 벌이 되고, 한쪽만 고치는 날이 온다.
  v3Place(root, '[data-v3-slot="filters"]', "archiveFilterDrawer");
}

// ── 보고서 탭 ──────────────────────────────────────────────────────────────
//
// 보고 후보는 plain 행에 '보고 검토' 표시를 붙이고, 2단에 사유(report_pick_why)와
// 각도(report_pick_angles)를 더한다. 발간물은 제목·발간기관·날짜 세 줄이면 족하다.
function v3ReportHtml(ctx) {
  const { picks = [], pubs = [] } = ctx;
  return `
  <div class="v3-hero"><h1>보고서</h1></div>

  <section class="v3-block" aria-labelledby="v3PickTitle">
    <h2 id="v3PickTitle">이번 주 보고 후보</h2>
    ${picks.length
      ? `<ul class="v3-rows">${picks.map(issue => v3Row(issue, "plain")).join("")}</ul>`
      : `<p class="v3-sub">보고 후보로 분류된 이슈가 아직 없습니다. 생기면 근거와 함께 여기 섭니다.</p>`}
  </section>

  <section class="v3-block" aria-labelledby="v3PubTitle">
    <h2 id="v3PubTitle">참고 발간물${pubs.length ? ` ${pubs.length}건` : ""}</h2>
    ${pubs.length
      ? `<ul class="v3-rows v3-pubs">${pubs.map(item => {
          const url = safeUrl(item.url || "");
          const title = String(item.title_kr || item.title || "").trim();
          const org = String(item.org_kr || item.org || "").trim();
          const meta = [org, item.date ? dateLabel(item.date) : ""].filter(Boolean).join(" · ");
          const body = `<span class="v3-title">${esc(title)}</span>${meta ? `<span class="v3-rg">${esc(meta)}</span>` : ""}`;
          return `<li class="v3-row">${url
            ? `<a class="v3-face" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${body}</a>`
            : `<div class="v3-face">${body}</div>`}</li>`;
        }).join("")}</ul>`
      : `<p class="v3-sub">연결된 발간물이 없습니다.</p>`}
  </section>`;
}

function v3RenderReport(root, ctx = {}) {
  v3ParkAll();
  root.innerHTML = v3ReportHtml(ctx);
  v3WireRows(root);
  // 보고 후보의 2단에만 붙는 두 줄. 행 컴포넌트를 변형으로 늘리지 않고 여기서
  // 덧댄다 — '보고 검토'는 보고서 탭 한 곳의 사정이다.
  (ctx.picks || []).forEach(issue => {
    const why = String(issue.report_pick_why || "").trim();
    const angles = (issue.report_pick_angles || []).slice(0, 3);
    if (!why && !angles.length) return;
    const panel = root.querySelector(`[data-v3-issue="${CSS.escape(issue.issue_id)}"] .v3-panel`);
    if (!panel) return;
    const extra = document.createElement("div");
    extra.className = "v3-pick-why";
    extra.innerHTML = `${why ? `<p><b>보고 관점</b>${esc(why)}</p>` : ""}
      ${angles.length ? `<div class="v3-angles">${angles.map(a => `<span>${esc(a)}</span>`).join("")}</div>` : ""}`;
    panel.insertBefore(extra, panel.querySelector(".v3-actions"));
  });
}

// ── 3단 시트 (DOM) ────────────────────────────────────────────────────────
//
// 주소·뒤로가기·포커스 복귀는 **새로 만들지 않는다.** app.js 의 issueId 배선
// (openIssueDialog / closeIssueDialog / restoreIssueFromHistory / syncUrl)이 이미
// 그 일을 하고 있고, v3 는 그 안에서 그릴 물건만 바꾼다. 여기에 pushState 를 또
// 얹으면 뒤로가기 한 번에 두 칸이 움직인다.
let v3SheetEl = null;
let v3ScrimEl = null;
let v3LastFocus = null;

function v3EnsureSheet() {
  if (v3SheetEl) return;
  v3ScrimEl = document.createElement("div");
  v3ScrimEl.className = "v3-scrim";
  v3ScrimEl.hidden = true;
  v3SheetEl = document.createElement("div");
  v3SheetEl.className = "v3-sheet";
  v3SheetEl.setAttribute("role", "dialog");
  v3SheetEl.setAttribute("aria-modal", "true");
  v3SheetEl.setAttribute("aria-labelledby", "v3SheetTitle");
  v3SheetEl.hidden = true;
  v3SheetEl.innerHTML = `
    <div class="v3-sheet-head">
      <button class="v3-sheet-close" type="button" aria-label="${UI_V3_STRINGS.close}">✕</button>
    </div>
    <div class="v3-sheet-body" id="v3SheetBody"></div>
    <div class="v3-sheet-foot"></div>`;
  document.body.append(v3ScrimEl, v3SheetEl);
  v3ScrimEl.addEventListener("click", () => v3RequestClose());
  v3SheetEl.querySelector(".v3-sheet-close").addEventListener("click", () => v3RequestClose());
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && v3SheetEl && !v3SheetEl.hidden) v3RequestClose();
  });
}

// 닫기는 app.js 에 맡긴다 — 거기서 history.back() 으로 되돌려야 주소와 뒤로가기가
// 어긋나지 않는다. 이 파일이 직접 닫으면 주소에 issue 가 남는다.
function v3RequestClose() {
  if (typeof closeIssueDialog === "function") closeIssueDialog();
  else v3CloseSheet();
}

function v3OpenSheet(issue, options = {}) {
  v3EnsureSheet();
  v3LastFocus = document.activeElement;
  const url = safeUrl(issue?.representative_article?.url || "");
  const id = String(issue?.issue_id || "");
  document.getElementById("v3SheetBody").innerHTML = v3SheetBody(issue, Boolean(options.qa));
  v3SheetEl.querySelector(".v3-sheet-foot").innerHTML =
    `<button type="button" data-save-issue="${esc(id)}">${
      typeof state !== "undefined" && state.savedIds?.has(id) ? UI_V3_STRINGS.saved : UI_V3_STRINGS.save}</button>`
    + (url ? `<a class="v3-primary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 보기</a>` : "");
  const more = v3SheetEl.querySelector(".v3-rel-more");
  if (more) {
    more.addEventListener("click", () => {
      v3SheetEl.querySelectorAll(".v3-rel li[hidden]").forEach(row => { row.hidden = false; });
      more.remove();
    });
  }
  v3SheetEl.hidden = false;
  v3ScrimEl.hidden = false;
  document.body.style.overflow = "hidden";
  document.getElementById("v3SheetBody").scrollTop = 0;
  requestAnimationFrame(() => {
    v3SheetEl.classList.add("on");
    v3ScrimEl.classList.add("on");
    v3SheetEl.querySelector(".v3-sheet-close").focus();
  });
}

function v3CloseSheet() {
  if (!v3SheetEl || v3SheetEl.hidden) return;
  v3SheetEl.classList.remove("on");
  v3ScrimEl.classList.remove("on");
  document.body.style.overflow = "";
  const hide = () => { v3SheetEl.hidden = true; v3ScrimEl.hidden = true; };
  if (typeof prefersReducedMotion === "function" && prefersReducedMotion()) hide();
  else window.setTimeout(hide, 240);
  if (v3LastFocus && document.contains(v3LastFocus)) v3LastFocus.focus();
  v3LastFocus = null;
}

function v3SheetOpen() {
  return Boolean(v3SheetEl) && !v3SheetEl.hidden;
}

const UI_V3 = {
  STRINGS: UI_V3_STRINGS,
  renderToday: v3RenderToday,
  renderTrend: v3RenderTrend,
  renderSearch: v3RenderSearch,
  renderReport: v3RenderReport,
  pickToday: v3PickToday,
  sortBySpan: v3SortBySpan,
  issueSpanDays: v3IssueSpanDays,
  todayHtml: v3TodayHtml,
  trendHtml: v3TrendHtml,
  searchHtml: v3SearchHtml,
  reportHtml: v3ReportHtml,
  openSheet: v3OpenSheet,
  closeSheet: v3CloseSheet,
  sheetOpen: v3SheetOpen,
  row: v3Row,
  tier2: v3Tier2,
  sheetBody: v3SheetBody,
  wireRows: v3WireRows,
  cardWhy: v3CardWhy,
  priorReport: v3PriorReport,
  changeLabel: v3ChangeLabel,
  timelineItems: v3TimelineItems,
  relatedItems: v3RelatedItems,
};
