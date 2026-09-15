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

// A1 단계의 오늘 화면 — 자리만 잡는다.
//
// 명세 §A-4-6 이 "3단 컴포넌트를 이 단계에서 만들되 아직 어느 화면에도 붙이지
// 않는다"고 못박았다. 플래그 배선이 실제로 도는지 눈으로 확인할 곳은 있어야 하므로
// 한 줄만 세운다. A2 가 이 함수를 통째로 갈아 끼운다.
function v3RenderToday(root, briefing, options = {}) {
  root.innerHTML = `<section class="v3-block">
    <h2>${UI_V3_STRINGS.todayTitle}</h2>
    <p class="v3-sub">화면 v3 는 준비 중입니다(A1: 플래그 기반). 기존 화면은 주소에서 <code>ui=v3</code> 를 빼면 그대로 열립니다.</p>
  </section>`;
  v3WireRows(root);
}

const UI_V3 = {
  STRINGS: UI_V3_STRINGS,
  renderToday: v3RenderToday,
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
