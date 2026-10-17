"use client";

import { useEffect, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase";

export interface NotificationPayload {
  title: string;
  body: string;
  icon?: string;
  badge?: string;
  tag?: string;
  data?: any;
}

export function usePushNotifications() {
  const [permission, setPermission] =
    useState<NotificationPermission>("default");
  const [subscription, setSubscription] = useState<PushSubscription | null>(
    null,
  );
  const [isSupported, setIsSupported] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);

  useEffect(() => {
    if (
      typeof globalThis !== "undefined" &&
      "serviceWorker" in navigator &&
      "PushManager" in globalThis
    ) {
      setIsSupported(true);
      setPermission(Notification.permission);
    }
  }, []);

  const requestPermission = async () => {
    if (!isSupported) {
      return false;
    }

    try {
      const result = await Notification.requestPermission();
      setPermission(result);
      return result === "granted";
    } catch (error) {
      // Permission request failed or not supported - graceful degradation
      if (error instanceof Error) {
        // Log error for debugging but don't throw
      }
      return false;
    }
  };

  const subscribe = async () => {
    if (!isSupported || permission !== "granted") {
      return null;
    }

    const accessToken = await readPushAccessToken();
    if (!accessToken) {
      setLastError("Sign in to enable notifications on this device.");
      return null;
    }

    try {
      const registration = await navigator.serviceWorker.ready;
      const vapidPublicKey =
        process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ||
        "BEl62iUYgUivxIkv69yViEuiBIa-Ib37J8xQmrEcxWLcNV5UvvhL_r0E4z-C0";
      const convertedVapidKey = urlBase64ToUint8Array(vapidPublicKey);
      const sub = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: convertedVapidKey as BufferSource,
      });
      const result = await postPushSubscription(
        "/push/subscribe",
        sub,
        accessToken,
      );

      if (result !== "accepted") {
        if (result === "rejected") {
          try {
            await sub.unsubscribe();
          } catch {
            // The browser subscription could not be rolled back.
          }
          setLastError("Notifications were not enabled for this account.");
        } else {
          setLastError("Notifications could not be enabled. Try again.");
        }
        return null;
      }

      setLastError(null);
      setSubscription(sub);
      return sub;
    } catch {
      setLastError("Notifications could not be enabled. Try again.");
      return null;
    }
  };

  const unsubscribe = async () => {
    if (!subscription) return false;

    const accessToken = await readPushAccessToken();
    if (!accessToken) {
      setLastError("Sign in to disable notifications on this device.");
      return false;
    }

    const result = await postPushSubscription(
      "/push/unsubscribe",
      subscription,
      accessToken,
    );
    if (result !== "accepted") {
      setLastError("Notifications could not be disabled. Try again.");
      return false;
    }

    try {
      await subscription.unsubscribe();
      setSubscription(null);
      setLastError(null);
      return true;
    } catch {
      return false;
    }
  };

  return {
    isSupported,
    permission,
    subscription,
    lastError,
    requestPermission,
    subscribe,
    unsubscribe,
  };
}

function getPushEndpoint(path: string): string {
  const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL || "";

  if (configuredApiUrl) {
    try {
      const url = new URL(configuredApiUrl);
      if (url.hostname === "localhost" || url.hostname === "127.0.0.1") {
        return path;
      }

      return `${configuredApiUrl.replace(/\/$/, "")}${path}`;
    } catch {
      // Fall back to same-origin relative paths for malformed local config values.
    }
  }

  return path;
}

// Helper function to convert base64 VAPID key to Uint8Array
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replaceAll("-", "+")
    .replaceAll("_", "/");

  const rawData = globalThis.atob(base64);
  const outputArray = new Uint8Array(rawData.length);

  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.codePointAt(i) || 0;
  }
  return outputArray;
}

type PushPostResult = "accepted" | "rejected" | "unavailable";

async function readPushAccessToken(): Promise<string | null> {
  try {
    const { data, error } = await getSupabaseClient().auth.refreshSession();
    if (error || !data.session?.access_token) return null;
    return data.session.access_token;
  } catch {
    return null;
  }
}

async function postPushSubscription(
  path: string,
  subscription: PushSubscription,
  accessToken: string,
): Promise<PushPostResult> {
  try {
    const response = await fetch(getPushEndpoint(path), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(subscription),
    });
    if (response.ok) return "accepted";
    if (response.status === 401 || response.status === 409) return "rejected";
    return "unavailable";
  } catch {
    return "unavailable";
  }
}
