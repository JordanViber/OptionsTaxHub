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
-- values still locate the same row. A source id that another of this user's
-- rows still embeds is also left in place: moving it would vacate the primary
-- key and the embedded row would shadow the alias.
DO $$
DECLARE
  rec record;
  embedded uuid;
  embedded_text text;
  owner_count integer;
  updated_count integer;
  pk_taken boolean;
  source_embedded boolean;
BEGIN
  -- Block concurrent inserts and updates for the rest of this transaction.
  -- The snapshot below is only a READ COMMITTED view: a row committed while
  -- the loop runs could otherwise lose its primary key to a rewrite.
  LOCK TABLE public.portfolio_analyses IN SHARE ROW EXCLUSIVE MODE;

  -- Snapshot embedded ids before any primary-key update. A later EXISTS would
  -- see ids this loop has already vacated.
  CREATE TEMP TABLE portfolio_analysis_embedded_snapshot ON COMMIT DROP AS
  SELECT user_id, id AS row_id, lower(result->>'analysis_id') AS embedded_id
  FROM public.portfolio_analyses
  WHERE COALESCE(result->>'analysis_id', '') <> '';

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

    SELECT EXISTS (
      SELECT 1
      FROM portfolio_analysis_embedded_snapshot other
      WHERE other.user_id = rec.user_id
        AND other.row_id <> rec.id
        AND other.embedded_id = lower(rec.id::text)
    ) INTO source_embedded;

    IF owner_count <> 1 OR pk_taken OR source_embedded THEN
      RAISE NOTICE 'skip collision analysis_id user=% row=% canonical=% owners=% pk_taken=% source_embedded=%',
        rec.user_id, rec.id, embedded, owner_count, pk_taken, source_embedded;
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

-- 006 clears snapshots keyed by the deleted row id and its embedded analysis
-- id. After a rewrite those snapshots may still be keyed by the legacy row id.
-- UUID-shaped snapshot keys match in any letter case. Other ids stay exact so
-- distinct non-UUID strings are not collapsed. Alias rows are removed after
-- they have been copied into cleanup_ids, so a later insert that reuses the
-- canonical UUID cannot resolve the old legacy id. Entitlement analysis_id
-- values are left unchanged.
CREATE OR REPLACE FUNCTION public.clear_packet_payload_after_analysis_delete()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  deleted_analysis_id TEXT;
  cleanup_ids TEXT[];
  uuid_cleanup_ids TEXT[];
BEGIN
  deleted_analysis_id := NULLIF(OLD.result ->> 'analysis_id', '');
  IF deleted_analysis_id IS NULL THEN
    deleted_analysis_id := OLD.id::text;
  END IF;

  SELECT ARRAY(
    SELECT DISTINCT candidate
    FROM (
      SELECT OLD.id::text AS candidate
      UNION ALL
      SELECT deleted_analysis_id
      UNION ALL
      SELECT legacy_row_id::text
      FROM public.portfolio_analysis_id_aliases
      WHERE user_id = OLD.user_id
        AND canonical_analysis_id = OLD.id
    ) keys
    WHERE candidate IS NOT NULL AND candidate <> ''
  ) INTO cleanup_ids;

  SELECT ARRAY(
    SELECT DISTINCT lower(candidate)
    FROM unnest(cleanup_ids) AS candidate
    WHERE candidate ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  ) INTO uuid_cleanup_ids;

  DELETE FROM public.year_close_packet_snapshots
  WHERE user_id = OLD.user_id
    AND paid_at IS NULL
    AND (
      analysis_id = ANY(cleanup_ids)
      OR (
        analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        AND lower(analysis_id) = ANY(uuid_cleanup_ids)
      )
    );

  UPDATE public.year_close_packet_snapshots
  SET packet_payload = NULL, updated_at = now()
  WHERE user_id = OLD.user_id
    AND paid_at IS NOT NULL
    AND (
      analysis_id = ANY(cleanup_ids)
      OR (
        analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        AND lower(analysis_id) = ANY(uuid_cleanup_ids)
      )
    );

  DELETE FROM public.portfolio_analysis_id_aliases
  WHERE user_id = OLD.user_id
    AND canonical_analysis_id = OLD.id;

  RETURN OLD;
END;
$$;

REVOKE ALL ON FUNCTION public.clear_packet_payload_after_analysis_delete() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.clear_packet_payload_after_analysis_delete() TO service_role;

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
