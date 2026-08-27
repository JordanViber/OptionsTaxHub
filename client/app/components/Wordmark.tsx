"use client";

import { Box, Typography } from "@mui/material";
import NextLink from "next/link";

export default function Wordmark({
  href = "/",
}: Readonly<{ href?: string }>) {
  return (
    <Box
      component={NextLink}
      href={href}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 1.25,
        textDecoration: "none",
        color: "text.primary",
      }}
    >
      <Box
        aria-hidden
        sx={{
          width: 22,
          height: 22,
          borderRadius: "6px",
          background:
            "linear-gradient(135deg, #d8d2c6 0%, #7a9e84 100%)",
          boxShadow: "0 0 0 1px rgb(236 234 228 / 0.12)",
        }}
      />
      <Typography
        component="span"
        sx={{
          fontWeight: 700,
          letterSpacing: "-0.03em",
          fontSize: { xs: "1rem", sm: "1.1rem" },
        }}
      >
        OptionsTaxHub
      </Typography>
    </Box>
  );
}
