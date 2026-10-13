"use client";

import { Box } from "@mui/material";

/**
 * Short label with the longer explanation on hover and focus.
 * Body copy stays primary-colored so it does not read as disabled.
 */
export default function GlossaryHint({
  label,
  explanation,
}: Readonly<{
  label: string;
  explanation: string;
}>) {
  return (
    <Box
      component="button"
      type="button"
      title={explanation}
      sx={{
        display: "inline",
        p: 0,
        border: 0,
        background: "none",
        color: "text.primary",
        font: "inherit",
        fontWeight: 600,
        textDecoration: "underline dotted",
        textUnderlineOffset: "0.18em",
        cursor: "help",
        verticalAlign: "baseline",
      }}
    >
      {label}
    </Box>
  );
}
