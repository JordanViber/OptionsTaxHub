"use client";

import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Collapse,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  AutoAwesome as AiIcon,
  SwapHoriz as SwapIcon,
  Warning as WarnIcon,
  TrendingDown as LossIcon,
} from "@mui/icons-material";
import { useState } from "react";
import type { HarvestingSuggestion, Position } from "@/lib/types";
import {
  buildHarvestTeasers,
  formatUsd,
  harvestTeaserTotals,
  PREVIEW_CARD_LIMIT,
  scrollToPacketPanel,
  type HarvestTeaser,
} from "@/lib/harvestPreview";

interface HarvestingSuggestionsProps {
  suggestions: HarvestingSuggestion[];
  lotsWithLosses?: number;
  locked?: boolean;
  positions?: Position[];
}

interface SuggestionDisplayMeta {
  suggestion: HarvestingSuggestion;
  lotIndex: number;
  lotCount: number;
}

/**
 * Format a number as USD currency.
 */
function formatCurrency(value: number): string {
  return formatUsd(value);
}

function formatShareCount(quantity: number): string {
  if (Number.isInteger(quantity)) return String(quantity);
  return quantity.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function replacementSymbolsLabel(
  suggestion: HarvestingSuggestion,
): string | null {
  const symbols = suggestion.replacement_candidates
    .map((candidate) => candidate.symbol)
    .filter(Boolean);
  if (symbols.length === 0) return null;
  if (symbols.length === 1) return symbols[0];
  if (symbols.length === 2) return `${symbols[0]} or ${symbols[1]}`;
  return `${symbols.slice(0, -1).join(", ")}, or ${symbols[symbols.length - 1]}`;
}

/**
 * Plain-language harvest action. Always visible — the card used to lead with
 * the ticker and leave the actual "sell this lot" instruction implicit.
 */
export function getRecommendedActionCopy(suggestion: HarvestingSuggestion): {
  headline: string;
  detail: string;
} {
  const qty = formatShareCount(suggestion.quantity);
  const symbol = suggestion.symbol;
  const term = suggestion.is_long_term ? "long-term" : "short-term";
  const loss = formatCurrency(Math.abs(suggestion.estimated_loss));
  const savings = formatCurrency(Math.abs(suggestion.tax_savings_estimate));
  const replacements = replacementSymbolsLabel(suggestion);
  const headline = `Sell ${qty} ${symbol} to harvest this ${term} loss`;
  const rotate = replacements
    ? ` To keep similar exposure without buying ${symbol} back, look at ${replacements}.`
    : ` Do not repurchase ${symbol} (or a substantially identical security) for 31 days.`;
  if (suggestion.wash_sale_risk) {
    return {
      headline,
      detail: `This lot is a harvest candidate, but selling now may trigger a wash sale — read the warning before you act. Estimated loss ${loss}; estimated tax savings ${savings}.${rotate}`,
    };
  }
  return {
    headline,
    detail: `Realize about ${loss} of loss for an estimated ${savings} in tax savings.${rotate}`,
  };
}

function suggestionGroupKey(suggestion: HarvestingSuggestion): string {
  return suggestion.display_label || suggestion.symbol;
}

function buildSuggestionDisplayMeta(
  suggestions: HarvestingSuggestion[],
): SuggestionDisplayMeta[] {
  const counts = new Map<string, number>();
  const seen = new Map<string, number>();

  for (const suggestion of suggestions) {
    const key = suggestionGroupKey(suggestion);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  return suggestions.map((suggestion) => {
    const key = suggestionGroupKey(suggestion);
    const lotIndex = (seen.get(key) ?? 0) + 1;
    seen.set(key, lotIndex);
    return {
      suggestion,
      lotIndex,
      lotCount: counts.get(key) ?? 1,
    };
  });
}

function getSuggestionDetailText(
  suggestion: HarvestingSuggestion,
  lotIndex: number,
  lotCount: number,
): string {
  const lotPrefix = lotCount > 1 ? `Lot ${lotIndex} of ${lotCount}` : "Tax lot";
  if (suggestion.lot_details) {
    return lotCount > 1
      ? `${lotPrefix} • ${suggestion.lot_details}`
      : suggestion.lot_details;
  }

  return `${lotPrefix} • Qty ${suggestion.quantity} at ${formatCurrency(
    suggestion.cost_basis_per_share,
  )} cost basis`;
}

/**
 * A single harvesting suggestion card.
 */
function SuggestionCard({
  suggestion,
  lotIndex,
  lotCount,
}: Readonly<{
  suggestion: HarvestingSuggestion;
  lotIndex: number;
  lotCount: number;
}>) {
  const [expanded, setExpanded] = useState(false);
  const needsManualReview = Boolean(suggestion.manual_review_required);
  const detailText = getSuggestionDetailText(suggestion, lotIndex, lotCount);
  const recommended = getRecommendedActionCopy(suggestion);

  return (
    <Card
      variant="outlined"
      sx={{
        borderLeft: 4,
        borderLeftColor: suggestion.wash_sale_risk
          ? "warning.main"
          : "success.main",
      }}
    >
      <CardContent sx={{ pb: expanded ? 2 : "16px !important" }}>
        {/* Header */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
          }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                mb: 0.5,
                flexWrap: "wrap",
              }}
            >
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {suggestion.display_label || suggestion.symbol}
              </Typography>
              {suggestion.display_label &&
                suggestion.display_label !== suggestion.symbol && (
                  <Chip
                    label={suggestion.symbol}
                    size="small"
                    variant="outlined"
                    sx={{ height: 20, fontSize: "0.65rem" }}
                  />
                )}
              <Chip
                label="Sell to harvest"
                size="small"
                color="error"
                variant="outlined"
                sx={{ height: 20, fontSize: "0.65rem", fontWeight: 700 }}
              />
              <Chip
                label={`#${suggestion.priority}`}
                size="small"
                color="primary"
                sx={{ height: 20, fontSize: "0.65rem" }}
              />
              {lotCount > 1 && (
                <Chip
                  label={`Lot ${lotIndex}/${lotCount}`}
                  size="small"
                  variant="outlined"
                  sx={{ height: 20, fontSize: "0.65rem" }}
                />
              )}
              <Chip
                label={suggestion.is_long_term ? "Long-Term" : "Short-Term"}
                size="small"
                color={suggestion.is_long_term ? "success" : "warning"}
                variant="outlined"
                sx={{ height: 20, fontSize: "0.65rem" }}
              />
              {suggestion.ai_generated && (
                <Chip
                  icon={<AiIcon sx={{ fontSize: 12 }} />}
                  label="AI"
                  size="small"
                  sx={{
                    height: 20,
                    fontSize: "0.65rem",
                    backgroundColor: "rgba(122, 158, 132, 0.18)",
                    color: "#7a9e84",
                  }}
                />
              )}
              {needsManualReview && (
                <Chip
                  icon={<WarnIcon sx={{ fontSize: 12 }} />}
                  label="Manual review"
                  size="small"
                  color="warning"
                  variant="outlined"
                  sx={{ height: 20, fontSize: "0.65rem" }}
                />
              )}
            </Box>

            {detailText && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ display: "block", mb: 0.5 }}
              >
                {detailText}
              </Typography>
            )}

            <Box
              data-testid="harvest-recommended-action"
              sx={{
                mt: 1,
                mb: 1,
                px: 1.25,
                py: 1,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "action.hover",
              }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  display: "block",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  fontWeight: 700,
                  mb: 0.25,
                }}
              >
                Recommended action
              </Typography>
              <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                {recommended.headline}
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.25, lineHeight: 1.5 }}
              >
                {recommended.detail}
              </Typography>
            </Box>

            {needsManualReview && suggestion.manual_review_reason && (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 0.75,
                  mb: 0.5,
                }}
              >
                <WarnIcon
                  sx={{ color: "warning.main", fontSize: 14, mt: 0.1 }}
                />
                <Typography
                  variant="caption"
                  sx={{ color: "warning.dark", display: "block" }}
                >
                  {suggestion.manual_review_reason}
                </Typography>
              </Box>
            )}

            <Stack direction="row" spacing={3} sx={{ mt: 1 }}>
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Estimated Loss
                </Typography>
                <Typography
                  variant="body1"
                  sx={{ fontWeight: 600, color: "error.main" }}
                >
                  <LossIcon
                    sx={{ fontSize: 14, mr: 0.5, verticalAlign: "middle" }}
                  />
                  {formatCurrency(suggestion.estimated_loss)}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Tax Savings
                </Typography>
                <Typography
                  variant="body1"
                  sx={{ fontWeight: 600, color: "success.main" }}
                >
                  {formatCurrency(suggestion.tax_savings_estimate)}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Qty × Cost
                </Typography>
                <Typography variant="body2">
                  {suggestion.quantity} ×{" "}
                  {formatCurrency(suggestion.cost_basis_per_share)}
                </Typography>
              </Box>
            </Stack>
          </Box>

          <IconButton
            size="small"
            onClick={() => setExpanded(!expanded)}
            aria-label={expanded ? "Show less" : "Show more"}
          >
            {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          </IconButton>
        </Box>

        {/* Wash-sale warning */}
        {suggestion.wash_sale_risk && (
          <Box
            sx={{
              mt: 1.5,
              p: 1,
              borderRadius: 1,
              backgroundColor: "warning.dark",
              display: "flex",
              alignItems: "flex-start",
              gap: 1,
            }}
          >
            <WarnIcon sx={{ color: "warning.main", fontSize: 18, mt: 0.2 }} />
            <Typography variant="caption" sx={{ color: "warning.main" }}>
              {suggestion.wash_sale_explanation ||
                "Wash-sale risk detected. Selling this position may trigger wash-sale rules."}
            </Typography>
          </Box>
        )}

        {/* Expandable details */}
        <Collapse in={expanded}>
          <Divider sx={{ my: 1.5 }} />

          {suggestion.ai_explanation && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
                {suggestion.ai_generated ? "AI Analysis" : "Analysis"}
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ lineHeight: 1.6 }}
              >
                {suggestion.ai_explanation}
              </Typography>
            </Box>
          )}

          {suggestion.replacement_candidates.length > 0 && (
            <Box>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  mb: 0.5,
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                }}
              >
                <SwapIcon sx={{ fontSize: 16 }} />
                Replacement Candidates
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mb: 1, display: "block" }}
              >
                Similar exposure without triggering wash-sale rules
              </Typography>
              <List dense disablePadding>
                {suggestion.replacement_candidates.map((candidate) => (
                  <ListItem
                    key={candidate.symbol}
                    disableGutters
                    sx={{ py: 0.25 }}
                  >
                    <ListItemText
                      primary={
                        <Box
                          sx={{ display: "flex", alignItems: "center", gap: 1 }}
                        >
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {candidate.symbol}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {candidate.name}
                          </Typography>
                        </Box>
                      }
                      secondary={candidate.reason}
                      slotProps={{ secondary: { variant: "caption" } }}
                    />
                  </ListItem>
                ))}
              </List>
            </Box>
          )}
        </Collapse>
      </CardContent>
    </Card>
  );
}

function HarvestTeaserCard({ teaser }: Readonly<{ teaser: HarvestTeaser }>) {
  return (
    <Card
      variant="outlined"
      data-testid="harvest-teaser-card"
      sx={{
        borderLeft: 4,
        borderLeftColor: teaser.washSaleRisk ? "warning.main" : "error.main",
      }}
    >
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            gap: 2,
            alignItems: "flex-start",
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }} noWrap>
              {teaser.label}
            </Typography>
            <Stack
              direction="row"
              spacing={0.75}
              sx={{ mt: 0.5 }}
              flexWrap="wrap"
              useFlexGap
            >
              <Chip
                label={teaser.isLongTerm ? "Long-term" : "Short-term"}
                size="small"
                variant="outlined"
                sx={{ height: 20, fontSize: "0.65rem" }}
              />
              {teaser.washSaleRisk && (
                <Chip
                  label="31-day wait"
                  size="small"
                  color="warning"
                  sx={{ height: 20, fontSize: "0.65rem" }}
                />
              )}
            </Stack>
          </Box>
          <Box sx={{ textAlign: "right", flexShrink: 0 }}>
            <Typography variant="caption" color="text.secondary">
              Open loss
            </Typography>
            <Typography sx={{ fontWeight: 700, color: "error.main" }}>
              {formatCurrency(teaser.estimatedLoss)}
            </Typography>
            {teaser.taxSavings != null && teaser.taxSavings > 0 && (
              <Typography
                variant="caption"
                sx={{ color: "success.main", display: "block" }}
              >
                {formatCurrency(teaser.taxSavings)} est. savings
              </Typography>
            )}
          </Box>
        </Box>
        <Box
          sx={{
            mt: 1.25,
            filter: "blur(5px)",
            userSelect: "none",
            opacity: 0.5,
          }}
          aria-hidden
        >
          <Typography variant="body2">
            Sell quantity and replacement names
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Lot opened · cost basis · wash-sale dates
          </Typography>
        </Box>
      </CardContent>
    </Card>
  );
}

function LockedHarvestPreview({
  teasers,
}: Readonly<{ teasers: HarvestTeaser[] }>) {
  const totals = harvestTeaserTotals(teasers);
  const visible = teasers.slice(0, PREVIEW_CARD_LIMIT);
  const hiddenCount = teasers.length - visible.length;
  const savingsLine = totals.hasTaxSavings
    ? ` · ${formatCurrency(totals.totalSavings)} estimated tax savings`
    : "";

  return (
    <Stack spacing={1.5} data-testid="harvest-packet-preview">
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Typography
            variant="caption"
            sx={{
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
              color: "text.secondary",
              fontWeight: 700,
            }}
          >
            Harvest preview
          </Typography>
          <Typography
            sx={{
              mt: 0.5,
              fontFamily: "var(--font-display), Fraunces, Georgia, serif",
              fontSize: "1.75rem",
              fontWeight: 600,
              letterSpacing: "-0.03em",
              lineHeight: 1,
            }}
          >
            {formatCurrency(totals.totalLoss)}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            {totals.count} lot{totals.count === 1 ? "" : "s"} with unrealized
            losses
            {savingsLine}
          </Typography>
        </Box>
        <Button variant="contained" onClick={scrollToPacketPanel}>
          Unlock harvest plan — $49
        </Button>
      </Box>
      {visible.map((teaser) => (
        <HarvestTeaserCard key={teaser.id} teaser={teaser} />
      ))}
      {hiddenCount > 0 && (
        <Typography variant="caption" color="text.secondary">
          + {hiddenCount} more lot{hiddenCount === 1 ? "" : "s"} in the $49
          packet
        </Typography>
      )}
    </Stack>
  );
}

export function getHarvestEmptyCopy(lotsWithLosses = 0): string {
  if (lotsWithLosses > 0) {
    return (
      "Open lots with unrealized losses were found, but none are recommended " +
      "to sell right now. They may sit inside a 30-day wash-sale window, or " +
      "this year's realized losses already exceed the extra $3,000 this return can use."
    );
  }
  return "No tax-loss harvesting opportunities found. No open lots currently show an unrealized loss.";
}


/**
 * Tax-loss harvesting suggestions panel.
 *
 * Displays a ranked list of suggestion cards, each showing the symbol,
 * estimated loss, tax savings, wash-sale risk, and replacement candidates.
 * AI-generated suggestions are marked with an "AI" badge.
 */
export default function HarvestingSuggestions({
  suggestions,
  lotsWithLosses = 0,
  locked = false,
  positions = [],
}: Readonly<HarvestingSuggestionsProps>) {
  if (locked) {
    const teasers = buildHarvestTeasers(suggestions, positions);
    if (teasers.length > 0) {
      return <LockedHarvestPreview teasers={teasers} />;
    }
    return (
      <Card variant="outlined" data-testid="harvest-packet-preview">
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
            Harvest plan is in the $49 packet
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {lotsWithLosses > 0
              ? `${lotsWithLosses} open lot${lotsWithLosses === 1 ? "" : "s"} show an unrealized loss. Pay $49 for lot-level sell instructions, replacements, and the CPA PDF.`
              : "Pay $49 to unlock lot-level harvest instructions, wash-sale detail, and the year-close PDF."}
          </Typography>
        </CardContent>
      </Card>
    );
  }

  if (suggestions.length === 0) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Typography
            variant="body1"
            color="text.secondary"
            sx={{ textAlign: "center", py: 3 }}
          >
            {getHarvestEmptyCopy(lotsWithLosses)}
          </Typography>
        </CardContent>
      </Card>
    );
  }

  const displayMeta = buildSuggestionDisplayMeta(suggestions);

  return (
    <Stack spacing={2}>
      {displayMeta.map(({ suggestion, lotIndex, lotCount }) => (
        <SuggestionCard
          key={
            suggestion.suggestion_id ||
            `${suggestion.symbol}-${suggestion.priority}`
          }
          suggestion={suggestion}
          lotIndex={lotIndex}
          lotCount={lotCount}
        />
      ))}
    </Stack>
  );
}
