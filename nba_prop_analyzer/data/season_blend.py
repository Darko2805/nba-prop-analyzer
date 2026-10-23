"""
Early-season blending of last season's data with the new season's.

The new season's numbers are tiny samples for the first games while last season's are large but
slightly stale, so the two are mixed by a weight on the new season.

Teams: weight = games / (games + K), growing continuously with the games played.
Players: see player_blend_weight -- last season alone before the opener, then this season's weight
ramps with the games played (20% after one, 100% at five), and a player with no last season uses
their own real numbers from game one.
"""
from __future__ import annotations

import dataclasses
from typing import Any


def blend_weight(games: float, k: float) -> float:
    return games / (games + k) if games and games > 0 else 0.0


def player_blend_weight(games: float, has_last_season: bool = True, full_trust_games: int = 5) -> float:
    """
    Weight on this season for a player's averages. 0 = last season only, 1 = this season only.
    The weight ramps with the games played (1 of 5 games = 20%) and reaches this season alone at
    `full_trust_games`, so no single night can outweigh a full prior season. A player with no last
    season has nothing to ramp from, so their own real numbers are used from the first game.
    """
    if not has_last_season:
        return 1.0
    if not games or games <= 0:
        return 0.0
    return min(1.0, games / full_trust_games)


def profile_blend_weight(games: float, full_trust_games: int = 5) -> float:
    """
    Weight on this season for a SHAPE rather than an average -- a player's shot-zone mix, where one
    game's shot locations are noise, not a style change. Unlike the season average, this never jumps:
    it ramps with the games actually played (1 game = 1/5 of the way) and reaches this season alone at
    `full_trust_games`. Below that the rest of the weight sits on the baseline the caller passes in
    (last season's profile, or the league-average profile for a player with no last season), so a
    single 3-point-heavy night cannot turn someone into a 3-point shooter.
    """
    if not games or games <= 0:
        return 0.0
    return min(1.0, games / full_trust_games)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def blend_numbers(cur: dict, prev: dict, weight: float) -> dict:
    """Blend two flat dicts: shared numeric values mix, anything else prefers
    the current value when the current season has any weight, else the prior."""
    out = dict(prev)
    for key, cv in cur.items():
        pv = prev.get(key)
        if _is_number(cv) and _is_number(pv):
            out[key] = weight * cv + (1 - weight) * pv
        elif key not in prev or weight > 0:
            out[key] = cv
    return out


def blend_dataclass(cur, prev, weight: float, keep_current: tuple = ("games_played",)):
    """
    Blend two instances of the same dataclass field by field. Numbers mix by
    weight; fields in keep_current (counts like games_played) and non-numeric
    fields take the current season's value when it has any weight and is
    non-empty, otherwise the prior's.
    """
    if cur is None:
        return prev
    if prev is None:
        return cur
    values = {}
    for f in dataclasses.fields(cur):
        cv, pv = getattr(cur, f.name), getattr(prev, f.name)
        if f.name in keep_current or not (_is_number(cv) and _is_number(pv)):
            values[f.name] = cv if (weight > 0 and cv not in ("", None)) else pv
        else:
            values[f.name] = weight * cv + (1 - weight) * pv
    return dataclasses.replace(cur, **values)
