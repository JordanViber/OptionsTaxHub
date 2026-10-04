-- Harden the tables 009 touches before that migration is applied to the
-- shared Supabase project. 009 does not enable row level security, and its
-- REVOKEs do not include service_role. 004, 005, and 006 GRANT ALL on the
-- packet tables, which leaves service_role with TRUNCATE, REFERENCES, and
-- TRIGGER. This file turns RLS on and takes those unused privileges away.
--
-- ALTER TABLE ... ENABLE ROW LEVEL SECURITY is idempotent. When RLS is
-- already on, running it again changes nothing. Do not force row security.
-- 009 already created the portfolio_analyses and tax_profiles policies.
-- Do not recreate them here.
--
-- Packet tables stay without a service_role policy because hosted Supabase
-- service_role has BYPASSRLS. Packet tables rely on hosted service_role BYPASSRLS.
-- No policy change for that. Local tests create service_role without BYPASSRLS
-- and do not need a packet policy for the access they check.
--
-- service_role keeps the table DML server/db.py uses:
--   portfolio_analyses: SELECT, INSERT, UPDATE, DELETE
--   tax_profiles: SELECT, INSERT, UPDATE (upsert)
--   both packet tables: SELECT, INSERT, UPDATE, DELETE
-- The app does not use TRUNCATE, REFERENCES, or TRIGGER. REVOKE of a
-- privilege the role does not have is a warning, not an error, so a database
-- whose tax_profiles grants came only from 009 still applies cleanly.

ALTER TABLE public.portfolio_analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tax_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.year_close_packet_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.year_close_packet_entitlements ENABLE ROW LEVEL SECURITY;

REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.portfolio_analyses FROM service_role;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.tax_profiles FROM service_role;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.year_close_packet_snapshots FROM service_role;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.year_close_packet_entitlements FROM service_role;
