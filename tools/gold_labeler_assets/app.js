"use strict";

const ui = {};
let data = null;
let filterMode = "first60";
let visibleIds = [];
let cursor = 0;
let draftLabel = null;
let draftReason = null;
let undoAction = null;

const byId = (id) => document.getElementById(id);
const currentId = () => visibleIds[cursor] || null;
const currentCase = () => data.candidates.find((item) => item.id === currentId());
const isTyping = (event) => {
  const target = event.target;
  return target && (target.matches("input, textarea, select") || target.isContentEditable);
};

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || (payload.errors || []).join(" · ") || `HTTP ${response.status}`);
  return payload;
}

function setMessage(message, isError = false) {
  ui.message.textContent = message;
  ui.message.style.color = isError ? "#a22d24" : "";
}

function setSaveState(message, kind = "") {
  ui.saveState.textContent = message;
  ui.saveState.className = `save-state ${kind}`.trim();
}

function updateCounts(counts) {
  data.counts = counts;
  ui.total.textContent = counts.total;
  ui.labeled.textContent = counts.labeled;
  ui.mergeCount.textContent = counts.merge;
  ui.separateCount.textContent = counts.separate;
  ui.ambiguousCount.textContent = counts.ambiguous;
  ui.remaining.textContent = counts.remaining;
  ui.first60.textContent = `${counts.first60_labeled} / ${counts.first60_total}`;
  ui.ready.textContent = counts.evaluation_ready ? "60개 human labels ready · 평가 실행 가능" : "라벨링 중";
}

function idsForFilter(mode) {
  const ids = data.candidates.map((item) => item.id);
  if (mode === "first60") return ids.slice(0, 60);
  if (mode === "unlabeled") return ids.filter((id) => !data.labels[id]);
  return ids;
}

function applyFilter(mode, preferredId = null) {
  filterMode = mode;
  document.querySelectorAll(".filter").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === mode);
  });
  visibleIds = idsForFilter(mode);
  const wanted = preferredId || data.start_id;
  const found = visibleIds.indexOf(wanted);
  cursor = found >= 0 ? found : Math.min(cursor, Math.max(visibleIds.length - 1, 0));
  render();
}

function text(id, value, fallback = "–") {
  byId(id).textContent = value || fallback;
}

function renderArticle(prefix, article) {
  text(`${prefix}-title`, article.title);
  text(`${prefix}-publisher`, article.publisher);
  text(`${prefix}-date`, article.published_at);
  text(`${prefix}-original`, article.original_title);
  text(`${prefix}-summary`, article.summary, "요약 없음");
  const link = byId(`${prefix}-link`);
  link.hidden = !article.url;
  link.href = article.url || "#";
}

function renderComparison(comparison) {
  text("date-gap", comparison.date_gap_days == null ? "게시일 차이: 확인 불가" : `게시일 차이: ${comparison.date_gap_days}일`);
  text("token-overlap", `제목 token overlap: ${Math.round(comparison.title_token_overlap * 100)}%`);
  text("common-units", `공통 원전/호기: ${(comparison.common_units || []).join(", ") || "없음"}`);
  text("shared-entities", `공통 기관: ${(comparison.shared_entities || []).join(", ") || "없음"}`);
  text("same-source", comparison.same_publisher ? "같은 출처" : "서로 다른 출처");
}

function renderReasons() {
  ui.reasonArea.hidden = !draftLabel;
  ui.reasons.replaceChildren();
  if (!draftLabel) return;
  data.reasons[draftLabel].forEach((reason, index) => {
    const button = document.createElement("button");
    button.className = `reason${draftReason === reason ? " selected" : ""}`;
    button.dataset.reason = reason;
    const key = index < 9 ? `${index + 1}` : "";
    button.innerHTML = `${key ? `<kbd>${key}</kbd>` : ""}<span></span>`;
    button.querySelector("span").textContent = reason;
    button.addEventListener("click", () => selectReason(reason));
    ui.reasons.append(button);
  });
  ui.otherWrap.hidden = draftReason !== "other";
}

function renderSelection() {
  document.querySelectorAll(".verdict").forEach((button) => {
    button.classList.toggle("selected", button.dataset.label === draftLabel);
  });
  renderReasons();
}

function render() {
  const item = currentCase();
  ui.empty.hidden = Boolean(item);
  ui.workspace.hidden = !item;
  if (!item) return;
  ui.position.textContent = `${item.position} / ${data.counts.total}`;
  ui.pairId.textContent = item.id;
  renderArticle("a", item.a);
  renderArticle("b", item.b);
  renderComparison(item.comparison);
  const saved = data.labels[item.id];
  draftLabel = saved?.human_label || null;
  draftReason = saved?.reason_code || null;
  ui.otherNote.value = saved?.human_notes || "";
  setSaveState(saved ? "디스크 저장됨" : "저장 대기", saved ? "saved" : "");
  renderSelection();
  ui.previous.disabled = cursor <= 0;
  ui.next.disabled = cursor >= visibleIds.length - 1;
  ui.clear.disabled = !saved;
  ui.reference.hidden = true;
  ui.reference.textContent = "";
  ui.referenceToggle.setAttribute("aria-expanded", "false");
  ui.referenceToggle.textContent = "참고정보 보기 · 모델 판정은 기본 숨김";
}

function selectLabel(label) {
  draftLabel = label;
  if (!data.reasons[label].includes(draftReason)) draftReason = null;
  setSaveState("reason 선택 필요");
  renderSelection();
}

async function selectReason(reason) {
  draftReason = reason;
  renderSelection();
  if (reason === "other") {
    ui.otherNote.focus();
    setSaveState("메모 입력 후 저장");
    return;
  }
  await saveCurrent(ui.autoNext.checked);
}

async function saveCurrent(moveAfter = false) {
  const id = currentId();
  if (!id || !draftLabel || !draftReason) {
    setSaveState("판정과 reason을 선택하세요", "error");
    return;
  }
  const note = draftReason === "other" ? ui.otherNote.value.trim() : null;
  const previous = data.labels[id] ? {...data.labels[id]} : null;
  setSaveState("저장 중…");
  try {
    const result = await request("/api/labels", {
      method: "POST",
      body: JSON.stringify({id, label: draftLabel, reason: draftReason, note}),
    });
    data.labels[id] = result.label;
    updateCounts(result.counts);
    undoAction = {id, previous};
    ui.undo.disabled = false;
    setSaveState("디스크 저장됨", "saved");
    setMessage(`${id} 저장 완료`);
    if (moveAfter) moveAfterSave(id);
    else render();
  } catch (error) {
    setSaveState(error.message, "error");
  }
}

function moveAfterSave(savedId) {
  if (filterMode === "unlabeled") {
    const oldCursor = cursor;
    visibleIds = idsForFilter(filterMode);
    cursor = Math.min(oldCursor, Math.max(visibleIds.length - 1, 0));
  } else {
    cursor = Math.min(cursor + 1, visibleIds.length - 1);
  }
  render();
}

function move(delta) {
  if (!visibleIds.length) return;
  cursor = Math.max(0, Math.min(cursor + delta, visibleIds.length - 1));
  render();
}

async function clearCurrent() {
  const id = currentId();
  if (!id || !data.labels[id]) return;
  const previous = {...data.labels[id]};
  try {
    const result = await request(`/api/labels?id=${encodeURIComponent(id)}`, {method: "DELETE"});
    delete data.labels[id];
    updateCounts(result.counts);
    undoAction = {id, previous};
    ui.undo.disabled = false;
    setMessage(`${id} 라벨 삭제 완료`);
    applyFilter(filterMode, id);
  } catch (error) { setMessage(error.message, true); }
}

async function undo() {
  if (!undoAction) return;
  const {id, previous} = undoAction;
  try {
    let result;
    if (previous) {
      result = await request("/api/labels", {method: "POST", body: JSON.stringify({
        id, label: previous.human_label, reason: previous.reason_code,
        note: previous.human_notes,
      })});
      data.labels[id] = result.label;
    } else {
      result = await request(`/api/labels?id=${encodeURIComponent(id)}`, {method: "DELETE"});
      delete data.labels[id];
    }
    updateCounts(result.counts);
    undoAction = null;
    ui.undo.disabled = true;
    applyFilter(filterMode, id);
    setMessage("방금 저장을 되돌렸습니다.");
  } catch (error) { setMessage(error.message, true); }
}

async function toggleReference() {
  if (!ui.reference.hidden) {
    ui.reference.hidden = true;
    ui.referenceToggle.setAttribute("aria-expanded", "false");
    ui.referenceToggle.textContent = "참고정보 보기 · 모델 판정은 기본 숨김";
    return;
  }
  try {
    const reference = await request(`/api/reference?id=${encodeURIComponent(currentId())}`);
    ui.reference.textContent = `${reference.warning}\n\n${JSON.stringify(reference, null, 2)}`;
    ui.reference.hidden = false;
    ui.referenceToggle.setAttribute("aria-expanded", "true");
    ui.referenceToggle.textContent = "참고정보 닫기";
  } catch (error) { setMessage(error.message, true); }
}

async function validate() {
  try {
    const report = await request("/api/validate");
    updateCounts(report.counts);
    setMessage(`검증 통과 · ${report.counts.labeled}개 저장 · 오류 0`);
  } catch (error) { setMessage(`검증 실패: ${error.message}`, true); }
}

async function exportFixture() {
  if (!window.confirm("sidecar의 사람 라벨을 canonical fixture에 반영하고 검증할까요?")) return;
  try {
    const report = await request("/api/export", {method: "POST"});
    updateCounts(report.counts);
    setMessage(`Export / Validate 통과 · ${report.counts.labeled}개 canonical 반영`);
  } catch (error) { setMessage(`Export 검증 실패: ${error.message}`, true); }
}

function bind() {
  ["position", "pair-id", "total", "labeled", "merge-count", "separate-count",
   "ambiguous-count", "remaining", "first60", "ready", "empty", "workspace",
   "previous", "next", "save-state", "reason-area", "reasons", "other-wrap",
   "other-note", "auto-next", "save-next", "undo", "clear", "reference-toggle",
   "reference", "validate", "export", "message"].forEach((id) => {
    ui[id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = byId(id);
  });
  document.querySelectorAll(".filter").forEach((button) => button.addEventListener("click", () => applyFilter(button.dataset.filter)));
  document.querySelectorAll(".verdict").forEach((button) => button.addEventListener("click", () => selectLabel(button.dataset.label)));
  ui.previous.addEventListener("click", () => move(-1));
  ui.next.addEventListener("click", () => move(1));
  ui.saveNext.addEventListener("click", () => saveCurrent(true));
  ui.clear.addEventListener("click", clearCurrent);
  ui.undo.addEventListener("click", undo);
  ui.referenceToggle.addEventListener("click", toggleReference);
  ui.validate.addEventListener("click", validate);
  ui.export.addEventListener("click", exportFixture);
  ui.autoNext.addEventListener("change", () => {
    ui.autoNext.nextElementSibling.textContent = ui.autoNext.checked ? "Auto-next ON" : "Auto-next OFF";
  });
  document.addEventListener("keydown", (event) => {
    if (isTyping(event)) return;
    const key = event.key.toLowerCase();
    if (data.shortcuts.labels[key]) { event.preventDefault(); selectLabel(data.shortcuts.labels[key]); return; }
    if (/^[1-9]$/.test(key) && draftLabel) {
      const reason = data.reasons[draftLabel][Number(key) - 1];
      if (reason) { event.preventDefault(); selectReason(reason); }
      return;
    }
    if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    if (event.key === "Enter") { event.preventDefault(); saveCurrent(true); }
  });
}

async function start() {
  bind();
  try {
    data = await request("/api/state");
    updateCounts(data.counts);
    applyFilter(data.default_filter, data.start_id);
    setMessage(`자동 저장 위치: ${data.sidecar_path}`);
  } catch (error) {
    setMessage(`시작 실패: ${error.message}`, true);
  }
}

start();
