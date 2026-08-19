// 운영 콘솔 — 병합 진단, 수집 설정, 그리고 사람이 내린 판정의 목록.
//
// app.js 와 합치지 않는다. 저쪽은 독자용 앱이고 여기는 관리자 도구다. 한 파일에
// 두면 독자가 받는 218KB 번들에 아무도 안 보는 진단 코드가 얹히고, 콘솔을 고칠
// 때마다 독자 화면 회귀를 걱정하게 된다. esc/safeUrl 같은 몇 줄은 복제하는 편이
// 싸다 — 이 파일은 /admin 밖으로는 아무것도 내보내지 않는다.
//
// 쓰기가 생겼다 — 그러나 저장소를 덮어쓰지는 않는다
// -------------------------------------------------
// 화면에서 누른 것은 `/admin/api/overrides` 로 가서 KV 에 항목으로 쌓이고,
// 워크플로가 그것을 `admin_overrides.json` 으로 끌어와 커밋한다. 기본 설정 파일
// (keywords.json · sources.json · news_bot 상수)은 그대로 남고 그 위에 덧칠된다.
// 그래서 저장소에서 손으로 고친 것과 여기서 누른 것이 서로를 조용히 지우지 않는다.
//
// 대신 **즉시 반영되지 않는다.** 다음 수집(최대 3시간)부터 듣는다. 화면은 그
// 사실을 숨기지 않는다 — 눌렀는데 아무 변화가 없어 보이면 관리자는 같은 판정을
// 몇 번씩 다시 누르고, 그러면 목록이 중복으로 찬다.

const state = {
  merges: null,
  config: null,
  overrides: { rev: 0, entries: [], updated_at: "" },
  panel: "merges",
  entryFilter: "all",
};

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

const KIND_LABEL = {
  story_split: "같은 날 분리", issue_split: "이슈 분리",
  issue_group_split: "사건 나누기", issue_join: "이슈 잇기",
  learned_rule: "학습 규칙",
  keyword_add: "키워드 추가", keyword_remove: "키워드 삭제",
  anchor_add: "앵커 추가", anchor_remove: "앵커 삭제",
  negative_add: "제외어 추가", negative_remove: "제외어 삭제",
  anti_add: "공통 제외어 추가", anti_remove: "공통 제외어 삭제",
  feed_add: "수집원 추가", feed_disable: "수집원 중지",
  official_disable: "기관 수집 중지",
  tier_upsert: "출처 등급 수정", tier_remove: "출처 등급 삭제",
  learned_term_add: "학습 검색어 추가", learned_term_remove: "학습 검색어 삭제",
  learned_term_keep: "학습 검색어 유지",
};

// 신규 이슈 탐색이 붙이는 유형. 화면에서 'plant' 를 그대로 보이면 관리자는
// 이것이 무슨 축인지 알 수 없다.
const LEARNED_TYPE_LABEL = {
  plant: "원전", company: "기업", org: "기관",
  project: "정책·사업", tech: "노형·기술", manual: "직접 입력",
};

const SOURCE_TYPES = [
  ["official", "공식기관"], ["specialist_media", "전문언론"],
  ["general_media", "일반언론"], ["press_release", "보도자료"], ["unknown", "미상"],
];
const EVIDENCE_ROLES = [
  ["primary", "1차 원문"], ["independent", "독립 취재"],
  ["distributed_claim", "받아쓴 주장"], ["unknown", "미상"],
];

// ── 쓰기 창구 ──────────────────────────────────────────────────────────────

function toast(message, kind = "ok") {
  const box = document.getElementById("adminToast");
  box.className = `admin-toast ${kind}`;
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { box.hidden = true; }, kind === "error" ? 9000 : 5000);
}

async function api(body) {
  const response = await fetch("/admin/api/overrides", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({ ...body, rev: state.overrides.rev }),
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    // 409(다른 곳에서 먼저 저장)는 화면이 낡았다는 뜻이라 최신본을 당겨 온다.
    if (response.status === 409 && Number.isInteger(payload.rev)) await pullOverrides();
    throw new Error(payload.error || `저장 실패 (${response.status})`);
  }
  state.overrides = payload;
  return payload;
}

async function pullOverrides() {
  try {
    const response = await fetch(`/admin/api/overrides?cb=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return false;
    state.overrides = await response.json();
    return true;
  } catch {
    return false;
  }
}

// 저장 → 최신 목록 반영 → 화면 다시 그리기. 저장 직후에 목록을 다시 그리지 않으면
// 방금 지운 항목이 화면에 남아 관리자가 한 번 더 누른다.
async function submit(body, okMessage) {
  try {
    await api(body);
    toast(okMessage, "ok");
  } catch (error) {
    toast(String(error.message || error), "error");
    return false;
  }
  renderAll();
  return true;
}

function entriesOf(kind) {
  return (state.overrides.entries || []).filter(row => row.kind === kind);
}

// 이 판정이 파이프라인에 도달했는가. 빌드된 데이터가 들고 있는 id 목록에 있으면
// 이미 듣고 있는 것이고, KV 에만 있으면 다음 수집을 기다리는 중이다.
function liveEntryIds() {
  const ids = new Set();
  for (const entry of (state.merges?.judgments?.entries || [])) ids.add(entry.id);
  for (const entry of (state.config?.overrides?.entries || [])) ids.add(entry.id);
  return ids;
}

function pendingEntries() {
  const live = liveEntryIds();
  return (state.overrides.entries || []).filter(entry => !live.has(entry.id));
}

function pendingBadge(entry) {
  return liveEntryIds().has(entry.id)
    ? '<span class="admin-badge">적용됨</span>'
    : '<span class="admin-badge warn">다음 수집부터</span>';
}

function renderPendingBanner() {
  const pending = pendingEntries();
  for (const id of ["configPending", "mergePending"]) {
    const box = document.getElementById(id);
    if (!box) continue;
    box.hidden = pending.length === 0;
    if (!pending.length) continue;
    box.innerHTML = `<strong>${pending.length}건이 아직 반영되지 않았습니다</strong>
      <p>판정은 다음 수집(최대 3시간)에 파이프라인으로 넘어갑니다. 지금 바로 적용하려면
      GitHub Actions 의 <code>Nuclear news crawl</code> 워크플로를 수동 실행하세요.</p>`;
  }
}

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
    ${storySplitBlock(row)}
    ${titles ? `<details class="admin-evidence"><summary>접힌 기사 제목 ${
      (row.related_titles || []).length}건</summary><ul class="admin-titles">${titles}</ul></details>` : ""}
    ${raws ? `<details class="admin-evidence"><summary>수집 단계에서 접힌 근거 ${
      row.raw_source_count}건 — 예전에는 여기서 삭제됐습니다</summary><ul class="admin-titles">${raws}</ul></details>` : ""}
    ${sources ? `<details class="admin-evidence"><summary>출처 ${
      (row.sources || []).length}곳</summary><ul class="admin-sources">${sources}</ul></details>` : ""}
    ${row.issue_id ? `<p class="admin-link"><a href="/issue/${esc(row.issue_id)}">이 사건이 들어간 이슈 열기 →</a></p>` : ""}
  </article>`;
}

// 수동 분리. 여기서 하는 일을 부풀리지 않는다 — 이미 나간 회차의 카드는 그대로
// 남는다(접힌 기사는 아카이브에 별도 레코드가 없어 되살릴 재료 자체가 없다).
// 실제 효과는 두 가지고, 화면에 그대로 적는다.
//   ① 이 조합은 앞으로 접지 않는다 (아직 큐에 살아 있는 동안 — 저녁에 갈라 두면
//      다음 날 아침 브리핑이 그 판정을 따른다).
//   ② 여기서 배운 판별축은 **새 기사에도** 적용된다 — 그쪽이 학습의 본체다.
function storySplitBlock(row) {
  const members = (row.members || []).filter(member => member.hash && member.hash !== row.hash);
  if (!members.length) {
    return `<p class="admin-reason empty"><strong>수동 분리</strong>이 회차는 분리 단위를
      남기지 않았습니다 — story 멤버 기록(hash↔제목)이 붙기 전의 회차입니다.</p>`;
  }
  const rows = members.map(member => {
    const blocked = entriesOf("story_split").some(entry =>
      [entry.left_hash, entry.right_hash].sort().join("|")
        === [row.hash, member.hash].sort().join("|"));
    return `<li>
      <div><strong>${esc(member.title || "제목 없음")}</strong>
        <small>${esc(member.publisher || "매체 미상")}${
          member.fold_stage ? ` · ${esc(FOLD_STAGE_LABEL[member.fold_stage] || member.fold_stage)}` : ""}</small></div>
      ${blocked
        ? '<span class="admin-badge">분리됨</span>'
        : `<button class="admin-mini" data-act="split-open" data-rep="${esc(row.hash)}"
             data-member="${esc(member.hash)}">떼어내기</button>`}
    </li>`;
  }).join("");
  return `<details class="admin-evidence admin-split">
    <summary>잘못 묶였나 — 기사 ${members.length}건 중에서 떼어내기</summary>
    <p class="admin-hint">떼어내도 <strong>이미 나간 회차의 카드는 바뀌지 않습니다</strong>
      (접힌 기사는 아카이브에 별도 기록이 없습니다). 바뀌는 것은 앞으로입니다 —
      이 조합을 다시 접지 않고, 함께 저장한 판별축은 새 기사에도 적용됩니다.</p>
    <ul class="admin-actions">${rows}</ul>
    <div class="admin-form-slot" data-slot="split-${esc(row.hash)}"></div>
  </details>`;
}

// 분리 사유를 물으면서 판별축을 제안한다. 제목에서 한쪽에만 있는 낱말이 곧
// 후보다 — 사람은 그중 무엇이 '사건을 가르는 축'인지 한눈에 알지만, 그걸 처음부터
// 타이핑하게 하면 아무도 안 한다.
//
// 축은 **나눈 뒤에** 뽑는다. 기사 두 건만 비교하면 두 제목의 우연한 차이(매체가
// 쓴 수식어, 날짜 표현)까지 후보로 올라오지만, 사건군 대 사건군으로 비교하면
// 한쪽 군의 여러 제목에 반복해서 나오는 말이 위로 온다 — 그것이 사건을 가르는
// 말일 가능성이 훨씬 높다. 그래서 인자는 제목 **목록** 둘이다.
function suggestAxis(leftTitles, rightTitles) {
  const tokens = value => String(value || "")
    .split(/[^0-9A-Za-z가-힣]+/).filter(word => word.length >= 2);
  // 낱말 → 그 낱말이 나온 제목 수. 같은 제목에서 두 번 나와도 한 번으로 센다.
  const frequency = titles => {
    const counts = new Map();
    for (const title of titles || []) {
      for (const word of new Set(tokens(title))) {
        const key = word.toLowerCase();
        const seen = counts.get(key);
        if (seen) seen.count += 1;
        else counts.set(key, { word, count: 1 });
      }
    }
    return counts;
  };
  const left = frequency(leftTitles);
  const right = frequency(rightTitles);
  // 반대편 군에 **한 번도** 안 나온 말만 축이 될 수 있다. 양쪽에 다 있는 말로는
  // 영원히 안 갈린다(admin_overrides.rule_conflict 는 겹치면 침묵한다).
  const only = (mine, theirs) => [...mine.entries()]
    .filter(([key]) => !theirs.has(key))
    // 여러 제목에 걸친 말이 먼저. 같은 빈도면 제목에 나온 순서를 지킨다(안정 정렬).
    .sort((a, b) => b[1].count - a[1].count)
    .map(([, value]) => value.word)
    .slice(0, 6);
  return { left: only(left, right), right: only(right, left) };
}

// 이 축이 얼마나 넓은가를 **저장하기 전에** 보여 준다. 화면에 이미 실린 제목만
// 세는 어림이지만, '원전' 같은 말을 축으로 골랐을 때 수십 건이 뜨는 것만으로도
// 충분히 막아 준다. 정확한 수치는 다음 빌드가 '내 판정' 탭에 적는다.
function axisReach(terms) {
  const needles = terms.map(term => term.replace(/\s+/g, "").toLowerCase()).filter(Boolean);
  if (!needles.length) return 0;
  return allKnownTitles().filter(title =>
    needles.some(needle => title.includes(needle))).length;
}

let _titleCache = null;
function allKnownTitles() {
  if (_titleCache) return _titleCache;
  const out = [];
  const push = value => {
    const text = String(value || "").replace(/\s+/g, "").toLowerCase();
    if (text) out.push(text);
  };
  for (const row of (state.merges?.story?.merges || [])) {
    push(row.title);
    (row.related_titles || []).forEach(push);
  }
  for (const cluster of (state.merges?.issue?.clusters || [])) {
    (cluster.members || []).forEach(member => push(member.title));
  }
  _titleCache = out;
  return out;
}

function splitForm(repHash, memberHash) {
  const row = (state.merges?.story?.merges || []).find(item => item.hash === repHash);
  const member = ((row || {}).members || []).find(item => item.hash === memberHash) || {};
  const suggestion = suggestAxis([row?.title], [member.title]);
  const chipRow = (side, words) => words.map((word, index) =>
    `<label class="admin-chip-toggle"><input type="checkbox" name="${side}" value="${esc(word)}"
      ${index === 0 ? "checked" : ""}> ${esc(word)}</label>`).join("") || '<small>제안 없음</small>';
  return `<form class="admin-form" data-act="split-save" data-rep="${esc(repHash)}"
      data-member="${esc(memberHash)}">
    <p class="admin-form-title">이 기사를 떼어냅니다</p>
    <ul class="admin-titles">
      <li>${esc(row?.title || "")}<small>남는 카드</small></li>
      <li>${esc(member.title || "")}<small>떼어낼 기사</small></li>
    </ul>
    <label class="admin-field"><span>왜 다른 사건입니까</span>
      <input name="note" type="text" maxlength="300" required
             placeholder="예: 같은 호기가 아니라 설비가 다르다"></label>
    <fieldset class="admin-axis">
      <legend>앞으로도 쓸 판별축 <small>고른 낱말이 서로 다른 쪽에 있으면 두 기사를 접지 않습니다</small></legend>
      <div class="admin-axis-side"><span>이 카드 쪽</span><div>${chipRow("left", suggestion.left)}</div></div>
      <div class="admin-axis-side"><span>떼어낸 기사 쪽</span><div>${chipRow("right", suggestion.right)}</div></div>
      <p class="admin-axis-reach" data-role="reach"></p>
      <label class="admin-check"><input type="checkbox" name="learn" checked>
        이 판별축을 학습합니다 (끄면 이 두 기사만 갈라 둡니다)</label>
    </fieldset>
    <div class="admin-form-buttons">
      <button type="submit" class="admin-mini primary">분리 저장</button>
      <button type="button" class="admin-mini" data-act="form-close">취소</button>
    </div>
  </form>`;
}

function updateReach(form) {
  const pick = side => [...form.querySelectorAll(`input[name="${side}"]:checked`)]
    .map(input => input.value);
  const left = pick("left");
  const right = pick("right");
  const box = form.querySelector('[data-role="reach"]');
  if (!box) return;
  if (!left.length || !right.length) {
    box.textContent = "양쪽 모두 한 낱말 이상 고르면 학습됩니다. 비우면 이 두 기사만 갈라 둡니다.";
    box.className = "admin-axis-reach";
    return;
  }
  const leftHits = axisReach(left);
  const rightHits = axisReach(right);
  const wide = leftHits > 25 || rightHits > 25;
  box.textContent = wide
    ? `주의 — 화면에 실린 제목 중 왼쪽 ${leftHits}건 · 오른쪽 ${rightHits}건에 걸립니다. 축이 너무 넓으면 무관한 사건까지 갈라 놓습니다.`
    : `화면에 실린 제목 중 왼쪽 ${leftHits}건 · 오른쪽 ${rightHits}건에 걸립니다.`;
  box.className = wide ? "admin-axis-reach warn" : "admin-axis-reach";
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
    stat("사람 분리", `${entriesOf("story_split").length}건`, "관리자가 갈라 둔 조합"),
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
  const vetoRows = vetoes.map(veto => {
    // 단계 충돌(기계)과 사람·학습 판정을 같은 목록에 두되 배지로 가른다 —
    // 규칙을 고칠 때 어느 쪽이 발동했는지가 곧 어디를 고칠지다.
    const human = veto.kind === "admin_split" || veto.kind === "learned_rule";
    const badge = veto.kind === "admin_split" ? "관리자 분리"
      : veto.kind === "learned_rule" ? "학습 규칙" : "단계 충돌";
    return `<article class="admin-card">
    <div class="admin-card-head">
      <div>
        <p class="admin-kicker"><span class="admin-badge ${human ? "" : "warn"}">${esc(badge)}</span>
          <span>${esc(dateLabel(veto.date))}</span>
          ${veto.stage ? `<span>${esc(veto.stage)}</span>` : ""}</p>
        <h3>${esc(veto.explanation || "사건 단계가 다름")}</h3>
      </div>
    </div>
    <ul class="admin-titles">
      <li>${esc(veto.left_title)}<small>${esc(veto.left_stage_label || "-")}</small></li>
      <li>${esc(veto.right_title)}<small>${esc(veto.right_stage_label || "-")}</small></li>
    </ul>
  </article>`;
  }).join("");

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
  const splittable = (cluster.members || []).filter(member => member.hash).length >= 2;
  const members = (cluster.members || []).map(member => {
    const separated = splitSeparates(cluster, member.hash);
    return `<li><time>${esc(dateLabel(member.article_date))}</time>
      <span>${esc(member.title)}</span>
      <small>${esc((member.countries || []).join(" · ") || "국가 미분류")}${
        (member.facilities || []).length ? ` · ${esc(member.facilities.join(" · "))}` : ""}</small>
      ${!splittable || !member.hash ? ""
        : separated ? '<span class="admin-badge">갈라 둠</span>'
        // 여기서 무엇과 갈라지는지는 **다음 화면**에서 정한다. 예전에는 이 버튼이
        // 곧바로 저장했고 상대 기사는 코드가 골랐다(대표 기사) — 화면은 그 상대를
        // 보여 주지 않았다. 그래서 "해외수출 기사를 뺀다"고 적힌 판정이 실제로는
        // 계속운전 기사 둘을 갈라 놓는 일이 일어났다(2026-08-16 실측).
        : `<button class="admin-mini" data-act="group-split-open"
             data-issue="${esc(cluster.issue_id)}" data-anchor="${esc(member.hash)}"
             data-preset="alone">이 기사만 분리…</button>`}
    </li>`;
  }).join("");
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
      <summary>묶인 기사 ${cluster.member_count}건 — 잘못 묶였으면 나눕니다</summary>
      ${pendingSplitNote(cluster)}
      <ul class="admin-members with-action">${members}</ul>
      ${splittable ? `<div class="admin-form-buttons">
        <button class="admin-mini" data-act="group-split-open"
          data-issue="${esc(cluster.issue_id)}" data-preset="manual">두 사건으로 나누기…</button>
        <small class="admin-hint-inline">기사 하나가 잘못 들어온 게 아니라
          서로 다른 사건군이 섞였을 때 — 어느 기사가 어느 쪽인지 직접 세웁니다.</small>
      </div>` : ""}
      <div class="admin-form-slot" data-slot="issue-${esc(cluster.issue_id)}"></div>
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

// ── 사건 나누기 ─────────────────────────────────────────────────────────────
//
// 왜 '떼어내기'가 아니라 '나누기'인가 — 2026-08-16 에 실제로 벌어진 일
// ------------------------------------------------------------------------
// 예전 버튼은 멤버마다 [떼어내기] 하나였고, **상대 기사는 코드가 골랐다**(대표
// 기사, 대표를 눌렀으면 두 번째 멤버). 화면은 그 상대를 끝까지 보여 주지 않았다.
// 관리자는 "이 기사를 이 이슈에서 뺀다"로 읽었지만 저장된 것은 임의의 쌍이었고,
// 그래서 사유에는 '해외수출'이라 적혀 있는데 실제 판정은 계속운전 기사 둘을
// 갈라 놓은 기록이 남았다. 그 판정은 다음 빌드에서 **엉뚱한 두 기사**를 갈라 놓는다.
//
// 게다가 쌍 하나로는 애초에 갈라지지 않는다. build_data.assign_issues 의 합류는
// 멤버 하나만 맞으면 되는 탐욕적 구조라, 막히지 않은 다른 멤버를 통해 같은
// 이슈로 도로 들어온다. 하나씩 떼는 조작은 원리적으로 부족하다.
//
// 그래서 단위를 바꾼다. 사람이 아는 것은 "이 넷과 저 둘이 다른 사건"이지
// "3번과 5번이 다른 사건"이 아니다. 화면은 **선**을 긋게 하고, 파이프라인이
// 그 선을 가로지르는 쌍 전부로 펼친다(admin_overrides.group_splits).
// 그리고 저장 직전에 어떤 쌍이 못 박히는지 그대로 보여 준다.

function clusterOf(issueId) {
  return (state.merges?.issue?.clusters || [])
    .find(cluster => cluster.issue_id === issueId) || null;
}

// 이 멤버가 이미 (아직 반영 안 된 판정으로) 다른 멤버와 갈라졌는가.
function splitSeparates(cluster, hash) {
  if (!hash) return false;
  const others = (cluster.members || [])
    .map(member => member.hash).filter(other => other && other !== hash);
  const crosses = (mine, theirs) =>
    mine.includes(hash) && theirs.some(other => others.includes(other));
  return entriesOf("issue_group_split").some(entry =>
    crosses(entry.left_hashes || [], entry.right_hashes || [])
      || crosses(entry.right_hashes || [], entry.left_hashes || []))
    // 옛 방식(쌍 하나)으로 저장된 판정도 계속 보여 준다 — 지우려면 먼저 보여야 한다.
    || entriesOf("issue_split").some(entry =>
      (entry.left_hash === hash && others.includes(entry.right_hash))
        || (entry.right_hash === hash && others.includes(entry.left_hash)));
}

function pendingSplitNote(cluster) {
  const hashes = (cluster.members || []).map(member => member.hash).filter(Boolean);
  const touches = entry => [...(entry.left_hashes || []), ...(entry.right_hashes || [])]
    .some(hash => hashes.includes(hash));
  const mine = entriesOf("issue_group_split")
    .filter(entry => entry.issue_id === cluster.issue_id || touches(entry));
  if (!mine.length) return "";
  const lines = mine.map(entry =>
    `<li>${esc(entrySubject(entry))}
      <small>${(entry.left_hashes || []).length}건 ↔ ${(entry.right_hashes || []).length}건 · ${
        esc(entry.note || "사유 없음")}</small></li>`).join("");
  return `<p class="admin-reason"><strong>나누기 판정 ${mine.length}건</strong>
    다음 빌드에서 갈라집니다. 이 화면의 묶음은 아직 나뉘기 전 상태입니다.</p>
    <ul class="admin-titles">${lines}</ul>`;
}

// 사건군을 세우는 화면. 클릭한 기사는 왼쪽에 고정되고, 나머지는 관리자가
// 한 건씩 어느 쪽인지 정한다. preset="alone" 은 "이 기사만 잘못 들어왔다" —
// 나머지 전부를 반대쪽으로 세운 상태로 연다(그래도 저장 전에 보여 준다).
function groupSplitForm(issueId, anchorHash, preset) {
  const cluster = clusterOf(issueId);
  if (!cluster) return "";
  const members = (cluster.members || []).filter(member => member.hash);
  const anchor = members.find(member => member.hash === anchorHash) || members[0] || {};
  const rest = members.filter(member => member.hash !== anchor.hash);
  const rows = rest.map(member => {
    const name = `side:${member.hash}`;
    const split = preset === "alone";
    return `<li>
      <div><strong>${esc(member.title || "제목 없음")}</strong>
        <small>${esc(dateLabel(member.article_date))} · ${
          esc((member.countries || []).join(" · ") || "국가 미분류")}</small></div>
      <div class="admin-side-pick">
        <label><input type="radio" name="${esc(name)}" value="keep"${
          split ? "" : " checked"}> 같은 사건</label>
        <label><input type="radio" name="${esc(name)}" value="split"${
          split ? " checked" : ""}> 다른 사건</label>
      </div>
    </li>`;
  }).join("");
  return `<form class="admin-form" data-act="group-split-save"
      data-issue="${esc(cluster.issue_id)}" data-anchor="${esc(anchor.hash || "")}">
    <p class="admin-form-title">이 이슈를 두 사건으로 나눕니다</p>
    <p class="admin-hint">기사 하나를 어딘가에서 떼는 것이 아니라 <strong>선을 하나 긋는</strong>
      것입니다. 선을 가로지르는 조합은 앞으로 한 이슈로 붙지 않습니다. 쌍 하나만
      막으면 다른 기사를 통해 도로 합쳐집니다 — 그래서 양쪽을 다 세웁니다.</p>
    <ul class="admin-split-pick">
      <li class="anchor">
        <div><strong>${esc(anchor.title || "제목 없음")}</strong>
          <small>기준 기사 — 아래에서 '같은 사건'을 고른 기사들과 함께 남습니다</small></div>
        <div class="admin-side-pick"><span class="admin-badge">기준</span></div>
      </li>
      ${rows}
    </ul>
    <div class="admin-split-preview" data-role="preview"></div>
    <label class="admin-field"><span>왜 다른 사건입니까</span>
      <input name="note" type="text" maxlength="300" required
             placeholder="예: 한쪽은 계속운전 심사, 다른 쪽은 원전 수출 업무협약"></label>
    <fieldset class="admin-axis">
      <legend>앞으로도 쓸 판별축
        <small>두 사건군의 제목을 비교해 뽑은 후보입니다 — 고른 낱말이 서로 다른 쪽에
          있으면 새 기사도 접지 않습니다</small></legend>
      <div class="admin-axis-side"><span>같은 사건 쪽</span><div data-role="axis-left"></div></div>
      <div class="admin-axis-side"><span>다른 사건 쪽</span><div data-role="axis-right"></div></div>
      <p class="admin-axis-reach" data-role="reach"></p>
      <label class="admin-check"><input type="checkbox" name="learn" checked>
        이 판별축을 학습합니다 (끄면 이 기사들만 갈라 둡니다)</label>
    </fieldset>
    <div class="admin-form-buttons">
      <button type="submit" class="admin-mini primary" data-role="save">이 나누기를 저장</button>
      <button type="button" class="admin-mini" data-act="form-close">취소</button>
    </div>
  </form>`;
}

// 폼에 세워진 두 사건군. 화면의 라디오만 읽는다 — 여기가 저장될 값의 유일한 출처다.
function groupSides(form) {
  const cluster = clusterOf(form.dataset.issue);
  const members = ((cluster || {}).members || []).filter(member => member.hash);
  const anchor = members.find(member => member.hash === form.dataset.anchor) || members[0];
  // 선택자에 hash 를 끼워 넣지 않는다(findSlot 의 같은 주의) — 값이 따옴표를 물면
  // 선택자가 깨지고, 증상은 '눌러도 아무 일도 안 일어남'이라 원인을 찾기 어렵다.
  // 라디오를 전부 훑어 이름에서 hash 를 떼어 낸다.
  const picked = new Map();
  for (const input of form.querySelectorAll('input[type="radio"]')) {
    const name = String(input.name || "");
    if (input.checked && name.startsWith("side:")) picked.set(name.slice(5), input.value);
  }
  const left = anchor ? [anchor] : [];
  const right = [];
  for (const member of members) {
    if (!anchor || member.hash === anchor.hash) continue;
    (picked.get(member.hash) === "split" ? right : left).push(member);
  }
  return { left, right };
}

// 저장 직전에 **무엇과 무엇이 갈라지는지** 그대로 적는다. 이 화면이 없어서
// 사유와 실제 판정이 어긋난 기록이 남았다(위 주석의 2026-08-16 건).
function groupSplitPreview(left, right) {
  if (!right.length) {
    return `<p class="admin-axis-reach">아직 아무것도 갈라지지 않습니다 —
      '다른 사건' 쪽에 기사를 한 건 이상 세우세요.</p>`;
  }
  const pairs = [];
  for (const one of left) {
    for (const other of right) pairs.push([one, other]);
  }
  const visible = pairs.slice(0, 8);
  const rest = pairs.length - visible.length;
  const shown = visible.map(([one, other]) =>
    `<li>${esc(one.title || one.hash)} <b>↔</b> ${esc(other.title || other.hash)}</li>`).join("");
  return `<p class="admin-split-scale"><strong>${left.length}건</strong>
      <span>↕</span> <strong>${right.length}건</strong>
      <small>${esc(left[0]?.title || "")} 쪽 ↔ ${esc(right[0]?.title || "")} 쪽</small></p>
    <p class="admin-form-title">저장하면 다음 ${pairs.length}쌍을 '다른 사건'으로 못 박습니다</p>
    <ul class="admin-titles">${shown}${rest ? `<li>… 외 ${rest}쌍</li>` : ""}</ul>`;
}

// 축 후보는 사건군이 바뀔 때마다 다시 뽑는다 — 나눈 결과에서 나오는 것이지
// 미리 정해 둘 수 있는 것이 아니다. 이미 고른 낱말은 살아남으면 그대로 둔다.
function axisChips(side, words, checked) {
  if (!words.length) return "<small>제안 없음</small>";
  const keep = new Set(checked);
  const any = words.some(word => keep.has(word));
  return words.map((word, index) =>
    `<label class="admin-chip-toggle"><input type="checkbox" name="${esc(side)}"
      value="${esc(word)}"${(any ? keep.has(word) : index === 0) ? " checked" : ""}> ${
      esc(word)}</label>`).join("");
}

function updateGroupSplit(form) {
  const { left, right } = groupSides(form);
  const preview = form.querySelector('[data-role="preview"]');
  if (preview) preview.innerHTML = groupSplitPreview(left, right);
  const picked = side => [...form.querySelectorAll(`input[name="${side}"]:checked`)]
    .map(input => input.value);
  const suggestion = suggestAxis(left.map(m => m.title), right.map(m => m.title));
  for (const [side, words] of [["left", suggestion.left], ["right", suggestion.right]]) {
    const box = form.querySelector(`[data-role="axis-${side}"]`);
    if (box) box.innerHTML = axisChips(side, right.length ? words : [], picked(side));
  }
  const save = form.querySelector('[data-role="save"]');
  if (save) save.disabled = !right.length;
  updateReach(form);
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
        <thead><tr><th class="num">점수</th><th>한쪽</th><th>다른 쪽</th><th class="num">제목 유사</th><th class="num">공통태그</th><th>차단</th><th></th></tr></thead>
        <tbody>${borderline.map(row => {
          const joined = entriesOf("issue_join").some(entry =>
            [entry.left_hash, entry.right_hash].sort().join("|")
              === [row.left_hash, row.right_hash].sort().join("|"));
          return `<tr>
          <td class="num">${ratio(row.score)}</td>
          <td><strong>${esc(row.left_title)}</strong><small>${esc(dateLabel(row.left_date))}</small></td>
          <td><strong>${esc(row.right_title)}</strong><small>${esc(dateLabel(row.right_date))}</small></td>
          <td class="num">${ratio(row.diagnostics?.title_ratio)}</td>
          <td class="num">${row.diagnostics?.tag_shared ?? "—"}</td>
          <td>${esc((row.diagnostics?.blocked_by || []).join(" · ") || "—")}</td>
          <td>${!row.left_hash || !row.right_hash ? "—"
            : joined ? '<span class="admin-badge">붙임</span>'
            : `<button class="admin-mini" data-act="issue-join"
                 data-left="${esc(row.left_hash)}" data-right="${esc(row.right_hash)}"
                 data-left-title="${esc(row.left_title)}" data-right-title="${esc(row.right_title)}">붙이기</button>`}</td>
        </tr>`; }).join("")}</tbody>
      </table></div>`
    : '<div class="empty-state"><strong>경계선 후보가 없습니다</strong><p>문턱 아래 구간에 남은 쌍이 없습니다.</p></div>';
}

// ── 수집 설정 ──────────────────────────────────────────────────────────────

// 지울 수 있는 칩. 기본 파일에서 온 말과 콘솔에서 더한 말을 구분해 표시한다 —
// 어느 쪽이든 지울 수 있지만, 무엇을 되돌리는 것인지는 알고 눌러야 한다.
// 칩 한 알의 네 가지 처지. 기본 파일에서 온 것 / 콘솔이 더해 이미 수집에 나가는 것
// / 방금 더해 아직 안 나가는 것 / 빼기로 했는데 아직 안 빠진 것.
const CHIP_STATE = {
  base: { cls: "", note: "" },
  added: { cls: "added", note: "" },
  pending: { cls: "pending", note: "다음 수집부터" },
  removing: { cls: "removing", note: "삭제 대기" },
};

// 화면에 실제로 설 칩 목록. **판정을 얹어서** 만든다.
//
// 예전에는 config 스냅샷(`group.keywords`)만 그렸다. 그런데 그 스냅샷은 다음
// 수집이 돌아 admin_overrides.json 이 커밋될 때까지 갱신되지 않는다 — 그래서
// 방금 추가한 키워드가 '내 판정'에는 뜨는데 정작 키워드 칸에는 없었고, 관리자는
// 추가가 실패한 줄 알고 같은 말을 다시 넣었다(사용자 지적). 판정은 이미 손에
// 있으므로 여기서 겹쳐 그리면 된다. 색으로 처지를 갈라 두면 '아직 안 나간다'는
// 사실도 같이 읽힌다.
function chipStates(values, { group, addKind, removeKind, baseValues }) {
  const mine = entry => (entry.group || "") === (group || "");
  const pendingRemoved = new Set(entriesOf(removeKind).filter(mine).map(entry => entry.value));
  const base = new Set(baseValues || values);
  const live = values || [];
  const rows = live.map(value => ({
    value,
    state: pendingRemoved.has(value) ? "removing" : (base.has(value) ? "base" : "added"),
  }));
  // 스냅샷에 아직 없는 추가는 뒤에 세운다. 사이에 끼워 넣으면 어제 보던 목록의
  // 자리가 매일 흔들린다.
  const known = new Set(live);
  for (const entry of entriesOf(addKind).filter(mine)) {
    if (known.has(entry.value)) continue;
    known.add(entry.value);
    rows.push({ value: entry.value, state: "pending" });
  }
  return rows;
}

function editableChips(values, options) {
  const { group, addKind, removeKind } = options;
  const rows = chipStates(values, options);
  if (!rows.length) return '<span class="admin-chip-empty">아직 없습니다</span>';
  return rows.map(({ value, state }) => {
    const meta = CHIP_STATE[state] || CHIP_STATE.base;
    // 빼기로 한 칩의 버튼은 '한 번 더 빼기'가 아니라 되돌리기다. 같은 ×를 두면
    // 삭제 판정이 두 벌 쌓인다.
    const undo = state === "removing";
    return `<span class="admin-chip ${meta.cls}">
      ${esc(value)}${meta.note ? `<small class="admin-chip-note">${esc(meta.note)}</small>` : ""}
      <button class="admin-chip-x" data-act="${undo ? "chip-restore" : "chip-remove"}"
        data-kind="${esc(removeKind)}"
        data-add-kind="${esc(addKind)}" data-group="${esc(group || "")}" data-value="${esc(value)}"
        aria-label="${esc(value)} ${undo ? "삭제 취소" : "삭제"}">${undo ? "↺" : "×"}</button></span>`;
  }).join("");
}

function addForm(kind, group, placeholder) {
  return `<form class="admin-inline-form" data-act="value-add" data-kind="${esc(kind)}"
      data-group="${esc(group || "")}">
    <input name="value" type="text" maxlength="200" required placeholder="${esc(placeholder)}">
    <button type="submit" class="admin-mini primary">추가</button>
  </form>`;
}

// 화면에 실제로 서는 칩 수. 머리줄 숫자가 칩 개수와 다르면 둘 중 어느 쪽이 맞는
// 말인지 알 길이 없다 — 대기 중인 추가는 세고, 대기 중인 삭제는 뺀다.
function chipCount(values, options) {
  return chipStates(values, options).filter(row => row.state !== "removing").length;
}

function renderKeywords() {
  const keywords = state.config?.keywords;
  if (!keywords) return;
  const totals = keywords.totals || {};
  const groups = keywords.groups || [];
  const optionsFor = (group, axis) => ({
    group: group.name,
    addKind: `${axis}_add`,
    removeKind: `${axis}_remove`,
    baseValues: axis === "keyword" ? group.base_keywords
      : axis === "anchor" ? group.base_anchors : group.negative_list,
  });
  const keywordTotal = groups.reduce(
    (sum, group) => sum + chipCount(group.keywords, optionsFor(group, "keyword")), 0);
  const anchorTotal = groups.reduce(
    (sum, group) => sum + chipCount(group.anchors, optionsFor(group, "anchor")), 0);
  // 스냅샷과 다르면 그 차이를 말한다. 숫자만 조용히 바뀌면 관리자는 자기가 더한
  // 것이 반영된 것인지 집계가 틀린 것인지 구분하지 못한다.
  const pendingNote = count => count > 0 ? ` · 대기 ${count}개` : "";
  document.getElementById("keywordStats").innerHTML = [
    stat("키워드 그룹", `${totals.groups || 0}개`, "검색 쿼리 묶음"),
    stat("검색 키워드", `${keywordTotal}개`,
      `그대로 검색에 나갑니다${pendingNote(keywordTotal - (totals.keywords || 0))}`),
    stat("앵커", `${anchorTotal}개`,
      `원자력 문맥 확인용${pendingNote(anchorTotal - (totals.anchors || 0))}`),
    stat("학습 쿼리", `${state.config?.search?.learned_query_count ?? 0}개`, "discovery 가 스스로 늘린 것"),
  ].join("");
  document.getElementById("keywordGroups").innerHTML = groups.map(group => {
    const keywordOptions = optionsFor(group, "keyword");
    const negativeOptions = optionsFor(group, "negative");
    const anchorOptions = optionsFor(group, "anchor");
    return `<article class="admin-card">
      <div class="admin-card-head">
        <div><h3>${esc(group.name)}</h3></div>
        <p class="admin-card-scale">키워드 ${chipCount(group.keywords, keywordOptions)}개<small>앵커 ${chipCount(group.anchors, anchorOptions)}개</small></p>
      </div>
      <div class="admin-chip-block">
        <h4>검색 키워드</h4>
        <div class="topic-row">${editableChips(group.keywords, keywordOptions)}</div>
        ${addForm("keyword_add", group.name, "새 검색 키워드")}
      </div>
      <div class="admin-chip-block">
        <h4>제외어 <small>제목에 걸리면 버립니다 (쿼리에는 붙지 않습니다)</small></h4>
        <div class="topic-row">${editableChips(group.negative_list || [], negativeOptions)}</div>
        ${addForm("negative_add", group.name, "새 제외어 (예: 공모주)")}
      </div>
      <details class="admin-evidence">
        <summary>앵커 ${chipCount(group.anchors, anchorOptions)}개 — 결과가 원자력 문맥인지 확인하는 말</summary>
        <div class="topic-row">${editableChips(group.anchors, anchorOptions)}</div>
        ${addForm("anchor_add", group.name, "새 앵커")}
      </details>
    </article>`;
  }).join("");

  renderLearnedTerms();

  const anti = state.config?.anti_keywords || [];
  document.getElementById("antiKeywords").innerHTML = `<article class="admin-card">
    <div class="admin-card-head">
      <div><h3>공통 제외어</h3><p class="data-note">news_bot.ANTI_KEYWORDS · 모든 그룹에 걸립니다</p></div>
      <p class="admin-card-scale">${anti.length}개</p>
    </div>
    <div class="topic-row">${editableChips(anti, {
      addKind: "anti_add", removeKind: "anti_remove",
      baseValues: anti.filter(value => !entriesOf("anti_add").some(entry => entry.value === value)),
    })}</div>
    ${addForm("anti_add", "", "새 공통 제외어 (예: 체육대회)")}
  </article>`;
}

// ── 학습된 검색어 ───────────────────────────────────────────────────────────
//
// 고정 키워드와 **다른 층**이라 같은 칸에 섞지 않는다. 고정 키워드는 사람이
// 정하고 영원히 나가지만, 이 말들은 기사에서 자동으로 생겨 24~72시간만 살고
// 성과가 없으면 스스로 사라진다. 한 목록으로 그리면 관리자는 자기가 지운 적
// 없는 키워드가 며칠 뒤 사라져 있는 것을 보게 된다.
//
// 그래서 화면이 반드시 말해야 하는 것이 셋이다: **왜 생겼나**(근거 기사),
// **언제 사라지나**(남은 시간), **뭘 물어 왔나**(성과). 이 셋이 없으면 목록은
// 판단할 수 없는 목록이고, 판단할 수 없는 목록은 아무도 안 본다.
function learnedTermRow(row) {
  const life = row.pinned
    ? '<span class="admin-badge">고정</span>'
    : (typeof row.expires_in_hours === "number"
      ? `${Math.max(0, Math.round(row.expires_in_hours))}시간`
      // 승격 후보는 만료로 지우지 않는다 — 사람이 결정할 때까지 붙잡아 둔다.
      : (row.status === "promote_candidate" ? "판단 대기" : "—"));
  const evidence = (row.evidence || []).map(item =>
    `<li>${esc(item.title || "제목 없음")}<small>${esc(item.domain || "매체 미상")}</small></li>`).join("");
  return `<tr>
    <td><strong>${esc(row.term)}</strong><small>${esc(row.query)}</small></td>
    <td><span class="admin-badge">${esc(LEARNED_TYPE_LABEL[row.type] || row.type || "—")}</span></td>
    <td><span class="admin-badge${row.status === "promote_candidate" ? " warn" : ""}">${
      esc(row.status_label || "추적 중")}</span>${
      row.origin === "console" ? '<small>직접 추가</small>' : ""}</td>
    <td class="num">${esc(life)}</td>
    <td class="num">${row.new_articles}건<small>검색 ${row.queries_run}회 · ${row.yield_days}일 성과</small></td>
    <td>${evidence ? `<details class="admin-evidence"><summary>근거 ${
      (row.evidence || []).length}건</summary><ul class="admin-titles">${evidence}</ul></details>` : "—"}</td>
    <td>
      <div class="admin-form-buttons">
        ${row.pinned ? "" : `<button class="admin-mini" data-act="learned-keep"
          data-value="${esc(row.term)}">계속 추적</button>`}
        <button class="admin-mini" data-act="learned-promote"
          data-id="${esc(row.id)}" data-query="${esc(row.query)}">고정 키워드로</button>
        <button class="admin-mini danger" data-act="chip-remove"
          data-kind="learned_term_remove" data-add-kind="learned_term_add"
          data-group="" data-value="${esc(row.term)}">빼기</button>
      </div>
      <div class="admin-form-slot" data-slot="learned-${esc(row.id)}"></div>
    </td>
  </tr>`;
}

// 승격은 새 판정 종류가 아니라 `keyword_add` 다 — "이 말을 고정 목록에 넣는다"와
// 같은 판단이기 때문이다. 그룹을 고르게 하는 이유는 그룹이 앵커·제외어를 함께
// 갖는 한 벌이라, 어느 벌에 넣느냐가 곧 어떤 잡음 필터를 태우느냐이기 때문이다.
function learnedPromoteForm(query) {
  const groups = (state.config?.keywords?.groups || []).map(group => group.name);
  return `<form class="admin-form" data-act="learned-promote-save">
    <input type="hidden" name="value" value="${esc(query)}">
    <p class="admin-hint">고정 키워드로 올리면 <strong>매 수집마다 검색에 나갑니다</strong>
      — 임시 검색어의 예산 상한과 자동 폐기가 더 이상 적용되지 않습니다.</p>
    <label class="admin-field"><span>넣을 그룹</span>
      <select name="group">${groups.map(name =>
        `<option value="${esc(name)}">${esc(name)}</option>`).join("")}</select></label>
    <label class="admin-field"><span>사유</span>
      <input name="note" type="text" maxlength="200" placeholder="예: 3일 연속 신규 기사"></label>
    <div class="admin-form-buttons">
      <button type="submit" class="admin-mini primary">고정 키워드로 올리기</button>
      <button type="button" class="admin-mini" data-act="form-close">취소</button>
    </div>
  </form>`;
}

function renderLearnedTerms() {
  const box = document.getElementById("learnedTerms");
  if (!box) return;
  const search = state.config?.search || {};
  const rows = search.learned_terms || [];
  const stats = search.learned_stats || {};
  const retired = search.learned_retired || [];
  const promote = rows.filter(row => row.status === "promote_candidate");

  const drafts = promote.filter(row => row.registry_draft).map(row =>
    `<li><strong>${esc(row.term)}</strong>
      <code>${esc(row.registry_draft)}</code></li>`).join("");

  box.innerHTML = `<article class="admin-card">
    <div class="admin-card-head">
      <div><h3>학습된 검색어</h3>
        <p class="data-note">기사에서 처음 본 이름으로 자동 생성 · 24~72시간만 삽니다</p></div>
      <p class="admin-card-scale">${rows.length}개<small>정원 ${stats.capacity ?? "—"}</small></p>
    </div>
    ${search.learned_error
      ? `<div class="error-state"><strong>학습된 검색어를 읽지 못했습니다</strong>
          <p>${esc(search.learned_error)}</p></div>`
      : ""}
    <div class="admin-stats">
      ${stat("오늘 쓴 질의", `${stats.spent_today ?? 0}/${stats.daily_budget ?? 0}`,
             "고정 키워드·후속 발굴 예산과 별도")}
      ${stat("오늘 새로 만든 말", `${stats.minted_today ?? 0}/${stats.mint_cap ?? 0}`, "하루 상한")}
      ${stat("승격 후보", `${stats.promote_candidates ?? 0}개`, "성과가 이어진 말")}
      ${stat("폐기됨", `${stats.retired ?? 0}개`, "성과 없거나 기간 만료")}
    </div>
    <div class="admin-chip-block">
      <h4>지금 쫓고 있는 말</h4>
      <div class="topic-row">${editableChips(rows.map(row => row.term), {
        addKind: "learned_term_add", removeKind: "learned_term_remove",
        baseValues: rows.filter(row => row.origin !== "console").map(row => row.term),
      })}</div>
      ${addForm("learned_term_add", "", "임시 검색어 직접 추가 (예: 아보이티즈 파워)")}
    </div>
    ${rows.length ? `<div class="admin-table-scroll"><table class="admin-table">
      <thead><tr><th>검색어</th><th>유형</th><th>상태</th><th class="num">남은 기간</th>
        <th class="num">성과</th><th>왜 생겼나</th><th></th></tr></thead>
      <tbody>${rows.map(learnedTermRow).join("")}</tbody>
    </table></div>` : `<div class="empty-state"><strong>지금 쫓고 있는 말이 없습니다</strong>
      <p>최근 기사에 사전에 없는 이름이 충분한 무게로 등장하면 여기 자동으로 쌓입니다.
      직접 넣은 말은 만료되지 않습니다 — 넣은 사람이 뺍니다.</p></div>`}
    ${drafts ? `<details class="admin-evidence"><summary>entity_registry 승격 초안 ${
      promote.length}건 — 저장소에 넣을 항목</summary>
      <p class="admin-hint">별칭과 <code>match_policy</code> 는 사람이 정해야 해서 화면에서
        등재하지 않습니다. 잘못 붙은 엔티티는 그 엔티티 페이지 전체의 신뢰를 깎습니다 —
        아래 초안을 <code>entity_registry.json</code> 에 넣고 별칭을 좁히세요.</p>
      <ul class="admin-titles">${drafts}</ul></details>` : ""}
    ${retired.length ? `<details class="admin-evidence"><summary>최근 폐기 ${
      retired.length}건 — 왜 사라졌나</summary><ul class="admin-titles">${
      retired.map(row => `<li>${esc(row.term)}<small>${esc(row.reason)} · 신규 ${
        row.new_articles}건 · ${esc(String(row.retired_at || "").slice(0, 10))}</small></li>`)
        .join("")}</ul></details>` : ""}
  </article>`;
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
  const stopCell = (kind) => row => {
    const target = row.url || row.name;
    return `<button class="admin-mini danger" data-act="feed-disable" data-kind="${esc(kind)}"
      data-target="${esc(target)}" data-label="${esc(row.name || row.publisher || target)}">중지</button>`;
  };
  document.getElementById("feedTables").innerHTML = [
    feeds.error
      ? `<div class="error-state"><strong>수집원 목록을 읽지 못했습니다</strong><p>${esc(feeds.error)}</p></div>`
      : "",
    feedTable("해외·전문지 RSS", "news_bot.RSS_SOURCES + 콘솔 추가분", rss, [
      { label: "이름", cell: row => esc(row.name) },
      { label: "도메인", cell: linkCell },
      // 직접 피드와 Google News 우회는 신뢰도가 다르다 — 우회는 색인 지연과
      // 관련도순 정렬을 타므로 같은 것으로 읽히면 안 된다.
      { label: "경로", cell: row => row.via === "google_news"
        ? '<span class="admin-badge warn">Google News 우회</span>'
        : '<span class="admin-badge">직접 피드</span>' },
      { label: "키워드 필수", cell: row => row.require_keywords ? "예" : "—" },
      { label: "", cell: stopCell("feed_disable") },
    ]),
    feedAddCard(),
    feedTable("국내 기관 직접 수집", "news_bot.OFFICIAL_DIRECT_SOURCES · 보도자료 원문", official, [
      { label: "기관", cell: row => esc(row.publisher || row.name) },
      { label: "게시판", cell: row => esc(row.name) },
      { label: "도메인", cell: linkCell },
      { label: "수집 방식", cell: row => `<code>${esc(row.kind)}</code>` },
      { label: "", cell: stopCell("official_disable") },
    ]),
    // 기관 직접 수집은 게시판마다 전용 파서(kind)가 코드에 있어야 읽힌다. 화면에서
    // 주소만 넣게 하면 파서 없는 항목이 매번 조용히 0건을 내고, 그건 '그 기관이
    // 조용한 날'과 구분되지 않는다 — 그래서 추가는 저장소 작업으로 남긴다.
    official.length ? `<p class="admin-hint">기관 직접 수집은 게시판마다 전용 파서가 필요해
      화면에서 추가할 수 없습니다. 새 기관은 <code>news_bot.OFFICIAL_DIRECT_SOURCES</code>
      에 파서와 함께 넣습니다.</p>` : "",
    (state.config?.publications?.orgs || []).length
      ? `<section class="admin-card">
          <div class="admin-card-head"><div><h3>발간물 기관</h3><p class="data-note">pubs_fetch · 보고서·분석 자료</p></div>
            <p class="admin-card-scale">${state.config.publications.orgs.length}곳</p></div>
          <div class="topic-row">${chips(state.config.publications.orgs)}</div>
        </section>`
      : "",
  ].filter(Boolean).join("");
}

// 새 수집원. 전용 RSS 가 없는 매체가 절반이라(Reuters·FT·Les Échos…) Google News
// 우회 주소를 손으로 조립하게 두지 않는다 — when: 연산자를 빼먹으면 '관련도순'
// 정렬이 몇 주 지난 기사를 물어 오고(2026-07-10 실측: 100건 중 95건), 그 실패는
// 수집이 도는 것처럼 보이면서 조용히 신선도를 갉는다.
function feedAddCard() {
  return `<section class="admin-card">
    <div class="admin-card-head"><div><h3>수집원 추가</h3>
      <p class="data-note">다음 수집부터 걷습니다</p></div></div>
    <form class="admin-form" data-act="feed-add">
      <div class="admin-field-row">
        <label class="admin-field"><span>방식</span>
          <select name="mode">
            <option value="direct">직접 RSS 주소</option>
            <option value="google">Google News 우회 (전용 피드가 없는 매체)</option>
          </select></label>
        <label class="admin-field"><span>이름</span>
          <input name="name" type="text" maxlength="120" required placeholder="예: Nikkei 원자력"></label>
      </div>
      <div class="admin-field-row" data-role="direct-fields">
        <label class="admin-field grow"><span>RSS 주소</span>
          <input name="url" type="url" maxlength="400" placeholder="https://example.com/feed"></label>
      </div>
      <div class="admin-field-row" data-role="google-fields" hidden>
        <label class="admin-field"><span>도메인</span>
          <input name="site" type="text" maxlength="120" placeholder="nikkei.com"></label>
        <label class="admin-field grow"><span>검색어</span>
          <input name="terms" type="text" maxlength="200" placeholder="nuclear OR reactor OR SMR"></label>
        <label class="admin-field"><span>기간</span>
          <select name="window"><option value="1d">1일</option><option value="2d" selected>2일</option><option value="3d">3일</option></select></label>
        <label class="admin-field"><span>언어</span>
          <select name="lang">
            <option value="en">영어</option><option value="ko">한국어</option>
            <option value="fr">프랑스어</option><option value="ja">일본어</option>
          </select></label>
      </div>
      <label class="admin-check"><input type="checkbox" name="require_keywords">
        제목에 원자력 낱말이 있는 것만 받기 <small>(섹션 피드처럼 비원자력이 섞이는 곳)</small></label>
      <div class="admin-form-buttons"><button type="submit" class="admin-mini primary">수집원 추가</button></div>
    </form>
  </section>`;
}

function renderTiers() {
  const tiers = state.config?.source_tiers;
  if (!tiers) return;
  const rows = tiers.rows || [];
  document.getElementById("tierTable").innerHTML = `
    <p class="data-note">tier1 선정 가산 +${esc(tiers.tier1_bonus ?? "—")} · tier2 +${esc(tiers.tier2_bonus ?? "—")} · tier3 가산 없음.
      등급은 선정 점수를, 근거 역할은 화면의 검증 배지를 정합니다.</p>
    <div class="admin-table-scroll"><table class="admin-table">
      <thead><tr><th>등급</th><th>이름</th><th>도메인</th><th>매체 성격</th><th>근거 역할</th><th></th></tr></thead>
      <tbody>${rows.map(row => `<tr>
        <td><span class="admin-badge${row.tier === 1 ? "" : " muted"}">tier ${esc(row.tier)}</span></td>
        <td>${esc(row.name)}</td>
        <td>${esc(row.domain)}</td>
        <td>${esc(labelOf(SOURCE_TYPES, row.source_type))}</td>
        <td>${esc(labelOf(EVIDENCE_ROLES, row.evidence_role))}</td>
        <td><button class="admin-mini" data-act="tier-open" data-domain="${esc(row.domain)}">수정</button></td>
      </tr>`).join("")}</tbody>
    </table></div>
    <div class="admin-form-slot" data-slot="tier"></div>
    <section class="admin-card">
      <div class="admin-card-head"><div><h3>출처 등급 추가</h3>
        <p class="data-note">목록에 없는 매체를 등급에 올립니다</p></div></div>
      ${tierForm({})}
    </section>`;
}

function labelOf(pairs, value) {
  const found = pairs.find(([key]) => key === value);
  return found ? found[1] : (value || "미상");
}

function tierForm(row) {
  const option = (pairs, current) => pairs.map(([key, label]) =>
    `<option value="${esc(key)}"${key === current ? " selected" : ""}>${esc(label)}</option>`).join("");
  const editing = Boolean(row.domain);
  return `<form class="admin-form" data-act="tier-save">
    ${editing ? `<p class="admin-form-title">${esc(row.name || row.domain)} 등급 수정</p>` : ""}
    <div class="admin-field-row">
      <label class="admin-field"><span>도메인</span>
        <input name="domain" type="text" maxlength="200" required value="${esc(row.domain || "")}"
          ${editing ? "readonly" : 'placeholder="world-nuclear-news.org"'}></label>
      <label class="admin-field"><span>이름</span>
        <input name="name" type="text" maxlength="120" value="${esc(row.name || "")}" placeholder="매체 이름"></label>
      <label class="admin-field"><span>등급</span>
        <select name="tier">
          <option value="1"${row.tier === 1 ? " selected" : ""}>tier 1 (+가산 큼)</option>
          <option value="2"${row.tier === 2 ? " selected" : ""}>tier 2</option>
          <option value="3"${row.tier === 3 || !row.tier ? " selected" : ""}>tier 3 (가산 없음)</option>
        </select></label>
    </div>
    <div class="admin-field-row">
      <label class="admin-field"><span>매체 성격</span>
        <select name="source_type">${option(SOURCE_TYPES, row.source_type || "unknown")}</select></label>
      <label class="admin-field"><span>근거 역할</span>
        <select name="evidence_role">${option(EVIDENCE_ROLES, row.evidence_role || "unknown")}</select></label>
      <label class="admin-field grow"><span>별칭 <small>쉼표로 구분 · 제목·메타에서 이 매체를 찾는 말</small></span>
        <input name="aliases" type="text" maxlength="300" value="${esc((row.aliases || []).join(", "))}"></label>
    </div>
    <label class="admin-field"><span>기록 사유</span>
      <input name="note" type="text" maxlength="300" placeholder="예: 원발표처가 아니라 받아쓴 매체"></label>
    <div class="admin-form-buttons">
      <button type="submit" class="admin-mini primary">저장</button>
      ${editing ? `<button type="button" class="admin-mini danger" data-act="tier-remove"
        data-domain="${esc(row.domain)}">등급에서 빼기</button>
        <button type="button" class="admin-mini" data-act="form-close">취소</button>` : ""}
    </div>
  </form>`;
}

// ── 내 판정 ────────────────────────────────────────────────────────────────

function renderJudgments() {
  const rules = state.merges?.judgments?.rules || [];
  const saved = entriesOf("learned_rule");
  const box = document.getElementById("learnedRules");
  if (box) {
    // 아직 빌드를 안 탄 규칙은 넓이(reach)를 모른다 — 그 사실을 숫자 대신 적는다.
    const live = new Map(rules.map(rule => [rule.id, rule]));
    box.innerHTML = saved.length ? saved.map(entry => {
      const measured = live.get(entry.id);
      const wide = measured && (measured.left_only > 25 || measured.right_only > 25);
      return `<article class="admin-card">
        <div class="admin-card-head">
          <div>
            <p class="admin-kicker">${pendingBadge(entry)}
              ${entry.enabled === false ? '<span class="admin-badge muted">꺼둠</span>' : ""}
              <span>${esc(String(entry.created_at || "").slice(0, 10))}</span></p>
            <h3>${esc(entry.label || "판별축")}</h3>
          </div>
          <p class="admin-card-scale">${measured
            ? `${measured.left_only} ↔ ${measured.right_only}<small>최근 30일 각 축에만 걸린 기사</small>`
            : "—<small>다음 빌드에서 측정</small>"}</p>
        </div>
        <div class="admin-axis-view">
          <div><span>한쪽</span><div class="topic-row">${chips(entry.left_terms)}</div></div>
          <div><span>다른 쪽</span><div class="topic-row">${chips(entry.right_terms)}</div></div>
        </div>
        ${entry.note ? `<p class="admin-reason"><strong>기록 사유</strong>${esc(entry.note)}</p>` : ""}
        ${wide ? `<p class="admin-reason"><strong>주의</strong>축이 넓습니다 — 이 규칙은 서로
          무관한 사건까지 갈라 놓을 수 있습니다. 잘못 배웠다고 판단되면 지우세요.</p>` : ""}
        <div class="admin-form-buttons">
          <button class="admin-mini" data-act="entry-toggle" data-id="${esc(entry.id)}"
            data-enabled="${entry.enabled === false ? "true" : "false"}">${
            entry.enabled === false ? "다시 켜기" : "잠시 끄기"}</button>
          <button class="admin-mini danger" data-act="entry-delete" data-id="${esc(entry.id)}"
            data-label="${esc(entry.label || "판별축")}">지우기</button>
        </div>
      </article>`;
    }).join("") : `<div class="empty-state"><strong>학습된 판별축이 없습니다</strong>
      <p>병합 진단에서 잘못 묶인 기사를 떼어낼 때, 두 기사를 가르는 낱말을 함께 저장하면
      여기 쌓입니다. 그 뒤로는 같은 축을 가진 <strong>새 기사</strong>도 서로 접히지 않습니다.</p></div>`;
  }

  const entries = state.overrides.entries || [];
  // 개수는 템플릿 밖에서 센다. 안에서 세면 `entry.kind` 가 HTML 보간 안에 서서
  // 이스케이프 검사(test_the_console_escapes_everything_it_renders)에 걸린다 —
  // 값은 숫자라 안전하지만, 규칙에 예외를 파는 것보다 계산을 밖으로 빼는 편이 낫다.
  const countByKind = new Map();
  for (const entry of entries) {
    countByKind.set(entry.kind, (countByKind.get(entry.kind) || 0) + 1);
  }
  document.getElementById("entryFilters").innerHTML = [
    `<button class="admin-filter${state.entryFilter === "all" ? " active" : ""}"
      data-act="entry-filter" data-kind="all">전체 ${entries.length}</button>`,
    ...[...countByKind].map(([kind, count]) =>
      `<button class="admin-filter${state.entryFilter === kind ? " active" : ""}"
      data-act="entry-filter" data-kind="${esc(kind)}">${esc(KIND_LABEL[kind] || kind)} ${count}</button>`),
  ].join("");

  const shown = state.entryFilter === "all"
    ? entries : entries.filter(entry => entry.kind === state.entryFilter);
  document.getElementById("entryList").innerHTML = shown.length
    ? `<div class="admin-table-scroll"><table class="admin-table">
        <thead><tr><th>종류</th><th>내용</th><th>사유</th><th>기록</th><th>상태</th><th></th></tr></thead>
        <tbody>${shown.map(entry => `<tr>
          <td><span class="admin-badge">${esc(KIND_LABEL[entry.kind] || entry.kind)}</span></td>
          <td>${esc(entrySubject(entry))}</td>
          <td>${esc(entry.note || "—")}</td>
          <td>${esc(String(entry.created_at || "").slice(0, 10))}</td>
          <td>${pendingBadge(entry)}</td>
          <td><button class="admin-mini danger" data-act="entry-delete" data-id="${esc(entry.id)}"
            data-label="${esc(entrySubject(entry))}">지우기</button></td>
        </tr>`).join("")}</tbody>
      </table></div>`
    : `<div class="empty-state"><strong>기록된 판정이 없습니다</strong>
       <p>병합 진단에서 기사를 떼어내거나 수집 설정을 고치면 여기 한 줄씩 쌓입니다.
       지우면 그 판단만 사라지고 기본 설정으로 정확히 돌아갑니다.</p></div>`;
}

function entrySubject(entry) {
  if (entry.kind === "learned_rule") return entry.label || "판별축";
  if (entry.kind === "issue_group_split") {
    // 이 줄이 판정을 되짚는 유일한 단서다. 건수만 적으면 어느 판정인지 모르고,
    // 제목을 다 적으면 표가 무너진다 — 양쪽 첫 제목과 건수를 함께 적는다.
    const side = (titles, hashes) => {
      const count = (hashes || []).length;
      const head = (titles || [])[0] || (hashes || [])[0] || "제목 없음";
      return count > 1 ? `${head} 외 ${count - 1}건` : head;
    };
    return `${side(entry.left_titles, entry.left_hashes)} ↔ ${
      side(entry.right_titles, entry.right_hashes)}`;
  }
  if (entry.left_title || entry.right_title) {
    return `${entry.left_title || entry.left_hash} ↔ ${entry.right_title || entry.right_hash}`;
  }
  if (entry.left_hash) return `${entry.left_hash} ↔ ${entry.right_hash}`;
  if (entry.group) return `${entry.group} · ${entry.value}`;
  if (entry.domain) return entry.domain;
  if (entry.url) return entry.name ? `${entry.name} (${entry.url})` : entry.url;
  if (entry.target) return entry.label || entry.target;
  return entry.value || "—";
}

// ── 동작 ───────────────────────────────────────────────────────────────────

function closeForms() {
  for (const slot of document.querySelectorAll(".admin-form-slot")) slot.innerHTML = "";
}

// 선택자 문자열에 hash 를 끼워 넣지 않는다. 값이 따옴표를 물면 선택자가 깨지고,
// 증상은 '버튼을 눌러도 아무 일도 안 일어남'이라 원인을 찾기 어렵다.
function findSlot(name) {
  for (const slot of document.querySelectorAll(".admin-form-slot")) {
    if (slot.dataset.slot === name) return slot;
  }
  return null;
}

async function onClick(event) {
  const trigger = event.target.closest("[data-act]");
  if (!trigger) return;
  const act = trigger.dataset.act;
  const data = trigger.dataset;

  if (act === "form-close") { closeForms(); return; }

  // 칸마다 붙은 [쓰는 법]. 도움말 탭을 열고 해당 항목으로 데려간다 — 탭만 열면
  // 관리자는 열두 항목 중에서 자기가 보던 칸을 다시 찾아야 한다.
  if (act === "help-open") {
    showPanel("help");
    const topic = document.getElementById(data.topic || "");
    topic?.scrollIntoView?.({ block: "start" });
    return;
  }

  if (act === "split-open") {
    closeForms();
    const slot = findSlot(`split-${data.rep}`);
    if (slot) {
      slot.innerHTML = splitForm(data.rep, data.member);
      updateReach(slot.querySelector("form"));
      slot.querySelector("input[name='note']")?.focus();
    }
    return;
  }

  if (act === "tier-open") {
    closeForms();
    const row = (state.config?.source_tiers?.rows || [])
      .find(item => item.domain === data.domain) || {};
    const slot = findSlot("tier");
    if (slot) slot.innerHTML = tierForm(row);
    return;
  }

  if (act === "entry-filter") {
    state.entryFilter = data.kind;
    renderJudgments();
    return;
  }

  if (act === "chip-restore") {
    // 아직 파이프라인에 안 간 삭제를 물린다. 판정 자체를 지우므로 목록에
    // '삭제했다가 되살렸다'는 이력이 남지 않는다.
    const removed = entriesOf(data.kind).find(entry =>
      entry.value === data.value && (entry.group || "") === (data.group || ""));
    if (removed) {
      await submit({ op: "delete", id: removed.id }, `'${data.value}' 삭제를 되돌렸습니다.`);
      return;
    }
    renderAll();
    return;
  }

  if (act === "chip-remove") {
    // 콘솔이 **더한** 말을 지울 때는 반대 항목을 새로 만들지 않고 그 항목 자체를
    // 지운다. 그러지 않으면 '추가'와 '삭제'가 나란히 남아 목록이 자기 이력으로 찬다.
    const added = entriesOf(data.addKind).find(entry =>
      entry.value === data.value && (entry.group || "") === (data.group || ""));
    if (added) {
      await submit({ op: "delete", id: added.id }, `추가했던 '${data.value}' 를 되돌렸습니다.`);
      return;
    }
    await submit({
      op: "add",
      entry: { kind: data.kind, group: data.group, value: data.value, note: "콘솔에서 삭제" },
    }, `'${data.value}' 를 뺐습니다 — 다음 수집부터 적용됩니다.`);
    return;
  }

  if (act === "learned-keep") {
    await submit({
      op: "add",
      entry: { kind: "learned_term_keep", value: data.value, note: "콘솔에서 계속 추적" },
    }, `'${data.value}' 를 붙잡았습니다 — 만료로 사라지지 않습니다.`);
    return;
  }

  if (act === "learned-promote") {
    closeForms();
    const slot = findSlot(`learned-${data.id}`);
    if (slot) slot.innerHTML = learnedPromoteForm(data.query);
    return;
  }

  if (act === "feed-disable") {
    const added = entriesOf("feed_add").find(entry => entry.url === data.target);
    if (added) {
      await submit({ op: "delete", id: added.id }, `추가했던 수집원 '${data.label}' 를 지웠습니다.`);
      return;
    }
    if (!confirm(`'${data.label}' 수집을 중지합니다. 다음 수집부터 이 곳은 걷지 않습니다.`)) return;
    await submit({
      op: "add",
      entry: { kind: data.kind, target: data.target, label: data.label, note: "콘솔에서 중지" },
    }, `'${data.label}' 수집을 중지했습니다.`);
    return;
  }

  if (act === "group-split-open") {
    closeForms();
    const slot = findSlot(`issue-${data.issue}`);
    if (!slot) return;
    slot.innerHTML = groupSplitForm(data.issue, data.anchor || "", data.preset || "manual");
    const form = slot.querySelector("form");
    if (form) updateGroupSplit(form);
    slot.querySelector("input[name='note']")?.focus();
    return;
  }

  if (act === "issue-join") {
    const note = prompt("왜 같은 사건입니까? (기록에 남습니다)");
    if (note === null) return;
    await submit({
      op: "add",
      entry: {
        kind: "issue_join", left_hash: data.left, right_hash: data.right,
        left_title: data.leftTitle, right_title: data.rightTitle, note,
      },
    }, "두 기사를 같은 이슈로 잇습니다 — 다음 빌드부터 붙습니다.");
    return;
  }

  if (act === "tier-remove") {
    if (!confirm(`${data.domain} 을 등급 목록에서 뺍니다. 선정 가산이 사라집니다.`)) return;
    await submit({
      op: "add",
      entry: { kind: "tier_remove", domain: data.domain, note: "콘솔에서 삭제" },
    }, `${data.domain} 을 등급에서 뺐습니다.`);
    return;
  }

  if (act === "entry-delete") {
    if (!confirm(`'${data.label}' 판정을 지웁니다. 이 판단만 사라지고 기본 동작으로 돌아갑니다.`)) return;
    await submit({ op: "delete", id: data.id }, "판정을 지웠습니다.");
    return;
  }

  if (act === "entry-toggle") {
    await submit({ op: "toggle", id: data.id, enabled: data.enabled === "true" },
      data.enabled === "true" ? "다시 켰습니다." : "잠시 껐습니다.");
  }
}

async function onSubmit(event) {
  const form = event.target.closest("form[data-act]");
  if (!form) return;
  event.preventDefault();
  const data = new FormData(form);
  const act = form.dataset.act;

  if (act === "value-add") {
    const value = String(data.get("value") || "").trim();
    if (!value) return;
    // 같은 말을 예전에 지웠던 기록이 있으면 그 삭제를 되돌리는 것이 맞다.
    const removeKind = form.dataset.kind.replace("_add", "_remove");
    const removed = entriesOf(removeKind).find(entry =>
      entry.value === value && (entry.group || "") === (form.dataset.group || ""));
    if (removed) {
      await submit({ op: "delete", id: removed.id }, `'${value}' 삭제를 되돌렸습니다.`);
      form.reset();
      return;
    }
    const ok = await submit({
      op: "add",
      entry: { kind: form.dataset.kind, group: form.dataset.group, value, note: "콘솔에서 추가" },
    }, `'${value}' 를 넣었습니다 — 다음 수집부터 검색에 나갑니다.`);
    if (ok) form.reset();
    return;
  }

  // 승격 = 고정 키워드 추가. 임시 검색어 쪽 항목은 굳이 지우지 않는다 — 같은
  // 질의가 고정 목록에 서면 adaptive 가 중복으로 보고 스스로 만들지 않고,
  // 살아 있던 항목은 만료로 사라진다. 두 번 누르게 하지 않기 위해서다.
  if (act === "learned-promote-save") {
    const value = String(data.get("value") || "").trim();
    const group = String(data.get("group") || "").trim();
    if (!value || !group) {
      toast("올릴 그룹을 고르세요.", "error");
      return;
    }
    const ok = await submit({
      op: "add",
      entry: {
        kind: "keyword_add", group, value,
        note: String(data.get("note") || "").trim() || "학습된 검색어에서 승격",
      },
    }, `'${value}' 를 ${group} 고정 키워드로 올렸습니다.`);
    if (ok) closeForms();
    return;
  }

  if (act === "split-save") {
    const note = String(data.get("note") || "").trim();
    const left = data.getAll("left").map(String);
    const right = data.getAll("right").map(String);
    const learn = data.get("learn") === "on" && left.length && right.length;
    const row = (state.merges?.story?.merges || []).find(item => item.hash === form.dataset.rep);
    const member = ((row || {}).members || [])
      .find(item => item.hash === form.dataset.member) || {};
    const ok = await submit({
      op: "add",
      entry: {
        kind: "story_split",
        left_hash: form.dataset.rep, right_hash: form.dataset.member,
        left_title: row?.title || "", right_title: member.title || "", note,
      },
    }, learn ? "분리했습니다 — 판별축을 이어서 저장합니다." : "분리했습니다.");
    if (ok && learn) {
      await submit({
        op: "add",
        entry: {
          kind: "learned_rule", left_terms: left, right_terms: right, note,
          label: `${left[0]} ↔ ${right[0]}`,
          origin_pair: [form.dataset.rep, form.dataset.member],
        },
      }, "판별축을 학습했습니다 — 새 기사에도 적용됩니다.");
    }
    closeForms();
    return;
  }

  if (act === "group-split-save") {
    const { left, right } = groupSides(form);
    // 화면이 막고 있지만 한 번 더 본다 — 한쪽이 비면 '모두 같은 사건'이라는 뜻이고,
    // 그걸 저장하면 아무것도 안 하는 판정이 목록에만 쌓인다.
    if (!left.length || !right.length) {
      toast("'다른 사건' 쪽에 기사를 한 건 이상 세워야 나눌 수 있습니다.", "error");
      return;
    }
    const note = String(data.get("note") || "").trim();
    const axisLeft = data.getAll("left").map(String);
    const axisRight = data.getAll("right").map(String);
    const learn = data.get("learn") === "on" && axisLeft.length && axisRight.length;
    const pairs = left.length * right.length;
    const ok = await submit({
      op: "add",
      entry: {
        kind: "issue_group_split", issue_id: form.dataset.issue,
        left_hashes: left.map(member => member.hash),
        right_hashes: right.map(member => member.hash),
        left_titles: left.map(member => member.title || ""),
        right_titles: right.map(member => member.title || ""),
        note,
      },
    }, `${left.length}건 ↔ ${right.length}건으로 나눴습니다 — 쌍 ${pairs}개를 못 박습니다.`);
    if (ok && learn) {
      await submit({
        op: "add",
        entry: {
          kind: "learned_rule", left_terms: axisLeft, right_terms: axisRight, note,
          label: `${axisLeft[0]} ↔ ${axisRight[0]}`,
          origin_pair: [left[0].hash, right[0].hash],
        },
      }, "판별축을 학습했습니다 — 새 기사에도 적용됩니다.");
    }
    closeForms();
    return;
  }

  if (act === "feed-add") {
    const mode = String(data.get("mode") || "direct");
    const name = String(data.get("name") || "").trim();
    let url = String(data.get("url") || "").trim();
    let domain = "";
    if (mode === "google") {
      const site = String(data.get("site") || "").trim().replace(/^www\./, "");
      const terms = String(data.get("terms") || "").trim();
      if (!site) { toast("도메인을 입력하세요.", "error"); return; }
      domain = site;
      // when: 을 반드시 붙인다. 없으면 Google News 검색 RSS 는 관련도순이라
      // 몇 주 지난 기사가 대부분이고, 그것들은 수집 창에서 전멸한다.
      const query = `site:${site}${terms ? ` (${terms})` : ""} when:${data.get("window") || "2d"}`;
      const lang = String(data.get("lang") || "en");
      const locale = { en: "hl=en-US&gl=US&ceid=US:en", ko: "hl=ko&gl=KR&ceid=KR:ko",
        fr: "hl=fr&gl=FR&ceid=FR:fr", ja: "hl=ja&gl=JP&ceid=JP:ja" }[lang];
      url = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&${locale}`;
    } else {
      try {
        domain = new URL(url).hostname.replace(/^www\./, "");
      } catch {
        toast("RSS 주소가 올바르지 않습니다.", "error");
        return;
      }
    }
    const ok = await submit({
      op: "add",
      entry: {
        kind: "feed_add", url, name, domain_label: domain,
        require_keywords: data.get("require_keywords") === "on"
          ? ["nuclear", "reactor", "smr", "uranium", "원전", "원자력"] : [],
        note: "콘솔에서 추가",
      },
    }, `'${name}' 을 수집원에 넣었습니다 — 다음 수집부터 걷습니다.`);
    if (ok) form.reset();
    return;
  }

  if (act === "tier-save") {
    const aliases = String(data.get("aliases") || "")
      .split(",").map(value => value.trim()).filter(Boolean);
    await submit({
      op: "add",
      entry: {
        kind: "tier_upsert",
        domain: String(data.get("domain") || "").trim(),
        name: String(data.get("name") || "").trim(),
        tier: Number(data.get("tier")),
        source_type: String(data.get("source_type") || ""),
        evidence_role: String(data.get("evidence_role") || ""),
        aliases,
        note: String(data.get("note") || ""),
      },
    }, "출처 등급을 고쳤습니다 — 다음 수집·빌드부터 적용됩니다.");
    closeForms();
  }
}

// ── 뼈대 ───────────────────────────────────────────────────────────────────

// 도움말도 하나의 화면이다. 별도 페이지로 빼지 않는 이유: 링크로 나가면 보던
// 목록을 잃고 돌아올 때 처음부터 다시 훑게 된다. 탭이면 눌렀다 돌아와도 같은 자리다.
// 내용은 index.html 에 정적으로 있다 — 데이터를 못 읽은 날에도 읽혀야 하므로.
const PANELS = ["merges", "config", "judgments", "help"];

function showPanel(panel) {
  state.panel = PANELS.includes(panel) ? panel : "merges";
  for (const button of document.querySelectorAll("#adminTabs [data-panel]")) {
    const active = button.dataset.panel === state.panel;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  for (const name of PANELS) {
    document.getElementById(`panel-${name}`).hidden = name !== state.panel;
  }
  const url = new URL(location.href);
  if (state.panel === "merges") url.searchParams.delete("panel");
  else url.searchParams.set("panel", state.panel);
  history.replaceState(history.state, "", url);
}

function renderAll() {
  renderStory();
  renderIssues();
  renderKeywords();
  renderFeeds();
  renderTiers();
  renderJudgments();
  renderPendingBanner();
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
  // 판정 목록은 KV 에서 온다(빌드 산출물보다 최신). 못 읽어도 진단 화면은 열어야
  // 하므로 실패를 치명으로 보지 않는다 — 편집만 못 하고 읽기는 그대로 된다.
  const writable = await pullOverrides();

  const totals = state.merges?.story?.totals || {};
  const clusters = state.merges?.issue?.totals?.clusters || 0;
  status.className = "readiness-panel ready";
  status.innerHTML = `<div><strong>병합 ${esc((totals.merge || 0) + (totals.duplicate || 0))}건 · 연결된 이슈 ${esc(clusters)}개</strong>
    <p>같은 날 병합은 기사 ${esc(totals.folded_articles || 0)}건을 접었습니다. 위험한 쪽은 누락이 아니라 오병합입니다.${
      writable ? "" : " <strong>판정 저장은 지금 쓸 수 없습니다</strong> — KV 연결을 확인하세요."}</p></div>`;
  document.getElementById("adminGenerated").textContent =
    `생성 ${String(state.merges?.generated_at || "").slice(0, 16).replace("T", " ")}`;

  renderAll();

  document.getElementById("adminTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-panel]");
    if (button) showPanel(button.dataset.panel);
  });
  document.addEventListener("click", onClick);
  document.addEventListener("submit", onSubmit);
  // 판별축 칩을 누를 때마다 '얼마나 넓은가'를 다시 센다 — 저장한 뒤에 알면 늦다.
  document.addEventListener("change", event => {
    const form = event.target.closest('form[data-act="split-save"]');
    if (form) updateReach(form);
    // 사건군이 바뀌면 갈라질 쌍도 축 후보도 달라진다. 저장 버튼이 무엇을 저장할지는
    // 항상 화면에 적혀 있어야 한다 — 그게 이번 개편의 요지다.
    const group = event.target.closest('form[data-act="group-split-save"]');
    if (group) updateGroupSplit(group);
    const feed = event.target.closest('form[data-act="feed-add"]');
    if (feed && event.target.name === "mode") {
      const google = event.target.value === "google";
      feed.querySelector('[data-role="direct-fields"]').hidden = google;
      feed.querySelector('[data-role="google-fields"]').hidden = !google;
      feed.querySelector('input[name="url"]').required = !google;
    }
  });
  // 도움말 항목 주소(/admin/#help-issue)를 그대로 붙여 쓸 수 있게. 이게 없으면
  // 링크를 받은 사람은 병합 진단 화면에서 아무 일도 안 일어난 것을 보게 된다.
  showPanel(String(location.hash || "").startsWith("#help-")
    ? "help"
    : new URLSearchParams(location.search).get("panel"));
}

start();
