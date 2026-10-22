"""
Forward-looking track record: each morning the five strongest of that day's
"Biggest Edges" are locked in with the tool's real projection, then checked
against the real final stat line once the game is played. Locked picks are
never changed, swapped or removed afterwards.

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
import datetime as _dt
from datetime import datetime, timezone

from .databallr_client import find_player
from .team_mapping import normalize_team, et_date
from .bbref_scraper import get_stat_from_games
from . import snapshot_store
from .supabase_keys import service_headers

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
    return service_headers(SUPABASE_SERVICE_ROLE_KEY, **{"Content-Type": "application/json"})


def _today() -> str:
    # Rows are keyed by the Eastern game date -- the date basketball-reference
    # logs the game under, which record_outcomes() later matches against.
    return et_date().isoformat()


PICKS_PER_DAY = 5
_BLOCKING_INJURIES = {"Out"}


def choose_daily_picks(edges: list, can_grade, n: int = PICKS_PER_DAY) -> list:
    """
    The day's picks: the first `n` of the Biggest Edges ranking (already ordered strongest
    first) that can honestly be graded afterwards:
      * a clear OVER or UNDER lean (a "no lean" call is not a claim, so it is not a pick)
      * not ruled out (an Out player cannot produce a result)
      * a player the grader can find box scores for (`can_grade(name)`)
    """
    picks = []
    for edge in edges:
        if edge.get("lean_direction") not in ("up", "down"):
            continue
        if edge.get("injury_status") in _BLOCKING_INJURIES:
            continue
        if not can_grade(edge["player"]):
            continue
        picks.append(edge)
        if len(picks) >= n:
            break
    return picks


def _can_grade(player_name: str) -> bool:
    return (snapshot_store.load_game_logs(player_name, prev=True) is not None
            or snapshot_store.load_game_logs(player_name) is not None)


def _pick_row(edge: dict, rank: int, day: str) -> dict:
    return {
        "date": day,
        "player": edge["player"],
        "team": edge.get("team"),
        "opponent": edge["opponent"],
        "prop_type": edge["prop_type"],
        "line": edge["line"],
        "predicted_value": edge["predicted_value"],
        "lean": "OVER" if edge["lean_direction"] == "up" else "UNDER",
        "pick_rank": rank,
        "headline_note": edge.get("headline_note"),
    }


def snapshot_top_edges(edges: list, can_grade=_can_grade) -> dict:
    """
    Locks today's picks. Runs once a day from the daily job; a repeat run (retry, restart) finds
    today's picks already there and changes nothing, so the picks can never be re-chosen with
    hindsight. Returns {"recorded": n, "status": ...} where status is one of
    ok | locked | no_picks | not_configured | schema | error.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return {"recorded": 0, "status": "not_configured"}

    today = _today()
    check = requests.get(
        f"{SUPABASE_URL}/rest/v1/tracked_predictions",
        headers=_headers(),
        params={"date": f"eq.{today}", "pick_rank": "not.is.null", "select": "player"},
        timeout=_REQUEST_TIMEOUT,
    )
    if check.status_code >= 400:
        return {"recorded": 0, "status": "schema" if "pick_rank" in check.text else "error", "detail": check.text[:160]}
    if check.json():
        return {"recorded": 0, "status": "locked", "existing": len(check.json())}

    picks = choose_daily_picks(edges, can_grade)
    if not picks:
        return {"recorded": 0, "status": "no_picks"}

    rows = [_pick_row(edge, i, today) for i, edge in enumerate(picks, start=1)]
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/tracked_predictions",
        headers={**_headers(), "Prefer": "resolution=ignore-duplicates"},
        json=rows,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        missing_column = any(c in resp.text for c in ("pick_rank", "headline_note", "team"))
        return {"recorded": 0, "status": "schema" if missing_column else "error", "detail": resp.text[:160]}
    return {"recorded": len(rows), "status": "ok"}


def fetch_pick_rows(limit_days: int = 90) -> list:
    """Every locked pick (graded or not), newest day first, best-ranked first within a day."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/tracked_predictions",
        headers=_headers(),
        params={
            "pick_rank": "not.is.null",
            "select": "date,player,team,opponent,prop_type,line,predicted_value,lean,actual_value,hit,pick_rank,headline_note",
            "order": "date.desc,pick_rank.asc",
            "limit": str(limit_days * PICKS_PER_DAY),
        },
        timeout=_REQUEST_TIMEOUT,
    )
    return resp.json() if resp.status_code < 400 else []


def pick_status(pick: dict) -> str:
    """pending | correct | wrong | push | dnp (a push or a did-not-play was never a graded claim)."""
    if pick.get("hit") == "DNP":
        return "dnp"
    if pick.get("actual_value") is None:
        return "pending"
    if pick.get("hit") == "PUSH":
        return "push"
    return "correct" if pick.get("lean") == pick.get("hit") else "wrong"


def group_by_day(rows: list) -> list:
    """[{date, picks, correct, graded, pending}], newest day first."""
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(dict(r))
    days = []
    for date in sorted(by_date, reverse=True):
        picks = sorted(by_date[date], key=lambda r: r.get("pick_rank") or 99)
        for p in picks:
            p["status"] = pick_status(p)
        graded = [p for p in picks if p["status"] in ("correct", "wrong")]
        days.append({
            "date": date,
            "picks": picks,
            "graded": len(graded),
            "correct": sum(1 for p in graded if p["status"] == "correct"),
            "pending": sum(1 for p in picks if p["status"] == "pending"),
        })
    return days


def summarize_days(days: list) -> dict:
    """Headline numbers. Pushes and did-not-plays count toward neither side of the hit rate."""
    graded = sum(d["graded"] for d in days)
    correct = sum(d["correct"] for d in days)
    return {
        "days": len(days),
        "picks": sum(len(d["picks"]) for d in days),
        "graded": graded,
        "correct": correct,
        "pending": sum(d["pending"] for d in days),
        "hit_rate": round(correct / graded * 100, 1) if graded else None,
        "earliest_date": min((d["date"] for d in days), default=None),
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
        params={"actual_value": "is.null", "hit": "is.null", "select": "date,player,prop_type,line"},
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
            # No box score for that day. If the player has since played other games, he sat this one
            # out: mark it so it is shown as "did not play" instead of pending forever.
            if row["date"] < (et_date() - _dt.timedelta(days=1)).isoformat() and any(g.date > row["date"] for g in games):
                requests.patch(
                    f"{SUPABASE_URL}/rest/v1/tracked_predictions",
                    headers=_headers(),
                    params={"date": f"eq.{row['date']}", "player": f"eq.{row['player']}", "prop_type": f"eq.{row['prop_type']}"},
                    json={"hit": "DNP"},
                    timeout=_REQUEST_TIMEOUT,
                )
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
