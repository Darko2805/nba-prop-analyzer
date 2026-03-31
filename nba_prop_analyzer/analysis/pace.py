from ..data.models import TeamProfile


def calculate_pace_factor(
    player_team: TeamProfile,
    opponent_team: TeamProfile,
    league_avg_pace: float,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string).
    Expected game pace = average of both teams' pace.
    """
    expected_pace = (player_team.pace + opponent_team.pace) / 2
    factor = expected_pace / max(league_avg_pace, 1)

    diff_pct = (factor - 1.0) * 100

    if factor > 1.02:
        note = f"+ Pace boost: Expected pace {expected_pace:.1f} ({diff_pct:+.1f}% volume)"
    elif factor < 0.98:
        note = f"- Slow pace: Expected pace {expected_pace:.1f} ({diff_pct:+.1f}% volume)"
    else:
        note = f"~ Neutral pace: Expected {expected_pace:.1f} (league avg {league_avg_pace:.1f})"

    return factor, note
