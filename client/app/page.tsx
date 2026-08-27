"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Box,
  Button,
  Container,
  Stack,
  Typography,
} from "@mui/material";
import Link from "next/link";
import { useAuth } from "@/app/context/auth";
import TaxDisclaimer from "./components/TaxDisclaimer";
import Wordmark from "./components/Wordmark";
import DeskPreview from "./components/DeskPreview";

const UPLOAD_INTENT_KEY = "oth-upload-intent";

export default function LandingPage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!loading && user) {
      router.push("/dashboard");
    }
  }, [loading, user, router]);

  if (!mounted) return null;
  if (loading) return null;
  if (user) return null;

  const openSample = () => {
    try {
      sessionStorage.setItem("oth-load-sample", "1");
    } catch {
      // ignore
    }
    router.push("/dashboard");
  };

  const openCsv = () => {
    try {
      sessionStorage.setItem(UPLOAD_INTENT_KEY, "1");
    } catch {
      // ignore
    }
    router.push("/dashboard");
  };

  return (
    <Box
      sx={{
        minHeight: "100vh",
        bgcolor: "background.default",
        color: "text.primary",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <Box
        className="desk-grid"
        sx={{
          pointerEvents: "none",
          position: "absolute",
          inset: 0,
          opacity: 0.6,
        }}
      />
      <Box
        className="grain"
        sx={{
          pointerEvents: "none",
          position: "absolute",
          inset: 0,
          opacity: 0.7,
        }}
      />

      <Box
        component="header"
        sx={{
          position: "relative",
          zIndex: 30,
          mx: "auto",
          maxWidth: 1152,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 2,
          px: 2.5,
          py: 2.5,
        }}
      >
        <Wordmark />
        <Stack direction="row" spacing={1} alignItems="center">
          <Button
            component={Link}
            href="/dashboard"
            variant="outlined"
            size="small"
          >
            Open desk
          </Button>
          <Button component={Link} href="/auth/signin" size="small">
            Sign In
          </Button>
        </Stack>
      </Box>

      <Container
        maxWidth="lg"
        sx={{ position: "relative", zIndex: 10, pt: { xs: 4, md: 8 }, pb: 10 }}
      >
        <Box
          sx={{
            display: "grid",
            gap: { xs: 6, lg: 8 },
            gridTemplateColumns: { lg: "1.05fr 0.95fr" },
            alignItems: "center",
          }}
        >
          <Box>
            <Typography
              sx={{
                fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
                fontSize: 11,
                letterSpacing: "0.22em",
                textTransform: "uppercase",
                color: "text.secondary",
              }}
            >
              Tax year 2026 · FIFO · Wash-sale window 30d
            </Typography>
            <Typography
              variant="h1"
              sx={{
                mt: 2,
                fontSize: { xs: "2.6rem", sm: "3.6rem", md: "4.25rem" },
                lineHeight: 1.05,
              }}
            >
              Keep more of what you trade.
            </Typography>
            <Typography
              sx={{
                mt: 2.5,
                maxWidth: 560,
                fontSize: { xs: "1.05rem", sm: "1.15rem" },
                color: "text.secondary",
                lineHeight: 1.65,
              }}
            >
              OptionsTaxHub is a year-end tax desk: upload a brokerage CSV, see
              harvestable lots, catch wash-sale traps, and know the federal
              dollars still on the table — before December 31. State tax is not
              included.
            </Typography>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1.5}
              sx={{ mt: 4 }}
            >
              <Button
                variant="contained"
                size="large"
                onClick={openSample}
                sx={{ py: 1.5, px: 3, fontWeight: 700 }}
              >
                Open the 2026 sample
              </Button>
              <Button
                variant="outlined"
                size="large"
                onClick={openCsv}
                sx={{ py: 1.5, px: 3, fontWeight: 700 }}
              >
                I have a CSV
              </Button>
            </Stack>
            <Typography
              variant="body2"
              sx={{ mt: 2, color: "text.secondary", opacity: 0.85 }}
            >
              Sample loads instantly. Sign in only if you want tax year and
              saved runs waiting on the next device.
            </Typography>
          </Box>
          <DeskPreview />
        </Box>
      </Container>

      <Box
        sx={{
          position: "relative",
          zIndex: 10,
          borderTop: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "rgba(18,20,26,0.4)",
        }}
      >
        <Container maxWidth="lg" sx={{ py: 6 }}>
          <Box
            sx={{
              display: "grid",
              gap: 4,
              gridTemplateColumns: { sm: "repeat(3, 1fr)" },
            }}
          >
            <Feature
              title="Harvest queue"
              body="Losing lots ranked by federal savings at your bracket. Short-term first. Wash-sale risk called out before you click."
            />
            <Feature
              title="Wash-sale radar"
              body="Same-symbol buys inside 30 days get grouped by ticker with disallowed loss and the basis bump on the replacement lot."
            />
            <Feature
              title="Lot ledger"
              body="FIFO tax lots under every position. Holding period in English. ST / LT. Expand a row — every lot, not a spreadsheet dump."
            />
          </Box>
        </Container>
      </Box>

      <Container
        maxWidth="lg"
        sx={{ position: "relative", zIndex: 10, py: 8 }}
      >
        <Typography
          sx={{
            fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
            fontSize: 11,
            letterSpacing: "0.22em",
            textTransform: "uppercase",
            color: "text.secondary",
          }}
        >
          How the desk works
        </Typography>
        <Typography
          variant="h2"
          sx={{ mt: 1.5, maxWidth: 640, fontSize: { xs: "1.75rem", md: "2.25rem" } }}
        >
          Three moves. Then you know what to sell.
        </Typography>
        <Box
          component="ol"
          sx={{
            mt: 5,
            p: 0,
            m: 0,
            listStyle: "none",
            display: "grid",
            gap: 3,
            gridTemplateColumns: { md: "repeat(3, 1fr)" },
          }}
        >
          {[
            {
              n: "01",
              t: "Drop a CSV",
              d: "Robinhood transaction export, or our 2026 sample. Parsed into FIFO lots in seconds.",
            },
            {
              n: "02",
              t: "Read the desk",
              d: "One number at the top: federal harvest still available. Then the queue, the wash sales, the ledger.",
            },
            {
              n: "03",
              t: "Take the packet",
              d: "1099 vs CSV wash lots and settlement-date FAQ on the desk. $49 unlocks the CPA PDF for that tax year.",
            },
          ].map((step) => (
            <Box
              component="li"
              key={step.n}
              className="hairline"
              sx={{ borderRadius: 3, bgcolor: "background.paper", px: 2.5, py: 3 }}
            >
              <Typography
                sx={{
                  fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
                  fontSize: 12,
                  color: "text.secondary",
                }}
              >
                {step.n}
              </Typography>
              <Typography variant="h5" sx={{ mt: 1.5 }}>
                {step.t}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {step.d}
              </Typography>
            </Box>
          ))}
        </Box>
      </Container>

      <Box
        sx={{
          position: "relative",
          zIndex: 10,
          borderTop: "1px solid",
          borderColor: "divider",
          bgcolor: "rgba(18,20,26,0.3)",
        }}
      >
        <Container maxWidth="lg" sx={{ py: 8 }}>
          <Typography
            sx={{
              fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
              fontSize: 11,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              color: "text.secondary",
            }}
          >
            Optional account
          </Typography>
          <Stack
            direction={{ xs: "column", lg: "row" }}
            justifyContent="space-between"
            alignItems={{ lg: "flex-end" }}
            spacing={3}
            sx={{ mt: 1.5 }}
          >
            <Typography
              variant="h2"
              sx={{ maxWidth: 640, fontSize: { xs: "1.75rem", md: "2.25rem" } }}
            >
              Sign in for the year that follows you — not a velvet rope.
            </Typography>
            <Button variant="outlined" component={Link} href="/auth/signin">
              See what's kept
            </Button>
          </Stack>
          <Box
            sx={{
              mt: 5,
              display: "grid",
              gap: 4,
              gridTemplateColumns: { sm: "repeat(3, 1fr)" },
            }}
          >
            <Feature
              title="Tax year travels"
              body="Filing status, income, state, and TY 2024–2026 sync to your account. Switch years without retyping the profile."
            />
            <Feature
              title="Saved runs"
              body="Named past analyses reopen without the original file. Harvest, wash sales, and packet unlocks follow the tax year. Analyses are saved to your account history so you can reopen or delete them."
            />
            <Feature
              title="Last year’s book"
              body="2025 next to 2026. Come back in December and pick up the run you already reconciled."
            />
          </Box>
        </Container>
      </Box>

      <Container maxWidth="md" sx={{ position: "relative", zIndex: 10, py: 4 }}>
        <TaxDisclaimer />
      </Container>

      <Box
        component="footer"
        sx={{
          position: "relative",
          zIndex: 10,
          borderTop: "1px solid",
          borderColor: "divider",
          py: 4,
          px: 2.5,
        }}
      >
        <Container
          maxWidth="lg"
          sx={{
            display: "flex",
            flexDirection: { xs: "column", sm: "row" },
            justifyContent: "space-between",
            gap: 3,
          }}
        >
          <Box sx={{ maxWidth: 520 }}>
            <Wordmark />
            <Typography
              variant="caption"
              sx={{ display: "block", mt: 2, color: "text.secondary", lineHeight: 1.6 }}
            >
              This tool is for educational and informational purposes only. It
              does not constitute financial, tax, or legal advice. Consult a
              qualified professional before making tax decisions. Analyses are
              saved to your account history and can be deleted.
            </Typography>
          </Box>
          <Stack direction="row" spacing={3} sx={{ color: "text.secondary" }}>
            <Link href="/privacy" style={{ color: "inherit" }}>
              Privacy
            </Link>
            <Link href="/auth/signin" style={{ color: "inherit" }}>
              Sign in
            </Link>
            <Link href="/dashboard" style={{ color: "inherit" }}>
              Desk
            </Link>
          </Stack>
        </Container>
      </Box>
    </Box>
  );
}

function Feature({
  title,
  body,
}: Readonly<{ title: string; body: string }>) {
  return (
    <Box>
      <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
        {title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.65 }}>
        {body}
      </Typography>
    </Box>
  );
}
