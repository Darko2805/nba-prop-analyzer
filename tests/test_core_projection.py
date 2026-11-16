import unittest
from types import SimpleNamespace as G

from nba_prop_analyzer.analysis.core_projection import core_estimate, core_note, resize_factor


def game(minutes, pts):
    return G(minutes=minutes, pts=pts, reb=0, ast=0, fg3=0, fgm=0, fga=0, fg3a=0, tov=0)


class CoreProjectionTest(unittest.TestCase):
    def test_no_games_gives_nothing(self):
        self.assertEqual(core_estimate("points", [], []), (None, {}))

    def test_before_the_opener_it_is_last_season(self):
        est, d = core_estimate("points", [], [game(30, 20)] * 60)
        self.assertAlmostEqual(est, 20.0)
        self.assertIsNone(d["recent_minutes"])

    def test_one_big_night_barely_moves_a_full_last_season(self):
        est, _ = core_estimate("points", [game(30, 45)], [game(30, 20)] * 60)
        self.assertTrue(20 < est < 24, est)

    def test_more_minutes_lifts_the_estimate_at_the_same_rate(self):
        prior = [game(24, 12)] * 60
        est, d = core_estimate("points", [game(34, 17)] * 5, prior)
        self.assertAlmostEqual(est / d["expected_minutes"], 0.5)
        self.assertTrue(d["expected_minutes"] > 31)

    def test_rookie_uses_own_games(self):
        est, _ = core_estimate("points", [game(20, 10)] * 4, [])
        self.assertAlmostEqual(est, 10.0)

    def test_thin_last_season_is_ignored(self):
        est, _ = core_estimate("points", [game(20, 10)] * 4, [game(30, 30)] * 3)
        self.assertAlmostEqual(est, 10.0)

    def test_factor_keeps_direction_at_a_quarter_strength(self):
        self.assertAlmostEqual(resize_factor(1.20), 1.05)
        self.assertAlmostEqual(resize_factor(0.80), 0.95)
        self.assertEqual(resize_factor(1.0), 1.0)

    def test_note_reads_plainly(self):
        _, d = core_estimate("points", [game(34, 17)] * 5, [game(24, 12)] * 60)
        self.assertIn("34.0 min over the last 5 games", core_note(1.3, d))
        self.assertTrue(core_note(1.0, {}).startswith("~"))


if __name__ == "__main__":
    unittest.main()
