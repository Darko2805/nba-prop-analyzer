"""
Reads/writes the committed data snapshot that production runs on.

Render's server IP is blocked (403) by basketball-reference, so the live
app can't scrape bbref itself. Instead, scripts/refresh_snapshot.py runs
from an unblocked network (e.g. this machine), fetches everything, and
writes it here. These files get committed + pushed, and Render's
Auto-Deploy (On Commit) picks up the new data automatically.

Live bbref fetches remain as a fallback for local dev and for anything
not covered by the snapshot (e.g. a player who fell below the rotation
threshold) — they just won't succeed on Render itself.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Optional

from .models import PlayerStats
from .bbref_scraper import GameLog

_SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshot")

# Per-file caches keyed by prev (False = current season, True = prior season).
_shot_zones_cache: dict = {}
_game_logs_cache: dict = {}


def _path(name: str) -> str:
    return os.path.join(_SNAPSHOT_DIR, name)


def _load_json(name: str, default):
    path = _path(name)
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _save_json(name: str, data) -> None:
    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
    with open(_path(name), "w") as f:
        json.dump(data, f)


def _name(base: str, prev: bool) -> str:
    """Prior-season files sit beside the current-season ones as <name>_prev.json."""
    return base.replace(".json", "_prev.json") if prev else base


def save_players(players: list[PlayerStats], prev: bool = False) -> None:
    _save_json(_name("players.json", prev), [asdict(p) for p in players])


def load_players(prev: bool = False) -> list[PlayerStats]:
    return [PlayerStats(**p) for p in _load_json(_name("players.json", prev), [])]


def save_team_stats(raw: dict, prev: bool = False) -> None:
    _save_json(_name("team_stats.json", prev), raw)


def load_team_stats(prev: bool = False) -> dict:
    return _load_json(_name("team_stats.json", prev), {})


def save_opponent_zone_defense(data: dict, prev: bool = False) -> None:
    _save_json(_name("opponent_zone_defense.json", prev), data)


def load_opponent_zone_defense(prev: bool = False) -> dict:
    return _load_json(_name("opponent_zone_defense.json", prev), {})


def save_teamrankings(data: dict, prev: bool = False) -> None:
    """data: team abbr -> {opp_ppg, opp_3pm, ...} as returned by the TeamRankings scraper."""
    _save_json(_name("teamrankings.json", prev), data)


def load_teamrankings(prev: bool = False) -> dict:
    return _load_json(_name("teamrankings.json", prev), {})


def save_shot_zones(data: dict, prev: bool = False) -> None:
    """data: player name -> {at_rim_freq, short_mid_freq, mid_range_freq}"""
    _shot_zones_cache.pop(prev, None)
    _save_json(_name("shot_zones.json", prev), data)


def load_shot_zone(player_name: str, prev: bool = False) -> Optional[dict]:
    if prev not in _shot_zones_cache:
        _shot_zones_cache[prev] = _load_json(_name("shot_zones.json", prev), {})
    return _shot_zones_cache[prev].get(player_name)


def save_game_logs(data: dict, prev: bool = False) -> None:
    """data: player name -> list[GameLog]"""
    serializable = {name: [asdict(g) for g in games] for name, games in data.items()}
    _game_logs_cache.pop(prev, None)
    _save_json(_name("game_logs.json", prev), serializable)


def load_game_logs(player_name: str, prev: bool = False) -> Optional[list[GameLog]]:
    if prev not in _game_logs_cache:
        _game_logs_cache[prev] = _load_json(_name("game_logs.json", prev), {})
    raw = _game_logs_cache[prev].get(player_name)
    if raw is None:
        return None
    return [GameLog(**g) for g in raw]


def save_meta(meta: dict) -> None:
    _save_json("meta.json", meta)


def load_meta() -> dict:
    return _load_json("meta.json", {})
