"""
Tax engine for OptionsTaxHub.

Provides tax bracket lookups, gain/loss tax calculations, and tax savings estimates
for tax-loss harvesting decisions. Supports 2025 and 2026 tax years.

Tax brackets source: IRS Revenue Procedures (inflation-adjusted annually).
DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
"""

from __future__ import annotations

from models import FilingStatus, TaxEstimate, TaxProfile

# --- 2025 Long-Term Capital Gains Brackets ---
# (filed in 2026)
LTCG_BRACKETS_2025 = {
    FilingStatus.SINGLE: [
        (48_350, 0.00),
        (533_400, 0.15),
        (float("inf"), 0.20),
    ],
    FilingStatus.MARRIED_FILING_JOINTLY: [
        (96_700, 0.00),
        (600_050, 0.15),
        (float("inf"), 0.20),
    ],
    FilingStatus.MARRIED_FILING_SEPARATELY: [
        (48_350, 0.00),
        (300_000, 0.15),
        (float("inf"), 0.20),
    ],
    FilingStatus.HEAD_OF_HOUSEHOLD: [
        (64_750, 0.00),
        (566_700, 0.15),
        (float("inf"), 0.20),
    ],
}

# --- 2026 Long-Term Capital Gains Brackets ---
# (filed in 2027)
LTCG_BRACKETS_2026 = {
    FilingStatus.SINGLE: [
        (49_450, 0.00),
        (545_500, 0.15),
        (float("inf"), 0.20),
    ],
    FilingStatus.MARRIED_FILING_JOINTLY: [
        (98_900, 0.00),
        (613_700, 0.15),
        (float("inf"), 0.20),
    ],
    FilingStatus.MARRIED_FILING_SEPARATELY: [
        (49_450, 0.00),
        (306_850, 0.15),
        (float("inf"), 0.20),
    ],
    FilingStatus.HEAD_OF_HOUSEHOLD: [
        (66_200, 0.00),
        (579_600, 0.15),
        (float("inf"), 0.20),
    ],
}

# --- Ordinary Income Brackets (Short-Term Capital Gains) ---
# Short-term gains are taxed as ordinary income

ORDINARY_BRACKETS_2025 = {
    FilingStatus.SINGLE: [
        (11_925, 0.10),
        (48_475, 0.12),
        (103_350, 0.22),
        (197_300, 0.24),
        (250_525, 0.32),
        (626_350, 0.35),
        (float("inf"), 0.37),
    ],
    FilingStatus.MARRIED_FILING_JOINTLY: [
        (23_850, 0.10),
        (96_950, 0.12),
        (206_700, 0.22),
        (394_600, 0.24),
        (501_050, 0.32),
        (751_600, 0.35),
        (float("inf"), 0.37),
    ],
    FilingStatus.MARRIED_FILING_SEPARATELY: [
        (11_925, 0.10),
        (48_475, 0.12),
        (103_350, 0.22),
        (197_300, 0.24),
        (250_525, 0.32),
        (375_800, 0.35),
        (float("inf"), 0.37),
    ],
    FilingStatus.HEAD_OF_HOUSEHOLD: [
        (17_000, 0.10),
        (64_850, 0.12),
        (103_350, 0.22),
        (197_300, 0.24),
        (250_500, 0.32),
        (626_350, 0.35),
        (float("inf"), 0.37),
    ],
}

ORDINARY_BRACKETS_2026 = {
    FilingStatus.SINGLE: [
        (12_150, 0.10),
        (49_475, 0.12),
        (105_525, 0.22),
        (201_450, 0.24),
        (255_800, 0.32),
        (639_750, 0.35),
        (float("inf"), 0.37),
    ],
    FilingStatus.MARRIED_FILING_JOINTLY: [
        (24_300, 0.10),
        (98_950, 0.12),
        (211_050, 0.22),
        (402_900, 0.24),
        (511_550, 0.32),
        (767_550, 0.35),
        (float("inf"), 0.37),
    ],
    FilingStatus.MARRIED_FILING_SEPARATELY: [
        (12_150, 0.10),
        (49_475, 0.12),
        (105_525, 0.22),
        (201_450, 0.24),
        (255_800, 0.32),
        (383_775, 0.35),
        (float("inf"), 0.37),
    ],
    FilingStatus.HEAD_OF_HOUSEHOLD: [
        (17_350, 0.10),
        (66_200, 0.12),
        (105_525, 0.22),
        (201_450, 0.24),
        (255_800, 0.32),
        (639_750, 0.35),
        (float("inf"), 0.37),
    ],
}

# --- Net Investment Income Tax (NIIT) ---
# Additional 3.8% on investment income for high earners
NIIT_RATE = 0.038
NIIT_THRESHOLDS = {
    FilingStatus.SINGLE: 200_000,
    FilingStatus.MARRIED_FILING_JOINTLY: 250_000,
    FilingStatus.MARRIED_FILING_SEPARATELY: 125_000,
    FilingStatus.HEAD_OF_HOUSEHOLD: 200_000,
}

# Annual capital loss deduction limit against ordinary income
CAPITAL_LOSS_DEDUCTION_LIMIT = 3_000
CAPITAL_LOSS_DEDUCTION_LIMIT_MFS = 1_500  # Married filing separately


def get_ltcg_brackets(tax_year: int) -> dict:
    """Get long-term capital gains brackets for the given tax year."""
    if tax_year >= 2026:
        return LTCG_BRACKETS_2026
    return LTCG_BRACKETS_2025


def get_ordinary_brackets(tax_year: int) -> dict:
    """Get ordinary income brackets for the given tax year."""
    if tax_year >= 2026:
        return ORDINARY_BRACKETS_2026
    return ORDINARY_BRACKETS_2025


def get_marginal_ordinary_rate(income: float, profile: TaxProfile) -> float:
    """
    Get the marginal ordinary income tax rate for a given income level.

    This is the rate applied to the next dollar of short-term capital gains.
    """
    brackets = get_ordinary_brackets(profile.tax_year)
    filing_brackets = brackets.get(profile.filing_status, brackets[FilingStatus.SINGLE])

    for threshold, rate in filing_brackets:
        if income <= threshold:
            return rate

    # Should never reach here due to inf bracket, but fallback to top rate
    return 0.37


def get_ltcg_rate(income: float, profile: TaxProfile) -> float:
    """
    Get the long-term capital gains rate for a given income level.

    Income here is taxable income including the capital gain.
    """
    brackets = get_ltcg_brackets(profile.tax_year)
    filing_brackets = brackets.get(profile.filing_status, brackets[FilingStatus.SINGLE])

    for threshold, rate in filing_brackets:
        if income <= threshold:
            return rate

    return 0.20


def check_niit(income: float, profile: TaxProfile) -> bool:
    """
    Check if the Net Investment Income Tax (3.8%) applies.

    NIIT applies when MAGI exceeds the threshold for the filing status.
    """
    threshold = NIIT_THRESHOLDS.get(profile.filing_status, 200_000)
    return income > threshold


def calculate_tax_on_gain(
    gain: float,
    is_long_term: bool,
    profile: TaxProfile,
) -> TaxEstimate:
    """
    Calculate the estimated tax on a capital gain.

    Args:
        gain: The capital gain amount (positive = gain, negative = loss).
        is_long_term: Whether the gain is long-term (held > 1 year).
        profile: User's tax profile.

    Returns:
        TaxEstimate with the applicable rates and estimated tax.
    """
    income = profile.estimated_annual_income
    niit_applies = check_niit(income, profile)

    if is_long_term:
        base_rate = get_ltcg_rate(income, profile)
    else:
        # Short-term gains taxed as ordinary income
        base_rate = get_marginal_ordinary_rate(income, profile)

    effective_rate = base_rate
    if niit_applies:
        effective_rate += NIIT_RATE

    estimated_tax = gain * effective_rate

    return TaxEstimate(
        short_term_rate=get_marginal_ordinary_rate(income, profile),
        long_term_rate=get_ltcg_rate(income, profile),
        niit_applies=niit_applies,
        effective_rate=effective_rate,
        estimated_tax=estimated_tax,
    )


def calculate_tax_savings(
    loss: float,
    is_long_term: bool,
    profile: TaxProfile,
) -> float:
    """
    Estimate the tax savings from harvesting a capital loss.

    The loss can offset gains of the same type first, then the other type.
    Any remaining loss can offset up to $3,000 of ordinary income per year.
    Excess losses carry forward to future years.

    Args:
        loss: The capital loss amount (positive number representing the loss).
        is_long_term: Whether the loss is long-term.
        profile: User's tax profile.

    Returns:
        Estimated tax savings as a positive dollar amount.
    """
    if loss <= 0:
        return 0.0

    # For simplicity in MVP, assume the loss offsets gains at the applicable rate.
    # A full implementation would net ST losses against ST gains first, etc.
    # This gives a reasonable estimate for the suggestion ranking.
    tax_estimate = calculate_tax_on_gain(
        gain=loss,
        is_long_term=is_long_term,
        profile=profile,
    )

    # Tax savings = the tax you would have owed on an equivalent gain
    return abs(tax_estimate.estimated_tax)


def get_capital_loss_limit(profile: TaxProfile) -> float:
    """
    Get the annual capital loss deduction limit against ordinary income.

    $3,000 for most filers, $1,500 for married filing separately.
    """
    if profile.filing_status == FilingStatus.MARRIED_FILING_SEPARATELY:
        return CAPITAL_LOSS_DEDUCTION_LIMIT_MFS
    return CAPITAL_LOSS_DEDUCTION_LIMIT


def get_tax_brackets_summary(profile: TaxProfile) -> dict:
    """
    Return a summary of applicable tax brackets for display in the UI.

    Used by GET /api/tax-brackets endpoint.
    """
    ordinary = get_ordinary_brackets(profile.tax_year)
    ltcg = get_ltcg_brackets(profile.tax_year)

    filing_ordinary = ordinary.get(profile.filing_status, ordinary[FilingStatus.SINGLE])
    filing_ltcg = ltcg.get(profile.filing_status, ltcg[FilingStatus.SINGLE])

    return {
        "tax_year": profile.tax_year,
        "filing_status": profile.filing_status.value,
        "ordinary_income_brackets": [
            {"up_to": threshold if threshold != float("inf") else None, "rate": rate}
            for threshold, rate in filing_ordinary
        ],
        "long_term_capital_gains_brackets": [
            {"up_to": threshold if threshold != float("inf") else None, "rate": rate}
            for threshold, rate in filing_ltcg
        ],
        "niit_threshold": NIIT_THRESHOLDS.get(profile.filing_status, 200_000),
        "niit_rate": NIIT_RATE,
        "capital_loss_limit": get_capital_loss_limit(profile),
        "marginal_ordinary_rate": get_marginal_ordinary_rate(
            profile.estimated_annual_income, profile
        ),
        "applicable_ltcg_rate": get_ltcg_rate(
            profile.estimated_annual_income, profile
        ),
    }
