#!/usr/bin/env python3
"""
Walk-forward backtest of the projection model against real box scores.

For every game date this season it rebuilds the data exactly as it stood that morning (the daily
snapshot committed to git before the games, with game logs cut off at the day before), runs the
CURRENT model code on it, and writes one row per player-game-prop with the baseline, every factor,
the final projection and what the player actually did. Nothing from the day itself or later reaches
the projection, so the rows are honest out-of-sample results.

  python3 scripts/backtest.py out.json            # all dates
  python3 scripts/backtest.py out.json 2026-11-01 # from a date on

Offline: no site is contacted. TeamRankings' live opponent table is not archived, so it is rebuilt
from the same morning's databallr opponent totals (the same quantities: points, threes, rebounds,
assists, free throws and eFG% allowed per game).
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from nba_prop_analyzer.config import CURRENT_SEASON_YEAR  # noqa: E402
from nba_prop_analyzer.cache import cache  # noqa: E402
from nba_prop_analyzer.data import snapshot_store, databallr_client  # noqa: E402
from nba_prop_analyzer.data.team_mapping import normalize_team  # noqa: E402
from nba_prop_analyzer.analysis import prop_analyzer as pa  # noqa: E402

SNAP_REL = "nba_prop_analyzer/data/snapshot"
SNAP_DIR = os.path.join(ROOT, SNAP_REL)
CURRENT_FILES = ("players.json", "game_logs.json", "shot_zones.json", "team_stats.json", "opponent_zone_defense.json")
PROPS = ("points", "rebounds", "assists", "3pm")
BASE_ATTR = {"points": "ppg", "rebounds": "rpg", "assists": "apg", "3pm": "three_pm_pg"}
STAT_FIELD = {"points": "pts", "rebounds": "reb", "assists": "ast", "3pm": "fg3"}
FACTORS = ("matchup", "pace", "shot_zone", "volume", "free_throw", "teammate_efficiency", "rest_fatigue", "trend")
PREGAME_CUTOFF_UTC_HOUR = 16  # every snapshot committed before this hour on game day predates tip-off


def _git(*args) -> str:
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, check=True).stdout


def snapshot_commits() -> list:
    """[(commit time UTC, sha)] oldest first, for every commit that touched the snapshot."""
    out = _git("log", "--format=%H %ct", "--", SNAP_REL)
    rows = [(datetime.datetime.utcfromtimestamp(int(ts)), sha) for sha, ts in (l.split() for l in out.splitlines())]
    return sorted(rows)


def commit_for(day: str, commits: list):
    cutoff = datetime.datetime.strptime(day, "%Y-%m-%d") + datetime.timedelta(hours=PREGAME_CUTOFF_UTC_HOUR)
    before = [sha for ts, sha in commits if ts < cutoff]
    return before[-1] if before else None


def materialize(sha: str, day: str, dest: str) -> None:
    """The snapshot directory as production had it on the morning of `day`."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(SNAP_DIR):
        if name.endswith("_prev.json"):  # last season: complete and static
            os.symlink(os.path.join(SNAP_DIR, name), os.path.join(dest, name))
    for name in CURRENT_FILES:
        res = subprocess.run(["git", "-C", ROOT, "show", f"{sha}:{SNAP_REL}/{name}"], capture_output=True, text=True)
        if res.returncode != 0:
            continue  # the file did not exist yet (before the opener)
        data = json.loads(res.stdout)
        if name == "game_logs.json":  # belt and braces: nothing from the day itself or later
            data = {p: [g for g in games if g.get("date", "") < day] for p, games in data.items()}
        with open(os.path.join(dest, name), "w") as f:
            json.dump(data, f)


def _teamrankings_from_databallr():
    """Stand-in for TeamRankings' live opponent table, from the snapshot's databallr opponent totals."""
    raw = snapshot_store.load_team_stats()
    rows = (raw or {}).get("opponent", {}).get("team_data", [])
    out = {}
    for o in rows:
        abbr = normalize_team(o.get("TeamAbbreviation", o.get("Name", "")))
        gp = o.get("GamesPlayed") or 0
        if not abbr or not gp:
            continue
        fg3a, fg3m = o.get("FG3A") or 0, o.get("FG3M") or 0
        out[abbr] = {
            "opp_ppg": (o.get("Points") or 0) / gp, "opp_3pm": fg3m / gp, "opp_3pa": fg3a / gp,
            "opp_3p_pct": fg3m / fg3a if fg3a else 0, "opp_rpg": (o.get("Rebounds") or 0) / gp,
            "opp_apg": (o.get("Assists") or 0) / gp, "opp_fta": (o.get("FTA") or 0) / gp,
            "opp_ftm": (o.get("FtPoints") or 0) / gp, "opp_efg_pct": o.get("EfgPct") or 0,
        }
    if not out:
        raise RuntimeError("no current-season team data in this snapshot")
    return out, str(CURRENT_SEASON_YEAR - 1)


def _go_offline() -> None:
    def no_network(*a, **k):
        raise RuntimeError("backtest is offline")
    databallr_client.fetch_team_stats_raw = no_network
    pa.fetch_teamrankings_opponent_stats = _teamrankings_from_databallr
    pa.fetch_back_to_back_teams = lambda *a, **k: set()
    pa.fetch_three_in_four_teams = lambda *a, **k: set()
    pa.LIVE_BBREF_FALLBACK = False
    pa.PropAnalyzer.refresh_player_teams = lambda self, *a, **k: None


def _vals(games: list) -> list:
    return [[g.get("minutes") or 0, g.get("pts") or 0, g.get("reb") or 0, g.get("ast") or 0, g.get("fg3") or 0,
             g.get("fga") or 0, g.get("fg3a") or 0, g.get("fta") or 0, g.get("date", ""), g.get("opponent", "")]
            for g in games]


def main():
    out_path = sys.argv[1]
    start = sys.argv[2] if len(sys.argv) > 2 else ""
    _go_offline()

    final_logs = json.load(open(os.path.join(SNAP_DIR, "game_logs.json")))
    final_team = {p["name"]: normalize_team(p["team_abbr"]) for p in json.load(open(os.path.join(SNAP_DIR, "players.json")))}
    prev_logs = json.load(open(os.path.join(SNAP_DIR, "game_logs_prev.json")))
    prev_players = {p["name"]: p for p in json.load(open(os.path.join(SNAP_DIR, "players_prev.json")))}
    by_day = {}
    for name, games in final_logs.items():
        for g in games:
            by_day.setdefault(g["date"], []).append((name, g))

    commits = snapshot_commits()
    work = tempfile.mkdtemp(prefix="backtest_")
    rows, skipped = [], {}
    try:
        for day in sorted(d for d in by_day if d >= start):
            sha = commit_for(day, commits)
            if not sha:
                continue
            dest = os.path.join(work, "snap")
            materialize(sha, day, dest)
            snapshot_store._SNAPSHOT_DIR = dest
            snapshot_store._shot_zones_cache.clear()
            snapshot_store._game_logs_cache.clear()
            cache._store.clear()
            analyzer = pa.PropAnalyzer()
            with contextlib.redirect_stdout(io.StringIO()):
                analyzer.load_data()
            for p in analyzer.players:  # the team a player was actually on (a traded player's old team is wrong)
                p.team_abbr = final_team.get(p.name) or p.team_abbr
            by_name = {p.name: p for p in analyzer.players}
            cur_rows = {p["name"]: p for p in snapshot_store._load_json("players.json", [])}
            cur_logs = snapshot_store._load_json("game_logs.json", {})

            n_before = len(rows)
            playing = {}
            for name, _ in by_day[day]:
                if name in by_name:
                    playing.setdefault(by_name[name].team_abbr, set()).add(name)
            for name, game in by_day[day]:
                player = by_name.get(name)
                opp = normalize_team(game.get("opponent", ""))
                if player is None or not opp:
                    skipped["no player/opponent"] = skipped.get("no player/opponent", 0) + 1
                    continue
                base = {
                    "date": day, "player": name, "team": player.team_abbr, "opp": opp,
                    "minutes": game.get("minutes") or 0,
                    "gp": (cur_rows.get(name) or {}).get("games_played", 0),
                    "has_prev": name in prev_players, "mpg": player.mpg,
                    "cur": _vals(cur_logs.get(name, [])),
                }
                # who sat that night: what a correct injury report would have said before tip-off
                out = [p.name for p in analyzer.players
                       if p.team_abbr == player.team_abbr and p.name not in playing[player.team_abbr]]
                for prop in PROPS:
                    line = round(getattr(player, BASE_ATTR[prop]) * 2) / 2 or 0.5
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            pred = analyzer.analyze_prop(name, opp, prop, line, out_players=out)
                    except Exception as e:
                        skipped[str(e)[:40]] = skipped.get(str(e)[:40], 0) + 1
                        continue
                    b = pred.breakdown
                    rows.append({**base, "prop": prop, "baseline": b["baseline"], "final": b["final"],
                                 "actual": game.get(STAT_FIELD[prop]) or 0,
                                 "line": line, "over_prob": pred.over_probability,
                                 "expected_minutes": b["expected_minutes"], "position_gap": b["position_gap"],
                                 "missing_share": b["missing_share"],
                                 **{f: b[f] for f in FACTORS}})
            print(f"{day}  snapshot {sha[:7]}  {len(by_day[day]):3d} player-games  {len(rows) - n_before:4d} rows", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    prev = {name: {"n": len(g), "vals": _vals(g)} for name, g in prev_logs.items()}
    with open(out_path, "w") as f:
        json.dump({"rows": rows, "prev": prev}, f)
    print(f"wrote {len(rows)} rows to {out_path}; skipped: {skipped}")


if __name__ == "__main__":
    main()
