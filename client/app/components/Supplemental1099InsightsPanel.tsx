"use client";

import { Box, Chip, Stack, Typography } from "@mui/material";
import type {
  LotMatchReport,
  LotMatchRow,
  RealizedSummary,
  Supplemental1099Summary,
} from "@/lib/types";
import {
  LOT_MATCHED_1099B_LOCKED_COPY,
  LOT_MATCHED_1099B_TITLE,
  LOT_MATCHED_1099B_UNLOCKED_COPY,
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
  csvWashSaleDisallowedTotal,
  exportLongTermNet,
  exportShortTermNet,
  formatUsd,
  isSameYear1099Compare,
  isUnknown1099Year,
  type ClassifiedWashInput,
  type WashSaleFlagLike,
} from "@/lib/supplemental1099";

const LOT_TABLE_LIMIT = 50;

function formatLotDate(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return String(value).slice(0, 10);
}

function LotRowCard({ row }: Readonly<{ row: LotMatchRow }>) {
  return (
    <Box
      className="hairline"
      data-testid={`lot-match-row-${row.symbol || "unknown"}`}
      sx={{ borderRadius: 1.5, px: 1.25, py: 1, bgcolor: "background.paper" }}
    >
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        useFlexGap
      >
        <Typography variant="body2" sx={{ fontWeight: 700 }}>
          {row.symbol || "UNKNOWN"}
        </Typography>
        <Chip size="small" label={row.status} />
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        1099 sold {formatLotDate(row.date_sold_1099)} · export{" "}
        {formatLotDate(row.export_trade_date)} · qty {row.quantity}
      </Typography>
      <Typography variant="caption" sx={{ display: "block" }}>
        1099 {formatUsd(row.proceeds_1099)} · export {formatUsd(row.proceeds_export)}
      </Typography>
    </Box>
  );
}

function LotMatchSection({
  report,
  locked,
}: Readonly<{
  report: LotMatchReport;
  locked: boolean;
}>) {
  const previewRows = [
    ...report.matched,
    ...report.gap,
    ...report.unmatched,
  ].slice(0, LOT_TABLE_LIMIT);
  const totalRows =
    report.matched_count + report.gap_count + report.unmatched_count;

  return (
    <Box data-testid="lot-matched-1099b">
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
        {LOT_MATCHED_1099B_TITLE}
      </Typography>
      <Typography
        variant="body2"
        data-testid="lot-match-counts"
        sx={{ fontWeight: 600, mt: 0.5 }}
      >
        Matched {report.matched_count} · Gap {report.gap_count} · Unmatched{" "}
        {report.unmatched_count}
      </Typography>
      {locked ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
          {LOT_MATCHED_1099B_LOCKED_COPY}
        </Typography>
      ) : (
        <Stack spacing={1} sx={{ mt: 0.75 }}>
          <Typography variant="body2" color="text.secondary">
            {LOT_MATCHED_1099B_UNLOCKED_COPY}
          </Typography>
          {previewRows.map((row, index) => (
            <LotRowCard
              key={`${row.status}-${row.symbol}-${row.date_sold_1099}-${index}`}
              row={row}
            />
          ))}
          {totalRows > LOT_TABLE_LIMIT && (
            <Typography variant="caption" color="text.secondary">
              Showing {LOT_TABLE_LIMIT} of {totalRows} lots. Full list is in the
              PDF.
            </Typography>
          )}
        </Stack>
      )}
    </Box>
  );
}

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
  csvWashSaleFlags,
  lotMatchReport = null,
  locked = true,
}: Readonly<{
  summary: Supplemental1099Summary;
  analysisTaxYear?: number | null;
  realizedSummary?: RealizedSummary | null;
  csvWashSaleDisallowed?: number;
  csvWashSaleFlags?: WashSaleFlagLike[];
  lotMatchReport?: LotMatchReport | null;
  locked?: boolean;
}>) {
  const washSaleDisallowed = combinedWashSaleDisallowed(summary);
  const exportWashInput: ClassifiedWashInput =
    csvWashSaleFlags ?? csvWashSaleDisallowed;
  const exportWashTotal =
    csvWashSaleFlags != null
      ? csvWashSaleDisallowedTotal(csvWashSaleFlags)
      : csvWashSaleDisallowed;
  const sameYear = isSameYear1099Compare(summary.tax_year, analysisTaxYear);
  const unknownYear = isUnknown1099Year(summary.tax_year);

  if (sameYear) {
    const showWashFaq =
      washSaleDisallowed > 0 || exportWashTotal > 0;

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
              shortTerm={exportShortTermNet(realizedSummary, exportWashInput)}
              longTerm={exportLongTermNet(realizedSummary, exportWashInput)}
              washSale={exportWashTotal}
            />
          </Stack>
          {lotMatchReport && (
            <LotMatchSection report={lotMatchReport} locked={locked} />
          )}
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
