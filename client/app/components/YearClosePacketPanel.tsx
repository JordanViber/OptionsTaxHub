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

function paidStorageKey(analysisId: string): string {
  return `optionstaxhub-packet-paid:${analysisId}`;
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
}: Readonly<{ analysis: PortfolioAnalysis }>) {
  const analysisId = analysis.analysis_id || "local-analysis";
  const [busy, setBusy] = useState<"pay" | "download" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paid, setPaid] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  useEffect(() => {
    const stored = sessionStorage.getItem(paidStorageKey(analysisId));
    if (stored && stored.startsWith("cs_")) {
      setPaid(true);
      setSessionId(stored);
    }

    const params = new URLSearchParams(window.location.search);
    const sid = params.get("packet_session");
    if (!sid) {
      return;
    }
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
        try {
          sessionStorage.setItem(paidStorageKey(analysisId), sid);
        } catch {
          // ignore
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not confirm payment.");
      } finally {
        setBusy(null);
      }
    })();
  }, [analysis, analysisId]);

  const handlePay = async () => {
    setBusy("pay");
    setError(null);
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
      globalThis.location.href = data.checkout_url;
    } catch (err) {
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
            {YEAR_CLOSE_PACKET_COPY} One-time payment — not a subscription and
            not a tip.
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
            disabled={busy !== null}
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
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        {paid && !error && (
          <Typography variant="caption" color="success.main">
            Payment confirmed. Download is unlocked for this analysis.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
