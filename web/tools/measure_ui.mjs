// 화면 부피 실측 — 390px 에서 세로 길이·글자 수·누를 수 있는 요소를 센다.
//
// 왜 있는가
// ---------------------------------------------------------------------------
// "복잡하다"는 피드백은 의견이지만 부피는 사실이다. 이 저장소는 같은 화면을 이미
// 세 번 줄였고(2026-08-03 모바일 감사 · 08-05 에디토리얼 개편 · PHASE_PLAN S4),
// 그때마다 다시 불었다. 줄었다는 말 대신 숫자를 남기려고 만든다.
//
// 새 의존성은 없다. admin_dom.mjs 와 같은 길 — 시스템에 있는 크롬을 --dump-dom
// 으로 부르고, 계측 스크립트를 주입한 index.html 을 임시 서버가 내준다.
// playwright 를 쓰지 않는 이유도 같다(배포 경로에 브라우저 내려받기 2분을
// 얹지 않는다).
//
//     node web/tools/measure_ui.mjs                 오늘 화면(구)
//     node web/tools/measure_ui.mjs "?ui=v3"        오늘 화면(v3)
//     node web/tools/measure_ui.mjs "?view=trend"   흐름 탭
//     node web/tools/measure_ui.mjs "?ui=v3" --json 기계가 읽을 형태로
//
// 주의: 로컬 서버는 압축하지 않는다. bytes 는 **압축 전** 값이다.

import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const runBrowser = promisify(execFile);
const here = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(here, "..", "public");

export const VIEWPORT = { width: 390, height: 844 };

// ── 브라우저 찾기 (admin_dom.mjs 와 같은 목록) ─────────────────────────────
//
// 못 찾으면 조용히 건너뛰지 않는다. 안 도는 검사는 없는 검사인데 초록불은
// 있는 검사처럼 보인다.
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

export function findBrowser() {
  return BROWSERS.find(candidate => fs.existsSync(candidate))
    || onPath(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
               "microsoft-edge", "chrome"]);
}

// ── 계측 스크립트 ──────────────────────────────────────────────────────────
//
// 페이지 안에서 돈다. 결과를 DOM 에 적어 두면 --dump-dom 이 그대로 실어 온다.
//
// '보이는 것'의 정의를 한 곳에 둔다 — 세 수치가 서로 다른 기준을 쓰면 표가
// 뜻을 잃는다. hidden 속성·display:none·visibility·0 크기를 전부 뺀다.
const PROBE = `
<script id="__ui_probe">
(() => {
  const OUT = "__ui_measure";
  const firstPaint = { at: null, resources: null };
  // booting 이 걷히는 순간이 이 앱의 '첫 의미 있는 렌더'다(index.html 주석).
  //
  // **그 순간에 자원 목록을 통째로 찍어 둔다.** 나중에 타임스탬프로 가르면 안 된다 —
  // --virtual-time-budget 아래에서는 performance.now() 가 가상 시계라 실제 네트워크
  // 완료 시각과 견줄 수 없다(첫 시도에서 briefings.json 이 '지연'으로 잘못 분류됐다).
  const snapshot = () => {
    firstPaint.at = performance.now();
    firstPaint.resources = performance.getEntriesByType("resource").map(entry => entry.name);
  };
  if (document.body.classList.contains("booting")) {
    const observer = new MutationObserver(() => {
      if (!document.body.classList.contains("booting")) {
        snapshot();
        observer.disconnect();
      }
    });
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  } else {
    snapshot();
  }

  const visible = (el) => {
    if (!el || el.hidden) return false;
    if (el.closest("[hidden]")) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  // 접힌 <details> 안쪽은 1단 기본 상태가 아니다 — 열어야 보이는 것은 세지 않는다.
  const collapsed = (el) => {
    const details = el.closest("details");
    return Boolean(details) && !details.open && !el.closest("summary");
  };

  function textChars() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let total = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent) continue;
      const tag = parent.tagName;
      // <select> 는 명세가 제외하라고 못박았다(옵션 목록이 글자 수를 지배한다).
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "SELECT" || tag === "OPTION") continue;
      if (parent.closest("select")) continue;
      if (!visible(parent) || collapsed(parent)) continue;
      total += node.textContent.replace(/\\s/g, "").length;
    }
    return total;
  }

  function tappables() {
    const nodes = document.body.querySelectorAll(
      'a[href], button, summary, input:not([type="hidden"]), [role="button"], [role="tab"], [onclick]');
    const seen = [];
    for (const node of nodes) {
      if (!visible(node) || collapsed(node)) continue;
      seen.push((node.tagName + "." + (node.id || node.className || "")).slice(0, 60));
    }
    return seen;
  }

  function payload() {
    const resources = performance.getEntriesByType("resource")
      .filter(entry => entry.name.includes("/data/") || entry.name.endsWith(".js") || entry.name.endsWith(".css"))
      .map(entry => ({
        name: entry.name.replace(location.origin, ""),
        bytes: entry.encodedBodySize || entry.transferSize || 0,
        endedAt: Math.round(entry.responseEnd),
      }));
    const before = new Set(firstPaint.resources || resources.map(r => location.origin + r.name));
    const isBlocking = (r) => before.has(location.origin + r.name) || before.has(r.name);
    return {
      viewport: { width: innerWidth, height: innerHeight },
      height: Math.round(document.documentElement.scrollHeight),
      chars: textChars(),
      tappableCount: tappables().length,
      tappables: tappables(),
      firstPaintMs: firstPaint.at === null ? null : Math.round(firstPaint.at),
      // 첫 렌더 시점에 **이미 목록에 있던** 요청 = 첫 화면이 실제로 기다린 바이트.
      blockingBytes: resources.filter(isBlocking).reduce((sum, r) => sum + r.bytes, 0),
      blocking: resources.filter(isBlocking).map(r => r.name),
      deferred: resources.filter(r => !isBlocking(r)).map(r => r.name),
      resources,
    };
  }

  // 앱이 설 때까지 기다린다. booting 이 걷히고도 렌더가 몇 프레임 더 남으므로
  // 여유를 준다 — 가상 시간이라 실제 대기는 없다.
  const settle = () => {
    const data = payload();
    const el = document.createElement("script");
    el.id = OUT;
    el.type = "application/json";
    el.textContent = JSON.stringify(data);
    document.body.appendChild(el);
    // 390px 뷰포트는 iframe 으로만 만들 수 있다(헤드리스 크롬은 --window-size 를
    // 무시하고 500px 로 선다). --dump-dom 은 최상위 문서만 찍으므로 결과를 올려보낸다.
    if (window.parent !== window) {
      try { window.parent.postMessage({ __uiMeasure: data }, "*"); } catch (_) {}
    }
  };
  const waitFor = (deadline) => {
    if (!document.body.classList.contains("booting") || performance.now() > deadline) {
      setTimeout(settle, 1200);
      return;
    }
    setTimeout(() => waitFor(deadline), 100);
  };
  waitFor(performance.now() + 15000);
})();
</script>
`;

// ── 390px 뷰포트를 만드는 껍데기 ───────────────────────────────────────────
//
// 헤드리스 크롬은 --window-size 를 --dump-dom 경로에서 무시하고 500×693 으로 선다
// (old·new 두 모드 모두 실측). 그런데 미디어 쿼리는 **iframe 자신의 뷰포트**를 보므로,
// 390px 짜리 iframe 안에서는 모바일 분기가 정확히 그대로 탄다. CSS 로 html 폭만
// 줄이는 길은 쓰지 않는다 — 그러면 레이아웃은 좁아지는데 미디어 쿼리는 데스크톱
// 분기를 타서, 실측이 실제 모바일 화면과 다른 것을 잰다.
function wrapper(target, width, height) {
  return `<!doctype html><meta charset="utf-8"><title>measure</title>
<style>html,body{margin:0;background:#fff}iframe{border:0;display:block}</style>
<body>
<iframe id="frame" src="${target}" width="${width}" height="${height}"></iframe>
<script>
addEventListener("message", (event) => {
  if (!event.data || !event.data.__uiMeasure) return;
  const el = document.createElement("script");
  el.id = "__ui_measure";
  el.type = "application/json";
  el.textContent = JSON.stringify(event.data.__uiMeasure);
  document.body.appendChild(el);
});
</script>
</body>`;
}

// ── 정적 서버 (admin_dom.mjs 와 같은 계약) ─────────────────────────────────
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2",
  ".png": "image/png", ".mp3": "audio/mpeg" };

export function startServer() {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname === "/__measure__") {
      response.writeHead(200, { "content-type": "text/html" });
      response.end(wrapper(url.searchParams.get("target") || "/",
        Number(url.searchParams.get("w")) || VIEWPORT.width,
        Number(url.searchParams.get("h")) || VIEWPORT.height));
      return;
    }
    let file = path.join(publicDir, decodeURIComponent(url.pathname));
    if (file.endsWith(path.sep) || (fs.existsSync(file) && fs.statSync(file).isDirectory())) {
      file = path.join(file, "index.html");
    }
    if (!path.resolve(file).startsWith(publicDir) || !fs.existsSync(file)) {
      response.writeHead(404).end("not found");
      return;
    }
    // index.html 에만 계측을 주입한다. 원본 파일은 건드리지 않는다 —
    // 배포본에 측정 코드가 실릴 길을 아예 만들지 않는다.
    if (path.extname(file) === ".html") {
      const html = fs.readFileSync(file, "utf8").replace("</body>", `${PROBE}</body>`);
      response.writeHead(200, { "content-type": "text/html" });
      response.end(html);
      return;
    }
    response.writeHead(200, { "content-type": TYPES[path.extname(file)] || "text/plain" });
    fs.createReadStream(file).pipe(response);
  });
  return server;
}

export async function measure(query = "", { timeoutMs = 90_000 } = {}) {
  const browser = findBrowser();
  if (!browser) throw new Error("크롬/엣지를 못 찾았다 — CHROME_PATH 로 알려 줄 것");

  const server = startServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "measure-ui-"));
  let html = "";
  try {
    const { stdout } = await runBrowser(browser, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
      `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
      "--disable-extensions", "--disable-background-networking", "--disable-sync",
      "--window-size=900,1000", "--force-device-scale-factor=1", "--hide-scrollbars",
      "--virtual-time-budget=25000",
      "--dump-dom",
      `http://127.0.0.1:${port}/__measure__?target=${encodeURIComponent("/" + query)}`
        + `&w=${VIEWPORT.width}&h=${VIEWPORT.height}`,
    ], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024, timeout: timeoutMs });
    html = stdout;
  } finally {
    server.close();
    fs.rmSync(profile, { recursive: true, force: true });
  }

  const match = html.match(/<script id="__ui_measure" type="application\/json">([\s\S]*?)<\/script>/);
  if (!match) {
    throw new Error(`계측 결과가 DOM 에 없다 — 페이지가 안 떴을 수 있다 (DOM ${html.length}자)`);
  }
  return JSON.parse(match[1]);
}

// ── CLI ────────────────────────────────────────────────────────────────────
if (import.meta.url === `file://${process.argv[1].replace(/\\/g, "/")}`
    || process.argv[1]?.endsWith("measure_ui.mjs")) {
  const args = process.argv.slice(2);
  const asJson = args.includes("--json");
  const query = args.find(arg => !arg.startsWith("--")) || "";
  const result = await measure(query);
  if (asJson) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    const kb = (n) => `${(n / 1024).toFixed(0)} KB`;
    console.log(`측정 대상: /${query || "(기본)"}  @ ${result.viewport.width}px`);
    console.log(`  세로 길이      ${result.height.toLocaleString()} px`);
    console.log(`  글자(공백 제외) ${result.chars.toLocaleString()} 자`);
    console.log(`  누를 수 있는 곳 ${result.tappableCount} 개`);
    console.log(`  첫 렌더        ${result.firstPaintMs} ms`);
    console.log(`  첫 렌더까지 받은 바이트(압축 전) ${kb(result.blockingBytes)}`);
    console.log(`    차단: ${result.blocking.join(", ") || "(없음)"}`);
    console.log(`    지연: ${result.deferred.join(", ") || "(없음)"}`);
  }
}
