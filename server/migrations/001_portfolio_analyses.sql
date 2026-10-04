-- Portfolio Analysis History table
-- Apply with the other files in server/migrations/, in filename order.
-- See docs/SUPABASE_SETUP.md. Do not add columns by hand.
--
-- `summary` feeds the history sidebar. `result` is the JSON object
-- server/db.py already inserts and reads (including analysis_id). It is
-- nullable so rows saved before the column existed stay valid.
-- CREATE TABLE IF NOT EXISTS does not add `result` to a table created by
-- an older copy of this file. 004_year_close_packet_snapshots.sql does.

CREATE TABLE IF NOT EXISTS portfolio_analyses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  filename TEXT NOT NULL DEFAULT 'upload.csv',
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  positions_count INTEGER NOT NULL DEFAULT 0,
  total_market_value NUMERIC NOT NULL DEFAULT 0,
  -- Public/redacted analysis only; full paid packet snapshots use the private
  -- year_close_packet_snapshots table.
  result JSONB,
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
  TO service_role
  WITH CHECK (true);

-- Server-side owner-scoped history updates; clients receive read-only access.
CREATE POLICY "Service role can update analyses"
  ON portfolio_analyses
  FOR UPDATE
  TO service_role
  USING (true)
  WITH CHECK (true);

-- Allow service role to delete (cleanup)
CREATE POLICY "Service role can delete analyses"
  ON portfolio_analyses
  FOR DELETE
  TO service_role
  USING (true);
