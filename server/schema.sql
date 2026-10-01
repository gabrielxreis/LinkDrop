-- LinkDrop licensing schema (Supabase / Postgres)
-- A license is one key for ONE computer. Paid: valid 1 year. Trial: 24 h from first activation,
-- and each computer can only ever run one trial (tracked by its hashed hardware ID).

create table if not exists public.licenses (
  id uuid primary key default gen_random_uuid(),
  key text unique not null,                       -- e.g. LD-7K2F-9QXA-M4TD (paid) or LDT-... (trial)
  user_id uuid references auth.users (id) on delete set null,
  email text,
  kind text not null check (kind in ('trial', 'paid')),
  status text not null default 'active' check (status in ('active', 'disabled', 'refunded')),
  created_at timestamptz not null default now(),
  expires_at timestamptz,                         -- paid: created_at + 1 year; trial: set on first activation
  stripe_session_id text unique,
  revoke_count int not null default 0,            -- computers removed since the last cooldown
  revoke_locked_until timestamptz                 -- after 5 removals, removing is blocked for 5 days
);

create table if not exists public.activations (
  id uuid primary key default gen_random_uuid(),
  license_id uuid not null references public.licenses (id) on delete cascade,
  machine_id text not null,                       -- sha256 of the hardware ID, never the raw ID
  machine_name text,
  os text,
  app_version text,
  activated_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz
);
create index if not exists activations_license_idx on public.activations (license_id);
create unique index if not exists activations_one_active_per_license
  on public.activations (license_id) where revoked_at is null;

-- every computer that ever started a trial (blocks a second free trial from a new account)
create table if not exists public.trial_machines (
  machine_id text primary key,
  license_id uuid references public.licenses (id) on delete set null,
  first_seen_at timestamptz not null default now()
);

alter table public.licenses enable row level security;
alter table public.activations enable row level security;
alter table public.trial_machines enable row level security;

-- people can read their own licenses and activations; all writes go through edge functions (service role)
drop policy if exists "own licenses" on public.licenses;
create policy "own licenses" on public.licenses for select using (auth.uid() = user_id);
drop policy if exists "own activations" on public.activations;
create policy "own activations" on public.activations for select
  using (exists (select 1 from public.licenses l where l.id = license_id and l.user_id = auth.uid()));
