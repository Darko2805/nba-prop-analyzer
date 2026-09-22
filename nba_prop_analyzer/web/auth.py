"""
Email + password auth (with email confirmation) on top of Supabase Auth
(GoTrue) + Postgres.

Why Supabase: Render's free-tier disk doesn't survive a restart (not just
deploys — ordinary idle spin-down/spin-up wipes it too), so user accounts
need to live in a real external database. Supabase gives us that plus a
built-in signup-confirmation email flow for free, so we don't have to run
our own SMTP or build email verification from scratch.

Flow:
  1. User submits name + email + password -> sign_up() calls Supabase's
     /auth/v1/signup, which creates an *unconfirmed* user and emails a
     confirmation link. The name travels along as user metadata.
  2. Supabase's default confirmation email template (the free hosted
     mailer doesn't allow customizing it without custom SMTP — a later
     upgrade) redirects to our `email_redirect_to` URL with the session
     in a URL *fragment* (`#access_token=...`), not a query param.
     Fragments never reach the server, so /auth/callback serves a tiny
     page whose JS reads window.location.hash and POSTs the access_token
     to /auth/complete-login — same mechanism regardless of whether the
     link came from signup confirmation or (in principle) a magic link.
  3. get_user_from_token() calls /auth/v1/user with that access token to
     fetch the Supabase user (id, email, user_metadata.name). We upsert
     a row in `profiles` (tier defaults to "free") and store just the
     user id/email/name/tier in the signed Flask session cookie.
  4. On later visits, sign_in_with_password() calls
     /auth/v1/token?grant_type=password directly — no email round-trip,
     just a normal API call that fails clearly if the account isn't
     confirmed yet or the password is wrong.

Passwords are only ever relayed over HTTPS straight to Supabase, which
hashes and stores them — this app never stores or sees a password at rest.
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
    """
    code is an optional machine-readable tag (e.g. "already_exists") the
    frontend can branch on — the message alone is meant for display, not
    parsing.
    """
    def __init__(self, message: str, code: str = None):
        super().__init__(message)
        self.code = code


def sign_up(email: str, password: str, name: str, redirect_to: str) -> None:
    """
    Creates an unconfirmed Supabase user and triggers the confirmation
    email. Raises AuthError on failure (including "already registered").
    """
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={
            "email": email,
            "password": password,
            "data": {"name": name},
            "options": {"email_redirect_to": redirect_to},
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if "already registered" in body.get("msg", "").lower() or body.get("error_code") == "user_already_exists":
            raise AuthError("An account with that email already exists — try logging in instead.", code="already_exists")
        if body.get("error_code") == "over_email_send_rate_limit":
            raise AuthError("We're sending a lot of sign-up emails right now — please try again in a few minutes.", code="rate_limited")
        raise AuthError(f"Supabase rejected the sign-up ({resp.status_code}): {resp.text}")

    # GoTrue's anti-enumeration behavior: signing up again with an email
    # that already has a CONFIRMED account returns 200 (not an error) with
    # a masked "success" response, so a public caller can't tell an email
    # is taken just from the status code -- no new email is actually sent.
    # Supabase's own documented tell for this case is an empty
    # `identities` array on the returned user (a genuinely new signup's
    # user object always has one entry). Without this check the caller
    # sees identical "success" whether or not anything was actually
    # sent, which is exactly what caused a real user to wait for a
    # confirmation email that was never going to arrive, day after day,
    # for an account they'd already confirmed and could just log into.
    try:
        body = resp.json()
    except ValueError:
        return
    if isinstance(body, dict) and body.get("identities") == []:
        raise AuthError("An account with that email already exists — try logging in instead.", code="already_exists")


def request_password_reset(email: str, redirect_to: str) -> None:
    """Ask Supabase to email a password-reset link. Never raises for an unknown
    email (Supabase itself stays silent about whether the account exists)."""
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/recover",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "options": {"redirect_to": redirect_to}},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("error_code") == "over_email_send_rate_limit":
            raise AuthError("We're sending a lot of emails right now — please try again in a few minutes.", code="rate_limited")
        raise AuthError(f"Supabase rejected the reset request ({resp.status_code}): {resp.text}")


def verify_otp(token_hash: str, otp_type: str) -> dict:
    """
    Redeems a one-time email token (the token_hash carried in our own
    confirmation/reset links) for a session. This must only ever be
    called from an explicit user action -- a button click -- never
    automatically when a page loads.

    Why: email link-scanners (Gmail's and Outlook Safe Links' own
    infrastructure) fetch every URL in an email to check it's safe,
    which silently burns Supabase's one-time verification token before
    the real user ever clicks. The user then gets "invalid or expired"
    and, since GoTrue's own /verify redirect has nowhere valid to send
    them, drops them on the plain site URL with no explanation --
    exactly what caused reset links to appear to "just go to the
    homepage." Routing links through our own page first (which does
    nothing until a click) and only redeeming the token on that click
    means a scanner's GET can't consume it.
    """
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/verify",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"type": otp_type, "token_hash": token_hash},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError("That link is invalid or has expired — request a new one.")
    return resp.json()


def update_password(access_token: str, new_password: str) -> dict:
    """Sets a new password using the access token from a recovery link. Returns the user dict."""
    resp = requests.put(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"password": new_password},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError("Couldn't update your password — the reset link may have expired.")
    return resp.json()


def sign_in_with_password(email: str, password: str) -> dict:
    """
    Direct password login, no email round-trip. Returns the Supabase user
    dict. Raises AuthError with a clear message for bad credentials vs. an
    unconfirmed account.
    """
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("error_code") == "email_not_confirmed":
            raise AuthError("Please confirm your email first — check your inbox for the confirmation link.")
        raise AuthError("Incorrect email or password.")
    payload = resp.json()
    user = payload.get("user")
    if not user:
        raise AuthError("Supabase didn't return a user for this login.")
    return user


def get_user_from_token(access_token: str) -> dict:
    """
    Resolves the Supabase user for an access token pulled from the
    confirmation link's redirect fragment by the browser. Raises AuthError
    if invalid.
    """
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise AuthError("That confirmation link is invalid or expired — try signing up again.")
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
