"use client";

import { useEffect } from "react";

export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof globalThis !== "undefined" && navigator.serviceWorker) {
      navigator.serviceWorker
        .register("/sw.js")
        .then((registration) => {
          // Check for updates periodically
          setInterval(() => {
            registration.update();
          }, 60000); // Check every minute
        })
        .catch(() => {
          // Service Worker registration failed silently
        });
    }
  }, []);

  return null;
}
