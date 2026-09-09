/**
 * TypeScript type definitions for OptionsTaxHub portfolio analysis.
 *
 * These types mirror the backend Pydantic models to ensure type safety
 * across the full stack. Keep in sync with server/models.py.
 *
 * DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
 */

// --- Enums ---

export type TransCode = "Buy" | "Sell" | "STO" | "BTC" | "BTO" | "STC" | "OEXP";

export type FilingStatus =
  | "single"
  | "married_filing_jointly"
  | "married_filing_separately"
  | "head_of_household";

export type AssetType = "stock" | "option";

// --- Tax Lot & Position ---

export interface TaxLot {
  symbol: string;
  quantity: number;
  cost_basis_per_share: number;
  total_cost_basis: number;
  purchase_date: string; // ISO date string
  current_price: number | null;
  asset_type: AssetType;
  contract_label?: string | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  holding_period_days: number | null;
  is_long_term: boolean | null;
  wash_sale_disallowed: number;
}

export interface Position {
  position_id?: string;
  symbol: string;
  display_label?: string | null;
  manual_review_required?: boolean;
  manual_review_reason?: string;
  quantity: number;
  avg_cost_basis: number;
  total_cost_basis: number;
  current_price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  earliest_purchase_date: string | null;
  holding_period_days: number | null;
  is_long_term: boolean | null;
  asset_type: AssetType;
  tax_lots: TaxLot[];
  wash_sale_risk: boolean;
  contract_label?: string | null;
}

// --- Wash-Sale ---

export interface WashSaleFlag {
  symbol: string;
  sale_date: string;
  sale_quantity: number;
  sale_loss: number;
  repurchase_date: string;
  repurchase_quantity: number;
  disallowed_loss: number;
  adjusted_cost_basis: number;
  explanation: string;
  // Original lot acquisition, when present on the flag. Used to classify
  // the washed sale as ST vs LT without changing wash_sale.py.
  purchase_date?: string | null;
}

// --- Tax Profile ---

export interface TaxProfile {
  user_id?: string;
  filing_status: FilingStatus;
  estimated_annual_income: number;
  state: string;
  tax_year: number;
  created_at?: string;
  updated_at?: string;
}

// --- Harvesting Suggestions ---

export interface ReplacementCandidate {
  symbol: string;
  name: string;
  reason: string;
}

export interface HarvestingSuggestion {
  symbol: string;
  suggestion_id?: string;
  display_label?: string;
  lot_details?: string;
  manual_review_required?: boolean;
  manual_review_reason?: string;
  action: string;
  quantity: number;
  current_price: number | null;
  cost_basis_per_share: number;
  estimated_loss: number;
  tax_savings_estimate: number;
  holding_period_days: number;
  is_long_term: boolean;
  wash_sale_risk: boolean;
  wash_sale_explanation: string;
  replacement_candidates: ReplacementCandidate[];
  ai_explanation: string;
  ai_generated: boolean;
  priority: number;
}

export interface Form1099BLot {
  symbol: string;
  cusip?: string;
  description?: string;
  quantity: number;
  date_sold?: string | null;
  date_acquired?: string | null;
  proceeds: number;
  cost_basis: number;
  wash_sale_disallowed: number;
  gain_or_loss?: number;
  term?: string;
  covered?: boolean;
  form_8949_box?: string;
  additional_info?: string;
  is_aggregate?: boolean;
}

export type LotMatchStatus =
  | "matched"
  | "matched_settlement_gap"
  | "1099_only"
  | "csv_only";

export interface LotMatchRow {
  status: LotMatchStatus | string;
  symbol: string;
  description?: string;
  quantity: number;
  date_sold_1099?: string | null;
  export_trade_date?: string | null;
  export_settle_date?: string | null;
  proceeds_1099: number;
  proceeds_export: number;
  cost_basis_1099?: number;
  cost_basis_export?: number;
  wash_sale_disallowed?: number;
}

export interface LotMatchReport {
  matched: LotMatchRow[];
  gap: LotMatchRow[];
  unmatched: LotMatchRow[];
  matched_count: number;
  gap_count: number;
  unmatched_count: number;
  totals_ok?: boolean;
  lot_proceeds_total?: number;
  lot_cost_basis_total?: number;
  lot_wash_total?: number;
}

export interface Supplemental1099Summary {
  source_filename: string;
  broker_name: string;
  tax_year: number | null;
  short_term_proceeds: number;
  short_term_cost_basis: number;
  short_term_wash_sale_disallowed: number;
  short_term_net_gain: number;
  long_term_proceeds: number;
  long_term_cost_basis: number;
  long_term_wash_sale_disallowed: number;
  long_term_net_gain: number;
  referenced_symbols: string[];
  matched_symbols: string[];
  insights: string[];
  lots?: Form1099BLot[];
}

// --- Portfolio Analysis Response ---

// --- Realized Gain/Loss Summary ---

export interface RealizedSummary {
  tax_year: number;
  st_gains: number;
  st_losses: number;
  lt_gains: number;
  lt_losses: number;
  net_st: number;
  net_lt: number;
  total_net: number;
  transactions_count: number;
}

export interface PortfolioSummary {
  total_market_value: number;
  total_cost_basis: number;
  total_unrealized_pnl: number;
  total_unrealized_pnl_pct: number;
  total_harvestable_losses: number;
  estimated_tax_savings: number;
  positions_count: number;
  lots_with_losses: number;
  lots_with_gains: number;
  wash_sale_flags_count: number;
  realized_summary?: RealizedSummary | null;
  activity_first_date?: string | null;
  activity_last_date?: string | null;
  activity_transaction_count?: number;
}

export interface ActivityBookSummary {
  transaction_count: number;
  first_activity_date?: string | null;
  last_activity_date?: string | null;
  added_from_this_upload: number;
  already_in_book: number;
  merged_from_analysis_id?: string | null;
  merged_from_filename?: string;
  gap_days: number;
  replaced: boolean;
  transactions?: unknown[];
}

export interface PortfolioAnalysis {
  positions: Position[];
  tax_lots: TaxLot[];
  suggestions: HarvestingSuggestion[];
  wash_sale_flags: WashSaleFlag[];
  summary: PortfolioSummary;
  tax_profile: TaxProfile | null;
  supplemental_1099?: Supplemental1099Summary | null;
  lot_match_report?: LotMatchReport | null;
  analysis_id?: string | null;
  activity_book?: ActivityBookSummary | null;
  packet_unlocked?: boolean;
  packet_session_id?: string | null;
  sample_run?: boolean;
  disclaimer: string;
  errors: string[];
  warnings: string[];
}

// --- Tax Brackets API ---

export interface TaxBracket {
  up_to: number | null;
  rate: number;
}

export interface TaxBracketsSummary {
  tax_year: number;
  filing_status: string;
  ordinary_income_brackets: TaxBracket[];
  long_term_capital_gains_brackets: TaxBracket[];
  niit_threshold: number;
  niit_rate: number;
  capital_loss_limit: number;
  marginal_ordinary_rate: number;
  applicable_ltcg_rate: number;
}

// --- Prices API ---

export interface PricesResponse {
  prices: Record<string, number>;
  warnings: string[];
}

// --- LEAP rank vs owning the stock ---

export type LeapRankFailReason =
  | "no_quote"
  | "no_chain"
  | "no_candidates"
  | "invalid";

export type LeapRankPremiumSource = "mid" | "last" | "ask" | "bid";

export interface LeapRankParams {
  symbol: string;
  right: "call" | "put";
  expiry_from: string;
  expiry_to: string;
}

export interface LeapRankCandidate {
  rank: number;
  contract_label: string;
  symbol: string;
  right: "call" | "put";
  strike: number;
  expiration: string;
  premium: number;
  premium_source: LeapRankPremiumSource;
  bid: number | null;
  ask: number | null;
  last: number | null;
  dte: number;
  implied_cagr: number;
  leverage: number;
  intrinsic: number;
  extrinsic: number;
  extrinsic_yield: number;
  breakeven: number;
  why_vs_stock: string;
  why_vs_richer: string | null;
}

export interface LeapRankSuccess {
  ok: true;
  symbol: string;
  right: "call" | "put";
  spot: number;
  as_of: string;
  expiry_from: string;
  expiry_to: string;
  expirations_used: string[];
  candidates_considered: number;
  ranks: LeapRankCandidate[];
  warnings: string[];
}

export interface LeapRankFailure {
  ok: false;
  reason: LeapRankFailReason;
  message: string;
  ranks: [];
}

export type LeapRankResponse = LeapRankSuccess | LeapRankFailure;

export const RH_CONNECTION_REQUIRED_COPY =
  "Connect Robinhood after sign-in for live top-3";

export type RhChainFailCode =
  | "RH_CONNECTION_REQUIRED"
  | "RH_TIMEOUT"
  | "RH_RATE_LIMITED"
  | "RH_EMPTY_CHAIN"
  | "RH_NO_EXPIRY_IN_WINDOW"
  | "RH_SAAS_WALL"
  | "RH_MALFORMED"
  | "RH_REVOKED";

export interface RhChainSuccess extends LeapRankSuccess {
  provider: "robinhood";
  quote_timestamp?: string | null;
  code?: "ok";
}

export interface RhChainFailure {
  ok: false;
  code: RhChainFailCode;
  reason?: string;
  message: string;
  ranks: [];
  provider?: "robinhood";
}

export type RhChainResponse = RhChainSuccess | RhChainFailure;

export interface RhStatusResponse {
  connected: boolean;
  code?: string;
  message?: string;
}

// --- Analysis History ---

export interface AnalysisHistoryItem {
  id: string;
  user_id: string;
  filename: string;
  uploaded_at: string;
  summary: PortfolioSummary;
  positions_count: number;
  total_market_value: number;
  result?: PortfolioAnalysis | null;
}

// --- Filing Status Display Labels ---

export const FILING_STATUS_LABELS: Record<FilingStatus, string> = {
  single: "Single",
  married_filing_jointly: "Married Filing Jointly",
  married_filing_separately: "Married Filing Separately",
  head_of_household: "Head of Household",
};

// --- US States for Tax Profile ---

export const US_STATES: { value: string; label: string }[] = [
  { value: "", label: "Select State" },
  { value: "AL", label: "Alabama" },
  { value: "AK", label: "Alaska" },
  { value: "AZ", label: "Arizona" },
  { value: "AR", label: "Arkansas" },
  { value: "CA", label: "California" },
  { value: "CO", label: "Colorado" },
  { value: "CT", label: "Connecticut" },
  { value: "DE", label: "Delaware" },
  { value: "FL", label: "Florida" },
  { value: "GA", label: "Georgia" },
  { value: "HI", label: "Hawaii" },
  { value: "ID", label: "Idaho" },
  { value: "IL", label: "Illinois" },
  { value: "IN", label: "Indiana" },
  { value: "IA", label: "Iowa" },
  { value: "KS", label: "Kansas" },
  { value: "KY", label: "Kentucky" },
  { value: "LA", label: "Louisiana" },
  { value: "ME", label: "Maine" },
  { value: "MD", label: "Maryland" },
  { value: "MA", label: "Massachusetts" },
  { value: "MI", label: "Michigan" },
  { value: "MN", label: "Minnesota" },
  { value: "MS", label: "Mississippi" },
  { value: "MO", label: "Missouri" },
  { value: "MT", label: "Montana" },
  { value: "NE", label: "Nebraska" },
  { value: "NV", label: "Nevada" },
  { value: "NH", label: "New Hampshire" },
  { value: "NJ", label: "New Jersey" },
  { value: "NM", label: "New Mexico" },
  { value: "NY", label: "New York" },
  { value: "NC", label: "North Carolina" },
  { value: "ND", label: "North Dakota" },
  { value: "OH", label: "Ohio" },
  { value: "OK", label: "Oklahoma" },
  { value: "OR", label: "Oregon" },
  { value: "PA", label: "Pennsylvania" },
  { value: "RI", label: "Rhode Island" },
  { value: "SC", label: "South Carolina" },
  { value: "SD", label: "South Dakota" },
  { value: "TN", label: "Tennessee" },
  { value: "TX", label: "Texas" },
  { value: "UT", label: "Utah" },
  { value: "VT", label: "Vermont" },
  { value: "VA", label: "Virginia" },
  { value: "WA", label: "Washington" },
  { value: "WV", label: "West Virginia" },
  { value: "WI", label: "Wisconsin" },
  { value: "WY", label: "Wyoming" },
  { value: "DC", label: "District of Columbia" },
];
