"use client";

import {
  Box,
  Card,
  CardContent,
  Chip,
  Divider,
  Typography,
  Grid,
} from "@mui/material";
import {
  AccountBalance as PortfolioIcon,
  TrendingDown as LossIcon,
  TrendingUp as GainIcon,
  Savings as SavingsIcon,
  Warning as WarnIcon,
  Receipt as RealizedIcon,
} from "@mui/icons-material";
import type { PortfolioSummary, RealizedSummary } from "@/lib/types";

interface PortfolioSummaryCardsProps {
  summary: PortfolioSummary;
}

/**
 * Format a number as USD currency.
 */
function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

/**
 * A single summary metric card.
 */
function MetricCard({
  title,
  value,
  subtitle,
  icon,
  color,
}: Readonly<{
  title: string;
  value: string;
  subtitle?: string;
  icon: React.ReactNode;
  color: string;
}>) {
  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2 }}>
          <Box
            sx={{
              p: 1,
              borderRadius: 2,
              backgroundColor: `${color}15`,
              color,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {icon}
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                fontWeight: 500,
                textTransform: "uppercase",
                letterSpacing: 0.5,
              }}
            >
              {title}
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>
              {value}
            </Typography>
            {subtitle && (
              <Typography variant="caption" color="text.secondary">
                {subtitle}
              </Typography>
            )}
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
}

/**
 * Realized gain/loss breakdown card.
 */
function RealizedCard({ realized }: Readonly<{ realized: RealizedSummary }>) {
  const netColor = realized.total_net >= 0 ? "#2e7d32" : "#d32f2f";
  const netSign = realized.total_net >= 0 ? "+" : "";
  const stSign = realized.net_st >= 0 ? "+" : "";
  const ltSign = realized.net_lt >= 0 ? "+" : "";

  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2 }}>
          <Box
            sx={{
              p: 1,
              borderRadius: 2,
              backgroundColor: `${netColor}15`,
              color: netColor,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <RealizedIcon />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                }}
              >
                {realized.tax_year} Realized
              </Typography>
              <Chip
                label={`${realized.transactions_count} trades`}
                size="small"
                variant="outlined"
                sx={{ height: 16, fontSize: "0.6rem" }}
              />
            </Box>
            <Typography
              variant="h5"
              sx={{ fontWeight: 700, color: netColor, mt: 0.5 }}
            >
              {netSign}
              {formatCurrency(realized.total_net)}
            </Typography>
            <Divider sx={{ my: 0.5 }} />
            <Box sx={{ display: "flex", gap: 1 }}>
              <Typography variant="caption" color="text.secondary">
                ST:{" "}
                <strong
                  style={{
                    color: realized.net_st >= 0 ? "#2e7d32" : "#d32f2f",
                  }}
                >
                  {stSign}
                  {formatCurrency(realized.net_st)}
                </strong>
              </Typography>
              <Typography variant="caption" color="text.secondary">
                LT:{" "}
                <strong
                  style={{
                    color: realized.net_lt >= 0 ? "#2e7d32" : "#d32f2f",
                  }}
                >
                  {ltSign}
                  {formatCurrency(realized.net_lt)}
                </strong>
              </Typography>
            </Box>
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
}

/**
 * Portfolio summary cards — displayed at top of dashboard after analysis.
 *
 * Shows: Net Open Position Value, Unrealized P&L, Harvestable Losses, Est. Tax Savings,
 * and (when available) the realized gain/loss breakdown for the analysis tax year.
 */
export default function PortfolioSummaryCards({
  summary,
}: Readonly<PortfolioSummaryCardsProps>) {
  const pnlColor = summary.total_unrealized_pnl >= 0 ? "#2e7d32" : "#d32f2f";
  const pnlSign = summary.total_unrealized_pnl >= 0 ? "+" : "";
  const hasRealized = !!summary.realized_summary;
  const md = hasRealized ? 2 : 3;

  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, sm: 6, md }}>
        <MetricCard
          title="Net Open Position Value"
          value={formatCurrency(summary.total_market_value)}
          subtitle={`${summary.positions_count} open positions — excludes cash and account equity adjustments`}
          icon={<PortfolioIcon />}
          color="#1976d2"
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md }}>
        <MetricCard
          title="Unrealized P&L"
          value={`${pnlSign}${formatCurrency(summary.total_unrealized_pnl)}`}
          subtitle={`${pnlSign}${summary.total_unrealized_pnl_pct.toFixed(1)}%`}
          icon={summary.total_unrealized_pnl >= 0 ? <GainIcon /> : <LossIcon />}
          color={pnlColor}
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md }}>
        <MetricCard
          title="Harvestable Losses"
          value={formatCurrency(summary.total_harvestable_losses)}
          subtitle={`${summary.lots_with_losses} lots with losses`}
          icon={<LossIcon />}
          color="#d32f2f"
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md }}>
        <MetricCard
          title="Est. Tax Savings"
          value={formatCurrency(summary.estimated_tax_savings)}
          subtitle={
            summary.wash_sale_flags_count > 0
              ? `${summary.wash_sale_flags_count} wash-sale warning(s)`
              : "From harvesting losses"
          }
          icon={
            summary.wash_sale_flags_count > 0 ? <WarnIcon /> : <SavingsIcon />
          }
          color={summary.wash_sale_flags_count > 0 ? "#f57f17" : "#2e7d32"}
        />
      </Grid>
      {hasRealized && summary.realized_summary && (
        <Grid size={{ xs: 12, sm: 12, md: 4 }}>
          <RealizedCard realized={summary.realized_summary} />
        </Grid>
      )}
    </Grid>
  );
}
