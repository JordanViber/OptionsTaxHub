"""
Pydantic models for OptionsTaxHub portfolio analysis and tax-loss harvesting.

These models define the data structures used throughout the tax engine,
CSV parsing, wash-sale detection, and harvesting suggestion pipeline.

DISCLAIMER: This is for educational/simulation purposes only — not financial or tax advice.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums ---


class TransCode(str, Enum):
    """Transaction type codes matching Robinhood CSV export format."""

    BUY = "Buy"
    SELL = "Sell"
    # Options transaction codes
    STO = "STO"  # Sell to Open
    BTC = "BTC"  # Buy to Close
    BTO = "BTO"  # Buy to Open
    STC = "STC"  # Sell to Close
    OEXP = "OEXP"  # Option Expiration


class FilingStatus(str, Enum):
    """IRS filing status for tax bracket determination."""

    SINGLE = "single"
    MARRIED_FILING_JOINTLY = "married_filing_jointly"
    MARRIED_FILING_SEPARATELY = "married_filing_separately"
    HEAD_OF_HOUSEHOLD = "head_of_household"


class AssetType(str, Enum):
    """Type of financial asset."""

    STOCK = "stock"
    OPTION = "option"


# --- Transaction & Position Models ---


class Transaction(BaseModel):
    """
    A single transaction from a Robinhood CSV export.

    Maps to columns: Activity Date, Process Date, Settle Date,
    Instrument, Description, Trans Code, Quantity, Price, Amount
    """

    activity_date: date
    process_date: Optional[date] = None
    settle_date: Optional[date] = None
    instrument: str = Field(..., description="Ticker symbol (e.g., AAPL)")
    description: str = ""
    trans_code: TransCode
    quantity: float = Field(..., ge=0)
    price: float = Field(..., ge=0)
    amount: float = Field(..., description="Total dollar amount (negative for buys)")
    asset_type: AssetType = AssetType.STOCK


class TaxLot(BaseModel):
    """
    An individual tax lot representing shares purchased at a specific time and price.

    Tax lots are the fundamental unit for gain/loss calculations and
    wash-sale rule tracking. Uses FIFO (first-in, first-out) by default.
    """

    symbol: str
    quantity: float = Field(..., ge=0)
    cost_basis_per_share: float = Field(..., ge=0)
    total_cost_basis: float = Field(..., ge=0)
    purchase_date: date
    current_price: Optional[float] = None
    asset_type: AssetType = AssetType.STOCK

    # Computed fields populated during analysis
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    holding_period_days: Optional[int] = None
    is_long_term: Optional[bool] = None
    wash_sale_disallowed: float = 0.0  # Amount of loss disallowed by wash-sale rule


class Position(BaseModel):
    """
    Aggregated position summary for a single symbol.

    Combines all tax lots for a symbol into a single view
    for dashboard display.
    """

    symbol: str
    quantity: float
    avg_cost_basis: float
    total_cost_basis: float
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    earliest_purchase_date: Optional[date] = None
    holding_period_days: Optional[int] = None
    is_long_term: Optional[bool] = None
    asset_type: AssetType = AssetType.STOCK
    tax_lots: list[TaxLot] = []
    wash_sale_risk: bool = False


# --- Wash-Sale Models ---


class WashSaleFlag(BaseModel):
    """
    Flags a transaction pair that triggers the IRS wash-sale rule.

    The wash-sale rule disallows a loss deduction when substantially identical
    securities are purchased within a 61-day window (30 days before or after the sale).
    The disallowed loss is added to the cost basis of the replacement shares.
    """

    symbol: str
    sale_date: date
    sale_quantity: float
    sale_loss: float  # The loss on the sale (positive number)
    repurchase_date: date
    repurchase_quantity: float
    disallowed_loss: float  # Amount of loss disallowed
    adjusted_cost_basis: float  # New cost basis for replacement shares
    explanation: str = ""


# --- Tax Profile Models ---


class TaxProfile(BaseModel):
    """
    User's tax profile for estimating tax impact of harvesting decisions.

    Stored in Supabase linked to user account.
    """

    user_id: Optional[str] = None
    filing_status: FilingStatus = FilingStatus.SINGLE
    estimated_annual_income: float = Field(
        default=75000.0, ge=0, description="Estimated total annual income (W-2 + other)"
    )
    state: str = Field(default="", description="US state abbreviation for state tax rate")
    tax_year: int = Field(default=2025, ge=2024, le=2026)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TaxEstimate(BaseModel):
    """Estimated tax impact for a specific gain or loss."""

    short_term_rate: float = Field(..., description="Marginal short-term (ordinary income) rate")
    long_term_rate: float = Field(..., description="Long-term capital gains rate")
    niit_applies: bool = Field(
        default=False, description="Whether 3.8% Net Investment Income Tax applies"
    )
    effective_rate: float = Field(..., description="Effective rate for this specific gain/loss")
    estimated_tax: float = Field(
        ..., description="Estimated tax owed (positive) or saved (negative)"
    )


# --- Harvesting Suggestion Models ---


class ReplacementCandidate(BaseModel):
    """A suggested replacement security for tax-loss harvesting."""

    symbol: str
    name: str = ""
    reason: str = Field(
        ...,
        description="Why this is similar but not substantially identical (avoids wash-sale)",
    )


class HarvestingSuggestion(BaseModel):
    """
    A tax-loss harvesting recommendation for a specific position.

    Identifies positions with unrealized losses that can be sold to realize
    a tax-deductible loss, along with replacement candidates to maintain
    market exposure while avoiding the wash-sale rule.

    DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
    """

    symbol: str
    action: str = "SELL"
    quantity: float
    current_price: Optional[float] = None
    cost_basis_per_share: float
    estimated_loss: float = Field(
        ..., description="Total estimated loss from selling (positive number)"
    )
    tax_savings_estimate: float = Field(
        ..., description="Estimated tax savings from harvesting this loss"
    )
    holding_period_days: int
    is_long_term: bool
    wash_sale_risk: bool = Field(
        default=False,
        description="Whether selling now would trigger a wash-sale due to recent purchases",
    )
    wash_sale_explanation: str = ""
    replacement_candidates: list[ReplacementCandidate] = []
    ai_explanation: str = Field(
        default="", description="AI-generated explanation of this suggestion"
    )
    ai_generated: bool = Field(
        default=False, description="Whether AI was used for this suggestion"
    )
    priority: int = Field(
        default=0, description="Priority ranking (1 = highest tax benefit)"
    )


# --- Portfolio Analysis Response ---


class PortfolioSummary(BaseModel):
    """High-level portfolio metrics for the dashboard summary cards."""

    total_market_value: float = 0.0
    total_cost_basis: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_unrealized_pnl_pct: float = 0.0
    total_harvestable_losses: float = 0.0
    estimated_tax_savings: float = 0.0
    positions_count: int = 0
    lots_with_losses: int = 0
    lots_with_gains: int = 0
    wash_sale_flags_count: int = 0


class PortfolioAnalysis(BaseModel):
    """
    Complete portfolio analysis response.

    Returned by POST /api/portfolio/analyze after processing a CSV upload.
    Contains positions, tax-loss harvesting suggestions, wash-sale flags,
    and summary metrics.

    DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
    """

    positions: list[Position] = []
    tax_lots: list[TaxLot] = []
    suggestions: list[HarvestingSuggestion] = []
    wash_sale_flags: list[WashSaleFlag] = []
    summary: PortfolioSummary = PortfolioSummary()
    tax_profile: Optional[TaxProfile] = None
    disclaimer: str = (
        "This analysis is for educational and simulation purposes only. "
        "It does not constitute financial, tax, or investment advice. "
        "Consult a qualified tax professional before making investment decisions."
    )
    errors: list[str] = []
    warnings: list[str] = []
