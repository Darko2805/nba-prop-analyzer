"""
Teammate/team shooting efficiency — the NBA's own tracking distinguishes
"potential assists" (a pass leading to any shot attempt) from actual
assists (only counts if the shot goes in), and that gap is driven
entirely by whether the recipient converts, not by the passer's own
skill or volume (already covered by volume.py's _assist_volume).

Ranks the player's own team's shooting efficiency (True Shooting %,
which folds 2P/3P/FT accuracy into one number) against the rest of the
league — same severity-curve approach as every other factor — so a team
full of good finishers converts more of this player's assist
opportunities into real, countable assists.
"""

from __future__ import annotations

from ..data.models import TeamProfile
from .severity import weakness_percentile, severity_bonus

_LEAGUE_TS_PCT = 0.565  # ~league-average True Shooting %
_SEVERITY_SCALE = 0.08
_SEVERITY_EXPONENT = 1.6


def calculate_teammate_efficiency_factor(
    player_team: TeamProfile,
    prop_type: str,
    all_team_profiles: dict | None = None,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string). Only applies to
    assists — how often a good pass turns into a made shot only bears on
    the assist count itself, not points/rebounds/other props.
    """
    if prop_type != "assists":
        return 1.0, "~ Teammate shooting efficiency not applicable for this prop"

    all_team_profiles = all_team_profiles or {}
    team_ts = player_team.ts_pct or _LEAGUE_TS_PCT

    base_ratio = team_ts / _LEAGUE_TS_PCT - 1.0
    weighted_base = base_ratio * 1.5

    severity = 0.0
    rank = n = 0
    pct = 0.5
    if all_team_profiles:
        population = [t.ts_pct for t in all_team_profiles.values() if t.ts_pct > 0]
        pct, rank, n = weakness_percentile(team_ts, population)
        severity = severity_bonus(pct, scale=_SEVERITY_SCALE, exponent=_SEVERITY_EXPONENT)

    factor = 1.0 + weighted_base + severity
    factor = max(0.90, min(factor, 1.12))

    rank_suffix = f", ranks {rank}/{n} in the league" if n >= 10 and (pct >= 2 / 3 or pct <= 1 / 3) else ""
    if factor >= 1.03:
        note = f"+ Teammates convert well: {player_team.abbreviation} shoots {team_ts:.1%} TS{rank_suffix}"
    elif factor <= 0.97:
        note = f"- Teammates finish poorly: {player_team.abbreviation} shoots only {team_ts:.1%} TS{rank_suffix}"
    else:
        note = "~ Team shooting efficiency neutral"

    return factor, note
