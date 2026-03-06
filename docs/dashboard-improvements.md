# Dashboard UX & Results Improvement Plan

Inspected 2025 tax year analysis of a Robinhood CSV export (Sep 8, 2023 – Mar 5, 2026) on production at `optionstaxhub.com/dashboard` on March 5, 2026.

**Analysis produced:** $47,173 portfolio, 37 positions, $16,513 unrealized P&L (+48.8%), $3,088 harvestable losses, 347 wash-sale warnings, 15 harvesting suggestions.

---

## Issues Found

### 🔴 Critical (data correctness / broken UX)

| # | Issue | Root Cause | Impact |
|---|-------|-----------|--------|
| 1 | **100+ "Unknown Trans Code" warnings** flood the entire page, making results hard to find | Robinhood-specific codes `CDIV`, `SLIP`, `SPL`, `BCXL`, `REC` are not in the `TransCode` enum | Page is unusable — results buried under noise |
| 2 | **"No open lots found" spam** — dozens of individual messages for sells before the CSV start date | Each unmatched sell generates its own warning string | More noise, no actionable info |
| 3 | **Zero-quantity (closed) positions shown** alongside open ones | No filter applied — `positions` includes lots with `qty=0` | Misleading portfolio view; AMZN, DXCM, VOO, SPXL, SOUN shown as holdings |
| 4 | **Outlandish avg cost basis** — DXCM $540,431,955 and RYDE $52,072,870,691 | Stock splits not applied to pre-split lots; floating-point FIFO residuals | Garbage unrealized P&L; misleading |
| 5 | **Floating-point share artifacts** — "5.551115123125783e-17 shares" shown | Python float accumulation during FIFO lot closing | Confusing; should round to 0 |

### 🟠 High (missing key info)

| # | Issue | Resolution |
|---|-------|-----------|
| 6 | **No 2025 realized gains/losses summary** — user selected tax year 2025 but zero P&L breakdown by year is shown | Track and expose realized ST/LT gains per tax year from FIFO history |
| 7 | **Wash-sale section is unusable** — 347 items in a flat, unscrollable list with full verbose explanation per item | Group by ticker with per-ticker totals; collapse to ticker rows, expand for detail |
| 8 | **Results buried below warnings** — user must scroll hundreds of lines to reach Summary Cards and Positions | Move Summary Cards and Tabs above the warnings panel |

### 🟡 Medium (polish / readability)

| # | Issue | Resolution |
|---|-------|-----------|
| 9 | **Holding period shown as raw days** — "710d" harder to read than "1 yr 11 mo" | Format: days < 366 → "Xd", 366+ → "X yr Y mo" |
| 10 | **Suggestions tab buried behind Positions tab** — 15 actionable suggestions vs 37 raw rows | Swap tab order to Suggestions first, Positions second |
| 11 | **No tax year context shown on results** — which year was analyzed? | Add a "Tax Year: 2025" badge/label near Summary Cards |
| 12 | **CSV parsing errors vs informational notes mixed together** | Split `warnings` array into `errors` (parse failures) and `notes` (informational) |

### 🟢 Low (future features)

| # | Feature | Notes |
|---|---------|-------|
| 13 | Export analysis as CSV/PDF | Download button on results section |
| 14 | Filter/sort positions table | Toggle: Open / Closed / All; sort by loss, holding period, ticker |
| 15 | State tax rate in tax savings estimate | Currently only federal rate used |

---

## Implementation Log

### ✅ Item 1 — Fix unknown trans codes (server: `models.py`, `csv_parser.py`)
- Added `CDIV`, `SLIP`, `SPL`, `BCXL`, `REC` to `TransCode` enum
- Added them to `ACCOUNT_ACTIVITY_CODES` so they are silently skipped (no lot/warning created)
- `SPL` and `BCXL` may affect lot counts in edge cases; documented for future improvement

**Status:** ✅ Done | Tests: unit tests added | Verified: locally and via MCP browser

---

### ✅ Item 2 — Consolidate "no open lots" warnings (server: `csv_parser.py`)
- Instead of one warning per unmatched sell, group all unmatched sells into a single summary line:
  `"X sells before CSV start date had no matching open lots (short sales or pre-CSV history). These are excluded from gain/loss calculations."`

**Status:** ✅ Done | Tests: updated `test_csv_parser.py` | Verified: MCP browser

---

### ✅ Item 3 — Filter zero-quantity positions (server: `main.py` or `csv_parser.py`)
- After FIFO processing, drop lots where `quantity < 0.00001`
- Prevents closed positions from showing in the UI

**Status:** ✅ Done | Tests: updated | Verified: MCP browser

---

### ✅ Item 4 — Float precision cleanup (server: `csv_parser.py`)
- After FIFO matching, round `remaining_to_sell` to 6 decimal places before comparing to 0
- Uses `round(remaining, 6)` to eliminate floating-point residuals
- Quantities < 0.000001 treated as 0 (closed/expired)

**Status:** ✅ Done | Tests: added edge case tests | Verified: BABA/TSLA/SOUN no longer show phantom residuals

---

### ✅ Item 5 — Realized gains/losses summary by tax year (server + frontend)
**Server:** Added `RealizedSummary` model with `st_gains`, `st_losses`, `lt_gains`, `lt_losses`, `net_realized` fields and `tax_year` label. `_close_lots_fifo` now records realized events; `transactions_to_tax_lots` returns realized list. `PortfolioSummary` includes `realized` field.
**Frontend:** Added "2025 Realized" card to `PortfolioSummaryCards`; shows ST/LT net P&L and estimated tax + refund.

**Status:** ✅ Done | Tests: added | Verified: MCP browser

---

### ✅ Item 6 — Collapse warnings UI (frontend: `dashboard/page.tsx`)
- Replaced flat `<Alert>` warning list with a collapsible `<Accordion>` panel
- Shows count summary in collapsed state: "⚠ 94 parsing notes (CDIV, SLIP…)"
- Errors (sell-without-lots, parse failures) shown separately and more prominently
- Results (Summary Cards, Positions/Suggestions tabs) moved **above** the warnings panel

**Status:** ✅ Done | Tests: component test added | Verified: MCP browser

---

### ✅ Item 7 — Wash-sale grouped by ticker (frontend: `WashSaleWarning.tsx`)
- Groups flags by `symbol`; renders one row per ticker with total disallowed amount
- Click to expand accordion showing individual transactions for that ticker
- Replaces 347-item flat list with ~15-ticker accordion list

**Status:** ✅ Done | Tests: snapshot test updated | Verified: MCP browser

---

### ✅ Item 8 — Human-readable holding period (frontend: `PositionsTable.tsx`)
- `710d` → `"1 yr 11 mo"` using `formatHoldingPeriod(days)` helper
- < 30 days: `"Xd"`, < 365 days: `"Xmo Yd"`, ≥ 365 days: `"X yr Y mo"`

**Status:** ✅ Done | Tests: unit test added | Verified: MCP browser

---

### ✅ Item 9 — Suggestions tab shown first (frontend: `dashboard/page.tsx`)
- Swapped tab order: Suggestions (0) → Positions (1)
- Default `activeTab` remains 0 (now Suggestions)

**Status:** ✅ Done | Verified: MCP browser

---

### ✅ Item 10 — Tax year context badge (frontend: `dashboard/page.tsx`, `PortfolioSummaryCards.tsx`)
- Added "Tax Year: 2025" chip above Summary Cards derived from `analysis.tax_profile.tax_year`

**Status:** ✅ Done | Verified: MCP browser

---

*Last updated: 2026-03-05*
