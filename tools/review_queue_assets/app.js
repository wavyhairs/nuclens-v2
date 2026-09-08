"use strict";
// 판정을 누르면 바로 서버에 저장한다. 중간에 닫아도 진행이 남아야 한다.
let state = null, cursor = 0;
const $ = (id) => document.getElementById(id);
const current = () => state.cases[cursor];

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function row(parent, label, value) {
  const wrap = el("div", null, "row");
  wrap.append(el("b", label));
  const body = el("pre", value === null || value === undefined || value === "" ? "–" : value);
  wrap.append(body);
  parent.append(wrap);
}

function card(title) {
  const box = el("div", null, "card");
  box.append(el("h2", title));
  return box;
}

function renderCase() {
  const host = $("case");
  host.replaceChildren();
  const item = current();
  if (item.queue === "blind" && item.task === "curation") {
    const src = card("원문");
    row(src, "제목", item.source.title);
    row(src, "게시일", item.source.published_at);
    if (item.source.url) {
      const link = el("a", "원문 열기 ↗");
      link.href = item.source.url; link.target = "_blank"; link.rel = "noopener";
      src.append(link);
    }
    const out = card("생성된 결과 — 이것을 판정합니다");
    ["title_kr", "summary", "detail", "implication", "why_important"]
      .forEach((k) => row(out, k, item.output[k]));
    host.append(src, out);
  } else if (item.queue === "blind") {
    const claim = card("검증할 주장 — 이것을 판정합니다");
    row(claim, "주장", item.claim);
    const ev = card("Source Evidence");
    row(ev, "근거", JSON.stringify(item.evidence, null, 2));
    host.append(claim, ev);
  } else {
    const pair = el("div", null, "pair");
    [["A", item.left], ["B", item.right]].forEach(([side, art]) => {
      const box = card("기사 " + side);
      row(box, "제목", art.title);
      row(box, "매체", art.publisher);
      row(box, "게시일", art.published_at);
      row(box, "요약", art.summary);
      pair.append(box);
    });
    const ctx = card("참고 — 결정적 메타데이터");
    row(ctx, "게시일 간격", item.context.date_gap_days);
    row(ctx, "공통 개체", (item.context.shared_entities || []).join(", "));
    row(ctx, "대기 사유", item.why_queued);
    host.append(pair, ctx);
  }
}

function renderVerdicts() {
  const host = $("verdicts");
  host.replaceChildren();
  const item = current();
  const chosen = state.labels[item.id];
  state.queues[item.queue].labels.forEach((label) => {
    const button = el("button", label, chosen === label ? "on" : "");
    button.addEventListener("click", () => saveLabel(label));
    host.append(button);
  });
}

function render() {
  const item = current();
  $("note").textContent = state.queues[item.queue].note;
  $("progress").textContent =
    `${cursor + 1} / ${state.cases.length} · ${item.queue}`;
  const done = state.cases.filter((c) => state.labels[c.id]).length;
  $("done").textContent = done === state.cases.length
    ? "모두 판정했습니다" : `판정 ${done}건`;
  renderCase();
  renderVerdicts();
}

async function saveLabel(label) {
  const item = current();
  const response = await fetch("/api/label", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ queue: item.queue, case_id: item.id, label }),
  });
  const payload = await response.json();
  if (!response.ok) { alert(payload.error || "저장 실패"); return; }
  state.labels[item.id] = label;
  render();
  // 판정 직후 자동으로 넘어간다 — 37건을 한 자리에서 끝내는 것이 목적이다.
  if (cursor < state.cases.length - 1) { cursor += 1; render(); }
}

function move(step) {
  cursor = Math.min(state.cases.length - 1, Math.max(0, cursor + step));
  render();
}

(async () => {
  state = await (await fetch("/api/state", { cache: "no-store" })).json();
  const firstUnlabelled = state.cases.findIndex((c) => !state.labels[c.id]);
  cursor = firstUnlabelled >= 0 ? firstUnlabelled : 0;
  $("prev").addEventListener("click", () => move(-1));
  $("next").addEventListener("click", () => move(1));
  document.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") move(-1);
    else if (event.key === "ArrowRight") move(1);
    else {
      const labels = state.queues[current().queue].labels;
      const index = Number(event.key) - 1;
      if (Number.isInteger(index) && index >= 0 && index < labels.length) {
        saveLabel(labels[index]);
      }
    }
  });
  render();
})();
