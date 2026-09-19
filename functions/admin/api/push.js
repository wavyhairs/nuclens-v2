// 운영 콘솔이 보는 아침 알림 구독 수 — **숫자 하나만** 낸다.
//
// 왜 /push/list 를 안 쓰나
// ------------------------
// 그쪽은 발송기(GitHub Actions)가 `PUSH_ADMIN_TOKEN` 으로 여는 창구이고, 구독
// endpoint 전문을 돌려준다. 콘솔이 그걸 부르려면 토큰을 브라우저에 내려보내야
// 하는데, 그 토큰은 **구독자 전원에게 알림을 보낼 수 있는 열쇠**다. 화면 하나에
// 숫자를 띄우자고 그 열쇠를 화면으로 내리지 않는다.
//
// 그리고 콘솔에 필요한 것은 숫자뿐이다. endpoint 는 그 브라우저를 특정하는
// 주소이고(구독은 비밀이 아니지만 사생활이다), 여기서 내보내면 콘솔 화면·브라우저
// 캐시·어깨너머까지 그 목록이 따라다닌다. 그래서 `count` 만 낸다.
//
// 접근 통제는 `functions/admin/_middleware.js` 가 이미 했다. 이 파일에 도달했다는
// 것은 서명된 세션 쿠키가 있다는 뜻이다(/admin/api/ 아래는 401 도 JSON 으로 온다).
//
// 값을 읽지 않는다
// ----------------
// 세는 데에는 키 이름만 있으면 된다. `kv.get()` 을 구독마다 부르면 읽기 횟수가
// 구독자 수만큼 붙는데, 콘솔은 열 때마다 이걸 부른다 — /push/list 와 성격이 다르다.

import { json, pushStore, KEY_PREFIX } from "../../push/_shared.js";

const PAGE_LIMIT = 1000;
// 커서가 안 줄어드는 사고에서 무한 반복을 막는 상한. 실제로 걸릴 일은 없다
// (1000 × 50 = 5만 구독) — 걸렸다면 그 사실 자체를 화면에 말한다.
const MAX_PAGES = 50;

export async function onRequestGet({ env }) {
  const kv = pushStore(env);
  if (!kv) return json({ error: "push_store_missing" }, 503);

  let count = 0;
  let cursor;
  let capped = false;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const listing = await kv.list({ prefix: KEY_PREFIX, limit: PAGE_LIMIT, cursor });
    count += listing.keys.length;
    if (listing.list_complete) return json({ count, capped: false });
    cursor = listing.cursor;
  }
  capped = true;
  return json({ count, capped });
}
