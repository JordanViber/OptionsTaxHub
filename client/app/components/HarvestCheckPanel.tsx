"use client";

import { useState } from "react";
import {
  Box,
  Button,
  Chip,
  Stack,
  Typography,
} from "@mui/material";
import type { HarvestCheck } from "@/lib/types";
import {
  HARVEST_TAX_PRO_COPY,
  formatHarvestDate,
  formatHarvestMoney,
  harvestBuyKindLabel,
  harvestCountLine,
  harvestPromptBlocked,
  harvestRowShowsDates,
} from "@/lib/harvestCheck";

function TriggerList({ check }: Readonly<{ check: HarvestCheck }>) {
  return (
    <Stack
      component="ul"
      spacing={0.75}
      data-testid={`harvest-check-buys-${check.symbol}`}
      sx={{ m: 0, mt: 1, pl: 2.5 }}
    >
      {check.triggers.map((trigger) => (
        <Box
          component="li"
          key={`${trigger.activity_date}-${trigger.kind}-${trigger.quantity}`}
          sx={{ pl: 0.5 }}
        >
          <Typography variant="body2">
            {formatHarvestDate(trigger.activity_date)}
            {" · "}
            {harvestBuyKindLabel(trigger.kind)}
            {trigger.account_name ? ` · ${trigger.account_name}` : ""}
            {" · "}
            {formatHarvestMoney(trigger.disallowed_loss)} of the loss
          </Typography>
          {trigger.permanent_disallowance ? (
            <Chip
              label="Permanent disallowance"
              size="small"
              color="warning"
              variant="outlined"
              sx={{ mt: 0.5, height: 22 }}
            />
          ) : null}
        </Box>
      ))}
    </Stack>
  );
}

function HarvestCheckRow({
  check,
  detailOpen,
}: Readonly<{ check: HarvestCheck; detailOpen: boolean }>) {
  const [open, setOpen] = useState(false);
  const canExpand = detailOpen && check.triggers.length > 0;
  const text = detailOpen && check.line ? check.line : harvestCountLine(check.trigger_count);
  const showDates = detailOpen && harvestRowShowsDates(check);

  return (
    <Box
      data-testid={`harvest-check-${check.symbol}`}
      sx={{
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        sx={{
          alignItems: { sm: "baseline" },
          justifyContent: "space-between",
        }}
      >
        <Typography variant="subtitle1" sx={{ fontWeight: 700, minWidth: 72 }}>
          {check.symbol}
        </Typography>
        <Typography variant="body2" sx={{ flex: 1 }}>
          {text}
        </Typography>
      </Stack>
      {showDates ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Earliest clean sell date {formatHarvestDate(check.earliest_clean_sell_date)}.
          {" "}
          Safe re-buy date {formatHarvestDate(check.safe_rebuy_date)}.
        </Typography>
      ) : null}
      {canExpand ? (
        <Button
          size="small"
          data-testid={`harvest-check-open-${check.symbol}`}
          onClick={() => setOpen((value) => !value)}
          sx={{ mt: 0.5, px: 0, textTransform: "none" }}
        >
          {open ? "Hide replacement buys" : "Show replacement buys"}
        </Button>
      ) : null}
      {open && canExpand ? <TriggerList check={check} /> : null}
    </Box>
  );
}

export default function HarvestCheckPanel({
  checks,
  detailOpen,
}: Readonly<{ checks: HarvestCheck[]; detailOpen: boolean }>) {
  const visible = checks.filter((check) => !harvestPromptBlocked(check.symbol));
  if (visible.length === 0) {
    return null;
  }
  return (
    <Box data-testid="harvest-check" sx={{ mb: 2 }}>
      <Typography variant="h6" sx={{ fontWeight: 700 }}>
        Year-end harvest check
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
        Replacement buys in the 30 days either side of a sale. {HARVEST_TAX_PRO_COPY}
      </Typography>
      {visible.map((check) => (
        <HarvestCheckRow key={check.symbol} check={check} detailOpen={detailOpen} />
      ))}
    </Box>
  );
}
