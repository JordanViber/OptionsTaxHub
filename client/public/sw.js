// Service Worker for OptionsTaxHub PWA
const CACHE_NAME = "optionstaxhub-v1";
const API_URL = "http://localhost:8080";

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
        // Cache successful responses
        if (response && response.status === 200) {
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
    tag: "default",
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
