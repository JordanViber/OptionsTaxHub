-- Unify portfolio_analyses.id with the server-owned result.analysis_id.
-- Additive. Does not change year_close_packet_entitlements.analysis_id,
-- year_close_packet_snapshots.analysis_id, or portfolio_activity_books.analysis_id.
-- Stripe still checks session metadata against the stored entitlement analysis id.
-- This file has its own transaction. apply_migrations.sh does not wrap it.
-- Not applied to Supabase project ref vgrlucxqncajjdoaoctq here.

BEGIN;

CREATE TABLE IF NOT EXISTS public.portfolio_analysis_id_aliases (
  user_id TEXT NOT NULL,
  legacy_row_id UUID NOT NULL,
  canonical_analysis_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, legacy_row_id)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_analysis_id_aliases_canonical
  ON public.portfolio_analysis_id_aliases (user_id, canonical_analysis_id);

ALTER TABLE public.portfolio_analysis_id_aliases ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.portfolio_analysis_id_aliases FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_analysis_id_aliases TO service_role;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.portfolio_analysis_id_aliases FROM service_role;

-- Record a legacy primary key, then move the row onto its embedded UUID when
-- that UUID is free and this user has exactly one row for it. Non-UUID
-- embedded ids and primary-key collisions stay on their current id so both
-- values still locate the same row.
DO $$
DECLARE
  rec record;
  embedded uuid;
  embedded_text text;
  owner_count integer;
  updated_count integer;
  pk_taken boolean;
BEGIN
  FOR rec IN
    SELECT id, user_id, result->>'analysis_id' AS embedded_id
    FROM public.portfolio_analyses
    WHERE COALESCE(result->>'analysis_id', '') <> ''
      AND id::text IS DISTINCT FROM lower(result->>'analysis_id')
  LOOP
    embedded_text := rec.embedded_id;
    BEGIN
      embedded := embedded_text::uuid;
    EXCEPTION
      WHEN invalid_text_representation THEN
        RAISE NOTICE 'skip non-uuid analysis_id user=% row=% value=%',
          rec.user_id, rec.id, embedded_text;
        CONTINUE;
    END;

    IF embedded = rec.id THEN
      CONTINUE;
    END IF;

    SELECT count(*) INTO owner_count
    FROM public.portfolio_analyses
    WHERE user_id = rec.user_id
      AND lower(result->>'analysis_id') = lower(embedded::text);

    SELECT EXISTS (
      SELECT 1
      FROM public.portfolio_analyses other
      WHERE other.id = embedded
        AND other.id <> rec.id
    ) INTO pk_taken;

    IF owner_count <> 1 OR pk_taken THEN
      RAISE NOTICE 'skip collision analysis_id user=% row=% canonical=% owners=% pk_taken=%',
        rec.user_id, rec.id, embedded, owner_count, pk_taken;
      CONTINUE;
    END IF;

    UPDATE public.portfolio_analyses
    SET id = embedded
    WHERE id = rec.id
      AND user_id = rec.user_id
      AND NOT EXISTS (
        SELECT 1
        FROM public.portfolio_analyses other
        WHERE other.id = embedded
          AND other.id <> rec.id
      );

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count = 0 THEN
      RAISE NOTICE 'skip collision analysis_id user=% row=% canonical=% owners=% pk_taken=%',
        rec.user_id, rec.id, embedded, owner_count, true;
      CONTINUE;
    END IF;

    INSERT INTO public.portfolio_analysis_id_aliases (
      user_id, legacy_row_id, canonical_analysis_id
    )
    VALUES (rec.user_id, rec.id, embedded)
    ON CONFLICT (user_id, legacy_row_id) DO NOTHING;
  END LOOP;
END $$;

-- Receipts for paid snapshots that never got an entitlement row. Do not
-- replace an entitlement that already recorded the checkout analysis id.
INSERT INTO public.year_close_packet_entitlements (
  user_id, tax_year, packet_session_id, analysis_id, created_at
)
SELECT user_id, tax_year, packet_session_id, analysis_id, COALESCE(paid_at, now())
FROM public.year_close_packet_snapshots
WHERE paid_at IS NOT NULL
  AND left(packet_session_id, 3) = 'cs_'
ON CONFLICT (user_id, tax_year, packet_session_id) DO NOTHING;

COMMIT;
