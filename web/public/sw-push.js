/* Web Push: lo importa el service worker generado por vite-plugin-pwa (workbox.importScripts). Muestra la notificación que
 * manda el engine y, al tocarla, abre o enfoca la consola en la vista de bolsillo. */
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  const opts = { body: d.body || "", icon: "/icons/icon-192.png", badge: "/icons/icon-192.png", data: { url: d.url || "/#pocket" }, timestamp: d.at ? d.at * 1000 : Date.now() };
  if (d.tag) { opts.tag = d.tag; opts.renotify = true; }
  e.waitUntil(self.registration.showNotification(d.title || "TradePilot X", opts));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) { if (c.navigate) c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
