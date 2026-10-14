// Service Worker for OptionsTaxHub PWA
// v6: cache-first only for same-origin static assets. The Cache API ignores
// Authorization, so cross-origin Supabase and API responses must never be stored.
const CACHE_NAME = "optionstaxhub-v6";

// Track recently shown notifications to prevent duplicates
// Using a simple LRU-like implementation with time-based expiration
const shownNotifications = new Map();
const NOTIFICATION_DEDUP_WINDOW = 5000; // 5 seconds
const MAX_NOTIFICATION_ENTRIES = 50; // Maximum entries before LRU eviction kicks in
const CLEANUP_INTERVAL = 10000; // Clean up every 10 seconds

// Periodic cleanup to prevent memory leaks
setInterval(() => {
  const now = Date.now();
  let cleanedCount = 0;

  for (const [key, timestamp] of shownNotifications.entries()) {
    if (now - timestamp > NOTIFICATION_DEDUP_WINDOW) {
      shownNotifications.delete(key);
      cleanedCount++;
    }
  }

  // If still too large after expiration cleanup, remove oldest entries (LRU)
  if (shownNotifications.size > MAX_NOTIFICATION_ENTRIES) {
    const entries = Array.from(shownNotifications.entries()).sort(
      (a, b) => a[1] - b[1],
    ); // Sort by timestamp (oldest first)
    const toRemove = entries.slice(
      0,
      shownNotifications.size - MAX_NOTIFICATION_ENTRIES,
    );
    for (const [key] of toRemove) {
      shownNotifications.delete(key);
      cleanedCount++;
    }
  }

  if (cleanedCount > 0) {
    console.log(
      `[SW] Cleaned ${cleanedCount} expired notification entries. Current size: ${shownNotifications.size}`,
    );
  }
}, CLEANUP_INTERVAL);

// Assets to cache on install.
// NOTE: Do NOT include the HTML document ("/") — it contains version-specific
// ?v= query params for JS/CSS chunks that change on every build/server restart.
// Caching it causes the browser to replay stale chunk URLs that 404 on new builds.
const STATIC_ASSETS = [
  "/manifest.json",
  "/icons/icon-192x192.png",
  "/icons/icon-512x512.png",
];

// Install event - cache static assets
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(
        Array.from(STATIC_ASSETS).filter((url) => !url.includes(".png")),
      ); // Skip icons for now
    }),
  );
  console.log("[SW] install event - skipping waiting");
  globalThis.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        Array.from(cacheNames).map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        }),
      );
    }),
  );
  console.log("[SW] activate event - claiming clients and clearing old caches");
  globalThis.clients.claim();
});

const STATIC_FILE = /\.(?:woff2?|ttf|otf|eot|png|jpe?g|gif|webp|svg|ico|avif)$/i;

function isCacheableStatic(url) {
  const path = url.pathname;
  if (path.startsWith("/_next/static/")) {
    return true;
  }
  if (path.startsWith("/icons/")) {
    return true;
  }
  if (path === "/manifest.json") {
    return true;
  }
  return STATIC_FILE.test(path);
}

// Fetch event — do not intercept anything except same-origin static GETs.
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (!url.protocol.startsWith("http")) {
    return;
  }

  // Cross-origin calls (Supabase /auth/v1, /rest/v1, backend /health and
  // /push/subscriptions) and non-GET requests stay on the network. Caching
  // them would ignore Authorization and leak one user's data to the next.
  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  // Do not intercept navigations or HTML. A rejected respondWith() is what
  // the browser reports as a service-worker 503 on /dashboard.
  const acceptHeader = request.headers.get("accept") || "";
  if (request.mode === "navigate" || acceptHeader.includes("text/html")) {
    return;
  }

  if (!isCacheableStatic(url)) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(request)
        .then((response) => {
          if (response?.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone);
            });
          }
          return response;
        })
        .catch(
          () => new Response("", { status: 504, statusText: "Offline" }),
        );
    }),
  );
});

// Push notification event
self.addEventListener("push", (event) => {
  let notificationData = {
    title: "OptionsTaxHub",
    body: "You have a new notification",
    icon: "/icons/icon-192x192.png",
    badge: "/icons/icon-192x192.png",
    tag: `notification-${Date.now()}`, // Unique tag to prevent duplicates
  };

  if (event.data) {
    try {
      const data = event.data.json();
      notificationData = {
        title: data.title || notificationData.title,
        body: data.body || notificationData.body,
        icon: data.icon || notificationData.icon,
        badge: data.badge || notificationData.badge,
        tag: data.tag || notificationData.tag,
        data: data.data || {},
      };
    } catch {
      // JSON parsing failed - attempt to use text content as fallback
      if (event.data && typeof event.data.text === "function") {
        notificationData.body = event.data.text();
      } else {
        notificationData.body = "New notification";
      }
    }
  }

  // Create a key for deduplication
  const notificationKey = `${notificationData.title}|${notificationData.body}`;
  const lastShownTime = shownNotifications.get(notificationKey);
  const now = Date.now();

  // Skip if same notification was shown recently (within dedup window)
  if (lastShownTime && now - lastShownTime < NOTIFICATION_DEDUP_WINDOW) {
    return;
  }

  // Record this notification as shown
  shownNotifications.set(notificationKey, now);

  event.waitUntil(
    globalThis.registration.showNotification(notificationData.title, {
      body: notificationData.body,
      icon: notificationData.icon,
      badge: notificationData.badge,
      tag: notificationData.tag,
      data: notificationData.data,
    }),
  );
});

// Notification click event
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  // Open the app or focus existing window
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        // Check if there's already a window open
        for (const client of Array.from(clientList)) {
          if (
            client.url === globalThis.registration.scope &&
            "focus" in client
          ) {
            return client.focus();
          }
        }
        // Open new window if none exists
        if (clients.openWindow) {
          return clients.openWindow("/");
        }
      }),
  );
});
