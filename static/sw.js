const SHELL_CACHE = 'delta-shell-v32';
const RUNTIME_CACHE = 'delta-runtime-v29';
const MAP_PACK_CACHE = 'delta-map-pack-v29';
const OFFLINE_FALLBACK_URL = '/static/offline.html';

const SAME_ORIGIN_SHELL = [
  '/',
  '/ai',
  '/cyber',
  '/map',
  '/survival',
  '/weapons',
  '/weapons/armory',
  '/mechanics',
  '/mechanics/gallery',
  '/mechanics/blueprints',
  '/mechanics/browser',
  '/bible',
  '/drone',
  '/radio',
  '/truth',
  '/manuals',
  '/live',
  '/site.webmanifest',
  '/static/style.css',
  '/static/auth.js',
  '/static/map-layer-catalog.js',
  '/static/scripture-hotspots.js',
  '/static/pwa-register.js',
  '/static/survival_loadout.js',
  '/static/survival_meal_bible.js',
  '/static/delta-icon.svg',
  '/static/vendor/leaflet/leaflet.css',
  '/static/vendor/leaflet/leaflet.js',
  '/static/vendor/leaflet/images/marker-icon.png',
  '/static/vendor/leaflet/images/marker-icon-2x.png',
  '/static/vendor/leaflet/images/marker-shadow.png',
  '/static/vendor/leaflet/images/layers.png',
  '/static/vendor/leaflet/images/layers-2x.png',
  '/static/vendor/html2canvas.min.js',
  OFFLINE_FALLBACK_URL,
  '/sw.js',
];

const REMOTE_ASSETS = [
  'https://cdnjs.cloudflare.com/ajax/libs/ace/1.4.14/ace.js',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
  'https://unpkg.com/html2canvas@1.4.1/dist/html2canvas.min.js',
  'https://unpkg.com/milsymbol@3.0.4/dist/milsymbol.js',
  'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&family=Inter:wght@400;500;600;700;900&display=swap',
];

async function cacheOpaqueUrls(cache, urls) {
  for (const url of urls) {
    try {
      const response = await fetch(new Request(url, { mode: 'no-cors', credentials: 'omit' }));
      if (response) {
        await cache.put(url, response);
      }
    } catch (_) {
      // Ignore individual failures so install still succeeds.
    }
  }
}

async function cacheUrls(urls) {
  const cache = await caches.open(MAP_PACK_CACHE);
  const uniqueUrls = Array.from(new Set((urls || []).filter(Boolean)));
  let cached = 0;

  for (const url of uniqueUrls) {
    try {
      const isSameOrigin = url.startsWith(self.location.origin);
      const request = new Request(url, { mode: isSameOrigin ? 'same-origin' : 'no-cors', credentials: 'omit' });
      const response = await fetch(request);
      if (response && (response.ok || response.type === 'opaque')) {
        await cache.put(request, response.clone());
        cached += 1;
      }
    } catch (_) {
      // Keep going so partial packs still save.
    }
  }

  return cached;
}

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil((async () => {
    const shellCache = await caches.open(SHELL_CACHE);
    await shellCache.addAll(SAME_ORIGIN_SHELL);
    await cacheOpaqueUrls(shellCache, REMOTE_ASSETS);
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.map(name => {
      if (![SHELL_CACHE, RUNTIME_CACHE, MAP_PACK_CACHE].includes(name)) {
        return caches.delete(name);
      }
      return Promise.resolve();
    }));
    await self.clients.claim();
  })());
});

async function cacheFirst(request, cacheName, ignoreSearch = false) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request, { ignoreVary: true, ignoreSearch });
  if (cached) return cached;

  const response = await fetch(request);
  if (response && (response.ok || response.type === 'opaque')) {
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request, cacheName, fallbackUrl = '/') {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (_) {
    return (await cache.match(request, { ignoreSearch: true })) || (await caches.match(fallbackUrl));
  }
}

function offlineJson(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store, max-age=0',
    },
  });
}

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, SHELL_CACHE, OFFLINE_FALLBACK_URL));
    return;
  }

  if (url.origin === self.location.origin) {
    if (url.pathname === '/api/online') {
      event.respondWith((async () => {
        try {
          return await fetch(request);
        } catch (_) {
          return offlineJson({ ok: true, count: 0, locations: [], offline: true });
        }
      })());
      return;
    }

    if (url.pathname.startsWith('/static/') || url.pathname === '/sw.js' || url.pathname === '/site.webmanifest') {
      event.respondWith(cacheFirst(request, SHELL_CACHE, false));
      return;
    }
  }

  if (request.destination === 'script' || request.destination === 'style' || request.destination === 'font') {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE));
    return;
  }

  if (request.destination === 'image') {
    event.respondWith(cacheFirst(request, MAP_PACK_CACHE));
  }
});

self.addEventListener('message', event => {
  if (event.data?.type !== 'CACHE_URLS') return;

  event.waitUntil((async () => {
    const cached = await cacheUrls(event.data.urls || []);
    if (event.ports && event.ports[0]) {
      event.ports[0].postMessage({ ok: true, cached });
    }
  })());
});