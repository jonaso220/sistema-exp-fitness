// Service Worker for Sistema EXP Fitness PWA
const CACHE_NAME = 'exp-fitness-v1';

// App shell resources to pre-cache on install
const APP_SHELL = [
  '/',
  '/dashboard',
  '/static/logo.png',
  '/static/manifest.json',
  // CDN resources
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// Offline fallback page
const OFFLINE_PAGE = `<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sin Conexion - EXP Fitness</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background-color: #0d1117;
      color: #e6edf3;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      text-align: center;
      padding: 2rem;
    }
    .container {
      max-width: 480px;
    }
    .icon {
      font-size: 4rem;
      margin-bottom: 1.5rem;
      opacity: 0.6;
    }
    h1 {
      font-size: 1.5rem;
      margin-bottom: 0.75rem;
      color: #6366f1;
    }
    p {
      font-size: 1rem;
      line-height: 1.6;
      color: #8b949e;
      margin-bottom: 1.5rem;
    }
    button {
      background-color: #6366f1;
      color: #fff;
      border: none;
      padding: 0.75rem 2rem;
      border-radius: 0.5rem;
      font-size: 1rem;
      cursor: pointer;
      transition: background-color 0.2s;
    }
    button:hover {
      background-color: #4f46e5;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="icon">&#x1F4F6;</div>
    <h1>Sin Conexion</h1>
    <p>No se puede conectar al servidor. Verifica tu conexion a internet e intenta nuevamente.</p>
    <button onclick="window.location.reload()">Reintentar</button>
  </div>
</body>
</html>`;

// --------------------------------------------------
// Install: pre-cache app shell
// --------------------------------------------------
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Pre-caching app shell');
      return cache.addAll(APP_SHELL);
    })
  );
  // Activate immediately without waiting for old SW to finish
  self.skipWaiting();
});

// --------------------------------------------------
// Activate: clean up old caches
// --------------------------------------------------
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    })
  );
  // Take control of all open clients immediately
  self.clients.claim();
});

// --------------------------------------------------
// Fetch: routing strategies
// --------------------------------------------------
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 1. API requests: network-only (no caching)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request).catch(() => {
        return new Response(
          JSON.stringify({ error: 'You are offline' }),
          {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          }
        );
      })
    );
    return;
  }

  // 2. Navigation requests (HTML): network-first with cache fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Cache the fresh response for future offline use
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, clone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(request).then((cachedResponse) => {
            return cachedResponse || new Response(OFFLINE_PAGE, {
              headers: { 'Content-Type': 'text/html; charset=utf-8' }
            });
          });
        })
    );
    return;
  }

  // 3. Static assets (CSS, JS, images): cache-first with network fallback
  if (
    request.destination === 'style' ||
    request.destination === 'script' ||
    request.destination === 'image' ||
    url.pathname.startsWith('/static/')
  ) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(request).then((networkResponse) => {
          // Cache the new resource for next time
          const clone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, clone);
          });
          return networkResponse;
        });
      })
    );
    return;
  }

  // 4. Everything else: network-first
  event.respondWith(
    fetch(request).catch(() => {
      return caches.match(request);
    })
  );
});
