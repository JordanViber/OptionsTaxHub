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
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  holding_period_days: number | null;
  is_long_term: boolean | null;
  wash_sale_disallowed: number;
}

export interface Position {
  symbol: string;
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

// --- Portfolio Analysis Response ---

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
}

export interface PortfolioAnalysis {
  positions: Position[];
  tax_lots: TaxLot[];
  suggestions: HarvestingSuggestion[];
  wash_sale_flags: WashSaleFlag[];
  summary: PortfolioSummary;
  tax_profile: TaxProfile | null;
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
