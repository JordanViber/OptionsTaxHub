"""
Tests for the price service module (price_service.py).

All yfinance interactions are mocked. Tests cover:
- In-memory caching (hit, miss, expiry, clear)
- Single / multi ticker price extraction
- yfinance download with error handling
- Fallback price logic
- Top-level fetch_current_prices / fetch_single_price
"""

import sys
import os
import time
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import price_service
from price_service import (
    _get_cached_price,
    _set_cached_price,
    clear_cache,
    _resolve_cached_prices,
    _extract_single_ticker_price,
    _extract_multi_ticker_prices,
    _download_yfinance_prices,
    _apply_fallback_prices,
    fetch_current_prices,
    fetch_single_price,
    CACHE_TTL_SECONDS,
)


@pytest.fixture(autouse=True)
def _clear_price_cache():
    """Ensure a clean cache for every test."""
    clear_cache()
    yield
    clear_cache()


# --- Cache tests ---

class TestCaching:
    def test_set_and_get_cached_price(self):
        _set_cached_price("AAPL", 150.0)
        assert _get_cached_price("AAPL") == 150.0

    def test_get_returns_none_for_missing(self):
        assert _get_cached_price("ZZZZ") is None

    def test_cache_expired(self, monkeypatch):
        _set_cached_price("AAPL", 150.0)
        # Advance the time past TTL
        future = time.time() + CACHE_TTL_SECONDS + 1
        monkeypatch.setattr(time, "time", lambda: future)
        assert _get_cached_price("AAPL") is None

    def test_clear_cache(self):
        _set_cached_price("AAPL", 150.0)
        _set_cached_price("MSFT", 300.0)
        clear_cache()
        assert _get_cached_price("AAPL") is None
        assert _get_cached_price("MSFT") is None


# --- _resolve_cached_prices ---

class TestResolveCachedPrices:
    def test_all_cached(self):
        _set_cached_price("AAPL", 150.0)
        _set_cached_price("MSFT", 300.0)
        prices, to_fetch = _resolve_cached_prices(["AAPL", "MSFT"])
        assert prices == {"AAPL": 150.0, "MSFT": 300.0}
        assert to_fetch == []

    def test_none_cached(self):
        prices, to_fetch = _resolve_cached_prices(["AAPL", "MSFT"])
        assert prices == {}
        assert set(to_fetch) == {"AAPL", "MSFT"}

    def test_partial_cached(self):
        _set_cached_price("AAPL", 150.0)
        prices, to_fetch = _resolve_cached_prices(["aapl", "MSFT"])
        assert prices == {"AAPL": 150.0}
        assert to_fetch == ["MSFT"]


# --- _extract_single_ticker_price ---

class TestExtractSingleTickerPrice:
    def test_valid_close(self):
        df = pd.DataFrame({"Close": [100.0, 105.0, 110.456]})
        assert _extract_single_ticker_price(df, "AAPL") == 110.46

    def test_no_close_column(self):
        df = pd.DataFrame({"Open": [100.0]})
        assert _extract_single_ticker_price(df, "AAPL") is None

    def test_empty_close(self):
        df = pd.DataFrame({"Close": pd.Series([], dtype=float)})
        assert _extract_single_ticker_price(df, "AAPL") is None


# --- _extract_multi_ticker_prices ---

class TestExtractMultiTickerPrices:
    def test_extracts_multiple(self):
        arrays = [["Close", "Close", "Open", "Open"],
                  ["AAPL", "MSFT", "AAPL", "MSFT"]]
        tuples = list(zip(*arrays))
        idx = pd.MultiIndex.from_tuples(tuples)
        data = [[150.0, 300.0, 148.0, 298.0],
                [151.0, 301.0, 149.0, 299.0]]
        df = pd.DataFrame(data, columns=idx)

        prices = _extract_multi_ticker_prices(df, ["AAPL", "MSFT"])
        assert prices["AAPL"] == 151.0
        assert prices["MSFT"] == 301.0

    def test_no_close_level(self):
        arrays = [["Open", "Open"], ["AAPL", "MSFT"]]
        tuples = list(zip(*arrays))
        idx = pd.MultiIndex.from_tuples(tuples)
        df = pd.DataFrame([[1, 2]], columns=idx)
        prices = _extract_multi_ticker_prices(df, ["AAPL"])
        assert prices == {}

    def test_symbol_not_in_close(self):
        arrays = [["Close", "Close"], ["AAPL", "MSFT"]]
        tuples = list(zip(*arrays))
        idx = pd.MultiIndex.from_tuples(tuples)
        df = pd.DataFrame([[150.0, 300.0]], columns=idx)
        prices = _extract_multi_ticker_prices(df, ["TSLA"])
        assert prices == {}

    def test_symbol_with_empty_series(self):
        arrays = [["Close", "Close"], ["AAPL", "MSFT"]]
        tuples = list(zip(*arrays))
        idx = pd.MultiIndex.from_tuples(tuples)
        df = pd.DataFrame([[150.0, np.nan]], columns=idx)
        prices = _extract_multi_ticker_prices(df, ["AAPL", "MSFT"])
        assert "AAPL" in prices
        assert "MSFT" not in prices  # NaN series dropna → empty


# --- _download_yfinance_prices ---

class TestDownloadYfinancePrices:
    def test_single_ticker_success(self):
        """yfinance downloads single ticker correctly."""
        fake_df = pd.DataFrame({"Close": [155.55]})
        mock_yf = MagicMock()
        mock_yf.download.return_value = fake_df

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            import importlib
            importlib.reload(price_service)
            prices, warnings = price_service._download_yfinance_prices(["AAPL"])
            assert prices.get("AAPL") == 155.55
            assert len(warnings) == 0

    def test_multi_ticker_success(self):
        """yfinance downloads multiple tickers correctly."""
        arrays = [["Close", "Close"], ["AAPL", "MSFT"]]
        tuples = list(zip(*arrays))
        idx = pd.MultiIndex.from_tuples(tuples)
        data = [[150.0, 300.0], [151.0, 301.0]]
        fake_df = pd.DataFrame(data, columns=idx)

        mock_yf = MagicMock()
        mock_yf.download.return_value = fake_df

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            import importlib
            importlib.reload(price_service)
            prices, warnings = price_service._download_yfinance_prices(["AAPL", "MSFT"])
            assert prices.get("AAPL") == 151.0
            assert prices.get("MSFT") == 301.0

    def test_empty_data_returns_warning(self):
        """yfinance returning empty DataFrame produces a warning."""
        mock_yf = MagicMock()
        mock_yf.download.return_value = pd.DataFrame()

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            import importlib
            importlib.reload(price_service)
            prices, warnings = price_service._download_yfinance_prices(["AAPL"])
            assert prices == {}
            assert any("no data" in w.lower() for w in warnings)

    def test_import_error_yfinance(self):
        """When yfinance is not installed, should warn gracefully."""
        # Remove yfinance from sys.modules to force reimport
        with patch.dict("sys.modules", {"yfinance": None}):
            import importlib
            importlib.reload(price_service)
            prices, warnings = price_service._download_yfinance_prices(["AAPL"])
            assert prices == {}
            assert any("not installed" in w for w in warnings)

    def test_generic_exception(self):
        """When yfinance raises a generic exception, should warn gracefully."""
        mock_yf = MagicMock()
        mock_yf.download.side_effect = RuntimeError("network error")

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            import importlib
            importlib.reload(price_service)
            prices, warnings = price_service._download_yfinance_prices(["AAPL"])
            assert prices == {}
            assert len(warnings) >= 1


# --- _apply_fallback_prices ---

class TestApplyFallbackPrices:
    def test_fills_missing_from_fallback(self):
        prices: dict[str, float] = {"AAPL": 150.0}
        fallback = {"MSFT": 300.0, "TSLA": 200.0}
        warnings = _apply_fallback_prices(["AAPL", "MSFT", "TSLA"], prices, fallback)
        assert prices["MSFT"] == 300.0
        assert prices["TSLA"] == 200.0
        assert any("MSFT" in w for w in warnings)
        assert any("TSLA" in w for w in warnings)

    def test_no_fallback_warns_missing(self):
        prices: dict[str, float] = {}
        warnings = _apply_fallback_prices(["AAPL"], prices, None)
        assert any("AAPL" in w for w in warnings)

    def test_no_missing_no_warnings(self):
        prices: dict[str, float] = {"AAPL": 150.0}
        warnings = _apply_fallback_prices(["AAPL"], prices, None)
        assert len(warnings) == 0

    def test_fallback_none(self):
        prices: dict[str, float] = {}
        warnings = _apply_fallback_prices(["AAPL"], prices, None)
        assert any("No prices available" in w for w in warnings)

    def test_partial_fallback(self):
        prices: dict[str, float] = {}
        fallback = {"AAPL": 150.0}
        warnings = _apply_fallback_prices(["AAPL", "MSFT"], prices, fallback)
        assert prices["AAPL"] == 150.0
        assert any("MSFT" in w for w in warnings)


# --- fetch_current_prices ---

class TestFetchCurrentPrices:
    def test_empty_symbols(self):
        prices, warnings = fetch_current_prices([])
        assert prices == {}
        assert warnings == []

    def test_all_cached(self):
        _set_cached_price("AAPL", 150.0)
        _set_cached_price("MSFT", 300.0)
        prices, warnings = fetch_current_prices(["AAPL", "MSFT"])
        assert prices == {"AAPL": 150.0, "MSFT": 300.0}
        assert warnings == []

    def test_uses_fallback_when_download_fails(self):
        with patch("price_service._download_yfinance_prices", return_value=({}, ["yfinance error"])):
            prices, warnings = fetch_current_prices(
                ["AAPL"],
                fallback_prices={"AAPL": 145.0},
            )
            assert prices["AAPL"] == 145.0
            assert any("yfinance" in w for w in warnings)

    def test_merges_cached_and_fetched(self):
        _set_cached_price("AAPL", 150.0)
        with patch("price_service._download_yfinance_prices", return_value=({"MSFT": 310.0}, [])):
            prices, warnings = fetch_current_prices(["AAPL", "MSFT"])
            assert prices["AAPL"] == 150.0
            assert prices["MSFT"] == 310.0


# --- fetch_single_price ---

class TestFetchSinglePrice:
    def test_returns_price(self):
        with patch("price_service.fetch_current_prices", return_value=({"AAPL": 155.0}, [])):
            assert fetch_single_price("AAPL") == 155.0

    def test_returns_none_when_missing(self):
        with patch("price_service.fetch_current_prices", return_value=({}, ["not found"])):
            assert fetch_single_price("AAPL") is None
