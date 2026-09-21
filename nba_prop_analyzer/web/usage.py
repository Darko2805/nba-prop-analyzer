"""
Usage limits for the three access tiers: anonymous, signed-up "free", and
paid. Paid is unlimited (no billing integration exists yet -- this just
checks the existing profiles.tier column, so flipping a user to "paid"
manually in Supabase already unlocks unlimited use ahead of any future
Stripe work).

Anonymous usage is tracked per IP in `anon_usage` since there's no
account to key on; signed-up usage is tracked directly on `profiles`
(usage_count/usage_batch_started_at) since it's a 1:1 relationship with
a real user row. Both live in Supabase, not in-memory or on disk -- see
auth.py's own note on why (Render's free-tier disk/process doesn't
survive a restart).

This is a soft nudge toward signing up/subscribing, not an airtight
abuse wall: IP-based limiting is inherently leaky (shared/dynamic IPs),
and the check-then-record pattern below has a small race window between
two near-simultaneous requests from the same visitor. Neither is worth
solving with real infrastructure (a Postgres RPC for atomicity, device
fingerprinting for anon tracking) for what's meant to be a friendly
"come back later or sign up" prompt, not a hard security boundary.
"""
from __future__ import annotations

import os
import requests
from datetime import datetime, timezone, timedelta
from flask import Request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_REQUEST_TIMEOUT = 10

ANON_DAILY_LIMIT = 1
FREE_TIER_BATCH_LIMIT = 5
FREE_TIER_RESET_DAYS = 4


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def get_client_ip(req: Request) -> str:
    """
    Render sits behind a reverse proxy, so request.remote_addr is the
    proxy's address, not the visitor's -- the real IP is the first entry
    in X-Forwarded-For (set by the proxy, not spoofable by the client
    since Render overwrites/appends to it rather than passing through
    an arbitrary client-supplied value).
    """
    forwarded = req.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.remote_addr or "unknown"


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def check_anon_usage(ip: str) -> tuple[bool, str | None]:
    """Read-only check: has this IP already used today's single free analysis?"""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("[usage] check_anon_usage: Supabase not configured, failing open")
        return True, None  # auth/usage tracking not configured -- fail open

    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/anon_usage",
        headers=_headers(),
        params={"ip": f"eq.{ip}", "select": "used_date"},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        print(f"[usage] check_anon_usage GET failed {resp.status_code}: {resp.text}")
        return True, None  # fail open rather than block real users over a DB hiccup

    rows = resp.json()
    print(f"[usage] check_anon_usage ip={ip} today={_today_utc()} rows={rows}")
    if rows and rows[0].get("used_date") == _today_utc():
        return False, (
            "You've used today's free analysis. Sign up free for 5 analyses "
            "every few days, or come back tomorrow for another single look."
        )
    return True, None


def record_anon_usage(ip: str) -> None:
    """Called only after a successful analysis -- a failed/invalid request shouldn't burn the day's use."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("[usage] record_anon_usage: Supabase not configured, skipping")
        return
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/anon_usage",
        headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
        json={"ip": ip, "used_date": _today_utc(), "updated_at": datetime.now(timezone.utc).isoformat()},
        timeout=_REQUEST_TIMEOUT,
    )
    print(f"[usage] record_anon_usage ip={ip} status={resp.status_code} body={resp.text}")


def _fetch_usage_fields(user_id: str) -> dict:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers=_headers(),
        params={"id": f"eq.{user_id}", "select": "usage_count,usage_batch_started_at"},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {"usage_count": 0, "usage_batch_started_at": None}


def check_signup_usage(user_id: str) -> tuple[bool, str | None]:
    """Read-only check against the signed-up free tier's 5-per-4-days allowance."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return True, None

    try:
        fields = _fetch_usage_fields(user_id)
    except requests.RequestException:
        return True, None  # fail open over a DB hiccup

    batch_started = fields.get("usage_batch_started_at")
    count = fields.get("usage_count") or 0

    if batch_started:
        started_at = datetime.fromisoformat(batch_started.replace("Z", "+00:00"))
        batch_expired = datetime.now(timezone.utc) - started_at >= timedelta(days=FREE_TIER_RESET_DAYS)
    else:
        batch_expired = True

    if batch_expired or count < FREE_TIER_BATCH_LIMIT:
        return True, None

    reset_at = started_at + timedelta(days=FREE_TIER_RESET_DAYS)
    remaining = reset_at - datetime.now(timezone.utc)
    hours = max(int(remaining.total_seconds() // 3600), 1)
    when = f"{hours} hour{'s' if hours != 1 else ''}" if hours < 24 else f"{hours // 24} day{'s' if hours // 24 != 1 else ''}"
    return False, (
        f"You've used all {FREE_TIER_BATCH_LIMIT} free analyses for this period. "
        f"5 more unlock in about {when}, or upgrade for unlimited analyses."
    )


def record_signup_usage(user_id: str) -> None:
    """Called only after a successful analysis. Re-reads current state to decide new-batch vs. increment."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    try:
        fields = _fetch_usage_fields(user_id)
    except requests.RequestException:
        return

    batch_started = fields.get("usage_batch_started_at")
    count = fields.get("usage_count") or 0
    now = datetime.now(timezone.utc)

    batch_expired = True
    if batch_started:
        started_at = datetime.fromisoformat(batch_started.replace("Z", "+00:00"))
        batch_expired = now - started_at >= timedelta(days=FREE_TIER_RESET_DAYS)

    if batch_expired:
        new_count, new_batch_started = 1, now.isoformat()
    else:
        new_count, new_batch_started = count + 1, batch_started

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers=_headers(),
        params={"id": f"eq.{user_id}"},
        json={"usage_count": new_count, "usage_batch_started_at": new_batch_started},
        timeout=_REQUEST_TIMEOUT,
    )
