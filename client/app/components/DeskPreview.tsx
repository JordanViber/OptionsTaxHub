"use client";

import { Box, Chip, Stack, Typography } from "@mui/material";

const rows = [
  { label: "NVDA", meta: "52 sh · $11,856", pnl: "−$9,768", loss: true },
  { label: "META", meta: "4 sh · $2,284", pnl: "−$596", loss: true },
  { label: "SPY", meta: "4 sh · $3,084", pnl: "−$196", loss: true },
  { label: "TSLA", meta: "4 sh · $1,420", pnl: "−$40", loss: true },
  { label: "MSFT", meta: "10 sh · $5,050", pnl: "+$890", loss: false },
  { label: "AAPL", meta: "15 sh · $4,725", pnl: "+$2,009", loss: false },
];

/**
 * Static landing preview of the 2026 sample desk.
 *
 * Hero dollars and wash-sale count must match analyze of
 * /sample-robinhood-transactions.csv at guest defaults (single, $75k, TY 2026)
 * using the snapshot quotes in server/tests/test_sample_preview_honesty.py.
 */
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
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 2,
          px: 2.5,
          pt: 2.5,
          pb: 2,
        }}
      >
        <Box>
          <Typography
            sx={{
              fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
              fontSize: 10,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "text.secondary",
            }}
          >
            Tax year 2026 · sample portfolio
          </Typography>
          <Typography
            sx={{
              mt: 1,
              fontFamily: "var(--font-display), Fraunces, Georgia, serif",
              fontSize: { xs: "2.4rem", sm: "3rem" },
              letterSpacing: "-0.03em",
              lineHeight: 1,
            }}
          >
            $2,086
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            Federal harvest still on the table
          </Typography>
        </Box>
        <Chip
          label="3 wash sales"
          size="small"
          sx={{
            bgcolor: "warning.dark",
            color: "warning.main",
            fontWeight: 700,
          }}
        />
      </Box>
      <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
        {rows.map((row) => (
          <Stack
            key={row.label}
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            gap={1.5}
            sx={{
              px: 2.5,
              py: 1.1,
              borderBottom: "1px solid",
              borderColor: "divider",
              "&:last-of-type": { borderBottom: 0 },
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ fontWeight: 600 }} noWrap>
                {row.label}
              </Typography>
              <Typography
                sx={{
                  fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
                  fontSize: 11,
                  color: "text.secondary",
                }}
              >
                {row.meta}
              </Typography>
            </Box>
            <Typography
              sx={{
                fontWeight: 600,
                color: row.loss ? "error.main" : "success.main",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {row.pnl}
            </Typography>
          </Stack>
        ))}
      </Box>
    </Box>
  );
}
