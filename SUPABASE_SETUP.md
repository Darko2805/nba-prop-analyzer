# Supabase setup for NBA Prop Analyzer auth

One-time setup in the Supabase project (Settings → API for the URL/keys, SQL Editor for the table, Authentication for the email template).

## 1. Create the `profiles` table

Run this in the Supabase SQL Editor:

```sql
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  name text,
  tier text not null default 'free',
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- The Flask backend writes through the service_role key, which bypasses RLS
-- entirely — this policy only matters if something ever reads the table
-- directly as an end user (e.g. a future client-side dashboard).
create policy "Users can view own profile"
  on public.profiles for select
  using (auth.uid() = id);
```

## 2. Add usage-tracking columns/table (tier gating)

Run this in the Supabase SQL Editor to support the three access tiers
(anonymous: 1/day by IP, signed-up "free": 5 every 4 days, "paid": unlimited):

```sql
alter table public.profiles add column if not exists usage_count integer not null default 0;
alter table public.profiles add column if not exists usage_batch_started_at timestamptz;

create table if not exists public.anon_usage (
  ip text primary key,
  used_date date not null,
  updated_at timestamptz not null default now()
);

alter table public.anon_usage enable row level security;
-- No public policies -- only the service_role key (server-side, in usage.py)
-- ever reads or writes this table.
```

To manually upgrade an account to paid (no billing integration exists yet):

```sql
update public.profiles set tier = 'paid' where email = 'someone@example.com';
```

## 3. Point the magic-link email at our own callback

Supabase's default magic-link email redirects through its own `/auth/v1/verify` endpoint and lands back on your site with tokens in a URL fragment — fragments never reach a server, so a server-rendered app like this one can't read them. Instead, point the email link straight at our callback with a `token_hash` query param, which Flask verifies itself.

In the dashboard: **Authentication → Email Templates → Magic Link**, change the link's `href` to:

```
{{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email
```

## 3b. Add the track-record table (paid-tier historical predictions)

```sql
create table if not exists public.tracked_predictions (
  date date not null,
  player text not null,
  opponent text,
  prop_type text not null,
  line numeric not null,
  predicted_value numeric not null,
  lean text not null,
  actual_value numeric,
  hit text,
  created_at timestamptz not null default now(),
  primary key (date, player, prop_type)
);

alter table public.tracked_predictions enable row level security;
grant select, insert, update on public.tracked_predictions to service_role;
```

(That `grant` line is required -- a table created via this SQL Editor does
NOT automatically get service_role privileges the way the dashboard's
Table Editor does. See the `anon_usage` incident in memory if this ever
silently stops writing again.)

Also set a `TRACK_RECORD_KEY` environment variable on Render (any random
string) -- it's the shared secret required to trigger a snapshot via
`/internal/snapshot-track-record?key=...` until this is wired into the
existing daily scheduled task.

## 4. Allow-list the redirect URL

**Authentication → URL Configuration**:
- Site URL: `https://nba-prop-analyzer-sqik.onrender.com`
- Redirect URLs: add `https://nba-prop-analyzer-sqik.onrender.com/auth/callback` (and `http://localhost:5000/auth/callback` if testing locally)

## 5. Environment variables (Render → Environment tab)

From **Settings → API**:
- `SUPABASE_URL` — the Project URL
- `SUPABASE_ANON_KEY` — the `anon` `public` key
- `SUPABASE_SERVICE_ROLE_KEY` — the `service_role` key (**secret** — server-side only, never expose to the browser)

Plus:
- `FLASK_SECRET_KEY` — any long random string, used to sign the session cookie
