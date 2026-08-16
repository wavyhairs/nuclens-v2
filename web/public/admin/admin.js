// 운영 콘솔 — 병합 진단과 수집 설정.
//
// app.js 와 합치지 않는다. 저쪽은 독자용 앱이고 여기는 관리자 도구다. 한 파일에
// 두면 독자가 받는 218KB 번들에 아무도 안 보는 진단 코드가 얹히고, 콘솔을 고칠
// 때마다 독자 화면 회귀를 걱정하게 된다. esc/safeUrl 같은 몇 줄은 복제하는 편이
// 싸다 — 이 파일은 /admin 밖으로는 아무것도 내보내지 않는다.
//
// 읽기 전용이다. 여기서 설정을 고칠 수 있게 만들면 저장소(keywords.json ·
// sources.json · news_bot)와 화면이 갈라지고, 그때부터 둘 다 못 믿는다.

const state = { merges: null, config: null, panel: "merges" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function dateLabel(value) {
  if (!value) return "-";
  const [, month, day] = String(value).split("-");
  if (!month || !day) return String(value);
  return `${Number(month)}월 ${Number(day)}일`;
}

// 0~1 지표는 소수점 넷째 자리까지 온다. 화면에서는 두 자리면 충분하고,
// 없는 값(null)을 0 으로 찍으면 '유사도 0'이라는 없는 사실이 생긴다.
function ratio(value) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

function stat(label, value, note) {
  return `<div class="admin-stat"><span>${esc(label)}</span><strong>${esc(value)}</strong>${
    note ? `<small>${esc(note)}</small>` : ""}</div>`;
}

function chips(values, className = "topic-chip") {
  return (values || []).filter(Boolean)
    .map(value => `<span class="${className}">${esc(value)}</span>`).join("");
}

const RELATION_LABEL = {
  merge: "병합", duplicate: "중복", single: "단독",
  // 수집 단계에서 접힌 것. 예전 파이프라인에서는 이 계층이 화면에 존재하지 않았다 —
  // 그때는 접힌 기사가 story 가 만들어지기도 전에 삭제됐기 때문이다.
  collected: "수집 병합",
};

const FOLD_STAGE_LABEL = {
  collect_url: "URL 동일",
  collect_title: "제목 완전일치",
  collect_fuzzy_title: "제목 유사",
  collect_embedding: "임베딩 의미 중복",
};

// ── 병합 진단 ──────────────────────────────────────────────────────────────

// story 병합은 LLM 이 판단하고 이유를 문장으로 남긴다. 그 문장이 이 화면의
// 핵심이다 — 점수만 있으면 "왜"를 못 읽고, 결국 아무도 검토하지 않는다.
function storyRow(row) {
  const fingerprint = row.fingerprint || {};
  const axes = [
    ["사건 유형", fingerprint.event_family],
    ["국가", (fingerprint.countries || []).join(" · ")],
    ["행위자", (fingerprint.actors || []).join(" · ")],
    ["대상", (fingerprint.assets || []).join(" · ")],
    ["동인", (fingerprint.drivers || []).join(" · ")],
    ["사건일", fingerprint.event_date],
  ].filter(([, value]) => value);
  const sources = (row.sources || []).map(source =>
    `<li><strong>${esc(source.publisher || source.identity || "출처 미상")}</strong>` +
    `<small>${esc(source.domain || "")}${source.tier ? ` · tier ${esc(source.tier)}` : ""}` +
    `${source.evidence_role ? ` · ${esc(source.evidence_role)}` : ""}</small></li>`).join("");
  // 접힌 형제 제목이 이 화면의 진짜 증거다. 제목들이 서로 다른 사건으로 읽히면
  // 그게 오병합이고, 사람은 그걸 한눈에 안다 — 지표는 못 한다.
  const titles = (row.related_titles || [])
    .map(title => `<li>${esc(title)}</li>`).join("");
  // 수집 단계에서 접힌 기사. 큐레이션 전이라 제목·매체·URL 만 있다. 이것이
  // story_outlet_count 를 실제 보도 매체 수로 만들어 주는 재료다.
  const raws = (row.raw_sources || []).map(raw => {
    const url = safeUrl(raw.link);
    const label = esc(raw.title || "제목 없음");
    return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label}
      <small>${esc(raw.publisher || raw.domain || "매체 미상")} · ${
        esc(FOLD_STAGE_LABEL[raw.fold_stage] || raw.fold_stage || "수집")}${
        typeof raw.similarity === "number" ? ` · 유사도 ${ratio(raw.similarity)}` : ""}</small></li>`;
  }).join("");
  // 카드의 얼굴을 story 완성 뒤에 골랐다는 사실. 교체가 있었으면 무엇에서
  // 무엇으로 바뀌었는지가 오병합만큼이나 자주 묻는 질문이다.
  const display = row.display_swapped_from
    ? `<p class="admin-reason"><strong>화면 대표 교체</strong>${esc(row.display_reason || "")}
        — 이전 대표: ${esc(row.display_swapped_from_title || row.display_swapped_from)}</p>`
    : (row.display_candidates > 1
      ? `<p class="admin-reason"><strong>화면 대표</strong>후보 ${row.display_candidates}건 중 이 기사를 유지${
          row.display_reason ? ` — ${esc(row.display_reason)}` : ""}</p>`
      : "");
  return `<article class="admin-card">
    <div class="admin-card-head">
      <div>
        <p class="admin-kicker"><span class="admin-badge ${row.relation === "duplicate" ? "warn" : ""}">${
          esc(RELATION_LABEL[row.relation] || row.relation)}</span>
          <span>${esc(dateLabel(row.briefing_date || row.article_date))}</span>
          ${row.stage ? `<span>${esc(row.stage)}</span>` : ""}</p>
        <h3>${esc(row.title)}</h3>
      </div>
      <p class="admin-card-scale">${row.article_count}건 통합<small>매체 ${row.outlet_count}곳 · 독립 ${
        row.independent_outlet_count}곳${row.tier1_count ? ` · tier1 ${row.tier1_count}` : ""}${
        row.raw_source_count ? ` · 수집 단계 ${row.raw_source_count}건` : ""}</small></p>
    </div>
    ${row.reason
      ? `<p class="admin-reason"><strong>판단 근거</strong>${esc(row.reason)}</p>`
      : '<p class="admin-reason empty"><strong>판단 근거</strong>기록되지 않았습니다</p>'}
    ${display}
    ${axes.length ? `<dl class="admin-fingerprint">${axes.map(([label, value]) =>
      `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>` : ""}
    ${titles ? `<details class="admin-evidence"><summary>접힌 기사 제목 ${
      (row.related_titles || []).length}건</summary><ul class="admin-titles">${titles}</ul></details>` : ""}
    ${raws ? `<details class="admin-evidence"><summary>수집 단계에서 접힌 근거 ${
      row.raw_source_count}건 — 예전에는 여기서 삭제됐습니다</summary><ul class="admin-titles">${raws}</ul></details>` : ""}
    ${sources ? `<details class="admin-evidence"><summary>출처 ${
      (row.sources || []).length}곳</summary><ul class="admin-sources">${sources}</ul></details>` : ""}
    ${row.issue_id ? `<p class="admin-link"><a href="/issue/${esc(row.issue_id)}">이 사건이 들어간 이슈 열기 →</a></p>` : ""}
  </article>`;
}

function renderStory() {
  const story = state.merges?.story;
  const box = document.getElementById("storyMerges");
  const stats = document.getElementById("storyStats");
  if (!story) return;
  const totals = story.totals || {};
  stats.innerHTML = [
    stat("병합", `${totals.merge || 0}건`, "서로 다른 기사를 한 사건으로"),
    stat("중복", `${totals.duplicate || 0}건`, "같은 기사의 재게재"),
    stat("수집 병합", `${totals.collected || 0}건`, "수집 단계에서 접은 것"),
    stat("접힌 기사", `${totals.folded_articles || 0}건`, "카드 뒤로 들어간 원문 수"),
    // 예전에는 이 숫자만큼이 story 가 만들어지기 전에 사라졌다. 지금은 근거로 남는다.
    stat("수집 근거 보존", `${totals.collect_folded_articles || 0}건`, "예전에는 삭제되던 수"),
    stat("계약 판", story.contract_version || "—", ""),
  ].join("");
  const rows = story.merges || [];
  box.innerHTML = rows.length
    ? rows.map(storyRow).join("")
    : `<div class="empty-state"><strong>이 구간에 병합된 사건이 없습니다</strong>
       <p>story 병합은 daily_brief 가 회차를 만들 때 기록합니다. 아직 story 계약이
       붙지 않은 과거 회차는 전부 단독으로 나옵니다.</p></div>`;
  renderStorySplits(story);
}

// "왜 붙었나"의 짝은 "왜 안 붙었나"다. 분리는 결과물에 아무 흔적을 남기지 않아서,
// 이 화면이 없으면 거부권이 과하게 작동해도 아무도 모른다 — 그저 비슷한 카드가
// 두 칸을 차지할 뿐이고, 그게 왜인지는 어디에도 안 적혀 있다.
function renderStorySplits(story) {
  const vetoes = story.stage_vetoes || [];
  const promotions = story.display_promotions || [];
  const box = document.getElementById("storySplits");
  if (!box) return;
  const vetoRows = vetoes.map(veto => `<article class="admin-card">
    <div class="admin-card-head">
      <div>
        <p class="admin-kicker"><span class="admin-badge warn">단계 충돌</span>
          <span>${esc(dateLabel(veto.date))}</span>
          ${veto.stage ? `<span>${esc(veto.stage)}</span>` : ""}</p>
        <h3>${esc(veto.explanation || "사건 단계가 다름")}</h3>
      </div>
    </div>
    <ul class="admin-titles">
      <li>${esc(veto.left_title)}<small>${esc(veto.left_stage_label || "-")}</small></li>
      <li>${esc(veto.right_title)}<small>${esc(veto.right_stage_label || "-")}</small></li>
    </ul>
  </article>`).join("");

  const promoRows = promotions.map(promo => `<article class="admin-card">
    <div class="admin-card-head">
      <div>
        <p class="admin-kicker"><span class="admin-badge">대표 교체</span>
          <span>${esc(dateLabel(promo.date))}</span>
          <span>후보 ${esc(promo.candidates ?? "—")}건</span></p>
        <h3>${esc(promo.to_title)}</h3>
      </div>
    </div>
    <p class="admin-reason"><strong>사유</strong>${esc(promo.reason || "")}</p>
    <ul class="admin-titles"><li>이전 대표<small>${esc(promo.from_title)}</small></li></ul>
  </article>`).join("");

  box.innerHTML = (vetoRows || promoRows)
    ? vetoRows + promoRows
    : `<div class="empty-state"><strong>단계 충돌로 갈라 둔 쌍이 없습니다</strong>
       <p>제목이 닮았는데 심사↔승인·정지↔재가동처럼 사건 단계가 넘어간 조합만 여기
       올라옵니다. 이 기록은 발송 회차(<code>delivery_log</code>)에서 옵니다 —
       아직 회차가 없으면 비어 있는 것이 정상입니다.</p></div>`;
}

// 이슈 병합은 규칙이 판단한다. 어느 규칙이 걸렸는지(method)와 얼마나 빠듯했는지
// (score)를 같이 보여야 "이건 붙을 만했다 / 이건 아니다"를 가를 수 있다.
function clusterRow(cluster) {
  const members = (cluster.members || []).map(member =>
    `<li><time>${esc(dateLabel(member.article_date))}</time>
      <span>${esc(member.title)}</span>
      <small>${esc((member.countries || []).join(" · ") || "국가 미분류")}${
        (member.facilities || []).length ? ` · ${esc(member.facilities.join(" · "))}` : ""}</small></li>`).join("");
  const matches = (cluster.matches || []).map(match => {
    const rule = (state.merges?.issue?.rules || []).find(item => item.id === match.method);
    return `<tr>
      <td><span class="admin-badge ${match.blocked_by?.length ? "warn" : ""}">${
        esc(rule?.label || match.method)}</span></td>
      <td class="num">${ratio(match.score)}</td>
      <td class="num">${ratio(match.title_ratio)}</td>
      <td class="num">${match.tag_shared ?? "—"}</td>
      <td class="num">${ratio(match.embedding_similarity ?? match.local_embedding_similarity)}</td>
      <td>${esc((match.story_fingerprint_shared || []).join(" · ") || "—")}</td>
      <td>${esc((match.blocked_by || []).join(" · ") || "—")}</td>
    </tr>`;
  }).join("");
  return `<article class="admin-card">
    <div class="admin-card-head">
      <div>
        <p class="admin-kicker">${chips(cluster.methods.map(method =>
          (state.merges?.issue?.rules || []).find(rule => rule.id === method)?.label || method), "admin-badge")}
          <span>${esc(dateLabel(cluster.first_seen))}–${esc(dateLabel(cluster.last_seen))}</span></p>
        <h3>${esc(cluster.title || cluster.issue_id)}</h3>
      </div>
      <p class="admin-card-scale">${cluster.member_count}건 연결<small>가장 약한 연결 ${
        ratio(cluster.weakest_score)} · ${(cluster.briefing_dates || []).length}회차</small></p>
    </div>
    <details class="admin-evidence" open>
      <summary>묶인 기사 ${cluster.member_count}건</summary>
      <ul class="admin-members">${members}</ul>
    </details>
    ${matches ? `<details class="admin-evidence">
      <summary>연결 근거 ${(cluster.matches || []).length}쌍</summary>
      <div class="admin-table-scroll"><table class="admin-table">
        <thead><tr><th>규칙</th><th class="num">점수</th><th class="num">제목</th><th class="num">공통태그</th><th class="num">임베딩</th><th>지문 일치</th><th>차단</th></tr></thead>
        <tbody>${matches}</tbody>
      </table></div>
    </details>` : ""}
    <p class="admin-link"><a href="/issue/${esc(cluster.issue_id)}">이슈 상세 열기 →</a></p>
  </article>`;
}

function renderIssues() {
  const issue = state.merges?.issue;
  if (!issue) return;
  const totals = issue.totals || {};
  document.getElementById("issueStats").innerHTML = [
    stat("연결된 이슈", `${totals.clusters || 0}개`, "기사 2건 이상이 묶인 것"),
    stat("검토 대기", `${totals.review_candidates || 0}쌍`, "자동 병합 아래 구간"),
    stat("사람 승인", `${totals.manual_approved || 0}건`, `기각 ${totals.manual_rejected || 0}건`),
    stat("연결 창", `${issue.window_days ?? "—"}일`, issue.matching_version || ""),
  ].join("");
  document.getElementById("mergeRules").innerHTML = (issue.rules || []).map(rule =>
    `<div class="admin-rule"><span class="admin-badge">${esc(rule.label)}</span>
      <p>${esc(rule.detail)}</p>
      <small>${issue.method_counts?.[rule.id] ? `이번 빌드에서 ${issue.method_counts[rule.id]}쌍` : "이번 빌드에서 사용 안 됨"}</small></div>`).join("");
  const clusters = issue.clusters || [];
  document.getElementById("issueClusters").innerHTML = clusters.length
    ? clusters.map(clusterRow).join("")
    : '<div class="empty-state"><strong>날짜를 넘어 연결된 이슈가 없습니다</strong><p>모든 이슈가 단일 회차입니다.</p></div>';

  const borderline = issue.borderline || [];
  document.getElementById("borderline").innerHTML = borderline.length
    ? `<div class="admin-table-scroll"><table class="admin-table borderline">
        <thead><tr><th class="num">점수</th><th>한쪽</th><th>다른 쪽</th><th class="num">제목 유사</th><th class="num">공통태그</th><th>차단</th></tr></thead>
        <tbody>${borderline.map(row => `<tr>
          <td class="num">${ratio(row.score)}</td>
          <td><strong>${esc(row.left_title)}</strong><small>${esc(dateLabel(row.left_date))}</small></td>
          <td><strong>${esc(row.right_title)}</strong><small>${esc(dateLabel(row.right_date))}</small></td>
          <td class="num">${ratio(row.diagnostics?.title_ratio)}</td>
          <td class="num">${row.diagnostics?.tag_shared ?? "—"}</td>
          <td>${esc((row.diagnostics?.blocked_by || []).join(" · ") || "—")}</td>
        </tr>`).join("")}</tbody>
      </table></div>`
    : '<div class="empty-state"><strong>경계선 후보가 없습니다</strong><p>문턱 아래 구간에 남은 쌍이 없습니다.</p></div>';
}

// ── 수집 설정 ──────────────────────────────────────────────────────────────

function renderKeywords() {
  const keywords = state.config?.keywords;
  if (!keywords) return;
  const totals = keywords.totals || {};
  document.getElementById("keywordStats").innerHTML = [
    stat("키워드 그룹", `${totals.groups || 0}개`, "검색 쿼리 묶음"),
    stat("고정 키워드", `${totals.keywords || 0}개`, "그대로 검색에 나갑니다"),
    stat("앵커", `${totals.anchors || 0}개`, "원자력 문맥 확인용"),
    stat("학습 쿼리", `${state.config?.search?.learned_query_count ?? 0}개`, "discovery 가 스스로 늘린 것"),
  ].join("");
  document.getElementById("keywordGroups").innerHTML = (keywords.groups || []).map(group =>
    `<article class="admin-card">
      <div class="admin-card-head">
        <div><h3>${esc(group.name)}</h3></div>
        <p class="admin-card-scale">키워드 ${group.keywords.length}개<small>앵커 ${group.anchors.length}개</small></p>
      </div>
      <div class="admin-chip-block"><h4>검색 키워드</h4><div class="topic-row">${chips(group.keywords)}</div></div>
      ${group.anchors.length ? `<details class="admin-evidence">
        <summary>앵커 ${group.anchors.length}개 — 결과가 원자력 문맥인지 확인하는 말</summary>
        <div class="topic-row">${chips(group.anchors)}</div>
      </details>` : ""}
      ${group.negative_terms ? `<p class="admin-reason"><strong>제외어</strong><code>${
        esc(group.negative_terms)}</code></p>` : ""}
    </article>`).join("") + (state.config?.anti_keywords?.length
      ? `<article class="admin-card">
          <div class="admin-card-head"><div><h3>공통 제외어</h3></div>
            <p class="admin-card-scale">${state.config.anti_keywords.length}개<small>제목에 걸리면 버립니다</small></p></div>
          <div class="topic-row">${chips(state.config.anti_keywords)}</div>
        </article>`
      : "");
}

function feedTable(title, note, rows, columns) {
  if (!rows.length) return "";
  return `<section class="admin-card">
    <div class="admin-card-head"><div><h3>${esc(title)}</h3><p class="data-note">${esc(note)}</p></div>
      <p class="admin-card-scale">${rows.length}곳</p></div>
    <div class="admin-table-scroll"><table class="admin-table">
      <thead><tr>${columns.map(column => `<th>${esc(column.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(row => `<tr>${columns.map(column =>
        `<td>${column.cell(row)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
  </section>`;
}

function renderFeeds() {
  const feeds = state.config?.feeds;
  if (!feeds) return;
  const rss = feeds.rss || [];
  const official = feeds.official || [];
  const viaDirect = rss.filter(row => row.via === "direct").length;
  document.getElementById("feedStats").innerHTML = [
    stat("RSS 피드", `${rss.length}곳`, `직접 ${viaDirect} · 우회 ${rss.length - viaDirect}`),
    stat("기관 직접 수집", `${official.length}곳`, "보도자료 페이지를 직접 읽습니다"),
    stat("검색 엔진", (state.config?.search?.engines || []).join(" · ") || "—", "국내 기사 발굴"),
    stat("발간물", `${(state.config?.publications?.orgs || []).length}개 기관`, "보고서·분석 자료"),
  ].join("");

  const linkCell = row => {
    const url = safeUrl(row.url);
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(row.domain || url)}</a>`
               : esc(row.domain || "");
  };
  document.getElementById("feedTables").innerHTML = [
    feeds.error
      ? `<div class="error-state"><strong>수집원 목록을 읽지 못했습니다</strong><p>${esc(feeds.error)}</p></div>`
      : "",
    feedTable("해외·전문지 RSS", "news_bot.RSS_SOURCES", rss, [
      { label: "이름", cell: row => esc(row.name) },
      { label: "도메인", cell: linkCell },
      // 직접 피드와 Google News 우회는 신뢰도가 다르다 — 우회는 색인 지연과
      // 관련도순 정렬을 타므로 같은 것으로 읽히면 안 된다.
      { label: "경로", cell: row => row.via === "google_news"
        ? '<span class="admin-badge warn">Google News 우회</span>'
        : '<span class="admin-badge">직접 피드</span>' },
      { label: "키워드 필수", cell: row => row.require_keywords ? "예" : "—" },
    ]),
    feedTable("국내 기관 직접 수집", "news_bot.OFFICIAL_DIRECT_SOURCES · 보도자료 원문", official, [
      { label: "기관", cell: row => esc(row.publisher || row.name) },
      { label: "게시판", cell: row => esc(row.name) },
      { label: "도메인", cell: linkCell },
      { label: "수집 방식", cell: row => `<code>${esc(row.kind)}</code>` },
    ]),
    (state.config?.publications?.orgs || []).length
      ? `<section class="admin-card">
          <div class="admin-card-head"><div><h3>발간물 기관</h3><p class="data-note">pubs_fetch · 보고서·분석 자료</p></div>
            <p class="admin-card-scale">${state.config.publications.orgs.length}곳</p></div>
          <div class="topic-row">${chips(state.config.publications.orgs)}</div>
        </section>`
      : "",
  ].filter(Boolean).join("");
}

function renderTiers() {
  const tiers = state.config?.source_tiers;
  if (!tiers) return;
  const rows = tiers.rows || [];
  document.getElementById("tierTable").innerHTML = `
    <p class="data-note">tier1 선정 가산 +${esc(tiers.tier1_bonus ?? "—")} · tier2 +${esc(tiers.tier2_bonus ?? "—")} · tier3 가산 없음</p>
    <div class="admin-table-scroll"><table class="admin-table">
      <thead><tr><th>등급</th><th>이름</th><th>도메인</th><th>매체 성격</th><th>근거 역할</th></tr></thead>
      <tbody>${rows.map(row => `<tr>
        <td><span class="admin-badge${row.tier === 1 ? "" : " muted"}">tier ${esc(row.tier)}</span></td>
        <td>${esc(row.name)}</td>
        <td>${esc(row.domain)}</td>
        <td>${esc(row.source_type)}</td>
        <td>${esc(row.evidence_role)}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
}

// ── 뼈대 ───────────────────────────────────────────────────────────────────

function showPanel(panel) {
  state.panel = panel;
  for (const button of document.querySelectorAll("#adminTabs [data-panel]")) {
    const active = button.dataset.panel === panel;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  document.getElementById("panel-merges").hidden = panel !== "merges";
  document.getElementById("panel-config").hidden = panel !== "config";
  const url = new URL(location.href);
  if (panel === "merges") url.searchParams.delete("panel");
  else url.searchParams.set("panel", panel);
  history.replaceState(history.state, "", url);
}

// /admin/data 아래에서 읽는다. /data 는 독자 화면이 쓰는 공개 경로라 엣지의
// 접근 통제(functions/admin/_middleware.js)가 닿지 않는다 — 화면만 잠그고 데이터를
// 공개 경로에 두면 URL 하나로 그대로 읽힌다.
async function loadJSON(name) {
  const response = await fetch(`/admin/data/${name}?cb=${Date.now()}`, { cache: "no-store" });
  if (response.status === 401) throw new Error("세션이 만료되었습니다 — 다시 로그인하세요");
  if (!response.ok) throw new Error(`${name} ${response.status}`);
  return response.json();
}

async function start() {
  const status = document.getElementById("adminStatus");
  try {
    [state.merges, state.config] = await Promise.all([
      loadJSON("merges.json"),
      loadJSON("config.json"),
    ]);
  } catch (error) {
    // 콘솔이 조용히 비면 '병합이 하나도 없다'로 읽힌다 — 원인을 그대로 적는다.
    status.className = "readiness-panel";
    status.innerHTML = `<div><strong>운영 데이터를 불러오지 못했습니다</strong>
      <p>${esc(String(error.message || error))} — 아직 빌드되지 않았을 수 있습니다.
      <code>python web/build_data.py</code> 이후 다시 확인하세요.</p></div>`;
    return;
  }
  const totals = state.merges?.story?.totals || {};
  const clusters = state.merges?.issue?.totals?.clusters || 0;
  status.className = "readiness-panel ready";
  status.innerHTML = `<div><strong>병합 ${esc((totals.merge || 0) + (totals.duplicate || 0))}건 · 연결된 이슈 ${esc(clusters)}개</strong>
    <p>같은 날 병합은 기사 ${esc(totals.folded_articles || 0)}건을 접었습니다. 위험한 쪽은 누락이 아니라 오병합입니다.</p></div>`;
  document.getElementById("adminGenerated").textContent =
    `생성 ${String(state.merges?.generated_at || "").slice(0, 16).replace("T", " ")}`;

  renderStory();
  renderIssues();
  renderKeywords();
  renderFeeds();
  renderTiers();

  document.getElementById("adminTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-panel]");
    if (button) showPanel(button.dataset.panel);
  });
  const requested = new URLSearchParams(location.search).get("panel");
  showPanel(requested === "config" ? "config" : "merges");
}

start();
