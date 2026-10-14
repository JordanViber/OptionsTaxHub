"use client";

import { Box, Container, Link as MuiLink, Typography } from "@mui/material";
import NextLink from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/app/context/auth";
import { signInHref } from "@/lib/safeNextPath";
import AppShell from "./components/AppShell";

export default function NotFound() {
  const pathname = usePathname() || "/";
  const { user } = useAuth();
  return (
    <AppShell>
      <Box
        data-testid="not-found"
        sx={{
          minHeight: "100vh",
          bgcolor: "background.default",
          color: "text.primary",
        }}
      >
        <Container maxWidth="sm" sx={{ py: { xs: 6, sm: 10 } }}>
          <Typography
            variant="h1"
            sx={{ fontSize: { xs: "2.2rem", sm: "2.8rem" }, lineHeight: 1.1 }}
          >
            Page not found
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1.5, maxWidth: 420 }}>
            That address is not on the tax page or the options tools. Tax and
            Options are in the header.
          </Typography>
          {user ? null : (
            <MuiLink
              component={NextLink}
              href={signInHref(pathname)}
              data-testid="not-found-sign-in"
              sx={{ display: "inline-block", mt: 2 }}
            >
              Sign in
            </MuiLink>
          )}
        </Container>
      </Box>
    </AppShell>
  );
}
