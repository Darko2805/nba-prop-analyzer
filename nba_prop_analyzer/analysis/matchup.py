from __future__ import annotations

from ..data.models import PlayerStats, TeamProfile, OpponentDefense
from .severity import weakness_percentile, severity_bonus

_DRTG_SEVERITY_SCALE = 0.09
_DRTG_SEVERITY_EXPONENT = 1.6


def calculate_matchup_factor(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    league_avg: dict,
    prop_type: str,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string).
    Compares player's team offensive strength vs opponent's defensive
    strength. For scoring-related props, the opponent's DRTG is also
    ranked against the full league (see severity.py) — a team that's a
    few points worse than average shouldn't move the number the same as
    the league's actual worst defense. The team's own offensive rating
    stays a flat ratio for now (not yet discussed/tuned).
    """
    avg_ortg = league_avg.get("ortg", 114.0)
    avg_drtg = league_avg.get("drtg", 114.0)

    if prop_type in ("points", "3pm", "fgm", "fga", "3pa"):
        # Offensive matchup: team ORTG vs opponent DRTG
        team_off_edge = player_team.ortg / max(avg_ortg, 1)
        opp_def_weakness = avg_drtg / max(opponent_defense.drtg, 1)
        base_factor = team_off_edge * opp_def_weakness

        severity = 0.0
        rank = n = 0
        if all_opponent_defenses and opponent_defense.drtg > 0:
            population = [o.drtg for o in all_opponent_defenses.values() if o.drtg > 0]
            pct, rank, n = weakness_percentile(opponent_defense.drtg, population)
            severity = severity_bonus(pct, scale=_DRTG_SEVERITY_SCALE, exponent=_DRTG_SEVERITY_EXPONENT)

        factor = base_factor + severity

        if n >= 10 and (rank / n >= 2 / 3 or rank / n <= 1 / 3) and abs(severity) > 0.015:
            tier = "bottom third" if severity > 0 else "top third"
            note = f"{'+' if severity > 0 else '-'} {player_team.abbreviation} ORTG {player_team.ortg:.1f} vs {opponent_defense.abbreviation} DRTG {opponent_defense.drtg:.1f} — ranks {rank}/{n} in the league ({tier})"
        elif factor > 1.02:
            note = f"+ Good matchup: {player_team.abbreviation} ORTG {player_team.ortg:.1f} vs {opponent_defense.abbreviation} DRTG {opponent_defense.drtg:.1f}"
        elif factor < 0.98:
            note = f"- Tough matchup: {player_team.abbreviation} ORTG {player_team.ortg:.1f} vs {opponent_defense.abbreviation} DRTG {opponent_defense.drtg:.1f}"
        else:
            note = f"~ Neutral matchup: ORTG/DRTG close to league average"

    elif prop_type == "rebounds":
        # Rebounding matchup: use team rebound rates
        opp_rpg = opponent_defense.opp_rpg
        avg_rpg = league_avg.get("ppg", 114.0) * 0.39  # rough boards per game estimate
        if avg_rpg > 0:
            factor = opp_rpg / avg_rpg
        else:
            factor = 1.0
        factor = max(0.90, min(factor, 1.10))

        if factor > 1.02:
            note = f"+ Opponent allows more rebounds than average"
        elif factor < 0.98:
            note = f"- Opponent limits rebounding opportunities"
        else:
            note = f"~ Neutral rebounding matchup"

    elif prop_type == "assists":
        opp_apg = opponent_defense.opp_apg
        avg_apg = 25.0  # league average team assists
        factor = opp_apg / max(avg_apg, 1)
        factor = max(0.90, min(factor, 1.10))

        if factor > 1.02:
            note = f"+ Opponent allows {opp_apg:.1f} AST/game (above avg)"
        elif factor < 0.98:
            note = f"- Opponent limits assists to {opp_apg:.1f}/game"
        else:
            note = f"~ Neutral assist matchup"

    elif prop_type == "turnovers":
        # Opponent's ball pressure/forced-turnover rate vs league average.
        # Higher opponent pressure -> more expected turnovers (factor > 1).
        avg_topg = 13.5  # league average team turnovers forced per game
        factor = opponent_defense.opp_topg / max(avg_topg, 1) if opponent_defense.opp_topg > 0 else 1.0
        factor = max(0.85, min(factor, 1.15))

        if factor > 1.03:
            note = f"+ High-pressure defense: opponent forces {opponent_defense.opp_topg:.1f} TOV/game"
        elif factor < 0.97:
            note = f"- Low-pressure defense: opponent forces only {opponent_defense.opp_topg:.1f} TOV/game"
        else:
            note = f"~ Neutral turnover matchup"
    else:
        factor = 1.0
        note = "~ Matchup data not applicable"

    return factor, note
