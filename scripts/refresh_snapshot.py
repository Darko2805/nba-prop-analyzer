#!/usr/bin/env python3
"""
Refreshes the CURRENT-season data snapshot the production app runs on.

The host's IP is blocked (403) by basketball-reference, so the live service
can't scrape bbref itself. Run this from an unblocked network (e.g. this
machine) -- it fetches bulk player/team data plus per-player game logs and
shot-zone profiles, and writes it to nba_prop_analyzer/data/snapshot/.
Commit + push after running; Railway auto-deploys the push.

Last season's data is NOT touched here: it lives beside these files as
<name>_prev.json (written once by scripts/roll_season.py) and the app blends
the two by games played (see nba_prop_analyzer/data/season_blend.py). Before
the new season has a game, every fetch below legitimately returns nothing and
this script changes nothing.

Rate-limited to 1 request per 3 seconds (bbref_scraper._rate_limit), so the
per-player part takes about 6 seconds per player -- up to 30-40 minutes once
~400 players have played.

That slow part is separable so a daily run can publish the fast data first:
  --fast        bulk player stats, zone defense, team stats (seconds).
  --logs-only   per-player game logs + shot zones for players who have played
                this season (the slow part).
  (no flag)     both, in that order.
Each mode writes only the files it owns, so commit + push after each.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nba_prop_analyzer.data.bbref_league_stats import (
    fetch_all_players_per_game, fetch_opponent_zone_defense,
)
from nba_prop_analyzer.data.bbref_scraper import fetch_game_logs, fetch_shot_zone_profile
from nba_prop_analyzer.data import snapshot_store
from nba_prop_analyzer.data.databallr_client import fetch_team_stats_raw, NoSeasonData

MIN_GAMES = 1
MIN_MPG = 12.0
PRIOR_ROTATION_MIN_GAMES = 5

# Data within a season only grows. A fetch that comes back much smaller than
# what is already saved is a site error page, not news -- keep what we have.
SHRINK_TOLERANCE = 0.8


def _looks_truncated(new_count: int, existing_count: int) -> bool:
    return existing_count > 0 and new_count < existing_count * SHRINK_TOLERANCE


def refresh_fast() -> list:
    print("Fetching bulk player per-game stats (current season)...")
    players = fetch_all_players_per_game()
    print(f"  {len(players)} players have played this season")
    existing = snapshot_store.load_players()
    if _looks_truncated(len(players), len(existing)):
        raise SystemExit(
            f"Only {len(players)} players returned but {len(existing)} are already saved -- "
            "keeping the previous snapshot untouched. Nothing to commit."
        )
    snapshot_store.save_players(players)

    print("Fetching opponent zone defense...")
    zone_defense = fetch_opponent_zone_defense()
    print(f"  {len(zone_defense)} teams")
    if _looks_truncated(len(zone_defense), len(snapshot_store.load_opponent_zone_defense())):
        print("  far fewer teams than already saved, keeping the previous zone-defense snapshot")
    else:
        snapshot_store.save_opponent_zone_defense(zone_defense)

    print("Fetching team stats (databallr)...")
    try:
        snapshot_store.save_team_stats(fetch_team_stats_raw(min_teams=1))
        print("  saved")
    except NoSeasonData:
        print("  databallr has no data for the new season yet")
    except Exception as e:
        print(f"  Team stats fetch failed ({e}), keeping the previous snapshot")

    snapshot_store.save_meta({
        **snapshot_store.load_meta(),
        "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "player_count": len(players),
    })
    print("Fast refresh complete.")
    return players


def refresh_logs(players=None) -> None:
    if players is None:
        players = snapshot_store.load_players()
    if not players:
        print("No new-season games yet -- nothing to fetch. (Last season's logs are untouched.)")
        return

    prior_rotation = {
        p.name for p in snapshot_store.load_players(prev=True)
        if p.games_played >= PRIOR_ROTATION_MIN_GAMES and p.mpg >= MIN_MPG
    }
    rotation_players = [
        p for p in players
        if p.games_played >= MIN_GAMES and (p.mpg >= MIN_MPG or p.name in prior_rotation)
    ]
    print(f"Refreshing game logs + shot zones for {len(rotation_players)} players who have played "
          f"(games>={MIN_GAMES}, mpg>={MIN_MPG} or a rotation player last season)...")

    game_logs = {}
    shot_zones = {}
    failures = 0
    for i, p in enumerate(rotation_players, 1):
        print(f"  [{i}/{len(rotation_players)}] {p.name}")
        try:
            logs = fetch_game_logs(p.name)
            if logs:
                game_logs[p.name] = logs
        except Exception as e:
            print(f"    game log fetch failed: {e}")
            failures += 1
        try:
            zone = fetch_shot_zone_profile(p.name)
            if zone:
                shot_zones[p.name] = zone
        except Exception as e:
            print(f"    shot zone fetch failed: {e}")
            failures += 1

    snapshot_store.save_game_logs(game_logs)
    snapshot_store.save_shot_zones(shot_zones)

    snapshot_store.save_meta({
        **snapshot_store.load_meta(),
        "logs_refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "rotation_player_count": len(rotation_players),
        "game_logs_count": len(game_logs),
        "shot_zones_count": len(shot_zones),
        "fetch_failures": failures,
    })

    print(f"Snapshot refresh complete. {len(game_logs)} players with game logs, "
          f"{len(shot_zones)} with shot zones, {failures} fetch failures.")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true", help="bulk stats only")
    mode.add_argument("--logs-only", action="store_true", help="per-player game logs + shot zones only")
    args = parser.parse_args()

    if args.logs_only:
        refresh_logs()
    elif args.fast:
        refresh_fast()
    else:
        refresh_logs(refresh_fast())


if __name__ == "__main__":
    main()
