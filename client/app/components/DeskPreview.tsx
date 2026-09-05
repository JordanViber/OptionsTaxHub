"use client";

import { Box, Stack, Typography } from "@mui/material";

/**
 * Static landing preview of the 2026 sample same-year compare.
 *
 * Broker ST, export ST, and wash must match analyze of
 * /sample-robinhood-transactions.csv + /sample-robinhood-1099-2026.pdf
 * at guest defaults (single, $75k, TY 2026). These totals come from the
 * 1099 PDF and realized export, not live quotes.
 */
export const SAMPLE_BROKER_ST = "$2,699";
export const SAMPLE_EXPORT_ST = "$0";
export const SAMPLE_WASH = "$924";

const monoSx = {
  fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
  fontSize: 10,
  letterSpacing: "0.18em",
  textTransform: "uppercase" as const,
  color: "text.secondary",
};

function CompareColumn({
  title,
  subtitle,
  shortTerm,
  wash,
  testId,
}: Readonly<{
  title: string;
  subtitle: string;
  shortTerm: string;
  wash: string;
  testId: string;
}>) {
  return (
    <Box data-testid={testId} sx={{ flex: 1, minWidth: 0 }}>
      <Typography sx={monoSx}>{title}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
        {subtitle}
      </Typography>
      <Typography
        sx={{
          mt: 1.5,
          fontFamily: "var(--font-display), Fraunces, Georgia, serif",
          fontSize: { xs: "2rem", sm: "2.5rem" },
          letterSpacing: "-0.03em",
          lineHeight: 1,
        }}
      >
        {shortTerm}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
        Short-term
      </Typography>
      <Typography
        sx={{
          mt: 1.25,
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        Wash {wash}
      </Typography>
    </Box>
  );
}

export default function DeskPreview() {
  return (
    <Box
      className="hairline"
      sx={{
        overflow: "hidden",
        borderRadius: 3,
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ px: 2.5, pt: 2.5, pb: 2 }}>
        <Typography sx={monoSx}>Tax year 2026 · 1099 vs your export</Typography>
      </Box>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={3}
        divider={
          <Box
            sx={{
              display: { xs: "none", sm: "block" },
              borderLeft: "1px solid",
              borderColor: "divider",
            }}
          />
        }
        sx={{ px: 2.5, pb: 2.5 }}
      >
        <CompareColumn
          title="Broker 1099"
          subtitle="Settlement date"
          shortTerm={SAMPLE_BROKER_ST}
          wash={SAMPLE_WASH}
          testId="landing-1099-broker"
        />
        <CompareColumn
          title="This export"
          subtitle="Trade date"
          shortTerm={SAMPLE_EXPORT_ST}
          wash={SAMPLE_WASH}
          testId="landing-1099-export"
        />
      </Stack>
      <Box
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          px: 2.5,
          py: 1.75,
        }}
      >
        <Typography variant="body2" color="text.secondary">
          A year-end short (SPX 12/31) can print a gain on the 1099 while the
          export still shows a loss. Not a software bug.
        </Typography>
      </Box>
    </Box>
  );
}
