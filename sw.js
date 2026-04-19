const V='arna-v6';
const FILES=['./index.html','./manifest.json','./icon.svg'];
self.addEventListener('install',e=>{
  e.waitUntil(caches.open(V).then(c=>c.addAll(FILES)));
  self.skipWaiting();
});
self.addEventListener('activate',e=>{
  e.waitUntil(
    caches.keys().then(ks=>Promise.all(
      ks.filter(k=>k!==V).map(k=>{console.log('Eski cache silindi:',k);return caches.delete(k);})
    ))
  );
  self.clients.claim();
});
self.addEventListener('fetch',e=>{
  if(e.request.url.includes('binance')||e.request.url.includes('fonts')) return;
  e.respondWith(
    caches.match(e.request).then(r=>{
      // Her zaman network'ten dene, başarısız olursa cache
      return fetch(e.request).then(nr=>{
        caches.open(V).then(c=>c.put(e.request,nr.clone()));
        return nr;
      }).catch(()=>r);
    })
  );
});
