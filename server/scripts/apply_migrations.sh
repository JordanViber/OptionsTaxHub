#!/bin/sh
# Apply the versioned SQL files in server/migrations/ in filename order.
#
# Fresh database (Supabase already provides auth.uid and the service_role,
# anon, and authenticated roles):
#   DATABASE_URL='postgresql://...' sh server/scripts/apply_migrations.sh
#
# Database whose portfolio_analyses table came from an older summary-only 001.
# Do not re-run 001: CREATE TABLE IF NOT EXISTS will not add result, and its
# CREATE POLICY statements are not repeatable. Start at the first file that
# has not been applied. 004 adds nullable result JSONB without deleting rows:
#   APPLY_FROM=004_year_close_packet_snapshots.sql \
#     DATABASE_URL='postgresql://...' sh server/scripts/apply_migrations.sh
#
# DATABASE_URL is a direct Postgres URI. It is not the Supabase REST URL.
# Run this only against the database you intend to migrate.
#
# The shared Supabase project ref vgrlucxqncajjdoaoctq is refused unless
# OPTAX_ALLOW_LIVE_SUPABASE_REF is set to a non-empty value. Unset or empty
# still refuses. The check is a case-insensitive string match on DATABASE_URL
# and it runs before psql. Do not set the override for a normal apply.
#
# 009_rls_owner_select_service_role_writes.sql and the following
# 010_enable_rls_revoke_unused_service_role_privileges.sql run in one
# psql --single-transaction when both are in the apply set and 010 is the
# next file. psql can wrap those two -f files without editing 009: 009 has
# no top-level BEGIN/COMMIT and no CREATE INDEX CONCURRENTLY. Every other
# file stays its own psql invocation. APPLY_FROM=010 applies 010 alone. If
# 010 is not present, 009 is applied alone.
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required. Use a direct Postgres URI, not the Supabase REST URL." >&2
  exit 1
fi

# Refuse the shared project before any psql invocation.
url_folded=$(printf '%s' "$DATABASE_URL" | tr '[:upper:]' '[:lower:]')
case "$url_folded" in
  *vgrlucxqncajjdoaoctq*)
    if [ -z "${OPTAX_ALLOW_LIVE_SUPABASE_REF:-}" ]; then
      echo "Refusing to apply migrations: DATABASE_URL contains Supabase project ref vgrlucxqncajjdoaoctq." >&2
      echo "Set OPTAX_ALLOW_LIVE_SUPABASE_REF to a non-empty value to override. Unset or empty still refuses. This check runs before psql." >&2
      exit 1
    fi
    ;;
esac

ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
MIGRATION_009="009_rls_owner_select_service_role_writes.sql"
MIGRATION_010="010_enable_rls_revoke_unused_service_role_privileges.sql"
started=0
applied=0
if [ -z "${APPLY_FROM:-}" ]; then
  started=1
fi

set --
for file in "$ROOT"/server/migrations/*.sql; do
  base=$(basename "$file")
  if [ "$started" -eq 0 ]; then
    if [ "$base" != "$APPLY_FROM" ]; then
      continue
    fi
    started=1
  fi
  set -- "$@" "$file"
done

while [ "$#" -gt 0 ]; do
  file=$1
  base=$(basename "$file")
  next_base=""
  if [ "$#" -ge 2 ]; then
    next_base=$(basename "$2")
  fi
  if [ "$base" = "$MIGRATION_009" ] && [ "$next_base" = "$MIGRATION_010" ]; then
    echo "Applying $base"
    echo "Applying $next_base"
    psql "$DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f "$file" -f "$2"
    applied=$((applied + 2))
    shift 2
  else
    echo "Applying $base"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$file"
    applied=$((applied + 1))
    shift
  fi
done

if [ "$applied" -eq 0 ]; then
  echo "No migrations applied (APPLY_FROM=${APPLY_FROM:-} matched nothing in server/migrations)." >&2
  exit 1
fi
