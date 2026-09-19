from __future__ import annotations

from ..data.models import TeamProfile
from .severity import weakness_percentile, severity_bonus

# Kept smaller than shot_zone's 0.14 — pace runs through every prop type
# equally, so its swing stacks with whichever prop-specific factors also
# fire, unlike a zone mismatch which is specific to one prop.
_PACE_SEVERITY_SCALE = 0.08
_PACE_SEVERITY_EXPONENT = 1.6


def calculate_pace_factor(
    player_team: TeamProfile,
    opponent_team: TeamProfile,
    league_avg_pace: float,
    all_team_profiles: dict | None = None,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string).
    Expected game pace = average of both teams' pace, compared to the
    league average for the base factor. On top of that, a convex severity
    bonus (see severity.py) rewards/penalizes games whose expected pace
    ranks among the extreme thirds of the league — not just "faster or
    slower than average" — by ranking this expected pace against what this
    same team's blended pace would be against every other opponent.
    `all_team_profiles` should be every team's TeamProfile (e.g.
    PropAnalyzer.team_profiles); the severity bonus is skipped without it.
    """
    expected_pace = (player_team.pace + opponent_team.pace) / 2
    base_factor = expected_pace / max(league_avg_pace, 1)

    severity = 0.0
    rank = n = 0
    if all_team_profiles:
        population = [(player_team.pace + t.pace) / 2 for t in all_team_profiles.values()]
        pct, rank, n = weakness_percentile(expected_pace, population)
        severity = severity_bonus(pct, scale=_PACE_SEVERITY_SCALE, exponent=_PACE_SEVERITY_EXPONENT)

    factor = base_factor + severity
    diff_pct = (factor - 1.0) * 100

    if n >= 10 and abs(severity) > 0.02:
        if severity > 0:
            note = f"+ Fast-paced matchup: expected pace {expected_pace:.1f} ranks {rank}/{n} for this team's schedule"
        else:
            note = f"- Slow-paced matchup: expected pace {expected_pace:.1f} ranks {rank}/{n} for this team's schedule"
    elif factor > 1.02:
        note = f"+ Pace boost: Expected pace {expected_pace:.1f} ({diff_pct:+.1f}% volume)"
    elif factor < 0.98:
        note = f"- Slow pace: Expected pace {expected_pace:.1f} ({diff_pct:+.1f}% volume)"
    else:
        note = f"~ Neutral pace: Expected {expected_pace:.1f} (league avg {league_avg_pace:.1f})"

    return factor, note
