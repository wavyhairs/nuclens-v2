// 운영 콘솔 접근 통제 — Cloudflare Pages Function 미들웨어.
//
// 왜 서버(엣지)에서 막는가
// ------------------------
// /admin 은 정적 파일이다. 화면 자바스크립트로 비밀번호를 물으면 파일 자체는 그대로
// 공개돼 있어서 URL 을 직접 치거나 개발자 도구를 열면 그냥 보인다. 진단 데이터에는
// 어떤 기사가 왜 접혔는지, 어떤 매체를 어떤 등급으로 보는지가 전부 들어 있다 —
// 화면만 가리는 것은 가린 게 아니다. 이 파일은 `/admin/*` 로 오는 **모든** 요청
// (HTML·JS·CSS·JSON) 앞에 서서, 통과하지 못하면 next() 를 아예 부르지 않는다.
//
// 그래서 admin 데이터도 /data 가 아니라 /admin/data 아래에 둔다. /data 는 독자
// 화면이 쓰는 공개 경로라 이 미들웨어가 닿지 않는다 (web/build_data.py 참조).
//
// 비밀번호는 KV 에 산다
// ---------------------
// 화면에서 비밀번호를 바꾸려면 바뀐 값을 쓸 곳이 있어야 한다. 환경변수는 Function 이
// 읽기만 할 수 있으므로 KV 네임스페이스 하나를 바인딩한다(`ADMIN_KV`). 세션 서명 키도
// KV 가 처음 쓸 때 스스로 만든다 — 설정할 환경변수가 하나도 남지 않는다.
//
//   Cloudflare 대시보드 → Workers & Pages → KV → Create namespace
//   → Pages 프로젝트 → Settings → Bindings → KV namespace, 변수명 `ADMIN_KV`
//
// 0000 은 **부트스트랩**이지 비밀번호가 아니다
// -------------------------------------------
// KV 에 비밀번호 기록이 없으면 `0000` 으로 들어올 수 있다. 대신 그 상태에서는 콘솔이
// 열리지 않고 비밀번호 변경 화면만 나온다 — 진단 화면도, 데이터 JSON 도 막힌다.
// 0000 은 경우의 수가 1만 가지고 /admin/login 은 인터넷에 열린 POST 엔드포인트라,
// 그대로 두면 자동화된 시도 앞에서 잠긴 문이 아니다. 여기서 유출은 문제가 아니다 —
// 추측이 문제다. 그래서 0000 이 방치될 수 있는 경로를 아예 만들지 않는다.
//
// 비밀번호를 잊었다면 대시보드에서 KV 의 `admin:password` 키를 지운다 → 다시 0000.

const KV_PASSWORD_KEY = "admin:password";
const KV_SECRET_KEY = "admin:session-secret";
const KV_FAIL_PREFIX = "admin:fail:";

const BOOTSTRAP_PASSWORD = "0000";
const MIN_PASSWORD_LENGTH = 8;

const COOKIE_NAME = "nuclens_admin";
const SESSION_TTL_SECONDS = 60 * 60 * 8; // 8시간 — 하루 업무 안에서 한 번만 묻는다
const FAILED_LOGIN_DELAY_MS = 400;
const MAX_FAILED_ATTEMPTS = 10;
const LOCKOUT_WINDOW_SECONDS = 900; // 15분

const encoder = new TextEncoder();

// ── 인코딩 도우미 ──────────────────────────────────────────────────────────

function bytesToBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBytes(value) {
  const normalized = String(value).replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// 길이가 다르면 즉시 false 를 내도 된다 — 길이는 비밀이 아니다. 같은 길이일 때
// 바이트마다 조기 종료하지 않는 것이 중요하다.
function timingSafeEqual(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left[i] ^ right[i];
  return diff === 0;
}

// ── 비밀번호 해시 ──────────────────────────────────────────────────────────
//
// 형식: pbkdf2-sha256$<iterations>$<salt-b64url>$<hash-b64url>
//
// 반복 횟수가 왜 5,000 인가 — Pages Function 은 Worker 라 무료 플랜의 요청당 CPU
// 예산이 10ms 다. 20만 회 같은 통상적인 값은 그 예산을 넘겨 로그인 자체가 실패한다.
// 그래서 여기서 실제 방어를 맡는 것은 KDF 가 아니라 **8자 하한 + 시도 제한**이다.
// KDF 는 저장된 다이제스트가 새어 나갔을 때를 위한 겹겹의 방어다.
export const PBKDF2_ITERATIONS = 5000;

async function deriveBits(password, salt, iterations, lengthBytes) {
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(password), "PBKDF2", false, ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, lengthBytes * 8,
  );
  return new Uint8Array(bits);
}

export async function hashPassword(password, iterations = PBKDF2_ITERATIONS) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const digest = await deriveBits(password, salt, iterations, 32);
  return `pbkdf2-sha256$${iterations}$${bytesToBase64Url(salt)}$${bytesToBase64Url(digest)}`;
}

export async function verifyPassword(password, encoded) {
  if (typeof password !== "string" || password.length === 0) return false;
  const parts = String(encoded || "").split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2-sha256") return false;
  const iterations = Number(parts[1]);
  if (!Number.isInteger(iterations) || iterations < 1000 || iterations > 1000000) return false;
  let salt;
  let expected;
  try {
    salt = base64UrlToBytes(parts[2]);
    expected = base64UrlToBytes(parts[3]);
  } catch {
    return false;
  }
  if (expected.length < 16) return false;
  return timingSafeEqual(await deriveBits(password, salt, iterations, expected.length), expected);
}

// 새 비밀번호가 갖춰야 할 조건. 통과하면 "" 를, 아니면 사용자에게 보일 이유를 낸다.
export function passwordProblem(next, confirm) {
  if (typeof next !== "string" || next.length === 0) return "새 비밀번호를 입력하세요.";
  if (next !== confirm) return "새 비밀번호 두 입력이 서로 다릅니다.";
  if (next.length < MIN_PASSWORD_LENGTH) {
    return `새 비밀번호는 ${MIN_PASSWORD_LENGTH}자 이상이어야 합니다.`;
  }
  if (next === BOOTSTRAP_PASSWORD) return "기본 비밀번호는 다시 쓸 수 없습니다.";
  return "";
}

// ── 세션 쿠키 ──────────────────────────────────────────────────────────────
//
// 서버에 세션 목록을 두지 않으므로 쿠키 자체에 만료시각을 담고 HMAC 으로 서명한다.
// 값은 `v1.<만료 epoch 초>.<서명>` — 만료를 고쳐 쓰면 서명이 깨진다.

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
}

export async function signSession(secret, expiresAt) {
  const signature = await crypto.subtle.sign(
    "HMAC", await hmacKey(secret), encoder.encode(`v1.${expiresAt}`),
  );
  return `v1.${expiresAt}.${bytesToBase64Url(new Uint8Array(signature))}`;
}

export async function sessionIsValid(token, secret) {
  if (!token || !secret) return false;
  const parts = String(token).split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return false;
  const expiresAt = Number(parts[1]);
  if (!Number.isInteger(expiresAt) || expiresAt * 1000 <= Date.now()) return false;
  const expected = await signSession(secret, expiresAt);
  return timingSafeEqual(encoder.encode(expected), encoder.encode(String(token)));
}

// 서명 키는 KV 가 스스로 만든다 — 사람이 설정할 환경변수를 하나도 남기지 않기 위해서다.
// 비밀번호를 바꿀 때 이 키를 갈아 끼우면 다른 기기의 세션이 전부 끊긴다(rotate=true).
async function sessionSecret(kv, { rotate = false } = {}) {
  if (!rotate) {
    const existing = await kv.get(KV_SECRET_KEY);
    if (existing) return existing;
  }
  const created = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  await kv.put(KV_SECRET_KEY, created);
  return created;
}

function readCookie(request, name) {
  const header = request.headers.get("Cookie") || "";
  for (const chunk of header.split(";")) {
    const [key, ...rest] = chunk.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return "";
}

function sessionCookie(value, maxAge) {
  // Path=/admin — 독자 화면 요청에는 이 쿠키가 아예 실리지 않는다.
  // HttpOnly 라 화면 스크립트가 읽지 못하고, __Host- 접두사는 Path=/ 를 요구하므로
  // 쓰지 않는다(경로를 좁히는 쪽을 택했다).
  return `${COOKIE_NAME}=${value}; Path=/admin; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

// ── 시도 제한 ──────────────────────────────────────────────────────────────
//
// KV 는 최종적 일관성이라 읽은 값이 최대 1분까지 낡을 수 있다 — 짧은 순간의 폭주는
// 이 카운터를 앞지른다. 그래서 이것만으로 0000 을 지킬 수 있다고 보지 않는다.
// 0000 을 부트스트랩으로만 두고 강제로 바꾸게 하는 것이 실제 방어이고, 이 카운터는
// **바꾼 뒤의** 비밀번호를 상대로 한 지속적인 추측을 비싸게 만드는 장치다.

function clientKey(request) {
  return KV_FAIL_PREFIX + (request.headers.get("CF-Connecting-IP") || "unknown");
}

async function failureCount(kv, key) {
  const raw = await kv.get(key);
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

// ── 화면 ───────────────────────────────────────────────────────────────────

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

// 다음 목적지는 **같은 사이트의 /admin 경로만** 허용한다. 사용자가 준 값을 그대로
// Location 에 넣으면 열린 리다이렉트가 된다.
export function safeNext(value) {
  const candidate = String(value || "");
  if (!candidate.startsWith("/admin")) return "/admin/";
  if (candidate.startsWith("//")) return "/admin/";
  return candidate;
}

function page(title, body, status) {
  return new Response(`<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="light dark">
<title>${escapeHtml(title)}</title>
<style>
  :root { color-scheme: light dark; --ink:#12251e; --paper:#f6f5f1; --line:#d9d6cd; --accent:#1f6f52; }
  @media (prefers-color-scheme: dark) { :root { --ink:#eceae4; --paper:#12140f; --line:#333; --accent:#7fd6b0; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:24px;
         background:var(--paper); color:var(--ink);
         font-family:Pretendard,-apple-system,"Segoe UI",system-ui,sans-serif; }
  main { width:min(420px,100%); border:1px solid var(--line); border-radius:14px;
         padding:28px; background:color-mix(in srgb, var(--paper) 85%, #fff); }
  h1 { font-size:1.1rem; margin:0 0 4px; letter-spacing:-0.01em; }
  p  { margin:0 0 18px; font-size:0.86rem; line-height:1.6; opacity:0.75; }
  label { display:block; font-size:0.78rem; margin:14px 0 6px; font-weight:600; }
  input { width:100%; padding:11px 12px; font-size:1rem; border-radius:9px;
          border:1px solid var(--line); background:transparent; color:inherit; }
  button { width:100%; margin-top:18px; padding:11px; font-size:0.92rem; font-weight:600;
           border:0; border-radius:9px; background:var(--accent); color:#fff; cursor:pointer; }
  .err { margin:0 0 14px; padding:10px 12px; border-radius:9px; font-size:0.82rem;
         background:color-mix(in srgb, #c0392b 16%, transparent); color:inherit; }
  .note { margin:0 0 14px; padding:10px 12px; border-radius:9px; font-size:0.82rem;
          background:color-mix(in srgb, #d68910 16%, transparent); color:inherit; }
  code { font-size:0.78rem; word-break:break-all; }
  a { color:var(--accent); font-size:0.8rem; }
</style>
</head><body><main>${body}</main></body></html>`, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
      "Referrer-Policy": "no-referrer",
    },
  });
}

function loginPage(next, error, status) {
  return page("운영 콘솔 로그인 · Nuclens⁺", `
    <h1>운영 콘솔</h1>
    <p>관리자 전용 화면입니다. 비밀번호를 입력하세요.</p>
    ${error ? `<p class="err">${escapeHtml(error)}</p>` : ""}
    <form method="POST" action="/admin/login">
      <input type="hidden" name="next" value="${escapeHtml(next)}">
      <label for="password">비밀번호</label>
      <input id="password" name="password" type="password" autocomplete="current-password"
             autofocus required>
      <button type="submit">들어가기</button>
    </form>
    <p style="margin:18px 0 0"><a href="/">← 서비스 화면으로</a></p>`, status);
}

function passwordPage({ bootstrap, error, status, done }) {
  return page("비밀번호 변경 · Nuclens⁺", `
    <h1>비밀번호 변경</h1>
    ${bootstrap
      ? `<p class="note">지금은 기본 비밀번호(<code>0000</code>)로 들어와 있습니다.
           바꾸기 전에는 콘솔이 열리지 않습니다 — 0000 은 경우의 수가 1만 가지라
           공개 주소에서는 잠긴 문이 아닙니다.</p>`
      : "<p>바꾸면 다른 기기의 로그인은 모두 끊깁니다.</p>"}
    ${error ? `<p class="err">${escapeHtml(error)}</p>` : ""}
    ${done ? '<p class="note">비밀번호를 바꿨습니다.</p>' : ""}
    <form method="POST" action="/admin/password">
      <label for="current">현재 비밀번호</label>
      <input id="current" name="current" type="password" autocomplete="current-password"
             ${bootstrap ? 'placeholder="0000"' : ""} autofocus required>
      <label for="next">새 비밀번호 <small>(${MIN_PASSWORD_LENGTH}자 이상)</small></label>
      <input id="next" name="next" type="password" autocomplete="new-password"
             minlength="${MIN_PASSWORD_LENGTH}" required>
      <label for="confirm">한 번 더</label>
      <input id="confirm" name="confirm" type="password" autocomplete="new-password"
             minlength="${MIN_PASSWORD_LENGTH}" required>
      <button type="submit">바꾸기</button>
    </form>
    ${bootstrap ? "" : '<p style="margin:18px 0 0"><a href="/admin/">← 콘솔로 돌아가기</a></p>'}`,
  status);
}

function setupPage() {
  return page("운영 콘솔 설정 필요 · Nuclens⁺", `
    <h1>운영 콘솔이 잠겨 있습니다</h1>
    <p>비밀번호를 보관할 KV 네임스페이스가 아직 연결되지 않았습니다. 연결 전에는
       열지 않습니다 — 설정을 깜빡한 것이 곧 공개가 되면 안 되기 때문입니다.</p>
    <p>Cloudflare 대시보드 → <strong>Workers &amp; Pages → KV</strong> 에서
       네임스페이스를 하나 만들고, Pages 프로젝트 →
       <strong>Settings → Bindings</strong> 에서 변수명 <code>ADMIN_KV</code> 로
       연결한 뒤 다시 배포하세요. 첫 로그인 비밀번호는 <code>0000</code> 이고,
       들어가자마자 바꾸게 됩니다.</p>
    <p><a href="/">← 서비스 화면으로</a></p>`, 503);
}

function jsonError(message, status) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
}

function redirect(location, cookie) {
  const headers = { Location: location, "Cache-Control": "no-store" };
  if (cookie) headers["Set-Cookie"] = cookie;
  return new Response(null, { status: 303, headers });
}

// ── 미들웨어 ───────────────────────────────────────────────────────────────

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const path = url.pathname;

  const kv = env.ADMIN_KV;
  if (!kv || typeof kv.get !== "function") return setupPage();

  // 비밀번호 기록이 없으면 아직 부트스트랩 상태다 — 0000 으로 들어오되 콘솔은 안 열린다.
  const storedHash = await kv.get(KV_PASSWORD_KEY);
  const bootstrap = !storedHash;
  const secret = await sessionSecret(kv);
  const authenticated = await sessionIsValid(readCookie(request, COOKIE_NAME), secret);
  const isDataRequest = path.startsWith("/admin/data/");

  if (path === "/admin/logout") {
    return redirect("/", sessionCookie("", 0));
  }

  if (path === "/admin/login") {
    if (request.method !== "POST") {
      return authenticated ? redirect("/admin/") : loginPage(safeNext(url.searchParams.get("next")), "", 200);
    }
    const failKey = clientKey(request);
    if (await failureCount(kv, failKey) >= MAX_FAILED_ATTEMPTS) {
      return loginPage("/admin/", "시도가 너무 많습니다. 15분 뒤에 다시 하세요.", 429);
    }
    let form;
    try {
      form = await request.formData();
    } catch {
      return loginPage("/admin/", "요청을 읽지 못했습니다.", 400);
    }
    const target = safeNext(form.get("next"));
    const supplied = form.get("password");
    const ok = bootstrap
      ? supplied === BOOTSTRAP_PASSWORD
      : await verifyPassword(supplied, storedHash);
    if (ok) {
      await kv.delete(failKey);
      const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
      const token = await signSession(secret, expiresAt);
      return redirect(bootstrap ? "/admin/password" : target,
                      sessionCookie(token, SESSION_TTL_SECONDS));
    }
    await kv.put(failKey, String(await failureCount(kv, failKey) + 1),
                 { expirationTtl: LOCKOUT_WINDOW_SECONDS });
    // 추측을 조금이라도 비싸게 만든다. setTimeout 은 대기이지 CPU 사용이 아니라
    // Worker 의 CPU 예산을 쓰지 않는다.
    await new Promise(resolve => setTimeout(resolve, FAILED_LOGIN_DELAY_MS));
    return loginPage(target, "비밀번호가 올바르지 않습니다.", 401);
  }

  if (!authenticated) {
    // 데이터 요청(JSON)에까지 로그인 HTML 을 돌려주면 화면이 파싱 오류로 죽어
    // '데이터가 없다'로 오독된다. 무엇 때문에 막혔는지 그대로 말한다.
    if (isDataRequest) return jsonError("unauthorized", 401);
    return loginPage(safeNext(path + url.search), "", 401);
  }

  if (path === "/admin/password") {
    if (request.method !== "POST") {
      return passwordPage({ bootstrap, error: "", status: 200, done: false });
    }
    let form;
    try {
      form = await request.formData();
    } catch {
      return passwordPage({ bootstrap, error: "요청을 읽지 못했습니다.", status: 400, done: false });
    }
    const current = form.get("current");
    const currentOk = bootstrap
      ? current === BOOTSTRAP_PASSWORD
      : await verifyPassword(current, storedHash);
    if (!currentOk) {
      await new Promise(resolve => setTimeout(resolve, FAILED_LOGIN_DELAY_MS));
      return passwordPage({ bootstrap, error: "현재 비밀번호가 올바르지 않습니다.", status: 401, done: false });
    }
    const problem = passwordProblem(form.get("next"), form.get("confirm"));
    if (problem) {
      return passwordPage({ bootstrap, error: problem, status: 400, done: false });
    }
    await kv.put(KV_PASSWORD_KEY, await hashPassword(String(form.get("next"))));
    // 서명 키를 갈아 끼워 다른 기기의 세션을 끊고, 이 브라우저에는 새 쿠키를 준다.
    const rotated = await sessionSecret(kv, { rotate: true });
    const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
    return redirect("/admin/",
                    sessionCookie(await signSession(rotated, expiresAt), SESSION_TTL_SECONDS));
  }

  // 부트스트랩 상태에서는 로그인해도 콘솔이 열리지 않는다. 0000 이 방치될 수 있는
  // 경로를 남기지 않기 위해서다 — 데이터 JSON 도 함께 막는다.
  if (bootstrap) {
    if (isDataRequest) return jsonError("password_change_required", 403);
    return passwordPage({ bootstrap: true, error: "", status: 403, done: false });
  }

  const response = await next();
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", "no-store");
  headers.set("X-Robots-Tag", "noindex, nofollow");
  headers.set("Referrer-Policy", "no-referrer");
  return new Response(response.body, { status: response.status, headers });
}
