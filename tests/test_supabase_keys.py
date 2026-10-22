"""The server must work with both Supabase key formats so the key can be rotated by changing one env var."""
from __future__ import annotations

import unittest

from nba_prop_analyzer.data.supabase_keys import service_headers
from nba_prop_analyzer.web import usage
from nba_prop_analyzer.data import track_record

LEGACY = "eyJhbGciOiJIUzI1NiJ9.payload.signature"   # a JWT-shaped legacy service_role key (fake)
NEW = "sb_secret_abc123"                             # the newer, non-JWT secret key format (fake)


class ServiceHeaderTests(unittest.TestCase):
    def test_legacy_jwt_key_goes_in_both_headers_as_before(self):
        self.assertEqual(service_headers(LEGACY), {"apikey": LEGACY, "Authorization": f"Bearer {LEGACY}"})

    def test_new_secret_key_goes_in_apikey_only(self):
        self.assertEqual(service_headers(NEW), {"apikey": NEW})

    def test_extra_headers_are_added_for_both(self):
        for key in (LEGACY, NEW):
            h = service_headers(key, **{"Content-Type": "application/json", "Prefer": "return=minimal"})
            self.assertEqual(h["Content-Type"], "application/json")
            self.assertEqual(h["Prefer"], "return=minimal")
            self.assertEqual(h["apikey"], key)

    def test_the_modules_use_the_helper(self):
        for mod in (usage, track_record):
            old = mod.SUPABASE_SERVICE_ROLE_KEY
            try:
                mod.SUPABASE_SERVICE_ROLE_KEY = NEW
                self.assertNotIn("Authorization", mod._headers())
                mod.SUPABASE_SERVICE_ROLE_KEY = LEGACY
                self.assertIn("Authorization", mod._headers())
            finally:
                mod.SUPABASE_SERVICE_ROLE_KEY = old


if __name__ == "__main__":
    unittest.main()
