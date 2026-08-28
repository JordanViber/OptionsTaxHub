"use client";

import {
  AppBar,
  Box,
  Container,
  Link as MuiLink,
  Stack,
  Toolbar,
  Typography,
} from "@mui/material";
import Wordmark from "../components/Wordmark";
import NextLink from "next/link";
import TaxDisclaimer from "../components/TaxDisclaimer";

export default function PrivacyPage() {
  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppBar position="static" elevation={0}>
        <Toolbar>
          <Wordmark />
        </Toolbar>
      </AppBar>

      <Container maxWidth="md" sx={{ py: { xs: 4, md: 6 } }}>
        <Stack spacing={3}>
          <Typography variant="h4" sx={{ fontWeight: 800 }}>
            Privacy
          </Typography>
          <Typography variant="body1" color="text.secondary">
            This page describes how OptionsTaxHub handles the information you
            provide. It is not legal advice.
          </Typography>

          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
              What we process
            </Typography>
            <Typography variant="body1" paragraph>
              When you upload a brokerage CSV (and optionally a prior-year 1099
              PDF), we process that file to build portfolio analysis: open
              positions, tax lots, wash-sale flags, and federal tax-loss
              harvesting estimates.
            </Typography>
          </Box>

          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
              What we save
            </Typography>
            <Typography variant="body1" paragraph>
              Analysis results are saved to your account history so you can
              reopen them later. Signed-in runs also keep the parsed trade book
              so a later CSV can add new activity without a full re-export. The
              current result may also be kept in your browser (session storage)
              until you sign out or clear it. This is not an in-memory-only
              tool, and uploads are not discarded immediately after analysis.
            </Typography>
          </Box>

          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
              What you can delete
            </Typography>
            <Typography variant="body1" paragraph>
              You can delete individual saved analyses from dashboard history.
              Signing out clears the browser-stored result on this device.
            </Typography>
          </Box>

          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
              Account data
            </Typography>
            <Typography variant="body1" paragraph>
              Creating an account uses email and password (via our
              authentication provider). Your tax profile settings (filing
              status, estimated income, tax year) are stored with your account
              so savings estimates can be calculated. State of residence may be
              saved on the profile, but state tax is not currently used in
              estimates.
            </Typography>
          </Box>

          <TaxDisclaimer />

          <Typography variant="body2">
            <MuiLink component={NextLink} href="/">
              Back to home
            </MuiLink>
            {" · "}
            <MuiLink component={NextLink} href="/auth/signin">
              Sign in
            </MuiLink>
          </Typography>
        </Stack>
      </Container>
    </Box>
  );
}
