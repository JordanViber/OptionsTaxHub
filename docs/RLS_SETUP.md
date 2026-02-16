"""
Row Level Security (RLS) Setup Guide for OptionsTaxHub

This document provides instructions for enabling Row Level Security (RLS) 
in Supabase to enforce database-level access control.

## Why RLS Matters

RLS ensures that:
1. Users can only access their own data at the database level (not just application level)
2. Even if someone bypasses the backend, they cannot access other users' data
3. Service role keys cannot bypass RLS policies when properly configured

## Current Implementation

The app now:
- Extracts user_id from JWT tokens (no more client-supplied user IDs)
- Uses Supabase Auth for user authentication
- Enforces ownership checks at the API layer

The next step is enabling RLS policies to enforce ownership at the database level.

## Steps to Enable RLS

### Step 1: Access Supabase Dashboard
1. Go to https://app.supabase.com
2. Select your project "OptionsTaxHub"
3. Go to "Authentication" > "Policies" or "SQL Editor"

### Step 2: Enable RLS on Tables

Run this SQL in the SQL Editor for each table that stores user data:

```sql
-- Enable RLS on portfolio_analyses table
ALTER TABLE portfolio_analyses ENABLE ROW LEVEL SECURITY;

-- Enable RLS on tax_profiles table
ALTER TABLE tax_profiles ENABLE ROW LEVEL SECURITY;
```

### Step 3: Create RLS Policies

#### Portfolio Analyses RLS Policies

```sql
-- Policy: Users can view their own analyses
CREATE POLICY "Users can view their own analyses"
  ON portfolio_analyses FOR SELECT
  USING (auth.uid() = user_id);

-- Policy: Users can create analyses
CREATE POLICY "Users can create analyses"
  ON portfolio_analyses FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Policy: Users can delete their own analyses
CREATE POLICY "Users can delete their own analyses"
  ON portfolio_analyses FOR DELETE
  USING (auth.uid() = user_id);

-- Policy: Users can update their own analyses
CREATE POLICY "Users can update their own analyses"
  ON portfolio_analyses FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
```

#### Tax Profiles RLS Policies

```sql
-- Policy: Users can view their own tax profile
CREATE POLICY "Users can view their own tax profile"
  ON tax_profiles FOR SELECT
  USING (auth.uid() = user_id);

-- Policy: Users can create their own tax profile
CREATE POLICY "Users can create their own tax profile"
  ON tax_profiles FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Policy: Users can update their own tax profile
CREATE POLICY "Users can update their own tax profile"
  ON tax_profiles FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- Policy: Users can delete their own tax profile
CREATE POLICY "Users can delete their own tax profile"
  ON tax_profiles FOR DELETE
  USING (auth.uid() = user_id);
```

### Step 4: Update Backend to Use Authenticated Client

Update `db.py` to use the user's JWT token instead of the service role key:

Currently, the backend uses:
```python
client = get_supabase()  # Uses service role (bypasses RLS)
```

For production with RLS enabled, you should:
1. Pass the user's access token to database functions
2. Create authenticated Supabase client with the token
3. Let RLS policies enforce access control

Example:
```python
from supabase import create_client, Client

def get_supabase_with_token(access_token: str) -> Client:
    """Create Supabase client authenticated with user's JWT token."""
    return create_client(
        SUPABASE_URL,
        SUPABASE_ANON_KEY,
        options={
            "headers": {
                "Authorization": f"Bearer {access_token}"
            }
        }
    )
```

### Step 5: Test RLS Policies

1. Sign in as User A and create a portfolio analysis
2. Sign in as User B and verify you CANNOT see User A's analysis
3. Verify User A can still see their own analysis
4. Delete policies and verify access is restricted correctly

## Verification Checklist

- [ ] RLS enabled on portfolio_analyses table
- [ ] RLS enabled on tax_profiles table
- [ ] All SELECT/INSERT/UPDATE/DELETE policies created
- [ ] User A cannot access User B's data
- [ ] Users can only create/update/delete their own records
- [ ] Backend properly extracts user_id from JWT token
- [ ] Frontend sends JWT token in Authorization header

## Troubleshooting

### "Permission denied" errors after enabling RLS
Check that:
1. Your user is authenticated (JWT token valid)
2. The auth.uid() in RLS policies matches your user ID
3. The table actually has the auth.uid() = user_id condition

### Service role key still bypasses RLS
This is expected - service role keys bypass RLS for admin operations.
In production, use the authenticated client with user tokens instead.

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