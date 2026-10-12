/* Service worker do portal (/app): instala como app (PWA), mostra as notificações dos pedidos (Web Push) e, sem
   rede, uma página simples em vez do erro do navegador. Não guarda dados da API em cache. */
const OFFLINE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>Agent Hangar</title><body style="font:16px system-ui;background:#1C2530;color:#C9D1DB;display:grid;place-items:center;height:100vh;margin:0">'
  + '<div style="text-align:center"><h1 style="color:#F4B400">Agent Hangar</h1><p>Sem conexão / Offline</p>'
  + '<button onclick="location.reload()" style="font:inherit;padding:8px 16px">Tentar de novo / Retry</button></div>';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { title: 'Agent Hangar', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Agent Hangar', {
    body: d.body || '', tag: d.tag || undefined, renotify: !!d.tag, icon: '/app/icon-192.png', badge: '/app/icon-192.png',
    data: { url: d.url || '/app/#/decisions' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/app/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const open = list.find(c => c.url.includes('/app/'));
    if (open) { open.navigate(url).catch(() => {}); return open.focus(); }
    return self.clients.openWindow(url);
  }));
});
