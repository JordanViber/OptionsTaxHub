"""Apply server/migrations and exercise portfolio_analyses.result.

Local Postgres only. Do not point this at Supabase, Render, or any hosted
database. db.py loads .env files; this test ignores DATABASE_URL and uses
OPTAX_TEST_DATABASE_URL, or a local socket when that variable is unset.
_assert_local runs before every DROP DATABASE and refuses a libpq host or
hostaddr that is not localhost, 127.0.0.1, or ::1, including ?host= and
?hostaddr=. No host at all is a unix socket and is local.

    cd server
    pip install 'psycopg[binary]'
    OPTAX_TEST_DATABASE_URL=postgresql:///postgres \\
      pytest tests/test_analysis_result_migration.py -q

The result JSON is the object save_analysis_history already writes: a dict
with analysis_id plus the fields the history helpers read.
"""

import json
import os
import re
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


# Connection targets that may receive DROP DATABASE. Anything else is refused.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _libpq_conninfo(url: str) -> dict:
    """Parse a libpq URI or keyword conninfo without connecting."""
    try:
        from psycopg.conninfo import conninfo_to_dict
    except Exception as exc:
        pytest.fail(
            "Refusing database URL because libpq conninfo could not be parsed "
            f"({exc.__class__.__name__}). "
            "Use an empty local Postgres, not Supabase or Render."
        )
    try:
        return conninfo_to_dict(url)
    except Exception as exc:
        pytest.fail(
            "Refusing database URL that libpq could not parse "
            f"({exc.__class__.__name__}). "
            "Use an empty local Postgres, not Supabase or Render."
        )


def _csv_targets(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _is_local_host(host: str) -> bool:
    text = host.strip().lower()
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1].strip()
    return text in _LOCAL_HOSTS


def _connection_targets(url: str) -> list[str]:
    """Hosts libpq will use. Query host replaces the URI authority host.

    hostaddr is a separate TCP target and is not ignored when host is local.
    Each comma-separated entry is its own target. Repeated host or hostaddr
    parameters use the last value, which is what libpq connects to.
    """
    info = _libpq_conninfo(url)
    return _csv_targets(info.get("host")) + _csv_targets(info.get("hostaddr"))


def _assert_local(url: str) -> None:
    """Refuse non-local libpq targets before any DROP DATABASE.

    urlsplit().hostname is None for postgresql:///postgres?host=db.example.com,
    but libpq still opens that query host. An empty host list is a unix socket.
    """
    for host in _connection_targets(url):
        if not _is_local_host(host):
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


def _apply_sql(database_url: str, sql: str) -> str:
    completed = subprocess.run(
        ["psql", database_url, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        raise AssertionError(
            f"psql failed ({completed.returncode})\n{output}"
        )
    return output


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


def _drop_database(admin, name: str, admin_url: str) -> None:
    _assert_local(admin_url)
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _recreate_database(admin, name: str, admin_url: str) -> None:
    _drop_database(admin, name, admin_url)
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
        _recreate_database(admin, name, admin_url)
        created.append(name)
        url = _url_for_database(admin_url, name)
        _assert_local(url)
        conn = psycopg.connect(url, autocommit=True, connect_timeout=3)
        _bootstrap_supabase_compat(conn)
        return url, conn

    yield open_database

    for name in created:
        _drop_database(admin, name, admin_url)
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


class _SqlLog:
    def __init__(self):
        self.statements = []

    def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)


_LOCAL_DATABASE_URLS = [
    "postgresql:///postgres",
    "postgres:///postgres",
    "postgresql://localhost/postgres",
    "postgresql://127.0.0.1/postgres",
    "postgresql://[::1]/postgres",
    "postgresql://localhost:5432/postgres",
    "postgresql:///postgres?host=localhost",
    "postgresql:///postgres?host=127.0.0.1",
    "postgresql:///postgres?host=::1",
    "postgresql:///postgres?host=LocalHost",
    "postgresql:///postgres?hostaddr=127.0.0.1",
    "postgresql:///postgres?hostaddr=::1",
    "postgresql:///postgres?hostaddr=[::1]",
    "postgresql://localhost/postgres?hostaddr=127.0.0.1",
    "postgresql:///postgres?host=localhost,127.0.0.1,::1",
    "postgresql://db.example.com/postgres?host=localhost",
    "postgresql:///postgres?host=db.example.com&host=localhost",
    "dbname=postgres",
    "host=localhost dbname=postgres",
    "host=127.0.0.1 hostaddr=::1 dbname=postgres",
]

_REMOTE_DATABASE_URLS = [
    "postgresql:///postgres?host=db.example.com",
    "postgresql://localhost/postgres?host=db.example.com",
    "postgresql:///postgres?host=localhost,db.example.com",
    "postgresql:///postgres?host=%3A%3A1,db.example.com",
    "postgresql:///postgres?host=localhost&host=db.example.com",
    "postgresql:///postgres?hostaddr=8.8.8.8",
    "postgresql:///postgres?hostaddr=127.0.0.1,8.8.8.8",
    "postgresql://localhost/postgres?hostaddr=8.8.8.8",
    "postgresql://127.0.0.1/postgres?hostaddr=10.0.0.1",
    "postgresql:///postgres?host=localhost&hostaddr=10.0.0.1",
    "postgresql:///postgres?host=db.example.com&hostaddr=127.0.0.1",
    "postgresql://db.example.com/postgres?hostaddr=127.0.0.1",
    "postgresql://db.example.com/postgres",
    "postgresql://localhost,db.example.com/postgres",
    "postgresql://[2001:db8::1]/postgres",
    "host=db.example.com dbname=postgres",
    "host=localhost hostaddr=8.8.8.8 dbname=postgres",
]


def test_query_string_remote_host_is_rejected_before_drop():
    pytest.importorskip("psycopg.conninfo")
    url = "postgresql:///postgres?host=db.example.com"
    admin = _SqlLog()
    with pytest.raises(pytest.fail.Exception, match="db.example.com"):
        _recreate_database(admin, EMPTY_DB, url)
    assert admin.statements == []
    assert not any("DROP DATABASE" in statement for statement in admin.statements)


@pytest.mark.parametrize("url", _LOCAL_DATABASE_URLS)
def test_local_database_urls_are_allowed(url):
    pytest.importorskip("psycopg.conninfo")
    _assert_local(url)


@pytest.mark.parametrize("url", _REMOTE_DATABASE_URLS)
def test_nonlocal_database_urls_are_rejected(url):
    pytest.importorskip("psycopg.conninfo")
    admin = _SqlLog()
    with pytest.raises(pytest.fail.Exception, match="Refusing non-local database host"):
        _drop_database(admin, EMPTY_DB, url)
    assert admin.statements == []


def test_local_socket_url_drops_only_after_the_guard():
    pytest.importorskip("psycopg.conninfo")
    admin = _SqlLog()
    _recreate_database(admin, EMPTY_DB, "postgresql:///postgres")
    assert admin.statements[0].startswith('DROP DATABASE IF EXISTS "oth_jor34_empty"')
    assert admin.statements[1].startswith('CREATE DATABASE "oth_jor34_empty"')


def _tax_profile_insert_policies(sql: str) -> list[tuple[str, str]]:
    policies = []
    pattern = re.compile(
        r"CREATE POLICY\s+\"(?P<name>[^\"]+)\"\s+ON\s+tax_profiles\b(?P<body>.*?);",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(sql):
        body = match.group("body")
        command = re.search(
            r"\bFOR\s+(SELECT|INSERT|UPDATE|DELETE|ALL)\b",
            body,
            flags=re.IGNORECASE,
        )
        cmd = command.group(1).upper() if command else "ALL"
        if cmd in {"INSERT", "ALL"}:
            policies.append((match.group("name"), body))
    return policies


def _policy_role_set(body: str) -> set[str]:
    to_clause = re.search(
        r"\bTO\s+(?P<roles>.+?)(?:\bUSING\b|\bWITH\s+CHECK\b|$)",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if to_clause is None:
        return {"public"}
    roles = set()
    for role in re.split(r"\s*,\s*", to_clause.group("roles").strip()):
        cleaned = role.strip().strip('"').lower()
        if cleaned:
            roles.add(cleaned)
    return roles


def test_tax_profile_insert_policy_sql_is_not_public():
    sql = (SERVER_DIR / "migrations" / "002_tax_profiles.sql").read_text()
    policies = _tax_profile_insert_policies(sql)
    assert policies, "tax_profiles INSERT policy is missing"
    for name, body in policies:
        roles = _policy_role_set(body)
        assert "public" not in roles, f"{name} applies to PUBLIC"
        assert "anon" not in roles
        assert "authenticated" not in roles
        assert roles == {"service_role"}


def test_tax_profile_insert_policy_is_not_public(postgres):
    url, conn = postgres("oth_jor34_tax_policy")
    try:
        _apply_migrations(url)
        rows = conn.execute(
            """
            SELECT policyname, cmd, roles
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = 'tax_profiles'
              AND cmd IN ('INSERT', 'ALL')
            """
        ).fetchall()
        assert rows, "tax_profiles INSERT policy is missing"
        for policyname, cmd, roles in rows:
            role_set = {str(role).lower() for role in roles}
            assert "public" not in role_set, f"{policyname} applies to PUBLIC ({roles})"
            assert "anon" not in role_set
            assert "authenticated" not in role_set
            assert role_set == {"service_role"}
            assert cmd == "INSERT"

        conn.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role")
        conn.execute(
            """
            GRANT INSERT ON TABLE public.tax_profiles
            TO anon, authenticated, service_role
            """
        )
        for role in ("anon", "authenticated"):
            conn.execute(f"SET ROLE {role}")
            denied = None
            try:
                conn.execute(
                    "INSERT INTO public.tax_profiles (user_id) VALUES (%s)",
                    ("someone-else",),
                )
            except Exception as exc:
                denied = exc
            conn.execute("RESET ROLE")
            assert denied is not None, f"{role} inserted an arbitrary tax profile"
            assert getattr(denied, "sqlstate", None) == "42501"

        conn.execute("SET ROLE service_role")
        conn.execute(
            "INSERT INTO public.tax_profiles (user_id) VALUES (%s)",
            ("backend-user",),
        )
        conn.execute("RESET ROLE")
        stored = conn.execute(
            "SELECT user_id FROM public.tax_profiles ORDER BY user_id"
        ).fetchall()
        assert stored == [("backend-user",)]
    finally:
        conn.execute("RESET ROLE")
        conn.close()


IDENTITY_DB = "oth_jor36_identity"
_COLLISION_CANONICAL = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_COLLISION_LEGACY = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_NON_UUID_ROW = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_REWRITE_LEGACY = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
_REWRITE_CANONICAL = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _insert_identity_row(conn, row_id: str, user_id: str, analysis_id: str) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_analyses (
          id, user_id, filename, summary, positions_count, total_market_value, result
        ) VALUES (
          %s, %s, 'upload.csv', CAST(%s AS jsonb), 0, 0, CAST(%s AS jsonb)
        )
        """,
        (
            row_id,
            user_id,
            json.dumps({"positions_count": 0}),
            json.dumps({"analysis_id": analysis_id, "positions": []}),
        ),
    )


def test_migration_012_locks_analyses_before_embedded_snapshot():
    sql = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    begin = sql.find("BEGIN;")
    lock = sql.find(
        "LOCK TABLE public.portfolio_analyses IN SHARE ROW EXCLUSIVE MODE;"
    )
    snapshot_lock = sql.find(
        "LOCK TABLE public.year_close_packet_snapshots IN SHARE ROW EXCLUSIVE MODE;"
    )
    entitlement_lock = sql.find(
        "LOCK TABLE public.year_close_packet_entitlements IN SHARE ROW EXCLUSIVE MODE;"
    )
    activity_lock = sql.find(
        "LOCK TABLE public.portfolio_activity_books IN SHARE ROW EXCLUSIVE MODE;"
    )
    canonical_update = sql.find(
        "UPDATE public.year_close_packet_snapshots\nSET analysis_id = (analysis_id)::uuid::text"
    )
    snapshot = sql.find("CREATE TEMP TABLE portfolio_analysis_embedded_snapshot")
    assert begin != -1
    assert lock != -1
    assert snapshot_lock != -1
    assert entitlement_lock != -1
    assert activity_lock != -1
    assert canonical_update != -1
    assert snapshot != -1
    assert begin < lock < snapshot
    assert begin < snapshot_lock < entitlement_lock < activity_lock < canonical_update
    assert sql.strip().endswith("COMMIT;")
    assert not re.search(
        r"create\s+unique\s+index[\s\S]{0,240}lower\s*\(\s*analysis_id",
        sql,
        re.I,
    )
    assert not re.search(
        r"create\s+unique\s+index[\s\S]{0,240}lower\s*\(\s*result",
        sql,
        re.I,
    )


def test_migration_012_unifies_ids_and_backfills_entitlements(postgres):
    url, conn = postgres(IDENTITY_DB)
    try:
        applied = _apply_migrations(url)
        assert "Applying 012_unify_analysis_identity.sql" in applied
        assert "vgrlucxqncajjdoaoctq" not in url

        _insert_identity_row(conn, _COLLISION_CANONICAL, "other-user", _COLLISION_CANONICAL)
        _insert_identity_row(conn, _COLLISION_LEGACY, "paid-user", _COLLISION_CANONICAL)
        _insert_identity_row(conn, _NON_UUID_ROW, "paid-user", "not-a-uuid")
        _insert_identity_row(conn, _REWRITE_LEGACY, "paid-user", _REWRITE_CANONICAL)
        conn.execute(
            """
            INSERT INTO year_close_packet_entitlements (
              user_id, tax_year, packet_session_id, analysis_id
            ) VALUES ('paid-user', 2024, 'cs_existing', 'original-analysis')
            """
        )
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at
            ) VALUES
              ('different-snapshot-analysis', 'paid-user', 2024, '{}'::jsonb, 'cs_existing', now()),
              ('snapshot-analysis', 'paid-user', 2026, '{"kept": true}'::jsonb, 'cs_backfill', now()),
              (%s, 'paid-user', 2025, '{}'::jsonb, 'cs_legacy_snapshot', now()),
              ('unpaid-analysis', 'paid-user', 2026, '{}'::jsonb, 'cs_unpaid', NULL)
            """,
            (_REWRITE_LEGACY,),
        )
        conn.execute(
            """
            INSERT INTO portfolio_activity_books (
              user_id, analysis_id, filename, transactions
            ) VALUES ('paid-user', 'book-analysis-id', 'book.csv', '[]'::jsonb)
            """
        )

        migration_sql = (
            SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql"
        ).read_text()
        assert migration_sql.strip().startswith("BEGIN;") or "\nBEGIN;" in "\n" + migration_sql
        assert migration_sql.strip().endswith("COMMIT;")
        output = _apply_sql(url, "SET client_min_messages TO notice;\n" + migration_sql)
        assert "skip non-uuid analysis_id" in output
        assert "skip collision analysis_id" in output
        assert _NON_UUID_ROW in output
        assert _COLLISION_LEGACY in output

        rewritten = conn.execute(
            """
            SELECT id::text, result->>'analysis_id'
            FROM portfolio_analyses
            WHERE user_id = 'paid-user' AND id = %s
            """,
            (_REWRITE_CANONICAL,),
        ).fetchone()
        assert rewritten == (_REWRITE_CANONICAL, _REWRITE_CANONICAL)
        assert conn.execute(
            "SELECT 1 FROM portfolio_analyses WHERE id = %s",
            (_REWRITE_LEGACY,),
        ).fetchone() is None
        alias = conn.execute(
            """
            SELECT legacy_row_id::text, canonical_analysis_id::text
            FROM portfolio_analysis_id_aliases
            WHERE user_id = 'paid-user'
            """
        ).fetchall()
        assert alias == [(_REWRITE_LEGACY, _REWRITE_CANONICAL)]

        by_legacy = conn.execute(
            """
            SELECT id::text, result->>'analysis_id'
            FROM portfolio_analyses
            WHERE user_id = 'paid-user' AND id = %s
            """,
            (_COLLISION_LEGACY,),
        ).fetchone()
        by_embedded = conn.execute(
            """
            SELECT id::text, result->>'analysis_id'
            FROM portfolio_analyses
            WHERE user_id = 'paid-user' AND result->>'analysis_id' = %s
            """,
            (_COLLISION_CANONICAL,),
        ).fetchone()
        assert by_legacy == by_embedded == (_COLLISION_LEGACY, _COLLISION_CANONICAL)
        assert conn.execute(
            """
            SELECT count(*)
            FROM portfolio_analysis_id_aliases
            WHERE user_id = 'paid-user' AND legacy_row_id = %s
            """,
            (_COLLISION_LEGACY,),
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT id::text, result->>'analysis_id'
            FROM portfolio_analyses
            WHERE id = %s
            """,
            (_NON_UUID_ROW,),
        ).fetchone() == (_NON_UUID_ROW, "not-a-uuid")
        assert conn.execute(
            "SELECT id::text FROM portfolio_analyses WHERE user_id = 'other-user'"
        ).fetchone()[0] == _COLLISION_CANONICAL

        entitlements = conn.execute(
            """
            SELECT tax_year, packet_session_id, analysis_id
            FROM year_close_packet_entitlements
            WHERE user_id = 'paid-user'
            ORDER BY tax_year, packet_session_id
            """
        ).fetchall()
        assert entitlements == [
            (2024, "cs_existing", "original-analysis"),
            (2025, "cs_legacy_snapshot", _REWRITE_LEGACY),
            (2026, "cs_backfill", "snapshot-analysis"),
        ]
        snapshots = conn.execute(
            """
            SELECT analysis_id, tax_year, packet_session_id, paid_at IS NOT NULL
            FROM year_close_packet_snapshots
            WHERE user_id = 'paid-user'
            ORDER BY tax_year, packet_session_id
            """
        ).fetchall()
        assert snapshots == [
            ("different-snapshot-analysis", 2024, "cs_existing", True),
            (_REWRITE_LEGACY, 2025, "cs_legacy_snapshot", True),
            ("snapshot-analysis", 2026, "cs_backfill", True),
            ("unpaid-analysis", 2026, "cs_unpaid", False),
        ]
        assert conn.execute(
            """
            SELECT analysis_id, transactions::text
            FROM portfolio_activity_books
            WHERE user_id = 'paid-user'
            """
        ).fetchone() == ("book-analysis-id", "[]")

        locked = conn.execute(
            """
            SELECT c.relrowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'portfolio_analysis_id_aliases'
            """
        ).fetchone()
        assert locked == (True,)
        assert conn.execute(
            """
            SELECT count(*) FROM pg_policies
            WHERE schemaname = 'public' AND tablename = 'portfolio_analysis_id_aliases'
            """
        ).fetchone()[0] == 0
        grants = conn.execute(
            """
            SELECT grantee, privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
              AND table_name = 'portfolio_analysis_id_aliases'
              AND grantee IN ('service_role', 'anon', 'authenticated', 'PUBLIC')
            ORDER BY grantee, privilege_type
            """
        ).fetchall()
        assert grants == [
            ("service_role", "DELETE"),
            ("service_role", "INSERT"),
            ("service_role", "SELECT"),
            ("service_role", "UPDATE"),
        ]

        second = _apply_sql(url, "SET client_min_messages TO notice;\n" + migration_sql)
        assert "skip non-uuid analysis_id" in second
        assert "skip collision analysis_id" in second
        assert conn.execute(
            "SELECT count(*) FROM portfolio_analysis_id_aliases"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT analysis_id FROM year_close_packet_entitlements
            WHERE user_id = 'paid-user' AND packet_session_id = 'cs_existing'
            """
        ).fetchone()[0] == "original-analysis"
        assert conn.execute(
            "SELECT count(*) FROM year_close_packet_entitlements WHERE user_id = 'paid-user'"
        ).fetchone()[0] == 3

        assert conn.execute(
            "DELETE FROM portfolio_analyses WHERE id = %s RETURNING id::text",
            (_REWRITE_CANONICAL,),
        ).fetchone() == (_REWRITE_CANONICAL,)
        assert conn.execute(
            """
            SELECT analysis_id, packet_payload IS NULL, paid_at IS NOT NULL
            FROM year_close_packet_snapshots
            WHERE user_id = 'paid-user' AND analysis_id = %s
            """,
            (_REWRITE_LEGACY,),
        ).fetchone() == (_REWRITE_LEGACY, True, True)
        assert conn.execute(
            """
            SELECT count(*)
            FROM portfolio_analysis_id_aliases
            WHERE user_id = 'paid-user'
              AND canonical_analysis_id = %s
            """,
            (_REWRITE_CANONICAL,),
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT analysis_id
            FROM year_close_packet_entitlements
            WHERE user_id = 'paid-user' AND packet_session_id = 'cs_legacy_snapshot'
            """
        ).fetchone()[0] == _REWRITE_LEGACY
        assert conn.execute(
            """
            SELECT packet_payload->>'kept'
            FROM year_close_packet_snapshots
            WHERE user_id = 'paid-user' AND packet_session_id = 'cs_backfill'
            """
        ).fetchone()[0] == "true"

        other_spelling = _COLLISION_CANONICAL.upper()
        conn.execute(
            """
            INSERT INTO year_close_packet_entitlements (
              user_id, tax_year, packet_session_id, analysis_id
            ) VALUES ('paid-user', 2023, 'cs_collision_case', %s)
            """,
            (other_spelling,),
        )
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at
            ) VALUES (%s, 'paid-user', 2023, '{"kept": true}'::jsonb, 'cs_collision_case', now())
            """,
            (other_spelling,),
        )
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at
            ) VALUES ('Not-A-Uuid', 'paid-user', 2022, '{"kept": true}'::jsonb, 'cs_non_uuid_case', now())
            """
        )
        assert conn.execute(
            "DELETE FROM portfolio_analyses WHERE id = %s RETURNING id::text",
            (_COLLISION_LEGACY,),
        ).fetchone() == (_COLLISION_LEGACY,)
        assert conn.execute(
            """
            SELECT analysis_id, packet_payload IS NULL, paid_at IS NOT NULL
            FROM year_close_packet_snapshots
            WHERE user_id = 'paid-user' AND packet_session_id = 'cs_collision_case'
            """
        ).fetchone() == (other_spelling, True, True)
        assert conn.execute(
            """
            SELECT analysis_id
            FROM year_close_packet_entitlements
            WHERE user_id = 'paid-user' AND packet_session_id = 'cs_collision_case'
            """
        ).fetchone()[0] == other_spelling
        assert conn.execute(
            "DELETE FROM portfolio_analyses WHERE id = %s RETURNING id::text",
            (_NON_UUID_ROW,),
        ).fetchone() == (_NON_UUID_ROW,)
        assert conn.execute(
            """
            SELECT analysis_id, packet_payload->>'kept', paid_at IS NOT NULL
            FROM year_close_packet_snapshots
            WHERE user_id = 'paid-user' AND packet_session_id = 'cs_non_uuid_case'
            """
        ).fetchone() == ("Not-A-Uuid", "true", True)
    finally:
        conn.close()


def _psql(database_url: str, sql: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["psql", database_url, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, f"{completed.stdout}\n{completed.stderr}"


def test_migration_012_collapses_snapshot_case_and_raises_on_two_paid_years(postgres):
    url, conn = postgres("oth_jor36_canon_collapse")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    paid_mix = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    unpaid_pair = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    entitlement_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    book_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    history_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    raise_years = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    raise_sessions = "abababab-abab-4aba-8aba-abababababab"
    hist_a = "12121212-1212-4121-8121-121212121212"
    hist_b = "34343434-3434-4343-8343-343434343434"
    hist_shared = "cdcdcdcd-cdcd-4cdc-8cdc-cdcdcdcdcdcd"
    try:
        _apply_migrations(url)

        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at
            ) VALUES
              (%s, 'raise-user', 2025, '{"y":2025}'::jsonb, 'cs_year_a', now()),
              (%s, 'raise-user', 2026, '{"y":2026}'::jsonb, 'cs_year_b', now())
            """,
            (raise_years.upper(), raise_years),
        )
        code, output = _psql(url, migration)
        assert code != 0
        assert "packet snapshots for one analysis UUID have more than one tax year" in output
        spellings = conn.execute(
            """
            SELECT analysis_id, tax_year
            FROM year_close_packet_snapshots
            WHERE user_id = 'raise-user'
            ORDER BY tax_year
            """
        ).fetchall()
        assert spellings == [(raise_years.upper(), 2025), (raise_years, 2026)]
        conn.execute("DELETE FROM year_close_packet_snapshots WHERE user_id = 'raise-user'")

        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at
            ) VALUES
              (%s, 'session-user', 2025, '{}'::jsonb, 'cs_one', now()),
              (%s, 'session-user', 2025, '{}'::jsonb, 'cs_two', now())
            """,
            (raise_sessions.upper(), raise_sessions),
        )
        code, output = _psql(url, migration)
        assert code != 0
        assert "different checkout sessions" in output
        assert conn.execute(
            """
            SELECT count(*), count(DISTINCT analysis_id)
            FROM year_close_packet_snapshots
            WHERE user_id = 'session-user'
            """
        ).fetchone() == (2, 2)
        conn.execute("DELETE FROM year_close_packet_snapshots WHERE user_id = 'session-user'")

        _insert_identity_row(conn, hist_a, "hist-collide", hist_shared.upper())
        _insert_identity_row(conn, hist_b, "hist-collide", hist_shared)
        code, output = _psql(url, migration)
        assert code != 0
        assert "two portfolio_analyses rows share one analysis UUID" in output
        remaining = conn.execute(
            """
            SELECT id::text FROM portfolio_analyses
            WHERE user_id = 'hist-collide'
            ORDER BY id::text
            """
        ).fetchall()
        assert remaining == [(hist_a,), (hist_b,)]
        conn.execute("DELETE FROM portfolio_analyses WHERE user_id = 'hist-collide'")

        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id,
              paid_at, updated_at
            ) VALUES
              (%s, 'mix-user', 2025, '{"marker":"paid"}'::jsonb, 'cs_paid', now(), now()),
              (%s, 'mix-user', 2025, '{"marker":"unpaid"}'::jsonb, NULL, NULL, now())
            """,
            (paid_mix.upper(), paid_mix),
        )
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, paid_at, updated_at
            ) VALUES
              (%s, 'unpaid-user', 2025, '{"marker":"old"}'::jsonb, NULL, '2020-01-01T00:00:00Z'),
              (%s, 'unpaid-user', 2025, '{"marker":"new"}'::jsonb, NULL, '2026-06-01T00:00:00Z')
            """,
            (unpaid_pair.upper(), unpaid_pair),
        )
        conn.execute(
            """
            INSERT INTO year_close_packet_entitlements (
              user_id, tax_year, packet_session_id, analysis_id
            ) VALUES ('ent-user', 2024, 'cs_ent', %s)
            """,
            (entitlement_id.upper(),),
        )
        conn.execute(
            """
            INSERT INTO portfolio_activity_books (
              user_id, analysis_id, filename, transactions
            ) VALUES ('book-user', %s, 'book.csv', '[{"symbol":"AMD"}]'::jsonb)
            """,
            (book_id.upper(),),
        )
        _insert_identity_row(conn, history_id, "hist-user", history_id.upper())

        code, output = _psql(url, migration)
        assert code == 0, output

        mixed = conn.execute(
            """
            SELECT analysis_id, tax_year, paid_at IS NOT NULL, packet_payload->>'marker'
            FROM year_close_packet_snapshots
            WHERE user_id = 'mix-user'
            """
        ).fetchall()
        assert mixed == [(paid_mix, 2025, True, "paid")]

        unpaid = conn.execute(
            """
            SELECT analysis_id, packet_payload->>'marker'
            FROM year_close_packet_snapshots
            WHERE user_id = 'unpaid-user'
            """
        ).fetchall()
        assert unpaid == [(unpaid_pair, "new")]

        assert conn.execute(
            """
            SELECT analysis_id, count(*)
            FROM year_close_packet_entitlements
            WHERE user_id = 'ent-user'
            GROUP BY analysis_id
            """
        ).fetchone() == (entitlement_id, 1)

        assert conn.execute(
            """
            SELECT analysis_id, transactions
            FROM portfolio_activity_books
            WHERE user_id = 'book-user'
            """
        ).fetchone()[0] == book_id

        history = conn.execute(
            """
            SELECT id::text, result->>'analysis_id'
            FROM portfolio_analyses
            WHERE user_id = 'hist-user'
            """
        ).fetchone()
        assert history == (history_id, history_id)
    finally:
        conn.close()


def test_migration_012_aborts_on_unpaid_other_year_spelling(postgres):
    url, conn = postgres("oth_jor36_unpaid_years")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    canonical = "abababab-abab-4aba-8aba-abababababab"
    try:
        _apply_migrations(url)
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, paid_at
            ) VALUES
              (%s, 'unpaid-years', 2025, '{"marker":"low"}'::jsonb, NULL),
              (%s, 'unpaid-years', 2026, '{"marker":"up"}'::jsonb, NULL)
            """,
            (canonical, canonical.upper()),
        )
        code, output = _psql(url, migration)
        assert code != 0
        assert "packet snapshots for one analysis UUID have more than one tax year" in output
        rows = conn.execute(
            """
            SELECT analysis_id, tax_year, packet_payload->>'marker', paid_at IS NULL
            FROM year_close_packet_snapshots
            WHERE user_id = 'unpaid-years'
            ORDER BY tax_year
            """
        ).fetchall()
        assert rows == [
            (canonical, 2025, "low", True),
            (canonical.upper(), 2026, "up", True),
        ]
    finally:
        conn.close()


def test_migration_012_aborts_on_entitlement_case_variants_in_different_years(postgres):
    url, conn = postgres("oth_jor36_ent_years")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    canonical = "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc"
    try:
        _apply_migrations(url)
        conn.execute(
            """
            INSERT INTO year_close_packet_entitlements (
              user_id, tax_year, packet_session_id, analysis_id
            ) VALUES
              ('ent-years', 2025, 'cs_a', %s),
              ('ent-years', 2026, 'cs_b', %s)
            """,
            (canonical.upper(), canonical),
        )
        code, output = _psql(url, migration)
        assert code != 0
        assert "packet entitlements for one analysis UUID have more than one tax year" in output
        rows = conn.execute(
            """
            SELECT analysis_id, tax_year, packet_session_id
            FROM year_close_packet_entitlements
            WHERE user_id = 'ent-years'
            ORDER BY tax_year
            """
        ).fetchall()
        assert rows == [
            (canonical.upper(), 2025, "cs_a"),
            (canonical, 2026, "cs_b"),
        ]
    finally:
        conn.close()


def test_migration_012_keeps_non_null_payload_when_collapsing_same_session(postgres):
    url, conn = postgres("oth_jor36_payload_keeper")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    canonical = "cdcdcdcd-cdcd-4cdc-8cdc-cdcdcdcdcdcd"
    try:
        _apply_migrations(url)
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id,
              paid_at, updated_at
            ) VALUES
              (%s, 'payload-user', 2025, '{"marker":"document"}'::jsonb, 'cs_same',
               '2024-01-01T00:00:00Z', '2020-01-01T00:00:00Z'),
              (%s, 'payload-user', 2025, NULL, 'cs_same',
               '2026-01-01T00:00:00Z', '2026-06-01T00:00:00Z')
            """,
            (canonical, canonical.upper()),
        )
        code, output = _psql(url, migration)
        assert code == 0, output
        rows = conn.execute(
            """
            SELECT analysis_id, packet_payload->>'marker', paid_at IS NOT NULL
            FROM year_close_packet_snapshots
            WHERE user_id = 'payload-user'
            """
        ).fetchall()
        assert rows == [(canonical, "document", True)]
    finally:
        conn.close()


def test_migration_012_aborts_when_paid_null_would_discard_only_payload(postgres):
    url, conn = postgres("oth_jor36_paid_null_payload")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    canonical = "efefefef-efef-4efe-8efe-efefefefefef"
    try:
        _apply_migrations(url)
        conn.execute(
            """
            INSERT INTO year_close_packet_snapshots (
              analysis_id, user_id, tax_year, packet_payload, packet_session_id,
              paid_at, updated_at
            ) VALUES
              (%s, 'payload-pair', 2025, '{"marker":"only-pdf"}'::jsonb, NULL,
               NULL, '2020-01-01T00:00:00Z'),
              (%s, 'payload-pair', 2025, NULL, 'cs_paid',
               '2026-01-01T00:00:00Z', '2026-06-01T00:00:00Z')
            """,
            (canonical, canonical.upper()),
        )
        code, output = _psql(url, migration)
        assert code != 0
        assert "only non-null payload" in output
        rows = conn.execute(
            """
            SELECT analysis_id, packet_payload->>'marker', paid_at IS NOT NULL,
                   packet_session_id
            FROM year_close_packet_snapshots
            WHERE user_id = 'payload-pair'
            ORDER BY analysis_id
            """
        ).fetchall()
        assert set(rows) == {
            (canonical, "only-pdf", False, None),
            (canonical.upper(), None, True, "cs_paid"),
        }
        entitlements = conn.execute(
            """
            SELECT count(*) FROM year_close_packet_entitlements
            WHERE user_id = 'payload-pair'
            """
        ).fetchone()
        assert entitlements[0] == 0
    finally:
        conn.close()


def test_migration_012_applies_single_spelling_two_entitlement_years(postgres):
    url, conn = postgres("oth_jor36_one_spelling_two_years")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    canonical = "fefefefe-fefe-4fef-8fef-fefefefefefe"
    try:
        _apply_migrations(url)
        conn.execute(
            """
            INSERT INTO year_close_packet_entitlements (
              user_id, tax_year, packet_session_id, analysis_id
            ) VALUES
              ('one-spelling', 2024, 'cs_x', %s),
              ('one-spelling', 2025, 'cs_y', %s)
            """,
            (canonical, canonical),
        )
        code, output = _psql(url, migration)
        assert code == 0, output
        rows = conn.execute(
            """
            SELECT analysis_id, tax_year, packet_session_id
            FROM year_close_packet_entitlements
            WHERE user_id = 'one-spelling'
            ORDER BY tax_year
            """
        ).fetchall()
        assert rows == [
            (canonical, 2024, "cs_x"),
            (canonical, 2025, "cs_y"),
        ]
    finally:
        conn.close()


def test_migration_012_leaves_conflict_entitlement_intact(postgres):
    url, conn = postgres("oth_jor36_conflict_receipt")
    migration = (SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql").read_text()
    canonical = "dededede-dede-4ded-8ded-dededededede"
    receipt_id = f"conflict:{canonical}"
    try:
        _apply_migrations(url)
        conn.execute(
            """
            INSERT INTO year_close_packet_entitlements (
              user_id, tax_year, packet_session_id, analysis_id
            ) VALUES ('conflict-user', 2026, 'cs_conflict', %s)
            """,
            (receipt_id,),
        )
        code, output = _psql(url, migration)
        assert code == 0, output
        row = conn.execute(
            """
            SELECT analysis_id, tax_year, packet_session_id
            FROM year_close_packet_entitlements
            WHERE user_id = 'conflict-user'
            """
        ).fetchone()
        assert row == (receipt_id, 2026, "cs_conflict")
    finally:
        conn.close()


def _resolve_migrated_analysis(conn, user_id: str, analysis_id: str):
    """Primary key, then embedded analysis_id, then alias. Same order as the app."""
    by_pk = conn.execute(
        """
        SELECT id::text, result->>'analysis_id'
        FROM portfolio_analyses
        WHERE user_id = %s AND id = %s::uuid
        """,
        (user_id, analysis_id),
    ).fetchone()
    if by_pk:
        return by_pk
    by_embedded = conn.execute(
        """
        SELECT id::text, result->>'analysis_id'
        FROM portfolio_analyses
        WHERE user_id = %s AND result->>'analysis_id' = %s
        """,
        (user_id, analysis_id),
    ).fetchone()
    if by_embedded:
        return by_embedded
    alias = conn.execute(
        """
        SELECT canonical_analysis_id::text
        FROM portfolio_analysis_id_aliases
        WHERE user_id = %s AND legacy_row_id = %s::uuid
        """,
        (user_id, analysis_id),
    ).fetchone()
    if not alias:
        return None
    return conn.execute(
        """
        SELECT id::text, result->>'analysis_id'
        FROM portfolio_analyses
        WHERE user_id = %s AND id = %s::uuid
        """,
        (user_id, alias[0]),
    ).fetchone()


def test_migration_012_does_not_vacate_an_embedded_primary_key(postgres):
    url, conn = postgres("oth_jor36_vacated")
    source = "22222222-2222-4222-8222-222222222222"
    destination = "33333333-3333-4333-8333-333333333333"
    embedded_row = "11111111-1111-4111-8111-111111111111"
    case_source = "55555555-5555-4555-8555-555555555555"
    case_destination = "66666666-6666-4666-8666-666666666666"
    case_embedded_row = "44444444-4444-4444-8444-444444444444"
    free_legacy = "77777777-7777-4777-8777-777777777777"
    free_canonical = "88888888-8888-4888-8888-888888888888"
    try:
        applied = _apply_migrations(url)
        assert "Applying 012_unify_analysis_identity.sql" in applied
        _insert_identity_row(conn, embedded_row, "same-user", source)
        _insert_identity_row(conn, source, "same-user", destination)
        _insert_identity_row(conn, case_embedded_row, "same-user", case_source.upper())
        _insert_identity_row(conn, case_source, "same-user", case_destination)
        _insert_identity_row(conn, free_legacy, "same-user", free_canonical)

        migration_sql = (
            SERVER_DIR / "migrations" / "012_unify_analysis_identity.sql"
        ).read_text()
        output = _apply_sql(url, "SET client_min_messages TO notice;\n" + migration_sql)
        assert "skip collision analysis_id" in output
        assert source in output

        resolved = _resolve_migrated_analysis(conn, "same-user", source)
        assert resolved == (source, destination)
        assert resolved != (embedded_row, source)
        assert conn.execute(
            """
            SELECT id::text FROM portfolio_analyses
            WHERE user_id = 'same-user' AND id = %s
            """,
            (case_source,),
        ).fetchone() == (case_source,)
        assert conn.execute(
            """
            SELECT count(*) FROM portfolio_analysis_id_aliases
            WHERE user_id = 'same-user'
              AND legacy_row_id IN (%s::uuid, %s::uuid)
            """,
            (source, case_source),
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT legacy_row_id::text, canonical_analysis_id::text
            FROM portfolio_analysis_id_aliases
            WHERE user_id = 'same-user' AND legacy_row_id = %s
            """,
            (free_legacy,),
        ).fetchone() == (free_legacy, free_canonical)
    finally:
        conn.close()
