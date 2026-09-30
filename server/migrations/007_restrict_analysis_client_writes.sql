-- Remove legacy client write policies that let users forge packet grant flags
-- in their own portfolio_analyses.result JSON. History writes are service-role
-- only; user-scoped reads remain available through the existing SELECT policy.
DROP POLICY IF EXISTS "Users can create analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can update their own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can delete their own analyses" ON public.portfolio_analyses;

DROP POLICY IF EXISTS "Service role can update analyses" ON public.portfolio_analyses;
CREATE POLICY "Service role can update analyses"
  ON public.portfolio_analyses
  FOR UPDATE
  TO service_role
  USING (true)
  WITH CHECK (true);
