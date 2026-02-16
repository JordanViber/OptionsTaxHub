import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  PortfolioAnalysis,
  TaxProfile,
  TaxBracketsSummary,
  PricesResponse,
  FilingStatus,
  AnalysisHistoryItem,
} from "@/lib/types";
import { getSession } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

/**
 * Get JWT token from Supabase session and add to request headers
 */
async function getAuthHeaders(): Promise<HeadersInit> {
  try {
    const session = await getSession();
    if (!session?.access_token) {
      throw new Error("No access token found");
    }
    return {
      "Authorization": `Bearer ${session.access_token}`,
      "Content-Type": "application/json",
    };
  } catch (error) {
    console.error("Failed to get auth headers:", error);
    throw new Error("Authentication required. Please sign in.");
  }
}

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
}

/**
 * Upload CSV and get full portfolio analysis with tax-loss harvesting suggestions.
 *
 * Requires authentication. Calls POST /api/portfolio/analyze with JWT token.
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

  const url = `${API_URL}/api/portfolio/analyze?${queryParams.toString()}`;
  const headers = await getAuthHeaders();
  
  // Don't set Content-Type for FormData (browser will set it with boundary)
  const headersForForm: HeadersInit = { Authorization: headers["Authorization"] };

  const response = await fetch(url, {
    method: "POST",
    body: formData,
    headers: headersForForm,
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
 * Save authenticated user's tax profile settings.
 * 
 * Requires JWT authentication.
 */
async function saveTaxProfile(
  profile: TaxProfile,
): Promise<{ message: string; profile: TaxProfile }> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/api/tax-profile`, {
    method: "POST",
    headers,
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
 * Fetch authenticated user's tax profile.
 * 
 * Requires JWT authentication.
 */
async function fetchTaxProfile(): Promise<TaxProfile> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/api/tax-profile`, {
    headers,
  });

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query hook for authenticated user's tax profile.
 */
export function useTaxProfile() {
  return useQuery({
    queryKey: ["tax-profile"],
    queryFn: fetchTaxProfile,
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
 * Fetch authenticated user's past portfolio analyses from Supabase.
 * 
 * Requires JWT authentication.
 */
async function fetchPortfolioHistory(): Promise<AnalysisHistoryItem[]> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_URL}/api/portfolio/history`, {
    headers,
  });

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * React Query hook for authenticated user's portfolio analysis history.
 *
 * Fetches past uploads automatically. Refetches when invalidated (e.g., after upload).
 */
export function usePortfolioHistory() {
  return useQuery({
    queryKey: ["portfolio-history"],
    queryFn: fetchPortfolioHistory,
    staleTime: 0, // Always refetch when invalidated
    refetchOnMount: "always", // Refetch every time component mounts
    refetchOnWindowFocus: false, // Don't refetch on window focus
  });
}

/**
 * Fetch a single past portfolio analysis by ID, including the full result.
 *
 * Used when a user clicks a history item to reload that report.
 * Requires JWT authentication.
 */
export async function fetchAnalysisById(
  analysisId: string,
): Promise<{ result: PortfolioAnalysis | null } & AnalysisHistoryItem> {
  const headers = await getAuthHeaders();
  const response = await fetch(
    `${API_URL}/api/portfolio/analysis/${analysisId}`,
    { headers },
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
 * 
 * Requires JWT authentication.
 */
export async function cleanupOrphanHistory(): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${API_URL}/api/portfolio/history/cleanup`, {
    method: "DELETE",
    headers,
  });
}

/**
 * Delete a single portfolio analysis by ID.
 *
 * Returns true if deletion succeeded.
 * Requires JWT authentication.
 */
export async function deleteAnalysis(
  analysisId: string,
): Promise<boolean> {
  const headers = await getAuthHeaders();
  const response = await fetch(
    `${API_URL}/api/portfolio/analysis/${analysisId}`,
    { 
      method: "DELETE",
      headers,
    },
  );
  return response.ok;
}
