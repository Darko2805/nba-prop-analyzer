"""basketball-reference ids are guesses; the page's own name must be checked (davisan01 is NOT Anthony Davis)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace as R
from unittest import mock

from nba_prop_analyzer.data import bbref_scraper as b

ROW = ('<tr><td data-stat="date">2026-03-01</td><td data-stat="opp_name_abbr">BOS</td><td data-stat="game_result">W</td>'
       '<td data-stat="mp">30:00</td><td data-stat="pts">25</td><td data-stat="trb">10</td><td data-stat="ast">3</td>'
       '<td data-stat="fg3">1</td><td data-stat="fg3a">3</td><td data-stat="orb">2</td><td data-stat="drb">8</td>'
       '<td data-stat="fga">18</td><td data-stat="fg">9</td><td data-stat="fta">8</td><td data-stat="ft">6</td><td data-stat="tov">2</td></tr>')


def gamelog_page(name):
    return R(status_code=200, text=f"<html><h1><span>{name}</span> 2025-26 Game Log</h1>"
                                   f'<table id="player_game_log_reg"><tbody>{ROW}</tbody></table></html>')


def main_page(name):
    return R(status_code=200, text=f"<html><h1><span>{name}</span></h1></html>")


NOT_FOUND = R(status_code=404, text="<h1>Page Not Found (404 error)</h1>")
GENERIC = R(status_code=200, text="<h1>List of all the NBA and ABA Players</h1>")


class IdResolutionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.tmp = tempfile.mkdtemp()
        p = [mock.patch.object(b, "_IDS_PATH", os.path.join(self.tmp, "player_ids.json")),
             mock.patch.object(b, "_ids_cache", None),
             mock.patch.object(b, "_http_get", self.fake_get)]
        for x in p:
            x.start(); self.addCleanup(x.stop)
        b.cache._store.clear()

    pages = {}

    def fake_get(self, url):
        self.calls.append(url.rsplit("/players/", 1)[1])
        return self.pages.get(url.rsplit("/players/", 1)[1], NOT_FOUND)

    def test_a_correct_standard_id_costs_one_request(self):
        self.pages = {"d/doncilu01/gamelog/2026": gamelog_page("Luka Dončić")}
        self.assertEqual(len(b.fetch_game_logs("Luka Doncic", 2026)), 1)
        self.assertEqual(self.calls, ["d/doncilu01/gamelog/2026"])

    def test_a_namesake_on_the_guessed_id_is_detected_and_the_right_id_is_found(self):
        self.pages = {
            "d/davisan01/gamelog/2026": R(status_code=200, text="<h1>Antonio Davis 2025-26 Game Log</h1>"),  # wrong person, no table
            "d/davisan01.html": main_page("Antonio Davis"),
            "d/davisan02.html": main_page("Anthony Davis"),
            "d/davisan02/gamelog/2026": gamelog_page("Anthony Davis"),
        }
        games = b.fetch_game_logs("Anthony Davis", 2026)
        self.assertEqual([g.pts for g in games], [25])
        self.assertEqual(json.load(open(b._IDS_PATH)), {"Anthony Davis": "davisan02"})

    def test_a_remembered_id_is_used_directly_next_time(self):
        json.dump({"Anthony Davis": "davisan02"}, open(b._IDS_PATH, "w"))
        self.pages = {"d/davisan02/gamelog/2026": gamelog_page("Anthony Davis")}
        b.fetch_game_logs("Anthony Davis", 2026)
        self.assertEqual(self.calls, ["d/davisan02/gamelog/2026"])

    def test_a_player_with_no_page_gets_nothing_not_a_strangers_games(self):
        self.pages = {"c/carribu01/gamelog/2026": GENERIC}   # a nickname: bbref answers with its generic list page
        self.assertEqual(b.fetch_game_logs("Bub Carrington", 2026), [])
        self.assertEqual(self.calls, ["c/carribu01/gamelog/2026", "c/carribu01.html"])  # stops at the first 404

    def test_when_blocked_it_does_not_start_guessing(self):
        self.pages = {"d/davisan01/gamelog/2026": R(status_code=403, text="blocked")}
        self.assertEqual(b.fetch_game_logs("Anthony Davis", 2026), [])
        self.assertEqual(len(self.calls), 1)

    def test_a_real_player_who_did_not_play_that_season_keeps_the_standard_id(self):
        empty = R(status_code=200, text='<h1>Damian Lillard 2025-26 Game Log</h1><table id="player_game_log_reg"><tbody></tbody></table>')
        self.pages = {"l/lillada01/gamelog/2026": empty}
        self.assertEqual(b.fetch_game_logs("Damian Lillard", 2026), [])
        self.assertEqual(self.calls, ["l/lillada01/gamelog/2026"])
        self.assertFalse(os.path.exists(b._IDS_PATH))

    def test_name_matching_ignores_accents_suffixes_and_punctuation(self):
        for page, name in [("Luka Dončić 2025-26 Game Log", "Luka Doncic"), ("Jaren Jackson Jr. 2025-26 Game Log", "Jaren Jackson Jr."),
                           ("De'Aaron Fox 2025-26 Game Log", "De'Aaron Fox")]:
            self.assertTrue(b._page_is_for(f"<h1>{page}</h1>", name), page)
        self.assertFalse(b._page_is_for("<h1>Antonio Davis 2025-26 Game Log</h1>", "Anthony Davis"))


if __name__ == "__main__":
    unittest.main()
