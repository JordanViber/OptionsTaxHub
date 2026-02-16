// Service Worker for OptionsTaxHub PWA
const CACHE_NAME = "optionstaxhub-v1";
const API_URL = "http://localhost:8080";

// Track recently shown notifications to prevent duplicates
// Using a simple LRU-like implementation with time-based expiration
const shownNotifications = new Map();
const NOTIFICATION_DEDUP_WINDOW = 5000; // 5 seconds
const MAX_NOTIFICATION_ENTRIES = 50; // Reduced from 100 for more aggressive cleanup
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
    const entries = Array.from(shownNotifications.entries())
      .sort((a, b) => a[1] - b[1]); // Sort by timestamp (oldest first)
    const toRemove = entries.slice(0, shownNotifications.size - MAX_NOTIFICATION_ENTRIES);
    for (const [key] of toRemove) {
      shownNotifications.delete(key);
      cleanedCount++;
    }
  }
  
  if (cleanedCount > 0) {
    console.log(`[SW] Cleaned ${cleanedCount} expired notification entries. Current size: ${shownNotifications.size}`);
  }
}, CLEANUP_INTERVAL);

// Assets to cache on install
const STATIC_ASSETS = [
  "/",
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
  globalThis.clients.claim();
});

// Fetch event - network first, fallback to cache
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip chrome-extension and non-http(s) requests
  if (!url.protocol.startsWith("http")) {
    return;
  }

  // API requests - network only (no caching for now)
  if (url.origin === API_URL) {
    event.respondWith(fetch(request));
    return;
  }

  // Static assets - cache first, fallback to network
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(request).then((response) => {
        // Only cache GET requests with successful responses
        if (response && response.status === 200 && request.method === "GET") {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseClone);
          });
        }
        return response;
      });
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
