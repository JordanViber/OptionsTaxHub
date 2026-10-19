"use client";

import { Typography } from "@mui/material";
import { priceAsOfCopy } from "@/lib/priceBasis";
import type { PriceBasis } from "@/lib/types";

export default function PriceAsOfNote({
  basis,
  surface,
}: Readonly<{
  basis?: PriceBasis | null;
  surface: "prices" | "pnl" | "harvest" | "positions";
}>) {
  const copy = priceAsOfCopy(basis);
  if (!copy) return null;
  const csv = !basis?.live;
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      data-testid={`price-as-of-${surface}`}
      data-source={csv ? "csv" : "live"}
      sx={{ display: "block", mt: 0.5, overflowWrap: "anywhere" }}
    >
      {copy}
    </Typography>
  );
}
