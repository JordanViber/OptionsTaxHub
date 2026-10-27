"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Box, CircularProgress, Container, Typography } from "@mui/material";
import { useAuth } from "@/app/context/auth";
import { storedAnalysisVisibleTo } from "@/lib/accountLocalData";
import { isEmailConfirmed } from "@/lib/supabase";
import { TAX_PATH, nextParam, safeNextPath } from "@/lib/safeNextPath";
import AppShell from "../components/AppShell";
import EntryAnalysisPanel from "../components/EntryAnalysisPanel";
import type { PortfolioAnalysis, Position } from "@/lib/types";

export const dynamic = "force-dynamic";

function readStoredPositions(viewerId: string | null): Position[] {
  try {
    if (!storedAnalysisVisibleTo(viewerId)) {
      return [];
    }
    const saved = sessionStorage.getItem("optionstaxhub-analysis");
    if (!saved) {
      return [];
    }
    const parsed = JSON.parse(saved) as PortfolioAnalysis;
    return Array.isArray(parsed.positions) ? parsed.positions : [];
  } catch {
    return [];
  }
}

export default function OptionsPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const emailConfirmed = isEmailConfirmed(user);
  const [positions, setPositions] = useState<Position[]>([]);

  useEffect(() => {
    if (authLoading) {
      return;
    }
    setPositions(readStoredPositions(user?.id ?? null));
  }, [authLoading, user]);

  useEffect(() => {
    if (authLoading) {
      return;
    }
    if (user && !emailConfirmed) {
      const rawNext = new URLSearchParams(window.location.search).get("next");
      if (rawNext) {
        const next = safeNextPath(rawNext);
        if (next !== TAX_PATH) {
          router.push(`/auth/confirm-email?next=${nextParam(next)}`);
          return;
        }
      }
      router.push("/auth/confirm-email");
    }
  }, [authLoading, user, emailConfirmed, router]);

  if (authLoading || (user && !emailConfirmed)) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <AppShell>
      <Container maxWidth="lg" sx={{ py: 3 }}>
        <Typography
          sx={{
            fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
            fontSize: 11,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "text.secondary",
          }}
        >
          Options
        </Typography>
        <Typography variant="h5" sx={{ mt: 0.5, mb: 2, fontWeight: 700 }}>
          LEAP ranking and what-if
        </Typography>
        <EntryAnalysisPanel positions={positions} />
      </Container>
    </AppShell>
  );
}
