/*
 * Weather Off-Grid - Service Worker.
 *
 * Strategy:
 *   - Static assets (HTML/JS/icons/CDN): cache-first, fall back to network.
 *   - API calls (URLs containing "/api/"): network-first, fall back to the last
 *     cached response so the dashboard still shows data when offline.
 *
 * Bump CACHE_VERSION to force clients to drop the old cache on next load.
 */
const CACHE_VERSION = "wog-v3";
const STATIC_CACHE = CACHE_VERSION + "-static";
const API_CACHE = CACHE_VERSION + "-api";

const PRECACHE = [
  "./",
  "./index.html",
  "./settings.html",
  "./manifest.json",
  "./config.js",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      // Don't fail the whole install if one optional asset (e.g. config.js) is missing.
      Promise.allSettled(PRECACHE.map((url) => cache.add(url)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => !k.startsWith(CACHE_VERSION)).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // API: network-first with cache fallback.
  if (url.pathname.includes("/api/")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(API_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Static assets: cache-first with network fallback (and cache-on-fetch).
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response && response.status === 200 && url.origin === self.location.origin) {
          const copy = response.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      });
    })
  );
});
