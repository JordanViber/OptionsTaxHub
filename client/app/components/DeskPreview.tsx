"use client";

import { Box, Chip, Stack, Typography } from "@mui/material";

const rows = [
  { label: "AMD 100c 7/24", meta: "2 ctr · $1,840", pnl: "−$612", loss: true },
  { label: "NVDA", meta: "12 sh · $1,428", pnl: "−$384", loss: true },
  { label: "AAPL 190c 6/18", meta: "1 ctr · $620", pnl: "+$94", loss: false },
  { label: "MSFT", meta: "8 sh · $3,360", pnl: "+$188", loss: false },
  { label: "META", meta: "4 sh · $2,040", pnl: "−$126", loss: true },
  { label: "SPY 520p 3/21", meta: "1 ctr · $410", pnl: "−$205", loss: true },
];

/**
 * Static landing preview of the desk — harvest number + sample rows.
 * Not live analysis; the 2026 sample CSV is the real path.
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
            $2,140
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
