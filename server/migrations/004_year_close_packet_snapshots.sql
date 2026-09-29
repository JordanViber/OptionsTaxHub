-- Keep private, server-generated packet documents separate from user history.
-- Unpaid snapshots expire after 24 hours; paid snapshots remain available
-- until the user deletes the source analysis.
ALTER TABLE public.portfolio_analyses
  ADD COLUMN IF NOT EXISTS result JSONB;

CREATE TABLE IF NOT EXISTS public.year_close_packet_snapshots (
  analysis_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  tax_year INTEGER NOT NULL,
  packet_payload JSONB,
  packet_session_id TEXT,
  paid_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
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
ALTER TABLE public.year_close_packet_snapshots ALTER COLUMN expires_at DROP NOT NULL;
UPDATE public.year_close_packet_snapshots SET expires_at = NULL WHERE paid_at IS NOT NULL;

REVOKE ALL ON TABLE public.year_close_packet_snapshots FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.year_close_packet_snapshots TO service_role;

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
  WHERE paid_at IS NULL AND expires_at <= now();
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END;
$$;

REVOKE ALL ON FUNCTION public.delete_expired_year_close_packet_snapshots() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.delete_expired_year_close_packet_snapshots() FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.delete_expired_year_close_packet_snapshots() TO service_role;

CREATE OR REPLACE FUNCTION public.clear_packet_payload_after_analysis_delete()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  deleted_analysis_id TEXT;
BEGIN
  deleted_analysis_id := OLD.result ->> 'analysis_id';
  IF deleted_analysis_id IS NULL OR deleted_analysis_id = '' THEN
    deleted_analysis_id := OLD.id::text;
  END IF;

  DELETE FROM public.year_close_packet_snapshots
  WHERE user_id = OLD.user_id
    AND analysis_id IN (OLD.id::text, deleted_analysis_id)
    AND paid_at IS NULL;

  UPDATE public.year_close_packet_snapshots
  SET packet_payload = NULL, updated_at = now()
  WHERE user_id = OLD.user_id
    AND analysis_id IN (OLD.id::text, deleted_analysis_id)
    AND paid_at IS NOT NULL;

  RETURN OLD;
END;
$$;

REVOKE ALL ON FUNCTION public.clear_packet_payload_after_analysis_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.clear_packet_payload_after_analysis_delete() FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.clear_packet_payload_after_analysis_delete() TO service_role;

DROP TRIGGER IF EXISTS clear_packet_payload_after_analysis_delete ON public.portfolio_analyses;
CREATE TRIGGER clear_packet_payload_after_analysis_delete
  AFTER DELETE ON public.portfolio_analyses
  FOR EACH ROW EXECUTE FUNCTION public.clear_packet_payload_after_analysis_delete();
