import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  PortfolioAnalysis,
  TaxProfile,
  TaxBracketsSummary,
  PricesResponse,
  FilingStatus,
  AnalysisHistoryItem,
} from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

/**
 * API response type for legacy CSV upload
 * Backend returns parsed CSV data (first 5 rows)
 */
export interface PortfolioData {
  [key: string]: string | number;
}

/**
 * Legacy: Upload CSV file to backend for parsing (first 5 rows)
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
 * Legacy React Query mutation hook for CSV upload
 */
export function useUploadPortfolio() {
  return useMutation({
    mutationFn: uploadPortfolioCsv,
  });
}

// --- Portfolio Analysis ---

interface AnalyzePortfolioParams {
  file: File;
  filingStatus?: FilingStatus;
  estimatedIncome?: number;
  taxYear?: number;
  userId?: string;
}

/**
 * Upload CSV and get full portfolio analysis with tax-loss harvesting suggestions.
 *
 * Calls POST /api/portfolio/analyze with the CSV file and tax profile params.
 * Returns positions, harvesting suggestions, wash-sale flags, and summary.
 */
async function analyzePortfolio(
  params: AnalyzePortfolioParams,
): Promise<PortfolioAnalysis> {
  const formData = new FormData();
  formData.append("file", params.file);

  const queryParams = new URLSearchParams();
  if (params.filingStatus)
    queryParams.set("filing_status", params.filingStatus);
  if (params.estimatedIncome)
    queryParams.set("estimated_income", params.estimatedIncome.toString());
  if (params.taxYear) queryParams.set("tax_year", params.taxYear.toString());
  if (params.userId) queryParams.set("user_id", params.userId);

  const url = `${API_URL}/api/portfolio/analyze?${queryParams.toString()}`;

  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    const message =
      errorData?.detail?.message ||
      errorData?.detail ||
      `Analysis failed: ${response.statusText}`;
    throw new Error(message);
  }

  return response.json();
}

/**
 * React Query mutation hook for full portfolio analysis.
 *
 * Usage:
 * ```tsx
 * const { mutate, isPending, data, error } = useAnalyzePortfolio();
 * mutate({ file, filingStatus: "single", estimatedIncome: 85000, taxYear: 2025 });
 * ```
 */
export function useAnalyzePortfolio() {
  return useMutation({
    mutationFn: analyzePortfolio,
  });
}

// --- Live Prices ---

/**
 * Fetch current prices for given symbols via yfinance.
 */
async function fetchPrices(symbols: string[]): Promise<PricesResponse> {
  const response = await fetch(
    `${API_URL}/api/prices?symbols=${symbols.join(",")}`,
  );

  if (!response.ok) {
    throw new Error(`Price fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query hook for fetching live prices.
 *
 * Usage:
 * ```tsx
 * const { data } = useFetchPrices(["AAPL", "MSFT"], true);
 * ```
 */
export function useFetchPrices(symbols: string[], enabled = false) {
  return useQuery({
    queryKey: ["prices", symbols],
    queryFn: () => fetchPrices(symbols),
    enabled: enabled && symbols.length > 0,
    staleTime: 5 * 60 * 1000, // 5 minutes — matches backend cache TTL
  });
}

// --- Tax Profile ---

/**
 * Save user's tax profile settings.
 */
async function saveTaxProfile(
  profile: TaxProfile,
): Promise<{ message: string; profile: TaxProfile }> {
  const response = await fetch(`${API_URL}/api/tax-profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });

  if (!response.ok) {
    throw new Error(`Save failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query mutation hook for saving tax profile.
 * Invalidates the tax-profile cache on success so subsequent reads get fresh data.
 */
export function useSaveTaxProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveTaxProfile,
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["tax-profile", variables.user_id],
      });
    },
  });
}

/**
 * Fetch user's tax profile.
 */
async function fetchTaxProfile(userId: string): Promise<TaxProfile> {
  const response = await fetch(`${API_URL}/api/tax-profile/${userId}`);

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query hook for loading tax profile.
 */
export function useTaxProfile(userId: string | undefined) {
  return useQuery({
    queryKey: ["tax-profile", userId],
    queryFn: () => {
      if (!userId) throw new Error("userId is required");
      return fetchTaxProfile(userId);
    },
    enabled: !!userId,
  });
}

// --- Tax Brackets ---

/**
 * Fetch tax brackets for given parameters.
 */
async function fetchTaxBrackets(
  year: number,
  filingStatus: FilingStatus,
  income: number,
): Promise<TaxBracketsSummary> {
  const params = new URLSearchParams({
    year: year.toString(),
    filing_status: filingStatus,
    income: income.toString(),
  });

  const response = await fetch(`${API_URL}/api/tax-brackets?${params}`);

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query hook for tax brackets.
 */
export function useTaxBrackets(
  year: number,
  filingStatus: FilingStatus,
  income: number,
  enabled = false,
) {
  return useQuery({
    queryKey: ["tax-brackets", year, filingStatus, income],
    queryFn: () => fetchTaxBrackets(year, filingStatus, income),
    enabled,
    staleTime: Infinity, // Tax brackets don't change during a session
  });
}

// --- Portfolio History ---

/**
 * Fetch past portfolio analyses for a user from Supabase.
 */
async function fetchPortfolioHistory(
  userId: string,
): Promise<AnalysisHistoryItem[]> {
  const response = await fetch(`${API_URL}/api/portfolio/history/${userId}`);

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query hook for portfolio analysis history.
 *
 * Fetches past uploads for the given user. Refetches automatically
 * when the query is invalidated (e.g., after a new upload).
 */
export function usePortfolioHistory(userId: string | undefined) {
  return useQuery({
    queryKey: ["portfolio-history", userId],
    queryFn: () => {
      if (!userId) throw new Error("userId is required");
      return fetchPortfolioHistory(userId);
    },
    enabled: !!userId,
    staleTime: 0, // Always refetch when invalidated
    refetchOnMount: "always", // Refetch every time component mounts
    refetchOnWindowFocus: false, // Don't refetch on window focus
  });
}

/**
 * Fetch a single past portfolio analysis by ID, including the full result.
 *
 * Used when a user clicks a history item to reload that report.
 */
export async function fetchAnalysisById(
  analysisId: string,
  userId: string,
): Promise<{ result: PortfolioAnalysis | null } & AnalysisHistoryItem> {
  const params = new URLSearchParams({ user_id: userId });
  const response = await fetch(
    `${API_URL}/api/portfolio/analysis/${analysisId}?${params}`,
  );

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Delete orphan history entries that have no stored result data.
 *
 * These are legacy rows created before the app started persisting
 * full analysis results. Called once on mount to clean up.
 */
export async function cleanupOrphanHistory(userId: string): Promise<void> {
  await fetch(`${API_URL}/api/portfolio/history/${userId}/cleanup`, {
    method: "DELETE",
  });
}

/**
 * Delete a single portfolio analysis by ID.
 *
 * Returns true if deletion succeeded.
 */
export async function deleteAnalysis(
  analysisId: string,
  userId: string,
): Promise<boolean> {
  const params = new URLSearchParams({ user_id: userId });
  const response = await fetch(
    `${API_URL}/api/portfolio/analysis/${analysisId}?${params}`,
    { method: "DELETE" },
  );
  return response.ok;
}
