import { useMutation, useQuery } from "@tanstack/react-query";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

/**
 * API response type for CSV upload
 * Backend returns parsed CSV data (first 5 rows)
 */
export interface PortfolioData {
  [key: string]: string | number;
}

/**
 * Upload CSV file to backend for parsing
 *
 * @param file - CSV file to upload
 * @returns Promise resolving to array of portfolio data
 */
async function uploadPortfolioCsv(file: File): Promise<PortfolioData[]> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_URL}/upload-csv`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Upload failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query mutation hook for CSV upload
 *
 * Usage:
 * ```
 * const { mutate, isPending, error, data } = useUploadPortfolio();
 * ```
 */
export function useUploadPortfolio() {
  return useMutation({
    mutationFn: uploadPortfolioCsv,
  });
}

/**
 * Fetch portfolio history (placeholder for future backend implementation)
 *
 * @returns Promise resolving to array of historical portfolio data
 */
async function fetchPortfolioHistory(): Promise<PortfolioData[]> {
  const response = await fetch(`${API_URL}/portfolio-history`);

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query query hook for fetching portfolio history
 *
 * Usage:
 * ```
 * const { data, isLoading, error } = usePortfolioHistory();
 * const { data, isLoading, error } = usePortfolioHistory(true);
 * ```
 */
export function usePortfolioHistory(enabled = false) {
  return useQuery({
    queryKey: ["portfolio-history"],
    queryFn: fetchPortfolioHistory,
    enabled, // Disable auto-fetch until endpoint exists
  });
}

/**
 * Push notification subscription data
 */
export interface PushSubscriptionData {
  endpoint: string;
  keys: {
    p256dh: string;
    auth: string;
  };
}

/**
 * Subscribe to push notifications
 *
 * @param subscription - Push subscription object from browser
 * @returns Promise resolving to subscription confirmation
 */
async function subscribeToPushNotifications(
  subscription: PushSubscriptionData,
): Promise<{ success: boolean; message: string }> {
  const response = await fetch(`${API_URL}/subscribe-push`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(subscription),
  });

  if (!response.ok) {
    throw new Error(`Push subscription failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query mutation hook for push notification subscription
 *
 * Usage:
 * ```
 * const { mutate, isPending } = usePushNotificationSubscription();
 * ```
 */
export function usePushNotificationSubscription() {
  return useMutation({
    mutationFn: subscribeToPushNotifications,
  });
}
