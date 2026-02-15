"""
Tests for the tax engine module.

Validates IRS tax bracket lookups, marginal rate calculations,
tax savings estimates, and the brackets summary endpoint logic.
"""

import sys
import os

import pytest

# Add server root to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import TaxProfile, FilingStatus
from tax_engine import (
    get_marginal_ordinary_rate,
    get_ltcg_rate,
    calculate_tax_on_gain,
    calculate_tax_savings,
    get_tax_brackets_summary,
    check_niit,
    get_capital_loss_limit,
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
        assert rate == pytest.approx(0.10)

    def test_single_22_percent_bracket(self):
        rate = get_marginal_ordinary_rate(50_000, _profile(income=50_000))
        assert rate == pytest.approx(0.22)

    def test_single_24_percent_bracket(self):
        rate = get_marginal_ordinary_rate(110_000, _profile(income=110_000))
        assert rate == pytest.approx(0.24)

    def test_single_37_percent_bracket(self):
        rate = get_marginal_ordinary_rate(700_000, _profile(income=700_000))
        assert rate == pytest.approx(0.37)

    def test_married_filing_jointly_lower_rate(self):
        p = _profile(filing_status="married_filing_jointly", income=100_000)
        rate = get_marginal_ordinary_rate(100_000, p)
        assert rate == pytest.approx(0.22)

    def test_head_of_household(self):
        p = _profile(filing_status="head_of_household", income=80_000)
        rate = get_marginal_ordinary_rate(80_000, p)
        assert rate == pytest.approx(0.22)

    def test_2026_brackets(self):
        p = _profile(tax_year=2026, income=50_000)
        rate = get_marginal_ordinary_rate(50_000, p)
        assert rate in {0.12, 0.22, 0.25}

    def test_zero_income(self):
        rate = get_marginal_ordinary_rate(0, _profile(income=0))
        assert rate == pytest.approx(0.10)


# --- Long-Term Capital Gains Rate Tests ---

class TestLtcgRate:
    def test_single_0_percent(self):
        rate = get_ltcg_rate(40_000, _profile(income=40_000))
        assert rate == pytest.approx(0.0)

    def test_single_15_percent(self):
        rate = get_ltcg_rate(100_000, _profile(income=100_000))
        assert rate == pytest.approx(0.15)

    def test_single_20_percent(self):
        rate = get_ltcg_rate(600_000, _profile(income=600_000))
        assert rate == pytest.approx(0.20)

    def test_mfj_higher_threshold(self):
        p = _profile(filing_status="married_filing_jointly", income=80_000)
        rate = get_ltcg_rate(80_000, p)
        assert rate == pytest.approx(0.0)

    def test_2026_ltcg_brackets(self):
        """2026 tax year uses LTCG_BRACKETS_2026."""
        p = _profile(tax_year=2026, income=100_000)
        rate = get_ltcg_rate(100_000, p)
        assert rate in {0.0, 0.15, 0.20}


# --- Tax Savings Calculation Tests ---

class TestTaxSavings:
    def test_short_term_loss_saves_at_ordinary_rate(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=1000, is_long_term=False, profile=p)
        # $1000 short-term loss at 22% marginal rate = $220
        assert savings == pytest.approx(220.0)

    def test_long_term_loss_saves_at_ltcg_rate(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=1000, is_long_term=True, profile=p)
        # $1000 long-term loss at 15% LTCG rate = $150
        assert savings == pytest.approx(150.0)

    def test_zero_loss(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=0, is_long_term=False, profile=p)
        assert savings == pytest.approx(0.0)

    def test_negative_loss_treated_as_zero(self):
        p = _profile(income=100_000)
        savings = calculate_tax_savings(loss=-500, is_long_term=False, profile=p)
        assert savings == pytest.approx(0.0)


# --- Tax on Gain Tests ---

class TestCalculateTaxOnGain:
    def test_short_term_gain_taxed_as_ordinary(self):
        p = _profile(income=100_000)
        result = calculate_tax_on_gain(gain=5000, is_long_term=False, profile=p)
        # 22% marginal rate on $5000 = $1100
        assert result.estimated_tax == pytest.approx(1100.0)

    def test_long_term_gain_taxed_at_ltcg(self):
        p = _profile(income=100_000)
        result = calculate_tax_on_gain(gain=5000, is_long_term=True, profile=p)
        # 15% LTCG rate on $5000 = $750
        assert result.estimated_tax == pytest.approx(750.0)


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
        assert summary["niit_rate"] == pytest.approx(0.038)
        assert summary["capital_loss_limit"] == 3000
        assert "marginal_ordinary_rate" in summary
        assert "applicable_ltcg_rate" in summary

    def test_summary_rates_match_lookups(self):
        p = _profile(income=100_000)
        summary = get_tax_brackets_summary(p)
        assert summary["marginal_ordinary_rate"] == get_marginal_ordinary_rate(100_000, p)
        assert summary["applicable_ltcg_rate"] == get_ltcg_rate(100_000, p)


# --- NIIT Tests ---

class TestCheckNiit:
    def test_single_below_threshold(self):
        p = _profile(filing_status="single", income=180_000)
        assert check_niit(180_000, p) is False

    def test_single_above_threshold(self):
        p = _profile(filing_status="single", income=250_000)
        assert check_niit(250_000, p) is True

    def test_married_filing_separately(self):
        p = _profile(filing_status="married_filing_separately", income=130_000)
        assert check_niit(130_000, p) is True

    def test_head_of_household(self):
        p = _profile(filing_status="head_of_household", income=210_000)
        assert check_niit(210_000, p) is True

    def test_niit_at_exact_threshold(self):
        p = _profile(filing_status="single", income=200_000)
        assert check_niit(200_000, p) is False  # not greater than


# --- Capital Loss Limit Tests ---

class TestGetCapitalLossLimit:
    def test_single_filer(self):
        p = _profile(filing_status="single")
        assert get_capital_loss_limit(p) == 3000

    def test_married_filing_jointly(self):
        p = _profile(filing_status="married_filing_jointly")
        assert get_capital_loss_limit(p) == 3000

    def test_married_filing_separately(self):
        p = _profile(filing_status="married_filing_separately")
        assert get_capital_loss_limit(p) == 1500

    def test_head_of_household(self):
        p = _profile(filing_status="head_of_household")
        assert get_capital_loss_limit(p) == 3000


# --- NIIT Integration with Tax Calculation ---

class TestNiitIntegration:
    def test_niit_adds_to_effective_rate(self):
        p = _profile(filing_status="single", income=250_000)
        result = calculate_tax_on_gain(gain=10_000, is_long_term=True, profile=p)
        assert result.niit_applies is True
        # effective_rate = ltcg_rate + 0.038
        assert result.effective_rate == pytest.approx(get_ltcg_rate(250_000, p) + 0.038)


# --- MFS Tax Brackets Summary ---

class TestMfsBracketsSummary:
    def test_mfs_capital_loss_limit_in_summary(self):
        p = _profile(filing_status="married_filing_separately", income=100_000)
        summary = get_tax_brackets_summary(p)
        assert summary["capital_loss_limit"] == 1500

    def test_hoh_brackets_summary(self):
        p = _profile(filing_status="head_of_household", income=100_000)
        summary = get_tax_brackets_summary(p)
        assert summary["filing_status"] == "head_of_household"
        assert summary["capital_loss_limit"] == 3000
