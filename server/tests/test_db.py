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
        self.calls = []

    def insert(self, row):
        return self

    def select(self, *args):
        return self

    def upsert(self, row, **kwargs):
        self.calls.append(("upsert", (row,), kwargs))
        return self

    def delete(self):
        return self

    def update(self, row):
        return self

    def eq(self, *args):
        self.calls.append(("eq", args, {}))
        return self

    def contains(self, *args):
        self.calls.append(("contains", args, {}))
        return self

    def is_(self, *args):
        return self

    def gt(self, *args):
        self.calls.append(("gt", args, {}))
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def limit(self, *args):
        return self

    def execute(self):
        return _FakeExecuteResult(self._data)


class _FakeClient:
    """Minimal fake Supabase client."""

    def __init__(self, table_data=None):
        self._table_data = table_data
        self.builders = []

    def table(self, name):
        builder = _FakeQueryBuilder(self._table_data)
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
        client = _FakeClient(table_data=rows)
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        book = db.get_latest_activity_book("user1")
        assert book["analysis_id"] == "book-1"
        assert book["packet_session_id"] == "cs_abc"
        assert len(book["transactions"]) == 1


class TestPacketSnapshots:
    def test_saves_private_packet_snapshot_with_short_unpaid_retention(self, monkeypatch):
        client = _FakeClient(table_data=[{"analysis_id": "analysis-a", "user_id": "user1"}])
        monkeypatch.setattr(db, "get_supabase", lambda: client)

        saved = db.save_packet_snapshot(
            "analysis-a",
            "user1",
            2026,
            {"lot_match_report": {"matched": [{"symbol": "AMD"}]}},
        )

        assert saved["analysis_id"] == "analysis-a"
        calls = client.builders[1].calls
        upsert = next(call for call in calls if call[0] == "upsert")
        assert upsert[1][0]["paid_at"] is None
        assert upsert[1][0]["tax_year"] == 2026
        assert upsert[1][0]["expires_at"] is not None

    def test_paid_packet_snapshot_has_no_expiry(self, monkeypatch):
        client = _FakeClient(table_data=[{"analysis_id": "analysis-a", "user_id": "user1"}])
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
        upsert = next(call for call in client.builders[0].calls if call[0] == "upsert")
        assert upsert[1][0]["paid_at"] is not None
        assert upsert[1][0]["expires_at"] is None

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

    def test_packet_grant_scans_all_paid_snapshots_for_a_valid_session(self, monkeypatch):
        rows = [
            {
                "analysis_id": f"newer-{index}",
                "packet_session_id": "invalid",
                "tax_year": 2026,
                "paid_at": "2026-09-29T00:00:00+00:00",
            }
            for index in range(25)
        ]
        rows.append(
            {
                "analysis_id": "older-valid",
                "packet_session_id": "cs_older_valid",
                "tax_year": 2026,
                "paid_at": "2026-09-28T00:00:00+00:00",
            }
        )
        client = _FakeClient(table_data=rows)
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        assert db.get_packet_grant_for_tax_year("user1", 2026) == "cs_older_valid"
        calls = client.builders[0].calls
        assert ("order", ("created_at",), {"desc": True}) in calls
        assert not any(call[0] == "limit" for call in calls)

    def test_packet_grant_query_failure_is_not_reported_as_unpaid(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.side_effect = RuntimeError("offline")
        monkeypatch.setattr(db, "get_supabase", lambda: client)
        assert db.lookup_packet_grant_for_tax_year("user1", 2026) == (None, False)
