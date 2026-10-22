"""Daily top-5 picks: choosing, locking, grading, 'did not play', and the page's grouping."""
from __future__ import annotations

import datetime
import unittest
from types import SimpleNamespace as P
from unittest import mock

from nba_prop_analyzer.data import track_record as tr


def edge(player, lean="up", injury=None, prop="points", score=1.0):
    return {"player": player, "team": "BOS", "opponent": "NYK", "prop_type": prop, "line": 25.5,
            "predicted_value": 28.1, "lean_direction": lean, "injury_status": injury,
            "headline_note": "Strong zone exploitation", "score": score}


EDGES = [edge("A"), edge("B", lean="neutral"), edge("C", injury="Out"), edge("D", lean="down"), edge("E"),
         edge("F", injury="Day-To-Day"), edge("G"), edge("H")]
gradable = lambda name: name != "E"          # E has no box scores to grade against


class ChooseTests(unittest.TestCase):
    def test_takes_the_first_five_that_can_be_graded_in_ranking_order(self):
        picks = tr.choose_daily_picks(EDGES, gradable)
        # B no lean, C is out, E cannot be graded -> A, D, F, G, H
        self.assertEqual([p["player"] for p in picks], ["A", "D", "F", "G", "H"])

    def test_day_to_day_players_stay_in_but_out_players_do_not(self):
        names = [p["player"] for p in tr.choose_daily_picks(EDGES, gradable)]
        self.assertIn("F", names); self.assertNotIn("C", names)

    def test_fewer_than_five_candidates_gives_fewer_picks(self):
        self.assertEqual(len(tr.choose_daily_picks([edge("A"), edge("B", lean="neutral")], gradable)), 1)

    def test_row_carries_lean_rank_and_the_reason(self):
        row = tr._pick_row(edge("D", lean="down"), 2, "2026-10-22")
        self.assertEqual((row["lean"], row["pick_rank"], row["headline_note"], row["date"]), ("UNDER", 2, "Strong zone exploitation", "2026-10-22"))


class FakeTable:
    """The slice of PostgREST that tracked_predictions uses (filters eq / is.null / not.is.null)."""
    def __init__(self):
        self.rows = []
        self.reject_with = None

    @staticmethod
    def _match(row, params):
        for col, cond in params.items():
            if col in ("select", "order", "limit"):
                continue
            v = row.get(col)
            if cond == "is.null" and v is not None: return False
            if cond == "not.is.null" and v is None: return False
            if cond.startswith("eq.") and str(v) != cond[3:]: return False
        return True

    def get(self, url, headers=None, params=None, timeout=None):
        if self.reject_with:
            return mock.Mock(status_code=400, text=self.reject_with, json=lambda: {})
        found = [r for r in self.rows if self._match(r, params)]
        return mock.Mock(status_code=200, json=lambda: found)

    def post(self, url, headers=None, json=None, timeout=None):
        if self.reject_with:
            return mock.Mock(status_code=400, text=self.reject_with)
        for new in json:
            key = (new["date"], new["player"], new["prop_type"])
            if not any((r["date"], r["player"], r["prop_type"]) == key for r in self.rows):
                self.rows.append(dict(new))
        return mock.Mock(status_code=201, text="")

    def patch(self, url, headers=None, params=None, json=None, timeout=None):
        for r in self.rows:
            if self._match(r, params):
                r.update(json)
        return mock.Mock(status_code=204, text="")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeTable()
        for x in (mock.patch.object(tr, "SUPABASE_URL", "https://x.test"), mock.patch.object(tr, "SUPABASE_SERVICE_ROLE_KEY", "k"),
                  mock.patch.object(tr.requests, "get", self.db.get), mock.patch.object(tr.requests, "post", self.db.post),
                  mock.patch.object(tr.requests, "patch", self.db.patch),
                  mock.patch.object(tr, "_today", lambda: "2026-10-22")):
            x.start(); self.addCleanup(x.stop)

    def test_first_run_locks_five_picks(self):
        out = tr.snapshot_top_edges(EDGES, gradable)
        self.assertEqual((out["recorded"], out["status"]), (5, "ok"))
        self.assertEqual([r["pick_rank"] for r in self.db.rows], [1, 2, 3, 4, 5])

    def test_a_second_run_the_same_day_changes_nothing_even_if_the_ranking_changed(self):
        tr.snapshot_top_edges(EDGES, gradable)
        before = [dict(r) for r in self.db.rows]
        reshuffled = list(reversed(EDGES))
        out = tr.snapshot_top_edges(reshuffled, gradable)
        self.assertEqual((out["recorded"], out["status"]), (0, "locked"))
        self.assertEqual(self.db.rows, before)

    def test_no_eligible_edges_records_nothing(self):
        out = tr.snapshot_top_edges([edge("B", lean="neutral")], gradable)
        self.assertEqual((out["recorded"], out["status"]), (0, "no_picks"))

    def test_missing_database_columns_are_reported_loudly_not_swallowed(self):
        self.db.reject_with = '{"message":"column tracked_predictions.pick_rank does not exist"}'
        self.assertEqual(tr.snapshot_top_edges(EDGES, gradable)["status"], "schema")

    def test_not_configured(self):
        with mock.patch.object(tr, "SUPABASE_URL", ""):
            self.assertEqual(tr.snapshot_top_edges(EDGES, gradable)["status"], "not_configured")


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeTable()
        today = datetime.date(2026, 10, 25)
        self.logs = {}
        for x in (mock.patch.object(tr, "SUPABASE_URL", "https://x.test"), mock.patch.object(tr, "SUPABASE_SERVICE_ROLE_KEY", "k"),
                  mock.patch.object(tr.requests, "get", self.db.get), mock.patch.object(tr.requests, "patch", self.db.patch),
                  mock.patch.object(tr, "et_date", lambda: today),
                  mock.patch.object(tr.snapshot_store, "load_game_logs", lambda name, prev=False: self.logs.get(name))):
            x.start(); self.addCleanup(x.stop)

    def row(self, player, date="2026-10-22"):
        r = {"date": date, "player": player, "prop_type": "points", "line": 25.5, "lean": "OVER", "actual_value": None, "hit": None}
        self.db.rows.append(r); return r

    def test_played_gets_scored_and_a_player_who_sat_out_is_marked_dnp(self):
        played, sat, early = self.row("Played"), self.row("Sat"), self.row("TooEarly", date="2026-10-24")
        game = lambda d, pts: P(date=d, pts=pts, reb=0, ast=0, fg3=0, fg3a=0, orb=0, drb=0, fga=0, fgm=0, fta=0, ftm=0, tov=0)
        self.logs = {"Played": [game("2026-10-22", 30)],
                     "Sat": [game("2026-10-20", 20), game("2026-10-24", 22)],      # played later, not on the 22nd
                     "TooEarly": [game("2026-10-20", 20), game("2026-10-25", 22)]}  # only yesterday's pick: too soon to call
        with mock.patch.object(tr, "get_stat_from_games", lambda games, prop: [games[0].pts]):
            scored = tr.record_outcomes()
        self.assertEqual(scored, 1)
        self.assertEqual((played["actual_value"], played["hit"]), (30, "OVER"))
        self.assertEqual((sat["actual_value"], sat["hit"]), (None, "DNP"))
        self.assertEqual((early["actual_value"], early["hit"]), (None, None))

    def test_a_marked_dnp_is_not_looked_at_again(self):
        r = self.row("Sat"); r["hit"] = "DNP"
        self.logs = {"Sat": []}
        self.assertEqual(tr.record_outcomes(), 0)


class PageGroupingTests(unittest.TestCase):
    ROWS = [
        {"date": "2026-10-22", "player": "B", "pick_rank": 2, "lean": "UNDER", "actual_value": 31, "hit": "OVER"},
        {"date": "2026-10-22", "player": "A", "pick_rank": 1, "lean": "OVER", "actual_value": 30, "hit": "OVER"},
        {"date": "2026-10-22", "player": "C", "pick_rank": 3, "lean": "OVER", "actual_value": None, "hit": None},
        {"date": "2026-10-22", "player": "D", "pick_rank": 4, "lean": "OVER", "actual_value": None, "hit": "DNP"},
        {"date": "2026-10-22", "player": "E", "pick_rank": 5, "lean": "OVER", "actual_value": 25.5, "hit": "PUSH"},
        {"date": "2026-10-21", "player": "F", "pick_rank": 1, "lean": "OVER", "actual_value": 12, "hit": "UNDER"},
    ]

    def test_days_newest_first_picks_in_rank_order_with_statuses(self):
        days = tr.group_by_day(self.ROWS)
        self.assertEqual([d["date"] for d in days], ["2026-10-22", "2026-10-21"])
        self.assertEqual([p["player"] for p in days[0]["picks"]], ["A", "B", "C", "D", "E"])
        self.assertEqual([p["status"] for p in days[0]["picks"]], ["correct", "wrong", "pending", "dnp", "push"])
        self.assertEqual((days[0]["correct"], days[0]["graded"], days[0]["pending"]), (1, 2, 1))

    def test_hit_rate_ignores_pending_pushes_and_did_not_plays(self):
        s = tr.summarize_days(tr.group_by_day(self.ROWS))
        self.assertEqual((s["days"], s["picks"], s["graded"], s["correct"], s["hit_rate"]), (2, 6, 3, 1, 33.3))
        self.assertEqual(s["earliest_date"], "2026-10-21")

    def test_empty(self):
        s = tr.summarize_days([])
        self.assertEqual((s["days"], s["hit_rate"], s["earliest_date"]), (0, None, None))


if __name__ == "__main__":
    unittest.main()
