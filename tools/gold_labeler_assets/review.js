"use strict";

const el = (id) => document.getElementById(id);
let data, visibleIds = [], cursor = 0, filterMode = "unlabeled", draft = {}, undoAction = null;
const currentId = () => visibleIds[cursor] || null;
const currentCase = () => data.candidates.find((item) => item.id === currentId());

async function request(path, options = {}) {
  const response = await fetch(path, {cache: "no-store", headers: {"Content-Type": "application/json"}, ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || (payload.errors || []).join(" · ") || `HTTP ${response.status}`);
  return payload;
}

function message(value, error = false) { el("message").textContent = value; el("message").style.color = error ? "#a22d24" : ""; }
function saveState(value, kind = "") { el("save-state").textContent = value; el("save-state").className = `save-state ${kind}`; }
function node(tag, text, className = "") { const result = document.createElement(tag); result.textContent = text; result.className = className; return result; }

function updateCounts(counts) {
  data.counts = counts;
  el("total").textContent = counts.total;
  el("labeled").textContent = counts.labeled;
  el("remaining").textContent = counts.remaining;
  el("provisional-count").textContent = counts.provisional;
  el("hard-count").textContent = counts.hard_cases;
  el("ready").textContent = counts.evaluation_ready ? "YES" : "NO";
  el("counts").textContent = `${Object.entries(counts.verdicts).map(([key, value]) => `${key} ${value}`).join(" · ")} · initial ${counts.initial_review_done}/${counts.initial_review_target} · 보류 ${counts.needs_review}`;
}

function idsForFilter() {
  const ids = data.review_order || data.candidates.map((item) => item.id);
  if (filterMode === "priority") return data.recommended_initial_ids.filter((id) => !data.labels[id]);
  return filterMode === "unlabeled" ? ids.filter((id) => !data.labels[id]) : ids;
}

function applyFilter(mode, preferred = null) {
  filterMode = mode;
  document.querySelectorAll(".filter").forEach((button) => button.classList.toggle("active", button.dataset.filter === mode));
  visibleIds = idsForFilter();
  const found = visibleIds.indexOf(preferred || data.start_id);
  cursor = found >= 0 ? found : Math.min(cursor, Math.max(visibleIds.length - 1, 0));
  render();
}

function field(parent, label, value) {
  const row = node("div", "", "review-row");
  row.append(node("strong", label), node("p", value || "–"));
  parent.append(row);
}

function renderCase(item) {
  const content = el("case-content"); content.replaceChildren();
  if (data.task === "CURATION") {
    const source = node("article", "", "article-card");
    source.append(node("div", "SOURCE", "card-label"));
    field(source, "원문 제목", item.source.title);
    field(source, "게시일", item.source.published_at);
    const link = node("a", "원문 열기 ↗", "source-link"); link.href = item.source.url; link.target = "_blank"; link.rel = "noopener noreferrer"; source.append(link);
    const output = node("article", "", "article-card"); output.append(node("div", "OUT", "card-label"));
    ["title_kr", "summary", "detail", "implication", "why_important"].forEach((key) => field(output, key, item.output[key]));
    content.append(source, output);
  } else {
    const claim = node("article", "", "article-card review-claim"); claim.append(node("div", "CLAIM", "card-label")); field(claim, "검증할 주장", item.claim);
    const evidence = node("article", "", "article-card"); evidence.append(node("div", "EVID", "card-label"));
    field(evidence, "원문 제목", item.source.title); field(evidence, "게시일", item.source.published_at);
    field(evidence, "계약 사실", item.source.contract_facts ? JSON.stringify(item.source.contract_facts, null, 2) : "–");
    field(evidence, "검증 evidence", item.source.verified_evidence ? JSON.stringify(item.source.verified_evidence, null, 2) : "–");
    if (item.source.url) { const link = node("a", "원문 열기 ↗", "source-link"); link.href = item.source.url; link.target = "_blank"; link.rel = "noopener noreferrer"; evidence.append(link); }
    content.append(claim, evidence);
  }
}

function renderVerdicts() {
  const host = el("verdicts"); host.replaceChildren();
  data.label_contract.forEach((label) => {
    const button = node("button", label, `verdict ${label.toLowerCase()}${draft.human_label === label ? " selected" : ""}`);
    button.type = "button"; button.addEventListener("click", () => { draft.human_label = label; if (label === "PASS") draft.human_error_types = []; renderVerdicts(); renderErrors(); }); host.append(button);
  });
}

function optionButton(name, value, selected, click) {
  const button = node("button", value || "N/A", `reason${selected ? " selected" : ""}`);
  button.type = "button"; button.dataset.field = name; button.addEventListener("click", click); return button;
}

function renderDimensions() {
  const host = el("dimensions"); host.replaceChildren(); host.hidden = data.task !== "CURATION";
  if (data.task !== "CURATION") return;
  Object.entries(data.dimension_contract).forEach(([name, values]) => {
    const group = node("section", "", "review-field"); group.append(node("h3", name));
    const options = node("div", "", "reasons");
    values.forEach((value) => options.append(optionButton(name, value, draft.human_dimensions?.[name] === value,
      () => { draft.human_dimensions[name] = value; renderDimensions(); })));
    group.append(options); host.append(group);
  });
}

function renderErrors() {
  const host = el("error-types"); host.replaceChildren(); host.hidden = data.task !== "SEMANTIC";
  if (data.task !== "SEMANTIC") return;
  host.append(node("h3", "Error types (해당 항목 모두 선택)"));
  const options = node("div", "", "reasons");
  data.error_type_contract.forEach((value) => {
    const selected = (draft.human_error_types || []).includes(value);
    options.append(optionButton("error", value, selected, () => {
      const set = new Set(draft.human_error_types || []); selected ? set.delete(value) : set.add(value); draft.human_error_types = [...set]; renderErrors();
    }));
  });
  host.append(options);
}

function renderProvisional(item) {
  const panel = el("provisional-panel"), provisional = data.provisional[item.id];
  panel.hidden = !provisional;
  if (!provisional) return;
  el("priority").textContent = `priority ${provisional.review_priority} · ${(provisional.review_reasons || []).join(", ") || "routine"}`;
  const host = el("provisional-detail"); host.replaceChildren();
  [["판정", provisional.provisional_label], ["confidence", provisional.confidence],
   ["error types", (provisional.error_types || []).join(", ")],
   ["근거", provisional.reason, "wide"], ["evidence reference", provisional.evidence_reference, "wide"]]
    .forEach(([name, value, klass]) => { const box = node("div", "", klass || ""); box.append(node("strong", name), node("span", value)); host.append(box); });
  el("approve").disabled = data.task === "CURATION" && provisional.provisional_label === "AMBIGUOUS";
}

function beginChange() {
  const suggestion = data.suggested_human[currentId()];
  if (suggestion) draft = JSON.parse(JSON.stringify(suggestion));
  renderVerdicts(); renderDimensions(); renderErrors();
  el("verdicts").scrollIntoView({behavior: "smooth"});
  message("Sol 제안을 초안으로 채웠습니다. 최종 Human Gold를 확인·수정하세요.");
}

function render() {
  const item = currentCase(); el("empty").hidden = Boolean(item); el("workspace").hidden = !item; if (!item) return;
  el("position").textContent = `${item.position} / ${data.counts.total}`; el("case-id").textContent = item.id;
  renderCase(item);
  renderProvisional(item);
  const saved = data.labels[item.id];
  draft = saved ? JSON.parse(JSON.stringify(saved)) : {human_label: null, human_dimensions: {}, human_error_types: [], required_repair: null, human_notes: null};
  if (data.task === "CURATION") Object.keys(data.dimension_contract).forEach((key) => { if (!(key in draft.human_dimensions)) draft.human_dimensions[key] = null; });
  el("required-repair").value = draft.required_repair || ""; el("required-repair").parentElement.hidden = data.task !== "CURATION";
  el("human-notes").value = draft.human_notes || "";
  saveState(saved ? "디스크 저장됨" : "저장 대기", saved ? "saved" : "");
  renderVerdicts(); renderDimensions(); renderErrors();
  el("previous").disabled = cursor <= 0; el("next").disabled = cursor >= visibleIds.length - 1; el("clear").disabled = !saved;
  el("reference").hidden = true;
}

function snapshot(id) { return {id, label: data.labels[id] ? JSON.parse(JSON.stringify(data.labels[id])) : null, status: data.review_status[id] ? JSON.parse(JSON.stringify(data.review_status[id])) : null}; }

function advanceAfter(id) {
  if (!el("auto-next").checked) { render(); return; }
  if (filterMode === "unlabeled" || filterMode === "priority") { visibleIds = idsForFilter(); cursor = Math.min(cursor, Math.max(visibleIds.length - 1, 0)); }
  else cursor = Math.min(cursor + 1, visibleIds.length - 1);
  render();
}

async function save(moveAfter = false) {
  if (!draft.human_label) { saveState("verdict를 선택하세요", "error"); return; }
  draft.required_repair = el("required-repair").value.trim() || null; draft.human_notes = el("human-notes").value.trim() || null;
  try {
    const id = currentId(), previous = snapshot(id);
    const result = await request("/api/labels", {method: "POST", body: JSON.stringify({id, ...draft, review_action: "CHANGE"})});
    data.labels[id] = result.label; data.review_status[id] = result.label; updateCounts(result.counts); undoAction = previous; el("undo").disabled = false; message(`${id} 사람 수정 저장 완료`);
    if (moveAfter) advanceAfter(id); else render();
  } catch (error) { saveState(error.message, "error"); }
}

async function reviewAction(action) {
  const id = currentId(); if (!id || !data.provisional[id]) { message("Sol provisional이 없습니다.", true); return; }
  const previous = snapshot(id);
  try {
    const result = await request("/api/review", {method: "POST", body: JSON.stringify({id, action})});
    if (action === "APPROVE") { data.labels[id] = result.label; data.review_status[id] = result.label; }
    else { delete data.labels[id]; data.review_status[id] = {needs_review: true, human_reviewed: false, review_action: action}; }
    updateCounts(result.counts); undoAction = previous; el("undo").disabled = false;
    message(action === "APPROVE" ? `${id} Sol 판정 사람 승인 완료` : `${id} 추가 검수 보류`); advanceAfter(id);
  } catch (error) { message(error.message, true); }
}

async function clearCurrent() {
  try { const id = currentId(), previous = snapshot(id); const result = await request(`/api/labels?id=${encodeURIComponent(id)}`, {method: "DELETE"}); delete data.labels[id]; data.review_status[id] = {cleared: true}; updateCounts(result.counts); undoAction = previous; el("undo").disabled = false; applyFilter(filterMode, id); }
  catch (error) { message(error.message, true); }
}

async function undo() {
  if (!undoAction) return; const previous = undoAction; let result;
  try {
    if (previous.label) result = await request("/api/labels", {method: "POST", body: JSON.stringify({id: previous.id, ...previous.label, review_action: previous.label.review_action || "CHANGE"})});
    else if (previous.status?.needs_review) result = await request("/api/review", {method: "POST", body: JSON.stringify({id: previous.id, action: "NEEDS_REVIEW"})});
    else result = await request(`/api/labels?id=${encodeURIComponent(previous.id)}`, {method: "DELETE"});
    if (previous.label) data.labels[previous.id] = result.label; else delete data.labels[previous.id];
    data.review_status[previous.id] = previous.status || {cleared: true}; updateCounts(result.counts); undoAction = null; el("undo").disabled = true; applyFilter(filterMode, previous.id); message("방금 검수 작업을 되돌렸습니다.");
  } catch (error) { message(error.message, true); }
}

async function reference() {
  if (!el("reference").hidden) { el("reference").hidden = true; return; }
  try { el("reference").textContent = JSON.stringify(await request(`/api/reference?id=${encodeURIComponent(currentId())}`), null, 2); el("reference").hidden = false; }
  catch (error) { message(error.message, true); }
}

async function validate(exporting = false) {
  if (exporting && !window.confirm("sidecar의 사람 라벨을 canonical fixture에 반영할까요?")) return;
  try { const result = await request(exporting ? "/api/export" : "/api/validate", exporting ? {method: "POST"} : {}); updateCounts(result.counts); message(`${exporting ? "Export / " : ""}Validate 통과 · 오류 0`); }
  catch (error) { message(error.message, true); }
}

async function start() {
  data = await request("/api/state"); el("title").textContent = data.title; document.title = `${data.title} Labeler`; updateCounts(data.counts);
  document.querySelectorAll(".filter").forEach((button) => button.addEventListener("click", () => applyFilter(button.dataset.filter)));
  el("previous").addEventListener("click", () => { cursor = Math.max(0, cursor - 1); render(); }); el("next").addEventListener("click", () => { cursor = Math.min(visibleIds.length - 1, cursor + 1); render(); });
  el("save-next").addEventListener("click", () => save(true)); el("clear").addEventListener("click", clearCurrent); el("reference-toggle").addEventListener("click", reference); el("validate").addEventListener("click", () => validate(false)); el("export").addEventListener("click", () => validate(true));
  el("approve").addEventListener("click", () => reviewAction("APPROVE")); el("change").addEventListener("click", beginChange); el("needs-review").addEventListener("click", () => reviewAction("NEEDS_REVIEW")); el("undo").addEventListener("click", undo);
  document.addEventListener("keydown", (event) => { if (event.target.matches("textarea,input")) return; const key = event.key.toLowerCase(); if (data.provisional[currentId()] && key === "a") reviewAction("APPROVE"); else if (data.provisional[currentId()] && key === "c") beginChange(); else if (data.provisional[currentId()] && key === "u") reviewAction("NEEDS_REVIEW"); else { const label = data.shortcuts[key]; if (label) { draft.human_label = label; if (label === "PASS") draft.human_error_types = []; renderVerdicts(); renderErrors(); } else if (event.key === "ArrowLeft") { cursor = Math.max(0, cursor - 1); render(); } else if (event.key === "ArrowRight") { cursor = Math.min(visibleIds.length - 1, cursor + 1); render(); } else if (event.key === "Enter") save(true); } });
  applyFilter(data.default_filter, data.start_id); message(`자동 저장 위치: ${data.sidecar_path}`);
}

start().catch((error) => message(`시작 실패: ${error.message}`, true));
