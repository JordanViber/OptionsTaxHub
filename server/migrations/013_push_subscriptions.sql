-- Owner-scoped Web Push subscriptions. Endpoint URLs and encryption keys are
-- capability secrets, so this table has no anon or authenticated policies.
-- Hosted service_role bypasses row level security and is the only writer
-- (server/push_subscriptions.py). This file is not applied to Supabase
-- project ref vgrlucxqncajjdoaoctq here.
--
-- Local migration tests bootstrap auth functions without auth.users. The
-- foreign key is added only when that table exists, which it does on Supabase.
-- Deleting the auth user then removes the device rows.

CREATE TABLE IF NOT EXISTS public.push_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  endpoint TEXT NOT NULL,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  expiration_time_ms DOUBLE PRECISION,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT push_subscriptions_endpoint_unique UNIQUE (endpoint),
  CONSTRAINT push_subscriptions_endpoint_length CHECK (char_length(endpoint) <= 2048),
  CONSTRAINT push_subscriptions_p256dh_length CHECK (char_length(p256dh) BETWEEN 1 AND 200),
  CONSTRAINT push_subscriptions_auth_length CHECK (char_length(auth) BETWEEN 1 AND 200),
  CONSTRAINT push_subscriptions_endpoint_host CHECK (
    endpoint ~ '^https://(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|web\.push\.apple\.com|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.notify\.windows\.com)(:443)?(/[^[:space:]@]*)?$'
  )
);

CREATE INDEX IF NOT EXISTS push_subscriptions_user_created_id_idx
  ON public.push_subscriptions (user_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS push_subscriptions_created_id_idx
  ON public.push_subscriptions (created_at DESC, id DESC);

ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.push_subscriptions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.push_subscriptions TO service_role;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.push_subscriptions FROM service_role;

DO $$
BEGIN
  IF to_regclass('auth.users') IS NOT NULL
     AND NOT EXISTS (
       SELECT 1
       FROM pg_constraint
       WHERE conname = 'push_subscriptions_user_id_fkey'
     ) THEN
    ALTER TABLE public.push_subscriptions
      ADD CONSTRAINT push_subscriptions_user_id_fkey
      FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE;
  END IF;
END $$;
