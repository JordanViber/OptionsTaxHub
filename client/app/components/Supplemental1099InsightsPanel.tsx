"use client";

import { Box, Stack, Typography } from "@mui/material";
import type { RealizedSummary, Supplemental1099Summary } from "@/lib/types";
import {
  SUPPLEMENTAL_1099_APPLIED_COPY,
  SUPPLEMENTAL_1099_APPLIED_TITLE,
  SUPPLEMENTAL_1099_BROKER_COLUMN,
  SUPPLEMENTAL_1099_COMPARE_COPY,
  SUPPLEMENTAL_1099_COMPARE_TITLE,
  SUPPLEMENTAL_1099_EXPORT_COLUMN,
  SUPPLEMENTAL_1099_GAP_COPY,
  SUPPLEMENTAL_1099_SETTLEMENT_FAQ,
  SUPPLEMENTAL_1099_UNKNOWN_YEAR_COPY,
  SUPPLEMENTAL_1099_UNKNOWN_YEAR_TITLE,
  SUPPLEMENTAL_1099_WASH_SALE_FAQ,
  combinedWashSaleDisallowed,
  exportLongTermNet,
  exportShortTermNet,
  formatUsd,
  isSameYear1099Compare,
  isUnknown1099Year,
} from "@/lib/supplemental1099";

function TotalsColumn({
  title,
  testId,
  shortTerm,
  longTerm,
  washSale,
}: Readonly<{
  title: string;
  testId: string;
  shortTerm: number;
  longTerm: number;
  washSale: number;
}>) {
  return (
    <Box data-testid={testId} sx={{ flex: 1, minWidth: 160 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        {title}
      </Typography>
      <Stack spacing={0.75}>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Short-term
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {formatUsd(shortTerm)}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Long-term
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {formatUsd(longTerm)}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Wash-sale disallowed
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {formatUsd(washSale)}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}

export default function Supplemental1099InsightsPanel({
  summary,
  analysisTaxYear = null,
  realizedSummary = null,
  csvWashSaleDisallowed = 0,
}: Readonly<{
  summary: Supplemental1099Summary;
  analysisTaxYear?: number | null;
  realizedSummary?: RealizedSummary | null;
  csvWashSaleDisallowed?: number;
}>) {
  const washSaleDisallowed = combinedWashSaleDisallowed(summary);
  const sameYear = isSameYear1099Compare(summary.tax_year, analysisTaxYear);
  const unknownYear = isUnknown1099Year(summary.tax_year);

  if (sameYear) {
    const showWashFaq =
      washSaleDisallowed > 0 || csvWashSaleDisallowed > 0;

    return (
      <Box
        data-testid="1099-vs-export-panel"
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
              {SUPPLEMENTAL_1099_COMPARE_TITLE}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {SUPPLEMENTAL_1099_COMPARE_COPY}
            </Typography>
          </Box>
          <Typography variant="body2">
            Using {summary.broker_name || "broker"} 1099 PDF for tax year{" "}
            {summary.tax_year ?? "unknown"} — same year as this export.
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {SUPPLEMENTAL_1099_GAP_COPY}
          </Typography>
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
          >
            <TotalsColumn
              title={SUPPLEMENTAL_1099_BROKER_COLUMN}
              testId="1099-broker-column"
              shortTerm={summary.short_term_net_gain}
              longTerm={summary.long_term_net_gain}
              washSale={washSaleDisallowed}
            />
            <TotalsColumn
              title={SUPPLEMENTAL_1099_EXPORT_COLUMN}
              testId="1099-export-column"
              shortTerm={exportShortTermNet(
                realizedSummary,
                csvWashSaleDisallowed,
              )}
              longTerm={exportLongTermNet(
                realizedSummary,
                csvWashSaleDisallowed,
              )}
              washSale={csvWashSaleDisallowed}
            />
          </Stack>
          {showWashFaq && (
            <Typography variant="body2" color="text.secondary">
              {SUPPLEMENTAL_1099_WASH_SALE_FAQ}
            </Typography>
          )}
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      data-testid={
        unknownYear
          ? "unknown-year-1099-supplement"
          : "previous-year-1099-supplement"
      }
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
            {unknownYear
              ? SUPPLEMENTAL_1099_UNKNOWN_YEAR_TITLE
              : SUPPLEMENTAL_1099_APPLIED_TITLE}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {unknownYear
              ? SUPPLEMENTAL_1099_UNKNOWN_YEAR_COPY
              : SUPPLEMENTAL_1099_APPLIED_COPY}
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
