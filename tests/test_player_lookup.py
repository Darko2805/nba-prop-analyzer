"""Player type-ahead ranking and the "next game" lookup behind the Analyze form."""
from __future__ import annotations

import datetime
import unittest
from types import SimpleNamespace as P

from nba_prop_analyzer.web import player_lookup as pl

PLAYERS = [
    P(name="LeBron James", mpg=34.0, ppg=24.4),
    P(name="James Harden", mpg=33.0, ppg=22.0),
    P(name="Jaylen Brown", mpg=34.4, ppg=24.0),
    P(name="Bruce Brown", mpg=18.0, ppg=7.0),
    P(name="Jaren Jackson Jr.", mpg=30.3, ppg=20.0),
    P(name="Luka Dončić", mpg=36.0, ppg=33.0),
    P(name="De'Aaron Fox", mpg=35.0, ppg=26.0),
    P(name="Trayce Jackson-Davis", mpg=9.4, ppg=4.0),
    P(name="Ghost Player", mpg=0.0, ppg=0.0),
]
names = lambda q: [p.name for p in pl.search_players(PLAYERS, q)]


class SearchTests(unittest.TestCase):
    def test_first_name_fragment(self):
        self.assertEqual(names("lebr"), ["LeBron James"])

    def test_one_word_matches_first_or_last_names_ranked_by_minutes(self):
        # "james" is LeBron's last name and Harden's first name: stars/minutes decide, not which half matched
        self.assertEqual(names("james"), ["LeBron James", "James Harden"])

    def test_one_word_prefers_rotation_players_over_bench_first_name_matches(self):
        pool = PLAYERS + [P(name="Brook Lopez", mpg=8.0, ppg=8.5), P(name="Bronny James", mpg=6.0, ppg=2.9)]
        out = [p.name for p in pl.search_players(pool, "bro")]
        self.assertEqual(out[:2], ["Jaylen Brown", "Bruce Brown"])

    def test_several_words_prefer_a_full_name_that_starts_with_them(self):
        pool = PLAYERS + [P(name="Alex Luka Mann", mpg=40.0, ppg=30.0)]
        self.assertEqual([p.name for p in pl.search_players(pool, "luka don")][0], "Luka Dončić")

    def test_same_last_name_orders_by_minutes_not_alphabet(self):
        self.assertEqual(names("brown"), ["Jaylen Brown", "Bruce Brown"])

    def test_suffix_and_punctuation_are_ignored(self):
        self.assertEqual(names("jackson jr"), ["Jaren Jackson Jr."])
        self.assertEqual(names("deaaron"), ["De'Aaron Fox"])
        self.assertEqual(names("de'aaron"), ["De'Aaron Fox"])

    def test_diacritics_and_hyphens(self):
        self.assertEqual(names("doncic"), ["Luka Dončić"])
        self.assertIn("Trayce Jackson-Davis", names("davis"))

    def test_middle_of_a_word_still_finds_them_but_after_better_matches(self):
        self.assertEqual(names("ebron"), ["LeBron James"])

    def test_needs_two_characters_and_ignores_players_who_never_played(self):
        self.assertEqual(names("l"), [])
        self.assertEqual(names("ghost"), [])

    def test_limit_and_no_duplicates(self):
        many = [P(name=f"Sam Player{i}", mpg=float(i), ppg=1.0) for i in range(20)] + [P(name="Sam Player3", mpg=99.0, ppg=1.0)]
        out = pl.search_players(many, "sam", limit=8)
        self.assertEqual(len(out), 8)
        self.assertEqual(len({p.name for p in out}), 8)

    def test_exact_player_never_guesses(self):
        self.assertEqual(pl.exact_player(PLAYERS, "luka doncic").name, "Luka Dončić")
        self.assertIsNone(pl.exact_player(PLAYERS, "luka"))


# --- next game ---------------------------------------------------------------
TEAMS = {"Boston Celtics": "BOS", "New York Knicks": "NYK", "Miami Heat": "MIA", "Chicago Bulls": "CHI"}
norm = TEAMS.get
D = datetime.date
today = D(2026, 10, 21)
game = lambda away, home, t="7:30p ET": {"away_team": away, "home_team": home, "start_time": t}


class NextGameTests(unittest.TestCase):
    def test_tonight_home_game(self):
        slates = {today: [game("New York Knicks", "Boston Celtics")]}
        g, ok = pl.next_game("BOS", slates, norm)
        self.assertEqual((g["opponent"], g["home"], g["date"], ok), ("NYK", True, today, True))
        self.assertEqual(pl.matchup_label(g, today), "Tonight · vs NYK · 7:30p ET")

    def test_away_game_label(self):
        g, _ = pl.next_game("NYK", {today: [game("New York Knicks", "Boston Celtics", "Live")]}, norm)
        self.assertEqual(pl.matchup_label(g, today), "Tonight · @ BOS · Live")

    def test_finished_game_is_skipped_to_the_next_one(self):
        slates = {today: [game("New York Knicks", "Boston Celtics", "Final")],
                  today + datetime.timedelta(days=2): [game("Boston Celtics", "Miami Heat")]}
        g, ok = pl.next_game("BOS", slates, norm)
        self.assertEqual((g["opponent"], g["home"], g["date"]), ("MIA", False, today + datetime.timedelta(days=2)))
        self.assertEqual(pl.matchup_label(g, today), "Fri, Oct 23 · @ MIA · 7:30p ET")

    def test_tomorrow_label_and_tbd_time_omitted(self):
        tomorrow = today + datetime.timedelta(days=1)
        g, _ = pl.next_game("CHI", {today: [], tomorrow: [game("Chicago Bulls", "Miami Heat", "TBD")]}, norm)
        self.assertEqual(pl.matchup_label(g, today), "Tomorrow · @ MIA")

    def test_no_game_in_window(self):
        self.assertEqual(pl.next_game("CHI", {today: [game("New York Knicks", "Boston Celtics")]}, norm), (None, True))

    def test_schedule_unavailable_before_a_game_is_found(self):
        self.assertEqual(pl.next_game("CHI", {today: [], today + datetime.timedelta(days=1): None}, norm), (None, False))

    def test_a_game_found_before_a_gap_still_wins(self):
        slates = {today: [game("Chicago Bulls", "Miami Heat")], today + datetime.timedelta(days=1): None}
        g, ok = pl.next_game("CHI", slates, norm)
        self.assertEqual((g["opponent"], ok), ("MIA", True))


if __name__ == "__main__":
    unittest.main()
