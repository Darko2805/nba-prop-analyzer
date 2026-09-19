"""
Free-throw generation — Dean Oliver's "Four Factors" treats getting to the
line as one of basketball's four core scoring-efficiency levers, alongside
shooting, rebounding, and ball control. It isn't covered by any of matchup
(team-level ORTG/DRTG), pace (possessions), shot-zone (field-goal accuracy
by zone), volume (attempt/rebound/assist counts), or trend.

Ranks the opponent's tendency to send shooters to the line (FTA allowed
per game) against the rest of the league — same severity-curve approach
as every other factor in this engine — weighted by how much this specific
player's own scoring actually relies on free throws (their FT rate:
FTA/FGA). A player who rarely draws fouls shouldn't get a boost just
because the opponent fouls a lot, and vice versa.
"""

from __future__ import annotations

from ..data.models import PlayerStats, OpponentDefense
from .severity import weakness_percentile, severity_bonus

_LEAGUE_OPP_FTA = 25.0  # ~league-average opponent FTA allowed per game
_SEVERITY_SCALE = 0.10
_SEVERITY_EXPONENT = 1.6


def calculate_free_throw_factor(
    player: PlayerStats,
    opponent_defense: OpponentDefense,
    prop_type: str,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string). Only applies to
    points — free-throw makes are a scoring-specific factor and don't bear
    on rebounds/assists/turnovers/field-goal-attempt or three-point props.
    (pra runs this as one of its three blended components, so it's covered
    there without needing "pra" listed here directly.)
    """
    if prop_type != "points":
        return 1.0, "~ Free-throw rate not applicable for this prop"

    all_opponent_defenses = all_opponent_defenses or {}
    ft_rate = max(player.ft_rate, 0.0)
    opp_fta = opponent_defense.opp_fta or _LEAGUE_OPP_FTA

    # Base ratio vs league average, weighted by how much this player's own
    # scoring leans on free throws (same usage-weighting idea as shot_zone).
    base_gap = opp_fta / _LEAGUE_OPP_FTA - 1.0
    weighted_base = ft_rate * base_gap * 2.0

    severity = 0.0
    rank = n = 0
    pct = 0.5
    if all_opponent_defenses:
        population = [o.opp_fta for o in all_opponent_defenses.values() if o.opp_fta > 0]
        pct, rank, n = weakness_percentile(opp_fta, population)
        severity = severity_bonus(pct, scale=_SEVERITY_SCALE, exponent=_SEVERITY_EXPONENT) * ft_rate

    factor = 1.0 + weighted_base + severity
    factor = max(0.92, min(factor, 1.14))

    rank_suffix = f", ranks {rank}/{n} in the league" if n >= 10 and (pct >= 2 / 3 or pct <= 1 / 3) else ""
    if factor >= 1.04:
        note = f"+ Free-throw edge: opp allows {opp_fta:.1f} FTA/game (player FT rate {ft_rate:.2f}){rank_suffix}"
    elif factor <= 0.96:
        note = f"- Free-throw limited: opp allows only {opp_fta:.1f} FTA/game (player FT rate {ft_rate:.2f}){rank_suffix}"
    else:
        note = "~ Free-throw rate neutral: opponent FTA allowed near average"

    return factor, note
