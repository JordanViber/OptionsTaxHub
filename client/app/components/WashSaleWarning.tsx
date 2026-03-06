"use client";

import { useState } from "react";
import {
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Box,
  Chip,
  Typography,
} from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  Warning as WarnIcon,
} from "@mui/icons-material";
import type { WashSaleFlag } from "@/lib/types";

interface WashSaleWarningProps {
  flags: WashSaleFlag[];
}

/**
 * Wash-sale warning panel grouped by ticker.
 *
 * Instead of rendering every individual wash-sale event (which can number in the
 * hundreds), flags are grouped by symbol. Each ticker shows the total disallowed
 * loss and can be expanded to reveal the individual transactions.
 */
export default function WashSaleWarning({
  flags,
}: Readonly<WashSaleWarningProps>) {
  const [expanded, setExpanded] = useState<string | false>(false);

  if (flags.length === 0) return null;

  // Group flags by symbol and compute per-ticker totals
  const grouped: Record<string, { total: number; flags: WashSaleFlag[] }> = {};
  for (const flag of flags) {
    if (!grouped[flag.symbol]) {
      grouped[flag.symbol] = { total: 0, flags: [] };
    }
    grouped[flag.symbol].total += flag.disallowed_loss;
    grouped[flag.symbol].flags.push(flag);
  }

  // Sort tickers by total disallowed loss descending
  const tickers = Object.keys(grouped).sort(
    (a, b) => grouped[b].total - grouped[a].total,
  );

  const totalDisallowed = flags.reduce((sum, f) => sum + f.disallowed_loss, 0);

  const handleChange =
    (panel: string) => (_: React.SyntheticEvent, isExpanded: boolean) => {
      setExpanded(isExpanded ? panel : false);
    };

  return (
    <Alert
      severity="warning"
      icon={<WarnIcon />}
      sx={{ "& .MuiAlert-message": { width: "100%" } }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
        Wash-Sale Rule Violations Detected ({flags.length} events across{" "}
        {tickers.length} ticker{tickers.length !== 1 ? "s" : ""})
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mb: 1, display: "block" }}
      >
        Total disallowed loss:{" "}
        <strong>
          $
          {totalDisallowed.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}
        </strong>
        . The IRS wash-sale rule disallows loss deductions when you repurchase
        substantially identical securities within 30 days of selling at a loss.
        The disallowed amount is added to the cost basis of the replacement
        shares. Expand a ticker below for details.
      </Typography>

      <Box sx={{ mt: 1 }}>
        {tickers.map((ticker) => {
          const group = grouped[ticker];
          return (
            <Accordion
              key={ticker}
              expanded={expanded === ticker}
              onChange={handleChange(ticker)}
              disableGutters
              elevation={0}
              sx={{
                backgroundColor: "transparent",
                border: "1px solid",
                borderColor: "warning.light",
                mb: 0.5,
                borderRadius: 1,
                "&:before": { display: "none" },
              }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon />}
                sx={{ py: 0.25, minHeight: 36, "& .MuiAccordionSummary-content": { my: 0 } }}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    width: "100%",
                    mr: 1,
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{ fontWeight: 700, minWidth: 60 }}
                  >
                    {ticker}
                  </Typography>
                  <Chip
                    label={`$${group.total.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} disallowed`}
                    size="small"
                    color="warning"
                    sx={{ height: 20, fontSize: "0.7rem" }}
                  />
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ ml: "auto" }}
                  >
                    {group.flags.length} event{group.flags.length !== 1 ? "s" : ""}
                  </Typography>
                </Box>
              </AccordionSummary>
              <AccordionDetails sx={{ pt: 0, pb: 1 }}>
                {group.flags.map((flag, idx) => (
                  <Box
                    key={`${flag.sale_date}-${idx}`}
                    sx={{
                      py: 0.5,
                      borderTop: idx > 0 ? "1px solid" : "none",
                      borderColor: "divider",
                    }}
                  >
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block" }}
                    >
                      {flag.explanation}
                    </Typography>
                  </Box>
                ))}
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Box>
    </Alert>
  );
}
