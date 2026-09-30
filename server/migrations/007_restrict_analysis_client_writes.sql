-- Remove legacy client write policies that let users forge packet grant flags
-- in their own portfolio_analyses.result JSON. History writes are service-role
-- only; user-scoped reads remain available through the existing SELECT policy.
DROP POLICY IF EXISTS "Users can create analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can update their own analyses" ON public.portfolio_analyses;
DROP POLICY IF EXISTS "Users can delete their own analyses" ON public.portfolio_analyses;
