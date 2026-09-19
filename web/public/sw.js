// 서비스워커 — 푸시 알림 전용. 캐시는 하지 않는다: 이 사이트는 매시 데이터가 바뀌고
// 브라우저 캐시 때문에 옛 화면을 보는 사고가 이미 있었다(2026-09-15 style.css).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// 오늘 무엇이 올라왔는지는 **푸시 본문에 실려 온다.**
//
// 예전엔 보내는 쪽이 본문 없는 알림을 보냈고 여기서 `/data/push.json` 을 다시
// 읽었다. 그 왕복이 실패하면(폰이 지하철에 있거나 배포가 늦으면) 알림은 매번
// 일반 문구로만 떴다 — 조용한 퇴화였고, 로그에는 '보냄'으로 남았다.
// 지금은 tools/push_notify.py 가 pywebpush 로 제목·본문을 암호화해 실어 보낸다.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Nuclens 오늘 브리핑", {
    body: data.body || "오늘의 원전 현안이 올라왔습니다.",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: data.tag || "nuclens-brief",
    renotify: false,
    data: { url: data.url || "/?src=push" },
  }));
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
