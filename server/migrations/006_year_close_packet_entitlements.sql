-- Keep a verified annual purchase independently from the private PDF source.
-- Deleting an analysis clears its packet payload while preserving this receipt.
CREATE TABLE IF NOT EXISTS public.year_close_packet_entitlements (
  user_id TEXT NOT NULL,
  tax_year INTEGER NOT NULL,
  packet_session_id TEXT NOT NULL,
  analysis_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, tax_year, packet_session_id)
);

CREATE INDEX IF NOT EXISTS idx_packet_entitlements_year
  ON public.year_close_packet_entitlements (user_id, tax_year, created_at DESC);

ALTER TABLE public.year_close_packet_entitlements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.year_close_packet_entitlements FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.year_close_packet_entitlements TO service_role;

-- Preserve purchases already recorded by the snapshot migration.
INSERT INTO public.year_close_packet_entitlements (
  user_id, tax_year, packet_session_id, analysis_id, created_at
)
SELECT user_id, tax_year, packet_session_id, analysis_id, COALESCE(paid_at, now())
FROM public.year_close_packet_snapshots
WHERE paid_at IS NOT NULL
  AND left(packet_session_id, 3) = 'cs_'
ON CONFLICT (user_id, tax_year, packet_session_id) DO NOTHING;

-- History row ids and analysis ids are different values in older installs.
-- Clear private payloads using both keys so deletes do not retain orphan data.
CREATE OR REPLACE FUNCTION public.clear_packet_payload_after_analysis_delete()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  deleted_analysis_id TEXT;
BEGIN
  deleted_analysis_id := NULLIF(OLD.result ->> 'analysis_id', '');
  IF deleted_analysis_id IS NULL THEN
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
REVOKE ALL ON FUNCTION public.clear_packet_payload_after_analysis_delete() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.clear_packet_payload_after_analysis_delete() TO service_role;

DROP TRIGGER IF EXISTS clear_packet_payload_after_analysis_delete ON public.portfolio_analyses;
CREATE TRIGGER clear_packet_payload_after_analysis_delete
  AFTER DELETE ON public.portfolio_analyses
  FOR EACH ROW EXECUTE FUNCTION public.clear_packet_payload_after_analysis_delete();
