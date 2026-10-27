"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import {
  Download as DownloadIcon,
  Payments as PayIcon,
} from "@mui/icons-material";
import { getSession } from "@/lib/supabase";
import {
  clearPacketCheckoutStarted,
  clearPacketCheckoutStartedIfClaim,
  clearPacketResumeLocal,
  clearPacketResumeSessionMarkers,
  packetCheckoutStartedRecently,
  readPacketCheckoutStarted,
  readPacketResumeExpected,
  rememberPacketCheckoutStarted,
  rememberPacketResumeExpected,
  rememberPacketResumeLocal,
  resumeAnalysisMatches,
} from "@/lib/packetResumeLocal";
import {
  DASHBOARD_RESUME_CHECKOUT_PATH,
  PACKET_RESUME_ANALYSIS_KEY,
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_SAMPLE_KEY,
  RESUME_CHECKOUT_PARAM,
  signInHref,
} from "@/lib/safeNextPath";
import type { PortfolioAnalysis } from "@/lib/types";

let packetResumeCheckoutLocked = false;

/** Tests only. A full reload already starts unlocked. */
export function resetPacketResumeCheckoutLock(): void {
  packetResumeCheckoutLocked = false;
}

const _RAW_API_URL = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
const _isLocalhost = (() => {
  if (process.env.NODE_ENV === "production") return false;
  try {
    const u = new URL(_RAW_API_URL);
    return u.hostname === "localhost" || u.hostname === "127.0.0.1";
  } catch {
    return false;
  }
})();
const API_URL = !_RAW_API_URL || _isLocalhost ? "" : _RAW_API_URL;

export const YEAR_CLOSE_PACKET_TITLE = "Year-close packet — $49";
export const YEAR_CLOSE_PACKET_COPY =
  "This is a reconciliation packet, not a filed Form 8949. The $49 buys the downloadable year-close PDF — lot-level 1099 reconciliation, wash chains, and harvest notes — plus updates for that tax year. It does not change the free 1099 totals, harvest queue, or wash-sale radar already on this page.";
export const PACKET_CHECKOUT_INFLIGHT_KEY =
  "optionstaxhub-packet-checkout-inflight";
export const PACKET_CHECKOUT_CANCELED_COPY =
  "Checkout was closed without payment. Pay $49 when you are ready.";
export const PACKET_CHECKOUT_OTHER_TAB_COPY =
  "Checkout opened in your other tab";
export const PACKET_ENTITLEMENT_UNCONFIRMED_COPY =
  "Couldn't confirm your purchase status. Pay or refresh.";
export const PACKET_PROMOTION_UNCONFIRMED_COPY =
  "Couldn't confirm your purchase status. Refresh to try again.";
export const PACKET_PAID_OTHER_ANALYSIS_COPY =
  "This tax year is already paid on another analysis. This CSV is not included in that packet.";

function isPacketMissingSourceDetail(detail: string): boolean {
  return /payment was received/i.test(detail) && /no source document/i.test(detail);
}

function isPacketAlreadyPaidExistingDetail(detail: string): boolean {
  return /already paid/i.test(detail) && /download your existing packet/i.test(detail);
}

function isPacketSameYearDuplicateDetail(detail: string): boolean {
  return (
    /PACKET_GRANT_SAME_YEAR_DUPLICATE/.test(detail) &&
    /another checkout session/i.test(detail) &&
    /recorded for a refund/i.test(detail)
  );
}

function isPacketConflictReceiptDetail(detail: string): boolean {
  return /already recorded as a refund/i.test(detail) && /does not grant a packet/i.test(detail);
}

function isPacketRefundRecordedDetail(detail: string): boolean {
  return isPacketSameYearDuplicateDetail(detail) || isPacketConflictReceiptDetail(detail);
}

function isPacketFinalizingDetail(detail: string): boolean {
  return /still being finalized/i.test(detail) && /confirming the payment/i.test(detail);
}

function paidStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-paid:${analysisId}`;
}

function canonicalStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-canonical:${analysisId}`;
}

function pendingSessionStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-pending:${analysisId}`;
}

function pendingCheckoutUrlStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-pending-url:${analysisId}`;
}

function pendingConfirmStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-pending-confirm:${analysisId}`;
}

function pendingOpenStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-pending-open:${analysisId}`;
}

function trustedCheckoutUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "checkout.stripe.com"
      ? value
      : null;
  } catch {
    return null;
  }
}

function localAnalysisIdentity(analysis: PortfolioAnalysis): string {
  const source = JSON.stringify(compactAnalysis(analysis));
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < source.length; index += 1) {
    const code = source.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return `local-${(first >>> 0).toString(16)}${(second >>> 0).toString(16)}`;
}

const HYPHENATED_ANALYSIS_UUID =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

/**
 * Same rule as the server `_analysis_ids_match`: exact text, except a
 * 36-character hyphenated UUID also matches in any letter case. Braces,
 * `urn:uuid:`, and 32-hex text stay distinct.
 */
export function analysisIdsMatch(left: string, right: string): boolean {
  const normalizedLeft = left.trim();
  const normalizedRight = right.trim();
  if (normalizedLeft === normalizedRight) {
    return true;
  }
  if (
    HYPHENATED_ANALYSIS_UUID.test(normalizedLeft) &&
    HYPHENATED_ANALYSIS_UUID.test(normalizedRight)
  ) {
    return normalizedLeft.toLowerCase() === normalizedRight.toLowerCase();
  }
  return false;
}

export function yearClosePacketStorageId(analysis: PortfolioAnalysis): string {
  return analysis.analysis_id || localAnalysisIdentity(analysis);
}

export function isYearClosePacketPaid(analysisId: string): boolean {
  const canonicalId = readSessionItem(canonicalStorageKey(analysisId));
  const stored = readSessionItem(paidStorageKey(canonicalId || analysisId));
  return Boolean(stored && stored.startsWith("cs_"));
}

export function rememberYearClosePacketPaid(
  analysisId: string,
  sessionId: string,
): void {
  if (!analysisId || !sessionId.startsWith("cs_")) {
    return;
  }
  writeSessionItem(paidStorageKey(analysisId), sessionId);
}

function readSessionItem(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSessionItem(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // ignore quota / private-mode failures
  }
}

function clearSessionItem(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // ignore
  }
}

function releaseCheckoutClaim(claim: string | undefined): void {
  if (!claim) {
    return;
  }
  clearPacketCheckoutStartedIfClaim(claim);
}

function resumeCheckoutArmed(): boolean {
  let urlResume = false;
  try {
    urlResume =
      new URLSearchParams(window.location.search).get(RESUME_CHECKOUT_PARAM) ===
      "1";
  } catch {
    urlResume = false;
  }
  return urlResume || readSessionItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
}

/** Hold Pay while this panel is not the run the resume handoff names. */
function payHeldForDifferentResume(analysis: PortfolioAnalysis): boolean {
  if (!readPacketResumeExpected() || resumeAnalysisMatches(analysis)) {
    return false;
  }
  return resumeCheckoutArmed();
}

type EntitlementGate = "unknown" | "paid" | "unpaid" | "unconfirmed";

/**
 * True only for a signed-in resume of this exact run, before we know whether
 * the year is already paid. Other-tab claims, cancels, and mismatches stay off
 * this path so Pay is not held and no entitlement request is sent.
 */
function resumeEntitlementPending(analysis: PortfolioAnalysis): boolean {
  if (analysis.packet_unlocked === true || isYearClosePacketPaid(
    yearClosePacketStorageId(analysis),
  )) {
    return false;
  }
  if (!resumeCheckoutArmed() || packetCheckoutStartedRecently()) {
    return false;
  }
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(window.location.search);
  } catch {
    return false;
  }
  if (params.get("packet_session") || params.get("packet_canceled") === "1") {
    return false;
  }
  if (!analysis.analysis_id || analysis.analysis_id === "local-analysis") {
    return false;
  }
  const year = analysis.tax_profile?.tax_year;
  if (typeof year !== "number" || !Number.isFinite(year)) {
    return false;
  }
  return resumeAnalysisMatches(analysis);
}

/**
 * A restored guest or local run has no resume markers, but the year may
 * already be paid. Check entitlement before Pay $49 can be pressed.
 * The recent-checkout claim stays on the resume path only.
 */
function restoredEntitlementPending(
  analysis: PortfolioAnalysis,
  restoredRun: boolean,
): boolean {
  if (!restoredRun) {
    return false;
  }
  if (
    analysis.packet_unlocked === true ||
    isYearClosePacketPaid(yearClosePacketStorageId(analysis))
  ) {
    return false;
  }
  const year = analysis.tax_profile?.tax_year;
  return typeof year === "number" && Number.isFinite(year);
}

/**
 * A fresh signed-in CSV upload has no resume markers. The year may already
 * be paid, so Pay stays held until entitlement returns. Resume checkout
 * keeps its own guard. The sample desk is not this path.
 */
function freshUploadEntitlementPending(
  analysis: PortfolioAnalysis,
  freshUpload: boolean,
): boolean {
  if (!freshUpload || analysis.sample_run === true || resumeCheckoutArmed()) {
    return false;
  }
  if (
    analysis.packet_unlocked === true ||
    isYearClosePacketPaid(yearClosePacketStorageId(analysis))
  ) {
    return false;
  }
  if (!analysis.analysis_id || analysis.analysis_id === "local-analysis") {
    return false;
  }
  const year = analysis.tax_profile?.tax_year;
  return typeof year === "number" && Number.isFinite(year);
}

function isGuestSnapshotRerunDetail(detail: string): boolean {
  return (
    /guest snapshot must be restored/i.test(detail) ||
    /no owner-scoped private snapshot/i.test(detail) ||
    /re-run it before checkout/i.test(detail)
  );
}

function stripResumeCheckoutParam(): void {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(RESUME_CHECKOUT_PARAM)) {
      return;
    }
    url.searchParams.delete(RESUME_CHECKOUT_PARAM);
    const next = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState(window.history.state, "", next);
  } catch {
    // ignore
  }
}

function stripPacketQueryParams(): void {
  try {
    const url = new URL(window.location.href);
    if (
      !url.searchParams.has("packet_canceled") &&
      !url.searchParams.has("packet_session") &&
      !url.searchParams.has("packet_analysis")
    ) {
      return;
    }
    url.searchParams.delete("packet_canceled");
    url.searchParams.delete("packet_session");
    url.searchParams.delete("packet_analysis");
    const next = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState(window.history.state, "", next);
  } catch {
    // ignore
  }
}

async function authHeaders(options?: { json?: boolean }): Promise<Record<string, string>> {
  const session = await getSession();
  if (!session?.access_token) {
    throw new Error("Authentication required. Please sign in.");
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${session.access_token}`,
  };
  if (options?.json !== false) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

export function purchaseDateFromSuggestionId(
  suggestionId?: string,
): string | undefined {
  const parts = (suggestionId || "").split("::").filter(Boolean);
  const token = parts[3];
  return token && /^\d{4}-\d{2}-\d{2}$/.test(token) ? token : undefined;
}

export function compactAnalysis(analysis: PortfolioAnalysis) {
  return {
    analysis_id: analysis.analysis_id,
    tax_profile: analysis.tax_profile,
    supplemental_1099: analysis.supplemental_1099 ?? null,
    wash_sale_flags: analysis.wash_sale_flags,
    summary: {
      realized_summary: analysis.summary?.realized_summary ?? null,
    },
    tax_lots: (analysis.tax_lots || []).map((lot) => ({
      symbol: lot.symbol,
      quantity: lot.quantity,
      purchase_date: lot.purchase_date,
      wash_sale_disallowed: lot.wash_sale_disallowed,
    })),
    lot_match_report: analysis.lot_match_report ?? null,
    suggestions: (analysis.suggestions || []).map((suggestion) => ({
      symbol: suggestion.symbol,
      display_label: suggestion.display_label,
      suggestion_id: suggestion.suggestion_id,
      lot_details: suggestion.lot_details,
      quantity: suggestion.quantity,
      purchase_date: purchaseDateFromSuggestionId(suggestion.suggestion_id),
      cost_basis_per_share: suggestion.cost_basis_per_share,
      estimated_loss: suggestion.estimated_loss,
      tax_savings_estimate: suggestion.tax_savings_estimate,
      is_long_term: suggestion.is_long_term,
      wash_sale_risk: suggestion.wash_sale_risk,
      wash_sale_explanation: suggestion.wash_sale_explanation,
    })),
  };
}

function triggerPdfDownload(blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "year-close-packet.pdf";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function YearClosePacketPanel(props: Readonly<{
  analysis: PortfolioAnalysis;
  guest?: boolean;
  restoredRun?: boolean;
  freshUpload?: boolean;
  onPaidChange?: (paid: boolean) => void;
  onSampleRerunForCheckout?: () => boolean;
}>) {
  const identity = yearClosePacketStorageId(props.analysis);
  return (
    <YearClosePacketPanelForAnalysis
      key={identity}
      analysis={props.analysis}
      analysisStorageId={identity}
      guest={props.guest === true}
      restoredRun={props.restoredRun === true}
      freshUpload={props.freshUpload === true}
      onPaidChange={props.onPaidChange}
      onSampleRerunForCheckout={props.onSampleRerunForCheckout}
    />
  );
}

function YearClosePacketPanelForAnalysis({
  analysis,
  analysisStorageId,
  guest,
  restoredRun,
  freshUpload,
  onPaidChange,
  onSampleRerunForCheckout,
}: Readonly<{
  analysis: PortfolioAnalysis;
  analysisStorageId: string;
  guest: boolean;
  restoredRun: boolean;
  freshUpload: boolean;
  onPaidChange?: (paid: boolean) => void;
  onSampleRerunForCheckout?: () => boolean;
}>) {
  const router = useRouter();
  const analysisId = analysis.analysis_id || "local-analysis";
  const onPaidChangeRef = useRef(onPaidChange);
  onPaidChangeRef.current = onPaidChange;
  const analysisRef = useRef(analysis);
  analysisRef.current = analysis;
  const [canonicalAnalysisId, setCanonicalAnalysisId] = useState(
    () => readSessionItem(canonicalStorageKey(analysisStorageId)) || analysisId,
  );
  const [busy, setBusy] = useState<"pay" | "download" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [canceledNotice, setCanceledNotice] = useState(false);
  const [paid, setPaid] = useState(() => {
    if (guest) {
      return false;
    }
    return (
      analysis.packet_unlocked === true ||
      isYearClosePacketPaid(analysisStorageId)
    );
  });
  const [entitlementGate, setEntitlementGate] = useState<EntitlementGate>(() => {
    if (guest) {
      return "unpaid";
    }
    if (
      analysis.packet_unlocked === true ||
      isYearClosePacketPaid(analysisStorageId)
    ) {
      return "paid";
    }
    return resumeEntitlementPending(analysis) ||
      restoredEntitlementPending(analysis, restoredRun) ||
      freshUploadEntitlementPending(analysis, freshUpload)
      ? "unknown"
      : "unpaid";
  });
  // Paid year whose receipt did not name this run, and promotion did not
  // confirm it. Pay stays held so refresh cannot open a second Checkout.
  // Cleared once this mount becomes paid, including a later unlocked refetch.
  const [paidYearUnconfirmed, setPaidYearUnconfirmed] = useState(false);
  const [otherTabNotice, setOtherTabNotice] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
  const [confirmPending, setConfirmPending] = useState(
    () => readSessionItem(pendingConfirmStorageKey(analysisStorageId)) === "1",
  );
  const [resumeOpenCheckout, setResumeOpenCheckout] = useState(
    () => readSessionItem(pendingOpenStorageKey(analysisStorageId)) === "1",
  );
  const [existingPacketDownload, setExistingPacketDownload] = useState(false);
  const [extraChargeRefund, setExtraChargeRefund] = useState(false);
  const confirmAttempt = useRef(0);
  const missingAnalysisId = canonicalAnalysisId === "local-analysis";

  // Same-tab Stripe redirect leaves this page in back-forward cache with
  // busy="pay". Closing checkout (or Back) restores that spinning button.
  useEffect(() => {
    const releasePaySpinner = () => {
      setBusy((current) => (current === "pay" ? null : current));
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
    };
    const onPageShow = () => {
      releasePaySpinner();
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  const persistConfirmFlag = (pending: boolean) => {
    setConfirmPending(pending);
    if (pending) {
      writeSessionItem(pendingConfirmStorageKey(analysisStorageId), "1");
    } else {
      clearSessionItem(pendingConfirmStorageKey(analysisStorageId));
    }
  };

  const persistResumeFlag = (open: boolean) => {
    setResumeOpenCheckout(open);
    if (open) {
      writeSessionItem(pendingOpenStorageKey(analysisStorageId), "1");
    } else {
      clearSessionItem(pendingOpenStorageKey(analysisStorageId));
    }
  };

  const applyAlreadyPaidRef = useRef<
    (session: string, paidAnalysisId: string, claim?: string) => void
  >(() => {});
  applyAlreadyPaidRef.current = (session, paidAnalysisId, claim) => {
    releaseCheckoutClaim(claim);
    clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
    setCanonicalAnalysisId(paidAnalysisId);
    setSessionId(session);
    persistConfirmFlag(false);
    persistResumeFlag(false);
    setCheckoutUrl(null);
    setPaid(true);
    setEntitlementGate("paid");
    clearSessionItem(pendingSessionStorageKey(analysisStorageId));
    clearSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId));
    writeSessionItem(canonicalStorageKey(analysisStorageId), paidAnalysisId);
    writeSessionItem(paidStorageKey(paidAnalysisId), session);
    onPaidChangeRef.current?.(true);
    setError(null);
    setBusy(null);
  };

  const refetchPaidYear = async (): Promise<"match" | "other-run" | "miss"> => {
    const year = analysisRef.current.tax_profile?.tax_year;
    if (typeof year !== "number" || !Number.isFinite(year)) {
      return "miss";
    }
    let headers: Record<string, string>;
    try {
      headers = await authHeaders();
    } catch {
      return "miss";
    }
    const response = await fetch(
      `${API_URL}/api/year-close-packet/entitlement?tax_year=${encodeURIComponent(String(year))}`,
      {
        method: "GET",
        headers,
      },
    );
    if (!response.ok) {
      return "miss";
    }
    const data = await response.json().catch(() => null);
    if (!data || data.paid !== true) {
      return "miss";
    }
    const sid =
      typeof data.session_id === "string" && data.session_id.startsWith("cs_")
        ? data.session_id
        : null;
    if (!sid) {
      return "miss";
    }
    const entitled =
      typeof data.analysis_id === "string" ? data.analysis_id.trim() : "";
    const currentId = (analysisRef.current.analysis_id || "").trim();
    if (!entitled || !currentId) {
      return "miss";
    }
    if (!analysisIdsMatch(entitled, currentId)) {
      return "other-run";
    }
    applyAlreadyPaidRef.current(sid, entitled);
    return "match";
  };
  const refetchPaidYearRef = useRef(refetchPaidYear);
  refetchPaidYearRef.current = refetchPaidYear;

  const confirmPacketSession = async (sid: string, confirmedAnalysisId: string) => {
    const attempt = ++confirmAttempt.current;
    if (sid.startsWith("cs_")) {
      writeSessionItem(pendingSessionStorageKey(analysisStorageId), sid);
    }
    persistConfirmFlag(true);
    persistResumeFlag(false);
    setSessionId(sid);
    setBusy("confirm");
    setError(null);
    let retryableFailure = true;
    let checkoutStillOpen = false;
    let alreadyPaidExisting = false;
    let refundRecorded = false;
    let finalizing = false;
    try {
      const headers = await authHeaders();
      const response = await fetch(`${API_URL}/api/year-close-packet/confirm`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          analysis_id: confirmedAnalysisId,
          packet_analysis: confirmedAnalysisId,
          session_id: sid,
          analysis: compactAnalysis(analysis),
        }),
      });
      if (attempt !== confirmAttempt.current) {
        return;
      }
      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        const detail = String(errData?.detail || "");
        checkoutStillOpen = response.status === 409 && /still open/i.test(detail);
        // Missing source, a verified paid packet, and an unverified extra
        // charge are terminal. Finalizing keeps this checkout session so Pay
        // retries confirm and does not open Stripe.
        const missingSource =
          response.status === 409 && isPacketMissingSourceDetail(detail);
        // Only the verified-paid sentence offers the existing download. The
        // generic conflict and same-year sentences are a terminal extra charge:
        // Pay stays disabled for this session and confirm does not retry.
        alreadyPaidExisting =
          response.status === 409 && isPacketAlreadyPaidExistingDetail(detail);
        refundRecorded =
          response.status === 409 && isPacketRefundRecordedDetail(detail);
        finalizing = response.status === 409 && isPacketFinalizingDetail(detail);
        retryableFailure =
          !missingSource &&
          !alreadyPaidExisting &&
          !refundRecorded &&
          !finalizing &&
          (response.status >= 500 || checkoutStillOpen);
        throw new Error(errData?.detail || "Could not confirm packet payment.");
      }
      const confirmData = await response.json();
      const canonicalId =
        typeof confirmData?.analysis_id === "string"
          ? confirmData.analysis_id
          : confirmedAnalysisId;
      setCanonicalAnalysisId(canonicalId);
      writeSessionItem(canonicalStorageKey(analysisStorageId), canonicalId);
      setPaid(true);
      setExistingPacketDownload(false);
      setExtraChargeRefund(false);
      persistConfirmFlag(false);
      persistResumeFlag(false);
      setCheckoutUrl(null);
      clearSessionItem(pendingSessionStorageKey(analysisStorageId));
      clearSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId));
      writeSessionItem(paidStorageKey(canonicalId), sid);
      onPaidChange?.(true);
      stripPacketQueryParams();
    } catch (err) {
      if (attempt !== confirmAttempt.current) {
        return;
      }
      setError(err instanceof Error ? err.message : "Could not confirm payment.");
      if (alreadyPaidExisting) {
        setExtraChargeRefund(false);
        setExistingPacketDownload(true);
        persistConfirmFlag(false);
        persistResumeFlag(false);
        setSessionId(null);
        setCheckoutUrl(null);
        clearSessionItem(pendingSessionStorageKey(analysisStorageId));
        clearSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId));
        stripPacketQueryParams();
      } else if (refundRecorded) {
        setExtraChargeRefund(true);
        setExistingPacketDownload(false);
        persistConfirmFlag(false);
        persistResumeFlag(false);
        setSessionId(null);
        setCheckoutUrl(null);
        clearSessionItem(pendingSessionStorageKey(analysisStorageId));
        clearSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId));
        stripPacketQueryParams();
      } else if (finalizing) {
        setExistingPacketDownload(false);
        persistConfirmFlag(true);
        persistResumeFlag(false);
      } else if (checkoutStillOpen) {
        persistConfirmFlag(false);
        persistResumeFlag(true);
        stripPacketQueryParams();
      } else if (retryableFailure) {
        persistConfirmFlag(true);
        persistResumeFlag(false);
      } else {
        setExistingPacketDownload(false);
        persistConfirmFlag(false);
        persistResumeFlag(false);
        setSessionId(null);
        setCheckoutUrl(null);
        clearSessionItem(pendingSessionStorageKey(analysisStorageId));
        clearSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId));
        stripPacketQueryParams();
      }
    } finally {
      if (attempt === confirmAttempt.current) {
        setBusy(null);
      }
    }
  };

  useEffect(() => {
    const savedCanonical = readSessionItem(canonicalStorageKey(analysisStorageId));
    const savedCanonicalId = savedCanonical || analysisId;
    setCanonicalAnalysisId(savedCanonicalId);
    const pendingSessionId = readSessionItem(pendingSessionStorageKey(analysisStorageId));
    const pendingCheckoutUrl = trustedCheckoutUrl(
      readSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId)),
    );
    if (pendingSessionId?.startsWith("cs_")) {
      setSessionId(pendingSessionId);
      setCheckoutUrl(pendingCheckoutUrl);
      setConfirmPending(
        readSessionItem(pendingConfirmStorageKey(analysisStorageId)) === "1",
      );
      setResumeOpenCheckout(
        readSessionItem(pendingOpenStorageKey(analysisStorageId)) === "1",
      );
    }
    if (guest) {
      // A stored cs_ or packet_unlocked flag belongs to whoever last signed in.
      setPaid(false);
    } else if (analysis.packet_unlocked) {
      setPaid(true);
      if (analysis.packet_session_id?.startsWith("cs_")) {
        setSessionId(analysis.packet_session_id);
        writeSessionItem(paidStorageKey(savedCanonicalId), analysis.packet_session_id);
      }
      onPaidChange?.(true);
    } else {
      const stored =
        readSessionItem(paidStorageKey(savedCanonicalId)) ||
        (!savedCanonical
          ? readSessionItem(paidStorageKey(analysisStorageId))
          : null);
      if (stored && stored.startsWith("cs_")) {
        setPaid(true);
        setSessionId(stored);
        onPaidChange?.(true);
      }
    }

    const params = new URLSearchParams(window.location.search);
    const sid = params.get("packet_session");
    const packetAnalysisId = params.get("packet_analysis");
    const confirmedAnalysisId = packetAnalysisId || savedCanonicalId;
    const canceled = params.get("packet_canceled") === "1";
    const inflight = readSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY) === analysisStorageId;

    if (sid) {
      if (
        (packetAnalysisId &&
          packetAnalysisId !== savedCanonicalId &&
          packetAnalysisId !== analysisId) ||
        (!packetAnalysisId && !inflight)
      ) {
        return;
      }
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
      setCanceledNotice(false);
      void confirmPacketSession(sid, confirmedAnalysisId);
      return;
    }

    if (canceled || (inflight && !pendingSessionId)) {
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
      // Stripe sent the shopper back without paying. Drop only this claim so
      // a later Pay is not treated as another tab's open checkout.
      if (canceled) {
        releaseCheckoutClaim(readPacketCheckoutStarted() ?? undefined);
      }
      // This effect strips packet_canceled before the resume effect reads the
      // URL. Drop the guest-pay marker here so a closed checkout cannot reopen.
      clearPacketResumeSessionMarkers();
      clearPacketResumeLocal();
      persistConfirmFlag(false);
      persistResumeFlag(false);
      setBusy(null);
      setCanceledNotice(true);
      stripPacketQueryParams();
      stripResumeCheckoutParam();
    }
  }, [analysis, analysisId, analysisStorageId, guest]);

  // A paid year must not sit next to "closed without payment", and the
  // resume handoff must not auto-start checkout for that year again.
  useEffect(() => {
    if (!paid) {
      return;
    }
    setCanceledNotice(false);
    setPaidYearUnconfirmed(false);
    clearPacketResumeSessionMarkers();
    clearPacketResumeLocal();
    clearPacketCheckoutStarted();
  }, [paid]);

  useEffect(() => {
    const current = analysisRef.current;
    if (guest) {
      setPaid(false);
      setEntitlementGate("unpaid");
      return;
    }
    if (
      paid ||
      current.packet_unlocked === true ||
      isYearClosePacketPaid(analysisStorageId)
    ) {
      setEntitlementGate("paid");
      return;
    }
    const checkEntitlement =
      resumeEntitlementPending(current) ||
      restoredEntitlementPending(current, restoredRun) ||
      freshUploadEntitlementPending(current, freshUpload);
    if (!checkEntitlement) {
      setEntitlementGate("unpaid");
      return;
    }
    const year = current.tax_profile?.tax_year;
    if (typeof year !== "number" || !Number.isFinite(year)) {
      setEntitlementGate("unpaid");
      return;
    }
    let cancelled = false;
    setEntitlementGate("unknown");
    const showPromotionClientError = (detail: string) => {
      setPaidYearUnconfirmed(false);
      setBusy(null);
      setError(detail);
      // Leave the resume effect off "unpaid" so it does not post again.
      // Pay stays usable because paidYearUnconfirmed is clear.
      setEntitlementGate("unconfirmed");
    };
    const promotePaidAnalysis = async (): Promise<"paid" | "hold" | "client"> => {
      const headers = await authHeaders();
      const response = await fetch(`${API_URL}/api/year-close-packet/checkout`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          analysis_id: canonicalAnalysisId,
          analysis: compactAnalysis(analysisRef.current),
        }),
      });
      if (cancelled) {
        return "hold";
      }
      if (!response.ok) {
        if (response.status >= 400 && response.status < 500) {
          const errData = await response.json().catch(() => null);
          const detail = String(
            errData && typeof errData === "object" ? errData.detail || "" : "",
          );
          if (
            response.status === 409 &&
            isPacketMissingSourceDetail(detail) &&
            freshUploadEntitlementPending(analysisRef.current, freshUpload)
          ) {
            const recovered = await refetchPaidYearRef.current();
            if (cancelled) {
              return "hold";
            }
            if (recovered === "match") {
              return "paid";
            }
            if (recovered === "other-run") {
              showPromotionClientError(PACKET_PAID_OTHER_ANALYSIS_COPY);
              return "client";
            }
          }
          if (!cancelled) {
            showPromotionClientError(detail || "Failed to start checkout.");
          }
          return "client";
        }
        return "hold";
      }
      const payload = await response.json();
      if (cancelled) {
        return "hold";
      }
      if (payload?.already_paid === true && typeof payload.session_id === "string") {
        const paidAnalysisId =
          typeof payload.analysis_id === "string" && payload.analysis_id.length > 0
            ? payload.analysis_id
            : canonicalAnalysisId;
        applyAlreadyPaidRef.current(payload.session_id, paidAnalysisId);
        return "paid";
      }
      // checkout_url is a new charge. Do not assign location.href.
      return "hold";
    };
    const loadEntitlement = async (
      accessToken: string,
    ): Promise<"paid" | "unpaid"> => {
      const response = await fetch(
        `${API_URL}/api/year-close-packet/entitlement?tax_year=${encodeURIComponent(String(year))}`,
        {
          method: "GET",
          headers: { Authorization: `Bearer ${accessToken}` },
        },
      );
      if (cancelled || !response.ok) {
        if (cancelled) {
          return "unpaid";
        }
        throw new Error("entitlement unavailable");
      }
      const data = await response.json();
      if (cancelled) {
        return "unpaid";
      }
      if (data?.paid === true) {
        const sid =
          typeof data.session_id === "string" && data.session_id.startsWith("cs_")
            ? data.session_id
            : null;
        const entitledAnalysisId =
          typeof data.analysis_id === "string" ? data.analysis_id.trim() : "";
        const currentAnalysisId = (analysisRef.current.analysis_id || "").trim();
        const idsMatch =
          entitledAnalysisId.length > 0 &&
          currentAnalysisId.length > 0 &&
          analysisIdsMatch(entitledAnalysisId, currentAnalysisId);
        // A missing, blank, or different id is not this snapshot. Promote
        // before Download is offered. Stay off "unpaid" so auto-resume
        // cannot open a second Checkout.
        const promoteThisRun =
          analysisRef.current.packet_unlocked !== true &&
          currentAnalysisId.length > 0 &&
          !idsMatch;
        if (promoteThisRun) {
          if (!sid) {
            throw new Error("entitlement unavailable");
          }
          setBusy("pay");
          const holdUnconfirmedPromotion = () => {
            setBusy(null);
            setPaidYearUnconfirmed(true);
            setEntitlementGate("unconfirmed");
          };
          try {
            const promoted = await promotePaidAnalysis();
            if (cancelled || promoted === "client") {
              return "unpaid";
            }
            if (promoted !== "paid") {
              holdUnconfirmedPromotion();
              return "unpaid";
            }
            return "paid";
          } catch {
            if (!cancelled) {
              holdUnconfirmedPromotion();
            }
            return "unpaid";
          }
        }
        if (analysisRef.current.packet_unlocked === true || idsMatch) {
          if (sid) {
            writeSessionItem(paidStorageKey(canonicalAnalysisId), sid);
            setSessionId(sid);
          }
          setPaid(true);
          setEntitlementGate("paid");
          onPaidChangeRef.current?.(true);
          return "paid";
        }
        throw new Error("entitlement unavailable");
      }
      setEntitlementGate("unpaid");
      return "unpaid";
    };
    void (async () => {
      let accessToken = "";
      try {
        const session = await getSession();
        accessToken = session?.access_token || "";
      } catch {
        accessToken = "";
      }
      if (cancelled) {
        return;
      }
      const latest = analysisRef.current;
      const resumePending = resumeEntitlementPending(latest);
      const restoredPending = restoredEntitlementPending(latest, restoredRun);
      const freshPending = freshUploadEntitlementPending(latest, freshUpload);
      if (!accessToken || (!resumePending && !restoredPending && !freshPending)) {
        setEntitlementGate("unpaid");
        return;
      }
      // A checkout claim in the last five minutes skips only the resume
      // check. A restored run and a fresh upload still have to learn
      // whether the year is paid.
      if (!restoredPending && !freshPending && packetCheckoutStartedRecently()) {
        setEntitlementGate("unpaid");
        return;
      }
      try {
        await loadEntitlement(accessToken);
      } catch {
        if (cancelled) {
          return;
        }
        try {
          await loadEntitlement(accessToken);
        } catch {
          // Manual Pay stays available. Auto-resume only runs once the
          // check has resolved as unpaid, and the server still refuses a
          // second charge for a year that is already paid.
          if (!cancelled) {
            setEntitlementGate("unconfirmed");
          }
        }
      }
    })();
    return () => {
      cancelled = true;
      setBusy((current) => (current === "pay" ? null : current));
    };
  }, [
    guest,
    restoredRun,
    freshUpload,
    paid,
    analysis.analysis_id,
    analysis.packet_unlocked,
    analysis.sample_run,
    analysis.tax_profile?.tax_year,
    analysisStorageId,
    canonicalAnalysisId,
  ]);

  const handlePay = async (options?: {
    guestRedirect?: boolean;
    claim?: string;
  }) => {
    if (payHeldForDifferentResume(analysis)) {
      return;
    }
    const guestRedirect = options?.guestRedirect !== false;
    if (paid || (!guest && analysis.packet_unlocked === true)) {
      return;
    }
    if (existingPacketDownload || extraChargeRefund) {
      return;
    }
    // Finalizing with a stored cs_ retries confirm. With no session (a
    // session-less download 409), Pay must still open checkout.
    if (sessionId && isPacketFinalizingDetail(error ?? "")) {
      await confirmPacketSession(sessionId, canonicalAnalysisId);
      return;
    }
    if (sessionId && confirmPending) {
      await confirmPacketSession(sessionId, canonicalAnalysisId);
      return;
    }
    if (sessionId && resumeOpenCheckout && checkoutUrl) {
      setBusy("pay");
      setError(null);
      try {
        globalThis.location.href = checkoutUrl;
      } catch {
        setBusy(null);
      }
      return;
    }
    if (sessionId) {
      await confirmPacketSession(sessionId, canonicalAnalysisId);
      return;
    }
    setBusy("pay");
    setError(null);
    setCanceledNotice(false);
    try {
      const session = await getSession();
      if (!session?.access_token) {
        releaseCheckoutClaim(options?.claim);
        if (!guestRedirect) {
          setError("Authentication required. Please sign in.");
          setBusy(null);
          return;
        }
        writeSessionItem(PACKET_RESUME_CHECKOUT_KEY, "1");
        if (analysis.sample_run) {
          writeSessionItem(PACKET_RESUME_SAMPLE_KEY, "1");
        } else {
          clearSessionItem(PACKET_RESUME_SAMPLE_KEY);
        }
        const resumeAnalysisId =
          typeof analysis.analysis_id === "string" &&
          analysis.analysis_id.length > 0
            ? analysis.analysis_id
            : null;
        const resumeTaxYear = analysis.tax_profile?.tax_year;
        if (
          resumeAnalysisId &&
          typeof resumeTaxYear === "number" &&
          Number.isFinite(resumeTaxYear)
        ) {
          rememberPacketResumeExpected(resumeAnalysisId, resumeTaxYear);
        } else {
          clearSessionItem(PACKET_RESUME_ANALYSIS_KEY);
        }
        rememberPacketResumeLocal({
          sample: analysis.sample_run === true,
          analysisId: resumeAnalysisId,
          taxYear:
            typeof resumeTaxYear === "number" && Number.isFinite(resumeTaxYear)
              ? resumeTaxYear
              : null,
        });
        try {
          router.push(signInHref(DASHBOARD_RESUME_CHECKOUT_PATH));
        } catch (navError) {
          setError(
            navError instanceof Error
              ? navError.message
              : "Could not open sign-in.",
          );
        }
        setBusy(null);
        return;
      }
      writeSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY, analysisStorageId);
      const headers = await authHeaders();
      if (options?.claim && readPacketCheckoutStarted() !== options.claim) {
        clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
        setOtherTabNotice(true);
        clearPacketResumeSessionMarkers();
        stripResumeCheckoutParam();
        setBusy(null);
        return;
      }
      const response = await fetch(`${API_URL}/api/year-close-packet/checkout`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          analysis_id: canonicalAnalysisId,
          analysis: compactAnalysis(analysis),
        }),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        const detail = String(errData?.detail || "");
        if (response.status === 409 && isPacketMissingSourceDetail(detail)) {
          const recovered = await refetchPaidYearRef.current();
          if (recovered === "match") {
            releaseCheckoutClaim(options?.claim);
            clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
            setBusy(null);
            return;
          }
          if (recovered === "other-run") {
            releaseCheckoutClaim(options?.claim);
            throw new Error(PACKET_PAID_OTHER_ANALYSIS_COPY);
          }
        }
        if (
          response.status === 409 &&
          analysis.sample_run &&
          isGuestSnapshotRerunDetail(detail)
        ) {
          // Drop this tab's claim before the re-run so that checkout can
          // start once more instead of looking like another tab's session.
          releaseCheckoutClaim(options?.claim);
          if (onSampleRerunForCheckout?.()) {
            packetResumeCheckoutLocked = false;
            clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
            setBusy(null);
            return;
          }
        }
        releaseCheckoutClaim(options?.claim);
        throw new Error(detail || "Failed to start checkout.");
      }
      const data = await response.json();
      if (data?.already_paid === true && typeof data?.session_id === "string") {
        const paidAnalysisId =
          typeof data.analysis_id === "string" ? data.analysis_id : canonicalAnalysisId;
        applyAlreadyPaidRef.current(data.session_id, paidAnalysisId, options?.claim);
        return;
      }
      if (typeof data?.checkout_url !== "string" || data.checkout_url.length === 0) {
        throw new Error("Checkout did not return a payment link.");
      }
      const safeCheckoutUrl = trustedCheckoutUrl(data.checkout_url);
      if (!safeCheckoutUrl) {
        throw new Error("Checkout returned an invalid payment link.");
      }
      if (typeof data?.analysis_id === "string" && data.analysis_id.length > 0) {
        setCanonicalAnalysisId(data.analysis_id);
        writeSessionItem(canonicalStorageKey(analysisStorageId), data.analysis_id);
      }
      if (typeof data?.session_id === "string" && data.session_id.startsWith("cs_")) {
        setSessionId(data.session_id);
        writeSessionItem(pendingSessionStorageKey(analysisStorageId), data.session_id);
        setCheckoutUrl(safeCheckoutUrl);
        persistConfirmFlag(false);
        persistResumeFlag(false);
        writeSessionItem(pendingCheckoutUrlStorageKey(analysisStorageId), safeCheckoutUrl);
      }
      try {
        globalThis.location.href = safeCheckoutUrl;
      } catch {
        // jsdom rejects in-page navigation. A real browser unloads this
        // page instead; busy stays "pay" until pageshow / remount.
      }
    } catch (err) {
      releaseCheckoutClaim(options?.claim);
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
      setError(err instanceof Error ? err.message : "Failed to start checkout.");
      setBusy(null);
    }
  };

  const handlePayRef = useRef(handlePay);
  handlePayRef.current = handlePay;

  // After sign-in, start the same checkout once. Do not send the guest back
  // to sign-in from here, and do not touch packet_session / packet_canceled.
  // A different analysis id, a missing id, or a checkout another tab already
  // claimed does not auto-post.
  useEffect(() => {
    if (guest || packetResumeCheckoutLocked) {
      return;
    }
    if (
      paid ||
      analysis.packet_unlocked ||
      entitlementGate !== "unpaid" ||
      missingAnalysisId ||
      existingPacketDownload ||
      extraChargeRefund
    ) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("packet_session") || params.get("packet_canceled") === "1") {
      return;
    }
    if (params.get(RESUME_CHECKOUT_PARAM) !== "1") {
      return;
    }

    const showOtherTab = () => {
      setOtherTabNotice(true);
      clearPacketResumeSessionMarkers();
      clearPacketResumeLocal();
      stripResumeCheckoutParam();
    };

    if (packetCheckoutStartedRecently()) {
      // Wipe the handoff only when this panel is the claimed run. A different
      // expected id has to stay so the dashboard can load it after history
      // arrives. With no expected id, the other-tab note is unchanged.
      if (resumeAnalysisMatches(analysis) || !readPacketResumeExpected()) {
        showOtherTab();
      }
      return;
    }
    if (readSessionItem(PACKET_RESUME_CHECKOUT_KEY) !== "1") {
      return;
    }
    if (!resumeAnalysisMatches(analysis)) {
      return;
    }

    let cancelled = false;
    void (async () => {
      const session = await getSession();
      if (cancelled || packetResumeCheckoutLocked) {
        return;
      }
      if (!session?.access_token) {
        return;
      }
      if (!resumeAnalysisMatches(analysis)) {
        return;
      }
      if (packetCheckoutStartedRecently()) {
        showOtherTab();
        return;
      }
      const claim = rememberPacketCheckoutStarted();
      if (readPacketCheckoutStarted() !== claim) {
        showOtherTab();
        return;
      }
      packetResumeCheckoutLocked = true;
      // Leave the expected analysis id. A guest-snapshot 409 re-runs the
      // sample under a new id and still has to match this tax year.
      try {
        sessionStorage.removeItem(PACKET_RESUME_CHECKOUT_KEY);
        sessionStorage.removeItem(PACKET_RESUME_SAMPLE_KEY);
      } catch {
        // ignore
      }
      clearPacketResumeLocal();
      stripResumeCheckoutParam();
      await handlePayRef.current({ guestRedirect: false, claim });
    })();

    return () => {
      cancelled = true;
    };
  }, [
    guest,
    paid,
    entitlementGate,
    analysis.packet_unlocked,
    missingAnalysisId,
    existingPacketDownload,
    extraChargeRefund,
    analysisStorageId,
    analysis,
  ]);

  const handleDownload = async () => {
    setBusy("download");
    const preservedMissingSource = isPacketMissingSourceDetail(error ?? "");
    if (!preservedMissingSource) {
      setError(null);
    }
    try {
      const headers = await authHeaders({ json: false });
      const params = new URLSearchParams({
        analysis_id: canonicalAnalysisId,
      });
      if (sessionId && !existingPacketDownload) {
        params.set("session_id", sessionId);
      }
      const response = await fetch(
        `${API_URL}/api/year-close-packet/download?${params.toString()}`,
        {
          method: "GET",
          headers,
        },
      );
      if (response.status === 409) {
        const errData = await response.json().catch(() => null);
        const detail = typeof errData?.detail === "string" ? errData.detail : "";
        if (isPacketFinalizingDetail(detail)) {
          persistResumeFlag(false);
          if (sessionId) {
            persistConfirmFlag(true);
          }
        }
        throw new Error(detail || "Download failed.");
      }
      if (response.status === 403) {
        throw new Error("Pay $49 to download the year-close packet.");
      }
      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        throw new Error(errData?.detail || "Download failed.");
      }
      setError(null);
      const blob = await response.blob();
      triggerPdfDownload(blob);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Download failed.",
      );
    } finally {
      setBusy(null);
    }
  };

  const holdUnconfirmedPay = paidYearUnconfirmed && !paid;

  return (
    <Box
      data-testid="year-close-packet-panel"
      id="year-close-packet"
      className="hairline"
      sx={{
        borderRadius: 2,
        px: 2,
        py: 1.75,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={1.25}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            {YEAR_CLOSE_PACKET_TITLE}
          </Typography>
          <Typography variant="body2">
            {YEAR_CLOSE_PACKET_COPY}{" "}
            {paid
              ? "Paid for this tax year. Later CSV updates stay included — no second $49."
              : "One-time payment, not a subscription."}
          </Typography>
        </Box>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          useFlexGap
        >
          {!paid && (
            <Button
              variant="contained"
              startIcon={
                busy === "pay" ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <PayIcon />
                )
              }
              onClick={() => {
                void handlePay();
              }}
              disabled={
                busy !== null ||
                missingAnalysisId ||
                existingPacketDownload ||
                extraChargeRefund ||
                entitlementGate === "unknown" ||
                holdUnconfirmedPay ||
                payHeldForDifferentResume(analysis)
              }
              sx={{ minHeight: 44, width: { xs: "100%", sm: "auto" } }}
            >
              Pay $49
            </Button>
          )}
          {!extraChargeRefund && !holdUnconfirmedPay && (
            <Button
              data-testid="packet-download"
              variant={paid || existingPacketDownload ? "contained" : "outlined"}
              color={paid || existingPacketDownload ? "success" : "primary"}
              sx={{ minHeight: 44, width: { xs: "100%", sm: "auto" } }}
              startIcon={
                busy === "download" ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <DownloadIcon />
                )
              }
              onClick={handleDownload}
              disabled={busy !== null || missingAnalysisId}
            >
              Download
            </Button>
          )}
        </Stack>
        {missingAnalysisId && (
          <Typography role="status" variant="caption" color="text.secondary">
            Re-run this analysis to access its packet.
          </Typography>
        )}
        {canceledNotice && !paid && !error && (
          <Alert
            severity="info"
            role="status"
            data-testid="packet-checkout-canceled"
          >
            {PACKET_CHECKOUT_CANCELED_COPY}
          </Alert>
        )}
        {entitlementGate === "unconfirmed" && !paid && !error && (
          <Alert
            severity="info"
            role="status"
            data-testid="packet-entitlement-unconfirmed"
          >
            {paidYearUnconfirmed
              ? PACKET_PROMOTION_UNCONFIRMED_COPY
              : PACKET_ENTITLEMENT_UNCONFIRMED_COPY}
          </Alert>
        )}
        {otherTabNotice && !error && (
          <Alert
            severity="info"
            role="status"
            data-testid="packet-checkout-other-tab"
          >
            {PACKET_CHECKOUT_OTHER_TAB_COPY}
          </Alert>
        )}
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        {paid && !error && (
          <Typography variant="caption" color="success.main">
            Payment confirmed for tax year{" "}
            {analysis.tax_profile?.tax_year ?? "this year"}. The PDF includes
            lot-level 1099 reconciliation, wash chains, and harvest notes.
            Later updates this year stay included — no second $49.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
