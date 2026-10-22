#!/usr/bin/env python3
"""
One-time (safe to re-run) top-up of LAST season's per-player game logs and shot zones.

The saved last-season files were missing a number of real rotation players (for example Luka
Doncic, Nikola Jokic, Anthony Davis, Jaylen Brown): some because basketball-reference ids are
ambiguous (davisan01 is a retired Antonio Davis -- fixed in bbref_scraper), some simply never
got saved. Players with no logs fall back to season averages (no last-10 form, trend, head-to-head
or shot-zone detail), and a pick on them can never be graded.

This only ADDS entries that are missing, from real basketball-reference pages (verified by the
page's own player name). It never overwrites or removes anything, and it stops by itself if the
site starts refusing requests.

  python3 scripts/backfill_prev.py
Commit + push nba_prop_analyzer/data/snapshot/ afterwards.
"""
import os
import sys
import time
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nba_prop_analyzer.config import PRIOR_SEASON_YEAR
from nba_prop_analyzer.data import snapshot_store
from nba_prop_analyzer.data.bbref_scraper import fetch_game_logs, fetch_shot_zone_profile

MIN_MPG = 12.0
MIN_GAMES = 5
MAX_EMPTY_IN_A_ROW = 8   # a run of players with nothing at all means the site is refusing us: stop
SAVE_EVERY = 10


def main():
    players = snapshot_store.load_players(prev=True)
    logs_path = snapshot_store._name("game_logs.json", True)
    logs = snapshot_store._load_json(logs_path, {})
    zones = dict(snapshot_store.load_shot_zones(prev=True))

    targets = [p for p in players
               if p.games_played >= MIN_GAMES and p.mpg >= MIN_MPG and (p.name not in logs or p.name not in zones)]
    targets.sort(key=lambda p: -p.mpg)
    print(f"{len(targets)} rotation players are missing last season's logs and/or shot zones "
          f"(already saved: {len(logs)} logs, {len(zones)} zone profiles)")

    added_logs = added_zones = empty_run = 0
    for i, p in enumerate(targets, 1):
        got = []
        if p.name not in logs:
            games = fetch_game_logs(p.name, PRIOR_SEASON_YEAR)
            if games:
                logs[p.name] = [asdict(g) for g in games]
                added_logs += 1
                got.append(f"{len(games)} games")
        if p.name not in zones:
            profile = fetch_shot_zone_profile(p.name, PRIOR_SEASON_YEAR)
            if profile:
                zones[p.name] = profile
                added_zones += 1
                got.append("shot zones")
        print(f"  [{i}/{len(targets)}] {p.name:28} {', '.join(got) if got else 'nothing found'}", flush=True)

        empty_run = 0 if got else empty_run + 1
        if empty_run >= MAX_EMPTY_IN_A_ROW:
            print(f"Stopping: {MAX_EMPTY_IN_A_ROW} players in a row returned nothing. The site may be refusing "
                  "requests. What was found so far is saved.")
            break
        if i % SAVE_EVERY == 0:
            snapshot_store._save_json(logs_path, logs)
            snapshot_store.save_shot_zones(zones, prev=True)

    snapshot_store._save_json(logs_path, logs)
    snapshot_store.save_shot_zones(zones, prev=True)
    print(f"Done: added {added_logs} game logs and {added_zones} shot-zone profiles "
          f"(now {len(logs)} logs, {len(zones)} zone profiles).")


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"took {(time.time() - start) / 60:.1f} minutes")
