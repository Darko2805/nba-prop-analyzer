"""
Operator notifications: an email to the site owner when something worth
knowing about happens (currently: a new signup).

Sent through Brevo's transactional API (the same Brevo account that already
delivers account emails). Entirely optional -- with the env vars below unset
it does nothing, and a send failure is logged and swallowed so it can never
block or slow a user's signup.

  OPERATOR_ALERT_EMAIL   where alerts go (falls back to LEGAL_CONTACT_EMAIL)
  BREVO_API_KEY          Brevo API key (v3) -- set in Railway only, never in git
  BREVO_SENDER_EMAIL     a sender address verified in Brevo
"""
from __future__ import annotations

import os
import threading

import requests

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _send(subject: str, text: str) -> None:
    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    sender = os.environ.get("BREVO_SENDER_EMAIL", "").strip()
    to = (os.environ.get("OPERATOR_ALERT_EMAIL") or os.environ.get("LEGAL_CONTACT_EMAIL") or "").strip()
    if not (api_key and sender and to):
        return
    try:
        resp = requests.post(
            _BREVO_URL,
            headers={"api-key": api_key, "accept": "application/json"},
            json={
                "sender": {"email": sender, "name": "matchupedge"},
                "to": [{"email": to}],
                "subject": subject,
                "textContent": text,
            },
            timeout=8,
        )
        if resp.status_code >= 400:
            print(f"[notify] Brevo send failed {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[notify] send skipped ({e})")


def notify_async(subject: str, text: str) -> None:
    """Fire-and-forget so the request that triggered it never waits on Brevo."""
    threading.Thread(target=_send, args=(subject, text), daemon=True, name="notify").start()


def new_signup(email: str, name: str, marketing_opt_in: bool) -> None:
    notify_async(
        "New matchupedge signup",
        f"{name} <{email}> just signed up (confirmation email sent).\n"
        f"Marketing opt-in: {'yes' if marketing_opt_in else 'no'}\n",
    )
