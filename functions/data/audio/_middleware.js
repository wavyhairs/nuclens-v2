// 오디오에만 Range 를 붙여 주는 창구.
//
// 왜 있는가
// ---------
// Cloudflare Pages 의 정적 자산 응답에는 `Accept-Ranges` 가 없고, `Range` 를
// 보내도 206 이 아니라 **200 에 전체 본문**이 온다(실측 2026-08-19: 8.85MB 짜리
// 전문가 브리핑에 `bytes=100000-100999` 를 요청해도 8,846,253 바이트가 왔다.
// /app.js·/fonts/* 도 같다 — 이 배포 전체의 성질이지 _headers 탓이 아니다).
//
// 그러면 브라우저는 **아직 받지 않은 지점으로 재생 위치를 옮기지 못한다.**
// seekable 이 받아 둔 앞부분까지밖에 없어서, 그 밖으로 옮기면 탐색을 물리고
// 원래 자리로 되돌린다. 1.87MB 짜리 빠른 브리핑(156초)은 금세 전부 받아져 티가
// 안 나고, 8.85MB 짜리 전문가 브리핑(552초)만 커서가 튕긴다 — 사용자가 본
// 증상이 정확히 이것이다.
//
// 어떻게
// ------
// 정적 자산을 그대로 받아 잘라서 돌려준다. mp3 는 CBR(`-b:a`, audio_brief.to_mp3)
// 이라 바이트 위치와 시간이 선형으로 대응하고, 그래서 Range 만 서면 탐색이
// 정확해진다. 파일은 10MB 안쪽이고 워커 메모리는 128MB 다.
//
// 이 미들웨어는 `/data/audio/*` 밖으로 나가지 않으며, 그 안에서도 `audio/*` 가
// 아닌 것(audio.json · script-*.txt)은 손대지 않고 흘려보낸다.

// 이보다 큰 파일은 통째로 메모리에 올리지 않고 원래 응답을 그대로 준다.
// 지금 가장 긴 회차가 8.85MB 이므로 한참 위에 둔 안전선이다.
const MAX_BUFFER_BYTES = 32 * 1024 * 1024;

// `bytes=0-499` · `bytes=500-` · `bytes=-500` 세 어법만 받는다. 여러 구간을
// 쉼표로 잇는 어법은 미디어 요소가 쓰지 않으므로 받지 않고 전체를 돌려준다.
function parseRange(header, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(String(header || "").trim());
  if (!match) return null;
  const [, rawStart, rawEnd] = match;
  if (rawStart === "" && rawEnd === "") return null;

  let start;
  let end;
  if (rawStart === "") {
    const suffix = Number(rawEnd);
    if (!Number.isFinite(suffix) || suffix <= 0) return { unsatisfiable: true };
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(rawStart);
    end = rawEnd === "" ? size - 1 : Number(rawEnd);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return { unsatisfiable: true };
    end = Math.min(end, size - 1);
  }
  if (start > end || start >= size || start < 0) return { unsatisfiable: true };
  return { start, end };
}

export async function onRequest(context) {
  const { request, next } = context;
  const response = await next();
  if (response.status !== 200) return response;
  if (!(response.headers.get("Content-Type") || "").startsWith("audio/")) return response;

  // HEAD 는 본문이 없다 — 버퍼링하면 Content-Length 를 0 으로 덮어써 거짓말이
  // 된다. 여기서는 "Range 를 받는다"는 사실만 알리고 지나간다.
  if (request.method === "HEAD") {
    const headers = new Headers(response.headers);
    headers.set("Accept-Ranges", "bytes");
    return new Response(null, { status: 200, headers });
  }

  const declared = Number(response.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > MAX_BUFFER_BYTES) return response;

  const buffer = await response.arrayBuffer();
  const size = buffer.byteLength;
  const headers = new Headers(response.headers);
  headers.set("Accept-Ranges", "bytes");
  // 길이는 우리가 들고 있는 바이트 수로 다시 적는다. 위에서 arrayBuffer() 가
  // 압축을 이미 풀었으므로 원래의 인코딩 표시를 남겨 두면 앞뒤가 안 맞는다.
  headers.delete("Content-Encoding");

  const range = parseRange(request.headers.get("Range"), size);
  if (!range) {
    headers.set("Content-Length", String(size));
    return new Response(buffer, { status: 200, headers });
  }
  if (range.unsatisfiable) {
    headers.set("Content-Range", `bytes */${size}`);
    headers.set("Content-Length", "0");
    return new Response(null, { status: 416, headers });
  }

  const slice = buffer.slice(range.start, range.end + 1);
  headers.set("Content-Range", `bytes ${range.start}-${range.end}/${size}`);
  headers.set("Content-Length", String(slice.byteLength));
  return new Response(slice, { status: 206, headers });
}
