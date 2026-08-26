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

/**
 * First-run guidance shown on the dashboard before any analysis exists.
 */
export default function FirstRunEmptyState() {
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
                Optionally attach last year&apos;s Robinhood 1099 PDF next to the
                CSV for reconciliation context — not a rebuild of lots
              </Typography>
            </Box>
          </Box>

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1}
            flexWrap="wrap"
            useFlexGap
          >
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
              href="/settings"
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
            <MuiLink component={NextLink} href="/settings">
              the Settings page
            </MuiLink>{" "}
            drive the federal tax-savings estimate. State tax is not calculated.
          </Alert>
        </Stack>
      </CardContent>
    </Card>
  );
}
