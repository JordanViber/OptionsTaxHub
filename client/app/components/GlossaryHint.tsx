"use client";

import { useId, useState } from "react";
import { Box } from "@mui/material";

/**
 * Short label that opens its explanation on click.
 * A title tooltip is not enough: guests on a phone never see it.
 * Body copy stays primary-colored so it does not read as disabled.
 */
export default function GlossaryHint({
  label,
  explanation,
}: Readonly<{
  label: string;
  explanation: string;
}>) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <Box component="span" sx={{ display: "inline" }}>
      <Box
        component="button"
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        title={explanation}
        onClick={() => setOpen((current) => !current)}
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
          cursor: "pointer",
          verticalAlign: "baseline",
        }}
      >
        {label}
      </Box>
      {open ? (
        <Box
          component="span"
          id={panelId}
          role="note"
          data-testid="glossary-hint-panel"
          sx={{
            display: "block",
            mt: 0.75,
            fontWeight: 400,
            lineHeight: 1.45,
          }}
        >
          {explanation}
        </Box>
      ) : null}
    </Box>
  );
}
