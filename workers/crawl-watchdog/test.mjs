import assert from "node:assert/strict";
import test from "node:test";

import {
  checkAndRecover, checkAndRecoverWeekly, evaluateRuns, evaluateWeekly,
  isoWeekId, recoveryLookbackHours, slotStart, weeklyWindow, weeklyWindowStart,
} from "./src/index.mjs";

const at = (value) => new Date(value);

test("uses fixed UTC three-hour slots", () => {
  assert.equal(slotStart(at("2026-08-28T09:36:00Z")).toISOString(),
               "2026-08-28T09:00:00.000Z");
});

test("missing 12·15·18·21 KST schedules each dispatch once", () => {
  const state = { slots: {} };
  for (const hour of [3, 6, 9, 12]) {
    const now = at(`2026-08-28T${String(hour).padStart(2, "0")}:37:00Z`);
    const missing = evaluateRuns([], now, 25);
    assert.equal(missing.shouldDispatch, true);
    assert.equal(missing.state, "trigger_missing");

    const key = new Date(Date.UTC(2026, 7, 28, hour)).toISOString().replace(".000Z", "Z");
    state.slots[key] = { status: "success_with_articles" };
    const recovered = evaluateRuns([{
      created_at: now.toISOString(), status: "completed", conclusion: "success",
    }], at(new Date(now.getTime() + 10 * 60_000).toISOString()), 25, state);
    assert.equal(recovered.shouldDispatch, false);
    assert.equal(recovered.state, "success_with_articles");
  }
});

test("active normal schedule prevents backup overlap", () => {
  const decision = evaluateRuns([{
    created_at: "2026-08-28T09:12:00Z", status: "in_progress", conclusion: null,
  }], at("2026-08-28T09:37:00Z"), 25);
  assert.equal(decision.shouldDispatch, false);
  assert.equal(decision.state, "workflow_active");
});

test("failed workflow is retried but success with zero articles is still success", () => {
  const failed = evaluateRuns([{
    created_at: "2026-08-28T09:12:00Z", status: "completed", conclusion: "failure",
  }], at("2026-08-28T09:22:00Z"), 25);
  assert.equal(failed.shouldDispatch, true);
  assert.equal(failed.state, "workflow_failed");

  // Actions conclusion remains success when the durable crawl state says
  // success_zero_articles; the watchdog must not turn a quiet news slot into a retry storm.
  const zero = evaluateRuns([{
    created_at: "2026-08-28T09:12:00Z", status: "completed", conclusion: "success",
  }], at("2026-08-28T09:37:00Z"), 25, { slots: {
    "2026-08-28T09:00:00Z": { status: "success_zero_articles" },
  }});
  assert.equal(zero.shouldDispatch, false);
  assert.equal(zero.state, "success_zero_articles");
});

test("a green duplicate run cannot mask an unconfirmed crawl", () => {
  const runs = [{
    created_at: "2026-08-28T09:35:00Z", status: "completed", conclusion: "success",
  }];
  const unconfirmed = evaluateRuns(
    runs, at("2026-08-28T09:52:00Z"), 25, { slots: {} });
  assert.equal(unconfirmed.shouldDispatch, true);
  assert.equal(unconfirmed.state, "workflow_unconfirmed");
});

test("long outage widens recovery window without exceeding the cap", () => {
  const runs = [{
    created_at: "2026-08-28T00:16:00Z", run_started_at: "2026-08-28T00:16:00Z",
    status: "completed", conclusion: "success",
  }];
  assert.equal(recoveryLookbackHours(runs, at("2026-08-28T12:37:00Z")), 14);
  assert.equal(recoveryLookbackHours([], at("2026-08-28T12:37:00Z")), 24);
});

test("watchdog dispatches exactly one backup for a missing slot", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    if ((init.method || "GET") === "POST") {
      return { ok: true, status: 204 };
    }
    if (String(url).includes("/contents/crawl_runs.json")) {
      const state = { slots: {
        "2026-08-28T00:00:00Z": {
          slot: "2026-08-28T00:00:00Z", status: "success_with_articles",
          finished_at: "2026-08-28T00:16:00Z",
        },
      } };
      return {
        ok: true, status: 200,
        async json() { return { content: Buffer.from(JSON.stringify(state)).toString("base64") }; },
      };
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return { workflow_runs: [{
          created_at: "2026-08-28T00:16:00Z",
          run_started_at: "2026-08-28T00:16:00Z",
          status: "completed",
          conclusion: "success",
        }] };
      },
    };
  };
  try {
    const result = await checkAndRecover({
      GITHUB_TOKEN: "test-token",
      GITHUB_OWNER: "wavyhairs",
      GITHUB_REPO: "nuclens-v2",
      GITHUB_WORKFLOW: "crawl.yml",
      BACKUP_GRACE_MINUTES: "25",
    }, at("2026-08-28T12:37:00Z"));
    assert.equal(result.dispatched, true);
    assert.equal(calls.filter((call) => call.init.method === "POST").length, 1);
    const payload = JSON.parse(calls.find((call) => call.init.method === "POST").init.body);
    assert.equal(payload.inputs.trigger_source, "backup_watchdog");
    assert.equal(payload.inputs.recovery_reason, "trigger_missing");
    assert.equal(payload.inputs.recovery_lookback_hours, "14");
  } finally {
    globalThis.fetch = originalFetch;
  }
});


// ── 주간 판세 ───────────────────────────────────────────────────────────────

// 값은 Python 이 정답이다 — weekly_bot 이 `datetime.isocalendar()` 로 주차를
// 짓고 게이트·알림이 그 문자열로 상태를 찾는다. 한 글자만 어긋나도 Worker 는
// 영원히 "안 나갔다"고 읽는다. 연말 두 경계를 함께 박아 둔다.
test("ISO week matches weekly_bot, including the year boundaries", () => {
  assert.equal(isoWeekId(at("2026-01-01T00:00:00Z")), "2026-W01");
  assert.equal(isoWeekId(at("2026-08-28T09:10:00Z")), "2026-W35");
  assert.equal(isoWeekId(at("2026-09-18T12:10:00Z")), "2026-W38");
  assert.equal(isoWeekId(at("2026-12-31T20:00:00Z")), "2026-W53");  // KST 로는 이미 1/1
  assert.equal(isoWeekId(at("2027-01-03T20:00:00Z")), "2027-W01");
  assert.equal(isoWeekId(at("2025-12-29T00:00:00Z")), "2026-W01");
});

test("the weekly window opens at 17:05 KST Friday and closes at noon Sunday", () => {
  assert.equal(weeklyWindow(at("2026-09-18T08:04:00Z")), false);  // 17:04 금
  assert.equal(weeklyWindow(at("2026-09-18T08:05:00Z")), true);   // 17:05 금
  assert.equal(weeklyWindow(at("2026-09-18T08:07:00Z")), true);   // 17:07 금 — Worker 첫 호출
  assert.equal(weeklyWindow(at("2026-09-19T06:00:00Z")), true);   // 15:00 토
  assert.equal(weeklyWindow(at("2026-09-20T02:59:00Z")), true);   // 11:59 일
  assert.equal(weeklyWindow(at("2026-09-20T03:00:00Z")), false);  // 12:00 일
  assert.equal(weeklyWindow(at("2026-09-21T08:10:00Z")), false);  // 월
});

test("the retry budget is counted from this Friday, not the last one", () => {
  const start = "2026-09-18T08:05:00.000Z";
  assert.equal(weeklyWindowStart(at("2026-09-18T09:00:00Z")).toISOString(), start);
  assert.equal(weeklyWindowStart(at("2026-09-19T15:00:00Z")).toISOString(), start);
  assert.equal(weeklyWindowStart(at("2026-09-20T02:00:00Z")).toISOString(), start);
});

test("a delivered week is never dispatched again", () => {
  const reports = { reports: { "2026-W38": {
    _automation: { telegram: { status: "sent" } },
  } } };
  const decision = evaluateWeekly([], reports, at("2026-09-18T12:10:00Z"));
  assert.equal(decision.shouldDispatch, false);
  assert.equal(decision.state, "delivery_confirmed");
});

test("a missing Friday report is dispatched as backup_watchdog", () => {
  const decision = evaluateWeekly([], { reports: {} }, at("2026-09-18T08:10:00Z"));
  assert.equal(decision.shouldDispatch, true);
  assert.equal(decision.state, "trigger_missing");
  assert.equal(decision.week, "2026-W38");
});

test("an in-flight weekly run is not doubled", () => {
  const runs = [{ created_at: "2026-09-18T08:12:00Z", status: "in_progress" }];
  const decision = evaluateWeekly(runs, { reports: {} }, at("2026-09-18T08:40:00Z"));
  assert.equal(decision.shouldDispatch, false);
  assert.equal(decision.state, "workflow_active");
});

test("a failed run waits half an hour before the next try", () => {
  const runs = [{
    created_at: "2026-09-18T08:12:00Z", status: "completed", conclusion: "failure",
    event: "workflow_dispatch",
  }];
  const early = evaluateWeekly(runs, { reports: {} }, at("2026-09-18T08:30:00Z"));
  assert.equal(early.shouldDispatch, false);
  assert.equal(early.state, "within_retry_gap");

  const later = evaluateWeekly(runs, { reports: {} }, at("2026-09-18T08:45:00Z"));
  assert.equal(later.shouldDispatch, true);
  assert.equal(later.state, "delivery_unconfirmed");
});

// 2026-09-18 Weekly 는 열한 번 연속 startup_failure 로 1초 만에 죽었다. 그런
// 주에는 아무리 불러도 안 산다 — 창이 닫힐 때까지 15분마다 부르면 죽은 런만
// 150개 쌓인다. 여섯 번에서 멈추고 나머지는 운영 알림에 넘긴다.
test("a workflow that cannot start does not become a dispatch storm", () => {
  const runs = [];
  for (let index = 0; index < 6; index += 1) {
    runs.push({
      created_at: new Date(Date.UTC(2026, 8, 18, 8, 10 + index * 30)).toISOString(),
      status: "completed", conclusion: "startup_failure", event: "workflow_dispatch",
    });
  }
  const decision = evaluateWeekly(runs, { reports: {} }, at("2026-09-18T12:10:00Z"));
  assert.equal(decision.shouldDispatch, false);
  assert.equal(decision.state, "retry_budget_spent");
});

test("outside the window the weekly check costs no API call", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return { ok: true, status: 204 }; };
  try {
    const result = await checkAndRecoverWeekly(
      { GITHUB_TOKEN: "test-token" }, at("2026-09-21T08:10:00Z"));
    assert.equal(result.weekly_state, "outside_window");
    assert.equal(result.dispatched, false);
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("the weekly dispatch identifies itself so the gate can judge it", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if ((init.method || "GET") === "POST") return { ok: true, status: 204 };
    if (String(url).includes("/contents/weekly_reports.json")) {
      return { ok: true, status: 200, async json() { return { reports: {} }; } };
    }
    return { ok: true, status: 200, async json() { return { workflow_runs: [] }; } };
  };
  try {
    const result = await checkAndRecoverWeekly({
      GITHUB_TOKEN: "test-token", GITHUB_OWNER: "wavyhairs",
      GITHUB_REPO: "nuclens-v2", GITHUB_WEEKLY_WORKFLOW: "weekly.yml",
    }, at("2026-09-18T08:10:00Z"));
    assert.equal(result.dispatched, true);
    assert.equal(result.week, "2026-W38");
    const post = calls.find((call) => call.init.method === "POST");
    assert.ok(post.url.includes("/workflows/weekly.yml/dispatches"));
    assert.equal(JSON.parse(post.init.body).inputs.trigger_source, "backup_watchdog");
    // raw 로 받아야 1MB 를 넘긴 weekly_reports.json 도 읽힌다.
    const read = calls.find((call) => call.url.includes("weekly_reports.json"));
    assert.equal(read.init.headers.Accept, "application/vnd.github.raw");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
