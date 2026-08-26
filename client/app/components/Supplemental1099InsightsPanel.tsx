"use client";

import { Box, Stack, Typography } from "@mui/material";
import type { Supplemental1099Summary } from "@/lib/types";
import {
  SUPPLEMENTAL_1099_APPLIED_COPY,
  SUPPLEMENTAL_1099_APPLIED_TITLE,
  SUPPLEMENTAL_1099_SETTLEMENT_FAQ,
  SUPPLEMENTAL_1099_WASH_SALE_FAQ,
  combinedWashSaleDisallowed,
  formatUsd,
} from "@/lib/supplemental1099";

export default function Supplemental1099InsightsPanel({
  summary,
}: Readonly<{
  summary: Supplemental1099Summary;
}>) {
  const washSaleDisallowed = combinedWashSaleDisallowed(summary);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "info.light",
        borderRadius: 2,
        px: 2,
        py: 1.75,
        background:
          "linear-gradient(180deg, rgba(227,242,253,0.5) 0%, rgba(227,242,253,0.18) 100%)",
      }}
    >
      <Stack spacing={1.25}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            {SUPPLEMENTAL_1099_APPLIED_TITLE}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {SUPPLEMENTAL_1099_APPLIED_COPY}
          </Typography>
        </Box>
        <Typography variant="body2">
          Using {summary.broker_name || "broker"} 1099 PDF for tax year{" "}
          {summary.tax_year ?? "unknown"}.
        </Typography>
        <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Short-term proceeds
            </Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {formatUsd(summary.short_term_proceeds)}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Long-term proceeds
            </Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {formatUsd(summary.long_term_proceeds)}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Wash-sale disallowed
            </Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {formatUsd(washSaleDisallowed)}
            </Typography>
          </Box>
        </Stack>
        <Typography variant="body2" color="text.secondary">
          {SUPPLEMENTAL_1099_SETTLEMENT_FAQ}
        </Typography>
        {washSaleDisallowed > 0 && (
          <Typography variant="body2" color="text.secondary">
            {SUPPLEMENTAL_1099_WASH_SALE_FAQ}
          </Typography>
        )}
        {summary.insights.length > 0 && (
          <Box component="ul" sx={{ pl: 2.5, my: 0 }}>
            {summary.insights.map((insight) => (
              <Typography component="li" variant="body2" key={insight}>
                {insight}
              </Typography>
            ))}
          </Box>
        )}
      </Stack>
    </Box>
  );
}
