"""
AI advisor for OptionsTaxHub — powered by Google Gemini.

Provides intelligent tax-loss harvesting suggestions including:
- Replacement security recommendations (similar but not substantially identical)
- Natural language explanations of each suggestion and its tax impact
- Prioritization reasoning for which positions to harvest first

Only sends anonymized/aggregated data to the AI (symbols, quantities, P&L).
No user identity, account numbers, or personal information is transmitted.

Falls back to hardcoded replacement mappings if the AI is unavailable.

DISCLAIMER: AI-generated suggestions are for educational/simulation purposes only.
Not financial, tax, or investment advice.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Gemini configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT_SECONDS = 15

# System prompt for the tax-loss harvesting advisor
SYSTEM_PROMPT = """You are a tax-loss harvesting advisor for a portfolio analysis tool.
Your role is to suggest replacement securities and explain harvesting strategies.

IMPORTANT RULES:
1. ALWAYS include a disclaimer that this is for educational/simulation purposes only,
   not financial or tax advice.
2. Replacement securities must NOT be "substantially identical" to the original
   to avoid triggering IRS wash-sale rules. ETFs tracking the same narrow index
   as the stock being sold should be avoided.
3. Focus on maintaining similar market exposure (sector, risk profile) while
   ensuring the replacement is clearly different from a wash-sale perspective.
4. Explain your reasoning in plain, accessible English suitable for DIY retail traders.
5. Consider both short-term and long-term tax implications in your analysis.

You will receive portfolio positions with unrealized losses. For each position, provide:
- 2-3 replacement securities (ticker, full name, and reason it's safe from wash-sale rules)
- A plain-English explanation of why harvesting this loss is beneficial
- Priority reasoning relative to other positions

Respond in valid JSON format only."""


def _build_prompt(positions_with_losses: list[dict]) -> str:
    """
    Build the user prompt with anonymized position data.

    Only includes: symbol, quantity, unrealized P&L, holding period, short/long-term status.
    No user identity or account information.
    """
    positions_text = json.dumps(positions_with_losses, indent=2)

    return f"""Analyze these portfolio positions that have unrealized losses and provide
tax-loss harvesting recommendations.

Positions with unrealized losses:
{positions_text}

For each position, provide your response in this exact JSON structure:
{{
  "suggestions": {{
    "<SYMBOL>": {{
      "replacements": [
        {{
          "symbol": "<TICKER>",
          "name": "<FULL_NAME>",
          "reason": "<WHY_NOT_SUBSTANTIALLY_IDENTICAL>"
        }}
      ],
      "explanation": "<PLAIN_ENGLISH_EXPLANATION_OF_WHY_TO_HARVEST>",
      "priority_reasoning": "<WHY_THIS_PRIORITY_VS_OTHERS>"
    }}
  }},
  "overall_strategy": "<BRIEF_OVERALL_HARVESTING_STRATEGY>",
  "disclaimer": "This analysis is for educational and simulation purposes only. It does not constitute financial, tax, or investment advice. Consult a qualified tax professional."
}}

Provide 2-3 replacement candidates per position. Ensure replacements are NOT substantially
identical to avoid wash-sale rule violations."""


async def get_ai_suggestions(
    positions_with_losses: list[dict],
) -> Optional[dict]:
    """
    Get AI-powered tax-loss harvesting suggestions from Google Gemini.

    Args:
        positions_with_losses: List of dicts with anonymized position data:
            [{"symbol": "AAPL", "quantity": 50, "unrealized_pnl": -500,
              "holding_period_days": 180, "is_long_term": false}, ...]

    Returns:
        Dict mapping symbol -> suggestion data, or None if AI is unavailable.
        Example: {"AAPL": {"replacements": [...], "explanation": "...", ...}}
    """
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        logger.warning(
            "GEMINI_API_KEY not set. Falling back to hardcoded replacement mappings."
        )
        return None

    if not positions_with_losses:
        return None

    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        prompt = _build_prompt(positions_with_losses)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.3,
                "max_output_tokens": 4096,
            },
        )

        if not response or not response.text:
            logger.warning("Gemini returned empty response")
            return None

        # Parse the JSON response
        response_text = response.text.strip()

        # Handle markdown code blocks in response
        if response_text.startswith("```"):
            # Remove ```json and ``` markers
            lines = response_text.split("\n")
            response_text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )

        parsed = json.loads(response_text)

        # Extract the suggestions dict
        if "suggestions" in parsed:
            logger.info(
                f"AI suggestions received for {len(parsed['suggestions'])} positions"
            )
            return parsed["suggestions"]
        else:
            logger.warning("AI response missing 'suggestions' key")
            return parsed

    except ImportError:
        logger.warning(
            "google-genai package not installed. "
            "Run: pip install google-genai. "
            "Falling back to hardcoded replacements."
        )
        return None

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return None

    except Exception as e:
        logger.error(f"AI advisor error: {e}")
        return None


def prepare_positions_for_ai(
    tax_lots: list,
) -> list[dict]:
    """
    Prepare anonymized position data for the AI advisor.

    Only sends: symbol, quantity, unrealized P&L, holding period, and
    short/long-term status. No user identity or account information.

    Args:
        tax_lots: List of TaxLot objects with computed metrics.

    Returns:
        List of anonymized position dicts for the AI prompt.
    """
    positions = []
    for lot in tax_lots:
        if lot.unrealized_pnl is not None and lot.unrealized_pnl < 0:
            positions.append(
                {
                    "symbol": lot.symbol,
                    "quantity": lot.quantity,
                    "unrealized_pnl": round(lot.unrealized_pnl, 2),
                    "cost_basis_per_share": round(lot.cost_basis_per_share, 2),
                    "current_price": lot.current_price,
                    "holding_period_days": lot.holding_period_days,
                    "is_long_term": lot.is_long_term,
                }
            )
    return positions
