-- Keep private, server-generated packet documents separate from user history.
-- Unpaid snapshots expire after 24 hours; paid snapshots after 90 days.
ALTER TABLE public.portfolio_analyses
  ADD COLUMN IF NOT EXISTS result JSONB;

CREATE TABLE IF NOT EXISTS public.year_close_packet_snapshots (
  analysis_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  tax_year INTEGER NOT NULL,
  packet_payload JSONB,
  packet_session_id TEXT,
  paid_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, analysis_id)
);

CREATE INDEX IF NOT EXISTS idx_packet_snapshots_paid_year
  ON public.year_close_packet_snapshots (user_id, tax_year, created_at DESC)
  WHERE paid_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_packet_snapshots_expiry
  ON public.year_close_packet_snapshots (expires_at);

ALTER TABLE public.year_close_packet_snapshots ENABLE ROW LEVEL SECURITY;

-- No client policies are granted. The backend accesses this table with the
-- Supabase service role, and ordinary history endpoints never select it.

CREATE OR REPLACE FUNCTION public.delete_expired_year_close_packet_snapshots()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  deleted_count INTEGER;
BEGIN
  DELETE FROM public.year_close_packet_snapshots
  WHERE expires_at <= now();
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END;
$$;

REVOKE ALL ON FUNCTION public.delete_expired_year_close_packet_snapshots() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.delete_expired_year_close_packet_snapshots() TO service_role;
