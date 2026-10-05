"""
Tests for the database client module (db.py).

All Supabase interactions are mocked to avoid real database calls.
Covers save/get/delete operations for portfolio analyses and tax profiles.
"""

import sys
import os
from functools import cmp_to_key
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
        self.calls = []

    def insert(self, row):
        self.calls.append(("insert", (row,), {}))
        return self

    def select(self, *args):
        return self

    def upsert(self, row, **kwargs):
        self.calls.append(("upsert", (row,), kwargs))
        return self

    def delete(self):
        return self

    def update(self, row):
        self.calls.append(("update", (row,), {}))
        return self

    def eq(self, *args):
        self.calls.append(("eq", args, {}))
        return self

    def contains(self, *args):
        self.calls.append(("contains", args, {}))
        return self

    def is_(self, *args, **kwargs):
        self.calls.append(("is_", args, kwargs))
        return self

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return self

    def gt(self, *args):
        self.calls.append(("gt", args, {}))
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def limit(self, *args):
        return self

    def range(self, *args):
        return self

    def execute(self):
        return _FakeExecuteResult(self._data)


class _FakeClient:
    """Minimal fake Supabase client."""

    def __init__(self, table_data=None, table_responses=None):
        self._table_data = table_data
        self._table_responses = list(table_responses) if table_responses is not None else None
        self.builders = []

    def table(self, name):
        if self._table_responses is not None and self._table_responses:
            data = self._table_responses.pop(0)
        else:
            data = self._table_data
        builder = _FakeQueryBuilder(data)
        self.builders.append(builder)
        return builder


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

    def test_returns_none_when_postgrest_omits_insert_representation(self, monkeypatch):
        client = _FakeClient(table_data=[])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        result = db.save_analysis_history("user1", "test.csv", {"positions_count": 5})
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.table.return_value.insert.return_value.select.return_value.execute.side_effect = Exception("db error")
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.save_analysis_history("user1", "test.csv", {})
        assert result is None

    def test_returns_none_on_permission_error(self, monkeypatch):
        mock_client = MagicMock()
        error = Exception("permission denied for table portfolio_analyses")
        error.code = "42501"
        mock_client.table.return_value.insert.return_value.select.return_value.execute.side_effect = error
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        result = db.save_analysis_history(
            "user1", "test.csv", {}, result_data={"analysis_id": "analysis-1"}
        )
        assert result is None

    def test_raises_when_result_column_is_missing(self, monkeypatch):
        mock_client = MagicMock()
        error = Exception(
            'column "result" of relation "portfolio_analyses" does not exist'
        )
        error.code = "42703"
        mock_client.table.return_value.insert.return_value.select.return_value.execute.side_effect = error
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        with pytest.raises(db.AnalysisSchemaError, match="server/migrations"):
            db.save_analysis_history(
                "user1",
                "test.csv",
                {"positions_count": 1},
                result_data={"analysis_id": "analysis-1"},
            )

    def test_raises_when_table_is_missing(self, monkeypatch):
        mock_client = MagicMock()
        error = Exception('relation "portfolio_analyses" does not exist')
        error.sqlstate = "42P01"
        mock_client.table.return_value.insert.return_value.select.return_value.execute.side_effect = error
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        with pytest.raises(db.AnalysisSchemaError, match="server/migrations"):
            db.save_analysis_history("user1", "test.csv", {})

    def test_raises_when_postgrest_schema_cache_misses_result(self, monkeypatch):
        mock_client = MagicMock()
        error = Exception(
            "Could not find the 'result' column of 'portfolio_analyses' in the schema cache"
        )
        error.code = "PGRST204"
        mock_client.table.return_value.insert.return_value.select.return_value.execute.side_effect = error
        monkeypatch.setattr(db, "get_supabase", lambda: mock_client)
        with pytest.raises(db.AnalysisSchemaError, match="result"):
            db.save_analysis_history(
                "user1",
                "test.csv",
                {},
                result_data={"analysis_id": "analysis-1"},
            )


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


class TestPatchAnalysisResult:
    def test_updates_row_found_by_embedded_analysis_id(self, monkeypatch):
        record = {
            "id": "history-row-uuid",
            "user_id": "user1",
            "result": {"analysis_id": "00000000-0000-4000-8000-000000000001", "packet_unlocked": False},
        }
        builder = MagicMock()
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.return_value.data = [record]
        builder.update.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = [record]
        client = MagicMock()
        client.table.return_value = builder
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.patch_analysis_result(
            "00000000-0000-4000-8000-000000000001", "user1", {"packet_unlocked": True}
        ) is True
        builder.update.assert_called_once_with(
            {"result": {"analysis_id": "00000000-0000-4000-8000-000000000001", "packet_unlocked": True}}
        )
        builder.update.return_value.eq.assert_called_once_with("id", "history-row-uuid")
        builder.update.return_value.eq.return_value.eq.return_value.select.assert_called_once_with("id")
        builder.select.return_value.eq.assert_any_call(
            "id", "00000000-0000-4000-8000-000000000001"
        )
        builder.select.return_value.eq.assert_any_call("user_id", "user1")
        builder.select.return_value.eq.return_value.contains.assert_called_once_with(
            "result", {"analysis_id": "00000000-0000-4000-8000-000000000001"}
        )
        builder.select.return_value.eq.return_value.contains.return_value.order.assert_called_once_with(
            "uploaded_at", desc=True
        )
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.assert_called_once_with(
            1
        )

    def test_requests_a_row_representation_when_patching(self, monkeypatch):
        record = {"id": "row-id", "user_id": "user1", "result": {"analysis_id": "00000000-0000-4000-8000-000000000001"}}
        builder = MagicMock()
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [record]
        builder.update.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []
        client = MagicMock()
        client.table.return_value = builder
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.patch_analysis_result("00000000-0000-4000-8000-000000000001", "user1", {"packet_unlocked": True}) is False
        builder.update.return_value.eq.return_value.eq.return_value.select.assert_called_once_with("id")

    def test_does_not_patch_embedded_analysis_from_another_user(self, monkeypatch):
        builder = MagicMock()
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        client = MagicMock()
        client.table.return_value = builder
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.patch_analysis_result(
            "00000000-0000-4000-8000-000000000001", "user1", {"packet_unlocked": True}
        ) is False
        builder.select.return_value.eq.assert_any_call("user_id", "user1")
        builder.select.return_value.eq.return_value.contains.assert_called_once_with(
            "result", {"analysis_id": "00000000-0000-4000-8000-000000000001"}
        )
        builder.select.return_value.eq.return_value.contains.return_value.order.assert_called_once_with(
            "uploaded_at", desc=True
        )
        builder.update.assert_not_called()

    def test_returns_none_when_supabase_lookup_raises(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.side_effect = RuntimeError("network")
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.patch_analysis_result(
            "00000000-0000-4000-8000-000000000001", "user1", {"packet_unlocked": True}
        ) is None


class TestEnsureAnalysisHistory:
    def test_reuses_existing_row_found_by_embedded_analysis_id(self, monkeypatch):
        record = {"id": "history-row", "user_id": "user1"}
        client = MagicMock()
        builder = client.table.return_value
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.return_value.data = [record]
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        monkeypatch.setattr(
            db,
            "save_analysis_history",
            lambda *_args, **_kwargs: pytest.fail("existing row must not be duplicated"),
        )

        assert db.ensure_analysis_history("00000000-0000-4000-8000-000000000001", "user1", None) == record
        builder.select.return_value.eq.return_value.contains.assert_called_once_with(
            "result", {"analysis_id": "00000000-0000-4000-8000-000000000001"}
        )
        builder.select.return_value.eq.assert_any_call("user_id", "user1")
        builder.select.return_value.eq.return_value.contains.return_value.order.assert_called_once_with(
            "uploaded_at", desc=True
        )
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.assert_called_once_with(
            1
        )

    def test_inserts_matching_analysis_when_history_row_is_missing(self, monkeypatch):
        inserted = {"id": "history-row", "user_id": "user1"}
        client = MagicMock()
        builder = client.table.return_value
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        saved = {}

        def save(user_id, filename, summary, result_data=None):
            saved.update(user_id=user_id, filename=filename, summary=summary, result=result_data)
            return inserted

        monkeypatch.setattr(db, "save_analysis_history", save)
        analysis = {
            "analysis_id": "analysis-uuid",
            "summary": {"positions_count": 2},
            "packet_unlocked": True,
            "packet_session_id": "cs_test_forged",
        }

        assert db.ensure_analysis_history("analysis-uuid", "user1", analysis) == inserted
        assert saved == {
            "user_id": "user1",
            "filename": "year-close-packet.csv",
            "summary": {"positions_count": 2},
            "result": {
                "analysis_id": "analysis-uuid",
                "summary": {"positions_count": 2},
            },
        }

    def test_drops_client_transactions_before_insert(self, monkeypatch):
        inserted = {"id": "history-row", "user_id": "user1"}
        client = MagicMock()
        builder = client.table.return_value
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        saved = {}

        def save(user_id, filename, summary, result_data=None):
            saved["result"] = result_data
            saved["summary"] = summary
            return inserted

        monkeypatch.setattr(db, "save_analysis_history", save)
        analysis = {
            "analysis_id": "analysis-uuid",
            "summary": {"positions_count": 1, "activity_transaction_count": 3},
            "transactions": [{"instrument": "AAPL", "trans_code": "Buy"}],
            "activity_book": {
                "transaction_count": 1,
                "transactions": [{"instrument": "AAPL", "trans_code": "Buy"}],
            },
            "packet_unlocked": True,
        }

        assert db.ensure_analysis_history("analysis-uuid", "user1", analysis) == inserted
        assert saved["result"]["activity_book"]["transactions"] == []
        assert saved["result"]["activity_book"]["transaction_count"] == 0
        assert saved["result"]["summary"]["activity_transaction_count"] == 0
        assert saved["summary"]["activity_transaction_count"] == 0
        assert "transactions" not in saved["result"]
        assert "packet_unlocked" not in saved["result"]
        assert analysis["activity_book"]["transaction_count"] == 1
        assert analysis["summary"]["activity_transaction_count"] == 3

    def test_refuses_to_insert_mismatched_analysis(self, monkeypatch):
        client = MagicMock()
        builder = client.table.return_value
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        monkeypatch.setattr(
            db,
            "save_analysis_history",
            lambda *_args, **_kwargs: pytest.fail("mismatched analysis must not be saved"),
        )

        assert db.ensure_analysis_history(
            "analysis-uuid", "user1", {"analysis_id": "different-analysis"}
        ) is None

    def test_does_not_insert_when_history_lookup_errors(self, monkeypatch):
        client = MagicMock()
        builder = client.table.return_value
        builder.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        builder.select.return_value.eq.return_value.contains.return_value.order.return_value.limit.return_value.execute.side_effect = RuntimeError("PostgREST failure")
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        monkeypatch.setattr(
            db,
            "save_analysis_history",
            lambda *_args, **_kwargs: pytest.fail("lookup failure must not duplicate the analysis"),
        )

        assert db.ensure_analysis_history(
            "analysis-uuid", "user1", {"analysis_id": "analysis-uuid"}
        ) is None

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


class TestLatestActivityBook:
    def test_skips_runs_without_a_trade_book(self, monkeypatch):
        rows = [
            {
                "id": "new",
                "filename": "snapshot.csv",
                "result": {"positions": []},
            },
            {
                "id": "book-1",
                "filename": "full.csv",
                "result": {
                    "activity_book": {
                        "transactions": [{"instrument": "AAPL", "trans_code": "Buy"}]
                    },
                    "packet_unlocked": True,
                    "packet_session_id": "cs_abc",
                    "tax_profile": {"tax_year": 2026},
                },
            },
        ]
        client = _FakeClient(table_responses=[[], rows])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        book = db.get_latest_activity_book("user1")
        assert book["analysis_id"] == "book-1"
        assert "packet_unlocked" not in book
        assert "packet_session_id" not in book
        assert len(book["transactions"]) == 1


class TestPacketSnapshots:
    def test_saves_private_packet_snapshot_with_short_unpaid_retention(self, monkeypatch):
        client = _FakeClient(
            table_responses=[[], [{"analysis_id": "analysis-a", "user_id": "user1"}]],
        )
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2026,
            {"lot_match_report": {"matched": [{"symbol": "AMD"}]}},
        )

        assert saved["analysis_id"] == "analysis-a"
        calls = client.builders[1].calls
        insert = next(call for call in calls if call[0] == "insert")
        assert insert[1][0]["paid_at"] is None
        assert insert[1][0]["tax_year"] == 2026
        assert insert[1][0]["expires_at"] is not None

    def test_paid_packet_snapshot_has_no_expiry(self, monkeypatch):
        client = _FakeClient(
            table_responses=[[], [{"analysis_id": "analysis-a", "user_id": "user1"}]],
        )
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2026,
            {"lot_match_report": {"matched": [{"symbol": "AMD"}]}},
            session_id="cs_paid",
            paid=True,
        )

        assert saved["analysis_id"] == "analysis-a"
        insert = next(call for call in client.builders[1].calls if call[0] == "insert")
        assert insert[1][0]["paid_at"] is not None
        assert insert[1][0]["expires_at"] is None

    def test_unpaid_snapshot_update_preserves_paid_document_and_tax_year(self, monkeypatch):
        existing = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2025,
            "packet_payload": {"report": "original"},
            "packet_session_id": "cs_paid_2025",
            "paid_at": "2026-09-29T00:00:00+00:00",
        }
        client = _FakeClient(table_data=[existing])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2025,
            {"report": "client-replacement"},
        )

        assert len(client.builders) == 1
        assert saved["analysis_id"] == "analysis-a"

    def test_paid_snapshot_cannot_be_moved_to_another_tax_year(self, monkeypatch):
        existing = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2025,
            "packet_payload": {"report": "original"},
            "packet_session_id": "cs_paid_2025",
            "paid_at": "2026-09-29T00:00:00+00:00",
        }
        client = _FakeClient(table_data=[existing])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2026,
            {"report": "wrong-year"},
        )

        assert saved is None
        assert len(client.builders) == 1

    def test_paid_snapshot_with_deleted_payload_can_be_refilled(self, monkeypatch):
        existing = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2025,
            "packet_payload": None,
            "packet_session_id": "cs_paid_2025",
            "paid_at": "2026-09-29T00:00:00+00:00",
        }
        client = _FakeClient(table_data=[existing])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2025,
            {"report": "restored"},
        )

        update = next(call for call in client.builders[1].calls if call[0] == "update")
        row = update[1][0]
        assert row["packet_payload"] == {"report": "restored"}
        assert row["packet_session_id"] == "cs_paid_2025"
        assert row["paid_at"] == existing["paid_at"]
        assert ("filter", ("paid_at", "not.is", "null"), {}) in client.builders[1].calls
        assert ("is_", ("packet_payload", "null"), {}) in client.builders[1].calls
        assert not any(call[0] == "upsert" for call in client.builders[1].calls)
        assert saved["analysis_id"] == "analysis-a"

    def test_conditional_repair_does_not_clobber_payload_written_after_read(self, monkeypatch):
        stale = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2025,
            "packet_payload": None,
            "packet_session_id": "cs_paid",
            "paid_at": "2026-09-29T00:00:00+00:00",
        }
        fresh = {
            **stale,
            "packet_payload": {"lot_match_report": {"matched": [{"symbol": "AMD"}]}},
        }

        class _RepairClient:
            def __init__(self):
                self.reads = 0
                self.updates = []
                self.filters = []
                self.mode = "read"

            def table(self, _name):
                self.mode = "read"
                return self

            def select(self, *_args):
                return self

            def eq(self, *_args):
                return self

            def limit(self, *_args):
                return self

            def filter(self, *args):
                self.filters.append(args)
                return self

            def is_(self, *args):
                self.filters.append(("is_", args))
                return self

            def update(self, row):
                self.mode = "update"
                self.updates.append(row)
                return self

            def insert(self, _row):
                raise AssertionError("repair must not insert over a paid row")

            def upsert(self, *_args, **_kwargs):
                raise AssertionError("repair must not upsert over a paid row")

            def execute(self):
                if self.mode == "update":
                    self.mode = "read"
                    return _FakeExecuteResult([])
                self.reads += 1
                row = stale if self.reads == 1 else fresh
                return _FakeExecuteResult([row])

        client = _RepairClient()
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2025,
            {"lot_match_report": {"matched": [{"symbol": "CLOBBER"}]}},
        )

        assert saved == {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2025,
        }
        assert len(client.updates) == 1
        assert client.updates[0]["packet_payload"]["lot_match_report"]["matched"] == [
            {"symbol": "CLOBBER"}
        ]
        assert ("paid_at", "not.is", "null") in client.filters
        assert ("is_", ("packet_payload", "null")) in client.filters
        assert fresh["packet_payload"]["lot_match_report"]["matched"] == [
            {"symbol": "AMD"}
        ]

    def test_racing_unpaid_snapshot_write_cannot_downgrade_a_paid_row(self, monkeypatch):
        unpaid = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2025,
            "packet_payload": {"report": "private"},
            "packet_session_id": None,
            "paid_at": None,
            "expires_at": "2026-09-30T00:00:00+00:00",
        }
        paid = {
            **unpaid,
            "packet_session_id": "cs_paid",
            "paid_at": "2026-09-30T00:01:00+00:00",
            "expires_at": None,
        }

        class RacingClient:
            def __init__(self):
                self.reads = 0
                self.updated_row = None
                self.update_filters = []
                self.upserts = []

            def table(self, _name):
                return self

            def select(self, *_args):
                return self

            def eq(self, *_args):
                return self

            def limit(self, *_args):
                return self

            def is_(self, *args):
                self.update_filters.append(args)
                return self

            def update(self, row):
                self.updated_row = row
                return self

            def upsert(self, row, **_kwargs):
                self.upserts.append(row)
                return self

            def insert(self, _row):
                raise AssertionError("existing snapshot should not be inserted")

            def execute(self):
                if self.updated_row is not None:
                    self.updated_row = None
                    self.reads += 1
                    return _FakeExecuteResult([])
                self.reads += 1
                return _FakeExecuteResult([unpaid] if self.reads == 1 else [paid])

        client = RacingClient()
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2025,
            {"report": "private"},
        )

        assert saved["analysis_id"] == "analysis-a"
        assert client.update_filters == [("paid_at", "null")]
        assert client.upserts == []
        assert paid["paid_at"] is not None
        assert paid["packet_session_id"] == "cs_paid"

    def test_loads_owner_scoped_paid_packet_snapshot_without_expiry(self, monkeypatch):
        row = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2026,
            "packet_payload": {"lot_match_report": {"matched": []}},
            "packet_session_id": "cs_paid",
            "paid_at": "2026-09-29T00:00:00+00:00",
            "expires_at": None,
        }
        client = _FakeClient(table_data=[row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        snapshot, lookup_succeeded = db.get_packet_snapshot("analysis-a", "user1")

        assert lookup_succeeded is True
        assert snapshot == row
        calls = client.builders[0].calls
        assert ("eq", ("analysis_id", "analysis-a"), {}) in calls
        assert ("eq", ("user_id", "user1"), {}) in calls
        assert not any(call[0] == "gt" and call[1][0] == "expires_at" for call in calls)

    def test_does_not_load_expired_unpaid_snapshot(self, monkeypatch):
        row = {
            "analysis_id": "analysis-a",
            "user_id": "user1",
            "tax_year": 2026,
            "packet_payload": {"lot_match_report": {"matched": []}},
            "paid_at": None,
            "expires_at": "2000-01-01T00:00:00+00:00",
        }
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeClient(table_data=[row]))
        assert db.get_packet_snapshot("analysis-a", "user1") == (None, True)

    def test_packet_year_lookup_uses_owner_scoped_paid_snapshot_first(self, monkeypatch):
        paid = {
            "analysis_id": "analysis-a",
            "packet_payload": {"lot_match_report": {"matched": []}},
            "packet_session_id": "cs_paid_year",
            "paid_at": "2026-09-29T00:00:00+00:00",
        }
        client = _FakeClient(table_data=[paid])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.lookup_packet_grant_for_tax_year("user1", 2026) == (
            "cs_paid_year",
            True,
        )
        calls = client.builders[0].calls
        assert ("eq", ("user_id", "user1"), {}) in calls
        assert ("eq", ("tax_year", 2026), {}) in calls
        assert len(client.builders) == 1

    def test_paid_snapshot_update_reports_database_failure(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.side_effect = RuntimeError("offline")
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.mark_packet_snapshot_paid(
            "analysis-a", "user1", 2026, "cs_paid_year"
        ) is None

    def test_paid_snapshot_update_removes_expiration(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = [
            {"analysis_id": "analysis-a"}
        ]
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.mark_packet_snapshot_paid(
            "analysis-a", "user1", 2026, "cs_paid_year"
        ) is True
        update = client.table.return_value.update.call_args.args[0]
        assert update["expires_at"] is None

    def test_packet_grant_ignores_client_writable_history_flags(self, monkeypatch):
        rows = [{
            "id": "forged-history-row",
            "result": {
                "packet_unlocked": True,
                "packet_session_id": "cs_forged",
                "tax_profile": {"tax_year": 2026},
            },
        }]
        client = _FakeClient(table_data=rows)
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        assert db.lookup_packet_grant_for_tax_year("user1", 2026) == (None, True)
        assert len(client.builders) == 1
        calls = client.builders[0].calls
        assert ("eq", ("user_id", "user1"), {}) in calls
        assert ("eq", ("tax_year", 2026), {}) in calls
        assert not any(call[0] == "contains" for call in calls)

    def test_packet_grant_keeps_receipt_when_source_document_is_missing(self, monkeypatch):
        client = _FakeClient(table_data=[{
            "analysis_id": "analysis-a",
            "packet_session_id": "cs_paid_year",
            "packet_payload": None,
            "paid_at": "2026-09-29T00:00:00+00:00",
        }])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        assert db.lookup_packet_grant_for_tax_year("user1", 2026) == (
            "cs_paid_year",
            True,
        )

    def test_separate_year_entitlement_survives_deleted_document(self, monkeypatch):
        row = {
            "analysis_id": "analysis-a",
            "packet_session_id": "cs_paid_year",
            "tax_year": 2026,
        }
        client = _FakeClient(table_data=[row])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        entitlement, lookup_succeeded = db.lookup_packet_entitlement_for_tax_year(
            "user1", 2026
        )

        assert lookup_succeeded is True
        assert entitlement == row
        assert client.builders[0].calls[0] == (
            "eq", ("user_id", "user1"), {}
        )
        assert client.builders[0].calls[1] == (
            "eq", ("tax_year", 2026), {}
        )

    def test_packet_grant_scans_all_paid_snapshots_for_a_valid_session(self, monkeypatch):
        rows = [
            {
                "analysis_id": f"newer-{index}",
                "packet_session_id": "invalid",
                "packet_payload": {"report": {}},
                "tax_year": 2026,
                "paid_at": "2026-09-29T00:00:00+00:00",
            }
            for index in range(25)
        ]
        rows.append(
            {
                "analysis_id": "older-valid",
                "packet_session_id": "cs_older_valid",
                "packet_payload": {"report": {}},
                "tax_year": 2026,
                "paid_at": "2026-09-28T00:00:00+00:00",
            }
        )
        client = _FakeClient(table_data=rows)
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        assert db.get_packet_grant_for_tax_year("user1", 2026) == ("cs_older_valid", True)
        calls = client.builders[0].calls
        assert ("order", ("created_at",), {"desc": True}) in calls
        assert not any(call[0] == "limit" for call in calls)

    def test_packet_grant_query_failure_is_not_reported_as_unpaid(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.side_effect = RuntimeError("offline")
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        assert db.lookup_packet_grant_for_tax_year("user1", 2026) == (None, False)

    def test_reused_entitlement_does_not_overwrite_original_checkout_analysis(self, monkeypatch):
        original = {
            "analysis_id": "original-analysis",
            "user_id": "user1",
            "tax_year": 2026,
            "packet_session_id": "cs_original",
        }

        class _DuplicateEntitlementClient(_FakeClient):
            def table(self, name):
                # Simulate ON CONFLICT DO NOTHING returning no representation,
                # followed by the exact-row read in save_packet_entitlement.
                data = [] if not self.builders else [original]
                builder = _FakeQueryBuilder(data)
                self.builders.append(builder)
                return builder

        client = _DuplicateEntitlementClient()
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_entitlement(
            "followup-analysis", "user1", 2026, "cs_original"
        )

        assert saved["analysis_id"] == "original-analysis"
        assert not any(
            call[0] == "upsert"
            for builder in client.builders
            for call in builder.calls
        )
        insert = next(call for call in client.builders[1].calls if call[0] == "insert")
        assert insert[1][0]["analysis_id"] == "followup-analysis"
        assert ("eq", ("packet_session_id", "cs_original"), {}) in client.builders[0].calls
        assert ("eq", ("packet_session_id", "cs_original"), {}) in client.builders[2].calls

    def test_entitlement_insert_conflict_keeps_original_analysis_id(self, monkeypatch):
        original = {
            "analysis_id": "original-analysis",
            "user_id": "user1",
            "tax_year": 2026,
            "packet_session_id": "cs_original",
        }

        class _ConflictClient:
            def __init__(self):
                self.inserts = []
                self.reads = 0
                self.mode = "read"

            def table(self, _name):
                self.mode = "read"
                return self

            def select(self, *_args):
                return self

            def eq(self, *_args):
                return self

            def limit(self, *_args):
                return self

            def insert(self, row):
                self.mode = "insert"
                self.inserts.append(row)
                return self

            def upsert(self, *_args, **_kwargs):
                raise AssertionError("entitlement insert must not upsert analysis_id")

            def execute(self):
                if self.mode == "insert":
                    self.mode = "read"
                    error = Exception("duplicate key value violates unique constraint")
                    error.code = "23505"
                    raise error
                self.reads += 1
                if self.reads == 1:
                    return _FakeExecuteResult([])
                return _FakeExecuteResult([original])

        client = _ConflictClient()
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_entitlement(
            "followup-analysis", "user1", 2026, "cs_original"
        )

        assert saved["analysis_id"] == "original-analysis"
        assert len(client.inserts) == 1
        assert client.inserts[0]["analysis_id"] == "followup-analysis"


class TestHistoryInsertConflict:
    def test_unique_violation_raises_conflict(self, monkeypatch):
        client = MagicMock()
        error = Exception("duplicate key value violates unique constraint")
        error.code = "23505"
        client.table.return_value.insert.return_value.select.return_value.execute.side_effect = error
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        with pytest.raises(db.HistoryInsertConflict):
            db.save_analysis_history(
                "user1",
                "guest.csv",
                {},
                result_data={"analysis_id": "analysis-1"},
            )


class _PagedBookClient:
    """In-memory client that honors eq, order, and inclusive range."""

    def __init__(self, books=None, history=None):
        self.books = list(books or [])
        self.history = list(history or [])
        self.history_queries = 0
        self.private_queries = 0
        self.select_error = None

    def table(self, name):
        return _PagedBookQuery(self, name)


class _PagedBookQuery:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.op = "select"
        self.filters = []
        self.orders = []
        self.range_bounds = None
        self.limit_n = None
        self.payload = None
        self.on_conflict = None

    def select(self, *_args, **_kwargs):
        return self

    def upsert(self, row, on_conflict=None, **_kwargs):
        self.op = "upsert"
        self.payload = row
        self.on_conflict = on_conflict
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, col, desc=False, **_kwargs):
        self.orders.append((col, bool(desc)))
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def range(self, start, end):
        self.range_bounds = (start, end)
        return self

    def execute(self):
        if (
            self.op == "select"
            and self.name == "portfolio_activity_books"
            and self.client.select_error is not None
        ):
            raise self.client.select_error
        if self.name == "portfolio_activity_books":
            self.client.private_queries += 1
            rows = list(self.client.books)
        else:
            self.client.history_queries += 1
            rows = list(self.client.history)
        if self.filters:
            for col, val in self.filters:
                rows = [row for row in rows if row.get(col) == val]
        if self.orders:
            rows = _sort_by_orders(rows, self.orders)
        if self.range_bounds is not None:
            start, end = self.range_bounds
            rows = rows[start:end + 1]
        elif self.limit_n is not None:
            rows = rows[: self.limit_n]
        if self.op == "upsert":
            stored = dict(self.payload or {})
            key = self.on_conflict or "user_id"
            match = stored.get(key)
            replaced = False
            target = self.client.books if self.name == "portfolio_activity_books" else rows
            for index, row in enumerate(target):
                if row.get(key) == match:
                    target[index] = stored
                    replaced = True
                    break
            if not replaced:
                target.append(stored)
            return _FakeExecuteResult([stored])
        return _FakeExecuteResult(rows)


def _sort_by_orders(rows, orders):
    def compare(left, right):
        for column, descending in orders:
            left_value = left.get(column) or ""
            right_value = right.get(column) or ""
            if left_value == right_value:
                continue
            if left_value < right_value:
                return 1 if descending else -1
            return -1 if descending else 1
        return 0

    return sorted(rows, key=cmp_to_key(compare))


def _history_row(row_id, filename, uploaded_at, transactions=None, count=0, user_id="user1"):
    book = {"transaction_count": count}
    if transactions is not None:
        book["transactions"] = transactions
    return {
        "id": row_id,
        "user_id": user_id,
        "filename": filename,
        "uploaded_at": uploaded_at,
        "summary": {"activity_transaction_count": count},
        "result": {
            "activity_book": book,
            "summary": {"activity_transaction_count": count},
            "tax_profile": {"tax_year": 2024},
        },
    }


class TestPrivateActivityBook:
    def test_empty_private_row_wins_and_skips_history(self):
        client = _PagedBookClient(
            books=[{
                "user_id": "user1",
                "analysis_id": "empty-book",
                "filename": "cleared.csv",
                "transactions": [],
            }],
            history=[
                _history_row(
                    "full",
                    "full.csv",
                    "2026-01-01T00:00:00Z",
                    transactions=[{"instrument": "AAPL", "trans_code": "Buy"}],
                    count=1,
                )
            ],
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is True
        assert lookup.unrecoverable is False
        assert lookup.book["analysis_id"] == "empty-book"
        assert lookup.book["transactions"] == []
        assert "packet_unlocked" not in lookup.book
        assert client.history_queries == 0

    @pytest.mark.parametrize("raw", [None, {"instrument": "AAPL"}, "AAPL"])
    def test_non_list_private_transactions_are_an_empty_book(self, raw):
        client = _PagedBookClient(
            books=[{
                "user_id": "user1",
                "analysis_id": "blank",
                "filename": "blank.csv",
                "transactions": raw,
            }],
            history=[
                _history_row(
                    "full",
                    "full.csv",
                    "2026-01-01T00:00:00Z",
                    transactions=[{"instrument": "AAPL"}],
                    count=1,
                )
            ],
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is False
        assert lookup.missing_schema is False
        assert lookup.book is None
        assert lookup.scan_incomplete is False
        assert client.history_queries == 0
        assert db.get_latest_activity_book("user1", client=client) is None
        assert client.history_queries == 0

    def test_same_uploaded_at_is_paged_by_id_descending(self, monkeypatch):
        monkeypatch.setattr(db, "ACTIVITY_BOOK_HISTORY_PAGE", 1)
        shared = "2026-08-01T00:00:00Z"
        book_txns = [{"instrument": "NVDA", "trans_code": "Buy", "quantity": 2}]
        client = _PagedBookClient(
            history=[
                _history_row(
                    "id-a",
                    "book.csv",
                    shared,
                    transactions=book_txns,
                    count=1,
                ),
                _history_row(
                    "id-b",
                    "empty.csv",
                    shared,
                    transactions=[],
                    count=0,
                ),
            ]
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is True
        assert lookup.unrecoverable is False
        assert lookup.book["analysis_id"] == "id-a"
        assert lookup.book["transactions"] == book_txns
        assert client.history_queries == 2

    def test_pages_past_the_first_page_and_skips_sample_filenames(self, monkeypatch):
        monkeypatch.setattr(db, "ACTIVITY_BOOK_HISTORY_PAGE", 10)
        sample = _history_row(
            "sample",
            "sample-robinhood-transactions.csv",
            "2026-06-01T00:00:00Z",
            transactions=[{"instrument": "SAMPLE", "trans_code": "Buy"}],
            count=1,
        )
        noise = [
            _history_row(f"n{index}", f"n{index}.csv", f"2026-05-{index:02d}T00:00:00Z", transactions=[], count=0)
            for index in range(1, 22)
        ]
        real = _history_row(
            "real",
            "old.csv",
            "2024-01-01T00:00:00Z",
            transactions=[{"instrument": "AAPL", "trans_code": "Buy"}],
            count=1,
        )
        top_level = {
            "id": "top",
            "user_id": "user1",
            "filename": "legacy.csv",
            "uploaded_at": "2023-01-01T00:00:00Z",
            "summary": {},
            "result": {"transactions": [{"instrument": "MSFT", "trans_code": "Buy"}]},
        }
        client = _PagedBookClient(history=[sample, *noise, real, top_level])
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.book["analysis_id"] == "real"
        assert lookup.book["transactions"][0]["instrument"] == "AAPL"
        assert lookup.book["tax_year"] == 2024
        assert lookup.unrecoverable is False
        assert client.history_queries >= 3

    def test_top_level_transactions_are_a_usable_list(self):
        client = _PagedBookClient(
            history=[{
                "id": "legacy",
                "user_id": "user1",
                "filename": "legacy.csv",
                "uploaded_at": "2024-02-01T00:00:00Z",
                "summary": {},
                "result": {"transactions": [{"instrument": "MSFT", "trans_code": "Buy"}]},
            }]
        )
        book = db.get_latest_activity_book("user1", client=client)
        assert book["analysis_id"] == "legacy"
        assert book["transactions"][0]["instrument"] == "MSFT"

    def test_finished_stripped_history_is_unrecoverable(self):
        client = _PagedBookClient(
            history=[_history_row("stripped", "recent.csv", "2026-08-01T00:00:00Z", transactions=[], count=4)]
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is True
        assert lookup.book is None
        assert lookup.unrecoverable is True
        assert lookup.scan_incomplete is False

    def test_older_list_after_a_stripped_row_is_not_the_book(self):
        older = [{"instrument": "MSFT", "trans_code": "Buy", "quantity": 3}]
        client = _PagedBookClient(
            history=[
                _history_row(
                    "stripped",
                    "recent.csv",
                    "2026-08-01T00:00:00Z",
                    transactions=[],
                    count=4,
                ),
                _history_row(
                    "old",
                    "old.csv",
                    "2024-01-01T00:00:00Z",
                    transactions=older,
                    count=1,
                ),
            ]
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is True
        assert lookup.scan_incomplete is False
        assert lookup.unrecoverable is True
        assert lookup.book is None

    def test_list_before_a_stripped_marker_is_the_book(self):
        newer = [{"instrument": "NVDA", "trans_code": "Buy", "quantity": 2}]
        client = _PagedBookClient(
            history=[
                _history_row(
                    "empty",
                    "empty.csv",
                    "2026-09-01T00:00:00Z",
                    transactions=[],
                    count=0,
                ),
                _history_row(
                    "newer",
                    "newer.csv",
                    "2026-08-01T00:00:00Z",
                    transactions=newer,
                    count=1,
                ),
                _history_row(
                    "stripped",
                    "old.csv",
                    "2024-01-01T00:00:00Z",
                    transactions=[],
                    count=4,
                ),
            ]
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is True
        assert lookup.unrecoverable is False
        assert lookup.scan_incomplete is False
        assert lookup.book["analysis_id"] == "newer"
        assert lookup.book["transactions"] == newer

    def test_page_cap_is_incomplete_not_unrecoverable(self, monkeypatch):
        monkeypatch.setattr(db, "ACTIVITY_BOOK_HISTORY_PAGE", 1)
        monkeypatch.setattr(db, "ACTIVITY_BOOK_HISTORY_MAX_PAGES", 1)
        client = _PagedBookClient(
            history=[
                _history_row("stripped", "recent.csv", "2026-08-01T00:00:00Z", transactions=[], count=4),
                _history_row(
                    "real",
                    "old.csv",
                    "2024-01-01T00:00:00Z",
                    transactions=[{"instrument": "AAPL", "trans_code": "Buy"}],
                    count=1,
                ),
            ]
        )
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.scan_incomplete is True
        assert lookup.unrecoverable is False
        assert lookup.book is None
        assert db.get_latest_activity_book("user1", client=client) is None

    def test_missing_table_does_not_scan_history(self):
        class _Missing(Exception):
            code = "42P01"

            def __str__(self):
                return 'relation "public.portfolio_activity_books" does not exist'

        client = _PagedBookClient(
            history=[_history_row("real", "old.csv", "2024-01-01T00:00:00Z", transactions=[{"instrument": "AAPL"}], count=1)]
        )
        client.select_error = _Missing()
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is False
        assert lookup.missing_schema is True
        assert lookup.book is None
        assert client.history_queries == 0

    def test_private_read_error_does_not_scan_history(self):
        client = _PagedBookClient(
            history=[_history_row("real", "old.csv", "2024-01-01T00:00:00Z", transactions=[{"instrument": "AAPL"}], count=1)]
        )
        client.select_error = RuntimeError("connection reset")
        lookup = db.load_activity_book_for_merge("user1", client=client)
        assert lookup.ok is False
        assert lookup.missing_schema is False
        assert client.history_queries == 0

    def test_upsert_writes_the_full_list(self, monkeypatch):
        client = _FakeClient(table_data=[{"user_id": "user1"}])
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        txns = [{"instrument": "AAPL", "trans_code": "Buy", "quantity": 10}]
        saved = db.upsert_activity_book("user1", "analysis-1", "book.csv", txns)
        assert saved["user_id"] == "user1"
        upsert = next(call for call in client.builders[0].calls if call[0] == "upsert")
        assert upsert[1][0]["transactions"] == txns
        assert upsert[1][0]["analysis_id"] == "analysis-1"
        assert upsert[2]["on_conflict"] == "user_id"

    def test_upsert_failure_returns_none(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("down")
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        assert db.upsert_activity_book("user1", "analysis-1", "book.csv", [{"instrument": "AAPL"}]) is None

    def test_upsert_empty_representation_returns_none(self, monkeypatch):
        client = MagicMock()
        executed = client.table.return_value.upsert.return_value.execute
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        executed.return_value.data = []
        assert db.upsert_activity_book("user1", "analysis-1", "book.csv", [{"instrument": "AAPL"}]) is None
        executed.return_value.data = None
        assert db.upsert_activity_book("user1", "analysis-1", "book.csv", [{"instrument": "AAPL"}]) is None
        executed.return_value.data = ["not-a-row"]
        assert db.upsert_activity_book("user1", "analysis-1", "book.csv", [{"instrument": "AAPL"}]) is None

    def test_no_client_is_an_empty_ok_lookup(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        lookup = db.load_activity_book_for_merge("user1")
        assert lookup.ok is True
        assert lookup.book is None
        assert lookup.unrecoverable is False
        assert db.upsert_activity_book("user1", "analysis-1", "a.csv", [{"instrument": "AAPL"}]) is None
