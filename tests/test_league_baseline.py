"""A factor must measure teams against what the league actually does, not a figure written down earlier."""
from __future__ import annotations

import unittest
from types import SimpleNamespace as T

from nba_prop_analyzer.analysis.league_baseline import league_mean

TEAMS = {f"T{i}": T(acc=0.40 + i * 0.01, missing=0.0) for i in range(12)}   # mean 0.455


class LeagueMeanTests(unittest.TestCase):
    def test_it_uses_the_real_mean_when_enough_teams_report(self):
        self.assertAlmostEqual(league_mean(TEAMS, "acc", 0.415), 0.455)

    def test_it_falls_back_when_too_few_teams_report(self):
        few = {k: TEAMS[k] for k in list(TEAMS)[:5]}
        self.assertEqual(league_mean(few, "acc", 0.415), 0.415)
        self.assertEqual(league_mean({}, "acc", 0.415), 0.415)
        self.assertEqual(league_mean(None, "acc", 0.415), 0.415)

    def test_zero_and_missing_values_are_not_counted_as_data(self):
        self.assertEqual(league_mean(TEAMS, "missing", 0.5), 0.5)      # every value 0 -> fallback
        mixed = dict(TEAMS, Z=T(acc=0.0, missing=0.0))                  # a 0 must not drag the mean down
        self.assertAlmostEqual(league_mean(mixed, "acc", 0.415), 0.455)

    def test_an_absent_attribute_is_treated_as_no_data(self):
        self.assertEqual(league_mean({"A": T(other=1.0)}, "acc", 0.33), 0.33)

    def test_a_drifted_fallback_would_bias_every_team(self):
        # the real point of the helper: with a fallback 10% below reality every team reads "weak"
        real, stale = league_mean(TEAMS, "acc", 0.415), 0.415
        self.assertGreater(real / stale - 1, 0.09)


if __name__ == "__main__":
    unittest.main()
