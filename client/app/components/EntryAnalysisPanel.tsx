"use client";

import { useMemo, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useLeapRankMutation } from "@/lib/api";
import type {
  LeapRankCandidate,
  LeapRankResponse,
  Position,
} from "@/lib/types";
import {
  analyzeEntry,
  buildEntryContextLine,
  formatUsdCents,
  todayIso,
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

type WindowPreset = "12-24" | "12-18" | "18-24" | "custom";

const PRESET_DAYS: Record<Exclude<WindowPreset, "custom">, { from: number; to: number }> =
  {
    "12-24": { from: 365, to: 730 },
    "12-18": { from: 365, to: 547 },
    "18-24": { from: 547, to: 730 },
  };

function addDaysIso(base: string, days: number): string {
  const [year, month, day] = base.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + days);
  return todayIso(date);
}

function datesForPreset(preset: Exclude<WindowPreset, "custom">, asOf = todayIso()) {
  const span = PRESET_DAYS[preset];
  return {
    from: addDaysIso(asOf, span.from),
    to: addDaysIso(asOf, span.to),
  };
}

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

function isLeapRankFail(
  result: LeapRankResponse,
): result is Extract<LeapRankResponse, { ok: false }> {
  return result.ok === false;
}

function formatImpliedMove(rate: number, right: "call" | "put"): string {
  const pct = `${(Math.abs(rate) * 100).toFixed(1)}%`;
  if (right === "put") {
    return `${pct} annualized decline to break even`;
  }
  return `${pct} annualized to break even`;
}

function rankErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return "Could not rank LEAPs. Rankings are hidden so we do not invent prices.";
}

export default function EntryAnalysisPanel({
  positions = [],
}: Readonly<{
  positions?: Position[];
}>) {
  const leapRank = useLeapRankMutation();
  const [symbol, setSymbol] = useState("");
  const [right, setRight] = useState<OptionRight>("call");
  const [side, setSide] = useState<OptionSide>("buy");
  const [strike, setStrike] = useState("");
  const [expiration, setExpiration] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [premium, setPremium] = useState("");
  const [windowPreset, setWindowPreset] = useState<WindowPreset>("12-24");
  const [expiryFrom, setExpiryFrom] = useState(
    () => datesForPreset("12-24").from,
  );
  const [expiryTo, setExpiryTo] = useState(() => datesForPreset("12-24").to);
  const [rankResult, setRankResult] = useState<LeapRankResponse | null>(null);
  const [rankError, setRankError] = useState<string | null>(null);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);

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

  const applyPreset = (preset: WindowPreset) => {
    setWindowPreset(preset);
    if (preset === "custom") return;
    const next = datesForPreset(preset);
    setExpiryFrom(next.from);
    setExpiryTo(next.to);
  };

  const selectCandidate = (candidate: LeapRankCandidate) => {
    setSelectedRank(candidate.rank);
    setSymbol(candidate.symbol);
    setRight(candidate.right);
    setSide("buy");
    setStrike(String(candidate.strike));
    setExpiration(candidate.expiration);
    setQuantity("1");
    setPremium(String(candidate.premium));
  };

  const handleFind = async () => {
    const ticker = symbol.trim().toUpperCase();
    if (!ticker) {
      setRankResult(null);
      setSelectedRank(null);
      setRankError("Enter an underlying ticker.");
      return;
    }
    if (!expiryFrom || !expiryTo || expiryFrom > expiryTo) {
      setRankResult(null);
      setSelectedRank(null);
      setRankError("Choose a valid LEAP expiry window.");
      return;
    }
    setRankError(null);
    setSelectedRank(null);
    try {
      const result = await leapRank.mutateAsync({
        symbol: ticker,
        right,
        expiry_from: expiryFrom,
        expiry_to: expiryTo,
      });
      setRankResult(result);
      if (isLeapRankFail(result)) {
        const fail = result;
        setRankError(fail.message);
      }
    } catch (error) {
      setRankResult(null);
      setRankError(rankErrorMessage(error));
    }
  };

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

  const successfulRanks =
    rankResult && !isLeapRankFail(rankResult) ? rankResult.ranks : [];
  const showRankList = successfulRanks.length > 0;
  const showRankError = Boolean(rankError);
  const showRankIdle = !showRankList && !showRankError;

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
          <Typography sx={monoSx}>Rank vs owning the stock</Typography>
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
            value={windowPreset}
            onChange={(_, value: WindowPreset | null) => {
              if (value) applyPreset(value);
            }}
            aria-label="LEAP expiry window"
            sx={toggleGroupSx}
          >
            <ToggleButton value="12-24" data-testid="entry-rank-window-12-24">
              12–24 months
            </ToggleButton>
            <ToggleButton value="12-18" data-testid="entry-rank-window-12-18">
              12–18
            </ToggleButton>
            <ToggleButton value="18-24" data-testid="entry-rank-window-18-24">
              18–24
            </ToggleButton>
            <ToggleButton value="custom" data-testid="entry-rank-window-custom">
              Custom
            </ToggleButton>
          </ToggleButtonGroup>

          {windowPreset === "custom" ? (
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1.5}
              useFlexGap
            >
              <TextField
                label="Window from"
                type="date"
                value={expiryFrom}
                onChange={(event) => setExpiryFrom(event.target.value)}
                inputProps={{ "data-testid": "entry-rank-from" }}
                InputLabelProps={{ shrink: true }}
                size="small"
                fullWidth
              />
              <TextField
                label="Window to"
                type="date"
                value={expiryTo}
                onChange={(event) => setExpiryTo(event.target.value)}
                inputProps={{ "data-testid": "entry-rank-to" }}
                InputLabelProps={{ shrink: true }}
                size="small"
                fullWidth
              />
            </Stack>
          ) : null}

          <Button
            variant="contained"
            onClick={() => {
              void handleFind();
            }}
            disabled={leapRank.isPending}
            data-testid="entry-rank-find"
            sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
          >
            {leapRank.isPending ? "Finding…" : "Find top 3"}
          </Button>

          {showRankIdle ? (
            <Typography
              variant="body2"
              color="text.secondary"
              data-testid="entry-rank-empty"
            >
              Find the three long LEAPs that need the smallest annualized move
              to break even vs owning the stock.
            </Typography>
          ) : null}

          {showRankError ? (
            <Typography
              variant="body2"
              color="text.secondary"
              data-testid="entry-rank-error"
            >
              {rankError}
            </Typography>
          ) : null}

          {showRankList ? (
            <Stack spacing={1} data-testid="entry-rank-list">
              {successfulRanks.map((candidate) => (
                <ButtonBase
                  key={`${candidate.rank}-${candidate.contract_label}`}
                  onClick={() => selectCandidate(candidate)}
                  data-testid={`entry-rank-${candidate.rank}`}
                  sx={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    borderRadius: 2,
                    px: 1.5,
                    py: 1.25,
                    border: "1px solid",
                    borderColor:
                      selectedRank === candidate.rank
                        ? "primary.main"
                        : "divider",
                    bgcolor:
                      selectedRank === candidate.rank
                        ? "action.selected"
                        : "background.paper",
                  }}
                >
                  <Stack spacing={0.5}>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>
                      #{candidate.rank} {candidate.contract_label}
                    </Typography>
                    <Typography variant="body2">
                      {formatImpliedMove(candidate.implied_cagr, candidate.right)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {candidate.leverage.toFixed(1)}× vs 100 shares
                    </Typography>
                    <Typography variant="body2">{candidate.why_vs_stock}</Typography>
                    {candidate.why_vs_richer ? (
                      <Typography variant="body2" color="text.secondary">
                        {candidate.why_vs_richer}
                      </Typography>
                    ) : null}
                  </Stack>
                </ButtonBase>
              ))}
            </Stack>
          ) : null}
        </Stack>

        <Stack spacing={1.5}>
          <Typography sx={monoSx}>Custom / selected contract</Typography>
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
          Ranking is a live quote snapshot, not advice. Implied move to break
          even is not a forecast. Simulation only. Not a filed Form 8949, and
          not the year-close packet.
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
