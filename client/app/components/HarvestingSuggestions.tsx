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
 * A single harvesting suggestion card.
 */
function SuggestionCard({
  suggestion,
}: Readonly<{
  suggestion: HarvestingSuggestion;
}>) {
  const [expanded, setExpanded] = useState(false);

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
          <Box sx={{ flex: 1 }}>
            <Box
              sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}
            >
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {suggestion.symbol}
              </Typography>
              <Chip
                label={`#${suggestion.priority}`}
                size="small"
                color="primary"
                sx={{ height: 20, fontSize: "0.65rem" }}
              />
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
                    backgroundColor: "#e3f2fd",
                    color: "#1565c0",
                  }}
                />
              )}
            </Box>

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

  return (
    <Stack spacing={2}>
      {suggestions.map((suggestion) => (
        <SuggestionCard key={suggestion.symbol} suggestion={suggestion} />
      ))}
    </Stack>
  );
}
