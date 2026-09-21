"""
Forward-looking track record for the paid tier: a fixed list of notable
players, snapshotted daily with today's real projection, then checked
against the real final stat line once the game is played.

Deliberately NOT a retroactive backtest -- matchup.py/pace.py/volume.py's
factors are built from season-to-date aggregates, so re-running today's
model against a game from two months ago would silently use full-season
hindsight that didn't exist at the time, which would misrepresent what
the tool actually would have said back then. This only ever records a
prediction the same day it's made, so what gets shown later is genuinely
what the tool said before the outcome was known.

Storage mirrors auth.py/usage.py's Supabase REST pattern -- Render's
free-tier disk/process doesn't survive a restart, so this can't live
in-memory or on disk.
"""
from __future__ import annotations

import os
import requests
from datetime import datetime, timezone

from .databallr_client import find_player
from .team_mapping import normalize_team

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_REQUEST_TIMEOUT = 15

# A fixed, recognizable list -- not "most wagered" (unobtainable: no public
# sportsbook API exposes real bet volume, only lines, and DraftKings/
# Underdog don't publish a public API at all), just a stable, well-known
# roster to track consistently day over day.
TRACKED_PLAYERS = [
    "LeBron James", "Stephen Curry", "Luka Doncic", "Nikola Jokic",
    "Giannis Antetokounmpo", "Shai Gilgeous-Alexander", "Jayson Tatum", "Anthony Edwards",
    "Kevin Durant", "Damian Lillard", "Devin Booker", "Ja Morant",
    "Joel Embiid", "Kawhi Leonard", "Jimmy Butler", "Donovan Mitchell",
    "Trae Young", "Zion Williamson", "Anthony Davis", "Tyrese Haliburton",
]


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def snapshot_todays_predictions(analyzer, games_today: list) -> int:
    """
    For each tracked player with a game today, runs the real points
    analysis (using their season average as the line -- the same
    real-data-only convention already used for Quick Looks/Popular
    Bets, never a fabricated number) and upserts one row per player
    per day. Returns how many rows were written.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return 0

    opponent_by_team = {}
    for g in games_today:
        away_abbr = normalize_team(g["away_team"])
        home_abbr = normalize_team(g["home_team"])
        if away_abbr and home_abbr:
            opponent_by_team[away_abbr] = home_abbr
            opponent_by_team[home_abbr] = away_abbr

    today = _today()
    rows = []
    for name in TRACKED_PLAYERS:
        player = find_player(name, analyzer.players)
        if not player or player.ppg <= 0:
            continue
        opponent = opponent_by_team.get(normalize_team(player.team_abbr))
        if not opponent:
            continue  # not playing today -- nothing to track

        line = round(player.ppg * 2) / 2  # nearest 0.5, matches Quick Looks' convention
        try:
            pred = analyzer.analyze_prop(
                player_name=player.name,
                opponent_abbr=opponent,
                prop_type="points",
                prop_line=line,
                trend_override=1.0,
            )
        except Exception:
            continue  # a lookup gap for one player shouldn't break the rest

        rows.append({
            "date": today,
            "player": player.name,
            "opponent": opponent,
            "prop_type": "points",
            "line": line,
            "predicted_value": round(pred.predicted_value, 1),
            "lean": "OVER" if pred.over_probability > 0.55 else ("UNDER" if pred.under_probability > 0.55 else "NO LEAN"),
        })

    if not rows:
        return 0

    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/tracked_predictions",
        headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
        json=rows,
        timeout=_REQUEST_TIMEOUT,
    )
    return len(rows) if resp.status_code < 400 else 0
