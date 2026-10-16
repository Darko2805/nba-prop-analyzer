"""Anonymous daily allowance: 3 per day, blocked only when BOTH IP and browser are used up."""
from __future__ import annotations

import re
import unittest
from unittest import mock

from flask import Flask

from nba_prop_analyzer.web import usage


class FakeSupabase:
    """Just enough of PostgREST's anon_usage table for usage.py's GET / POST / DELETE."""

    def __init__(self):
        self.rows = {}  # key -> used_date
        self.fail_get = False

    def get(self, url, headers=None, params=None, timeout=None):
        resp = mock.Mock()
        if self.fail_get:
            resp.status_code, resp.text = 500, "boom"
            return resp
        keys = re.match(r"in\.\((.*)\)", params["ip"]).group(1).split(",")
        resp.status_code = 200
        resp.json.return_value = [{"ip": k, "used_date": self.rows[k]} for k in keys if k in self.rows]
        return resp

    def post(self, url, headers=None, json=None, timeout=None):
        for row in json if isinstance(json, list) else [json]:
            self.rows[row["ip"]] = row["used_date"]
        resp = mock.Mock(); resp.status_code = 201
        return resp

    def delete(self, *a, **k):
        resp = mock.Mock(); resp.status_code = 204
        return resp


class AnonUsageTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeSupabase()
        patches = [
            mock.patch.object(usage, "SUPABASE_URL", "https://example.test"),
            mock.patch.object(usage, "SUPABASE_SERVICE_ROLE_KEY", "key"),
            mock.patch.object(usage.requests, "get", self.db.get),
            mock.patch.object(usage.requests, "post", self.db.post),
            mock.patch.object(usage.requests, "delete", self.db.delete),
        ]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        self.app = Flask(__name__)

    def use(self, ip, token):
        """One full anonymous analysis: check, and record if allowed. Returns allowed."""
        request = mock.Mock(headers={"X-Forwarded-For": ip}, remote_addr=None)
        hashed_ip = usage.get_client_ip(request)   # same step the /analyze route performs
        with self.app.app_context():
            v = usage.visitor_key(token)
            allowed, _ = usage.check_anon_usage(hashed_ip, v)
            if allowed:
                usage.record_anon_usage(hashed_ip, v)
            return allowed

    def test_same_ip_and_browser_gets_exactly_three(self):
        results = [self.use("ipA", "browser-1") for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])

    def test_people_sharing_one_ip_each_keep_their_own_allowance(self):
        for n in range(3):                      # three users on one office IP use their 3 each
            for _ in range(3):
                self.assertTrue(self.use("office", f"browser-{n}"))
        # IP is now used up, but a fourth person's browser is fresh
        self.assertTrue(self.use("office", "browser-new"))

    def test_browser_that_moves_to_a_new_ip_is_not_blocked(self):
        for _ in range(3):
            self.use("home", "browser-1")
        self.assertTrue(self.use("cafe", "browser-1"))

    def test_without_a_cookie_only_the_ip_is_known_so_not_blocked(self):
        for _ in range(3):
            self.use("ipA", "browser-1")
        self.assertTrue(self.use("ipA", None))

    def test_yesterdays_rows_do_not_count(self):
        request = mock.Mock(headers={"X-Forwarded-For": "ipA"}, remote_addr=None)
        keys = (usage.get_client_ip(request), usage.visitor_key("b"))
        self.db.rows.update({f"{k}:{i}": "2000-01-01" for k in keys for i in range(3)})
        self.assertTrue(self.use("ipA", "b"))

    def test_database_failure_fails_open(self):
        self.db.fail_get = True
        self.assertTrue(self.use("ipA", "b"))

    def test_recording_stops_once_both_are_full(self):
        for _ in range(3):
            self.use("ipA", "b")
        size = len(self.db.rows)
        request = mock.Mock(headers={"X-Forwarded-For": "ipA"}, remote_addr=None)
        with self.app.app_context():
            usage.record_anon_usage(usage.get_client_ip(request), usage.visitor_key("b"))
        self.assertEqual(len(self.db.rows), size)

    def test_keys_never_contain_the_raw_ip_or_token(self):
        self.use("203.0.113.9", "secret-token")
        for key in self.db.rows:
            self.assertNotIn("203.0.113.9", key)
            self.assertNotIn("secret-token", key)


if __name__ == "__main__":
    unittest.main()
