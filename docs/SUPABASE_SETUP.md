# Supabase Setup Guide

## Local Development Setup

### 1. Create Supabase Project
1. Go to [supabase.com](https://supabase.com) and sign up
2. Click **New Project**
3. Fill in project details:
   - **Name:** OptionsTaxHub
   - **Password:** (save this securely)
   - **Region:** Choose closest to you (e.g., us-east-1)
4. Wait for project to initialize (1-2 minutes)

### 2. Get Your Keys
1. Go to **Settings** → **API**
2. Copy:
   - **Project URL** → `NEXT_PUBLIC_SUPABASE_URL`
   - **anon public** key → `NEXT_PUBLIC_SUPABASE_ANON_KEY`

### 3. Update Local Environment
1. Copy `.env.local.example` to `.env.local`:
   ```bash
   cp client/.env.local.example client/.env.local
   ```

2. Fill in your Supabase keys:
   ```
   NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key-here
   ```

### 4. Apply versioned migrations

A new Supabase project has Auth and no OptionsTaxHub tables. The schema is the SQL files in `server/migrations/`, applied once, in filename order. That set creates `portfolio_analyses` with nullable `result` JSONB (the analysis object `server/db.py` already saves, including `analysis_id`) and the unique index on `(user_id, (result->>'analysis_id'))`. `009_rls_owner_select_service_role_writes.sql` is the next step after `008_portfolio_analyses_one_analysis_id.sql`. It does not add tables. `010_enable_rls_revoke_unused_service_role_privileges.sql` is the next file after `009_rls_owner_select_service_role_writes.sql`. 010 enables row level security on the four tables 009 touches: `portfolio_analyses`, `tax_profiles`, `year_close_packet_snapshots`, and `year_close_packet_entitlements`. It revokes `TRUNCATE`, `REFERENCES`, and `TRIGGER` from `service_role`. Packet tables still rely on hosted `service_role` BYPASSRLS. No policy change for that. Apply the migration files in order. 009 and 010 must be applied together in one transaction, the same way `server/scripts/apply_migrations.sh` does (one `psql --single-transaction` around those two files). The Supabase SQL editor must not stop at 009 and must not run 009 and 010 as separate commits. Other migration files may still be applied one file at a time. If someone runs 009 without 010, authenticated SELECT on those tables is unrestricted wherever RLS is off. There is no separate RLS script to paste into the SQL editor.

`011_private_activity_books.sql` is the next file after `010_enable_rls_revoke_unused_service_role_privileges.sql`. Apply it on its own, not inside the 009 and 010 transaction. It creates server-only `portfolio_activity_books`, one private trade book per account. It grants nothing to `anon` or `authenticated` and adds no client policy. Hosted `service_role` has BYPASSRLS. This change does not apply 011 to Supabase project ref `vgrlucxqncajjdoaoctq`.

Use the project's direct Postgres URI (Supabase Dashboard → Project Settings → Database). That URI is not the REST URL or the service-role key. Run this only against the database you intend to migrate.

```bash
DATABASE_URL="postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres" \
  sh server/scripts/apply_migrations.sh
```

The Supabase SQL Editor applies the same files. Run each migration file in order, one file at a time, except `009_rls_owner_select_service_role_writes.sql` and `010_enable_rls_revoke_unused_service_role_privileges.sql`. `010_enable_rls_revoke_unused_service_role_privileges.sql` is the next file after `009_rls_owner_select_service_role_writes.sql`. 010 enables row level security on the four tables 009 touches: `portfolio_analyses`, `tax_profiles`, `year_close_packet_snapshots`, and `year_close_packet_entitlements`. It revokes `TRUNCATE`, `REFERENCES`, and `TRIGGER` from `service_role`. Packet tables still rely on hosted `service_role` BYPASSRLS. No policy change for that. 009 and 010 must be applied together in one transaction, the same way `server/scripts/apply_migrations.sh` does (one `psql --single-transaction` around those two files). The Supabase SQL editor must not stop at 009 and must not run 009 and 010 as separate commits. Other migration files may still be applied one file at a time. If someone runs 009 without 010, authenticated SELECT on those tables is unrestricted wherever RLS is off.

`001` adds `result` only when it creates the table. `CREATE TABLE IF NOT EXISTS` does not alter an older summary-only `portfolio_analyses`. Do not re-run `001` on that database: its `CREATE POLICY` statements are not repeatable, and re-running it still leaves the column missing. Apply the later files that are not on the database yet. `004_year_close_packet_snapshots.sql` runs `ADD COLUMN IF NOT EXISTS result JSONB`, keeps every existing row, and leaves `result` null until a new save. Continue through `008`, which adds the unique index the history helpers use, and then `009_rls_owner_select_service_role_writes.sql` together with `010_enable_rls_revoke_unused_service_role_privileges.sql`. `010_enable_rls_revoke_unused_service_role_privileges.sql` is the next file after `009_rls_owner_select_service_role_writes.sql`. 010 enables row level security on the four tables 009 touches: `portfolio_analyses`, `tax_profiles`, `year_close_packet_snapshots`, and `year_close_packet_entitlements`. It revokes `TRUNCATE`, `REFERENCES`, and `TRIGGER` from `service_role`. Packet tables still rely on hosted `service_role` BYPASSRLS. No policy change for that. 009 and 010 must be applied together in one transaction, the same way `server/scripts/apply_migrations.sh` does (one `psql --single-transaction` around those two files). The Supabase SQL editor must not stop at 009 and must not run 009 and 010 as separate commits. Other migration files may still be applied one file at a time. If someone runs 009 without 010, authenticated SELECT on those tables is unrestricted wherever RLS is off.

`user_id` is `text` on `portfolio_analyses`, `tax_profiles`, and both year-close packet tables. `009` leaves one authenticated `SELECT` policy on `portfolio_analyses` and one on `tax_profiles`, each with `USING ((auth.uid())::text = user_id)`. The server's `SELECT`, history `INSERT`/`UPDATE`/`DELETE`, and tax-profile `INSERT`/`UPDATE` policies are `TO service_role`. Packet tables stay server-only: `009` does not grant them to `anon` or `authenticated` and does not add policies. Run `009` from `server/migrations/` with the other files, together with the next file, `010_enable_rls_revoke_unused_service_role_privileges.sql`. 010 enables row level security on the four tables 009 touches: `portfolio_analyses`, `tax_profiles`, `year_close_packet_snapshots`, and `year_close_packet_entitlements`. It revokes `TRUNCATE`, `REFERENCES`, and `TRIGGER` from `service_role`. Packet tables still rely on hosted `service_role` BYPASSRLS. No policy change for that. 009 and 010 must be applied together in one transaction, the same way `server/scripts/apply_migrations.sh` does (one `psql --single-transaction` around those two files). The Supabase SQL editor must not stop at 009 and must not run 009 and 010 as separate commits. Other migration files may still be applied one file at a time. If someone runs 009 without 010, authenticated SELECT on those tables is unrestricted wherever RLS is off. Do not add those policies again by hand.

```bash
DATABASE_URL="postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres" \
  APPLY_FROM=004_year_close_packet_snapshots.sql \
  sh server/scripts/apply_migrations.sh
```

`002_tax_profiles.sql` adds a unique constraint without `IF NOT EXISTS`, so the full script is for a fresh database or for files that have not been applied yet. Running it a second time on a database that already finished `002` stops there.

If `portfolio_analyses` or `result` is missing, `save_analysis_history` raises `AnalysisSchemaError` and the API returns 503 with that message. A server with no Supabase client configured still skips history instead of crashing.

Check the committed files against an empty local Postgres. Do not point this at Supabase, Render, or any hosted database:

```bash
cd server
pip install 'psycopg[binary]'
OPTAX_TEST_DATABASE_URL="postgresql:///postgres" \
  pytest tests/test_analysis_result_migration.py tests/test_rls_ownership.py -q
```

The tests need local Postgres and `psql`. `test_analysis_result_migration.py` applies every migration, checks save, list, restore, and delete, and upgrades a summary-only table without deleting the historical row. `test_rls_ownership.py` checks anonymous, user A, user B, and service-role access after a fresh `001`–`010` apply and after the path that starts at `009`. A fresh apply includes `010`. The path that starts at `009` also applies `010` in the same transaction and cleans the older duplicate policies. Point `OPTAX_TEST_DATABASE_URL` at local Postgres only.

### 5. Test Locally
1. Start the dev server: `npm run dev` (client directory)
2. Navigate to http://localhost:3000
3. You'll be redirected to `/auth/signin`
4. Click "Create Account" to sign up
5. Check your email for confirmation link
6. Sign in and you should see the app dashboard

## Production Deployment (Render)

### 1. Add to Render Dashboard

#### For Production Backend:
1. Go to your **options-tax-hub-server-prod** service
2. **Environment** tab → **Add Environment Variable**
3. Add your Supabase URL and keys (same as local)

#### For Production Frontend:
1. Go to your **options-tax-hub-client-prod** service
2. **Environment** tab → **Add Environment Variables**
3. Add:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`

### 2. Supabase Production Setup

#### Configure Redirect URLs
1. Go to Supabase Dashboard → **Authentication** → **URL Configuration**
2. Add these redirect URLs:
   ```
   http://localhost:3000/auth/signin
   https://options-tax-hub-client-prod.onrender.com/auth/signin
   https://options-tax-hub-client-staging.onrender.com/auth/signin
   ```

#### Enable Email Provider (Optional)
1. **Authentication** → **Providers**
2. Email/Password is enabled by default
3. For custom emails, configure SMTP in **Settings** → **Email Templates**

## Features Implemented

✅ **Sign Up** - Create new account with email/password
✅ **Sign In** - Login with email/password
✅ **Sign Out** - Logout from app
✅ **Auth Context** - Global auth state across app
✅ **Protected Routes** - Only authenticated users see dashboard
✅ **User Profile** - Display user email in header
✅ **Environment Variables** - Local and deployed configs

## Next Steps

1. Apply `server/migrations/` (step 4). That creates portfolio history and tax profiles.
2. Once Supabase Auth is configured, users can create accounts.
3. Signed-in analyses are stored on `portfolio_analyses.result`. No extra table is required for that payload.

## Troubleshooting

### "Missing Supabase environment variables"
- Make sure `.env.local` has both keys
- Restart dev server after adding env vars

### Can't sign up / "Invalid credentials"
- Check Supabase Authentication is enabled
- Verify email matches your Supabase project settings

### Redirect loop on deployed version
- Confirm redirect URLs are added to Supabase
- Check `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are set in Render

## Resources
- [Supabase Docs](https://supabase.com/docs)
- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Next.js Auth Integration](https://supabase.com/docs/guides/auth/server-side/nextjs)
