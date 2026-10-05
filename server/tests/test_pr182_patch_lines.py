"""Execute PR #182 lines that patch coverage still misses.

Supabase is faked. These tests do not connect to Postgres, Supabase, or Render,
and they do not skip.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SERVER_DIR = TESTS_DIR.parent
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(TESTS_DIR))

import db
import ledger
import test_analysis_result_migration as migration


ANALYSIS_ID = "analysis-1"
USER_ID = "user-1"
TAX_YEAR = 2024
ANALYSIS_UUID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def _reset_supabase_client():
    db._supabase_client = None
    yield
    db._supabase_client = None


class _Result:
    def __init__(self, data):
        self.data = data


class _Builder:
    """Chainable PostgREST builder. execute() returns data or raises."""

    _CHAIN = (
        "insert",
        "select",
        "update",
        "eq",
        "contains",
        "is_",
        "filter",
        "order",
        "limit",
    )

    def __init__(self, table_name, outcome):
        self.table_name = table_name
        self.calls = []
        self._outcome = outcome
        for name in self._CHAIN:
            setattr(self, name, self._bind(name))

    def _bind(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return method

    def execute(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return _Result(self._outcome)


class _ScriptedClient:
    """table() pops the next scripted execute() outcome."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.builders = []

    def table(self, name):
        if not self._outcomes:
            raise AssertionError(f"no scripted response left for table({name!r})")
        builder = _Builder(name, self._outcomes.pop(0))
        self.builders.append(builder)
        return builder


def test_scripted_client_rejects_unscripted_table_call():
    client = _ScriptedClient([])
    with pytest.raises(AssertionError, match="no scripted response left"):
        client.table("portfolio_analyses")


def _install(monkeypatch, *outcomes):
    client = _ScriptedClient(outcomes)
    monkeypatch.setattr(db, "get_supabase", lambda: client)
    return client


def _no_client(monkeypatch):
    monkeypatch.setattr(db, "get_supabase", lambda: None)


def _error(message="db error", *, code=None, args=None, **attrs):
    error = Exception(message) if args is None else Exception(*args)
    if code is not None:
        error.code = code
    for key, value in attrs.items():
        setattr(error, key, value)
    return error


def _snap(**overrides):
    row = {
        "analysis_id": ANALYSIS_ID,
        "user_id": USER_ID,
        "tax_year": TAX_YEAR,
        "packet_payload": {"lots": [1]},
        "packet_session_id": None,
        "paid_at": None,
        "expires_at": None,
    }
    row.update(overrides)
    return row


def _assert_consumed(client):
    assert client._outcomes == []


# --- save_analysis_history line 170 ---


def test_save_analysis_history_reraises_insert_conflict(monkeypatch):
    _install(monkeypatch, db.HistoryInsertConflict("already inserted"))
    with pytest.raises(db.HistoryInsertConflict, match="already inserted"):
        db.save_analysis_history(USER_ID, "book.csv", {"positions_count": 1})


def test_save_analysis_history_reraises_schema_error(monkeypatch):
    _install(monkeypatch, db.AnalysisSchemaError("result column missing"))
    with pytest.raises(db.AnalysisSchemaError, match="result column missing"):
        db.save_analysis_history(USER_ID, "book.csv", {"positions_count": 1})


# --- get_analysis_by_result_analysis_id lines 266, 281-283 ---


@pytest.mark.parametrize(
    "analysis_id,user_id",
    [("", USER_ID), (ANALYSIS_ID, ""), (None, USER_ID), (ANALYSIS_ID, None)],
)
def test_get_analysis_by_result_analysis_id_missing_ids(analysis_id, user_id):
    assert db.get_analysis_by_result_analysis_id(analysis_id, user_id) is None


def test_get_analysis_by_result_analysis_id_returns_row(monkeypatch):
    row = {"id": "history-1", "user_id": USER_ID, "result": {"analysis_id": ANALYSIS_ID}}
    client = _install(monkeypatch, [row])
    found = db.get_analysis_by_result_analysis_id(ANALYSIS_ID, USER_ID)
    assert found == row
    assert found is not row
    _assert_consumed(client)


def test_get_analysis_by_result_analysis_id_empty_data(monkeypatch):
    client = _install(monkeypatch, [])
    assert db.get_analysis_by_result_analysis_id(ANALYSIS_ID, USER_ID) is None
    _assert_consumed(client)


# --- lookup_analysis_for_entitlement lines 338-343 ---


@pytest.mark.parametrize(
    "analysis_id,user_id",
    [("", USER_ID), (ANALYSIS_ID, "")],
)
def test_lookup_analysis_for_entitlement_missing_ids(analysis_id, user_id):
    assert db.lookup_analysis_for_entitlement(analysis_id, user_id) == (None, False)


def test_lookup_analysis_for_entitlement_without_client(monkeypatch):
    _no_client(monkeypatch)
    assert db.lookup_analysis_for_entitlement(ANALYSIS_ID, USER_ID) == (None, False)


def test_lookup_analysis_for_entitlement_delegates_to_query(monkeypatch):
    row = {"id": "history-1", "user_id": USER_ID, "result": {"analysis_id": ANALYSIS_ID}}
    client = _install(monkeypatch, [row])
    found, ok = db.lookup_analysis_for_entitlement(ANALYSIS_ID, USER_ID)
    assert ok is True
    assert found == row
    _assert_consumed(client)


# --- ensure_analysis_history lines 353, 356, 370, 384-392 ---


@pytest.mark.parametrize(
    "analysis_id,user_id",
    [("", USER_ID), (ANALYSIS_ID, "")],
)
def test_ensure_analysis_history_missing_ids(analysis_id, user_id):
    assert db.ensure_analysis_history(analysis_id, user_id, {"analysis_id": analysis_id}) is None


def test_ensure_analysis_history_without_client(monkeypatch):
    _no_client(monkeypatch)
    assert db.ensure_analysis_history(ANALYSIS_ID, USER_ID, {"analysis_id": ANALYSIS_ID}) is None


def test_ensure_analysis_history_replaces_non_dict_summary(monkeypatch):
    client = _install(monkeypatch, [])
    captured = {}

    def save(user_id, filename, summary, result_data=None):
        captured.update(
            user_id=user_id,
            filename=filename,
            summary=summary,
            result_data=result_data,
        )
        return {"id": "saved-1"}

    monkeypatch.setattr(db, "save_analysis_history", save)
    analysis = {
        "analysis_id": ANALYSIS_ID,
        "summary": ["not-a-dict"],
        "filename": "book.csv",
    }
    assert db.ensure_analysis_history(ANALYSIS_ID, USER_ID, analysis) == {"id": "saved-1"}
    assert captured["summary"] == {}
    assert captured["filename"] == "book.csv"
    assert captured["result_data"]["summary"] == ["not-a-dict"]
    _assert_consumed(client)


def test_ensure_analysis_history_conflict_race_lookup_fails(monkeypatch):
    client = _install(monkeypatch, [], RuntimeError("lookup down"))

    def save(*_args, **_kwargs):
        raise db.HistoryInsertConflict("raced")

    monkeypatch.setattr(db, "save_analysis_history", save)
    assert db.ensure_analysis_history(
        ANALYSIS_ID,
        USER_ID,
        {"analysis_id": ANALYSIS_ID, "summary": {}},
    ) is None
    _assert_consumed(client)


def test_ensure_analysis_history_conflict_race_lookup_returns_row(monkeypatch):
    row = {"id": "raced-1", "user_id": USER_ID, "result": {"analysis_id": ANALYSIS_ID}}
    client = _install(monkeypatch, [], [row])

    def save(*_args, **_kwargs):
        raise db.HistoryInsertConflict("raced")

    monkeypatch.setattr(db, "save_analysis_history", save)
    assert db.ensure_analysis_history(
        ANALYSIS_ID,
        USER_ID,
        {"analysis_id": ANALYSIS_ID, "summary": {"positions_count": 1}},
    ) == row
    _assert_consumed(client)


# --- packet grant / entitlement guards and entitlement query tails ---


@pytest.mark.parametrize(
    "user_id,tax_year",
    [("", TAX_YEAR), (USER_ID, None)],
)
def test_lookup_packet_grant_for_tax_year_missing_args(user_id, tax_year):
    assert db.lookup_packet_grant_for_tax_year(user_id, tax_year) == (None, False)


@pytest.mark.parametrize(
    "user_id,tax_year",
    [("", TAX_YEAR), (USER_ID, None)],
)
def test_lookup_packet_entitlement_for_tax_year_missing_args(user_id, tax_year):
    assert db.lookup_packet_entitlement_for_tax_year(user_id, tax_year) == (None, False)


def test_lookup_packet_entitlement_query_raises(monkeypatch):
    client = _install(monkeypatch, RuntimeError("entitlement down"))
    assert db.lookup_packet_entitlement_for_tax_year(USER_ID, TAX_YEAR) == (None, False)
    _assert_consumed(client)


def test_lookup_packet_entitlement_without_checkout_session(monkeypatch):
    rows = [
        {"analysis_id": ANALYSIS_ID, "packet_session_id": "pi_card", "tax_year": TAX_YEAR},
        {"analysis_id": ANALYSIS_ID, "packet_session_id": None, "tax_year": TAX_YEAR},
        "not-a-dict",
    ]
    client = _install(monkeypatch, rows)
    assert db.lookup_packet_entitlement_for_tax_year(USER_ID, TAX_YEAR) == (None, True)
    _assert_consumed(client)


# --- _exception_text via the schema/unique classifiers ---


def test_exception_text_includes_details_and_dict_args():
    exc = _error(
        args=({"duplicate key": "packet_year"},),
        message="duplicate key value",
        details="already exists",
        hint="unique constraint",
    )
    assert db._is_unique_violation(exc) is True


@pytest.mark.parametrize("token", ["42703", "42p01", "pgrst204", "pgrst205"])
def test_missing_schema_detects_code_token_in_text(token):
    assert db._is_missing_analysis_schema(Exception(f"failure {token} reported")) is True


@pytest.mark.parametrize("noun", ["column", "table", "relation"])
def test_missing_schema_detects_schema_cache_noun(noun):
    message = f"schema cache does not list the {noun}"
    assert db._is_missing_analysis_schema(Exception(message)) is True


@pytest.mark.parametrize(
    "phrase",
    ["undefined column", "undefined table", "undefined relation"],
)
def test_missing_schema_detects_undefined_phrase(phrase):
    assert db._is_missing_analysis_schema(Exception(f"{phrase} portfolio_analyses")) is True


# --- save_packet_entitlement lines 682, 689, 690-692 ---


def test_save_packet_entitlement_reraises_non_unique_into_handler(monkeypatch):
    client = _install(
        monkeypatch,
        [],
        _error("insert failed", code="08006"),
    )
    assert db.save_packet_entitlement(ANALYSIS_ID, USER_ID, TAX_YEAR, "cs_entitlement") is None
    _assert_consumed(client)


def test_save_packet_entitlement_unique_reread_misses(monkeypatch):
    client = _install(
        monkeypatch,
        [],
        _error(
            args=({"code": "23505", "message": "duplicate key"},),
            code="23505",
            message="duplicate key",
            details="year_close_packet_entitlements",
            hint="unique constraint",
        ),
        [],
    )
    assert db.save_packet_entitlement(ANALYSIS_ID, USER_ID, TAX_YEAR, "cs_entitlement") is None
    _assert_consumed(client)


def test_save_packet_entitlement_empty_insert_and_reread_returns_none(monkeypatch):
    client = _install(monkeypatch, [], [], [])
    assert db.save_packet_entitlement(ANALYSIS_ID, USER_ID, TAX_YEAR, "cs_entitlement") is None
    _assert_consumed(client)


# --- save_packet_snapshot ---


@pytest.mark.parametrize(
    "analysis_id,user_id,tax_year",
    [("", USER_ID, TAX_YEAR), (ANALYSIS_ID, "", TAX_YEAR), (ANALYSIS_ID, USER_ID, None)],
)
def test_save_packet_snapshot_missing_ids(analysis_id, user_id, tax_year):
    assert db.save_packet_snapshot(analysis_id, user_id, tax_year, {"lots": []}) is None


def test_save_packet_snapshot_repairs_non_null_stored_payload_then_misses(monkeypatch):
    stored = "stored-text"
    client = _install(
        monkeypatch,
        [_snap(paid_at="2024-06-01T00:00:00+00:00", packet_payload=stored)],
        [],
        [],
    )
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [1]}) is None
    repair = client.builders[1]
    assert ("eq", ("packet_payload", stored), {}) in repair.calls
    assert ("filter", ("paid_at", "not.is", "null"), {}) in repair.calls
    _assert_consumed(client)


def test_save_packet_snapshot_repair_reread_falls_through_to_none(monkeypatch):
    stored = "stored-text"
    client = _install(
        monkeypatch,
        [_snap(paid_at="2024-06-01T00:00:00+00:00", packet_payload=stored)],
        [],
        [_snap(paid_at="2024-06-01T00:00:00+00:00", packet_payload="still-text")],
    )
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [1]}) is None
    _assert_consumed(client)


def test_save_packet_snapshot_unpaid_update_returns_row(monkeypatch):
    updated = {"analysis_id": ANALYSIS_ID, "user_id": USER_ID, "tax_year": TAX_YEAR}
    client = _install(monkeypatch, [_snap()], [updated])
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [2]}) == updated
    _assert_consumed(client)


def test_save_packet_snapshot_unpaid_update_reread_missing(monkeypatch):
    client = _install(monkeypatch, [_snap()], [], [])
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [2]}) is None
    _assert_consumed(client)


def test_save_packet_snapshot_unpaid_reread_matching_payload_uses_result_row(monkeypatch):
    payload = {"lots": [2]}
    client = _install(
        monkeypatch,
        [_snap(packet_payload={"old": True})],
        [],
        [_snap(packet_payload=payload)],
    )
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, payload) == {
        "analysis_id": ANALYSIS_ID,
        "user_id": USER_ID,
        "tax_year": TAX_YEAR,
    }
    _assert_consumed(client)


def test_save_packet_snapshot_unpaid_reread_different_payload_returns_none(monkeypatch):
    client = _install(
        monkeypatch,
        [_snap(packet_payload={"old": True})],
        [],
        [_snap(packet_payload={"other": True})],
    )
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [2]}) is None
    _assert_consumed(client)


def test_save_packet_snapshot_insert_reread_paid_other_year(monkeypatch):
    client = _install(
        monkeypatch,
        [],
        [],
        [_snap(tax_year=2023, paid_at="2024-01-01T00:00:00+00:00")],
    )
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [1]}) is None
    _assert_consumed(client)


def test_save_packet_snapshot_insert_reread_paid_same_year(monkeypatch):
    client = _install(
        monkeypatch,
        [],
        [],
        [_snap(paid_at="2024-01-01T00:00:00+00:00")],
    )
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [1]}) == {
        "analysis_id": ANALYSIS_ID,
        "user_id": USER_ID,
        "tax_year": TAX_YEAR,
    }
    _assert_consumed(client)


def test_save_packet_snapshot_insert_reread_unpaid(monkeypatch):
    client = _install(monkeypatch, [], [], [_snap(paid_at=None)])
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [1]}) == {
        "analysis_id": ANALYSIS_ID,
        "user_id": USER_ID,
        "tax_year": TAX_YEAR,
    }
    _assert_consumed(client)


def test_save_packet_snapshot_insert_reread_missing(monkeypatch):
    client = _install(monkeypatch, [], [], [])
    assert db.save_packet_snapshot(ANALYSIS_ID, USER_ID, TAX_YEAR, {"lots": [1]}) is None
    _assert_consumed(client)


def _paid_insert_conflict(monkeypatch, *after_conflict):
    return _install(
        monkeypatch,
        [],
        _error("duplicate key value violates unique constraint", code="23505"),
        *after_conflict,
    )


def test_save_packet_snapshot_unique_conflict_promote_returns_row(monkeypatch):
    promoted = {"analysis_id": ANALYSIS_ID, "user_id": USER_ID, "tax_year": TAX_YEAR}
    client = _paid_insert_conflict(
        monkeypatch,
        [_snap(paid_at=None)],
        [promoted],
    )
    assert db.save_packet_snapshot(
        ANALYSIS_ID,
        USER_ID,
        TAX_YEAR,
        {"lots": [1]},
        session_id="cs_promote",
        paid=True,
    ) == promoted
    _assert_consumed(client)


def test_save_packet_snapshot_unique_conflict_promote_reread_paid(monkeypatch):
    client = _paid_insert_conflict(
        monkeypatch,
        [_snap(paid_at=None)],
        [],
        [_snap(paid_at="2024-02-02T00:00:00+00:00")],
    )
    assert db.save_packet_snapshot(
        ANALYSIS_ID,
        USER_ID,
        TAX_YEAR,
        {"lots": [1]},
        session_id="cs_promote",
        paid=True,
    ) == {
        "analysis_id": ANALYSIS_ID,
        "user_id": USER_ID,
        "tax_year": TAX_YEAR,
    }
    _assert_consumed(client)


def test_save_packet_snapshot_unique_conflict_promote_reread_raises(monkeypatch):
    client = _paid_insert_conflict(
        monkeypatch,
        [_snap(paid_at=None)],
        [],
        RuntimeError("promote read failed"),
    )
    assert db.save_packet_snapshot(
        ANALYSIS_ID,
        USER_ID,
        TAX_YEAR,
        {"lots": [1]},
        session_id="cs_promote",
        paid=True,
    ) is None
    _assert_consumed(client)


def test_save_packet_snapshot_unique_conflict_same_year_paid_row(monkeypatch):
    client = _install(
        monkeypatch,
        [],
        _error("unique constraint year_close_packet_snapshots", code=None),
        [_snap(paid_at="2024-01-01T00:00:00+00:00")],
    )
    assert db.save_packet_snapshot(
        ANALYSIS_ID,
        USER_ID,
        TAX_YEAR,
        {"lots": [1]},
        session_id="cs_promote",
        paid=True,
    ) == {
        "analysis_id": ANALYSIS_ID,
        "user_id": USER_ID,
        "tax_year": TAX_YEAR,
    }
    _assert_consumed(client)


# --- get_packet_snapshot lines 908, 912, 924, 930, 934-935, 937-939 ---


@pytest.mark.parametrize(
    "analysis_id,user_id",
    [("", USER_ID), (ANALYSIS_ID, "")],
)
def test_get_packet_snapshot_missing_ids(analysis_id, user_id):
    assert db.get_packet_snapshot(analysis_id, user_id) == (None, False)


def test_get_packet_snapshot_without_client(monkeypatch):
    _no_client(monkeypatch)
    assert db.get_packet_snapshot(ANALYSIS_ID, USER_ID) == (None, False)


def test_get_packet_snapshot_empty_data(monkeypatch):
    client = _install(monkeypatch, [])
    assert db.get_packet_snapshot(ANALYSIS_ID, USER_ID) == (None, True)
    _assert_consumed(client)


def test_get_packet_snapshot_unpaid_without_expiry(monkeypatch):
    client = _install(monkeypatch, [_snap(paid_at=None, expires_at=None)])
    assert db.get_packet_snapshot(ANALYSIS_ID, USER_ID) == (None, True)
    _assert_consumed(client)


def test_get_packet_snapshot_unpaid_bad_expiry(monkeypatch):
    client = _install(monkeypatch, [_snap(paid_at=None, expires_at="not-a-date")])
    assert db.get_packet_snapshot(ANALYSIS_ID, USER_ID) == (None, True)
    _assert_consumed(client)


def test_get_packet_snapshot_query_raises(monkeypatch):
    client = _install(monkeypatch, RuntimeError("snapshot down"))
    assert db.get_packet_snapshot(ANALYSIS_ID, USER_ID) == (None, False)
    _assert_consumed(client)


# --- mark_packet_snapshot_paid lines 951, 955 ---


@pytest.mark.parametrize(
    "analysis_id,user_id,tax_year,session_id",
    [
        ("", USER_ID, TAX_YEAR, "cs_paid"),
        (ANALYSIS_ID, "", TAX_YEAR, "cs_paid"),
        (ANALYSIS_ID, USER_ID, None, "cs_paid"),
        (ANALYSIS_ID, USER_ID, TAX_YEAR, ""),
    ],
)
def test_mark_packet_snapshot_paid_missing_args(analysis_id, user_id, tax_year, session_id):
    assert db.mark_packet_snapshot_paid(analysis_id, user_id, tax_year, session_id) is False


def test_mark_packet_snapshot_paid_without_client(monkeypatch):
    _no_client(monkeypatch)
    assert db.mark_packet_snapshot_paid(ANALYSIS_ID, USER_ID, TAX_YEAR, "cs_paid") is None


# --- patch_analysis_result line 1019 ---


def test_patch_analysis_result_update_raises(monkeypatch):
    record = {
        "id": "history-row",
        "user_id": USER_ID,
        "result": {"analysis_id": ANALYSIS_UUID, "packet_unlocked": False},
    }
    client = _install(monkeypatch, [record], RuntimeError("update failed"))
    assert db.patch_analysis_result(
        ANALYSIS_UUID,
        USER_ID,
        {"packet_unlocked": True},
    ) is None
    assert client.builders[1].calls[0][0] == "update"
    _assert_consumed(client)


# --- ledger.is_trusted_sample_csv_bytes line 59 ---


def test_trusted_sample_csv_bytes_rejects_empty_inputs():
    assert ledger.is_trusted_sample_csv_bytes(None) is False
    assert ledger.is_trusted_sample_csv_bytes(b"") is False


# --- migration helpers, no database and no psycopg connection ---


def test_admin_url_defaults_and_reads_local_override(monkeypatch):
    monkeypatch.delenv("OPTAX_TEST_DATABASE_URL", raising=False)
    assert migration._admin_url() == "postgresql:///postgres"
    monkeypatch.setenv("OPTAX_TEST_DATABASE_URL", "postgresql:///postgres")
    assert migration._admin_url() == "postgresql:///postgres"


def test_csv_targets_empty_blank_and_comma_list():
    assert migration._csv_targets(None) == []
    assert migration._csv_targets("") == []
    assert migration._csv_targets("   ") == []
    assert migration._csv_targets(",") == []
    assert migration._csv_targets(" localhost , , 127.0.0.1 ") == ["localhost", "127.0.0.1"]


@pytest.mark.parametrize(
    "host,expected",
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("db.example.com", False),
    ],
)
def test_is_local_host(host, expected):
    assert migration._is_local_host(host) is expected


def test_url_for_database_unix_socket_and_netloc():
    assert migration._url_for_database("postgresql:///postgres", "oth_unit") == (
        "postgresql:///oth_unit"
    )
    assert migration._url_for_database(
        "postgresql:///postgres?sslmode=disable",
        "oth_unit",
    ) == "postgresql:///oth_unit?sslmode=disable"
    assert migration._url_for_database(
        "postgresql://127.0.0.1/postgres",
        "oth_unit",
    ) == "postgresql://127.0.0.1/oth_unit"


def test_json_parses_string_and_returns_dict():
    assert migration._json('{"tax_year": 2024}') == {"tax_year": 2024}
    assert migration._json({"tax_year": 2024}) == {"tax_year": 2024}


def test_apply_sql_failure_and_success(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        returncode = 0 if len(calls) == 2 else 1
        return subprocess.CompletedProcess(args, returncode, stdout="out", stderr="err")

    monkeypatch.setattr(migration.subprocess, "run", fake_run)
    with pytest.raises(AssertionError, match=r"psql failed \(1\)"):
        migration._apply_sql("postgresql:///postgres", "SELECT 1")
    assert migration._apply_sql("postgresql:///postgres", "SELECT 1") == "out\nerr"
    assert calls[0][0][0] == "psql"
    assert calls[1][0][0] == "psql"


def test_apply_migrations_with_and_without_apply_from(monkeypatch):
    seen = []

    def fake_run(args, **kwargs):
        seen.append({"args": args, "env": kwargs["env"]})
        return subprocess.CompletedProcess(args, 0, stdout="applied", stderr="")

    monkeypatch.setattr(migration.subprocess, "run", fake_run)
    monkeypatch.setenv("APPLY_FROM", "009")
    output = migration._apply_migrations("postgresql:///postgres", apply_from=None)
    assert output == "applied\n"
    assert seen[0]["env"]["DATABASE_URL"] == "postgresql:///postgres"
    assert "APPLY_FROM" not in seen[0]["env"]

    output = migration._apply_migrations("postgresql:///postgres", apply_from="004")
    assert output == "applied\n"
    assert seen[1]["env"]["APPLY_FROM"] == "004"
    assert seen[1]["env"]["DATABASE_URL"] == "postgresql:///postgres"
    assert seen[0]["args"][0] == "sh"


def test_apply_migrations_failure(monkeypatch):
    def fake_run(args, **_kwargs):
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="nope")

    monkeypatch.setattr(migration.subprocess, "run", fake_run)
    with pytest.raises(AssertionError, match=r"apply_migrations.sh failed \(2\)"):
        migration._apply_migrations("postgresql:///postgres")


def test_libpq_conninfo_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg.conninfo", None)
    with pytest.raises(pytest.fail.Exception, match="could not be parsed"):
        migration._libpq_conninfo("postgresql:///postgres")


class _SqlAdmin:
    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)


def test_drop_and_recreate_database_on_fake_admin(monkeypatch):
    monkeypatch.setattr(migration, "_assert_local", lambda _url: None)
    admin = _SqlAdmin()
    migration._drop_database(admin, "oth_unit", "postgresql:///postgres")
    migration._recreate_database(admin, "oth_unit", "postgresql:///postgres")
    assert admin.statements == [
        'DROP DATABASE IF EXISTS "oth_unit" WITH (FORCE)',
        'DROP DATABASE IF EXISTS "oth_unit" WITH (FORCE)',
        'CREATE DATABASE "oth_unit"',
    ]
