"""
Tests for the database client module (db.py).

All Supabase interactions are mocked to avoid real database calls.
Covers save/get/delete operations for portfolio analyses and tax profiles.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db


@pytest.fixture(autouse=True)
def _reset_supabase_client():
    """Reset the singleton client between tests."""
    db._supabase_client = None
    yield
    db._supabase_client = None


class _FakeExecuteResult:
    """Mimics the Supabase execute() result."""

    def __init__(self, data=None):
        self.data = data or []


class _FakeQueryBuilder:
    """Fake query builder that records chained method calls."""

    def __init__(self, data=None):
        self._data = data

    def insert(self, row):
        return self

    def select(self, *args):
        return self

    def upsert(self, row, **kwargs):
        return self

    def delete(self):
        return self

    def eq(self, *args):
        return self

    def is_(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        return _FakeExecuteResult(self._data)


class _FakeClient:
    """Minimal fake Supabase client."""

    def __init__(self, table_data=None):
        self._table_data = table_data

    def table(self, name):
        return _FakeQueryBuilder(self._table_data)


# --- get_supabase ---

class TestGetSupabase:
    def test_returns_none_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        result = db.get_supabase()
        assert result is None

    def test_returns_none_when_url_only(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        result = db.get_supabase()
        assert result is None

    def test_returns_cached_client(self):
        fake = _FakeClient()
        db._supabase_client = fake
        result = db.get_supabase()
        assert result is fake

    def test_handles_create_client_exception(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

        # Patch sys.modules so 'from supabase import create_client' inside
        # get_supabase() picks up our mock that raises an exception.
        mock_supabase_module = MagicMock()
        mock_supabase_module.create_client.side_effect = Exception("connection failed")
        with patch.dict("sys.modules", {"supabase": mock_supabase_module}):
            db._supabase_client = None
            result = db.get_supabase()
            assert result is None


# --- save_analysis_history ---

class TestSaveAnalysisHistory:
    def test_returns_none_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.save_analysis_history("user1", "test.csv", {"positions_count": 5})
        assert result is None

    def test_saves_successfully(self, monkeypatch):
        saved_row = {"id": "abc-123", "user_id": "user1", "filename": "test.csv"}
        client = _FakeClient(table_data=[saved_row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.save_analysis_history("user1", "test.csv", {"positions_count": 5})
        assert result == saved_row

    def test_saves_with_result_data(self, monkeypatch):
        saved_row = {"id": "abc-123", "user_id": "user1"}
        client = _FakeClient(table_data=[saved_row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.save_analysis_history(
            "user1", "test.csv",
            {"positions_count": 5},
            result_data={"positions": []},
        )
        assert result == saved_row

    def test_returns_none_on_empty_data(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.save_analysis_history("user1", "test.csv", {"positions_count": 5})
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.insert.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.save_analysis_history("user1", "test.csv", {})
        assert result is None


# --- get_analysis_history ---

class TestGetAnalysisHistory:
    def test_returns_empty_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.get_analysis_history("user1")
        assert result == []

    def test_returns_history(self, monkeypatch):
        rows = [
            {"id": "h1", "filename": "a.csv"},
            {"id": "h2", "filename": "b.csv"},
        ]
        client = _FakeClient(table_data=rows)
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.get_analysis_history("user1", limit=10)
        assert len(result) == 2

    def test_returns_empty_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value \
            .order.return_value.limit.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.get_analysis_history("user1")
        assert result == []


# --- get_analysis_by_id ---

class TestGetAnalysisById:
    def test_returns_none_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.get_analysis_by_id("abc", "user1")
        assert result is None

    def test_returns_record(self, monkeypatch):
        row = {"id": "abc", "user_id": "user1", "result": {"positions": []}}
        client = _FakeClient(table_data=[row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.get_analysis_by_id("abc", "user1")
        assert result == row

    def test_returns_none_when_not_found(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.get_analysis_by_id("nonexistent", "user1")
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.limit.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.get_analysis_by_id("abc", "user1")
        assert result is None


# --- delete_analyses_without_result ---

class TestDeleteAnalysesWithoutResult:
    def test_returns_zero_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.delete_analyses_without_result("user1")
        assert result == 0

    def test_returns_count(self, monkeypatch):
        deleted_rows = [{"id": "d1"}, {"id": "d2"}, {"id": "d3"}]
        client = _FakeClient(table_data=deleted_rows)
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.delete_analyses_without_result("user1")
        assert result == 3

    def test_returns_zero_when_none_deleted(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.delete_analyses_without_result("user1")
        assert result == 0

    def test_returns_zero_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.delete.return_value.eq.return_value \
            .is_.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.delete_analyses_without_result("user1")
        assert result == 0


# --- delete_analysis_by_id ---

class TestDeleteAnalysisById:
    def test_returns_false_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.delete_analysis_by_id("abc", "user1")
        assert result is False

    def test_returns_true_on_success(self, monkeypatch):
        client = _FakeClient(table_data=[{"id": "abc"}])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.delete_analysis_by_id("abc", "user1")
        assert result is True

    def test_returns_false_when_not_found(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.delete_analysis_by_id("nonexistent", "user1")
        assert result is False

    def test_returns_false_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.delete.return_value.eq.return_value \
            .eq.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.delete_analysis_by_id("abc", "user1")
        assert result is False


# --- save_tax_profile ---

class TestSaveTaxProfile:
    def test_returns_none_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.save_tax_profile("user1", "single", 100000, "CA", 2025)
        assert result is None

    def test_saves_successfully(self, monkeypatch):
        saved_row = {
            "user_id": "user1",
            "filing_status": "single",
            "estimated_annual_income": 100000,
        }
        client = _FakeClient(table_data=[saved_row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.save_tax_profile("user1", "single", 100000, "CA", 2025)
        assert result == saved_row

    def test_returns_none_on_empty_result(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.save_tax_profile("user1", "single", 100000, "CA", 2025)
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.upsert.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.save_tax_profile("user1", "single", 100000, "CA", 2025)
        assert result is None


# --- get_tax_profile ---

class TestGetTaxProfile:
    def test_returns_none_when_no_client(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        result = db.get_tax_profile("user1")
        assert result is None

    def test_returns_profile(self, monkeypatch):
        row = {"user_id": "user1", "filing_status": "single", "estimated_annual_income": 100000}
        client = _FakeClient(table_data=[row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.get_tax_profile("user1")
        assert result == row

    def test_returns_none_when_not_found(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.get_tax_profile("user1")
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value \
            .limit.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.get_tax_profile("user1")
        assert result is None
