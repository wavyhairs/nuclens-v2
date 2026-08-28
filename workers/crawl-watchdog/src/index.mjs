const SLOT_MS = 3 * 60 * 60 * 1000;
const MIN_LOOKBACK_HOURS = 6;
const MAX_LOOKBACK_HOURS = 24;

export function slotStart(now) {
  return new Date(Math.floor(now.getTime() / SLOT_MS) * SLOT_MS);
}

function durableSlot(state, start) {
  return state?.slots?.[start.toISOString().replace(".000Z", "Z")] || null;
}

export function evaluateRuns(runs, now = new Date(), graceMinutes = 25, state = null) {
  const start = slotStart(now);
  const graceEnds = new Date(start.getTime() + graceMinutes * 60_000);
  const current = runs.filter((run) => {
    const created = new Date(run.created_at || 0);
    return created >= start;
  });
  const active = current.find((run) => run.status === "queued" || run.status === "in_progress");
  if (active) return { shouldDispatch: false, state: "workflow_active", start };

  const durable = durableSlot(state, start);
  if (durable?.status === "success_with_articles" ||
      durable?.status === "success_zero_articles") {
    return { shouldDispatch: false, state: durable.status, start };
  }

  const failed = current.find((run) => run.status === "completed" && run.conclusion !== "success");
  if (failed || durable?.status === "failed") {
    return { shouldDispatch: true, state: "workflow_failed", start };
  }
  if (durable?.status === "running") {
    const claimed = new Date(durable.claimed_at || 0);
    if (now.getTime() - claimed.getTime() < 45 * 60_000) {
      return { shouldDispatch: false, state: "claim_waiting", start };
    }
    return { shouldDispatch: true, state: "stale_claim", start };
  }
  if (now < graceEnds) return { shouldDispatch: false, state: "within_schedule_grace", start };
  const unconfirmed = current.find((run) =>
    run.status === "completed" && run.conclusion === "success");
  if (unconfirmed) return { shouldDispatch: true, state: "workflow_unconfirmed", start };
  return { shouldDispatch: true, state: "trigger_missing", start };
}

export function recoveryLookbackHours(runs, now = new Date(), state = null) {
  const durableTimes = Object.values(state?.slots || {})
    .filter((row) => row?.status === "success_with_articles" ||
                     row?.status === "success_zero_articles")
    .map((row) => new Date(row.finished_at || row.slot || 0))
    .filter((value) => !Number.isNaN(value.getTime()))
    .sort((a, b) => b - a);
  if (durableTimes.length) {
    const staleHours = Math.ceil((now.getTime() - durableTimes[0].getTime()) / 3_600_000) + 1;
    return Math.max(MIN_LOOKBACK_HOURS, Math.min(MAX_LOOKBACK_HOURS, staleHours));
  }
  const lastSuccess = runs.find((run) =>
    run.status === "completed" && run.conclusion === "success");
  if (!lastSuccess) return MAX_LOOKBACK_HOURS;
  const at = new Date(lastSuccess.run_started_at || lastSuccess.created_at || 0);
  const staleHours = Math.ceil((now.getTime() - at.getTime()) / 3_600_000) + 1;
  return Math.max(MIN_LOOKBACK_HOURS, Math.min(MAX_LOOKBACK_HOURS, staleHours));
}

async function github(env, path, init = {}) {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "nuclens-crawl-watchdog/1.0",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(`GitHub ${init.method || "GET"} ${path}: HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

export async function checkAndRecover(env, now = new Date()) {
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN Worker secret is missing");
  const owner = env.GITHUB_OWNER || "wavyhairs";
  const repo = env.GITHUB_REPO || "nuclens-v2";
  const workflow = env.GITHUB_WORKFLOW || "crawl.yml";
  const grace = Number.parseInt(env.BACKUP_GRACE_MINUTES || "25", 10);
  const base = `/repos/${owner}/${repo}/actions/workflows/${workflow}`;
  const [data, content] = await Promise.all([
    github(env, `${base}/runs?branch=main&per_page=30`),
    github(env, `/repos/${owner}/${repo}/contents/crawl_runs.json?ref=main`),
  ]);
  const runs = Array.isArray(data?.workflow_runs) ? data.workflow_runs : [];
  const bytes = Uint8Array.from(atob(String(content?.content || "").replace(/\s/g, "")),
                                (char) => char.charCodeAt(0));
  const state = JSON.parse(new TextDecoder().decode(bytes));
  const decision = evaluateRuns(runs, now, grace, state);
  const log = {
    watchdog_state: decision.state,
    slot: decision.start.toISOString(),
    checked_at: now.toISOString(),
    dispatched: false,
  };
  if (!decision.shouldDispatch) {
    console.log(JSON.stringify(log));
    return log;
  }

  const lookback = recoveryLookbackHours(runs, now, state);
  await github(env, `${base}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      ref: "main",
      inputs: {
        trigger_source: "backup_watchdog",
        recovery_reason: decision.state,
        recovery_lookback_hours: String(lookback),
      },
    }),
  });
  log.dispatched = true;
  log.recovery_lookback_hours = lookback;
  console.log(JSON.stringify(log));
  return log;
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(checkAndRecover(env));
  },
};
