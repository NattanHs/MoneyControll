/* MoneyControl — service worker: guarda o app inteiro no aparelho para abrir sem internet.
   A versão (BUILD) é trocada a cada publicação; ao mudar, o navegador baixa tudo de novo e apaga a cópia antiga.
   A lista de arquivos vem do precache.json, gerado na publicação. Seus dados NÃO passam por aqui:
   ficam no IndexedDB, gravados pelo worker.js. */
const BUILD = 'dev';
const CACHE = 'mc-' + BUILD;
const SCOPE = self.registration.scope;

async function fetchWithRetry(url, tries = 3) {
  let last;
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url, { cache: 'reload' });
      if (res.ok) return res;
      last = new Error(url + ' → ' + res.status);
    } catch (e) { last = e; }
    await new Promise(r => setTimeout(r, 400 * (i + 1)));
  }
  throw last;
}

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    let files = [];
    try { files = (await (await fetchWithRetry('precache.json')).json()).files || []; } catch { /* sem lista (teste local) */ }
    // tudo ou nada: uma cópia pela metade deixaria o app sem poder abrir offline
    for (const f of files) {
      const url = new URL(f, SCOPE).href;
      await cache.put(url, await fetchWithRetry(url));
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key.startsWith('mc-') && key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    if (req.mode === 'navigate') {
      const home = await cache.match(new URL('index.html', SCOPE).href);
      if (home) return home;
    }
    return fetch(req);
  })());
});
