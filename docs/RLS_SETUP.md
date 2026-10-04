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

Portfolio history is read-only to authenticated clients. The backend uses its
service-role key for history inserts, updates, and deletes, with explicit
`user_id` filters in application code. Do not create client INSERT, UPDATE, or
DELETE policies on `portfolio_analyses`: its `result` JSON must not be writable
from a browser because it can contain packet access flags.

## Steps to Enable RLS

RLS and the history policies are already in `server/migrations/`. Apply those files in order (see [Supabase setup](SUPABASE_SETUP.md)). Do not paste a second copy of this schema into the SQL Editor.

### Step 1: Access Supabase Dashboard
1. Go to https://app.supabase.com
2. Select your project "OptionsTaxHub"
3. Apply `server/migrations/` if you have not already (SQL Editor or `server/scripts/apply_migrations.sh`)

### Step 2: What the migrations enable

`001_portfolio_analyses.sql` enables RLS on `portfolio_analyses` and lets authenticated users read their own rows (`user_id = auth.uid()::text`). Inserts, updates, and deletes are granted to `service_role` only. `007_restrict_analysis_client_writes.sql` drops legacy client write policies, including "Users can update their own analyses". Do not recreate them. `result` must not be writable from a browser because it can contain packet access flags.

`002_tax_profiles.sql` enables RLS on `tax_profiles`.

### Step 3: Keep History Writes on the Backend

Do not switch `portfolio_analyses` writes to a browser-token client. The server
uses the service-role key for history writes and must scope each write by the
authenticated `user_id`. Never expose the service-role key to the frontend.

Tax-profile policies may allow authenticated users to manage their own profile.
They do not authorize or establish packet access.

### Step 4: Test RLS Policies

1. Sign in as User A and verify they can read their own portfolio history
2. Sign in as User B and verify they cannot read User A's history
3. Verify authenticated clients cannot insert, update, or delete portfolio history
4. Verify backend history writes use the service role and filter by `user_id`

## Verification Checklist

- [ ] RLS enabled on portfolio_analyses table
- [ ] RLS enabled on tax_profiles table
- [ ] Authenticated users have SELECT only on `portfolio_analyses`
- [ ] Service role can INSERT/UPDATE/DELETE portfolio history
- [ ] User A cannot access User B's data
- [ ] No authenticated client write policies exist on `portfolio_analyses`
- [ ] Backend properly extracts user_id from JWT token
- [ ] Frontend sends JWT token in Authorization header

## Troubleshooting

### "Permission denied" errors after enabling RLS
Check that:
1. Your user is authenticated (JWT token valid)
2. The auth.uid() in RLS policies matches your user ID
3. Reads use the authenticated role and writes use the backend service role

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
