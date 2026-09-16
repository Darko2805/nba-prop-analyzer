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
import json
import os
from dataclasses import asdict
from typing import Optional

from .models import PlayerStats
from .bbref_scraper import GameLog

_SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshot")

_shot_zones_cache: Optional[dict] = None
_game_logs_cache: Optional[dict] = None


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


def save_players(players: list[PlayerStats]) -> None:
    _save_json("players.json", [asdict(p) for p in players])


def load_players() -> list[PlayerStats]:
    return [PlayerStats(**p) for p in _load_json("players.json", [])]


def save_opponent_zone_defense(data: dict) -> None:
    _save_json("opponent_zone_defense.json", data)


def load_opponent_zone_defense() -> dict:
    return _load_json("opponent_zone_defense.json", {})


def save_shot_zones(data: dict) -> None:
    """data: player name -> {at_rim_freq, short_mid_freq, mid_range_freq}"""
    _save_json("shot_zones.json", data)


def load_shot_zone(player_name: str) -> Optional[dict]:
    global _shot_zones_cache
    if _shot_zones_cache is None:
        _shot_zones_cache = _load_json("shot_zones.json", {})
    return _shot_zones_cache.get(player_name)


def save_game_logs(data: dict) -> None:
    """data: player name -> list[GameLog]"""
    serializable = {name: [asdict(g) for g in games] for name, games in data.items()}
    _save_json("game_logs.json", serializable)


def load_game_logs(player_name: str) -> Optional[list[GameLog]]:
    global _game_logs_cache
    if _game_logs_cache is None:
        _game_logs_cache = _load_json("game_logs.json", {})
    raw = _game_logs_cache.get(player_name)
    if raw is None:
        return None
    return [GameLog(**g) for g in raw]


def save_games_today(games: list[dict]) -> None:
    _save_json("games_today.json", games)


def load_games_today() -> list[dict]:
    return _load_json("games_today.json", [])


def save_meta(meta: dict) -> None:
    _save_json("meta.json", meta)


def load_meta() -> dict:
    return _load_json("meta.json", {})
