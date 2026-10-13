"use client";

import { useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { Close as CloseIcon } from "@mui/icons-material";
import type { WashClosedLot, WashSaleChain, WashSaleHop } from "@/lib/types";
import { formatUsd } from "@/lib/supplemental1099";

export const WASH_SALE_CHAIN_DISCLAIMER =
  "Educational only — not tax advice. This explains wash sales in this Robinhood export and the 1099-B you imported for this portfolio. It does not see other brokers, other accounts, or IRAs. A wash outside this file will not show up here.";

const CARRYOVER_MISSING = "Holding-period carryover is not in this export.";

export function washChainsVisible(
  analysis: {
    sample_run?: boolean;
    wash_sale_chains?: readonly unknown[] | null;
  },
  packetUnlocked: boolean,
): boolean {
  const count = analysis.wash_sale_chains?.length ?? 0;
  if (count === 0) return false;
  return Boolean(analysis.sample_run) || packetUnlocked;
}

export function formatChainDollars(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  if (Math.abs(rounded - Math.round(rounded)) < 0.001) {
    return `$${Math.round(rounded).toLocaleString("en-US")}`;
  }
  return rounded.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}

export function washChainStatusLabel(
  status: string,
  difference: number | null | undefined,
): string {
  if (status === "matches") return "Matches";
  if (status === "differs") {
    return `Differs by ${formatChainDollars(difference ?? 0)}`;
  }
  if (status === "not_on_1099") return "Not on your 1099";
  if (status === "on_1099_only") return "On your 1099 only";
  if (status === "not_compared") return "Not compared";
  return status;
}

function formatChainDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  const parsed = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatQty(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return String(Math.round(value * 10000) / 10000);
}

function chipColor(
  status: string,
): "success" | "warning" | "default" | "info" {
  if (status === "matches") return "success";
  if (status === "differs") return "warning";
  if (status === "on_1099_only") return "info";
  return "default";
}

function Fact({
  label,
  value,
}: Readonly<{ label: string; value: string }>) {
  return (
    <Box sx={{ minWidth: 0, maxWidth: "100%" }}>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{
          fontWeight: 600,
          whiteSpace: "normal",
          overflowWrap: "anywhere",
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}

function isIsoDate(value: string | null | undefined): value is string {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}/.test(value));
}

const BASIS_SPLIT_CENTS = 0.02;

export function basisSplitIsIllustrative(hop: WashSaleHop): boolean {
  if (hop.engine_adjusted_cost_basis == null) return false;
  const split = hop.replacement_buys
    .filter((buy) => buy.allocated_quantity > 0)
    .reduce((sum, buy) => sum + buy.explained_basis, 0);
  return Math.abs(split - hop.engine_adjusted_cost_basis) > BASIS_SPLIT_CENTS;
}

function differingClosedLots(hop: WashSaleHop): WashClosedLot[] | null {
  const lots = hop.closed_lots ?? [];
  if (lots.length <= 1) return null;
  const keys = lots.map((lot) =>
    isIsoDate(lot.purchase_date) ? lot.purchase_date.slice(0, 10) : "",
  );
  const shared = keys.every((key) => key !== "" && key === keys[0]);
  if (shared) return null;
  return lots;
}

function carryoverCopy(hop: WashSaleHop): string {
  if (isIsoDate(hop.tacked_acquisition_date) && hop.carryover_days != null) {
    return `Holding period starts ${formatChainDate(hop.tacked_acquisition_date)} for these replacement shares (${hop.carryover_days} days carried from the sold lot).`;
  }
  return CARRYOVER_MISSING;
}

function QuietHopDetails({
  hop,
  instrument,
}: Readonly<{ hop: WashSaleHop; instrument: string }>) {
  return (
    <Stack
      spacing={1.25}
      data-testid={`wash-sale-hop-${hop.hop_id}`}
      sx={{ minWidth: 0, maxWidth: "100%" }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        sx={{ alignItems: { sm: "center" } }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          This sale
        </Typography>
        <Chip
          size="small"
          color={chipColor(hop.reconcile_status)}
          label={washChainStatusLabel(hop.reconcile_status, hop.difference)}
        />
      </Stack>
      <Typography
        variant="body2"
        sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
      >
        {hop.plain_english}
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 1.25,
          minWidth: 0,
        }}
      >
        <Fact label="Symbol" value={hop.symbol} />
        <Fact label="Sale date" value={formatChainDate(hop.sale_date)} />
        <Fact label="Instrument" value={instrument} />
      </Box>
    </Stack>
  );
}

function SparseHopDetails({ hop }: Readonly<{ hop: WashSaleHop }>) {
  return (
    <Stack
      spacing={1.25}
      data-testid={`wash-sale-hop-${hop.hop_id}`}
      sx={{ minWidth: 0, maxWidth: "100%" }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        sx={{ alignItems: { sm: "center" } }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          This sale
        </Typography>
        <Chip
          size="small"
          color={chipColor(hop.reconcile_status)}
          label={washChainStatusLabel(hop.reconcile_status, hop.difference)}
        />
      </Stack>
      <Typography
        variant="body2"
        sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
      >
        {hop.plain_english}
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 1.25,
          minWidth: 0,
        }}
      >
        <Fact label="Symbol" value={hop.symbol} />
        <Fact label="Sale date" value={formatChainDate(hop.sale_date)} />
        {hop.form_1099_wash != null && (
          <Fact
            label="1099 wash adjustment"
            value={formatUsd(hop.form_1099_wash)}
          />
        )}
      </Box>
    </Stack>
  );
}

function HopDetails({ hop }: Readonly<{ hop: WashSaleHop }>) {
  if (hop.asset_type === "option") {
    return <QuietHopDetails hop={hop} instrument="Option" />;
  }
  if (hop.asset_type === "unclassified") {
    return <QuietHopDetails hop={hop} instrument="Unclassified" />;
  }
  if (
    hop.not_compared_reason === "overlap" ||
    hop.not_compared_reason === "unassigned" ||
    hop.not_compared_reason === "unidentified" ||
    hop.not_compared_reason === "unknown"
  ) {
    return <SparseHopDetails hop={hop} />;
  }
  const perLot = differingClosedLots(hop);
  const noAllocatedBuy = hop.replacement_buys.every(
    (buy) => buy.allocated_quantity === 0,
  );
  // A 1099-only sale with no allocated replacement has nothing to tack.
  const hideUnallocated1099Only =
    hop.reconcile_status === "on_1099_only" && noAllocatedBuy;
  return (
    <Stack
      spacing={1.25}
      data-testid={`wash-sale-hop-${hop.hop_id}`}
      sx={{ minWidth: 0, maxWidth: "100%" }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        sx={{ alignItems: { sm: "center" } }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          This sale
        </Typography>
        <Chip
          size="small"
          color={chipColor(hop.reconcile_status)}
          label={washChainStatusLabel(hop.reconcile_status, hop.difference)}
        />
      </Stack>
      <Typography
        variant="body2"
        sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
      >
        {hop.plain_english}
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 1.25,
          minWidth: 0,
        }}
      >
        <Fact label="Symbol" value={hop.symbol} />
        <Fact label="Sale date" value={formatChainDate(hop.sale_date)} />
        <Fact label="Quantity" value={formatQty(hop.sale_quantity)} />
        {hop.proceeds != null && (
          <Fact label="Proceeds" value={formatUsd(hop.proceeds)} />
        )}
        {hop.cost_basis != null && (
          <Fact label="Basis" value={formatUsd(hop.cost_basis)} />
        )}
        <Fact label="Loss" value={formatUsd(hop.loss)} />
        <Fact label="Disallowed" value={formatUsd(hop.disallowed_loss)} />
        {hop.form_1099_wash != null && (
          <Fact
            label="1099 wash adjustment"
            value={formatUsd(hop.form_1099_wash)}
          />
        )}
        {hop.engine_adjusted_cost_basis != null && (
          <Fact
            label="Engine adjusted basis (source of truth)"
            value={formatUsd(hop.engine_adjusted_cost_basis)}
          />
        )}
      </Box>
      {!hideUnallocated1099Only && (
        <>
          <Typography variant="caption" color="text.secondary">
            Replacement buys
          </Typography>
          {noAllocatedBuy ? (
            <Typography variant="body2">
              No replacement buy in this Robinhood export.
            </Typography>
          ) : (
            hop.replacement_buys.map((buy, index) =>
              buy.allocated_quantity === 0 ? null : (
                <Typography
                  key={`${hop.hop_id}-${index}-${buy.activity_date}-${buy.quantity}`}
                  variant="body2"
                  sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
                >
                  {formatChainDate(buy.activity_date)} · {formatQty(buy.allocated_quantity)}
                  {` · explained basis ${formatUsd(buy.explained_basis)}`}
                </Typography>
              ),
            )
          )}
        </>
      )}
      {basisSplitIsIllustrative(hop) && (
        <Typography variant="caption" color="text.secondary">
          Per-buy basis split is illustrative.
        </Typography>
      )}
      {!hideUnallocated1099Only &&
        (perLot ? (
          perLot.map((lot, index) => (
            <Typography
              key={`${hop.hop_id}-lot-${index}`}
              variant="body2"
              data-testid={`wash-sale-closed-lot-${hop.hop_id}-${index}`}
              sx={{ overflowWrap: "anywhere" }}
            >
              {formatQty(lot.quantity)} acquired {formatChainDate(lot.purchase_date)}
            </Typography>
          ))
        ) : (
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {carryoverCopy(hop)}
          </Typography>
        ))}
    </Stack>
  );
}

export default function WashSaleChainPanel({
  chains,
}: Readonly<{ chains: WashSaleChain[] }>) {
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down("sm"), { noSsr: true });
  const [openId, setOpenId] = useState<string | null>(null);
  const openChain = chains.find((chain) => chain.chain_id === openId) ?? null;

  if (chains.length === 0) return null;

  return (
    <Box
      data-testid="wash-sale-chain-card"
      sx={{
        border: "1px solid",
        borderColor: "warning.light",
        borderRadius: 2,
        px: 2,
        py: 1.75,
        maxWidth: "100%",
        boxSizing: "border-box",
        minWidth: 0,
      }}
    >
      <Stack spacing={1.25}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            Wash-sale chains
          </Typography>
          <Typography variant="body2" color="text.secondary">
            From this Robinhood export and the 1099-B uploaded with it.
          </Typography>
        </Box>
        {chains.map((chain) => (
          <Box
            key={chain.chain_id}
            sx={{
              borderTop: "1px solid",
              borderColor: "divider",
              pt: 1,
              minWidth: 0,
              maxWidth: "100%",
            }}
          >
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              sx={{
                alignItems: { xs: "flex-start", sm: "center" },
                mb: 0.5,
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                {chain.symbol}
              </Typography>
              <Chip
                size="small"
                color={chipColor(chain.reconcile_status)}
                label={washChainStatusLabel(
                  chain.reconcile_status,
                  chain.difference,
                )}
              />
            </Stack>
            <Typography
              variant="body2"
              sx={{
                whiteSpace: "normal",
                overflowWrap: "anywhere",
                mb: 0.75,
              }}
            >
              {chain.plain_english}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              data-testid={`wash-sale-chain-open-${chain.chain_id}`}
              aria-label={`Open chain ${chain.symbol} ${formatChainDate(chain.hops[0]?.sale_date)}`}
              onClick={() => setOpenId(chain.chain_id)}
            >
              Open chain
            </Button>
          </Box>
        ))}
        <Typography
          variant="caption"
          color="text.secondary"
          data-testid="wash-sale-chain-disclaimer"
          sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
        >
          {WASH_SALE_CHAIN_DISCLAIMER}
        </Typography>
      </Stack>
      <Dialog
        open={openChain != null}
        onClose={() => setOpenId(null)}
        fullScreen={compact}
        fullWidth
        maxWidth="sm"
        data-testid="wash-sale-chain-dialog"
      >
        <DialogTitle
          sx={{
            pr: 6,
            whiteSpace: "normal",
            overflowWrap: "anywhere",
          }}
        >
          {openChain ? `${openChain.symbol} wash-sale chain` : "Wash-sale chain"}
          <IconButton
            aria-label="Close wash-sale chain"
            onClick={() => setOpenId(null)}
            sx={{ position: "absolute", right: 8, top: 8 }}
          >
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent sx={{ minWidth: 0, maxWidth: "100%" }}>
          {openChain && (
            <Stack spacing={2} sx={{ pb: 1 }}>
              {openChain.hops.length === 0 ? (
                <Typography variant="body2">{openChain.plain_english}</Typography>
              ) : (
                openChain.hops.map((hop) => (
                  <HopDetails key={hop.hop_id} hop={hop} />
                ))
              )}
              <Typography
                variant="caption"
                color="text.secondary"
                data-testid="wash-sale-chain-dialog-disclaimer"
                sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
              >
                {WASH_SALE_CHAIN_DISCLAIMER}
              </Typography>
            </Stack>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
