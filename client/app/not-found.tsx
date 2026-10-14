"use client";

import { Box, Container, Typography } from "@mui/material";
import AppShell from "./components/AppShell";

export default function NotFound() {
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
        </Container>
      </Box>
    </AppShell>
  );
}
