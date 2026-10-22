"""
Headers for server-side Supabase calls made with the project's secret key.

Supabase has two key formats and this must work with both, so the key can be
swapped by changing one environment variable:
  * legacy `service_role` keys are JWTs: sent as `apikey` AND `Authorization: Bearer`
  * the newer `sb_secret_...` keys are not JWTs: sent as `apikey` only (Supabase's gateway
    turns that into a short-lived token itself)
"""
from __future__ import annotations


def service_headers(key: str, **extra: str) -> dict:
    headers = {"apikey": key}
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    headers.update(extra)
    return headers
