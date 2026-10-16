// 最小 service worker：提供安装条件 + 离线壳，不拦截刷课请求
const CACHE = "ccb-shell-v1";
self.addEventListener("install", (e) => { self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });
// 仅对导航请求做 network-first，API 一律放行，不缓存
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/")));
  }
});
