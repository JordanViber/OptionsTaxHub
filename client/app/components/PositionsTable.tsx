"use client";

import { Fragment, useCallback, useMemo, useState } from "react";
import {
  Box,
  Chip,
  Collapse,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { DataGrid, type GridColDef, type GridRowParams } from "@mui/x-data-grid";
import {
  ExpandLess as CollapseIcon,
  ExpandMore as ExpandIcon,
  Warning as WarnIcon,
} from "@mui/icons-material";
import type { Position, TaxLot, WashSaleFlag } from "@/lib/types";

/**
 * Convert raw holding-period days to a human-readable label.
 * Examples: 12 → "12d", 90 → "3mo", 400 → "1yr 1mo"
 */
function formatHoldingPeriod(days: number): string {
  if (days < 30) return `${days}d`;
  const years = Math.floor(days / 365);
  const months = Math.floor((days % 365) / 30);
  if (years === 0) return `${months}mo`;
  return months > 0 ? `${years}yr ${months}mo` : `${years}yr`;
}

interface PositionsTableProps {
  positions: Position[];
  /** Realized wash-sale flags from analyze — mapped onto replacement lots. */
  washSaleFlags?: WashSaleFlag[];
}

function getPositionRowId(row: Position): string {
  return row.position_id ?? `${row.symbol}:${row.asset_type}`;
}

/**
 * Format a number as USD currency.
 */
function formatCurrency(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);
}

function formatAcquiredDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  const parsed = match
    ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    : new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}


const WASH_SALE_WINDOW_DAYS = 30;

function dateKey(value: string | null | undefined): string {
  if (!value) return "";
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return match ? match[1] : value;
}

function shiftIsoDate(iso: string, days: number): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return null;
  const shifted = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  shifted.setDate(shifted.getDate() + days);
  const year = shifted.getFullYear();
  const month = String(shifted.getMonth() + 1).padStart(2, "0");
  const day = String(shifted.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

interface LotWashDetails {
  disallowedLoss: number;
  basisBump: number;
  adjustedCostBasis: number | null;
  saleDate: string | null;
  repurchaseDate: string | null;
  windowStart: string | null;
  windowEnd: string | null;
}

/**
 * Map engine wash-sale flags onto a replacement lot.
 *
 * The engine adds `wash_sale_disallowed` to the repurchase lot (same symbol +
 * purchase_date === repurchase_date). Window dates are sale_date ± 30 days.
 */
function getLotWashDetails(
  lot: TaxLot,
  flags: WashSaleFlag[],
): LotWashDetails | null {
  const matching = flags.filter(
    (flag) =>
      flag.symbol === lot.symbol &&
      dateKey(flag.repurchase_date) === dateKey(lot.purchase_date) &&
      Math.abs(flag.disallowed_loss) >= 0.01,
  );
  const lotDisallowed = lot.wash_sale_disallowed ?? 0;
  if (matching.length === 0 && lotDisallowed < 0.01) {
    return null;
  }

  const primary = matching[0];
  const disallowedLoss =
    matching.length > 0
      ? matching.reduce((sum, flag) => sum + flag.disallowed_loss, 0)
      : lotDisallowed;
  const saleDate = primary?.sale_date ?? null;
  const repurchaseDate = primary?.repurchase_date ?? lot.purchase_date;
  const windowStart = saleDate
    ? shiftIsoDate(saleDate, -WASH_SALE_WINDOW_DAYS)
    : null;
  const windowEnd = saleDate
    ? shiftIsoDate(saleDate, WASH_SALE_WINDOW_DAYS)
    : null;

  return {
    disallowedLoss,
    basisBump: disallowedLoss,
    adjustedCostBasis: primary?.adjusted_cost_basis ?? lot.total_cost_basis,
    saleDate,
    repurchaseDate,
    windowStart,
    windowEnd,
  };
}

/**
 * Format P&L with color and sign.
 */
function PnlCell({
  value,
  pct,
}: Readonly<{ value: number | null; pct: number | null }>) {
  if (value == null) return <Typography variant="body2">—</Typography>;
  const color = value >= 0 ? "success.main" : "error.main";
  const sign = value >= 0 ? "+" : "";
  return (
    <Box sx={{ display: "flex", alignItems: "baseline", gap: 0.5 }}>
      <Typography
        variant="body2"
        sx={{ color, fontWeight: 600, fontSize: "0.8rem" }}
      >
        {sign}
        {formatCurrency(value)}
      </Typography>
      {pct != null && (
        <Typography variant="caption" sx={{ color, fontSize: "0.7rem" }}>
          {`(${sign}${pct.toFixed(1)}%)`}
        </Typography>
      )}
    </Box>
  );
}

function TermChip({ isLong }: Readonly<{ isLong: boolean | null }>) {
  if (isLong == null) {
    return (
      <Typography variant="body2" color="text.secondary">
        —
      </Typography>
    );
  }
  return (
    <Tooltip
      title={
        isLong
          ? "Long-Term: held > 1 year (lower capital gains tax rate)"
          : "Short-Term: held ≤ 1 year (taxed as ordinary income)"
      }
    >
      <Chip
        label={isLong ? "LT" : "ST"}
        size="small"
        color={isLong ? "success" : "warning"}
        sx={{ height: 18, fontSize: "0.65rem" }}
      />
    </Tooltip>
  );
}

export function TaxLotsPanel({
  position,
  washSaleFlags = [],
}: Readonly<{ position: Position; washSaleFlags?: WashSaleFlag[] }>) {
  const lots: TaxLot[] = position.tax_lots ?? [];

  return (
    <Box
      data-testid={`tax-lots-panel-${position.symbol}`}
      sx={{
        mt: 1.5,
        p: 1.5,
        border: 1,
        borderColor: "divider",
        borderRadius: 2,
        bgcolor: "background.default",
      }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        Tax lots — {position.display_label ?? position.symbol}
      </Typography>
      {lots.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No open tax lots are recorded for this position.
        </Typography>
      ) : (
        <Table size="small" aria-label={`Tax lots for ${position.symbol}`}>
          <TableHead>
            <TableRow>
              <TableCell>Acquired</TableCell>
              <TableCell align="right">Qty</TableCell>
              <TableCell align="right">Cost / share</TableCell>
              <TableCell align="right">Cost basis</TableCell>
              <TableCell align="right">Current</TableCell>
              <TableCell>Term</TableCell>
              <TableCell align="right">Wash-sale adj.</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {lots.map((lot, index) => {
              const lotCurrent =
                lot.current_price == null
                  ? null
                  : lot.current_price * lot.quantity;
              const wash = getLotWashDetails(lot, washSaleFlags);
              const hasWashAdj = wash != null;
              return (
                <Fragment
                  key={`${lot.symbol}-${lot.purchase_date}-${index}`}
                >
                  <TableRow
                    data-testid={`tax-lot-${position.symbol}-${index}`}
                  >
                    <TableCell>{formatAcquiredDate(lot.purchase_date)}</TableCell>
                    <TableCell align="right">{lot.quantity}</TableCell>
                    <TableCell align="right">
                      {formatCurrency(lot.cost_basis_per_share)}
                    </TableCell>
                    <TableCell align="right">
                      {formatCurrency(lot.total_cost_basis)}
                    </TableCell>
                    <TableCell align="right">
                      <Box>
                        <Typography variant="body2">
                          {formatCurrency(lotCurrent)}
                        </Typography>
                        {lot.current_price != null && (
                          <Typography variant="caption" color="text.secondary">
                            {formatCurrency(lot.current_price)}/sh
                          </Typography>
                        )}
                      </Box>
                    </TableCell>
                    <TableCell>
                      <TermChip isLong={lot.is_long_term} />
                    </TableCell>
                    <TableCell align="right">
                      {hasWashAdj ? (
                        <Tooltip title="Wash-sale disallowed loss added to this lot's cost basis">
                          <Typography
                            variant="body2"
                            sx={{ color: "warning.dark", fontWeight: 600 }}
                          >
                            +{formatCurrency(wash.basisBump)}
                          </Typography>
                        </Tooltip>
                      ) : (
                        <Typography variant="body2" color="text.secondary">
                          —
                        </Typography>
                      )}
                    </TableCell>
                  </TableRow>
                  {wash && (
                    <TableRow
                      data-testid={`tax-lot-wash-${position.symbol}-${index}`}
                    >
                      <TableCell
                        colSpan={7}
                        sx={{ pt: 0, pb: 1.25, borderBottomColor: "divider" }}
                      >
                        <Box
                          sx={{
                            display: "flex",
                            flexWrap: "wrap",
                            alignItems: "center",
                            gap: 1,
                            px: 0.5,
                            py: 0.75,
                            borderRadius: 1,
                            bgcolor: "warning.light",
                          }}
                        >
                          <Chip
                            icon={<WarnIcon sx={{ fontSize: 14 }} />}
                            label="Wash sale"
                            size="small"
                            color="warning"
                            sx={{ height: 22 }}
                          />
                          <Typography variant="caption" sx={{ fontWeight: 600 }}>
                            Disallowed loss {formatCurrency(wash.disallowedLoss)}
                          </Typography>
                          <Typography variant="caption" sx={{ fontWeight: 600 }}>
                            Replacement-lot basis bump +{formatCurrency(wash.basisBump)}
                          </Typography>
                          {wash.windowStart && wash.windowEnd && (
                            <Typography variant="caption">
                              30-day window{" "}
                              {formatAcquiredDate(wash.windowStart)} –{" "}
                              {formatAcquiredDate(wash.windowEnd)}
                            </Typography>
                          )}
                          {wash.saleDate && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              Sold {formatAcquiredDate(wash.saleDate)}
                              {wash.repurchaseDate
                                ? ` · replaced ${formatAcquiredDate(wash.repurchaseDate)}`
                                : ""}
                            </Typography>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}
    </Box>
  );
}

function buildColumns(
  expandedId: string | null,
  onToggle: (position: Position) => void,
): GridColDef<Position>[] {
  return [
    {
      field: "symbol",
      headerName: "Position",
      width: 240,
      renderCell: (params) => {
        const lotCount = params.row.tax_lots?.length ?? 0;
        const rowId = getPositionRowId(params.row);
        const isExpanded = expandedId === rowId;
        return (
          <Box sx={{ minWidth: 0, display: "flex", alignItems: "center", gap: 0.75 }}>
            {lotCount > 0 ? (
              isExpanded ? (
                <CollapseIcon sx={{ fontSize: 18, color: "text.secondary" }} />
              ) : (
                <ExpandIcon sx={{ fontSize: 18, color: "text.secondary" }} />
              )
            ) : null}
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>
                {params.row.display_label ?? params.value}
              </Typography>
              {params.row.display_label &&
                params.row.display_label !== params.value && (
                  <Typography variant="caption" color="text.secondary" noWrap>
                    {params.value}
                  </Typography>
                )}
              {lotCount > 0 && (
                <Chip
                  label={`${lotCount} lot${lotCount === 1 ? "" : "s"}`}
                  size="small"
                  variant="outlined"
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggle(params.row);
                  }}
                  sx={{ height: 18, fontSize: "0.65rem", mt: 0.25 }}
                />
              )}
              {params.row.manual_review_required &&
                params.row.manual_review_reason && (
                  <Tooltip title={params.row.manual_review_reason}>
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.5,
                        minWidth: 0,
                      }}
                    >
                      <WarnIcon sx={{ color: "warning.main", fontSize: 14 }} />
                      <Typography variant="caption" color="warning.dark" noWrap>
                        Manual review
                      </Typography>
                    </Box>
                  </Tooltip>
                )}
            </Box>
          </Box>
        );
      },
    },
    {
      field: "quantity",
      headerName: "Qty",
      width: 80,
      type: "number",
    },
    {
      field: "avg_cost_basis",
      headerName: "Avg Cost",
      width: 110,
      type: "number",
      renderCell: (params) => (
        <Typography variant="body2">
          {formatCurrency(params.value as number)}
        </Typography>
      ),
    },
    {
      field: "current_price",
      headerName: "Price",
      width: 110,
      type: "number",
      renderCell: (params) => (
        <Typography variant="body2">
          {formatCurrency(params.value as number | null)}
        </Typography>
      ),
    },
    {
      field: "market_value",
      headerName: "Mkt Value",
      width: 120,
      type: "number",
      renderCell: (params) => (
        <Typography variant="body2">
          {formatCurrency(params.value as number | null)}
        </Typography>
      ),
    },
    {
      field: "unrealized_pnl",
      headerName: "Unrealized P&L",
      width: 140,
      type: "number",
      renderCell: (params) => (
        <PnlCell
          value={params.value as number | null}
          pct={params.row.unrealized_pnl_pct}
        />
      ),
    },
    {
      field: "holding_period_days",
      headerName: "Holding",
      width: 100,
      renderCell: (params) => {
        const days = params.value as number | null;
        if (days == null) return "—";
        return (
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Typography variant="body2" sx={{ fontSize: "0.8rem" }}>
              {formatHoldingPeriod(days)}
            </Typography>
            <TermChip isLong={params.row.is_long_term} />
          </Box>
        );
      },
    },
    {
      field: "wash_sale_risk",
      headerName: "Wash Sale",
      width: 100,
      renderCell: (params) => {
        if (!params.value) return null;
        return (
          <Tooltip title="Wash-Sale Risk: selling and repurchasing within 30 days may disallow the loss deduction">
            <Chip
              icon={<WarnIcon sx={{ fontSize: 14 }} />}
              label="Risk"
              size="small"
              color="warning"
              sx={{ height: 22 }}
            />
          </Tooltip>
        );
      },
    },
    {
      field: "asset_type",
      headerName: "Type",
      width: 80,
      renderCell: (params) => (
        <Tooltip
          title={
            params.value === "option" ? "Options contract" : "Stock position"
          }
        >
          <Chip
            label={params.value === "option" ? "OPT" : "STK"}
            size="small"
            variant="outlined"
            sx={{ height: 20, fontSize: "0.65rem" }}
          />
        </Tooltip>
      ),
    },
  ];
}

/**
 * Positions table — MUI DataGrid showing all portfolio positions.
 *
 * Displays symbol, quantity, cost basis, current price, P&L, holding period,
 * short/long-term badge, wash-sale risk, and asset type.
 * Click a row (or its lot chip) to inspect individual tax lots.
 * Rows with losses/gains use dark ink fills so cream type stays readable.
 */
export default function PositionsTable({
  positions,
  washSaleFlags = [],
}: Readonly<PositionsTableProps>) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const togglePosition = useCallback((position: Position) => {
    const rowId = getPositionRowId(position);
    setExpandedId((current) => (current === rowId ? null : rowId));
  }, []);

  const columns = useMemo(
    () => buildColumns(expandedId, togglePosition),
    [expandedId, togglePosition],
  );

  const expandedPosition = positions.find(
    (position) => getPositionRowId(position) === expandedId,
  );

  const handleRowClick = (params: GridRowParams<Position>) => {
    togglePosition(params.row);
  };

  return (
    <Box sx={{ width: "100%" }} data-testid="positions-table-wrap">
      <DataGrid
        rows={positions}
        columns={columns}
        getRowId={(row) => getPositionRowId(row)}
        onRowClick={handleRowClick}
        initialState={{
          sorting: {
            sortModel: [{ field: "unrealized_pnl", sort: "asc" }],
          },
        }}
        rowHeight={48}
        columnHeaderHeight={40}
        pageSizeOptions={[10, 25, 50]}
        disableRowSelectionOnClick
        autoHeight
        sx={{
          fontSize: "0.8rem",
          "& .MuiDataGrid-cell": {
            py: 0.5,
          },
          "& .MuiDataGrid-row": {
            cursor: "pointer",
            "&:hover": { backgroundColor: "action.hover" },
          },
          "& .loss-row": {
            backgroundColor: "error.dark",
          },
          "& .gain-row": {
            backgroundColor: "success.dark",
          },
          border: 1,
          borderColor: "divider",
          borderRadius: 2,
        }}
        getRowClassName={(params) => {
          const pnl = params.row.unrealized_pnl;
          if (pnl != null && pnl < 0) return "loss-row";
          if (pnl != null && pnl > 0) return "gain-row";
          return "";
        }}
      />
      <Collapse in={Boolean(expandedPosition)} unmountOnExit>
        {expandedPosition && (
          <TaxLotsPanel
            position={expandedPosition}
            washSaleFlags={washSaleFlags}
          />
        )}
      </Collapse>
    </Box>
  );
}
