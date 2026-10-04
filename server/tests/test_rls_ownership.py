"""RLS ownership for portfolio history and tax profiles.

Local Postgres only. Do not point this at Supabase, Render, or any hosted
database. This module never reads DATABASE_URL from the environment or from
.env. The connection is OPTAX_TEST_DATABASE_URL, or a local socket when that
variable is unset. _assert_local runs before every DROP DATABASE and refuses
a libpq host or hostaddr that is not localhost, 127.0.0.1, or ::1, including
?host= and ?hostaddr=. It also refuses a non-local PGHOST or PGHOSTADDR when
libpq would still apply that variable: both, when the URL has no host and no
hostaddr, and PGHOSTADDR when the URL names a host but does not set hostaddr.
A URL that already names a host is not refused for PGHOST. A URL that already
sets hostaddr is not refused for PGHOSTADDR. It refuses a service-based
connection before connecting and before any DROP DATABASE. A non-empty libpq
service parameter, including a URI query service=, is refused. A non-empty
PGSERVICE environment variable is refused when the connection string does
not already set service. A socket URL such as postgresql:///postgres has no
host and no hostaddr, so PGSERVICE can still point pg_service.conf at a
hosted database and is refused. The service file is not read. It also
refuses project ref vgrlucxqncajjdoaoctq.

Migration 010 enables row level security on every table 009 touches and
revokes TRUNCATE, REFERENCES, and TRIGGER from service_role. The apply
script refuses a DATABASE_URL that contains that project ref unless
OPTAX_ALLOW_LIVE_SUPABASE_REF is a non-empty value. Guard tests use a
stub psql and do not open a connection.

    cd server
    pip install 'psycopg[binary]'
    OPTAX_TEST_DATABASE_URL=postgresql:///postgres \\
      pytest tests/test_rls_ownership.py -q
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest


SERVER_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS = SERVER_DIR / "migrations"
APPLY_SCRIPT = SERVER_DIR / "scripts" / "apply_migrations.sh"
MIGRATION_009 = "009_rls_owner_select_service_role_writes.sql"
MIGRATION_010 = "010_enable_rls_revoke_unused_service_role_privileges.sql"
MIGRATION_011 = "011_private_activity_books.sql"
_UNUSED_SERVICE_PRIVS = frozenset({"TRUNCATE", "REFERENCES", "TRIGGER"})
_SERVICE_DML = {
    "portfolio_analyses": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "tax_profiles": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "year_close_packet_snapshots": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "year_close_packet_entitlements": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "portfolio_activity_books": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
}

USER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SERVICE_USER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

PACKET_TABLES = (
    "year_close_packet_snapshots",
    "year_close_packet_entitlements",
)
# Not part of PACKET_TABLES: migration 010's SQL text does not mention this table.
PRIVATE_BOOK_TABLES = ("portfolio_activity_books",)
CLIENT_TABLES = ("portfolio_analyses", "tax_profiles")

LIVE_DUPLICATE_NAMES = (
    ("portfolio_analyses", "Users can create analyses"),
    ("portfolio_analyses", "Users can update their own analyses"),
    ("portfolio_analyses", "Users can delete their own analyses"),
    ("portfolio_analyses", "Users can view their own portfolio analyses"),
    ("tax_profiles", "Service role full access"),
    ("tax_profiles", "Users can update own tax profile"),
    ("tax_profiles", "Users can update their own tax profile"),
    ("tax_profiles", "Users can insert own tax profile"),
    ("tax_profiles", "Users can insert their own tax profile"),
    ("tax_profiles", "Users can view their own tax profile"),
)

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PRIV_NAME = {
    "r": "SELECT",
    "a": "INSERT",
    "w": "UPDATE",
    "d": "DELETE",
    "D": "TRUNCATE",
    "x": "REFERENCES",
    "t": "TRIGGER",
    "SELECT": "SELECT",
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
    "DELETE": "DELETE",
    "TRUNCATE": "TRUNCATE",
    "REFERENCES": "REFERENCES",
    "TRIGGER": "TRIGGER",
}


def _admin_url() -> str:
    return os.environ.get("OPTAX_TEST_DATABASE_URL", "postgresql:///postgres")


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


def _refuse_remote_env(name: str) -> None:
    """Refuse a non-local PGHOST or PGHOSTADDR. Unset variables are allowed.

    conninfo_to_dict does not apply these variables. Read os.environ and do
    not delete them. Every non-empty comma-separated entry must be local.
    """
    if name not in os.environ:
        return
    for host in _csv_targets(os.environ[name]):
        if not _is_local_host(host):
            pytest.fail(
                f"Refusing non-local {name} {host}. "
                "Use an empty local Postgres, not Supabase or Render."
            )


def _refuse_service(url: str) -> None:
    """Refuse a service-based connection before connecting.

    conninfo_to_dict does not apply PGSERVICE and does not read
    pg_service.conf. A non-empty service parameter, including a URI query
    service=, is refused. A non-empty PGSERVICE is refused when the
    connection string does not already set service. An empty service= is
    set, so libpq ignores PGSERVICE.
    """
    info = _libpq_conninfo(url)
    if "service" in info:
        service = info.get("service") or ""
    else:
        service = os.environ.get("PGSERVICE")
    if service is not None and str(service).strip():
        pytest.fail(
            "Refusing service-based database connection. "
            "Use an empty local Postgres, not Supabase or Render."
        )


def _assert_local(url: str) -> None:
    """Refuse non-local libpq targets before any DROP DATABASE.

    Service-based connections are refused before connecting. A non-empty
    libpq service parameter, including a URI query service=, is refused. A
    non-empty PGSERVICE is refused when the connection string does not
    already set service. A socket URL such as postgresql:///postgres has no
    host and no hostaddr, so PGSERVICE can still select a hosted
    pg_service.conf entry. The service file is not read.

    When the URL omits host, libpq uses PGHOST. When it omits hostaddr,
    libpq uses PGHOSTADDR even if host is already local. An empty hostaddr
    is still set, so libpq ignores PGHOSTADDR, but it names no address:
    PGHOST remains the target when host is omitted too. Those PGHOST and
    PGHOSTADDR checks are unchanged.
    """
    if "vgrlucxqncajjdoaoctq" in url.lower():
        pytest.fail(
            "Refusing hosted Supabase project ref vgrlucxqncajjdoaoctq. "
            "Use an empty local Postgres, not Supabase or Render."
        )
    _refuse_service(url)
    for host in _connection_targets(url):
        if not _is_local_host(host):
            pytest.fail(
                f"Refusing non-local database host {host}. "
                "Use an empty local Postgres, not Supabase or Render."
            )
    info = _libpq_conninfo(url)
    host_specified = "host" in info
    hostaddr_specified = "hostaddr" in info
    if not host_specified and not _csv_targets(info.get("hostaddr")):
        _refuse_remote_env("PGHOST")
    if not hostaddr_specified:
        _refuse_remote_env("PGHOSTADDR")


def _url_for_database(admin_url: str, name: str) -> str:
    parts = urlsplit(admin_url)
    if parts.netloc == "":
        query = f"?{parts.query}" if parts.query else ""
        return f"{parts.scheme}:///{name}{query}"
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _apply_sql(database_url: str, sql: str) -> None:
    _assert_local(database_url)
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


def _apply_files(database_url: str, files: list[Path]) -> None:
    _assert_local(database_url)
    for file in files:
        completed = subprocess.run(
            ["psql", database_url, "-v", "ON_ERROR_STOP=1", "-f", str(file)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"psql failed on {file.name} ({completed.returncode})\n"
                f"{completed.stdout}\n{completed.stderr}"
            )


def _migrations_through_008() -> list[Path]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    through = [file for file in files if file.name < "009_"]
    assert through, "migrations 001-008 are missing"
    assert through[-1].name.startswith("008_")
    assert all(not file.name.startswith("009_") for file in through)
    return through


def _apply_migrations(database_url: str, apply_from: str | None = None) -> str:
    _assert_local(database_url)
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
    """Local stand-in for Supabase roles and auth.uid(). Not a migration.

    auth.uid() reads request.jwt.claim.sub, which the tests set before
    SET ROLE authenticated. A missing claim yields NULL.
    """
    conn.execute("CREATE SCHEMA IF NOT EXISTS auth")
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$
          SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        $$
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION auth.role() RETURNS text
        LANGUAGE sql STABLE
        AS $$
          SELECT current_setting('request.jwt.claim.role', true)
        $$
        """
    )
    for role in ("anon", "authenticated", "service_role"):
        found = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
        if found is None:
            conn.execute(f"CREATE ROLE {role} NOLOGIN")


def _prepare_session(conn) -> None:
    conn.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role")
    conn.execute("GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role")
    conn.execute(
        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA auth TO anon, authenticated, service_role"
    )


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


def _reset_role(conn) -> None:
    conn.execute("RESET ROLE")
    conn.execute("SELECT set_config('request.jwt.claim.sub', '', false)")


def _as_anon(conn) -> None:
    _reset_role(conn)
    conn.execute("SET ROLE anon")


def _as_user(conn, user_id: str) -> None:
    _reset_role(conn)
    conn.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (user_id,))
    conn.execute("SET ROLE authenticated")


def _as_service(conn) -> None:
    _reset_role(conn)
    conn.execute("SET ROLE service_role")


def _expect_denied(conn, sql: str, params=None) -> Exception:
    try:
        conn.execute(sql, params)
    except Exception as exc:
        state = getattr(exc, "sqlstate", None)
        assert state == "42501", exc
        return exc
    raise AssertionError(f"statement succeeded: {sql}")


def _policies(conn, tables: tuple[str, ...] | None = None) -> list[dict]:
    names = list(tables or CLIENT_TABLES + PACKET_TABLES + PRIVATE_BOOK_TABLES)
    rows = conn.execute(
        """
        SELECT c.relname AS table_name,
               p.polname AS policy_name,
               CASE p.polcmd
                 WHEN 'r' THEN 'SELECT'
                 WHEN 'a' THEN 'INSERT'
                 WHEN 'w' THEN 'UPDATE'
                 WHEN 'd' THEN 'DELETE'
                 WHEN '*' THEN 'ALL'
                 ELSE p.polcmd::text
               END AS cmd,
               p.polpermissive AS permissive,
               CASE
                 WHEN p.polroles = '{0}'::oid[] OR cardinality(p.polroles) = 0
                   THEN ARRAY['public']::name[]
                 ELSE ARRAY(
                   SELECT rolname FROM pg_roles
                   WHERE oid = ANY (p.polroles)
                   ORDER BY rolname
                 )
               END AS roles,
               pg_get_expr(p.polqual, p.polrelid) AS qual,
               pg_get_expr(p.polwithcheck, p.polrelid) AS with_check
        FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = ANY (%s)
        ORDER BY c.relname, p.polname
        """,
        (names,),
    ).fetchall()
    found = []
    for row in rows:
        roles = {str(role).lower() for role in (row[4] or [])}
        found.append(
            {
                "table": row[0],
                "name": row[1],
                "cmd": row[2],
                "permissive": row[3],
                "roles": roles,
                "qual": row[5],
                "with_check": row[6],
            }
        )
    return found


def _compact(expr: str | None) -> str:
    return re.sub(r"\s+", "", expr or "").lower()


def _is_owner_check(expr: str | None) -> bool:
    compact = _compact(expr)
    if "user_id::uuid" in compact:
        return False
    return "(auth.uid())::text=user_id" in compact and "auth.uid()=user_id" not in compact


def _privileges(conn, tables: tuple[str, ...]) -> dict[str, dict[str, set[str]]]:
    rows = conn.execute(
        """
        SELECT c.relname AS table_name,
               CASE WHEN priv.grantee = 0 THEN 'public' ELSE r.rolname END AS grantee,
               priv.privilege_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(
          COALESCE(c.relacl, acldefault('r', c.relowner))
        ) AS priv
        LEFT JOIN pg_roles r ON r.oid = priv.grantee
        WHERE n.nspname = 'public'
          AND c.relname = ANY (%s)
        """,
        (list(tables),),
    ).fetchall()
    found: dict[str, dict[str, set[str]]] = {table: {} for table in tables}
    for table_name, grantee, privilege in rows:
        if grantee is None:
            continue
        name = str(grantee).lower()
        priv = _PRIV_NAME.get(str(privilege), str(privilege).upper())
        found[table_name].setdefault(name, set()).add(priv)
    return found


def _assert_policy_shape(conn) -> None:
    rows = _policies(conn)
    by_key = {(row["table"], row["name"]): row for row in rows}
    client_rows = [row for row in rows if row["table"] in CLIENT_TABLES]
    packet_rows = [row for row in rows if row["table"] in PACKET_TABLES]
    private_rows = [row for row in rows if row["table"] in PRIVATE_BOOK_TABLES]
    assert packet_rows == []
    assert private_rows == []
    assert len(client_rows) == len(by_key)
    expected = {
        ("portfolio_analyses", "Users can view own analyses"),
        ("portfolio_analyses", "Service role can select analyses"),
        ("portfolio_analyses", "Service role can insert analyses"),
        ("portfolio_analyses", "Service role can update analyses"),
        ("portfolio_analyses", "Service role can delete analyses"),
        ("tax_profiles", "Users can view own tax profile"),
        ("tax_profiles", "Service role can select tax profiles"),
        ("tax_profiles", "Service role can upsert tax profiles"),
        ("tax_profiles", "Service role can update tax profiles"),
    }
    assert set(by_key) == expected, sorted(by_key)

    analyses_select = by_key[("portfolio_analyses", "Users can view own analyses")]
    tax_select = by_key[("tax_profiles", "Users can view own tax profile")]
    for select in (analyses_select, tax_select):
        assert select["cmd"] == "SELECT"
        assert select["permissive"] is True
        assert select["roles"] == {"authenticated"}
        assert _is_owner_check(select["qual"])
        assert select["with_check"] is None

    insert_analyses = by_key[("portfolio_analyses", "Service role can insert analyses")]
    insert_tax = by_key[("tax_profiles", "Service role can upsert tax profiles")]
    for insert in (insert_analyses, insert_tax):
        assert insert["cmd"] == "INSERT"
        assert insert["roles"] == {"service_role"}
        assert _compact(insert["with_check"]) == "true"
        assert insert["qual"] is None

    update_analyses = by_key[("portfolio_analyses", "Service role can update analyses")]
    update_tax = by_key[("tax_profiles", "Service role can update tax profiles")]
    for update in (update_analyses, update_tax):
        assert update["cmd"] == "UPDATE"
        assert update["roles"] == {"service_role"}
        assert _compact(update["qual"]) == "true"
        assert _compact(update["with_check"]) == "true"

    delete_analyses = by_key[("portfolio_analyses", "Service role can delete analyses")]
    assert delete_analyses["cmd"] == "DELETE"
    assert delete_analyses["roles"] == {"service_role"}
    assert _compact(delete_analyses["qual"]) == "true"

    owner_selects = {
        row["table"]: row["name"]
        for row in client_rows
        if row["cmd"] == "SELECT" and row["roles"] == {"authenticated"}
    }
    assert owner_selects == {
        "portfolio_analyses": "Users can view own analyses",
        "tax_profiles": "Users can view own tax profile",
    }
    service_selects = [row for row in client_rows if row["cmd"] == "SELECT" and row["roles"] == {"service_role"}]
    assert {(row["table"], row["name"]) for row in service_selects} == {
        ("portfolio_analyses", "Service role can select analyses"),
        ("tax_profiles", "Service role can select tax profiles"),
    }
    for row in service_selects:
        assert _compact(row["qual"]) == "true"
        assert row["with_check"] is None

    for row in client_rows:
        if "public" not in row["roles"]:
            continue
        if row["cmd"] not in {"INSERT", "UPDATE", "DELETE", "ALL"}:
            continue
        assert _compact(row["qual"]) != "true"
        assert _compact(row["with_check"]) != "true"

    for row in client_rows:
        if row["table"] != "tax_profiles":
            continue
        expr = f"{row['qual'] or ''} {row['with_check'] or ''}".lower()
        if "auth.role()" in expr:
            assert "public" not in row["roles"]

    for table, name in LIVE_DUPLICATE_NAMES:
        assert (table, name) not in by_key


def _assert_grants(conn) -> None:
    privs = _privileges(conn, CLIENT_TABLES + PACKET_TABLES + PRIVATE_BOOK_TABLES)
    for table in CLIENT_TABLES:
        assert privs[table].get("anon", set()) == set()
        assert privs[table].get("public", set()) == set()
        assert privs[table].get("authenticated", set()) == {"SELECT"}
        write_privs = {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}
        assert write_privs.isdisjoint(privs[table].get("authenticated", set()))
        assert write_privs.isdisjoint(privs[table].get("anon", set()))

    for table in PACKET_TABLES + PRIVATE_BOOK_TABLES:
        assert privs[table].get("anon", set()) == set()
        assert privs[table].get("authenticated", set()) == set()
        assert privs[table].get("public", set()) == set()

    for table, required in _SERVICE_DML.items():
        role_privs = privs[table].get("service_role", set())
        missing = required - role_privs
        assert not missing, (table, sorted(missing), sorted(role_privs))
        unused = _UNUSED_SERVICE_PRIVS & role_privs
        assert not unused, (table, sorted(unused), sorted(role_privs))

    _assert_rls_enabled(conn)


def _assert_rls_enabled(conn) -> None:
    """RLS is on for every table 009 touches, and it is not forced."""
    rows = conn.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = ANY (%s)
        """,
        (list(CLIENT_TABLES + PACKET_TABLES + PRIVATE_BOOK_TABLES),),
    ).fetchall()
    found = {row[0]: (bool(row[1]), bool(row[2])) for row in rows}
    for table in CLIENT_TABLES + PACKET_TABLES + PRIVATE_BOOK_TABLES:
        assert table in found, table
        enabled, forced = found[table]
        assert enabled, table
        assert not forced, table


def _seed(conn) -> None:
    _reset_role(conn)
    conn.execute("DELETE FROM public.portfolio_activity_books")
    conn.execute("DELETE FROM public.year_close_packet_entitlements")
    conn.execute("DELETE FROM public.year_close_packet_snapshots")
    conn.execute("DELETE FROM public.portfolio_analyses")
    conn.execute("DELETE FROM public.tax_profiles")
    conn.execute(
        """
        INSERT INTO public.portfolio_analyses (user_id, filename)
        VALUES (%s, 'a.csv'), (%s, 'b.csv')
        """,
        (USER_A, USER_B),
    )
    conn.execute(
        """
        INSERT INTO public.tax_profiles (user_id, filing_status)
        VALUES (%s, 'single'), (%s, 'single')
        """,
        (USER_A, USER_B),
    )
    conn.execute(
        """
        INSERT INTO public.year_close_packet_snapshots (analysis_id, user_id, tax_year)
        VALUES ('packet-a', %s, 2025)
        """,
        (USER_A,),
    )
    conn.execute(
        """
        INSERT INTO public.year_close_packet_entitlements (
          user_id, tax_year, packet_session_id, analysis_id
        ) VALUES (%s, 2025, 'cs_test', 'packet-a')
        """,
        (USER_A,),
    )
    conn.execute(
        """
        INSERT INTO public.portfolio_activity_books (user_id, analysis_id, filename, transactions)
        VALUES (%s, 'analysis-a', 'a.csv', '[{"instrument": "AAPL"}]'::jsonb)
        """,
        (USER_A,),
    )


def _expect_private_book_denied(conn, user_id: str) -> None:
    _expect_denied(conn, "SELECT user_id FROM public.portfolio_activity_books")
    _expect_denied(
        conn,
        "INSERT INTO public.portfolio_activity_books (user_id, analysis_id, transactions) "
        "VALUES (%s, 'forged', '[]'::jsonb)",
        (user_id,),
    )
    _expect_denied(
        conn,
        "UPDATE public.portfolio_activity_books SET filename = 'hacked.csv' WHERE user_id = %s",
        (user_id,),
    )
    _expect_denied(
        conn,
        "DELETE FROM public.portfolio_activity_books WHERE user_id = %s",
        (user_id,),
    )


def _visible_users(conn, table: str) -> list[str]:
    rows = conn.execute(f"SELECT user_id FROM public.{table} ORDER BY user_id").fetchall()
    return [row[0] for row in rows]


def _exercise_access(conn) -> None:
    _prepare_session(conn)
    _seed(conn)

    _as_anon(conn)
    for sql, params in (
        ("SELECT user_id FROM public.portfolio_analyses", None),
        ("SELECT user_id FROM public.tax_profiles", None),
        ("SELECT user_id FROM public.year_close_packet_snapshots", None),
        ("SELECT user_id FROM public.year_close_packet_entitlements", None),
        (
            "INSERT INTO public.portfolio_analyses (user_id, filename) VALUES (%s, 'anon.csv')",
            (USER_B,),
        ),
        (
            "UPDATE public.portfolio_analyses SET filename = 'anon.csv' WHERE user_id = %s",
            (USER_B,),
        ),
        ("DELETE FROM public.portfolio_analyses WHERE user_id = %s", (USER_B,)),
        (
            "INSERT INTO public.tax_profiles (user_id) VALUES (%s)",
            (SERVICE_USER,),
        ),
        (
            "UPDATE public.tax_profiles SET filing_status = 'married' WHERE user_id = %s",
            (USER_B,),
        ),
    ):
        _expect_denied(conn, sql, params)
    _expect_private_book_denied(conn, USER_B)

    _as_user(conn, USER_A)
    assert conn.execute("SELECT auth.uid()::text").fetchone()[0] == USER_A
    assert _visible_users(conn, "portfolio_analyses") == [USER_A]
    assert _visible_users(conn, "tax_profiles") == [USER_A]
    _expect_denied(conn, "SELECT user_id FROM public.year_close_packet_snapshots")
    _expect_denied(conn, "SELECT user_id FROM public.year_close_packet_entitlements")
    _expect_denied(
        conn,
        "INSERT INTO public.portfolio_analyses (user_id, filename) VALUES (%s, 'forged.csv')",
        (USER_B,),
    )
    _expect_denied(
        conn,
        "INSERT INTO public.tax_profiles (user_id) VALUES (%s)",
        (USER_B,),
    )
    _expect_denied(
        conn,
        "UPDATE public.portfolio_analyses SET filename = 'hacked.csv' WHERE user_id = %s",
        (USER_B,),
    )
    _expect_denied(
        conn,
        "DELETE FROM public.portfolio_analyses WHERE user_id = %s",
        (USER_B,),
    )
    _expect_denied(
        conn,
        "UPDATE public.tax_profiles SET filing_status = 'married' WHERE user_id = %s",
        (USER_B,),
    )
    _expect_private_book_denied(conn, USER_A)

    _as_user(conn, USER_B)
    assert conn.execute("SELECT auth.uid()::text").fetchone()[0] == USER_B
    assert _visible_users(conn, "portfolio_analyses") == [USER_B]
    assert _visible_users(conn, "tax_profiles") == [USER_B]
    _expect_denied(
        conn,
        "INSERT INTO public.portfolio_analyses (user_id, filename) VALUES (%s, 'forged.csv')",
        (USER_A,),
    )
    _expect_private_book_denied(conn, USER_B)

    # Grants alone must not let an authenticated user write another user's row.
    _reset_role(conn)
    conn.execute(
        "GRANT INSERT, UPDATE, DELETE ON public.portfolio_analyses TO authenticated"
    )
    conn.execute(
        "GRANT INSERT, UPDATE, DELETE ON public.tax_profiles TO authenticated"
    )
    _as_user(conn, USER_A)
    forged = _expect_denied(
        conn,
        "INSERT INTO public.portfolio_analyses (user_id, filename) VALUES (%s, 'forged.csv')",
        (USER_B,),
    )
    assert "row-level security" in str(forged).lower()
    own_insert = _expect_denied(
        conn,
        "INSERT INTO public.portfolio_analyses (user_id, filename) VALUES (%s, 'own.csv')",
        (USER_A,),
    )
    assert "row-level security" in str(own_insert).lower()
    forged_profile = _expect_denied(
        conn,
        "INSERT INTO public.tax_profiles (user_id) VALUES (%s)",
        (USER_B,),
    )
    assert "row-level security" in str(forged_profile).lower()
    updated = conn.execute(
        "UPDATE public.portfolio_analyses SET filename = 'hacked.csv' WHERE user_id = %s",
        (USER_B,),
    )
    assert updated.rowcount == 0
    deleted = conn.execute(
        "DELETE FROM public.portfolio_analyses WHERE user_id = %s",
        (USER_B,),
    )
    assert deleted.rowcount == 0
    updated_profile = conn.execute(
        "UPDATE public.tax_profiles SET filing_status = 'married' WHERE user_id = %s",
        (USER_B,),
    )
    assert updated_profile.rowcount == 0
    deleted_profile = conn.execute(
        "DELETE FROM public.tax_profiles WHERE user_id = %s",
        (USER_B,),
    )
    assert deleted_profile.rowcount == 0

    _reset_role(conn)
    conn.execute(
        "REVOKE INSERT, UPDATE, DELETE ON public.portfolio_analyses FROM authenticated"
    )
    conn.execute(
        "REVOKE INSERT, UPDATE, DELETE ON public.tax_profiles FROM authenticated"
    )
    b_filename = conn.execute(
        "SELECT filename FROM public.portfolio_analyses WHERE user_id = %s",
        (USER_B,),
    ).fetchone()[0]
    assert b_filename == "b.csv"
    b_status = conn.execute(
        "SELECT filing_status FROM public.tax_profiles WHERE user_id = %s",
        (USER_B,),
    ).fetchone()[0]
    assert b_status == "single"

    # A SELECT grant still hides the owner's private book: RLS is on and there
    # is no policy. Local service_role also lacks BYPASSRLS, so this test does
    # not require a successful service_role read of the private book.
    _reset_role(conn)
    conn.execute("GRANT SELECT ON public.portfolio_activity_books TO authenticated")
    _as_user(conn, USER_A)
    assert conn.execute("SELECT user_id FROM public.portfolio_activity_books").fetchall() == []
    _reset_role(conn)
    conn.execute("REVOKE SELECT ON public.portfolio_activity_books FROM authenticated")

    _as_service(conn)
    conn.execute(
        "INSERT INTO public.portfolio_analyses (user_id, filename) VALUES (%s, 'svc.csv')",
        (USER_A,),
    )
    conn.execute(
        "UPDATE public.portfolio_analyses SET filename = 'svc2.csv' WHERE filename = 'svc.csv'"
    )
    conn.execute("DELETE FROM public.portfolio_analyses WHERE filename = 'svc2.csv'")
    conn.execute(
        "INSERT INTO public.tax_profiles (user_id, filing_status) VALUES (%s, 'single')",
        (SERVICE_USER,),
    )
    conn.execute(
        "UPDATE public.tax_profiles SET state = 'NY' WHERE user_id = %s",
        (SERVICE_USER,),
    )
    conn.execute(
        """
        INSERT INTO public.tax_profiles (user_id, filing_status)
        VALUES (%s, 'married')
        ON CONFLICT (user_id) DO UPDATE SET filing_status = EXCLUDED.filing_status
        """,
        (SERVICE_USER,),
    )

    _reset_role(conn)
    leftover = conn.execute(
        "SELECT count(*) FROM public.portfolio_analyses WHERE filename IN ('svc.csv', 'svc2.csv')"
    ).fetchone()[0]
    assert leftover == 0
    saved = conn.execute(
        "SELECT filing_status, state FROM public.tax_profiles WHERE user_id = %s",
        (SERVICE_USER,),
    ).fetchone()
    assert saved[0] == "married"
    assert saved[1] == "NY"
    assert _visible_users(conn, "portfolio_analyses") == [USER_A, USER_B]


LIVE_SHAPE_SQL = """
DROP POLICY IF EXISTS "Users can view own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can view their own portfolio analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can create analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can update their own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can delete their own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can insert analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can update analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can delete analyses" ON public.portfolio_analyses;

CREATE POLICY "Users can view own analyses"
  ON public.portfolio_analyses
  FOR SELECT
  TO public
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Users can view their own portfolio analyses"
  ON public.portfolio_analyses
  FOR SELECT
  TO public
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Users can create analyses"
  ON public.portfolio_analyses
  FOR INSERT
  TO public
  WITH CHECK ((auth.uid())::text = user_id);

CREATE POLICY "Users can update their own analyses"
  ON public.portfolio_analyses
  FOR UPDATE
  TO public
  USING ((auth.uid())::text = user_id)
  WITH CHECK ((auth.uid())::text = user_id);

CREATE POLICY "Users can delete their own analyses"
  ON public.portfolio_analyses
  FOR DELETE
  TO public
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Service role can insert analyses"
  ON public.portfolio_analyses
  FOR INSERT
  TO service_role
  WITH CHECK (true);

CREATE POLICY "Service role can update analyses"
  ON public.portfolio_analyses
  FOR UPDATE
  TO service_role
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Service role can delete analyses"
  ON public.portfolio_analyses
  FOR DELETE
  TO service_role
  USING (true);

DROP POLICY IF EXISTS "Service role full access" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can view own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can view their own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can update own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can update their own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can insert own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can insert their own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Service role can upsert tax profiles" ON public.tax_profiles;

CREATE POLICY "Service role full access"
  ON public.tax_profiles
  FOR ALL
  TO public
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Users can view own tax profile"
  ON public.tax_profiles
  FOR SELECT
  TO public
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Users can view their own tax profile"
  ON public.tax_profiles
  FOR SELECT
  TO public
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Users can update own tax profile"
  ON public.tax_profiles
  FOR UPDATE
  TO public
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Users can update their own tax profile"
  ON public.tax_profiles
  FOR UPDATE
  TO public
  USING ((auth.uid())::text = user_id)
  WITH CHECK ((auth.uid())::text = user_id);

CREATE POLICY "Users can insert own tax profile"
  ON public.tax_profiles
  FOR INSERT
  TO public
  WITH CHECK ((auth.uid())::text = user_id);

CREATE POLICY "Users can insert their own tax profile"
  ON public.tax_profiles
  FOR INSERT
  TO public
  WITH CHECK ((auth.uid())::text = user_id);

CREATE POLICY "Service role can upsert tax profiles"
  ON public.tax_profiles
  FOR INSERT
  TO service_role
  WITH CHECK (true);
"""

OLD_PUBLIC_TRUE_SQL = """
DROP POLICY IF EXISTS "Service role can insert analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can update analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can delete analyses" ON public.portfolio_analyses;

CREATE POLICY "Service role can insert analyses"
  ON public.portfolio_analyses
  FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Service role can update analyses"
  ON public.portfolio_analyses
  FOR UPDATE
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Service role can delete analyses"
  ON public.portfolio_analyses
  FOR DELETE
  USING (true);
"""


def _public_true_writes(conn) -> list[str]:
    found = []
    for row in _policies(conn, ("portfolio_analyses",)):
        if "public" not in row["roles"]:
            continue
        if row["cmd"] not in {"INSERT", "UPDATE", "DELETE", "ALL"}:
            continue
        if _compact(row["qual"]) == "true" or _compact(row["with_check"]) == "true":
            found.append(row["name"])
    return found


def _finish(conn) -> None:
    try:
        _reset_role(conn)
    finally:
        conn.close()


def test_migration_009_sql_matches_owner_and_service_role_rules():
    sql = (MIGRATIONS / MIGRATION_009).read_text()
    assert "(auth.uid())::text = user_id" in sql
    assert "user_id::uuid" not in sql.lower()
    assert "auth.role()" not in sql.lower()
    assert "TO authenticated" in sql
    assert "TO service_role" in sql
    for _table, name in LIVE_DUPLICATE_NAMES:
        assert name in sql
    for statement in sql.split(";"):
        lowered = statement.lower()
        if "grant" in lowered and "year_close_packet" in lowered:
            raise AssertionError(statement.strip())
    packet_section = sql.split("REVOKE ALL ON TABLE public.year_close_packet_snapshots")[-1]
    assert "CREATE POLICY" not in packet_section
    assert "GRANT" not in packet_section.upper()


def test_refuses_hosted_supabase_and_remote_hosts():
    pytest.importorskip("psycopg.conninfo")
    for url in (
        "postgresql://db.vgrlucxqncajjdoaoctq.supabase.co/postgres",
        "postgresql:///postgres?host=db.example.com",
        "postgresql://localhost/postgres?hostaddr=10.0.0.1",
    ):
        with pytest.raises(pytest.fail.Exception, match="Refusing"):
            _assert_local(url)
    _assert_local("postgresql:///postgres")
    _assert_local("postgresql://127.0.0.1/postgres")


def test_refuses_pghost_when_url_omits_host(monkeypatch):
    pytest.importorskip("psycopg.conninfo")
    monkeypatch.setenv("PGHOST", "db.example.com")
    with pytest.raises(pytest.fail.Exception, match="Refusing"):
        _assert_local("postgresql:///postgres")


def test_refuses_pghostaddr_when_url_omits_host(monkeypatch):
    pytest.importorskip("psycopg.conninfo")
    monkeypatch.setenv("PGHOSTADDR", "10.0.0.1")
    with pytest.raises(pytest.fail.Exception, match="Refusing"):
        _assert_local("postgresql:///postgres")


def test_documented_socket_url_passes_when_pghost_and_pghostaddr_unset(monkeypatch):
    pytest.importorskip("psycopg.conninfo")
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    _assert_local("postgresql:///postgres")


def test_url_host_refuses_remote_pghostaddr(monkeypatch):
    pytest.importorskip("psycopg.conninfo")
    monkeypatch.setenv("PGHOSTADDR", "10.0.0.1")
    with pytest.raises(pytest.fail.Exception, match="Refusing"):
        _assert_local("postgresql://127.0.0.1/postgres")


def test_url_host_ignores_remote_pghost(monkeypatch):
    pytest.importorskip("psycopg.conninfo")
    monkeypatch.setenv("PGHOST", "db.example.com")
    _assert_local("postgresql://127.0.0.1/postgres")


def test_refuses_service_based_socket_url_before_connect(monkeypatch):
    """Fail closed before connect. PGSERVICE and service= must not skip."""
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.setenv("PGSERVICE", "hosted")

    def _refuse_connect(*_args, **_kwargs):
        raise AssertionError("connection attempted")

    monkeypatch.setattr(subprocess, "run", _refuse_connect)
    try:
        import psycopg
    except Exception:
        psycopg = None
    else:
        monkeypatch.setattr(psycopg, "connect", _refuse_connect)

    with pytest.raises(pytest.fail.Exception, match="Refusing service-based"):
        _assert_local("postgresql:///postgres")

    monkeypatch.delenv("PGSERVICE", raising=False)
    with pytest.raises(pytest.fail.Exception, match="Refusing service-based"):
        _assert_local("postgresql:///postgres?service=hosted")


def test_fresh_migrations_policy_shape_and_access(postgres):
    url, conn = postgres("oth_jor33_fresh")
    try:
        output = _apply_migrations(url)
        assert f"Applying {MIGRATION_009}" in output
        assert f"Applying {MIGRATION_010}" in output
        assert f"Applying {MIGRATION_011}" in output
        assert "Applying 008_portfolio_analyses_one_analysis_id.sql" in output
        _assert_policy_shape(conn)
        _assert_grants(conn)
        _exercise_access(conn)
    finally:
        _finish(conn)


def test_live_duplicates_removed_by_009_only(postgres):
    url, conn = postgres("oth_jor33_live")
    try:
        _apply_files(url, _migrations_through_008())
        _apply_sql(url, LIVE_SHAPE_SQL)
        conn.execute(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
            ON TABLE public.portfolio_analyses, public.tax_profiles
            TO anon, authenticated, service_role
            """
        )
        before = _policies(conn, CLIENT_TABLES)
        before_names = {(row["table"], row["name"]) for row in before}
        for table, name in LIVE_DUPLICATE_NAMES:
            assert (table, name) in before_names
        analysis_selects = [
            row["name"]
            for row in before
            if row["table"] == "portfolio_analyses" and row["cmd"] == "SELECT"
        ]
        assert len(analysis_selects) == 2
        full_access = next(
            row for row in before if row["name"] == "Service role full access"
        )
        assert full_access["roles"] == {"public"}
        assert full_access["cmd"] == "ALL"
        assert "auth.role()" in (full_access["qual"] or "").lower()

        # APPLY_FROM=009 applies every later file, including 010.
        output = _apply_migrations(url, apply_from=MIGRATION_009)
        assert f"Applying {MIGRATION_009}" in output
        assert f"Applying {MIGRATION_010}" in output
        assert f"Applying {MIGRATION_011}" in output
        assert "Applying 001_portfolio_analyses.sql" not in output
        assert "Applying 008_portfolio_analyses_one_analysis_id.sql" not in output

        _assert_policy_shape(conn)
        _assert_grants(conn)
        service_privs = _privileges(
            conn, ("portfolio_analyses",) + PACKET_TABLES + PRIVATE_BOOK_TABLES
        )
        for table in ("portfolio_analyses",) + PACKET_TABLES + PRIVATE_BOOK_TABLES:
            role_privs = service_privs[table].get("service_role", set())
            assert "TRUNCATE" not in role_privs
            assert "REFERENCES" not in role_privs
            assert "TRIGGER" not in role_privs
            assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= role_privs
        _exercise_access(conn)
    finally:
        _finish(conn)


def test_old_public_check_true_policies_replaced_by_009(postgres):
    url, conn = postgres("oth_jor33_old001")
    try:
        _apply_files(url, _migrations_through_008())
        _apply_sql(url, OLD_PUBLIC_TRUE_SQL)
        assert set(_public_true_writes(conn)) == {
            "Service role can delete analyses",
            "Service role can insert analyses",
            "Service role can update analyses",
        }
        output = _apply_migrations(url, apply_from=MIGRATION_009)
        assert f"Applying {MIGRATION_010}" in output
        assert f"Applying {MIGRATION_011}" in output
        assert _public_true_writes(conn) == []
        _assert_policy_shape(conn)
        _assert_grants(conn)
        _exercise_access(conn)
    finally:
        _finish(conn)


def _sql_without_line_comments(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        lines.append(line)
    return "\n".join(lines).lower()


def test_migration_010_enables_rls_and_revokes_unused_privileges():
    sql = (MIGRATIONS / MIGRATION_010).read_text()
    lowered = sql.lower()
    assert "bypassrls" in lowered
    assert "packet tables rely on hosted service_role bypassrls" in lowered
    assert "no policy change for that" in lowered
    body = _sql_without_line_comments(sql)
    assert "create policy" not in body
    assert "force row level security" not in body
    assert "grant " not in body
    for table in CLIENT_TABLES + PACKET_TABLES:
        assert f"alter table public.{table} enable row level security" in body
        revoke = (
            "revoke truncate, references, trigger on table "
            f"public.{table} from service_role"
        )
        assert revoke in body


_LIVE_REF = "vgrlucxqncajjdoaoctq"
# The ref is only in application_name. The host is loopback, and the stub
# psql below never connects, so this cannot reach Supabase.
_REF_IN_LOCAL_URL = (
    "postgresql://postgres@127.0.0.1:9/postgres?application_name=" + _LIVE_REF
)
_LOCAL_STUB_URL = "postgresql://postgres@127.0.0.1:5432/postgres"


def _run_apply_script(tmp_path, database_url, extra_env=None, apply_from=None):
    """Run apply_migrations.sh against a stub psql that records argv and exits.

    The stub does not open a socket. Guard and transaction-grouping tests use
    it so a URL that contains the live project ref is never contacted.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log_path = tmp_path / "psql-invocations.log"
    if log_path.exists():
        log_path.unlink()
    fake = bin_dir / "psql"
    fake.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$PSQL_LOG"\n'
        "exit 0\n"
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["DATABASE_URL"] = database_url
    env["PSQL_LOG"] = str(log_path)
    env.pop("OPTAX_ALLOW_LIVE_SUPABASE_REF", None)
    env.pop("APPLY_FROM", None)
    if extra_env:
        env.update(extra_env)
    if apply_from is not None:
        env["APPLY_FROM"] = apply_from
    completed = subprocess.run(
        ["sh", str(APPLY_SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    text = log_path.read_text() if log_path.exists() else ""
    invocations = [line for line in text.splitlines() if line.strip()]
    return completed, invocations


def test_apply_migrations_refuses_live_ref_before_psql(tmp_path):
    header = APPLY_SCRIPT.read_text().split("set -eu", 1)[0]
    assert "OPTAX_ALLOW_LIVE_SUPABASE_REF" in header
    assert _LIVE_REF in header
    cases = (
        (_REF_IN_LOCAL_URL, None),
        (_REF_IN_LOCAL_URL, {"OPTAX_ALLOW_LIVE_SUPABASE_REF": ""}),
        (_REF_IN_LOCAL_URL.replace(_LIVE_REF, _LIVE_REF.upper()), None),
    )
    for url, extra in cases:
        completed, invocations = _run_apply_script(tmp_path, url, extra)
        assert completed.returncode != 0
        assert invocations == []
        assert _LIVE_REF in completed.stderr
        assert "OPTAX_ALLOW_LIVE_SUPABASE_REF" in completed.stderr
        assert "before psql" in completed.stderr


def test_apply_migrations_override_does_not_refuse(tmp_path):
    """A non-empty override passes the guard. The stub psql does not connect."""
    completed, invocations = _run_apply_script(
        tmp_path,
        _REF_IN_LOCAL_URL,
        {"OPTAX_ALLOW_LIVE_SUPABASE_REF": "yes"},
    )
    assert completed.returncode == 0
    assert "Refusing" not in completed.stderr
    assert invocations
    assert all("127.0.0.1" in line for line in invocations)
    assert all(_LIVE_REF in line for line in invocations)


def test_apply_migrations_wraps_009_and_010_in_one_transaction(tmp_path):
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    assert names[names.index(MIGRATION_009) + 1] == MIGRATION_010
    completed, invocations = _run_apply_script(tmp_path, _LOCAL_STUB_URL)
    assert completed.returncode == 0
    assert len(invocations) == len(names) - 1
    paired = [line for line in invocations if "--single-transaction" in line]
    assert len(paired) == 1
    assert MIGRATION_009 in paired[0]
    assert MIGRATION_010 in paired[0]
    assert paired[0].index(MIGRATION_009) < paired[0].index(MIGRATION_010)
    for line in invocations:
        if line == paired[0]:
            continue
        assert "--single-transaction" not in line
        assert MIGRATION_009 not in line
        assert MIGRATION_010 not in line
    for name in names:
        assert f"Applying {name}" in completed.stdout


def test_apply_from_009_pairs_only_with_010(tmp_path):
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    later = [name for name in names if name >= MIGRATION_009]
    assert later[:2] == [MIGRATION_009, MIGRATION_010]
    completed, invocations = _run_apply_script(
        tmp_path,
        _LOCAL_STUB_URL,
        apply_from=MIGRATION_009,
    )
    assert completed.returncode == 0
    rest = later[2:]
    assert len(invocations) == 1 + len(rest)
    paired = invocations[0]
    assert "--single-transaction" in paired
    assert paired.index(MIGRATION_009) < paired.index(MIGRATION_010)
    for line, name in zip(invocations[1:], rest):
        assert "--single-transaction" not in line
        assert name in line
        assert MIGRATION_009 not in line
    assert "Applying 001_portfolio_analyses.sql" not in completed.stdout
    assert f"Applying {MIGRATION_009}" in completed.stdout
    assert f"Applying {MIGRATION_010}" in completed.stdout


def test_apply_from_010_is_its_own_psql(tmp_path):
    later = sorted(
        path.name for path in MIGRATIONS.glob("*.sql") if path.name >= MIGRATION_010
    )
    assert later[0] == MIGRATION_010
    completed, invocations = _run_apply_script(
        tmp_path,
        _LOCAL_STUB_URL,
        apply_from=MIGRATION_010,
    )
    assert completed.returncode == 0
    assert len(invocations) == len(later)
    joined = "\n".join(invocations)
    assert "--single-transaction" not in joined
    assert MIGRATION_009 not in joined
    assert MIGRATION_010 in invocations[0]
    assert f"Applying {MIGRATION_009}" not in completed.stdout
    assert f"Applying {MIGRATION_010}" in completed.stdout
