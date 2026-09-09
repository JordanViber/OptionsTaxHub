"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AppBar,
  Box,
  Button,
  CircularProgress,
  Container,
  IconButton,
  Toolbar,
  Typography,
} from "@mui/material";
import { Settings as SettingsIcon } from "@mui/icons-material";
import NextLink from "next/link";
import Wordmark from "../components/Wordmark";
import DeskSwitcher, { rememberDesk } from "../components/DeskSwitcher";
import EntryAnalysisPanel from "../components/EntryAnalysisPanel";
import TaxDisclaimer from "../components/TaxDisclaimer";
import { useAuth } from "@/app/context/auth";
import { isEmailConfirmed } from "@/lib/supabase";
import type { Position } from "@/lib/types";

export const dynamic = "force-dynamic";

function positionsFromStorage(): Position[] {
  try {
    const raw = sessionStorage.getItem("optionstaxhub-analysis");
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as { positions?: Position[] };
    return Array.isArray(parsed.positions) ? parsed.positions : [];
  } catch {
    return [];
  }
}

export default function OptionsDeskPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [positions, setPositions] = useState<Position[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setPositions(positionsFromStorage());
  }, []);

  useEffect(() => {
    if (authLoading) {
      return;
    }
    if (user && !isEmailConfirmed(user)) {
      router.push("/auth/confirm-email");
      return;
    }
    rememberDesk("options");
  }, [authLoading, user, router]);

  if (authLoading || !mounted) {
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

  if (user && !isEmailConfirmed(user)) {
    return null;
  }

  const hasBook = positions.length > 0;

  return (
    <>
      <AppBar position="static" sx={{ zIndex: 40 }}>
        <Toolbar sx={{ px: { xs: 1, sm: 2 }, gap: { xs: 0.25, sm: 0.5 } }}>
          <Wordmark href="/" />
          <DeskSwitcher />
          <Box sx={{ flexGrow: 1 }} />
          <IconButton
            color="inherit"
            component={NextLink}
            href="/settings"
            aria-label="Settings"
            sx={{ display: { xs: "inline-flex", sm: "none" } }}
          >
            <SettingsIcon />
          </IconButton>
          <Button
            color="inherit"
            component={NextLink}
            href="/settings"
            sx={{
              textTransform: "none",
              display: { xs: "none", sm: "inline-flex" },
            }}
          >
            Settings
          </Button>
          {user ? null : (
            <Button
              component={NextLink}
              href="/auth/signin"
              variant="contained"
              size="small"
              sx={{ ml: 0.5 }}
            >
              Sign In
            </Button>
          )}
        </Toolbar>
      </AppBar>

      <Container maxWidth="md" sx={{ py: { xs: 3, sm: 5 } }}>
        <Typography variant="h4" sx={{ fontWeight: 650 }}>
          Options desk
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 1, mb: 2.5, maxWidth: 560 }}
        >
          Rank long LEAPs against owning the stock, then price a single
          contract. Tax close, harvest, and the year-close packet stay on the
          tax desk.
        </Typography>

        {hasBook ? (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mb: 2 }}
            data-testid="options-book-status"
          >
            Using the book already loaded on the tax desk.
          </Typography>
        ) : (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mb: 2 }}
            data-testid="options-book-status"
          >
            No book loaded.{" "}
            <Box
              component={NextLink}
              href="/dashboard"
              sx={{ color: "text.primary", fontWeight: 600 }}
            >
              Load a CSV on the tax desk
            </Box>{" "}
            to compare a contract against your lots. Manual premium still works
            here.
          </Typography>
        )}

        <EntryAnalysisPanel positions={positions} />

        <Box sx={{ mt: 3 }}>
          <TaxDisclaimer />
        </Box>
      </Container>
    </>
  );
}
