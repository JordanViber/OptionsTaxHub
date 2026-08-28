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

// Base API URL: if NEXT_PUBLIC_API_URL is set (production), use it; otherwise use
// relative paths so the dev server can proxy `/api/*` to the backend.
const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
const IS_PROD = process.env.NODE_ENV === "production";

function apiPath(path: string) {
  // If no explicit base is configured, use relative paths so Next dev proxy rewrites work.
  if (!API_BASE) return path.startsWith("/") ? path : `/${path}`;

  // If the configured base points to localhost, prefer relative paths during
  // development so the Next dev server can proxy /api/* to the backend and
  // avoid CORS errors in the browser (developers often set NEXT_PUBLIC_API_URL
  // to http://localhost:8011 for convenience).
  // NOTE: Do NOT apply this in production — Next.js rewrites only run in `next dev`.
  if (!IS_PROD) {
    try {
      const url = new URL(API_BASE);
      if (url.hostname === "localhost" || url.hostname === "127.0.0.1") {
        return path.startsWith("/") ? path : `/${path}`;
      }
    } catch {
      // NOSONAR typescript:S2486 — URL parse failure is expected when API_BASE is a relative path
      // If API_BASE is not a valid URL (e.g. a relative path), fall back to using it as-is.
    }
  }

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${normalizedPath}`;
}

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
      Authorization: `Bearer ${session.access_token}`,
      "Content-Type": "application/json",
    };
  } catch (error) {
    console.error("Failed to get auth headers:", error);
    throw new Error("Authentication required. Please sign in.");
  }
}

/**
 * Authorization header when a session exists; empty object for guests.
 * Used by analyze so a CSV can run without a velvet rope.
 */
async function getOptionalAuthHeaders(): Promise<HeadersInit> {
  try {
    const session = await getSession();
    if (!session?.access_token) {
      return {};
    }
    return {
      Authorization: `Bearer ${session.access_token}`,
    };
  } catch {
    return {};
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

  const response = await fetch(apiPath(`/upload-csv`), {
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
  supplemental1099File?: File;
  filingStatus?: FilingStatus;
  estimatedIncome?: number;
  taxYear?: number;
  mergeMode?: "auto" | "replace";
}

const OBJECT_OBJECT_MESSAGE = "[object Object]";

function isErrorRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Reject empty strings and the JS default object stringification. */
function usableErrorText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed === OBJECT_OBJECT_MESSAGE) return null;
  return trimmed;
}

function messageFromFastApiItem(item: unknown): string | null {
  const asText = usableErrorText(item);
  if (asText) return asText;
  if (!isErrorRecord(item)) return null;
  return (
    usableErrorText(item.msg) ||
    usableErrorText(item.message) ||
    usableErrorText(item.detail)
  );
}

/**
 * Pull a user-facing string out of a FastAPI / JSON error body.
 * Handles `detail` as a string, `{message}`, or validation `[{msg}]`.
 */
function messageFromApiErrorBody(body: unknown): string | null {
  if (body == null) return null;

  const asText = usableErrorText(body);
  if (asText) return asText;

  if (Array.isArray(body)) {
    const parts = body
      .map((item) => messageFromFastApiItem(item))
      .filter((part): part is string => part != null);
    return parts.length > 0 ? parts.join("; ") : null;
  }

  if (!isErrorRecord(body)) return null;

  if (body.detail !== undefined) {
    const fromDetail = messageFromApiErrorBody(body.detail);
    if (fromDetail) return fromDetail;
  }

  return (
    usableErrorText(body.message) ||
    usableErrorText(body.msg) ||
    messageFromApiErrorBody(body.errors)
  );
}

function analysisFailureFallback(status: number, statusText: string): string {
  const fromStatus = usableErrorText(statusText);
  if (fromStatus) return `Analysis failed: ${fromStatus}`;
  return `Analysis failed (${status})`;
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || /^(undefined|null|nan)$/i.test(trimmed)) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function analyzePortfolioPath(params: AnalyzePortfolioParams): string {
  const queryParams = new URLSearchParams();
  if (typeof params.filingStatus === "string") {
    const status = params.filingStatus.trim();
    if (status && !/^(undefined|null|nan)$/i.test(status)) {
      queryParams.set("filing_status", status);
    }
  }
  const income = toFiniteNumber(params.estimatedIncome);
  if (income != null && income >= 0) {
    queryParams.set("estimated_income", String(income));
  }
  const year = toFiniteNumber(params.taxYear);
  if (year != null && Number.isInteger(year) && year >= 2024 && year <= 2026) {
    queryParams.set("tax_year", String(year));
  }
  if (params.mergeMode === "replace") {
    queryParams.set("merge_mode", "replace");
  }
  const qs = queryParams.toString();
  return qs ? `/api/portfolio/analyze?${qs}` : `/api/portfolio/analyze`;
}

/**
 * Upload CSV and get full portfolio analysis with tax-loss harvesting suggestions.
 *
 * Authentication is optional. With a session, POST /api/portfolio/analyze includes
 * a JWT so the run is saved to history. Guests still get a full analysis.
 */
async function analyzePortfolio(
  params: AnalyzePortfolioParams,
): Promise<PortfolioAnalysis> {
  const formData = new FormData();
  formData.append("file", params.file);
  if (params.supplemental1099File) {
    formData.append("supplemental_1099", params.supplemental1099File);
  }

  const url = apiPath(analyzePortfolioPath(params));
  const headers = await getOptionalAuthHeaders();

  const response = await fetch(url, {
    method: "POST",
    body: formData,
    headers,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(
      messageFromApiErrorBody(errorData) ||
        analysisFailureFallback(response.status, response.statusText),
    );
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
    apiPath(`/api/prices?symbols=${symbols.join(",")}`),
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
  const response = await fetch(apiPath(`/api/tax-profile`), {
    method: "POST",
    headers,
    body: JSON.stringify(profile),
  });

  if (!response.ok) {
    throw new Error(`Save failed: ${response.statusText}`);
  }

  return response.json();
}

function normalizeTaxProfile(profile: TaxProfile): TaxProfile {
  return {
    ...profile,
    estimated_annual_income: Number(profile.estimated_annual_income ?? 75000),
    tax_year: Number(profile.tax_year ?? 2026),
    state: profile.state ?? "",
    filing_status: profile.filing_status ?? "single",
  };
}

/**
 * React Query mutation hook for saving tax profile.
 * Invalidates the tax-profile cache on success so subsequent reads get fresh data.
 */
export function useSaveTaxProfile(userId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveTaxProfile,
    onSuccess: (response) => {
      const profile = normalizeTaxProfile(response.profile);
      const queryKey = [
        "tax-profile",
        userId ?? profile.user_id ?? "anonymous",
      ];

      queryClient.setQueryData(queryKey, profile);
      queryClient.invalidateQueries({ queryKey });
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
  const response = await fetch(apiPath(`/api/tax-profile`), {
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return normalizeTaxProfile(await response.json());
}

/**
 * React Query hook for authenticated user's tax profile.
 * Pass `enabled: !!user` to prevent firing before the auth session is ready.
 */
export function useTaxProfile({
  enabled = true,
  userId,
}: {
  enabled?: boolean;
  userId?: string;
} = {}) {
  return useQuery({
    queryKey: ["tax-profile", userId ?? "anonymous"],
    queryFn: fetchTaxProfile,
    enabled: enabled && !!userId,
    staleTime: 0,
    refetchOnMount: "always",
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

  const response = await fetch(apiPath(`/api/tax-brackets?${params}`));

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
  const response = await fetch(apiPath(`/api/portfolio/history`), {
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
 * Uses JWT authentication from getAuthHeaders().
 */
export function usePortfolioHistory(userId?: string) {
  return useQuery({
    queryKey: ["portfolio-history", userId ?? "anonymous"],
    queryFn: fetchPortfolioHistory,
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
 * Requires JWT authentication.
 */
export async function fetchAnalysisById(
  analysisId: string,
): Promise<{ result: PortfolioAnalysis | null } & AnalysisHistoryItem> {
  const headers = await getAuthHeaders();
  const response = await fetch(
    apiPath(`/api/portfolio/analysis/${analysisId}`),
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
  await fetch(apiPath(`/api/portfolio/history/cleanup`), {
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
export async function deleteAnalysis(analysisId: string): Promise<boolean> {
  const headers = await getAuthHeaders();
  const response = await fetch(
    apiPath(`/api/portfolio/analysis/${analysisId}`),
    {
      method: "DELETE",
      headers,
    },
  );
  return response.ok;
}

/**
 * Save a guest desk run into the signed-in user's history.
 *
 * Guest analyze only keeps the result in sessionStorage. After sign-in the
 * dashboard posts that snapshot so it appears in Saved runs.
 */
export async function persistGuestAnalysis(
  analysis: PortfolioAnalysis,
  filename = "guest-run.csv",
): Promise<boolean> {
  const headers = await getAuthHeaders();
  const response = await fetch(apiPath("/api/portfolio/history"), {
    method: "POST",
    headers,
    body: JSON.stringify({ filename, analysis }),
  });
  return response.ok;
}

/**
 * Map analysis / network failures to an end-user message.
 * Never mention local ports or developer start commands.
 * Never surface the JS default `[object Object]` stringification.
 */
export function getAnalysisErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    const fromMessage = usableErrorText(error.message);
    if (fromMessage) {
      if (/failed to fetch|network|econnrefused/i.test(fromMessage)) {
        return "Could not reach the analysis service. Please try again in a few minutes.";
      }
      return fromMessage;
    }
    const fromCause = messageFromApiErrorBody(error.cause);
    if (fromCause) return fromCause;
  } else if (isErrorRecord(error) || Array.isArray(error)) {
    const fromBody = messageFromApiErrorBody(error);
    if (fromBody) return fromBody;
  }

  return "An error occurred";
}

/**
 * Production-safe copy when the health check fails.
 */
export function getBackendUnreachableMessage(): string {
  return "The analysis service is not responding. Please try again in a few minutes.";
}

/**
 * Poll the backend /health endpoint to detect server availability.
 *
 * Uses apiPath so production hits the real backend URL. In local `next dev`,
 * rewrites proxy /health to the API. Refetches every 30 seconds.
 * Callers can use isFetched to distinguish "still checking" from "confirmed down".
 */
export function useBackendHealth() {
  return useQuery({
    queryKey: ["backend-health"],
    queryFn: async () => {
      // Use apiPath so production hits the real backend URL, not the Next.js server.
      const res = await fetch(apiPath("/health"));
      if (!res.ok) throw new Error("Backend unhealthy");
      return true;
    },
    retry: 1,
    retryDelay: 2000,
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
}
