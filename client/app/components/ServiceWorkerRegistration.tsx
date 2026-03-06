"use client";

import { useEffect } from "react";

type ServiceWorkerTestGlobal = typeof globalThis & {
  __OPTIONS_TAX_HUB_DISABLE_SW__?: boolean;
};

function ignoreServiceWorkerError(): void {
  // Fail silently for service worker setup and cleanup.
}

function isDevelopmentEnvironment(): boolean {
  return (
    process.env["NODE_ENV"] === "development" ||
    (globalThis as ServiceWorkerTestGlobal).__OPTIONS_TAX_HUB_DISABLE_SW__ ===
      true
  );
}

function unregisterDevelopmentServiceWorkers(): void {
  const registrationsPromise = navigator.serviceWorker?.getRegistrations?.() as
    | Promise<ServiceWorkerRegistration[]>
    | undefined;

  if (registrationsPromise === undefined) {
    return;
  }

  registrationsPromise
    .then((registrations) => {
      registrations.forEach((registration) => {
        registration.unregister().catch(ignoreServiceWorkerError);
      });
    })
    .catch(ignoreServiceWorkerError);
}

export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof globalThis === "undefined" || !navigator.serviceWorker) {
      return;
    }

    if (isDevelopmentEnvironment()) {
      unregisterDevelopmentServiceWorkers();
      return;
    }

    let intervalId: ReturnType<typeof setInterval> | undefined;

    navigator.serviceWorker
      .register("/sw.js")
      .then((registration) => {
        // Check for updates periodically
        intervalId = setInterval(() => {
          registration.update();
        }, 60000); // Check every minute
      })
      .catch(ignoreServiceWorkerError);

    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, []);

  return null;
}
