"use client";

import { useMemo, useState } from "react";
import {
  Box,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import type { Position } from "@/lib/types";
import {
  analyzeEntry,
  buildEntryContextLine,
  formatUsdCents,
  type EntryAnalysisResult,
  type EntryProposal,
  type OptionRight,
  type OptionSide,
} from "@/lib/entryAnalysis";

const monoSx = {
  fontFamily: "var(--font-mono), 'IBM Plex Mono', monospace",
  fontSize: 10,
  letterSpacing: "0.18em",
  textTransform: "uppercase" as const,
  color: "text.secondary",
};

const toggleGroupSx = {
  flexWrap: "wrap" as const,
  "& .MuiToggleButton-root.Mui-selected": {
    bgcolor: "primary.main",
    color: "primary.contrastText",
    "&:hover": { bgcolor: "primary.light" },
  },
};

function parsePositiveNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

function parseQuantity(raw: string): number | null {
  return parsePositiveNumber(raw);
}

function formatPayoffMoney(value: number | null): string {
  if (value === null) return "Unlimited";
  return formatUsdCents(value);
}

function isEntryFail(
  result: EntryAnalysisResult,
): result is Extract<EntryAnalysisResult, { ok: false }> {
  return result.ok === false;
}

export default function EntryAnalysisPanel({
  positions = [],
}: Readonly<{
  positions?: Position[];
}>) {
  const [symbol, setSymbol] = useState("");
  const [right, setRight] = useState<OptionRight>("call");
  const [side, setSide] = useState<OptionSide>("buy");
  const [strike, setStrike] = useState("");
  const [expiration, setExpiration] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [premium, setPremium] = useState("");

  const proposal: EntryProposal = useMemo(
    () => ({
      symbol,
      right,
      strike: parsePositiveNumber(strike),
      expiration,
      side,
      quantity: parseQuantity(quantity),
      premium: parsePositiveNumber(premium),
    }),
    [symbol, right, strike, expiration, side, quantity, premium],
  );

  const analysis = analyzeEntry(proposal);
  const contextLine = buildEntryContextLine(proposal, positions);

  let resultsNode;
  if (isEntryFail(analysis)) {
    const fail = analysis;
    resultsNode = (
      <Typography
        variant="body2"
        color="text.secondary"
        data-testid={
          fail.reason === "incomplete" ? "entry-empty" : "entry-error"
        }
      >
        {fail.message}
      </Typography>
    );
  } else {
    const ok = analysis;
    resultsNode = (
      <Stack spacing={0.75} data-testid="entry-results">
        <ResultRow
          label="Max loss"
          value={formatPayoffMoney(ok.payoff.maxLoss)}
          testId="entry-max-loss"
        />
        <ResultRow
          label="Max gain"
          value={formatPayoffMoney(ok.payoff.maxGain)}
          testId="entry-max-gain"
        />
        <ResultRow
          label="Breakeven"
          value={formatUsdCents(ok.payoff.breakeven)}
          testId="entry-breakeven"
        />
        {ok.payoff.collateralNote ? (
          <Typography
            variant="body2"
            color="text.secondary"
            data-testid="entry-collateral"
            sx={{ pt: 0.5 }}
          >
            {ok.payoff.collateralNote}
          </Typography>
        ) : null}
        <Typography variant="caption" color="text.secondary" sx={{ pt: 0.25 }}>
          Standard 100-share multiplier. Index specs are not modeled.
        </Typography>
      </Stack>
    );
  }

  return (
    <Box
      data-testid="entry-analysis-panel"
      className="hairline"
      sx={{
        borderRadius: 3,
        bgcolor: "background.paper",
        px: { xs: 2, sm: 2.5 },
        py: 2.25,
      }}
    >
      <Stack spacing={2} data-testid="entry-analysis-stack">
        <Box>
          <Typography sx={monoSx}>What-if · single-leg</Typography>
          <Typography variant="h6" sx={{ mt: 0.5, fontWeight: 700 }}>
            Analyze a new option
          </Typography>
        </Box>

        <Stack spacing={1.5}>
          <TextField
            label="Underlying"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            inputProps={{
              "data-testid": "entry-symbol",
              autoCapitalize: "characters",
              spellCheck: false,
            }}
            size="small"
            fullWidth
            placeholder="NVDA"
          />

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            useFlexGap
            flexWrap="wrap"
          >
            <ToggleButtonGroup
              exclusive
              size="small"
              value={right}
              onChange={(_, value: OptionRight | null) => {
                if (value) setRight(value);
              }}
              aria-label="Call or put"
              sx={toggleGroupSx}
            >
              <ToggleButton value="call" data-testid="entry-right-call">
                Call
              </ToggleButton>
              <ToggleButton value="put" data-testid="entry-right-put">
                Put
              </ToggleButton>
            </ToggleButtonGroup>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={side}
              onChange={(_, value: OptionSide | null) => {
                if (value) setSide(value);
              }}
              aria-label="Buy or sell"
              sx={toggleGroupSx}
            >
              <ToggleButton value="buy" data-testid="entry-side-buy">
                Buy
              </ToggleButton>
              <ToggleButton value="sell" data-testid="entry-side-sell">
                Sell
              </ToggleButton>
            </ToggleButtonGroup>
          </Stack>

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            useFlexGap
          >
            <TextField
              label="Strike"
              value={strike}
              onChange={(event) => setStrike(event.target.value)}
              inputProps={{
                "data-testid": "entry-strike",
                inputMode: "decimal",
              }}
              size="small"
              fullWidth
            />
            <TextField
              label="Expiration"
              type="date"
              value={expiration}
              onChange={(event) => setExpiration(event.target.value)}
              inputProps={{ "data-testid": "entry-expiration" }}
              InputLabelProps={{ shrink: true }}
              size="small"
              fullWidth
            />
          </Stack>

          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            useFlexGap
          >
            <TextField
              label="Qty (contracts)"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              inputProps={{
                "data-testid": "entry-quantity",
                inputMode: "numeric",
              }}
              size="small"
              fullWidth
            />
            <TextField
              label="Premium (per share)"
              value={premium}
              onChange={(event) => setPremium(event.target.value)}
              inputProps={{
                "data-testid": "entry-premium",
                inputMode: "decimal",
              }}
              size="small"
              fullWidth
            />
          </Stack>
        </Stack>

        {resultsNode}

        {contextLine ? (
          <Typography variant="body2" data-testid="entry-context">
            {contextLine}
          </Typography>
        ) : null}

        <Typography variant="caption" color="text.secondary">
          Simulation only. Not advice, not a filed Form 8949, and not the
          year-close packet.
        </Typography>
      </Stack>
    </Box>
  );
}

function ResultRow({
  label,
  value,
  testId,
}: Readonly<{
  label: string;
  value: string;
  testId: string;
}>) {
  return (
    <Stack
      direction="row"
      justifyContent="space-between"
      spacing={2}
      sx={{ minWidth: 0 }}
    >
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        data-testid={testId}
        sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
