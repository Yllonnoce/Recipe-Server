/* Minimal service worker so the app can be installed to the home screen.
 * Network first; the shell files are cached only as a fallback when the
 * server is unreachable (the library itself always needs the server). */
const CACHE = 'recipelib-v1';
const SHELL = ['/static/app.css', '/static/vendor/htmx.min.js', '/static/scale.js', '/static/cook.css', '/static/cook.js'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
