"""
Early-season blending of last season's data with the new season's.

The new season's numbers are tiny samples for the first weeks (a player's
3-game scoring average is mostly noise) while last season's are large but
slightly stale. So every stat is a weighted mix, with the weight on the new
season growing continuously with how many games it has:

    weight = games / (games + K)

With K=10, a player is 50/50 after 10 games and ~89% new-season after 80.
It is 0.0 with no new-season games, in which case the result is exactly last
season's data -- so before the opener nothing changes, and no cutoff or
tier is involved at any point.
"""
from __future__ import annotations

import dataclasses
from typing import Any


def blend_weight(games: float, k: float) -> float:
    return games / (games + k) if games and games > 0 else 0.0


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
