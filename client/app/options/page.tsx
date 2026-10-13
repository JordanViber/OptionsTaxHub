"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AppBar,
  Box,
  Button,
  CircularProgress,
  Container,
  Toolbar,
  Typography,
} from "@mui/material";
import NextLink from "next/link";
import { useAuth } from "@/app/context/auth";
import { isEmailConfirmed } from "@/lib/supabase";
import DeskNav from "../components/DeskNav";
import EntryAnalysisPanel from "../components/EntryAnalysisPanel";
import Wordmark from "../components/Wordmark";
import type { PortfolioAnalysis, Position } from "@/lib/types";
import { OPTIONS_SIGN_IN_HREF } from "@/lib/safeNextPath";

export const dynamic = "force-dynamic";

function readStoredPositions(): Position[] {
  try {
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
    setPositions(readStoredPositions());
  }, []);

  useEffect(() => {
    if (authLoading) {
      return;
    }
    if (user && !emailConfirmed) {
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
    <>
      <AppBar position="static" sx={{ zIndex: 40 }}>
        <Toolbar sx={{ px: { xs: 1, sm: 2 }, gap: 0.5 }}>
          <Wordmark href="/" />
          <Box sx={{ flexGrow: 1 }} />
          <Button
            color="inherit"
            onClick={() => {
              if (!user) {
                router.push("/auth/signin?reason=profile");
                return;
              }
              router.push("/settings");
            }}
            sx={{ textTransform: "none" }}
          >
            Settings
          </Button>
          {!user && (
            <Button
              component={NextLink}
              href={OPTIONS_SIGN_IN_HREF}
              variant="contained"
              size="small"
            >
              Sign In
            </Button>
          )}
        </Toolbar>
        <DeskNav />
      </AppBar>
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
    </>
  );
}
