"use client";

import { Box, Chip, Tooltip, Typography } from "@mui/material";
import { DataGrid, type GridColDef } from "@mui/x-data-grid";
import { Warning as WarnIcon } from "@mui/icons-material";
import type { Position } from "@/lib/types";

interface PositionsTableProps {
  positions: Position[];
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
      <Typography variant="body2" sx={{ color, fontWeight: 600, fontSize: "0.8rem" }}>
        {sign}
        {formatCurrency(value)}
      </Typography>
      {pct != null && (
        <Typography variant="caption" sx={{ color, fontSize: "0.7rem" }}>
          ({sign}{pct.toFixed(1)}%)
        </Typography>
      )}
    </Box>
  );
}

const columns: GridColDef<Position>[] = [
  {
    field: "symbol",
    headerName: "Symbol",
    width: 100,
    renderCell: (params) => (
      <Typography variant="body2" sx={{ fontWeight: 700 }}>
        {params.value}
      </Typography>
    ),
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
      const isLong = params.row.is_long_term;
      return (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Typography variant="body2" sx={{ fontSize: "0.8rem" }}>{days}d</Typography>
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

/**
 * Positions table — MUI DataGrid showing all portfolio positions.
 *
 * Displays symbol, quantity, cost basis, current price, P&L, holding period,
 * short/long-term badge, wash-sale risk, and asset type.
 * Rows with losses are highlighted in light red, gains in light green.
 */
export default function PositionsTable({
  positions,
}: Readonly<PositionsTableProps>) {
  return (
    <Box sx={{ width: "100%" }}>
      <DataGrid
        rows={positions}
        columns={columns}
        getRowId={(row) => row.symbol}
        initialState={{
          sorting: {
            sortModel: [{ field: "unrealized_pnl", sort: "asc" }],
          },
        }}
        rowHeight={42}
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
            "&:hover": { backgroundColor: "action.hover" },
          },
          "& .loss-row": {
            backgroundColor: "#ffebee",
          },
          "& .gain-row": {
            backgroundColor: "#e8f5e9",
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
    </Box>
  );
}
