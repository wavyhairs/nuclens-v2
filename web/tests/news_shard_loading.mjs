// 기사 피드 로딩 — 배포 전환 창에서 **틀린 세대를 섞지 않고** 다시 맞춘다.
// 실행: node web/tests/news_shard_loading.mjs  (의존성 없음)
//
// 2026-09-30: 배포 직후 manifest 는 새 세대, shard 는 옛 세대로 응답해
// `news/000.json declared=2142 actual=2161` 이 났다. shard 이름에 내용 지문을
// 실은 뒤로(build_data.write_news_payload) 그 창의 실패는 '틀린 데이터'가 아니라
// '아직 없음(404)'이다. 로더는 잠깐 뒤 **manifest 부터** 다시 받아야 한다 —
// shard 만 다시 받으면 옛 manifest 가 가리키던 이름을 또 찾는다.
//
// app.js 는 모듈이 아니라서 함수 블록만 잘라 평가한다(date_window.mjs 와 같은 방식).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = readFileSync(
  fileURLToPath(new URL("../public/app.js", import.meta.url)), "utf8");

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`app.js 에 ${name}() 이 없다 — 이름이 바뀌었으면 이 검사도 같이 고쳐라`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && (depth -= 1) === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name}() 블록이 안 닫힌다`);
}

function loaderWith(responses) {
  const asked = [];
  const loadJSON = async name => {
    asked.push(name);
    const queue = responses[name];
    const next = Array.isArray(queue) ? queue.shift() : queue;
    if (next === undefined || next instanceof Error) throw next || new Error(`${name} 404`);
    return next;
  };
  const { loadNewsPayload } = new Function("loadJSON", `
    const NEWS_RETRY_DELAY_MS = 0;
    async ${extract("loadNewsShards")}
    async ${extract("loadNewsPayload")}
    return { loadNewsPayload };
  `)(loadJSON);
  return { loadNewsPayload, asked };
}

const cases = [];
const eq = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  cases.push({ label, got, want, ok });
};

const oldManifest = { shards: [{ file: "news/000-aaaaaaaaaaaa.json" }] };
const newManifest = { shards: [{ file: "news/000-bbbbbbbbbbbb.json" }] };

{
  // 첫 manifest 가 가리킨 shard 가 아직 없다 → manifest 부터 다시 → 새 세대로 완주.
  const { loadNewsPayload, asked } = loaderWith({
    "news-manifest.json": [oldManifest, newManifest],
    "news/000-bbbbbbbbbbbb.json": [[{ id: 1 }, { id: 2 }]],
  });
  eq("전환 창 404 뒤 새 세대로 회복", await loadNewsPayload(), [{ id: 1 }, { id: 2 }]);
  eq("manifest 부터 다시 받는다", asked.filter(n => n === "news-manifest.json").length, 2);
}

{
  // 정상 경로는 한 번에 끝난다 — 재시도가 평소 요청을 늘리지 않는다.
  const { loadNewsPayload, asked } = loaderWith({
    "news-manifest.json": [newManifest],
    "news/000-bbbbbbbbbbbb.json": [[{ id: 1 }]],
  });
  eq("정상 경로 결과", await loadNewsPayload(), [{ id: 1 }]);
  eq("정상 경로 요청 수", asked.length, 2);
}

{
  // 둘 다 실패하고 legacy news.json 도 없으면 **원래 실패**가 올라온다.
  const { loadNewsPayload } = loaderWith({ "news-manifest.json": [new Error("news-manifest.json 503"), new Error("news-manifest.json 503")] });
  let message = "";
  try { await loadNewsPayload(); } catch (error) { message = error.message; }
  eq("legacy 404 가 원래 실패를 덮지 않는다", message, "news-manifest.json 503");
}

{
  // 옛 배포 세대가 legacy 파일만 들고 있으면 그것으로 뜬다(기존 계약).
  const { loadNewsPayload } = loaderWith({ "news.json": [[{ id: 9 }]] });
  eq("legacy 폴백 유지", await loadNewsPayload(), [{ id: 9 }]);
}

const failed = cases.filter(c => !c.ok);
for (const c of cases) console.log(`${c.ok ? "ok  " : "FAIL"} ${c.label}`);
if (failed.length) {
  for (const c of failed) console.error(`  ${c.label}: got ${JSON.stringify(c.got)} want ${JSON.stringify(c.want)}`);
  process.exit(1);
}
console.log(`news shard loading: ${cases.length} OK`);
