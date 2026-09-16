#!/usr/bin/env python3
"""
Refreshes the data snapshot the production app runs on.

Render's server IP is blocked (403) by basketball-reference, so the live
service can't scrape bbref itself. Run this from an unblocked network
(e.g. this machine) — it fetches bulk player/team-defense data plus
per-player game logs and shot-zone profiles for active rotation players,
and writes it all to nba_prop_analyzer/data/snapshot/. Commit + push after
running; Render's Auto-Deploy (On Commit) redeploys automatically.

Rate-limited to 1 request per 3 seconds (bbref_scraper._rate_limit), so a
full run over ~300-400 rotation players takes roughly 30-40 minutes.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nba_prop_analyzer.data.bbref_league_stats import (
    fetch_all_players_per_game, fetch_opponent_zone_defense, fetch_todays_games,
)
from nba_prop_analyzer.data.bbref_scraper import fetch_game_logs, fetch_shot_zone_profile
from nba_prop_analyzer.data import snapshot_store

MIN_GAMES = 5
MIN_MPG = 12.0


def main():
    print("Fetching bulk player per-game stats...")
    players = fetch_all_players_per_game()
    print(f"  {len(players)} players")
    snapshot_store.save_players(players)

    print("Fetching opponent zone defense...")
    zone_defense = fetch_opponent_zone_defense()
    print(f"  {len(zone_defense)} teams")
    snapshot_store.save_opponent_zone_defense(zone_defense)

    print("Fetching today's game schedule...")
    try:
        games_today = fetch_todays_games()
        print(f"  {len(games_today)} games today")
    except Exception as e:
        print(f"  Today's games fetch failed ({e}), continuing with none")
        games_today = []
    snapshot_store.save_games_today(games_today)

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
        "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "player_count": len(players),
        "rotation_player_count": len(rotation_players),
        "game_logs_count": len(game_logs),
        "shot_zones_count": len(shot_zones),
        "games_today_count": len(games_today),
        "fetch_failures": failures,
    })

    print(f"Snapshot refresh complete. {len(game_logs)} players with game logs, "
          f"{len(shot_zones)} with shot zones, {failures} fetch failures.")


if __name__ == "__main__":
    main()
