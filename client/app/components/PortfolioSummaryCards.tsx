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
  preview?: {
    locked: boolean;
    candidateCount: number;
    openLossTotal: number;
    potentialTaxSavings: number;
  };
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
              backgroundColor: `${color}22`,
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
  const netColor = realized.total_net >= 0 ? "#7a9e84" : "#c46a58";
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
              backgroundColor: `${netColor}22`,
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
                    color: realized.net_st >= 0 ? "#7a9e84" : "#c46a58",
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
                    color: realized.net_lt >= 0 ? "#7a9e84" : "#c46a58",
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
 * Portfolio summary — harvest number is the hero, then supporting metrics.
 */
export default function PortfolioSummaryCards({
  summary,
  preview,
}: Readonly<PortfolioSummaryCardsProps>) {
  const pnlColor = summary.total_unrealized_pnl >= 0 ? "#7a9e84" : "#c46a58";
  const pnlSign = summary.total_unrealized_pnl >= 0 ? "+" : "";
  const hasRealized = !!summary.realized_summary;
  const savingsColor =
    summary.wash_sale_flags_count > 0 ? "#c4a36a" : "#7a9e84";
  const showLossPreview =
    Boolean(preview?.locked) &&
    (preview?.candidateCount ?? 0) > 0 &&
    summary.estimated_tax_savings <= 0;
  let heroValue = summary.estimated_tax_savings;
  if (showLossPreview) {
    heroValue = preview?.openLossTotal ?? 0;
  } else if (preview?.locked && (preview.potentialTaxSavings ?? 0) > 0) {
    heroValue = preview.potentialTaxSavings;
  }
  const heroLabel = showLossPreview ? "Open losses to review" : "Est. Tax Savings";
  const heroColor = showLossPreview ? "#c46a58" : savingsColor;
  const harvestableValue =
    preview?.locked && (preview.openLossTotal ?? 0) > summary.total_harvestable_losses
      ? preview.openLossTotal
      : summary.total_harvestable_losses;
  const harvestableSubtitle = `${
    preview?.locked
      ? preview.candidateCount || summary.lots_with_losses
      : summary.lots_with_losses
  } lots with losses`;

  return (
    <Box sx={{ display: "grid", gap: 2 }}>
      <Card>
        <CardContent sx={{ px: { xs: 2.5, sm: 3.5 }, py: { xs: 2.5, sm: 3 } }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: 1.4,
              fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
            }}
          >
            {heroLabel}
          </Typography>
          <Typography
            sx={{
              mt: 1,
              fontFamily: "var(--font-display), Fraunces, Georgia, serif",
              fontSize: { xs: "2.6rem", sm: "3.4rem" },
              fontWeight: 600,
              letterSpacing: "-0.03em",
              lineHeight: 1,
              color: heroColor,
            }}
          >
            {formatCurrency(heroValue)}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
            {showLossPreview
              ? `${preview?.candidateCount ?? 0} lots with unrealized losses. Unlock the $49 plan for sell instructions and what to wait on.`
              : `Federal harvest still available from ${formatCurrency(
                  harvestableValue,
                )} of losing lots.`}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {summary.wash_sale_flags_count > 0
              ? `${summary.wash_sale_flags_count} wash-sale warning(s)`
              : showLossPreview
                ? "Preview — recipes stay in the packet"
                : "From harvesting losses"}
          </Typography>
        </CardContent>
      </Card>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, sm: 6, md: hasRealized ? 3 : 4 }}>
          <MetricCard
            title="Net Open Position Value"
            value={formatCurrency(summary.total_market_value)}
            subtitle={`${summary.positions_count} open positions — short options count as liabilities; excludes cash`}
            icon={<PortfolioIcon />}
            color="#d8d2c6"
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: hasRealized ? 3 : 4 }}>
          <MetricCard
            title="Unrealized P&L"
            value={`${pnlSign}${formatCurrency(summary.total_unrealized_pnl)}`}
            subtitle={`${pnlSign}${summary.total_unrealized_pnl_pct.toFixed(1)}%`}
            icon={
              summary.total_unrealized_pnl >= 0 ? <GainIcon /> : <LossIcon />
            }
            color={pnlColor}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: hasRealized ? 3 : 4 }}>
          <MetricCard
            title="Harvestable Losses"
            value={formatCurrency(harvestableValue)}
            subtitle={harvestableSubtitle}
            icon={<LossIcon />}
            color="#c46a58"
          />
        </Grid>
        {hasRealized && summary.realized_summary && (
          <Grid size={{ xs: 12, sm: 6, md: 3 }}>
            <RealizedCard realized={summary.realized_summary} />
          </Grid>
        )}
      </Grid>
    </Box>
  );
}
