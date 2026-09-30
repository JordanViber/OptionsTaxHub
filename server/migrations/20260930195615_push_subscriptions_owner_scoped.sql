-- Push endpoints contain device-specific capability URLs and encryption keys.
-- Keep them behind the server's service-role client, which always filters by owner.
create table if not exists public.push_subscriptions (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,
    endpoint text not null unique check (endpoint like 'https://%'),
    keys jsonb not null,
    expiration_time_ms double precision,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists push_subscriptions_user_created_idx
    on public.push_subscriptions (user_id, created_at desc);

alter table public.push_subscriptions enable row level security;

revoke all on table public.push_subscriptions from anon, authenticated;
grant select, insert, update, delete on table public.push_subscriptions to service_role;
