"""
Lightweight passwordless auth on top of Supabase Auth (GoTrue) + Postgres.

Why Supabase: Render's free-tier disk doesn't survive a restart (not just
deploys — ordinary idle spin-down/spin-up wipes it too), so user accounts
need to live in a real external database. Supabase gives us that plus a
built-in email OTP flow for free, so we don't have to run our own SMTP or
build email verification from scratch.

Flow:
  1. User submits email + name -> request_magic_link() calls Supabase's
     /auth/v1/otp, which emails them a sign-in link. The name travels
     along as user metadata so it's available once they verify.
  2. Supabase's default email template (the free hosted mailer doesn't
     allow customizing it without setting up custom SMTP — a later
     upgrade) redirects to our `email_redirect_to` URL with the session
     in a URL *fragment* (`#access_token=...`), not a query param.
     Fragments never reach the server, so /auth/callback serves a tiny
     page whose JS reads window.location.hash and POSTs the access_token
     to /auth/complete-login.
  3. get_user_from_token() calls /auth/v1/user with that access token to
     fetch the Supabase user (id, email, user_metadata.name). We upsert
     a row in `profiles` (tier defaults to "free") and store just the
     user id/email/name/tier in the signed Flask session cookie —
     Supabase's access/refresh tokens aren't kept after that, since all
     profile writes go through the service key, not RLS.

Known tradeoff: some email clients (notably Gmail's link safety
pre-scan) occasionally open the link before the person clicks it,
burning the single-use token — if that happens the fix is just to
request a new one. Switching to a visible OTP code instead would avoid
this, but requires custom SMTP to show the code in the email at all
(the default hosted mailer's template only includes the link).
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
        raise AuthError(f"Supabase rejected the sign-in request ({resp.status_code}): {resp.text}")


def get_user_from_token(access_token: str) -> dict:
    """
    Resolves the Supabase user for an access token pulled from the magic
    link's redirect fragment by the browser. Raises AuthError if invalid.
    """
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError("That sign-in link is invalid or expired — request a new one.")
    return resp.json()


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
