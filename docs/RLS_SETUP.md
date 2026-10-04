"""
Row Level Security (RLS) Setup Guide for OptionsTaxHub

This document provides instructions for enabling Row Level Security (RLS)
in Supabase to enforce database-level access control.

## Why RLS Matters

RLS ensures that:
1. Users can only access their own data at the database level (not just application level)
2. Even if someone bypasses the backend, they cannot access other users' data
3. Service role keys bypass RLS and must stay on the backend

## Current Implementation

The app now:
- Extracts user_id from JWT tokens (no more client-supplied user IDs)
- Uses Supabase Auth for user authentication
- Enforces ownership checks at the API layer

`user_id` is `text` on `portfolio_analyses`, `tax_profiles`,
`year_close_packet_snapshots`, and `year_close_packet_entitlements`. An owner
check is `(auth.uid())::text = user_id`.

Portfolio history and tax profiles are read-only to authenticated clients. The
backend uses its service-role key for those writes, with explicit `user_id`
filters in application code. `portfolio_analyses.result` can contain packet
access flags, so that JSON is not writable from a browser. `save_tax_profile`
upserts `tax_profiles` with the same service-role client. Packet snapshot and
entitlement tables stay server-only.

## Steps to Enable RLS

RLS is defined only by `server/migrations/`. Apply those files in filename order, including `009_rls_owner_select_service_role_writes.sql` after `008_portfolio_analyses_one_analysis_id.sql` (see [Supabase setup](SUPABASE_SETUP.md)). `010_enable_rls_revoke_unused_service_role_privileges.sql` is the next file after `009_rls_owner_select_service_role_writes.sql`. 010 enables row level security on the four tables 009 touches: `portfolio_analyses`, `tax_profiles`, `year_close_packet_snapshots`, and `year_close_packet_entitlements`. It revokes `TRUNCATE`, `REFERENCES`, and `TRIGGER` from `service_role`. Packet tables still rely on hosted `service_role` BYPASSRLS. No policy change for that. 009 and 010 must be applied together in one transaction, the same way `server/scripts/apply_migrations.sh` does (one `psql --single-transaction` around those two files). The Supabase SQL editor must not stop at 009 and must not run 009 and 010 as separate commits. Other migration files may still be applied one file at a time. If someone runs 009 without 010, authenticated SELECT on those tables is unrestricted wherever RLS is off. There is no separate RLS script.

`011_private_activity_books.sql` is the next file after `010_enable_rls_revoke_unused_service_role_privileges.sql`. Apply it on its own, not inside the 009 and 010 transaction. It creates server-only `portfolio_activity_books`, one private trade book per account. It grants nothing to `anon` or `authenticated` and adds no client policy. Hosted `service_role` has BYPASSRLS. This change does not apply 011 to Supabase project ref `vgrlucxqncajjdoaoctq`.

### Step 1: Access Supabase Dashboard
1. Go to https://app.supabase.com
2. Select your project "OptionsTaxHub"
3. Apply `server/migrations/` if you have not already (SQL Editor or `server/scripts/apply_migrations.sh`)

### Step 2: What the migrations enable

`001_portfolio_analyses.sql` creates `portfolio_analyses` and enables RLS. `002_tax_profiles.sql` creates `tax_profiles` and enables RLS. `007_restrict_analysis_client_writes.sql` drops legacy client write policies on history, including "Users can update their own analyses".

`009_rls_owner_select_service_role_writes.sql` is the next step after `008_portfolio_analyses_one_analysis_id.sql`. `010_enable_rls_revoke_unused_service_role_privileges.sql` is the next file after `009_rls_owner_select_service_role_writes.sql`. 009 drops the older permissive policies and the duplicate live policy names, then leaves this end state:

- `portfolio_analyses`: one client `SELECT` policy, `TO authenticated`, `USING ((auth.uid())::text = user_id)`, named "Users can view own analyses". `SELECT`, `INSERT`, `UPDATE`, and `DELETE` for the server are `TO service_role`.
- `tax_profiles`: one client `SELECT` policy, `TO authenticated`, `USING ((auth.uid())::text = user_id)`, named "Users can view own tax profile". `SELECT`, `INSERT`, and `UPDATE` for the server are `TO service_role` so the upsert can read and write the existing row. There is no client insert or update policy.
- `year_close_packet_snapshots` and `year_close_packet_entitlements`: RLS stays enabled. `009` adds no policies and no grants for `anon` or `authenticated`.

Service-role writes use `TO service_role` with `USING` / `WITH CHECK` true. They are not a public policy that checks `auth.role() = 'service_role'`.

`010_enable_rls_revoke_unused_service_role_privileges.sql` is the next file after `009_rls_owner_select_service_role_writes.sql`. 010 enables row level security on the four tables 009 touches: `portfolio_analyses`, `tax_profiles`, `year_close_packet_snapshots`, and `year_close_packet_entitlements`. It revokes `TRUNCATE`, `REFERENCES`, and `TRIGGER` from `service_role`. Packet tables still rely on hosted `service_role` BYPASSRLS. No policy change for that. 009 and 010 must be applied together in one transaction, the same way `server/scripts/apply_migrations.sh` does (one `psql --single-transaction` around those two files). The Supabase SQL editor must not stop at 009 and must not run 009 and 010 as separate commits. Other migration files may still be applied one file at a time. If someone runs 009 without 010, authenticated SELECT on those tables is unrestricted wherever RLS is off.

### Step 3: Keep History and Tax-Profile Writes on the Backend

`server/db.py` uses `get_supabase()` (the service-role key) for `portfolio_analyses` and `tax_profiles`. Scope each write by the authenticated `user_id`. Keep the service-role key on the backend.

Packet tables are reached the same way. A browser token does not read or write them.

### Step 4: Test RLS Policies

1. Sign in as User A and verify they can read their own portfolio history and tax profile
2. Sign in as User B and verify they cannot read User A's rows
3. Verify authenticated clients cannot insert, update, or delete portfolio history or tax profiles
4. Verify an authenticated user cannot insert a history or tax-profile row owned by someone else
5. Verify backend writes use the service role and filter by `user_id`

Local Postgres coverage is `server/tests/test_rls_ownership.py` (see [Supabase setup](SUPABASE_SETUP.md)).

## Verification Checklist

- [ ] RLS enabled on portfolio_analyses, tax_profiles, and both packet tables
- [ ] `user_id` is text, and owner SELECT uses `(auth.uid())::text = user_id`
- [ ] Authenticated users have one SELECT policy each on `portfolio_analyses` and `tax_profiles`
- [ ] Service role can SELECT/INSERT/UPDATE/DELETE portfolio history, `TO service_role`
- [ ] Service role can SELECT/INSERT/UPDATE tax profiles, `TO service_role`
- [ ] User A cannot access User B's data
- [ ] No authenticated client write policies exist on `portfolio_analyses` or `tax_profiles`
- [ ] Packet tables have no anon/authenticated policies or grants
- [ ] Backend properly extracts user_id from JWT token
- [ ] Frontend sends JWT token in Authorization header

## Troubleshooting

### "Permission denied" errors after enabling RLS
Check that:
1. Your user is authenticated (JWT token valid)
2. The policy expression is `(auth.uid())::text = user_id` (`user_id` is text)
3. Reads use the authenticated role and writes use the backend service role (`TO service_role`)

### Service role key bypasses RLS
This is expected. Keep it on the backend, and scope history writes by the
authenticated user's ID in application code.

## Security Benefits

With RLS enabled:
1. ✅ Database-level access control (impossible to bypass)
2. ✅ No risk of showing wrong user's data even if app has bugs
3. ✅ Compliant with privacy regulations (GDPR, etc.)
4. ✅ Separation of concerns (auth at multiple layers)
5. ✅ Audit trail of who accessed what data when

## References

- Supabase RLS Documentation: https://supabase.com/docs/guides/auth/row-level-security
- PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
"""
