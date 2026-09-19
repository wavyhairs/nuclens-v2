// 구독 목록 — 발송기(tools/push_notify.py, GitHub Actions)만 읽는다.
//
// 왜 목록 창구가 생겼나
// ---------------------
// 예전엔 이 자리가 없었다. 발송을 엣지(`/push/send`)가 했으니 목록이 바깥으로
// 나갈 이유가 없었고, "구독자를 보여 줘"를 만들지 않는 것 자체가 방어였다.
// 발송이 파이썬으로 옮겨 오면서 목록은 나가야 한다 — 대신 **토큰을 건다.**
//
// endpoint 는 그 브라우저를 특정하는 주소다. 그래서 여기만 인증이 있고
// (subscribe 는 독자가 쓰는 길이라 못 건다), 토큰이 안 맞으면 401,
// 토큰이 아예 없는 배포면 503 이다 — "설정 안 함"과 "틀린 열쇠"를 가른다.
//
// 토큰은 Cloudflare Pages 시크릿과 GitHub Secret 에 **같은 값**으로 넣는다.
// 저장소가 공개라 wrangler 설정이나 변수에는 두지 않는다.

import { json, pushStore, KEY_PREFIX, timingSafeEqual } from "./_shared.js";

// KV list 의 한 장. 구독이 많아지면 커서로 이어 받는다 — 한 요청의 일이
// 구독자 수와 함께 무한정 자라지 않게 하는 상한이다.
const PAGE_LIMIT = 200;

function authorized(request, env) {
  const expected = String((env && env.PUSH_ADMIN_TOKEN) || "");
  const header = String(request.headers.get("Authorization") || "");
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  return timingSafeEqual(supplied, expected);
}

export async function onRequestGet({ request, env }) {
  // 짧은 토큰은 설정 안 된 것으로 본다. 빈 값을 받아 주면 인증이 사실상 없다.
  if (String((env && env.PUSH_ADMIN_TOKEN) || "").length < 16) {
    return json({ error: "push_admin_token_missing" }, 503);
  }
  if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
  const kv = pushStore(env);
  if (!kv) return json({ error: "push_store_missing" }, 503);

  const subscriptions = [];
  let cursor;
  do {
    const page = await kv.list({ prefix: KEY_PREFIX, limit: PAGE_LIMIT, cursor });
    for (const entry of page.keys) {
      const raw = await kv.get(entry.name);
      if (!raw) continue;
      try {
        const parsed = JSON.parse(raw);
        // 발송에 필요한 것만 내보낸다 — 저장해 둔 seen_at 까지 나갈 이유가 없다.
        if (parsed && parsed.endpoint && parsed.keys) {
          subscriptions.push({ endpoint: parsed.endpoint, keys: parsed.keys });
        }
      } catch {
        // 깨진 항목은 건너뛴다. 여기서 지우지는 않는다 — 읽기 창구가 쓰기를
        // 하면 토큰 하나로 목록을 지울 수 있게 된다.
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);

  return json({ count: subscriptions.length, subscriptions });
}
