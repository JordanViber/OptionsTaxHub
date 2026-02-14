"use client";

import {
  Alert,
  Box,
  Card,
  CardContent,
  Divider,
  Typography,
} from "@mui/material";
import { Warning as WarnIcon } from "@mui/icons-material";
import type { WashSaleFlag } from "@/lib/types";

interface WashSaleWarningProps {
  flags: WashSaleFlag[];
}

/**
 * Wash-sale warning banner.
 *
 * Displays an orange alert when wash-sale rule violations are detected
 * in the transaction history, with details of each flag.
 */
export default function WashSaleWarning({
  flags,
}: Readonly<WashSaleWarningProps>) {
  if (flags.length === 0) return null;

  return (
    <Alert
      severity="warning"
      icon={<WarnIcon />}
      sx={{ "& .MuiAlert-message": { width: "100%" } }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
        Wash-Sale Rule Violations Detected ({flags.length})
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mb: 1.5, display: "block" }}
      >
        The IRS wash-sale rule disallows loss deductions when you repurchase
        substantially identical securities within 30 days of selling at a loss.
        The disallowed loss is added to the cost basis of the replacement
        shares.
      </Typography>

      <Card variant="outlined" sx={{ backgroundColor: "transparent" }}>
        <CardContent sx={{ py: 1, "&:last-child": { pb: 1 } }}>
          {flags.map((flag, idx) => (
            <Box key={`${flag.symbol}-${flag.sale_date}`}>
              {idx > 0 && <Divider sx={{ my: 1 }} />}
              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                {flag.symbol}: ${flag.disallowed_loss.toLocaleString()} loss
                disallowed
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {flag.explanation}
              </Typography>
            </Box>
          ))}
        </CardContent>
      </Card>
    </Alert>
  );
}
