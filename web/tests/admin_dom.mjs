// 운영 콘솔 실물 DOM 검사 — 진짜 브라우저로 한 번 그려 본다.
//
// 왜 admin_render.mjs 로 부족한가
// ---------------------------------------------------------------------------
// 저쪽은 DOM 을 흉내 낸다. 흉내는 **내가 아는 것만** 흉내 내므로, 내가 틀리게
// 아는 것은 그대로 통과시킨다. 2026-08-22 라이브가 그 값을 치렀다:
//
//   '먼저 확인할 것'의 [해당 칸 열기] 버튼도 data-fold 를 들고 있고 문서에서
//   details 보다 앞에 선다. 그래서 document.querySelector('[data-fold="story"]')
//   가 버튼을 돌려줬고, 버튼에 open=true 를 넣는 것은 임의의 속성 하나를 만드는
//   일이라 아무 일도 일어나지 않았다 — 우선 확인에 걸린 칸 셋이 통째로 안
//   그려진 채 배포됐다. 요약 줄의 '건수를 세는 중'이 그 증상이었다.
//
//   흉내 DOM 은 [data-fold="story"] 를 정규식으로 받아 **언제나 그 칸**을
//   돌려줬으므로 초록불이었다. 선택자의 모호함은 진짜 문서에만 있다.
//
// 그래서 여기서는 크롬을 띄워 실제로 그린 DOM 을 받아 본다. 새 의존성은 없다 —
// 시스템에 이미 있는 크롬/엣지를 --dump-dom 으로 부른다(러너 이미지에 크롬이
// 들어 있다). playwright 를 쓰지 않는 이유는 이 검사가 **배포 경로**에 있어야
// 하는데 브라우저 내려받기 2분을 매 배포에 얹을 이유가 없기 때문이다.
//
//     node web/tests/admin_dom.mjs        (build_data 이후)

import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

// **동기로 부르면 안 된다.** execFileSync 는 이벤트 루프를 통째로 막아서 아래
// 정적 서버가 한 요청도 못 받는다 — 크롬은 안 오는 페이지를 끝까지 기다리고,
// 증상은 '검사가 멈췄다'로만 보인다(여기서 한 번 겪었다).
const runBrowser = promisify(execFile);

const here = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(here, "..", "public");

for (const rel of ["admin/index.html", "admin/admin.js", "admin/data/merges.json"]) {
  if (!fs.existsSync(path.join(publicDir, rel))) {
    console.error(`admin dom: ${rel} 이 없다 — 먼저 python web/build_data.py`);
    process.exit(1);
  }
}

// ── 브라우저 찾기 ──────────────────────────────────────────────────────────
//
// 못 찾으면 **조용히 건너뛰지 않는다.** 안 도는 검사는 없는 검사인데, 초록불은
// 있는 검사처럼 보인다 — 그 조합이 제일 나쁘다.
const BROWSERS = [
  process.env.CHROME_PATH,
  process.env.CHROME_BIN,
  "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium-browser", "/usr/bin/chromium", "/snap/bin/chromium",
  "/usr/bin/microsoft-edge",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);

// 절대 경로로 못 찾으면 PATH 를 뒤진다. 배포판·러너 이미지마다 자리가 다르고,
// 그때마다 목록에 한 줄씩 더하는 것은 다음 사람이 또 겪는다는 뜻이다.
function onPath(names) {
  const dirs = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  const suffixes = process.platform === "win32" ? [".exe", ""] : [""];
  for (const dir of dirs) {
    for (const name of names) {
      for (const suffix of suffixes) {
        const candidate = path.join(dir, name + suffix);
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  }
  return null;
}

const browser = BROWSERS.find(candidate => fs.existsSync(candidate))
  || onPath(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
             "microsoft-edge", "chrome"]);
if (!browser) {
  console.error("admin dom: 크롬/엣지를 못 찾았다 — CHROME_PATH 로 알려 줄 것");
  process.exit(1);
}

// ── 정적 서버 ──────────────────────────────────────────────────────────────
//
// file:// 로는 안 된다. 콘솔이 부르는 경로가 절대 경로(/admin/data/…)이고
// fetch 도 file:// 에서는 막힌다.
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2" };

const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  let file = path.join(publicDir, decodeURIComponent(url.pathname));
  if (file.endsWith(path.sep) || fs.existsSync(file) && fs.statSync(file).isDirectory()) {
    file = path.join(file, "index.html");
  }
  // 경로 탈출 방지. 테스트용이라도 서버는 서버다.
  if (!path.resolve(file).startsWith(publicDir) || !fs.existsSync(file)) {
    response.writeHead(404).end("not found");
    return;
  }
  response.writeHead(200, { "content-type": TYPES[path.extname(file)] || "text/plain" });
  fs.createReadStream(file).pipe(response);
});

await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "admin-dom-"));
let html = "";
try {
  const { stdout } = await runBrowser(browser, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
    // 새 프로필은 첫 실행 절차를 부른다 — 그러면 --dump-dom 이 영영 안 돌아온다.
    `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-background-networking", "--disable-sync",
    // 콘솔은 JSON 두 벌을 받고 나서야 그린다 — 가상 시간으로 그 뒤까지 민다.
    "--virtual-time-budget=9000",
    "--dump-dom", `http://127.0.0.1:${port}/admin/`,
    // 브라우저가 안 돌아오는 날에도 배포는 멈추지 않아야 한다. 멈추면
    // '검사가 오래 걸린다'가 아니라 '배포가 죽었다'로 나타난다.
  ], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout: 90_000 });
  html = stdout;
} finally {
  server.close();
  fs.rmSync(profile, { recursive: true, force: true });
}

// ── 검사 ───────────────────────────────────────────────────────────────────

assert.ok(html.length > 5000, `DOM 이 너무 짧다(${html.length}자) — 페이지가 안 떴다`);
assert.ok(!html.includes("운영 데이터를 불러오지 못했습니다"), "콘솔이 데이터를 못 읽었다");

// 회차 줄. 이게 없으면 그 아래가 통째로 의미를 잃는다.
assert.ok(html.includes('data-act="round-pick"'), "회차 고르는 자리가 안 그려졌다");
const options = [...html.matchAll(/<option value="(\d{4}-\d{2}-\d{2})"[^>]*>([^<]*)</g)];
assert.ok(options.length > 0, "회차 목록이 비었다");
for (const [, date, label] of options) {
  assert.ok(/판단 [\d,]+건/.test(label),
    `회차 옵션이 판단 건수를 안 적는다: ${date} → "${label}"`);
  // 후보 더미를 판단에 더하면 더미가 판단을 덮는다(라이브에서 29,305건이 그랬다).
  assert.ok(!/^[^·]*·\s*[\d,]+건\s*$/.test(label),
    `회차 옵션이 네 계층을 한 숫자로 뭉쳤다: "${label}"`);
}

// ── 접히는 칸 ──────────────────────────────────────────────────────────────
//
// 여기가 이 파일의 존재 이유다. 요약 줄은 **언제나 건수**여야 한다 — 'N건'이
// 안 보이면 그 칸은 안 그려진 것이고, 화면은 그것을 '오늘은 아무것도 없다'로
// 보여 준다.
const folds = [...html.matchAll(
  /<details class="admin-fold" data-fold="([a-z]+)"([^>]*)>\s*<summary><span data-role="count">([^<]*)</g)];
assert.equal(folds.length, 4, `접히는 칸이 4개가 아니다: ${folds.length}`);
assert.ok(!html.includes("건수를 세는 중"),
  "요약 줄이 placeholder 그대로다 — 칸을 못 찾았거나 그리기 전에 예외가 났다");

const shown = new Map();
for (const [, name, attrs, summary] of folds) {
  assert.ok(/[\d,]+건/.test(summary), `${name} 칸이 건수를 안 적었다: "${summary}"`);
  shown.set(name, { open: /\sopen\b/.test(attrs), summary });
}

// '먼저 확인할 것'이 가리킨 칸은 **열려 있어야** 한다. 접힌 칸으로 데려다 놓기만
// 하면 운영자는 거기서 한 번 더 눌러야 하고, 애초에 그 줄을 만든 이유가 사라진다.
// (그리고 이 검사가 곧 선택자 모호함의 덫이다 — 버튼을 칸으로 착각하면 여기서 걸린다.)
const flagged = new Set([...html.matchAll(/data-act="fold-jump" data-fold="([a-z]+)"/g)]
  .map(match => match[1]));
for (const name of flagged) {
  const fold = shown.get(name);
  assert.ok(fold, `우선 확인이 없는 칸을 가리킨다: ${name}`);
  assert.ok(fold.open, `우선 확인에 걸린 '${name}' 칸이 닫혀 있다 — 열기가 칸에 안 닿았다`);
  assert.ok(fold.summary.includes("접기"), `'${name}' 요약이 열림과 어긋난다: "${fold.summary}"`);
}

// 열린 칸에는 실제로 무언가 서 있어야 한다. 빈 회차면 empty-state 가 선다.
const drew = html.includes('class="admin-card"') || html.includes('class="empty-state"')
  || html.includes("admin-table");
assert.ok(drew, "펼친 칸에 카드도 빈 화면도 없다");

const open = [...shown].filter(([, fold]) => fold.open).map(([name]) => name);
console.log(`admin dom: 실물 렌더 통과 (회차 ${options.length}개 · 칸 4개`
  + `${open.length ? ` · 열림 ${open.join("·")}` : " · 전부 접힘"}`
  + `${flagged.size ? ` · 우선 확인 ${[...flagged].join("·")}` : ""})`);
