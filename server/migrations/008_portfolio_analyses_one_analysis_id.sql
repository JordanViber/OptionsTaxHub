-- One history row per user and embedded analysis id.
-- Concurrent guest persists must conflict and reuse the existing row
-- instead of inserting a second copy of the same run.
CREATE UNIQUE INDEX IF NOT EXISTS portfolio_analyses_user_embedded_analysis_id_uidx
  ON public.portfolio_analyses (user_id, ((result ->> 'analysis_id')))
  WHERE COALESCE(result ->> 'analysis_id', '') <> '';
