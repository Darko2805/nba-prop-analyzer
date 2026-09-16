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

## 2. Point the magic-link email at our own callback

Supabase's default magic-link email redirects through its own `/auth/v1/verify` endpoint and lands back on your site with tokens in a URL fragment — fragments never reach a server, so a server-rendered app like this one can't read them. Instead, point the email link straight at our callback with a `token_hash` query param, which Flask verifies itself.

In the dashboard: **Authentication → Email Templates → Magic Link**, change the link's `href` to:

```
{{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email
```

## 3. Allow-list the redirect URL

**Authentication → URL Configuration**:
- Site URL: `https://nba-prop-analyzer-sqik.onrender.com`
- Redirect URLs: add `https://nba-prop-analyzer-sqik.onrender.com/auth/callback` (and `http://localhost:5000/auth/callback` if testing locally)

## 4. Environment variables (Render → Environment tab)

From **Settings → API**:
- `SUPABASE_URL` — the Project URL
- `SUPABASE_ANON_KEY` — the `anon` `public` key
- `SUPABASE_SERVICE_ROLE_KEY` — the `service_role` key (**secret** — server-side only, never expose to the browser)

Plus:
- `FLASK_SECRET_KEY` — any long random string, used to sign the session cookie
