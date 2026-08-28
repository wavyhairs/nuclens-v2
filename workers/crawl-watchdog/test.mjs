import assert from "node:assert/strict";
import test from "node:test";

import {
  checkAndRecover, evaluateRuns, recoveryLookbackHours, slotStart,
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
