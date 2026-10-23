"""
The yardstick a factor measures an opponent against.

A gap like "allows 38.0% from three" only means something next to what the rest of the league
allows. Hardcoded season estimates drift from whatever the data source actually reports, and a
yardstick that is even slightly low makes EVERY team look weak -- a bias that then gets multiplied
into every projection. So the league average is computed from the same numbers being compared,
and the written-down estimate is used only when there is too little data to compute one.
"""
from __future__ import annotations

MIN_TEAMS = 10


def league_mean(all_defenses: dict | None, field: str, fallback: float, min_teams: int = MIN_TEAMS) -> float:
    """Mean of `field` across every team that reports it, or `fallback` when too few do."""
    if not all_defenses:
        return fallback
    values = [v for v in (getattr(o, field, 0.0) or 0.0 for o in all_defenses.values()) if v > 0]
    if len(values) < min_teams:
        return fallback
    return sum(values) / len(values)
