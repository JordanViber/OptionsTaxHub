import { QueryClient } from "@tanstack/react-query";

/**
 * QueryClient configuration for React Query
 *
 * Key settings for OptionsTaxHub:
 * - staleTime: 5 minutes for portfolio data (financial data changes frequently)
 * - gcTime: 15 minutes (keep data for offline PWA mode)
 * - retry: 3 attempts with exponential backoff for failed API calls
 * - refetchOnWindowFocus: Refresh data when user returns to tab
 * - refetchOnReconnect: Refresh when connection restored (PWA/mobile important)
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      gcTime: 1000 * 60 * 15, // 15 minutes (formerly cacheTime)
      retry: 3,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
    },
    mutations: {
      retry: 1,
    },
  },
});
