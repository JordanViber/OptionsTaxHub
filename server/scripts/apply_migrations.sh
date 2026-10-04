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
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required. Use a direct Postgres URI, not the Supabase REST URL." >&2
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
started=0
applied=0
if [ -z "${APPLY_FROM:-}" ]; then
  started=1
fi

for file in "$ROOT"/server/migrations/*.sql; do
  base=$(basename "$file")
  if [ "$started" -eq 0 ]; then
    if [ "$base" != "$APPLY_FROM" ]; then
      continue
    fi
    started=1
  fi
  echo "Applying $base"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$file"
  applied=$((applied + 1))
done

if [ "$applied" -eq 0 ]; then
  echo "No migrations applied (APPLY_FROM=${APPLY_FROM:-} matched nothing in server/migrations)." >&2
  exit 1
fi
