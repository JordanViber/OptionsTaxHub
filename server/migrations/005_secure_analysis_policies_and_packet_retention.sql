-- Restrict history writes/deletes to the service role, and retain paid packet
-- snapshots until the user's source analysis is deleted.
DROP POLICY IF EXISTS "Service role can insert analyses" ON public.portfolio_analyses;
CREATE POLICY "Service role can insert analyses"
  ON public.portfolio_analyses
  FOR INSERT
  TO service_role
  WITH CHECK (true);

DROP POLICY IF EXISTS "Service role can delete analyses" ON public.portfolio_analyses;
CREATE POLICY "Service role can delete analyses"
  ON public.portfolio_analyses
  FOR DELETE
  TO service_role
  USING (true);

ALTER TABLE public.year_close_packet_snapshots ALTER COLUMN expires_at DROP NOT NULL;
UPDATE public.year_close_packet_snapshots SET expires_at = NULL WHERE paid_at IS NOT NULL;

REVOKE ALL ON TABLE public.year_close_packet_snapshots FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.year_close_packet_snapshots TO service_role;

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
REVOKE ALL ON FUNCTION public.delete_expired_year_close_packet_snapshots() FROM PUBLIC, anon, authenticated;
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
REVOKE ALL ON FUNCTION public.clear_packet_payload_after_analysis_delete() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.clear_packet_payload_after_analysis_delete() TO service_role;

DROP TRIGGER IF EXISTS clear_packet_payload_after_analysis_delete ON public.portfolio_analyses;
CREATE TRIGGER clear_packet_payload_after_analysis_delete
  AFTER DELETE ON public.portfolio_analyses
  FOR EACH ROW EXECUTE FUNCTION public.clear_packet_payload_after_analysis_delete();
