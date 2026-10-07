#!/usr/bin/env python3
"""
One-time season rollover: turns the data snapshot that currently holds the
season that just ended into the "prior season" slot (<name>_prev.json) and
leaves the current-season files empty for scripts/refresh_snapshot.py to fill
as games are played. The app blends the two (nba_prop_analyzer/data/season_blend.py).

Also captures TeamRankings' numbers as the prior. TeamRankings only publishes a
live "current season" column, so this must run BEFORE it flips to the new season
(its header reads the season's start year: "2025" for 2025-26). It refuses to run
once that label no longer matches.

Safe to re-run: a prior file that already exists is never overwritten.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nba_prop_analyzer.config import PRIOR_SEASON_YEAR
from nba_prop_analyzer.data import snapshot_store
from nba_prop_analyzer.data.teamrankings_scraper import fetch_teamrankings_opponent_stats

# file name -> what the (new, empty) current-season file starts as
FILES = {
    "players.json": [],
    "game_logs.json": {},
    "shot_zones.json": {},
    "team_stats.json": {},
    "opponent_zone_defense.json": {},
}


def main():
    prior_label = str(PRIOR_SEASON_YEAR - 1)
    stats, label = fetch_teamrankings_opponent_stats()
    if label != prior_label or len(stats) < 25:
        raise SystemExit(
            f"TeamRankings shows season column {label!r} with {len(stats)} teams, expected "
            f"{prior_label!r} with 30 -- it has already moved on to the new season, so its "
            "prior-season numbers can't be captured. Stopping before changing anything."
        )

    for name, empty in FILES.items():
        current = snapshot_store._path(name)
        prior = snapshot_store._path(snapshot_store._name(name, True))
        if os.path.exists(prior):
            print(f"  {name}: prior copy already exists, leaving it alone")
            continue
        if not os.path.exists(current):
            print(f"  {name}: nothing to roll")
            continue
        os.rename(current, prior)
        snapshot_store._save_json(name, empty)
        print(f"  {name} -> {snapshot_store._name(name, True)} (current season reset to empty)")

    if os.path.exists(snapshot_store._path("teamrankings_prev.json")):
        print("  teamrankings_prev.json already exists, leaving it alone")
    else:
        snapshot_store.save_teamrankings(stats, prev=True)
        print(f"  teamrankings_prev.json saved ({len(stats)} teams, season column {label!r})")
    print("Done. Commit nba_prop_analyzer/data/snapshot/ together with the code that reads it.")


if __name__ == "__main__":
    main()
