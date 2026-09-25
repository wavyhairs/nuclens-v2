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


// ── 주간 판세 ───────────────────────────────────────────────────────────────
//
// 금요일 cron 은 이 저장소에서 배달자가 아니었다. 2026-08-21~09-18 의 예약은
// 08:07Z 인데 실제로는 08:46Z · (미발생) · 12:41Z · 12:47Z · 12:55Z 에 떴다.
// 네 주 연속 실제 발송을 끝낸 것은 전부 3시간마다 도는 crawl 의 복구 경로였고,
// 도착은 18:58~21:41, 한 번은 토요일 00:01 이었다.
//
// cron 을 더 거는 것은 답이 아니다 — 같은 지연을 똑같이 먹는다. 크롤에서 이미
// 쓰고 있는 이 Worker 가 15분마다 직접 확인해서 부른다.
const WEEKLY_MIN_GAP_MS = 30 * 60_000;
const WEEKLY_MAX_DISPATCHES = 6;
const KST_OFFSET_MS = 9 * 3_600_000;
// 주간 경계 — tools/weekly_trigger_gate.py 의 CUTOFF_HOUR/CUTOFF_MINUTE 와 같아야 한다.
const WEEKLY_CUTOFF_HOUR = 17;
const WEEKLY_CUTOFF_MINUTE = 5;

function kst(now) {
  return new Date(now.getTime() + KST_OFFSET_MS);
}

/** KST 날짜 기준 ISO 주차. weekly_bot 의 `{year}-W{week:02d}` 와 같은 값이어야 한다. */
export function isoWeekId(now) {
  const local = kst(now);
  const date = new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth(),
                                local.getUTCDate()));
  const weekday = date.getUTCDay() || 7;            // 월=1 … 일=7
  date.setUTCDate(date.getUTCDate() + 4 - weekday); // 그 주의 목요일이 연도를 정한다
  const year = date.getUTCFullYear();
  const jan1 = Date.UTC(year, 0, 1);
  const week = Math.ceil(((date.getTime() - jan1) / 86_400_000 + 1) / 7);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

/**
 * 금요일 17:05 ~ 일요일 12:00 KST. 게이트의 복구 창과 같은 자리에 선다.
 * 17:05 는 weekly_bot 의 주간 경계(tools/weekly_trigger_gate.py CUTOFF_*)다 —
 * 이 Worker 의 cron(:07)이 경계 직후 첫 호출이 된다.
 */
export function weeklyWindow(now) {
  const local = kst(now);
  const day = local.getUTCDay();          // 일=0 … 금=5, 토=6
  const hour = local.getUTCHours();
  const minute = local.getUTCMinutes();
  if (day === 5) return hour > WEEKLY_CUTOFF_HOUR
    || (hour === WEEKLY_CUTOFF_HOUR && minute >= WEEKLY_CUTOFF_MINUTE);
  if (day === 6) return true;
  return day === 0 && hour < 12;
}

/** 이번 주 개인 알림이 나갔는가. 채널은 보지 않는다 — 아래 주석 참고. */
export function weeklyDelivered(reports, week) {
  const automation = reports?.reports?.[week]?._automation;
  return String(automation?.telegram?.status || "") === "sent";
}

export function evaluateWeekly(runs, reports, now = new Date()) {
  if (!weeklyWindow(now)) return { shouldDispatch: false, state: "outside_window" };
  const week = isoWeekId(now);
  // 판정을 **개인 알림 하나로** 좁힌 것은 의도다. 채널까지 보면, 채널 배치가
  // partial 로 굳었는데 게이트는(채널 미설정이면) 완료로 읽는 조합에서 Worker 가
  // 창이 닫힐 때까지 호출을 반복한다. 사용자가 받는 것은 개인 알림이고, 채널
  // 잔여분은 crawl 복구가 게이트의 온전한 규칙으로 계속 집는다.
  if (weeklyDelivered(reports, week)) {
    return { shouldDispatch: false, state: "delivery_confirmed", week };
  }

  const recent = runs.filter((run) => weeklyWindowStart(now) <= new Date(run.created_at || 0));
  if (recent.some((run) => run.status === "queued" || run.status === "in_progress")) {
    return { shouldDispatch: false, state: "workflow_active", week };
  }
  const last = recent
    .map((run) => new Date(run.created_at || 0).getTime())
    .sort((a, b) => b - a)[0];
  if (last !== undefined && now.getTime() - last < WEEKLY_MIN_GAP_MS) {
    return { shouldDispatch: false, state: "within_retry_gap", week };
  }
  // 워크플로가 통째로 깨진 주에는(2026-09-18 의 startup_failure 처럼) 아무리
  // 불러도 안 산다. 창이 닫힐 때까지 15분마다 부르는 대신 여섯 번에서 멈추고,
  // 나머지는 crawl 복구와 운영 알림에 넘긴다.
  const dispatched = recent.filter((run) => run.event === "workflow_dispatch").length;
  if (dispatched >= WEEKLY_MAX_DISPATCHES) {
    return { shouldDispatch: false, state: "retry_budget_spent", week };
  }
  return { shouldDispatch: true, state: last === undefined ? "trigger_missing" : "delivery_unconfirmed", week };
}

export function weeklyWindowStart(now) {
  // 이번 창이 열린 금요일 17:05 KST. 지난 주 실행이 예산에 섞이지 않게 한다.
  const local = kst(now);
  const day = local.getUTCDay();
  const back = day === 5 ? 0 : (day === 6 ? 1 : 2);   // 금 0 · 토 1 · 일 2
  const start = new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth(),
                                  local.getUTCDate() - back,
                                  WEEKLY_CUTOFF_HOUR, WEEKLY_CUTOFF_MINUTE));
  return new Date(start.getTime() - KST_OFFSET_MS);
}

export async function checkAndRecoverWeekly(env, now = new Date()) {
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN Worker secret is missing");
  const owner = env.GITHUB_OWNER || "wavyhairs";
  const repo = env.GITHUB_REPO || "nuclens-v2";
  const workflow = env.GITHUB_WEEKLY_WORKFLOW || "weekly.yml";
  const base = `/repos/${owner}/${repo}/actions/workflows/${workflow}`;
  if (!weeklyWindow(now)) {
    return { weekly_state: "outside_window", checked_at: now.toISOString(), dispatched: false };
  }

  const [data, reports] = await Promise.all([
    github(env, `${base}/runs?branch=main&per_page=30`),
    // raw 로 받는다. contents API 의 base64 는 1MB 에서 빈 값이 되는데
    // weekly_reports.json 은 주마다 커진다.
    github(env, `/repos/${owner}/${repo}/contents/weekly_reports.json?ref=main`,
           { headers: { Accept: "application/vnd.github.raw" } }),
  ]);
  const runs = Array.isArray(data?.workflow_runs) ? data.workflow_runs : [];
  const decision = evaluateWeekly(runs, reports, now);
  const log = {
    weekly_state: decision.state,
    week: decision.week || "",
    checked_at: now.toISOString(),
    dispatched: false,
  };
  if (decision.shouldDispatch) {
    await github(env, `${base}/dispatches`, {
      method: "POST",
      body: JSON.stringify({
        ref: "main",
        inputs: { trigger_source: "backup_watchdog" },
      }),
    });
    log.dispatched = true;
  }
  console.log(JSON.stringify(log));
  return log;
}


export default {
  async scheduled(_controller, env, ctx) {
    // 둘을 갈라 둔다. 주간 쪽이 터져도 크롤 복구는 돌아야 한다 — 이 Worker 의
    // 첫 임무는 3시간 수집이고, 주간은 그 위에 얹은 것이다.
    ctx.waitUntil(Promise.all([
      checkAndRecover(env).catch((error) => {
        console.log(JSON.stringify({ watchdog_error: String(error) }));
      }),
      checkAndRecoverWeekly(env).catch((error) => {
        console.log(JSON.stringify({ weekly_error: String(error) }));
      }),
    ]));
  },
};
