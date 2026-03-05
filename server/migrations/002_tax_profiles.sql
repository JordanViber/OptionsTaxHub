-- Tax Profiles table
-- Applied to production via Supabase MCP (March 2026).
-- Run in the Supabase SQL Editor if setting up a new project from scratch.
--
-- NOTE: The live table was originally created with an `id uuid` primary key
-- and `user_id` as a plain text column. The schema below matches production.

CREATE TABLE IF NOT EXISTS tax_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  filing_status TEXT NOT NULL DEFAULT 'single',
  estimated_annual_income NUMERIC NOT NULL DEFAULT 75000,
  state TEXT NOT NULL DEFAULT '',
  tax_year INTEGER NOT NULL DEFAULT 2025,
  ai_suggestions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unique constraint on user_id required for ON CONFLICT (user_id) upserts
ALTER TABLE tax_profiles ADD CONSTRAINT tax_profiles_user_id_key UNIQUE (user_id);

-- Row-Level Security
ALTER TABLE tax_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own tax profile"
  ON tax_profiles FOR SELECT
  USING (user_id = auth.uid()::text);

CREATE POLICY "Users can update own tax profile"
  ON tax_profiles FOR UPDATE
  USING (user_id = auth.uid()::text);

CREATE POLICY "Service role can upsert tax profiles"
  ON tax_profiles FOR INSERT
  WITH CHECK (true);

-- Auto-update updated_at trigger
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

