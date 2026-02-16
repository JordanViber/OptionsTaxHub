"""
Tests for the AI advisor module (ai_advisor.py).

Google Gemini is mocked. Tests cover:
- Prompt building
- get_ai_suggestions (success, no API key, empty input, parsing errors)
- prepare_positions_for_ai filtering and anonymization
"""

import sys
import os
import json
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_advisor import _build_prompt, get_ai_suggestions, prepare_positions_for_ai
from models import TaxLot, AssetType
from datetime import date


# --- _build_prompt ---

class TestBuildPrompt:
    def test_includes_positions_json(self):
        positions = [{"symbol": "AAPL", "unrealized_pnl": -500}]
        prompt = _build_prompt(positions)
        assert "AAPL" in prompt
        assert "-500" in prompt

    def test_includes_json_structure_instructions(self):
        prompt = _build_prompt([])
        assert "suggestions" in prompt
        assert "replacements" in prompt

    def test_includes_disclaimer_instruction(self):
        prompt = _build_prompt([])
        assert "disclaimer" in prompt.lower() or "educational" in prompt.lower()


# --- get_ai_suggestions ---

class TestGetAiSuggestions:
    def test_returns_none_when_no_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        result = get_ai_suggestions([{"symbol": "AAPL", "unrealized_pnl": -500}])
        assert result is None

    def test_returns_none_for_empty_positions(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        result = get_ai_suggestions([])
        assert result is None

    def _make_mock_genai(self, response_text):
        """Helper to create a mock genai module with a preconfigured response."""
        mock_response = MagicMock()
        mock_response.text = response_text

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client
        return mock_genai

    def _run_with_mock(self, monkeypatch, response_text):
        """Helper to run get_ai_suggestions with a mocked genai module."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        mock_genai = self._make_mock_genai(response_text)

        # Patch sys.modules so 'from google import genai' picks up our mock
        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            # Need to reload ai_advisor to pick up the new mock
            import importlib
            import ai_advisor as ai_mod
            importlib.reload(ai_mod)
            return ai_mod.get_ai_suggestions([{"symbol": "AAPL", "unrealized_pnl": -500}])

    def test_success_with_suggestions_key(self, monkeypatch: pytest.MonkeyPatch):
        response_data = {
            "suggestions": {
                "AAPL": {
                    "replacements": [
                        {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF",
                         "reason": "Broad market exposure"}
                    ],
                    "explanation": "Harvest AAPL losses",
                    "priority_reasoning": "Large unrealized loss",
                }
            },
            "disclaimer": "Educational only",
        }
        result = self._run_with_mock(monkeypatch, json.dumps(response_data))
        assert result is not None
        assert "AAPL" in result

    def test_success_without_suggestions_key(self, monkeypatch: pytest.MonkeyPatch):
        response_data = {"AAPL": {"explanation": "harvest it"}}
        result = self._run_with_mock(monkeypatch, json.dumps(response_data))
        assert result is not None

    def test_empty_response(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            import importlib
            import ai_advisor as ai_mod
            importlib.reload(ai_mod)
            result = ai_mod.get_ai_suggestions([{"symbol": "AAPL", "unrealized_pnl": -500}])
            assert result is None

    def test_none_response(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = None

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            import importlib
            import ai_advisor as ai_mod
            importlib.reload(ai_mod)
            result = ai_mod.get_ai_suggestions([{"symbol": "AAPL", "unrealized_pnl": -500}])
            assert result is None

    def test_json_parse_error(self, monkeypatch: pytest.MonkeyPatch):
        result = self._run_with_mock(monkeypatch, "this is not valid json at all")
        assert result is None

    def test_markdown_code_block_stripped(self, monkeypatch: pytest.MonkeyPatch):
        response_data = {"suggestions": {"AAPL": {"explanation": "harvest"}}}
        wrapped = f"```json\n{json.dumps(response_data)}\n```"
        result = self._run_with_mock(monkeypatch, wrapped)
        assert result is not None
        assert "AAPL" in result

    def test_generic_exception(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_genai = MagicMock()
        mock_genai.Client.return_value.models.generate_content.side_effect = RuntimeError("API down")

        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            import importlib
            import ai_advisor as ai_mod
            importlib.reload(ai_mod)
            result = ai_mod.get_ai_suggestions([{"symbol": "AAPL", "unrealized_pnl": -500}])
            assert result is None


# --- prepare_positions_for_ai ---

class TestPreparePositionsForAi:
    def _make_lot(
        self,
        symbol: str = "AAPL",
        quantity: float = 10,
        cost_basis: float = 150.0,
        current_price: float = 140.0,
        unrealized_pnl: float = -100.0,
        holding_period_days: int = 90,
        is_long_term: bool = False,
    ) -> TaxLot:
        return TaxLot(
            symbol=symbol,
            quantity=quantity,
            cost_basis_per_share=cost_basis,
            total_cost_basis=cost_basis * quantity,
            purchase_date=date(2025, 1, 1),
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            holding_period_days=holding_period_days,
            is_long_term=is_long_term,
        )

    def test_filters_only_losses(self):
        lots = [
            self._make_lot(symbol="AAPL", unrealized_pnl=-500),
            self._make_lot(symbol="MSFT", unrealized_pnl=200),
            self._make_lot(symbol="TSLA", unrealized_pnl=-300),
        ]
        result = prepare_positions_for_ai(lots)
        assert len(result) == 2
        symbols = [p["symbol"] for p in result]
        assert "AAPL" in symbols
        assert "TSLA" in symbols
        assert "MSFT" not in symbols

    def test_skips_none_pnl(self):
        lot = self._make_lot(unrealized_pnl=-100.0)
        lot.unrealized_pnl = None
        result = prepare_positions_for_ai([lot])
        assert len(result) == 0

    def test_anonymized_fields(self):
        lots = [self._make_lot(
            symbol="AAPL", quantity=50, unrealized_pnl=-500,
            cost_basis=150.0, current_price=140.0,
            holding_period_days=180, is_long_term=False,
        )]
        result = prepare_positions_for_ai(lots)
        assert len(result) == 1
        pos = result[0]
        assert pos["symbol"] == "AAPL"
        assert pos["quantity"] == 50
        assert pos["unrealized_pnl"] == -500.0
        assert pos["cost_basis_per_share"] == 150
        assert pos["current_price"] == 140
        assert pos["holding_period_days"] == 180
        assert pos["is_long_term"] is False

    def test_empty_input(self):
        result = prepare_positions_for_ai([])
        assert result == []

    def test_zero_pnl_not_included(self):
        lot = self._make_lot(unrealized_pnl=0.0)
        result = prepare_positions_for_ai([lot])
        assert len(result) == 0
