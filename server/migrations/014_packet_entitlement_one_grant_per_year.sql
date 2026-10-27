-- One non-conflict receipt per user and tax year.
--
-- Two Checkout sessions for different runs can both pass the grant's
-- read-before-write check and both insert. The primary key is
-- (user_id, tax_year, packet_session_id), so those inserts do not collide.
-- This partial unique index is the claim: the second non-conflict insert
-- raises unique_violation and the grant records a refund instead of a
-- second purchase.
--
-- A refund row keeps its cs_ session id. The conflict marker is the
-- analysis_id prefix conflict:, which is why the predicate is on
-- analysis_id and not packet_session_id. A conflict: row can sit beside
-- the one real receipt. This file does not update or delete existing rows
-- and is not applied to a hosted database here.

CREATE UNIQUE INDEX IF NOT EXISTS idx_packet_entitlements_one_nonconflict_per_year
  ON public.year_close_packet_entitlements (user_id, tax_year)
  WHERE left(analysis_id, 9) <> 'conflict:';
