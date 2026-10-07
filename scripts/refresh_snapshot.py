#!/usr/bin/env python3
"""
Refreshes the data snapshot the production app runs on.

Render's server IP is blocked (403) by basketball-reference, so the live
service can't scrape bbref itself. Run this from an unblocked network
(e.g. this machine) — it fetches bulk player/team-defense data plus
per-player game logs and shot-zone profiles for active rotation players,
and writes it all to nba_prop_analyzer/data/snapshot/. Commit + push after
running; Render's Auto-Deploy (On Commit) redeploys automatically.

Rate-limited to 1 request per 3 seconds (bbref_scraper._rate_limit), so the
per-player part over ~300-400 rotation players takes roughly 30-40 minutes.

That slow part is separable so a daily run can publish the fast data first:
  --fast        bulk player/team stats, zone defense, team stats
                (seconds) -- enough to keep rosters, teams
                current even if a long run is interrupted.
  --logs-only   per-player game logs + shot zones for the players already in
                the snapshot (the 30-40 minute part).
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
from nba_prop_analyzer.data.databallr_client import fetch_team_stats_raw

MIN_GAMES = 5
MIN_MPG = 12.0

# A bulk fetch that comes back nearly empty (a site error page, or the first
# day of a new season before many games exist) must never replace good data.
MIN_PLAYERS_TO_ACCEPT = 150
MIN_TEAMS_TO_ACCEPT = 25


def refresh_fast() -> dict:
    print("Fetching bulk player per-game stats...")
    players = fetch_all_players_per_game()
    print(f"  {len(players)} players")
    if len(players) < MIN_PLAYERS_TO_ACCEPT:
        raise SystemExit(
            f"Only {len(players)} players returned (need {MIN_PLAYERS_TO_ACCEPT}+) -- "
            "keeping the previous snapshot untouched. Nothing to commit."
        )
    snapshot_store.save_players(players)

    print("Fetching opponent zone defense...")
    zone_defense = fetch_opponent_zone_defense()
    print(f"  {len(zone_defense)} teams")
    if len(zone_defense) >= MIN_TEAMS_TO_ACCEPT:
        snapshot_store.save_opponent_zone_defense(zone_defense)
    else:
        print("  too few teams returned, keeping the previous zone-defense snapshot")

    print("Fetching team stats (databallr) for the fallback snapshot...")
    try:
        snapshot_store.save_team_stats(fetch_team_stats_raw())
        print("  saved")
    except Exception as e:
        print(f"  Team stats fetch failed ({e}), keeping the previous snapshot")

    snapshot_store.save_meta({
        **snapshot_store.load_meta(),
        "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "player_count": len(players),
    })
    print("Fast refresh complete.")
    return {"players": players}


def refresh_logs(players=None) -> None:
    if players is None:
        players = snapshot_store.load_players()
    if not players:
        raise SystemExit("No players in the snapshot -- run --fast first.")

    rotation_players = [p for p in players if p.games_played >= MIN_GAMES and p.mpg >= MIN_MPG]
    print(f"Refreshing game logs + shot zones for {len(rotation_players)} rotation players "
          f"(games>={MIN_GAMES}, mpg>={MIN_MPG})...")

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
        refresh_logs(refresh_fast()["players"])


if __name__ == "__main__":
    main()
