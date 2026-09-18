// 서비스워커 — 푸시 알림 전용. 캐시는 하지 않는다: 이 사이트는 매시 데이터가 바뀌고
// 브라우저 캐시 때문에 옛 화면을 보는 사고가 이미 있었다(2026-09-15 style.css).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// 오늘 무엇이 올라왔는지는 **여기서 받아 온다.**
//
// 보내는 쪽(functions/push/send.js)은 본문 없는 알림을 보낸다 — 본문을 실으려면
// 구독마다 암호화를 돌려야 하고(RFC 8291), 그 구현을 검증 없이 배포 경로에 두지
// 않기로 했다. 대신 알림이 도착한 순간 1KB 짜리 push.json 한 장을 읽는다.
// 못 읽어도 알림은 뜬다 — 아래 기본 문구가 그 자리를 지킨다.
async function briefCard() {
  try {
    const response = await fetch(`/data/push.json?cb=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return {};
    const card = await response.json();
    return card && typeof card === "object" ? card : {};
  } catch {
    return {};
  }
}

self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
    // 본문이 실려 오면 그것이 우선이다 — 나중에 암호화를 붙여도 이 핸들러는
    // 그대로 산다. 비어 있을 때만 오늘 카드를 읽는다.
    if (!data.title && !data.body) data = await briefCard();
    await self.registration.showNotification(data.title || "Nuclens 오늘 브리핑", {
      body: data.body || "오늘의 원전 현안이 올라왔습니다.",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.tag || "nuclens-brief",
      renotify: false,
      data: { url: data.url || "/?src=push" },
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const same = wins.find((w) => w.url.startsWith(self.location.origin));
    if (same) return same.focus().then((w) => (w && "navigate" in w ? w.navigate(url) : w));
    return self.clients.openWindow(url);
  }));
});
