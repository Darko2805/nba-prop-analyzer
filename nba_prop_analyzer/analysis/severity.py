"""
Shared league-percentile severity scoring, used across the prediction
engine's correlation factors (shot zone, pace, opponent volume allowed, ...).

The idea: rank a value against the real league distribution this season
(not a fixed ratio-to-average), then convert that rank into a smooth,
convex bonus/penalty — zero at league-average, small near the bottom/top-
third boundary, accelerating hard only once a value is genuinely one of
the most extreme in the league. No cliffs: a team ranked 20th and a team
ranked 21st get almost the same bonus even if a discrete-tier line would
have put them on opposite sides of it.

Originally built inside shot_zone.py for zone-defense ranking; pulled out
here so pace, opponent-volume-allowed, and future correlation factors can
reuse the exact same curve instead of each rolling their own.
"""

from __future__ import annotations


def rank_in_league(value: float, population: list[float]) -> tuple[int, int]:
    """1-indexed ascending rank of `value` among `population` (1 = lowest value in the league). Ties share the lower rank."""
    if not population:
        return 0, 0
    n = len(population)
    rank = sum(1 for v in population if v < value) + 1
    return rank, n


def weakness_percentile(value: float, population: list[float]) -> tuple[float, int, int]:
    """
    Where `value` ranks against `population`, this season — not against a
    fixed average. Returns (percentile, rank, league_size): 0.0 = the
    lowest value in the league, 1.0 = the highest, 0.5 = dead average.
    Falls back to a neutral 0.5 when there's no population to rank against
    (e.g. a data source hiccup), so a missing input never accidentally
    reads as an extreme case.
    """
    rank, n = rank_in_league(value, population)
    if n <= 1:
        return 0.5, rank, n
    return (rank - 1) / (n - 1), rank, n


def severity_bonus(percentile: float, scale: float = 0.14, exponent: float = 1.6) -> float:
    """
    Converts a league percentile (0..1, 0.5 = average) into a signed bonus:
    zero at average, ramping up convexly toward +scale as percentile -> 1.0
    and -scale as percentile -> 0.0. `exponent` controls how sharply it
    accelerates — 1.6 stays gentle through the middle thirds and only opens
    up once a value is genuinely near the extreme.
    """
    centered = percentile - 0.5  # -0.5 (lowest in league) .. +0.5 (highest in league)
    if centered == 0:
        return 0.0
    magnitude = min(abs(centered) * 2, 1.0)  # 0 at average, 1 at the extreme
    sign = 1.0 if centered > 0 else -1.0
    return sign * (magnitude ** exponent) * scale
