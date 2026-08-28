"use client";

import {
  Box,
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
import type { HarvestingSuggestion } from "@/lib/types";

interface HarvestingSuggestionsProps {
  suggestions: HarvestingSuggestion[];
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
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
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
              backgroundColor: "#fff8e1",
              display: "flex",
              alignItems: "flex-start",
              gap: 1,
            }}
          >
            <WarnIcon sx={{ color: "warning.main", fontSize: 18, mt: 0.2 }} />
            <Typography variant="caption" sx={{ color: "warning.dark" }}>
              {suggestion.wash_sale_explanation ||
                "Wash-sale risk detected. Selling this position may trigger wash-sale rules."}
            </Typography>
          </Box>
        )}

        {/* Expandable details */}
        <Collapse in={expanded}>
          <Divider sx={{ my: 1.5 }} />

          {/* AI Explanation */}
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

          {/* Replacement Candidates */}
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

/**
 * Tax-loss harvesting suggestions panel.
 *
 * Displays a ranked list of suggestion cards, each showing the symbol,
 * estimated loss, tax savings, wash-sale risk, and replacement candidates.
 * AI-generated suggestions are marked with an "AI" badge.
 */
export default function HarvestingSuggestions({
  suggestions,
}: Readonly<HarvestingSuggestionsProps>) {
  if (suggestions.length === 0) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Typography
            variant="body1"
            color="text.secondary"
            sx={{ textAlign: "center", py: 3 }}
          >
            No tax-loss harvesting opportunities found. All positions are
            currently at a gain.
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
