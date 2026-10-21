"""In production a web request must never scrape basketball-reference (it blocks the host's IP):
a player missing from the snapshot gets season averages, not a multi-second failing fetch."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from nba_prop_analyzer.analysis import prop_analyzer as pa


def make_analyzer():
    return pa.PropAnalyzer.__new__(pa.PropAnalyzer)  # skip load_data(); these methods need no state


class NoLiveScrapeTests(unittest.TestCase):
    player = SimpleNamespace(name="Anthony Davis", blend_weight=0.0)

    def run_case(self, live_enabled):
        with mock.patch.object(pa, "LIVE_BBREF_FALLBACK", live_enabled), \
             mock.patch.object(pa.snapshot_store, "load_game_logs", return_value=None), \
             mock.patch.object(pa.snapshot_store, "load_shot_zone", return_value=None), \
             mock.patch.object(pa, "fetch_game_logs", return_value=[]) as logs, \
             mock.patch.object(pa, "fetch_shot_zone_profile", return_value=None) as zones:
            a = make_analyzer()
            a._league_zone = lambda: {}
            result = (a._game_logs_for(self.player), a._shot_zone_for(self.player))
            return result, logs.call_count, zones.call_count

    def test_production_never_fetches_live(self):
        (logs, zone), log_calls, zone_calls = self.run_case(False)
        self.assertEqual((logs, zone, log_calls, zone_calls), ([], None, 0, 0))

    def test_local_dev_still_falls_back_to_live(self):
        _, log_calls, zone_calls = self.run_case(True)
        self.assertEqual(log_calls, 2)   # last season + this season
        self.assertEqual(zone_calls, 1)


if __name__ == "__main__":
    unittest.main()
