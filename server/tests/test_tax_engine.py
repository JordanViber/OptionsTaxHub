"""
Tests for the tax engine module.

Validates IRS tax bracket lookups, marginal rate calculations,
tax savings estimates, and the brackets summary endpoint logic.
"""

import sys
import os

# Add server root to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import TaxProfile, FilingStatus
from tax_engine import (
    get_marginal_ordinary_rate,
    get_ltcg_rate,
    calculate_tax_on_gain,
    calculate_tax_savings,
    get_tax_brackets_summary,
)


def _profile(
    filing_status: str = "single",
    income: float = 75000,
    tax_year: int = 2025,
) -> TaxProfile:
    """Helper to create a TaxProfile for testing."""
    return TaxProfile(
        filing_status=FilingStatus(filing_status),
        estimated_annual_income=income,
        tax_year=tax_year,
    )


# --- Marginal Ordinary Income Rate Tests ---

class TestMarginalOrdinaryRate:
    def test_single_10_percent_bracket(self):
        rate = get_marginal_ordinary_rate(10_000, _profile(income=10_000))
        assert rate == 0.10

    def test_single_22_percent_bracket(self):
        rate = get_marginal_ordinary_rate(50_000, _profile(income=50_000))
        assert rate == 0.22

    def test_single_24_percent_bracket(self):
        rate = get_marginal_ordinary_rate(110_000, _profile(income=110_000))
        assert rate == 0.24

    def test_single_37_percent_bracket(self):
        rate = get_marginal_ordinary_rate(700_000, _profile(income=700_000))
        assert rate == 0.37

    def test_married_filing_jointly_lower_rate(self):
        p = _profile(filing_status="married_filing_jointly", income=100_000)
        rate = get_marginal_ordinary_rate(100_000, p)
        assert rate == 0.22

    def test_head_of_household(self):
        p = _profile(filing_status="head_of_household", income=80_000)
        rate = get_marginal_ordinary_rate(80_000, p)
        assert rate == 0.22

    def test_2026_brackets(self):
        p = _profile(tax_year=2026, income=50_000)
        rate = get_marginal_ordinary_rate(50_000, p)
        assert rate in {0.12, 0.22, 0.25}

    def test_zero_income(self):
        rate = get_marginal_ordinary_rate(0, _profile(income=0))
        assert rate == 0.10


# --- Long-Term Capital Gains Rate Tests ---

class TestLtcgRate:
    def test_single_0_percent(self):
        rate = get_ltcg_rate(40_000, _profile(income=40_000))
        assert rate == 0.0

    def test_single_15_percent(self):
        rate = get_ltcg_rate(100_000, _profile(income=100_000))
        assert rate == 0.15

    def test_single_20_percent(self):
        rate = get_ltcg_rate(600_000, _profile(income=600_000))
        assert rate == 0.20

    def test_mfj_higher_threshold(self):
        p = _profile(filing_status="married_filing_jointly", income=80_000)
        rate = get_ltcg_rate(80_000, p)
        assert rate == 0.0


# --- Tax Savings Calculation Tests ---

class TestTaxSavings:
    def test_short_term_loss_saves_at_ordinary_rate(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=1000, is_long_term=False, profile=p)
        # $1000 short-term loss at 22% marginal rate = $220
        assert savings == 220.0

    def test_long_term_loss_saves_at_ltcg_rate(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=1000, is_long_term=True, profile=p)
        # $1000 long-term loss at 15% LTCG rate = $150
        assert savings == 150.0

    def test_zero_loss(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=0, is_long_term=False, profile=p)
        assert savings == 0.0

    def test_negative_loss_treated_as_zero(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=-500, is_long_term=False, profile=p)
        assert savings == 0.0


# --- Tax on Gain Tests ---

class TestCalculateTaxOnGain:
    def test_short_term_gain_taxed_as_ordinary(self):
        p = _profile(income=100_000)
        result = calculate_tax_on_gain(gain=5000, is_long_term=False, profile=p)
        # 22% marginal rate on $5000 = $1100
        assert result.estimated_tax == 1100.0

    def test_long_term_gain_taxed_at_ltcg(self):
        p = _profile(income=100_000)
        result = calculate_tax_on_gain(gain=5000, is_long_term=True, profile=p)
        # 15% LTCG rate on $5000 = $750
        assert result.estimated_tax == 750.0


# --- Tax Brackets Summary Tests ---

class TestGetTaxBracketsSummary:
    def test_summary_structure(self):
        p = _profile(income=100_000)
        summary = get_tax_brackets_summary(p)
        assert summary["tax_year"] == 2025
        assert summary["filing_status"] == "single"
        assert "ordinary_income_brackets" in summary
        assert "long_term_capital_gains_brackets" in summary
        assert "niit_threshold" in summary
        assert "niit_rate" in summary
        assert summary["niit_rate"] == 0.038
        assert summary["capital_loss_limit"] == 3000
        assert "marginal_ordinary_rate" in summary
        assert "applicable_ltcg_rate" in summary

    def test_summary_rates_match_lookups(self):
        p = _profile(income=100_000)
        summary = get_tax_brackets_summary(p)
        assert summary["marginal_ordinary_rate"] == get_marginal_ordinary_rate(100_000, p)
        assert summary["applicable_ltcg_rate"] == get_ltcg_rate(100_000, p)
