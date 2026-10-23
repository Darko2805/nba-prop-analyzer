"""
A defence's weakness must be counted once.

Before this was enforced, the shot-zone factor took the usage-weighted accuracy gap (already the
expected change in scoring), doubled it, then added a league-rank bonus reading the same weakness
again -- turning Sacramento's real +8.2% edge for Lillard into +28%.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace as T

from nba_prop_analyzer.analysis import free_throw as ft
from nba_prop_analyzer.analysis import shot_zone as sz


def player(**kw):
    base = dict(name="P", at_rim_freq=0.20, short_mid_freq=0.20, mid_range_freq=0.10,
                three_point_rate=0.50, ft_rate=0.30, at_rim_acc=0.65, short_mid_acc=0.42,
                long_mid_acc=0.40, three_pct=0.36)
    base.update(kw)
    return T(**base)


def defence(scale=1.0, fta=24.0):
    """A defence `scale` times as generous as the league in every zone."""
    return T(opp_at_rim_acc=0.674 * scale, opp_short_mid_acc=0.460 * scale,
             opp_long_mid_acc=0.429 * scale, opp_3p_pct=0.357 * scale, opp_fta=fta)


# 12 teams so league_mean uses the real average rather than the written-down fallback
LEAGUE = {f"T{i}": defence(1.0 + (i - 6) * 0.01, fta=24.0 + (i - 6) * 0.5) for i in range(12)}


class ShotZoneCountsOnceTests(unittest.TestCase):
    def factor(self, opp):
        f, _, _ = sz.calculate_shot_zone_exploitation(player(), T(), opp, "points", dict(LEAGUE, X=opp))
        return f

    def test_the_settings_are_the_agreed_ones(self):
        self.assertEqual((sz.GAP_AMPLIFIER, sz.SEVERITY_WEIGHT), (1.0, 0.5))
        self.assertEqual((ft.GAP_AMPLIFIER, ft.SEVERITY_WEIGHT), (1.0, 0.5))

    def test_an_average_defence_moves_nothing(self):
        self.assertAlmostEqual(self.factor(defence(1.0)), 1.0, places=2)

    def test_the_boost_never_exceeds_twice_the_real_gap(self):
        # a defence allowing 10% better shooting everywhere: the honest read is about +10%,
        # and the rank bonus may add to that, but it must not approach the old ~3x reading.
        got = self.factor(defence(1.10))
        self.assertGreater(got, 1.05)
        self.assertLess(got, 1.10 * 2)

    def test_it_stays_inside_the_published_limits_both_ways(self):
        for scale in (0.5, 0.8, 1.0, 1.2, 1.5):
            f = self.factor(defence(scale))
            self.assertGreaterEqual(f, sz.ZONE_FACTOR_MIN)
            self.assertLessEqual(f, sz.ZONE_FACTOR_MAX)

    def test_a_stingy_defence_lowers_it(self):
        self.assertLess(self.factor(defence(0.90)), 1.0)

    def test_doubling_the_amplifier_would_double_the_claim(self):
        """Guards the knob itself: if someone restores x2.0 the effect is visibly larger."""
        honest = self.factor(defence(1.10))
        sz.GAP_AMPLIFIER = 2.0
        try:
            inflated = self.factor(defence(1.10))
        finally:
            sz.GAP_AMPLIFIER = 1.0
        self.assertGreater(inflated - 1.0, (honest - 1.0) * 1.4)


class FreeThrowCountsOnceTests(unittest.TestCase):
    def factor(self, fta):
        f, _ = ft.calculate_free_throw_factor(player(), defence(1.0, fta=fta), "points", dict(LEAGUE, X=defence(1.0, fta=fta)))
        return f

    def test_an_average_fouling_rate_moves_nothing(self):
        self.assertAlmostEqual(self.factor(24.0), 1.0, places=2)

    def test_a_foul_prone_defence_helps_only_in_proportion_to_the_players_ft_rate(self):
        # +25% FTA allowed, player takes 30% of his scoring from the line -> well under +25%
        got = self.factor(30.0)
        self.assertGreater(got, 1.0)
        self.assertLess(got, 1.15)

    def test_it_stays_inside_its_limits(self):
        for fta in (15.0, 20.0, 24.0, 30.0, 40.0):
            self.assertGreaterEqual(self.factor(fta), 0.92)
            self.assertLessEqual(self.factor(fta), 1.14)


if __name__ == "__main__":
    unittest.main()
