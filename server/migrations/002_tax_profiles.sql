-- Tax Profiles table
-- Run this in the Supabase SQL Editor to create the table.
--
-- Stores each user's tax filing settings (filing status, income, state, tax year).
-- Used by the backend to compute accurate tax brackets and harvest savings estimates.

CREATE TABLE IF NOT EXISTS tax_profiles (
  user_id TEXT PRIMARY KEY,
  filing_status TEXT NOT NULL DEFAULT 'single',
  estimated_annual_income NUMERIC NOT NULL DEFAULT 75000,
  state TEXT NOT NULL DEFAULT '',
  tax_year INTEGER NOT NULL DEFAULT 2025,
  ai_suggestions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Row-Level Security (RLS)
ALTER TABLE tax_profiles ENABLE ROW LEVEL SECURITY;

-- Users can read only their own profile
CREATE POLICY "Users can view own tax profile"
  ON tax_profiles
  FOR SELECT
  USING (user_id = auth.uid()::text);

-- Users can update only their own profile
CREATE POLICY "Users can update own tax profile"
  ON tax_profiles
  FOR UPDATE
  USING (user_id = auth.uid()::text);

-- Service role can insert / upsert (server-side writes bypass RLS automatically)
CREATE POLICY "Service role can upsert tax profiles"
  ON tax_profiles
  FOR INSERT
  WITH CHECK (true);

-- Auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION update_tax_profiles_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tax_profiles_updated_at
  BEFORE UPDATE ON tax_profiles
  FOR EACH ROW
  EXECUTE FUNCTION update_tax_profiles_updated_at();
