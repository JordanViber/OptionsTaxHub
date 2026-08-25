"use client";

import { Box, Container, Link as MuiLink, Stack } from "@mui/material";
import { Dashboard as DashboardIcon } from "@mui/icons-material";
import NextLink from "next/link";
import TaxDisclaimer from "./TaxDisclaimer";

/**
 * Shared chrome for sign-in, sign-up, and password reset: home link + tax disclaimer.
 */
export default function AuthPageShell({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <Container maxWidth="sm" sx={{ py: 6 }}>
      <Stack spacing={3}>
        <Box sx={{ textAlign: "center" }}>
          <MuiLink
            component={NextLink}
            href="/"
            underline="none"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 1,
              color: "text.primary",
              fontWeight: 700,
              fontSize: "1.15rem",
            }}
          >
            <DashboardIcon color="primary" />
            OptionsTaxHub
          </MuiLink>
        </Box>
        {children}
        <TaxDisclaimer />
      </Stack>
    </Container>
  );
}
