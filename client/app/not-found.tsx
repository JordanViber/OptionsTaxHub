"use client";

import { Box, Button, Container, Stack, Typography } from "@mui/material";
import NextLink from "next/link";
import Wordmark from "./components/Wordmark";
import { OPTIONS_PATH, TAX_PATH } from "@/lib/safeNextPath";

export default function NotFound() {
  return (
    <Box
      data-testid="not-found"
      sx={{
        minHeight: "100vh",
        bgcolor: "background.default",
        color: "text.primary",
      }}
    >
      <Container maxWidth="sm" sx={{ py: { xs: 6, sm: 10 } }}>
        <Wordmark href="/" />
        <Typography
          variant="h1"
          sx={{ mt: 4, fontSize: { xs: "2.2rem", sm: "2.8rem" }, lineHeight: 1.1 }}
        >
          Page not found
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 1.5, maxWidth: 420 }}>
          That address is not on the tax page or the options tools.
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 3 }}>
          <Button component={NextLink} href={TAX_PATH} variant="contained">
            Tax
          </Button>
          <Button component={NextLink} href={OPTIONS_PATH} variant="outlined">
            Options
          </Button>
        </Stack>
      </Container>
    </Box>
  );
}
