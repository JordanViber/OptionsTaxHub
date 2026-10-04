-- One private trade book per account. portfolio_analyses.result stays the
-- redacted public history document. Server-only. Apply as its own file after
-- 010. Do not edit 009 or 010. Hosted service_role has BYPASSRLS, so there
-- is no policy (same pattern as the packet tables). Deleting a history row
-- does not delete this book. An empty transactions array is still a real book.
-- This file is not applied to Supabase project ref vgrlucxqncajjdoaoctq here.

CREATE TABLE IF NOT EXISTS public.portfolio_activity_books (
  user_id TEXT PRIMARY KEY,
  analysis_id TEXT NOT NULL,
  filename TEXT NOT NULL DEFAULT '',
  transactions JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.portfolio_activity_books ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.portfolio_activity_books FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_activity_books TO service_role;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.portfolio_activity_books FROM service_role;
