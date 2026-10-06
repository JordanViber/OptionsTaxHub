-- Unify portfolio_analyses.id with the server-owned result.analysis_id.
-- Also canonicalizes UUID analysis_id values on year_close_packet_snapshots,
-- year_close_packet_entitlements, and portfolio_activity_books in this
-- transaction. Snapshot case-variants collapse. Any two tax years for one
-- snapshot UUID (paid or not), two paid sessions in one year, a collapse that
-- would discard the only non-null payload, entitlement UUID case-variants
-- (more than one stored spelling) in different tax years, and two history
-- rows for one UUID RAISE. One entitlement spelling in two tax years does
-- not RAISE. conflict: entitlement ids fail the UUID regex and are left as
-- they are.
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

-- Block concurrent writes before any detection or rewrite. The analyses lock
-- stays ahead of the embedded-id temp snapshot inside the primary-key loop.
LOCK TABLE public.portfolio_analyses IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.year_close_packet_snapshots IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.year_close_packet_entitlements IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.portfolio_activity_books IN SHARE ROW EXCLUSIVE MODE;

-- Detection only. A RAISE aborts the transaction before any canonical write.
DO $$
DECLARE
  collision record;
BEGIN
  FOR collision IN
    SELECT user_id,
           (result->>'analysis_id')::uuid AS canonical_uuid,
           (array_agg(id::text ORDER BY id::text))[1] AS row_a,
           (array_agg(id::text ORDER BY id::text))[2] AS row_b
    FROM public.portfolio_analyses
    WHERE COALESCE(result->>'analysis_id', '') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    GROUP BY user_id, (result->>'analysis_id')::uuid
    HAVING count(*) > 1
  LOOP
    RAISE EXCEPTION
      'two portfolio_analyses rows share one analysis UUID user=% canonical=% rows=% %',
      collision.user_id, collision.canonical_uuid, collision.row_a, collision.row_b;
  END LOOP;

  FOR collision IN
    SELECT user_id,
           (analysis_id)::uuid AS canonical_uuid,
           array_agg(DISTINCT tax_year ORDER BY tax_year) AS years
    FROM public.year_close_packet_snapshots
    WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    GROUP BY user_id, (analysis_id)::uuid
    HAVING count(DISTINCT tax_year) > 1
  LOOP
    RAISE EXCEPTION
      'packet snapshots for one analysis UUID have more than one tax year user=% canonical=% years=%',
      collision.user_id, collision.canonical_uuid, collision.years;
  END LOOP;

  FOR collision IN
    SELECT user_id,
           (analysis_id)::uuid AS canonical_uuid,
           tax_year,
           array_agg(DISTINCT COALESCE(packet_session_id, '') ORDER BY COALESCE(packet_session_id, '')) AS sessions
    FROM public.year_close_packet_snapshots
    WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND paid_at IS NOT NULL
    GROUP BY user_id, (analysis_id)::uuid, tax_year
    HAVING count(DISTINCT COALESCE(packet_session_id, '')) > 1
  LOOP
    RAISE EXCEPTION
      'two paid packet snapshots for one analysis UUID in the same tax year with different checkout sessions user=% canonical=% year=% sessions=%',
      collision.user_id, collision.canonical_uuid, collision.tax_year, collision.sessions;
  END LOOP;

  -- Paid-null beats an unpaid PDF under the keeper ORDER BY. The 006 delete
  -- trigger is case-sensitive, so that pair is reachable. Abort instead of
  -- discarding the only payload. A lone paid-null snapshot is not this case
  -- and is still backfilled later as a receipt.
  FOR collision IN
    SELECT user_id,
           (analysis_id)::uuid AS canonical_uuid
    FROM public.year_close_packet_snapshots
    WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    GROUP BY user_id, (analysis_id)::uuid
    HAVING count(*) > 1
       AND count(*) FILTER (WHERE paid_at IS NOT NULL AND packet_payload IS NOT NULL) = 0
       AND count(*) FILTER (WHERE paid_at IS NOT NULL AND packet_payload IS NULL) > 0
       AND count(*) FILTER (WHERE packet_payload IS NOT NULL) > 0
  LOOP
    RAISE EXCEPTION
      'packet snapshot collapse would discard the only non-null payload user=% canonical=%',
      collision.user_id, collision.canonical_uuid;
  END LOOP;
END $$;

-- Same-year same-session duplicates keep one row. Different tax years have
-- already RAISE'd. Keeper: a paid row, then a row that still has a payload,
-- then latest updated_at, then latest created_at, then the lexicographically
-- greatest original analysis_id.
DELETE FROM public.year_close_packet_snapshots AS loser
WHERE loser.ctid IN (
  SELECT ranked.ctid
  FROM (
    SELECT
      snap.ctid,
      row_number() OVER (
        PARTITION BY snap.user_id, (snap.analysis_id)::uuid
        ORDER BY
          (snap.paid_at IS NOT NULL) DESC,
          (snap.packet_payload IS NOT NULL) DESC,
          snap.updated_at DESC NULLS LAST,
          snap.created_at DESC NULLS LAST,
          snap.analysis_id DESC
      ) AS keeper_rank,
      count(*) OVER (
        PARTITION BY snap.user_id, (snap.analysis_id)::uuid
      ) AS group_size
    FROM public.year_close_packet_snapshots AS snap
    WHERE snap.analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  ) AS ranked
  WHERE ranked.group_size > 1
    AND ranked.keeper_rank > 1
);

UPDATE public.year_close_packet_snapshots
SET analysis_id = (analysis_id)::uuid::text
WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND analysis_id IS DISTINCT FROM (analysis_id)::uuid::text;

-- conflict: receipts fail the UUID predicate below, so this RAISE and the
-- following UPDATE leave them intact. One stored spelling in two tax years
-- is a legitimate pair of receipts and does not RAISE. Case variants do.
DO $$
DECLARE
  collision record;
BEGIN
  FOR collision IN
    SELECT user_id,
           (analysis_id)::uuid AS canonical_uuid,
           array_agg(DISTINCT tax_year ORDER BY tax_year) AS years
    FROM public.year_close_packet_entitlements
    WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    GROUP BY user_id, (analysis_id)::uuid
    HAVING count(DISTINCT tax_year) > 1
       AND count(DISTINCT analysis_id) > 1
  LOOP
    RAISE EXCEPTION
      'packet entitlements for one analysis UUID have more than one tax year user=% canonical=% years=%',
      collision.user_id, collision.canonical_uuid, collision.years;
  END LOOP;
END $$;

UPDATE public.year_close_packet_entitlements
SET analysis_id = (analysis_id)::uuid::text
WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND analysis_id IS DISTINCT FROM (analysis_id)::uuid::text;

UPDATE public.portfolio_activity_books
SET analysis_id = (analysis_id)::uuid::text
WHERE analysis_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND analysis_id IS DISTINCT FROM (analysis_id)::uuid::text;

UPDATE public.portfolio_analyses
SET result = jsonb_set(
  result,
  '{analysis_id}',
  to_jsonb((result->>'analysis_id')::uuid::text)
)
WHERE COALESCE(result->>'analysis_id', '') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  AND (result->>'analysis_id') IS DISTINCT FROM (result->>'analysis_id')::uuid::text;

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
-- Application writes are canonical. The case-insensitive UUID arm covers a
-- mixed-case snapshot from the old server during deploy. Other ids stay exact
-- so distinct non-UUID strings are not collapsed. Alias rows are removed after
-- they have been copied into cleanup_ids, so a later insert that reuses the
-- canonical UUID cannot resolve the old legacy id. Entitlement analysis_id
-- values are rewritten to canonical text above and are not deleted here.
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
-- A paid-null row is still a receipt when it is the only snapshot. The RAISE
-- above aborts before this INSERT when keeping that empty row would delete
-- the only non-null payload, so the backfill does not adopt the empty row
-- in place of the PDF.
INSERT INTO public.year_close_packet_entitlements (
  user_id, tax_year, packet_session_id, analysis_id, created_at
)
SELECT user_id, tax_year, packet_session_id, analysis_id, COALESCE(paid_at, now())
FROM public.year_close_packet_snapshots
WHERE paid_at IS NOT NULL
  AND left(packet_session_id, 3) = 'cs_'
ON CONFLICT (user_id, tax_year, packet_session_id) DO NOTHING;

COMMIT;
