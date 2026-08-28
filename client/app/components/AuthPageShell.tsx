"use client";

import { Box, Container, Stack, Typography } from "@mui/material";
import Wordmark from "./Wordmark";
import TaxDisclaimer from "./TaxDisclaimer";

/**
 * Shared chrome for sign-in, sign-up, confirm-email, and password reset.
 */
export default function AuthPageShell({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <Box
      sx={{
        minHeight: "100vh",
        bgcolor: "background.default",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <Box
        className="desk-grid"
        sx={{ pointerEvents: "none", position: "absolute", inset: 0, opacity: 0.45 }}
      />
      <Container maxWidth="sm" sx={{ py: 6, position: "relative", zIndex: 1 }}>
        <Stack spacing={3}>
          <Box sx={{ textAlign: "center" }}>
            <Wordmark />
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 2, maxWidth: 420, mx: "auto" }}
            >
              Sign in keeps tax year, saved runs, and packet unlocks on the next
              device. You can still analyze a CSV without an account.
            </Typography>
          </Box>
          {children}
          <TaxDisclaimer />
        </Stack>
      </Container>
    </Box>
  );
}
