"""
Lightweight passwordless auth on top of Supabase Auth (GoTrue) + Postgres.

Why Supabase: Render's free-tier disk doesn't survive a restart (not just
deploys — ordinary idle spin-down/spin-up wipes it too), so user accounts
need to live in a real external database. Supabase gives us that plus a
built-in magic-link email flow for free, so we don't have to run our own
SMTP or build email verification from scratch.

Flow (server-side only, no client JS/SDK needed):
  1. User submits email + name -> request_magic_link() calls Supabase's
     /auth/v1/otp, which emails them a link. The name travels along as
     user metadata so it's available once they verify.
  2. Supabase's email template must point the link at OUR callback with
     `token_hash` and `type` as query params (not the implicit-flow
     fragment tokens, which never reach the server) — see AUTH_SETUP.md.
  3. They click it -> Flask's /auth/callback calls verify_magic_link(),
     which exchanges the token_hash for a real session server-side and
     upserts a row in `profiles` (tier defaults to "free").
  4. We store just the user id + email + name in the Flask session cookie
     (signed, not a JWT) — Supabase's own access/refresh tokens aren't
     needed after that point since we're not calling any RLS-protected
     endpoints as the user; all profile writes go through the service key.
"""
from __future__ import annotations

import os
import requests
from flask import session

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_REQUEST_TIMEOUT = 10


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY)


class AuthError(Exception):
    pass


def request_magic_link(email: str, name: str, redirect_to: str) -> None:
    """Ask Supabase to email a magic sign-in link. Raises AuthError on failure."""
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/otp",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={
            "email": email,
            "create_user": True,
            "data": {"name": name},
            "options": {"email_redirect_to": redirect_to},
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError(f"Supabase rejected the magic-link request ({resp.status_code}): {resp.text}")


def verify_magic_link(token_hash: str, otp_type: str = "email") -> dict:
    """
    Exchanges the token_hash from the email link for a session, server-side.
    Returns the Supabase user dict ({id, email, user_metadata: {name}, ...}).
    Raises AuthError on an invalid/expired link.
    """
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/verify",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"type": otp_type, "token_hash": token_hash},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError(f"That sign-in link is invalid or expired ({resp.status_code}): {resp.text}")
    payload = resp.json()
    user = payload.get("user")
    if not user:
        raise AuthError("Supabase didn't return a user for this link.")
    return user


def upsert_profile(user_id: str, email: str, name: str) -> dict:
    """
    Creates the profile row on first sign-in, or updates name/email on
    later ones, without touching an existing tier. Uses the service role
    key so this works regardless of RLS policy (this is a trusted server
    context, never exposed to the browser). Returns the profile row.
    """
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        json={"id": user_id, "email": email, "name": name},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError(f"Couldn't save your profile ({resp.status_code}): {resp.text}")
    rows = resp.json()
    return rows[0] if rows else {"id": user_id, "email": email, "name": name, "tier": "free"}


def get_profile(user_id: str) -> dict:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
        params={"id": f"eq.{user_id}", "select": "*"},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


def log_in_session(profile: dict) -> None:
    session["user_id"] = profile["id"]
    session["email"] = profile["email"]
    session["name"] = profile.get("name") or ""
    session["tier"] = profile.get("tier") or "free"


def log_out_session() -> None:
    session.clear()


def current_user() -> dict | None:
    if "user_id" not in session:
        return None
    return {
        "id": session["user_id"],
        "email": session["email"],
        "name": session["name"],
        "tier": session["tier"],
    }
