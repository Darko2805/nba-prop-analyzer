import unittest
from types import SimpleNamespace as P

from nba_prop_analyzer.analysis import context_factors as cf
from nba_prop_analyzer.analysis.probability import estimate_probability


def log(date, opp="BOS", **stats):
    return {"date": date, "opponent": opp, "minutes": 30, "pts": 0, "reb": 0, "ast": 0, "fg3": 0, **stats}


class TeammatesOutTest(unittest.TestCase):
    def setUp(self):
        self.me = P(name="Role Player", team_abbr="DEN", ppg=12.0)
        self.star = P(name="Nikola Jokić", team_abbr="DEN", ppg=28.0)
        self.bench = P(name="Deep Bench", team_abbr="DEN", ppg=4.0)
        self.other = P(name="Other Star", team_abbr="LAL", ppg=30.0)
        self.players = [self.me, self.star, self.bench, self.other]
        days = ["2026-11-0%d" % d for d in range(1, 6)]
        self.logs = {"Role Player": [log(d) for d in days], "Nikola Jokić": [log(d) for d in days]}

    def share(self, out, logs=None):
        return cf.missing_scoring_share(self.me, self.players, out, 112.0, logs or self.logs)

    def test_nobody_out_is_zero(self):
        self.assertEqual(self.share([]), (0.0, []))

    def test_star_out_counts_his_share_and_ignores_accents(self):
        share, names = self.share(["Nikola Jokic"])
        self.assertAlmostEqual(share, 28.0 / 112.0)
        self.assertEqual(names, ["Nikola Jokić"])

    def test_bench_player_and_other_teams_do_not_count(self):
        self.assertEqual(self.share(["Deep Bench", "Other Star"])[0], 0.0)

    def test_long_term_absence_is_already_in_the_averages(self):
        logs = dict(self.logs, **{"Nikola Jokić": []})
        self.assertEqual(self.share(["Nikola Jokić"], logs)[0], 0.0)

    def test_partly_absent_counts_partly(self):
        logs = dict(self.logs, **{"Nikola Jokić": self.logs["Nikola Jokić"][:2]})
        self.assertAlmostEqual(self.share(["Nikola Jokić"], logs)[0], 0.4 * 28.0 / 112.0)

    def test_the_player_himself_is_never_his_own_boost(self):
        self.assertEqual(cf.missing_scoring_share(self.star, self.players, ["Nikola Jokić"], 112.0, self.logs)[0], 0.0)


class PositionDefenseTest(unittest.TestCase):
    def test_soft_opponent_shows_a_shrunk_positive_gap(self):
        guard = P(name="G", position="PG")
        logs = {"G": [log("d1", "UTA", pts=30), log("d2", "BOS", pts=20), log("d3", "BOS", pts=20), log("d4", "BOS", pts=10)]}
        table = cf.build_position_defense([guard], logs)
        ratio, n = table[("UTA", "guards", "points")]
        self.assertAlmostEqual(ratio, 1.5)
        gap, group, raw, games = cf.position_gap(table, "UTA", "PG", "points")
        self.assertEqual((group, games), ("guards", 1))
        self.assertTrue(0 < gap < 0.05)  # one game barely counts

    def test_unknown_is_neutral(self):
        self.assertEqual(cf.position_gap({}, "UTA", "C", "points")[0], 0.0)
        self.assertEqual(cf.position_gap({}, "UTA", "", "points")[0], 0.0)

    def test_combo_strength_is_weighted(self):
        s = cf.prop_strength({"points": 0.5, "rebounds": 0.3}, "pts_reb", {"points": 30.0, "rebounds": 10.0})
        self.assertAlmostEqual(s, 0.45)


class TypicalNightTest(unittest.TestCase):
    def test_line_at_the_projection_leans_under(self):
        over, under = estimate_probability(20.0, 20.0, "points")
        self.assertTrue(0.40 < over < 0.48)
        self.assertAlmostEqual(over + under, 1.0)


if __name__ == "__main__":
    unittest.main()
