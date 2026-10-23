"""The default Biggest Edges view must represent every prop and never repeat a player."""
from __future__ import annotations

import unittest

from nba_prop_analyzer.analysis.edge_picks import balanced_edges


def c(player, prop, score):
    return {"player": player, "prop_type": prop, "score": score}


BY_PROP = {
    "points": [c("A", "points", .9), c("B", "points", .8), c("C", "points", .7), c("D", "points", .6)],
    "3pm": [c("A", "3pm", .9), c("E", "3pm", .5)],
    "rebounds": [c("F", "rebounds", .3), c("B", "rebounds", .2)],
    "assists": [c("G", "assists", .2)],
}


class BalancedEdgesTests(unittest.TestCase):
    def test_every_prop_gets_a_place_even_when_points_are_louder(self):
        picks = balanced_edges(BY_PROP, limit=4)
        self.assertEqual([p["prop_type"] for p in picks], ["points", "3pm", "rebounds", "assists"])

    def test_a_player_never_takes_two_slots(self):
        picks = balanced_edges(BY_PROP, limit=8)
        names = [p["player"] for p in picks]
        self.assertEqual(len(names), len(set(names)))
        # A is points #1, so A's 3pm entry is skipped and E (the next 3pm) is used instead
        self.assertEqual(names[:4], ["A", "E", "F", "G"])

    def test_second_round_continues_in_the_same_prop_order(self):
        names = [p["player"] for p in balanced_edges(BY_PROP, limit=8)]
        self.assertEqual(names, ["A", "E", "F", "G", "B", "C", "D"])   # rebounds' B is already taken by points

    def test_limit_is_respected_and_a_short_supply_is_fine(self):
        self.assertEqual(len(balanced_edges(BY_PROP, limit=3)), 3)
        self.assertEqual(balanced_edges({}, limit=5), [])
        self.assertEqual([p["player"] for p in balanced_edges({"points": [c("A", "points", 1)]}, limit=5)], ["A"])

    def test_the_inputs_are_not_modified(self):
        before = {k: list(v) for k, v in BY_PROP.items()}
        balanced_edges(BY_PROP, limit=8)
        self.assertEqual(BY_PROP, before)


if __name__ == "__main__":
    unittest.main()
