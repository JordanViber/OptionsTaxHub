-- Owner SELECT for authenticated clients, service-role writes for the server.
-- Next step after 008_portfolio_analyses_one_analysis_id.sql.
--
-- user_id is text. Owner checks are (auth.uid())::text = user_id.
--
-- Safe on a database that applied 001-008 as checked in, and on the live
-- policy set (duplicate public owner policies, client writes, and
-- "Service role full access" FOR ALL TO public). Also drops the older 001
-- shape: INSERT/UPDATE/DELETE with no TO role (PUBLIC) and USING/CHECK true.
-- Dropping a policy that 001, 002, 005, or 007 already dropped is intentional.
--
-- End state:
--   portfolio_analyses: one authenticated SELECT; service_role SELECT/INSERT/UPDATE/DELETE.
--   tax_profiles: one authenticated SELECT; service_role SELECT/INSERT/UPDATE.
--   service_role SELECT is required so UPDATE, DELETE, and upsert can read the
--   existing row when that role does not bypass row security.
--   Packet tables: no new anon/authenticated grants or policies.
-- The React client does not query these tables. server/db.py writes them with
-- the service-role key. History result JSON stays not writable from a browser.

-- portfolio_analyses: live client writes and duplicate selects.
DROP POLICY IF EXISTS "Users can create analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can update their own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can update own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can delete their own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can delete own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can view own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can view their own portfolio analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can view their own analyses" ON public.portfolio_analyses;

-- Same names as the service-role policies, including copies created with no
-- TO role (PUBLIC) and WITH CHECK (true) / USING (true).
DROP POLICY IF EXISTS "Service role can insert analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can update analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can delete analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Service role can select analyses" ON public.portfolio_analyses;

-- tax_profiles: public ALL role-name policy, duplicate owner writes, duplicate selects.
DROP POLICY IF EXISTS "Service role full access" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can update own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can update their own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can insert own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can insert their own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can view own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can view their own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can select own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Users can read own tax profile" ON public.tax_profiles;
DROP POLICY IF EXISTS "Service role can upsert tax profiles" ON public.tax_profiles;
DROP POLICY IF EXISTS "Service role can update tax profiles" ON public.tax_profiles;
DROP POLICY IF EXISTS "Service role can select tax profiles" ON public.tax_profiles;

-- Any remaining policy on these two tables is a duplicate of the sets above.
DO $$
DECLARE
  pol record;
BEGIN
  FOR pol IN
    SELECT n.nspname AS schemaname, c.relname AS tablename, p.polname AS policyname
    FROM pg_policy p
    JOIN pg_class c ON c.oid = p.polrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname IN ('portfolio_analyses', 'tax_profiles')
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON %I.%I',
      pol.policyname,
      pol.schemaname,
      pol.tablename
    );
  END LOOP;
END $$;

CREATE POLICY "Users can view own analyses"
  ON public.portfolio_analyses
  FOR SELECT
  TO authenticated
  USING ((auth.uid())::text = user_id);

-- service_role has no BYPASSRLS in a stock Postgres role. UPDATE, DELETE, and
-- upsert must be able to read the existing row, so SELECT is explicit here.
CREATE POLICY "Service role can select analyses"
  ON public.portfolio_analyses
  FOR SELECT
  TO service_role
  USING (true);

CREATE POLICY "Service role can insert analyses"
  ON public.portfolio_analyses
  FOR INSERT
  TO service_role
  WITH CHECK (true);

CREATE POLICY "Service role can update analyses"
  ON public.portfolio_analyses
  FOR UPDATE
  TO service_role
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Service role can delete analyses"
  ON public.portfolio_analyses
  FOR DELETE
  TO service_role
  USING (true);

CREATE POLICY "Users can view own tax profile"
  ON public.tax_profiles
  FOR SELECT
  TO authenticated
  USING ((auth.uid())::text = user_id);

CREATE POLICY "Service role can select tax profiles"
  ON public.tax_profiles
  FOR SELECT
  TO service_role
  USING (true);

CREATE POLICY "Service role can upsert tax profiles"
  ON public.tax_profiles
  FOR INSERT
  TO service_role
  WITH CHECK (true);

CREATE POLICY "Service role can update tax profiles"
  ON public.tax_profiles
  FOR UPDATE
  TO service_role
  USING (true)
  WITH CHECK (true);

-- Authenticated keeps owner SELECT. anon keeps neither read nor write.
-- Do not revoke service_role. Packet tables are not granted here.
REVOKE ALL ON TABLE public.portfolio_analyses FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.portfolio_analyses TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_analyses TO service_role;

REVOKE ALL ON TABLE public.tax_profiles FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.tax_profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.tax_profiles TO service_role;

REVOKE ALL ON TABLE public.year_close_packet_snapshots FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.year_close_packet_entitlements FROM PUBLIC, anon, authenticated;
