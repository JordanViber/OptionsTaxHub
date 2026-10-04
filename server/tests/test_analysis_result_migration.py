"""Apply server/migrations and exercise portfolio_analyses.result.

Local Postgres only. Do not point this at Supabase, Render, or any hosted
database. db.py loads .env files; this test ignores DATABASE_URL and uses
OPTAX_TEST_DATABASE_URL, or a local socket when that variable is unset.

    cd server
    pip install 'psycopg[binary]'
    OPTAX_TEST_DATABASE_URL=postgresql:///postgres \\
      pytest tests/test_analysis_result_migration.py -q

The result JSON is the object save_analysis_history already writes: a dict
with analysis_id plus the fields the history helpers read.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db


SERVER_DIR = Path(__file__).resolve().parents[1]
APPLY_SCRIPT = SERVER_DIR / "scripts" / "apply_migrations.sh"
EMPTY_DB = "oth_jor34_empty"
UPGRADE_DB = "oth_jor34_upgrade"

# Pre-result portfolio_analyses from main 2f1ebce (summary columns only).
# Kept here so the upgrade test can start from that shape. It is not a
# migration to apply on a fresh deploy.
PRE_RESULT_PORTFOLIO_ANALYSES_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_analyses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  filename TEXT NOT NULL DEFAULT 'upload.csv',
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  positions_count INTEGER NOT NULL DEFAULT 0,
  total_market_value NUMERIC NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_analyses_user_id
  ON portfolio_analyses (user_id, uploaded_at DESC);

ALTER TABLE portfolio_analyses ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own analyses"
  ON portfolio_analyses
  FOR SELECT
  USING (user_id = auth.uid()::text);

CREATE POLICY "Service role can insert analyses"
  ON portfolio_analyses
  FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Service role can delete analyses"
  ON portfolio_analyses
  FOR DELETE
  USING (true);
"""

ANALYSIS_ID = "11111111-1111-4111-8111-111111111111"
SUMMARY = {
    "positions_count": 1,
    "total_market_value": 90.0,
    "total_cost_basis": 100.0,
    "total_unrealized_pnl": -10.0,
}
# Shape save_analysis_history persists and the history helpers read back.
RESULT = {
    "analysis_id": ANALYSIS_ID,
    "positions": [
        {
            "symbol": "AAPL",
            "quantity": 1,
            "market_value": 90.0,
            "cost_basis": 100.0,
        }
    ],
    "suggestions": [{"symbol": "AAPL", "unrealized_loss": 10.0}],
    "summary": SUMMARY,
    "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    "activity_book": {
        "transaction_count": 1,
        "transactions": [
            {"symbol": "AAPL", "trans_code": "Buy", "quantity": 1, "price": 100.0}
        ],
    },
    "packet_unlocked": False,
    "packet_session_id": None,
    "warnings": [],
    "errors": [],
}


def _admin_url() -> str:
    return os.environ.get("OPTAX_TEST_DATABASE_URL", "postgresql:///postgres")


def _assert_local(url: str) -> None:
    host = urlsplit(url).hostname
    if host is None:
        return
    if host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail(
            f"Refusing non-local database host {host}. "
            "Use an empty local Postgres, not Supabase or Render."
        )


def _url_for_database(admin_url: str, name: str) -> str:
    parts = urlsplit(admin_url)
    # urlsplit("postgresql:///postgres") has an empty netloc. urlunsplit then
    # drops a slash and psycopg no longer treats it as a unix-socket URI.
    if parts.netloc == "":
        query = f"?{parts.query}" if parts.query else ""
        return f"{parts.scheme}:///{name}{query}"
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _apply_sql(database_url: str, sql: str) -> None:
    completed = subprocess.run(
        ["psql", database_url, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"psql failed ({completed.returncode})\n{completed.stdout}\n{completed.stderr}"
        )


def _apply_migrations(database_url: str, apply_from: str | None = None) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    if apply_from:
        env["APPLY_FROM"] = apply_from
    else:
        env.pop("APPLY_FROM", None)
    completed = subprocess.run(
        ["sh", str(APPLY_SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        raise AssertionError(
            f"apply_migrations.sh failed ({completed.returncode})\n{output}"
        )
    return output


def _bootstrap_supabase_compat(conn) -> None:
    """Local stand-in for Supabase roles and auth.uid(). Not a migration."""
    conn.execute("CREATE SCHEMA IF NOT EXISTS auth")
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT NULL::uuid $$
        """
    )
    for role in ("anon", "authenticated", "service_role"):
        found = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
        if found is None:
            conn.execute(f"CREATE ROLE {role} NOLOGIN")


def _recreate_database(admin, name: str) -> None:
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="module")
def postgres():
    psycopg = pytest.importorskip("psycopg")
    if shutil.which("psql") is None:
        pytest.skip("psql is not installed")
    admin_url = _admin_url()
    _assert_local(admin_url)
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
        admin.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local Postgres is not available ({exc.__class__.__name__})")
    created = []

    def open_database(name: str):
        _recreate_database(admin, name)
        created.append(name)
        url = _url_for_database(admin_url, name)
        conn = psycopg.connect(url, autocommit=True, connect_timeout=3)
        _bootstrap_supabase_compat(conn)
        return url, conn

    yield open_database

    for name in created:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


def _save_analysis(conn, user_id: str, filename: str, summary: dict, result: dict | None):
    """Insert the same columns save_analysis_history writes."""
    positions_count = summary.get("positions_count", 0)
    total_market_value = summary.get("total_market_value", 0)
    if result is None:
        return conn.execute(
            """
            INSERT INTO portfolio_analyses (
              user_id, filename, summary, positions_count, total_market_value
            ) VALUES (%s, %s, CAST(%s AS jsonb), %s, %s)
            RETURNING id::text
            """,
            (user_id, filename, json.dumps(summary), positions_count, total_market_value),
        ).fetchone()[0]
    return conn.execute(
        """
        INSERT INTO portfolio_analyses (
          user_id, filename, summary, positions_count, total_market_value, result
        ) VALUES (%s, %s, CAST(%s AS jsonb), %s, %s, CAST(%s AS jsonb))
        RETURNING id::text
        """,
        (
            user_id,
            filename,
            json.dumps(summary),
            positions_count,
            total_market_value,
            json.dumps(result),
        ),
    ).fetchone()[0]


def test_empty_database_save_list_restore_delete(postgres):
    url, conn = postgres(EMPTY_DB)
    try:
        output = _apply_migrations(url)
        assert "Applying 001_portfolio_analyses.sql" in output
        assert "Applying 008_portfolio_analyses_one_analysis_id.sql" in output

        row_id = _save_analysis(conn, "user-a", "aapl.csv", SUMMARY, RESULT)

        listed = conn.execute(
            """
            SELECT id::text, user_id, filename, summary::text, positions_count, total_market_value
            FROM portfolio_analyses
            WHERE user_id = %s
            ORDER BY uploaded_at DESC
            LIMIT 20
            """,
            ("user-a",),
        ).fetchall()
        assert [row[0] for row in listed] == [row_id]
        assert listed[0][1] == "user-a"
        assert listed[0][2] == "aapl.csv"
        assert _json(listed[0][3])["positions_count"] == 1
        assert listed[0][4] == 1
        assert float(listed[0][5]) == 90.0

        restored = conn.execute(
            """
            SELECT id::text, user_id, filename, result::text
            FROM portfolio_analyses
            WHERE id = %s AND user_id = %s
            LIMIT 1
            """,
            (row_id, "user-a"),
        ).fetchone()
        assert restored[0] == row_id
        payload = _json(restored[3])
        assert payload["analysis_id"] == ANALYSIS_ID
        assert payload["tax_profile"]["tax_year"] == 2025
        assert payload["suggestions"][0]["symbol"] == "AAPL"
        assert payload["activity_book"]["transactions"][0]["trans_code"] == "Buy"
        assert payload["packet_unlocked"] is False

        by_contains = conn.execute(
            """
            SELECT id::text
            FROM portfolio_analyses
            WHERE user_id = %s AND result @> CAST(%s AS jsonb)
            ORDER BY uploaded_at DESC
            LIMIT 1
            """,
            ("user-a", json.dumps({"analysis_id": ANALYSIS_ID})),
        ).fetchone()
        assert by_contains[0] == row_id

        by_expression = conn.execute(
            """
            SELECT id::text
            FROM portfolio_analyses
            WHERE user_id = %s AND (result->>'analysis_id') = %s
            """,
            ("user-a", ANALYSIS_ID),
        ).fetchone()
        assert by_expression[0] == row_id

        wrong_user = conn.execute(
            """
            DELETE FROM portfolio_analyses
            WHERE id = %s AND user_id = %s
            RETURNING id::text
            """,
            (row_id, "someone-else"),
        ).fetchall()
        assert wrong_user == []

        deleted = conn.execute(
            """
            DELETE FROM portfolio_analyses
            WHERE id = %s AND user_id = %s
            RETURNING id::text
            """,
            (row_id, "user-a"),
        ).fetchall()
        assert [row[0] for row in deleted] == [row_id]
        remaining = conn.execute(
            "SELECT id::text FROM portfolio_analyses WHERE user_id = %s",
            ("user-a",),
        ).fetchall()
        assert remaining == []

        legacy_id = _save_analysis(conn, "user-a", "legacy.csv", {"positions_count": 0}, None)
        kept_id = _save_analysis(conn, "user-a", "kept.csv", SUMMARY, RESULT)
        duplicate = Exception("duplicate was not rejected")
        try:
            _save_analysis(conn, "user-a", "dup.csv", SUMMARY, RESULT)
        except Exception as exc:
            duplicate = exc
        assert db._is_unique_violation(duplicate)
        assert getattr(duplicate, "sqlstate", None) == "23505"

        legacy_only = conn.execute(
            """
            DELETE FROM portfolio_analyses
            WHERE user_id = %s AND result IS NULL
            RETURNING id::text
            """,
            ("user-a",),
        ).fetchall()
        assert [row[0] for row in legacy_only] == [legacy_id]
        still_there = conn.execute(
            "SELECT id::text, result->>'analysis_id' FROM portfolio_analyses WHERE user_id = %s",
            ("user-a",),
        ).fetchall()
        assert still_there == [(kept_id, ANALYSIS_ID)]

        index = conn.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE indexname = 'portfolio_analyses_user_embedded_analysis_id_uidx'
            """
        ).fetchone()
        assert index is not None
        assert "result" in index[0]
        assert "analysis_id" in index[0]
    finally:
        conn.close()


def test_summary_only_upgrade_keeps_historical_row(postgres):
    url, conn = postgres(UPGRADE_DB)
    try:
        _apply_sql(url, PRE_RESULT_PORTFOLIO_ANALYSES_SQL)
        historical = conn.execute(
            """
            INSERT INTO portfolio_analyses (
              user_id, filename, summary, positions_count, total_market_value
            ) VALUES (%s, %s, CAST(%s AS jsonb), %s, %s)
            RETURNING id::text, filename, summary::text, positions_count,
                      total_market_value, created_at, uploaded_at
            """,
            (
                "legacy-user",
                "legacy.csv",
                json.dumps({"positions_count": 2, "total_market_value": 1500.5, "note": "kept"}),
                2,
                1500.5,
            ),
        ).fetchone()
        historical_id = historical[0]

        missing = None
        try:
            conn.execute(
                """
                INSERT INTO portfolio_analyses (
                  user_id, filename, summary, positions_count, total_market_value, result
                ) VALUES (%s, %s, CAST(%s AS jsonb), %s, %s, CAST(%s AS jsonb))
                """,
                (
                    "legacy-user",
                    "too-soon.csv",
                    "{}",
                    0,
                    0,
                    json.dumps({"analysis_id": ANALYSIS_ID}),
                ),
            )
        except Exception as exc:
            missing = exc
        assert missing is not None
        assert db._is_missing_analysis_schema(missing)

        before = conn.execute(
            "SELECT count(*) FROM portfolio_analyses WHERE user_id = %s",
            ("legacy-user",),
        ).fetchone()[0]
        assert before == 1

        output = _apply_migrations(url, apply_from="002_tax_profiles.sql")
        assert "Applying 002_tax_profiles.sql" in output
        assert "Applying 004_year_close_packet_snapshots.sql" in output
        assert "Applying 008_portfolio_analyses_one_analysis_id.sql" in output
        assert "Applying 001_portfolio_analyses.sql" not in output

        column = conn.execute(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'portfolio_analyses'
              AND column_name = 'result'
            """
        ).fetchone()
        assert column == ("jsonb", "YES")

        upgraded = conn.execute(
            """
            SELECT id::text, filename, summary::text, positions_count,
                   total_market_value, created_at, uploaded_at, result IS NULL
            FROM portfolio_analyses
            WHERE id = %s
            """,
            (historical_id,),
        ).fetchone()
        assert upgraded[0] == historical_id
        assert upgraded[1] == historical[1]
        assert _json(upgraded[2]) == _json(historical[2])
        assert upgraded[3] == historical[3]
        assert float(upgraded[4]) == float(historical[4])
        assert upgraded[5] == historical[5]
        assert upgraded[6] == historical[6]
        assert upgraded[7] is True

        count = conn.execute(
            "SELECT count(*) FROM portfolio_analyses"
        ).fetchone()[0]
        assert count == 1

        new_id = _save_analysis(conn, "legacy-user", "after.csv", SUMMARY, RESULT)
        rows = conn.execute(
            """
            SELECT id::text, result IS NULL
            FROM portfolio_analyses
            ORDER BY uploaded_at ASC
            """
        ).fetchall()
        assert rows == [(historical_id, True), (new_id, False)]
    finally:
        conn.close()
