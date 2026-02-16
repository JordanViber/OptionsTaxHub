"use client";

import { Alert, Typography } from "@mui/material";
import { Warning as WarningIcon } from "@mui/icons-material";

/**
 * Reusable disclaimer banner for tax-related content.
 *
 * Per project guidelines: "This app is for educational/simulation use only"
 * Must be displayed prominently on all pages with financial/tax guidance.
 */
export default function TaxDisclaimer() {
  return (
    <Alert
      severity="info"
      icon={<WarningIcon />}
      sx={{
        "& .MuiAlert-message": { width: "100%" },
      }}
    >
      <Typography variant="caption" sx={{ fontWeight: 500 }}>
        For educational and simulation purposes only — not financial, tax, or
        investment advice. Consult a qualified tax professional before making
        investment decisions.
      </Typography>
    </Alert>
  );
}
