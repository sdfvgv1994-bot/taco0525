// 雲端看盤 App 的 service worker：有網路就拿最新的，沒網路時顯示上一次看過的資料。
// 只處理同一個網站的 GET；GitHub API（存自選股、下單）一律直接連線，不經過這裡。
const CACHE = "cloud-monitor-v1";
self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["./", "./index.html", "./app/manifest.json", "./app/icon-192.png"])));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  const key = url.pathname.endsWith("data.json") ? new Request(url.origin + url.pathname) : req;  // 忽略 ?t= 參數
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(key, copy)); }
    return res;
  }).catch(() => caches.match(key).then((hit) => hit || Promise.reject(new Error("offline")))));
});
