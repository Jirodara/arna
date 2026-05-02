const V = 'arna-engineer-v1';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(V).then(c => c.addAll([
    './engineer.html',
    './engineer-manifest.json',
    './engineer-icon.svg'
  ])));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
