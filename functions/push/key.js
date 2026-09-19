// 화면이 구독을 만들 때 필요한 VAPID 공개키.
//
// 왜 app.js 에 상수로 박지 않는가
// -------------------------------
// v1(policy174)은 박아 뒀고 그래서 키를 갈면 app.js 를 고쳐 다시 배포해야 한다.
// 더 나쁜 것은 **키가 없는 배포에서도 버튼이 떴다**는 점이다 — 눌러도 되는 게
// 없는 버튼은 고장으로 읽힌다(그 이유로 2026-09-18 에 버튼째 내렸다).
//
// 여기서 받아 오면 설정이 안 된 배포는 404 를 내고, initPush 는 버튼을 숨긴
// 채로 둔다. "설정하지 않은 것"과 "고장난 것"이 화면에서 갈린다. 발송 경로를
// v1 방식으로 되돌리면서도 이 한 겹만 남긴 이유다.

import { json, vapidPublicKey } from "./_shared.js";

export async function onRequestGet({ env }) {
  const publicKey = vapidPublicKey(env);
  if (!publicKey) return json({ error: "push_not_configured" }, 404);
  // 공개키는 비밀이 아니다. 그래도 no-store 로 둔다 — 키를 갈았을 때
  // 엣지 캐시에 옛 키가 남으면 그 구독은 조용히 못 받는 구독이 된다.
  return json({ key: publicKey });
}
