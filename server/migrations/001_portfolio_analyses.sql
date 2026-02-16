-- Portfolio Analysis History table
-- Run this in the Supabase SQL Editor to create the table.
--
-- Stores a summary of each portfolio analysis upload per user.
-- Full position data is NOT persisted (processed in-memory per the security policy).

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

-- Index for fast user lookups sorted by recency
CREATE INDEX IF NOT EXISTS idx_portfolio_analyses_user_id
  ON portfolio_analyses (user_id, uploaded_at DESC);

-- Row-Level Security (RLS)
ALTER TABLE portfolio_analyses ENABLE ROW LEVEL SECURITY;

-- Allow users to read only their own rows
CREATE POLICY "Users can view own analyses"
  ON portfolio_analyses
  FOR SELECT
  USING (user_id = auth.uid()::text);

-- Allow service role to insert (server-side writes)
CREATE POLICY "Service role can insert analyses"
  ON portfolio_analyses
  FOR INSERT
  WITH CHECK (true);

-- Allow service role to delete (cleanup)
CREATE POLICY "Service role can delete analyses"
  ON portfolio_analyses
  FOR DELETE
  USING (true);
