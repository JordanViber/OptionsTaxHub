"use client";

import { useEffect, useState } from "react";
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
import type { PortfolioAnalysis } from "@/lib/types";

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
  "This is a reconciliation packet, not a filed Form 8949 and not a rebuild of lots.";
export const PACKET_CHECKOUT_INFLIGHT_KEY =
  "optionstaxhub-packet-checkout-inflight";
export const PACKET_CHECKOUT_CANCELED_COPY =
  "Checkout was closed without payment. Pay $49 when you are ready.";

function paidStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-paid:${analysisId}`;
}

export function isYearClosePacketPaid(analysisId: string): boolean {
  const stored = readSessionItem(paidStorageKey(analysisId));
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

async function authHeaders(): Promise<HeadersInit> {
  const session = await getSession();
  if (!session?.access_token) {
    throw new Error("Authentication required. Please sign in.");
  }
  return {
    Authorization: `Bearer ${session.access_token}`,
    "Content-Type": "application/json",
  };
}

function compactAnalysis(analysis: PortfolioAnalysis) {
  return {
    analysis_id: analysis.analysis_id,
    tax_profile: analysis.tax_profile,
    supplemental_1099: analysis.supplemental_1099 ?? null,
    wash_sale_flags: analysis.wash_sale_flags,
    tax_lots: (analysis.tax_lots || []).map((lot) => ({
      symbol: lot.symbol,
      quantity: lot.quantity,
      purchase_date: lot.purchase_date,
      wash_sale_disallowed: lot.wash_sale_disallowed,
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

export default function YearClosePacketPanel({
  analysis,
  onPaidChange,
}: Readonly<{
  analysis: PortfolioAnalysis;
  onPaidChange?: (paid: boolean) => void;
}>) {
  const analysisId = analysis.analysis_id || "local-analysis";
  const [busy, setBusy] = useState<"pay" | "download" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [canceledNotice, setCanceledNotice] = useState(false);
  const [paid, setPaid] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

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

  useEffect(() => {
    if (analysis.packet_unlocked) {
      setPaid(true);
      if (analysis.packet_session_id?.startsWith("cs_")) {
        setSessionId(analysis.packet_session_id);
        writeSessionItem(paidStorageKey(analysisId), analysis.packet_session_id);
      }
      onPaidChange?.(true);
    }
    const stored = readSessionItem(paidStorageKey(analysisId));
    if (stored && stored.startsWith("cs_")) {
      setPaid(true);
      setSessionId(stored);
      onPaidChange?.(true);
    }

    const params = new URLSearchParams(window.location.search);
    const sid = params.get("packet_session");
    const canceled = params.get("packet_canceled") === "1";
    const inflight = readSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY) === analysisId;

    if (sid) {
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
      setCanceledNotice(false);
      setSessionId(sid);
      setBusy("confirm");
      setError(null);
      void (async () => {
        try {
          const headers = await authHeaders();
          const response = await fetch(`${API_URL}/api/year-close-packet/confirm`, {
            method: "POST",
            headers,
            body: JSON.stringify({
              analysis_id: analysisId,
              session_id: sid,
              analysis: compactAnalysis(analysis),
            }),
          });
          if (!response.ok) {
            const errData = await response.json().catch(() => null);
            throw new Error(
              errData?.detail || "Could not confirm packet payment.",
            );
          }
          setPaid(true);
          writeSessionItem(paidStorageKey(analysisId), sid);
          onPaidChange?.(true);
          stripPacketQueryParams();
        } catch (err) {
          setError(err instanceof Error ? err.message : "Could not confirm payment.");
        } finally {
          setBusy(null);
        }
      })();
      return;
    }

    if (canceled || inflight) {
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
      setBusy(null);
      setCanceledNotice(true);
      stripPacketQueryParams();
    }
  }, [analysis, analysisId]);

  const handlePay = async () => {
    setBusy("pay");
    setError(null);
    setCanceledNotice(false);
    writeSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY, analysisId);
    try {
      const headers = await authHeaders();
      const response = await fetch(`${API_URL}/api/year-close-packet/checkout`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          analysis_id: analysisId,
          analysis: compactAnalysis(analysis),
        }),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        throw new Error(errData?.detail || "Failed to start checkout.");
      }
      const data = await response.json();
      if (typeof data?.checkout_url !== "string" || data.checkout_url.length === 0) {
        throw new Error("Checkout did not return a payment link.");
      }
      try {
        globalThis.location.href = data.checkout_url;
      } catch {
        // jsdom rejects in-page navigation. A real browser unloads this
        // page instead; busy stays "pay" until pageshow / remount.
      }
    } catch (err) {
      clearSessionItem(PACKET_CHECKOUT_INFLIGHT_KEY);
      setError(err instanceof Error ? err.message : "Failed to start checkout.");
      setBusy(null);
    }
  };

  const handleDownload = async () => {
    setBusy("download");
    setError(null);
    try {
      const headers = await authHeaders();
      const response = await fetch(`${API_URL}/api/year-close-packet/download`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          analysis_id: analysisId,
          session_id: sessionId,
          analysis: compactAnalysis(analysis),
        }),
      });
      if (response.status === 403) {
        throw new Error("Pay $49 to download the year-close packet.");
      }
      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        throw new Error(errData?.detail || "Download failed.");
      }
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
          <Typography variant="body2" color="text.secondary">
            {YEAR_CLOSE_PACKET_COPY}{" "}
            {paid
              ? "Unlocked for this tax year — later CSV updates stay included, no second $49."
              : "One-time payment — not a subscription and not a tip. Unlocks lot-level harvest instructions, wash-sale events, tax-lot detail, and the downloadable PDF."}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button
            variant="contained"
            startIcon={
              busy === "pay" ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <PayIcon />
              )
            }
            onClick={handlePay}
            disabled={busy !== null || paid}
          >
            Pay $49
          </Button>
          <Button
            variant={paid ? "contained" : "outlined"}
            color={paid ? "success" : "primary"}
            startIcon={
              busy === "download" ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <DownloadIcon />
              )
            }
            onClick={handleDownload}
            disabled={busy !== null}
          >
            Download
          </Button>
        </Stack>
        {canceledNotice && !error && (
          <Alert
            severity="info"
            role="status"
            data-testid="packet-checkout-canceled"
          >
            {PACKET_CHECKOUT_CANCELED_COPY}
          </Alert>
        )}
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        {paid && !error && (
          <Typography variant="caption" color="success.main">
            Payment confirmed. Unlocked for tax year{" "}
            {analysis.tax_profile?.tax_year ?? "this year"}. Later updates this
            year stay included — no second $49.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
