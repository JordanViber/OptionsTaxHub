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
  Stack,
  Typography,
} from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  AutoAwesome as AiIcon,
  Warning as WarnIcon,
  TrendingDown as LossIcon,
} from "@mui/icons-material";
import { useState } from "react";
import type { HarvestingSuggestion, Position, PriceBasis } from "@/lib/types";
import { pricedNumbersVisible } from "@/lib/priceBasis";
import PriceAsOfNote from "./PriceAsOfNote";
import { harvestPromptBlocked } from "@/lib/harvestCheck";
import {
  buildHarvestTeasers,
  formatUsd,
  harvestTeaserTotals,
  PREVIEW_CARD_LIMIT,
  purchaseFillUnknownCopy,
  scrollToPacketPanel,
  type HarvestTeaser,
} from "@/lib/harvestPreview";

interface HarvestingSuggestionsProps {
  suggestions: HarvestingSuggestion[];
  lotsWithLosses?: number;
  locked?: boolean;
  positions?: Position[];
  priceBasis?: PriceBasis | null;
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

export const HARVEST_WASH_WINDOW_NOTE =
  "Buying the same stock or its options within 30 days may disallow the loss. See the year-end harvest check.";

const HARVEST_WASH_WARNING =
  "Wash-sale risk detected. A wash sale may apply. See the year-end harvest check.";

/** Queue display only. A sell or buy instruction in source copy is not shown. */
export function harvestQueueWarning(explanation: string | null | undefined): string {
  const text = (explanation || "").trim();
  if (!text || /\bSell(?:ing|s)?\b|\bBuy(?:ing)?\b/i.test(text)) {
    return HARVEST_WASH_WARNING;
  }
  return text;
}

/**
 * Informational harvest note. No trade instruction and no replacement ticker.
 */
export function getRecommendedActionCopy(suggestion: HarvestingSuggestion): {
  headline: string;
  detail: string;
} {
  const symbol = suggestion.symbol;
  if (harvestPromptBlocked(symbol)) {
    return {
      headline: "A wash-sale rule may apply, ask a tax pro.",
      detail: "This note is informational. It is not a recommendation to trade.",
    };
  }
  const qty = formatShareCount(suggestion.quantity);
  const term = suggestion.is_long_term ? "long-term" : "short-term";
  const singular = suggestion.quantity === 1;
  const noun = singular ? "share" : "shares";
  const verb = singular ? "shows" : "show";
  const loss = formatCurrency(Math.abs(suggestion.estimated_loss));
  const savings = formatCurrency(Math.abs(suggestion.tax_savings_estimate));
  const headline = `${qty} ${symbol} ${noun} ${verb} a ${term} loss you may be able to harvest.`;
  const dollars = `Estimated loss ${loss}. Estimated tax savings ${savings}.`;
  const windowNote = ` ${HARVEST_WASH_WINDOW_NOTE}`;
  if (suggestion.wash_sale_risk) {
    return {
      headline,
      detail: `A wash sale may apply to this lot. ${dollars}${windowNote}`,
    };
  }
  return {
    headline,
    detail: `${dollars}${windowNote}`,
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
  showPrices,
}: Readonly<{
  suggestion: HarvestingSuggestion;
  lotIndex: number;
  lotCount: number;
  showPrices: boolean;
}>) {
  const [expanded, setExpanded] = useState(false);
  const needsManualReview = Boolean(suggestion.manual_review_required);
  const detailText = getSuggestionDetailText(suggestion, lotIndex, lotCount);
  const unknownLoss =
    Boolean(suggestion.price_note) || suggestion.action === "REVIEW";
  const blockedPrompt = harvestPromptBlocked(suggestion.symbol);
  const recommended =
    unknownLoss && !blockedPrompt ? null : getRecommendedActionCopy(suggestion);
  const hasDetails = !unknownLoss && Boolean(suggestion.ai_explanation);

  return (
    <Card
      variant="outlined"
      sx={{
        borderLeft: 4,
        borderLeftColor: unknownLoss
          ? "divider"
          : suggestion.wash_sale_risk
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
              {unknownLoss || blockedPrompt ? null : (
                <Chip
                  label="Harvest candidate"
                  size="small"
                  color="error"
                  variant="outlined"
                  sx={{ height: 20, fontSize: "0.65rem", fontWeight: 700 }}
                />
              )}
              {unknownLoss ? null : (
                <Chip
                  label={`#${suggestion.priority}`}
                  size="small"
                  color="primary"
                  sx={{ height: 20, fontSize: "0.65rem" }}
                />
              )}
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
              {suggestion.ai_generated && !unknownLoss && (
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

            {recommended ? (
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
                  Harvest note
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
            ) : null}

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

            {suggestion.price_note ? (
              <Typography
                variant="caption"
                color="text.secondary"
                data-testid="harvest-price-note"
                sx={{ display: "block", mt: 1 }}
              >
                {suggestion.price_note}
              </Typography>
            ) : null}

            <Stack direction="row" spacing={3} sx={{ mt: 1 }}>
              <Box>
                <Typography variant="caption" color="text.secondary">
                  {unknownLoss ? "Loss" : "Estimated Loss"}
                </Typography>
                <Typography
                  variant="body1"
                  sx={{ fontWeight: 600, color: "error.main" }}
                >
                  {unknownLoss ? null : (
                    <LossIcon
                      sx={{ fontSize: 14, mr: 0.5, verticalAlign: "middle" }}
                    />
                  )}
                  {unknownLoss
                    ? "Unknown"
                    : showPrices
                      ? formatCurrency(suggestion.estimated_loss)
                      : "—"}
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
                  {unknownLoss
                    ? "Unknown"
                    : showPrices
                      ? formatCurrency(suggestion.tax_savings_estimate)
                      : "—"}
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

          {hasDetails ? (
            <IconButton
              size="small"
              onClick={() => setExpanded(!expanded)}
              aria-label={expanded ? "Show less" : "Show more"}
            >
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          ) : null}
        </Box>

        {/* Wash-sale warning */}
        {suggestion.wash_sale_risk && !blockedPrompt && (
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
              {harvestQueueWarning(suggestion.wash_sale_explanation)}
            </Typography>
          </Box>
        )}

        {hasDetails ? (
          <Collapse in={expanded}>
            <Divider sx={{ my: 1.5 }} />
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
          </Collapse>
        ) : null}
      </CardContent>
    </Card>
  );
}

function HarvestTeaserCard({
  teaser,
  showPrices,
}: Readonly<{ teaser: HarvestTeaser; showPrices: boolean }>) {
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
            {teaser.priceNote ? (
              <Typography
                variant="caption"
                color="text.secondary"
                data-testid="harvest-price-note"
                sx={{ display: "block" }}
              >
                {teaser.priceNote}
              </Typography>
            ) : null}
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
              {teaser.priceNote ? "Loss" : "Open loss"}
            </Typography>
            <Typography sx={{ fontWeight: 700, color: "error.main" }}>
              {teaser.priceNote
                ? "Unknown"
                : showPrices
                  ? formatCurrency(teaser.estimatedLoss)
                  : "—"}
            </Typography>
            {showPrices &&
              !teaser.priceNote &&
              teaser.taxSavings != null &&
              teaser.taxSavings > 0 && (
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
            Share count and wash-sale window
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
  priceBasis,
}: Readonly<{ teasers: HarvestTeaser[]; priceBasis?: PriceBasis | null }>) {
  const showPrices = pricedNumbersVisible(priceBasis);
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
          {totals.count > 0 ? (
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
          ) : null}
          {totals.count > 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
              {totals.count} lot{totals.count === 1 ? "" : "s"} with unrealized
              losses
              {savingsLine}
            </Typography>
          ) : null}
          {totals.unknownLossCount > 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              data-testid="harvest-unknown-loss-count"
              sx={{ mt: 0.75 }}
            >
              {purchaseFillUnknownCopy(totals.unknownLossCount)}
            </Typography>
          ) : null}
        </Box>
        <Button variant="contained" onClick={scrollToPacketPanel}>
          Unlock harvest plan — $49
        </Button>
      </Box>
      <PriceAsOfNote basis={priceBasis} surface="harvest" />
      {visible.map((teaser) => (
        <HarvestTeaserCard
          key={teaser.id}
          teaser={teaser}
          showPrices={showPrices}
        />
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
  priceBasis,
}: Readonly<HarvestingSuggestionsProps>) {
  const showPrices = pricedNumbersVisible(priceBasis);
  if (locked) {
    const teasers = buildHarvestTeasers(suggestions, positions);
    if (teasers.length > 0) {
      return (
        <LockedHarvestPreview teasers={teasers} priceBasis={priceBasis} />
      );
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
  const unknownLossCount = suggestions.filter((row) => row.price_note).length;

  return (
    <Stack spacing={2}>
      <PriceAsOfNote basis={priceBasis} surface="harvest" />
      {unknownLossCount > 0 ? (
        <Typography
          variant="body2"
          color="text.secondary"
          data-testid="harvest-unknown-loss-count"
        >
          {purchaseFillUnknownCopy(unknownLossCount)}
        </Typography>
      ) : null}
      {displayMeta.map(({ suggestion, lotIndex, lotCount }) => (
        <SuggestionCard
          key={
            suggestion.suggestion_id ||
            `${suggestion.symbol}-${suggestion.priority}`
          }
          suggestion={suggestion}
          lotIndex={lotIndex}
          lotCount={lotCount}
          showPrices={showPrices}
        />
      ))}
    </Stack>
  );
}
