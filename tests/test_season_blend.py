"""
End-to-end check of the early-season blend against a synthetic in-season snapshot.

Last season's data is the real snapshot (the *_prev.json files). The "current
season" is built here with known numbers, so every blended value can be asserted
exactly. Run:  python3 -m unittest tests.test_season_blend -v
"""
import copy
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from nba_prop_analyzer.cache import cache
from nba_prop_analyzer.config import PLAYER_BLEND_K, TEAM_BLEND_K, ROOKIE_BLEND_K
from nba_prop_analyzer.data import snapshot_store, databallr_client
from nba_prop_analyzer.data.bbref_scraper import GameLog
from nba_prop_analyzer.data.season_blend import blend_weight

REAL_SNAPSHOT = os.path.join(ROOT, "nba_prop_analyzer", "data", "snapshot")


def _prior(name):
    with open(os.path.join(REAL_SNAPSHOT, name)) as f:
        return json.load(f)


class SeasonBlendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for f in os.listdir(REAL_SNAPSHOT):
            if f.endswith("_prev.json"):
                shutil.copy(os.path.join(REAL_SNAPSHOT, f), cls.tmp)

        prior_players = {p["name"]: p for p in _prior("players_prev.json")}
        cls.prior_players = prior_players

        # current-season players: LeBron 10 games, Tatum 30, Curry 1, plus a rookie with no prior
        def cur(name, games, scale):
            p = dict(prior_players[name])
            p["games_played"] = games
            p["ppg"] = p["ppg"] * scale
            return p
        rookie = dict(prior_players["LeBron James"], name="Test Rookie", games_played=5, ppg=12.0)
        rookie_one_game = dict(prior_players["LeBron James"], name="Test Rookie 1g", games_played=1, ppg=30.0)
        current_players = [cur("LeBron James", 10, 1.5), cur("Jayson Tatum", 30, 1.5),
                           cur("Stephen Curry", 1, 1.5), rookie, rookie_one_game]
        cls.rookie = rookie
        cls._write("players.json", current_players)

        # LeBron: 10 new games after last season's, all 40 pts
        prior_logs = _prior("game_logs_prev.json")["LeBron James"]
        new_logs = [dict(prior_logs[-1], date=f"2026-10-{21 + i:02d}", pts=40) for i in range(10)]
        cls._write("game_logs.json", {"LeBron James": new_logs})
        cls.prior_log_count = len(prior_logs)

        prior_zone = _prior("shot_zones_prev.json")["LeBron James"]
        cls._write("shot_zones.json", {"LeBron James": dict(prior_zone, at_rim_freq=prior_zone["at_rim_freq"] + 0.2),
                                       "Test Rookie": dict(prior_zone, at_rim_freq=0.9)})
        cls.prior_zone = prior_zone

        # one team (BOS) has played 15 games, scoring 10% more per game than last season
        raw = _prior("team_stats_prev.json")
        cur_raw = copy.deepcopy(raw)
        for side in ("team", "opponent"):
            rows = [r for r in cur_raw[side]["team_data"] if r.get("TeamAbbreviation") == "BOS"]
            assert rows, "BOS missing from the prior team snapshot"
            for r in rows:
                gp = r.get("GamesPlayed") or 82
                for k in ("OffPoss", "DefPoss", "Points", "OpponentPoints"):
                    if k in r and r[k]:
                        r[k] = r[k] * 15 / gp * (1.1 if k == "Points" else 1.0)
                r["GamesPlayed"] = 15
            cur_raw[side]["team_data"] = rows
        cls._write("team_stats.json", cur_raw)

        prior_zd = _prior("opponent_zone_defense_prev.json")
        cls.prior_zd_bos = prior_zd["BOS"]
        cls._write("opponent_zone_defense.json", {"BOS": dict(prior_zd["BOS"], short_mid_acc=prior_zd["BOS"]["short_mid_acc"] + 0.10)})

        cls.prior_tr = _prior("teamrankings_prev.json")["BOS"]

        cls.patches = [
            patch.object(snapshot_store, "_SNAPSHOT_DIR", cls.tmp),
            patch("nba_prop_analyzer.analysis.prop_analyzer.fetch_current_teams", return_value={}),
            patch("nba_prop_analyzer.analysis.prop_analyzer.fetch_teamrankings_opponent_stats",
                  return_value=({"BOS": dict(cls.prior_tr, opp_ppg=cls.prior_tr["opp_ppg"] * 1.2)}, "2026")),
            patch.object(databallr_client, "fetch_team_stats_raw", return_value=cur_raw),
            # the analysis asks ESPN who is on a back-to-back; keep the test off the network
            patch("nba_prop_analyzer.analysis.prop_analyzer.fetch_back_to_back_teams", return_value=set()),
            patch("nba_prop_analyzer.analysis.prop_analyzer.fetch_three_in_four_teams", return_value=set()),
        ]
        for p in cls.patches:
            p.start()
        snapshot_store._shot_zones_cache.clear()
        snapshot_store._game_logs_cache.clear()
        cache._store.clear()

        from nba_prop_analyzer.analysis.prop_analyzer import PropAnalyzer
        cls.an = PropAnalyzer()
        cls.an.load_data()
        cls.by_name = {p.name: p for p in cls.an.players}

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp)

    @classmethod
    def _write(cls, name, data):
        with open(os.path.join(cls.tmp, name), "w") as f:
            json.dump(data, f)

    # ---- players ----
    def test_player_weights_follow_games_over_games_plus_k(self):
        for name, games in (("LeBron James", 10), ("Jayson Tatum", 30), ("Stephen Curry", 1)):
            self.assertAlmostEqual(self.by_name[name].blend_weight, games / (games + PLAYER_BLEND_K), places=9)

    def test_player_stats_are_a_weighted_mix(self):
        for name, games in (("LeBron James", 10), ("Jayson Tatum", 30), ("Stephen Curry", 1)):
            w = games / (games + PLAYER_BLEND_K)
            prior = self.prior_players[name]["ppg"]
            self.assertAlmostEqual(self.by_name[name].ppg, w * prior * 1.5 + (1 - w) * prior, places=6)
            self.assertEqual(self.by_name[name].games_played, games)

    def test_player_without_new_games_is_exactly_last_season(self):
        p, prior = self.by_name["Nikola Jokić"], self.prior_players["Nikola Jokić"]
        self.assertEqual(p.blend_weight, 0.0)
        self.assertEqual(p.ppg, prior["ppg"])
        self.assertEqual(p.games_played, prior["games_played"])

    def _league_pool(self):
        return [p for p in self.prior_players.values() if p["games_played"] >= 5 and p["mpg"] >= 12.0]

    def test_rookie_blends_with_a_league_average_player_of_their_own_minutes(self):
        pool, r = self._league_pool(), self.rookie
        total = sum(p["games_played"] * p["mpg"] for p in pool)
        league_ppg_per_min = sum(p["games_played"] * p["ppg"] for p in pool) / total
        w = 5 / (5 + ROOKIE_BLEND_K)
        got = self.by_name["Test Rookie"]
        self.assertAlmostEqual(got.blend_weight, w, places=9)
        self.assertAlmostEqual(got.ppg, w * 12.0 + (1 - w) * league_ppg_per_min * r["mpg"], places=6)
        self.assertEqual(got.mpg, r["mpg"])            # their role is observed, never blended
        self.assertEqual(got.games_played, 5)

    def test_rookie_rate_stats_use_the_minutes_weighted_league_mean(self):
        pool, r = self._league_pool(), self.rookie
        total = sum(p["games_played"] * p["mpg"] for p in pool)
        league_ts = sum(p["games_played"] * p["mpg"] * p["ts_pct"] for p in pool) / total
        w = 5 / (5 + ROOKIE_BLEND_K)
        self.assertAlmostEqual(self.by_name["Test Rookie"].ts_pct, w * r["ts_pct"] + (1 - w) * league_ts, places=6)

    def test_one_game_rookie_is_mostly_the_league_baseline_not_that_one_game(self):
        pool = self._league_pool()
        total = sum(p["games_played"] * p["mpg"] for p in pool)
        baseline = sum(p["games_played"] * p["ppg"] for p in pool) / total * self.rookie["mpg"]
        w = 1 / (1 + ROOKIE_BLEND_K)
        got = self.by_name["Test Rookie 1g"]
        self.assertAlmostEqual(got.blend_weight, w, places=9)
        self.assertAlmostEqual(got.ppg, w * 30.0 + (1 - w) * baseline, places=6)
        self.assertLess(abs(got.ppg - baseline), abs(got.ppg - 30.0))   # nearer the baseline than the one big game

    def test_rookie_shot_zones_start_from_the_league_average_profile(self):
        zones = list(_prior("shot_zones_prev.json").values())
        league_rim = sum(z["at_rim_freq"] for z in zones) / len(zones)
        w = self.by_name["Test Rookie"].blend_weight
        z = self.an._shot_zone_for(self.by_name["Test Rookie"])
        self.assertAlmostEqual(z["at_rim_freq"], w * 0.9 + (1 - w) * league_rim, places=9)

    # ---- game logs, shot zones ----
    def test_game_logs_are_last_season_then_this_season_in_order(self):
        logs = self.an._game_logs_for(self.by_name["LeBron James"])
        self.assertEqual(len(logs), self.prior_log_count + 10)
        self.assertEqual([g.pts for g in logs[-10:]], [40] * 10)
        dates = [g.date for g in logs]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(self.an._game_logs_for(self.by_name["Nikola Jokić"])), len(snapshot_store.load_game_logs("Nikola Jokić", prev=True)))

    def test_shot_zones_blend_by_the_players_weight(self):
        w = self.by_name["LeBron James"].blend_weight
        z = self.an._shot_zone_for(self.by_name["LeBron James"])
        self.assertAlmostEqual(z["at_rim_freq"], self.prior_zone["at_rim_freq"] + w * 0.2, places=9)

    # ---- teams ----
    def test_team_weight_uses_team_k(self):
        self.assertAlmostEqual(self.an.team_weights["BOS"], 15 / (15 + TEAM_BLEND_K), places=9)
        self.assertEqual(self.an.team_weights["LAL"], 0.0)

    def test_team_profile_is_a_mix_and_untouched_teams_are_last_season(self):
        prior_profiles, prior_opps, _ = databallr_client._parse_team_raw(_prior("team_stats_prev.json"))
        w = self.an.team_weights["BOS"]
        self.assertAlmostEqual(self.an.team_profiles["BOS"].ppg, prior_profiles["BOS"].ppg * (w * 1.1 + (1 - w)), places=6)
        self.assertAlmostEqual(self.an.team_profiles["BOS"].pace, prior_profiles["BOS"].pace, places=6)
        self.assertEqual(self.an.team_profiles["LAL"].ppg, prior_profiles["LAL"].ppg)

    def test_opponent_zone_defense_blends_by_team_weight(self):
        w = self.an.team_weights["BOS"]
        self.assertAlmostEqual(self.an.opponent_defenses["BOS"].opp_short_mid_acc,
                               self.prior_zd_bos["short_mid_acc"] + w * 0.10, places=9)

    def test_teamrankings_mixes_current_with_the_prior_snapshot(self):
        w = self.an.team_weights["BOS"]
        self.assertAlmostEqual(self.an.opponent_defenses["BOS"].opp_ppg,
                               self.prior_tr["opp_ppg"] * (w * 1.2 + (1 - w)), places=6)

    # ---- end to end ----
    def test_analysis_runs_and_the_blend_moves_the_projection(self):
        lebron = self.an.analyze_prop("LeBron James", "LAL", "points", 22.5)
        self.assertGreater(lebron.predicted_value, 0)
        self.assertGreater(lebron.season_avg, self.prior_players["LeBron James"]["ppg"])


if __name__ == "__main__":
    unittest.main()
