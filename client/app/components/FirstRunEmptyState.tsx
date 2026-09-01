"use client";

import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Card,
  CardContent,
  Link as MuiLink,
  Stack,
  Typography,
} from "@mui/material";
import {
  Download as DownloadIcon,
  Settings as SettingsIcon,
} from "@mui/icons-material";
import NextLink from "next/link";
import { SUPPLEMENTAL_1099_FIRST_RUN_HINT } from "@/lib/supplemental1099";

/**
 * First-run guidance shown on the dashboard before any analysis exists.
 */
export default function FirstRunEmptyState({
  onLoadSample,
  settingsHref = "/settings",
}: Readonly<{
  onLoadSample?: () => void;
  settingsHref?: string;
}>) {
  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
              Get started with your first analysis
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Upload a Robinhood transactions CSV to see open positions, tax
              lots, wash-sale flags, and federal tax-loss harvesting estimates.
              Or open the 2026 sample — no account required.
            </Typography>
          </Box>

          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
              Export from Robinhood
            </Typography>
            <Box component="ol" sx={{ pl: 2.5, my: 0 }}>
              <Typography component="li" variant="body2" sx={{ mb: 0.5 }}>
                In Robinhood, open <strong>Account → Reports/Statements</strong>
              </Typography>
              <Typography component="li" variant="body2" sx={{ mb: 0.5 }}>
                Export your <strong>transactions CSV</strong>, then upload it
                above
              </Typography>
              <Typography component="li" variant="body2">
                {SUPPLEMENTAL_1099_FIRST_RUN_HINT}
              </Typography>
            </Box>
          </Box>

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1}
            flexWrap="wrap"
            useFlexGap
          >
            {onLoadSample && (
              <Button
                variant="contained"
                onClick={onLoadSample}
                sx={{ textTransform: "none" }}
              >
                Open the 2026 sample
              </Button>
            )}
            <Button
              component="a"
              href="/sample-robinhood-transactions.csv"
              download
              variant="outlined"
              startIcon={<DownloadIcon />}
              sx={{ textTransform: "none" }}
            >
              Download sample CSV
            </Button>
            <Button
              component={NextLink}
              href={settingsHref}
              variant="text"
              startIcon={<SettingsIcon />}
              sx={{ textTransform: "none" }}
            >
              Review tax profile
            </Button>
          </Stack>

          <Alert severity="info" variant="outlined">
            <AlertTitle>Savings estimates use your tax profile</AlertTitle>
            Filing status, estimated income, and tax year in{" "}
            <MuiLink component={NextLink} href={settingsHref}>
              the Settings page
            </MuiLink>{" "}
            drive the federal tax-savings estimate. State tax is not calculated.
            Sign in to keep that profile on the next device.
          </Alert>
        </Stack>
      </CardContent>
    </Card>
  );
}
