/* The server stamps the asset list and its content hash into each release. */
const manifest = __SARDINA_MANIFEST__;
const PREFIX = 'sardina-shell-';
const SHELL = PREFIX + manifest.version;
const DATA = 'sardina-offline-data-v1';
const assets = new Set(manifest.assets);
const cachedApi = new Set(['/api/sites', '/api/home-sections']);
const appRoute = path => path === '/' || /^\/discover(?:\/(popular|latest))?$/.test(path) || /^\/(s|m|read)\//.test(path);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    await cache.addAll(manifest.assets.map(url => new Request(url, {cache: 'reload'})));
    // Existing pages keep their loaded code; the next navigation uses the
    // complete new shell. No reload interrupts an active reading session.
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    await Promise.all((await caches.keys()).filter(key => key.startsWith(PREFIX) && key !== SHELL).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

async function sourceData(request) {
  const cache = await caches.open(DATA), controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(request, {signal: controller.signal});
    if (!response.ok) throw new Error('Source list unavailable');
    await cache.put(request, response.clone()).catch(() => {});
    return response;
  } catch {
    const saved = await cache.match(request) || await (await caches.open(SHELL)).match(request);
    if (saved) return saved;
    const home = new URL(request.url).pathname === '/api/home-sections';
    return new Response(JSON.stringify(home ? {data: {featured: []}} : {error: '请先联网完成离线准备'}),
      {status: home ? 200 : 503, headers: {'Content-Type': 'application/json'}});
  } finally {clearTimeout(timer);}
}

self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (cachedApi.has(url.pathname) && !url.search) {
    event.respondWith(sourceData(request));
  } else if (request.mode === 'navigate' && appRoute(url.pathname)) {
    event.respondWith(caches.open(SHELL).then(async cache => await cache.match('/') || fetch(request)));
  } else if (assets.has(url.pathname) && !url.search) {
    event.respondWith(caches.open(SHELL).then(async cache => await cache.match(request) || fetch(request)));
  }
});

self.addEventListener('message', event => {
  if (event.data?.type !== 'offline-status') return;
  event.waitUntil(caches.open(SHELL).then(async cache => {
    const present = await Promise.all(manifest.assets.map(path => cache.match(path)));
    event.ports[0]?.postMessage({ready: present.every(Boolean), version: manifest.version});
  }));
});
