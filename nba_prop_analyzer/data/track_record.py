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
from .bbref_scraper import get_stat_from_games
from . import snapshot_store

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


def fetch_scored_history(limit: int = 200) -> list:
    """
    Every tracked prediction that's actually been graded, most recent
    first -- the real track record, for the paid tier's history page.
    Never includes a still-pending row (actual_value is null), since
    that outcome hasn't happened yet.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/tracked_predictions",
        headers=_headers(),
        params={
            "actual_value": "not.is.null",
            "select": "date,player,opponent,prop_type,line,predicted_value,lean,actual_value,hit",
            "order": "date.desc",
            "limit": str(limit),
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        return []
    return resp.json()


def summarize_history(rows: list) -> dict:
    """
    Aggregate accuracy across graded rows. A PUSH or a "NO LEAN" call
    counts toward neither hits nor misses -- it was never a directional
    claim to grade in the first place, so including it either way would
    misstate what the tool actually got right or wrong.
    """
    graded = [r for r in rows if r.get("lean") in ("OVER", "UNDER") and r.get("hit") in ("OVER", "UNDER")]
    correct = sum(1 for r in graded if r["lean"] == r["hit"])
    return {
        "total_scored": len(rows),
        "graded": len(graded),
        "correct": correct,
        "hit_rate": round(correct / len(graded) * 100, 1) if graded else None,
        "earliest_date": min((r["date"] for r in rows), default=None),
    }


def record_outcomes() -> int:
    """
    For every tracked prediction still missing an actual result, looks
    up that player's real game-log entry for the exact date the
    prediction was made and fills in what actually happened. Never
    re-runs the model -- this only ever reads a game that's already
    been played, so it can't turn into a backtest. A player with no
    matching date in the snapshot yet (game not played, or the daily
    snapshot refresh hasn't caught up) is left alone and picked up on
    a later run. Returns how many rows were scored.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return 0

    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/tracked_predictions",
        headers=_headers(),
        params={"actual_value": "is.null", "select": "date,player,prop_type,line"},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        return 0
    pending = resp.json()

    scored = 0
    for row in pending:
        games = snapshot_store.load_game_logs(row["player"])
        if not games:
            continue
        match = next((g for g in games if g.date == row["date"]), None)
        if match is None:
            continue

        actual = get_stat_from_games([match], row["prop_type"])[0]
        line = row["line"]
        hit = "OVER" if actual > line else ("UNDER" if actual < line else "PUSH")

        patch_resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/tracked_predictions",
            headers=_headers(),
            params={
                "date": f"eq.{row['date']}",
                "player": f"eq.{row['player']}",
                "prop_type": f"eq.{row['prop_type']}",
            },
            json={"actual_value": actual, "hit": hit},
            timeout=_REQUEST_TIMEOUT,
        )
        if patch_resp.status_code < 400:
            scored += 1

    return scored
