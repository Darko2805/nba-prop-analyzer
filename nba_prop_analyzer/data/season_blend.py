"""
Early-season blending of last season's data with the new season's.

The new season's numbers are tiny samples for the first games while last season's are large but
slightly stale, so the two are mixed by a weight on the new season.

Teams: weight = games / (games + K), growing continuously with the games played.
Players: a plain rule (see player_blend_weight) -- last season alone before the opener, an equal mix
for games 1 to 4, this season alone from game 5, and this season alone from game 1 for a player who
has no last season.
"""
from __future__ import annotations

import dataclasses
from typing import Any


def blend_weight(games: float, k: float) -> float:
    return games / (games + k) if games and games > 0 else 0.0


def player_blend_weight(games: float, has_last_season: bool = True,
                        full_trust_games: int = 5, early_weight: float = 0.5) -> float:
    """Weight on this season for a player. 0 = last season only, 1 = this season only."""
    if not games or games <= 0:
        return 0.0 if has_last_season else 1.0
    if not has_last_season or games >= full_trust_games:
        return 1.0
    return early_weight


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
