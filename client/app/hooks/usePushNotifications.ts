"use client";

import { useEffect, useState } from "react";

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

    try {
      const registration = await navigator.serviceWorker.ready;

      // Generate VAPID keys on server and use them here
      // For now, using a placeholder - you'll need to generate real keys
      const vapidPublicKey =
        process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ||
        "BEl62iUYgUivxIkv69yViEuiBIa-Ib37J8xQmrEcxWLcNV5UvvhL_r0E4z-C0";

      const convertedVapidKey = urlBase64ToUint8Array(vapidPublicKey);

      const sub = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: convertedVapidKey as BufferSource,
      });

      setSubscription(sub);

      // Send subscription to backend
      await sendSubscriptionToBackend(sub);

      return sub;
    } catch (error) {
      // Subscription failed - graceful degradation for unsupported browsers
      if (error instanceof Error) {
        // Log error for debugging but don't throw
      }
      return null;
    }
  };

  const unsubscribe = async () => {
    if (!subscription) return false;

    try {
      await subscription.unsubscribe();

      // Remove subscription from backend
      await removeSubscriptionFromBackend(subscription);

      setSubscription(null);
      return true;
    } catch (error) {
      // Unsubscribe failed - graceful degradation
      if (error instanceof Error) {
        // Log error for debugging but don't throw
      }
      return false;
    }
  };

  return {
    isSupported,
    permission,
    subscription,
    requestPermission,
    subscribe,
    unsubscribe,
  };
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

// Send subscription to backend
async function sendSubscriptionToBackend(subscription: PushSubscription) {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
    const response = await fetch(`${apiUrl}/push/subscribe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(subscription),
    });

    if (!response.ok) {
      throw new Error("Failed to send subscription to backend");
    }
  } catch (error) {
    // Backend subscription registration failed - non-critical for PWA functionality
    if (error instanceof Error) {
      // Log error for debugging but don't re-throw
    }
  }
}

// Remove subscription from backend
async function removeSubscriptionFromBackend(subscription: PushSubscription) {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
    const response = await fetch(`${apiUrl}/push/unsubscribe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(subscription),
    });

    if (!response.ok) {
      throw new Error("Failed to remove subscription from backend");
    }
  } catch (error) {
    // Backend subscription removal failed - non-critical for PWA functionality
    if (error instanceof Error) {
      // Log error for debugging but don't re-throw
    }
  }
}
