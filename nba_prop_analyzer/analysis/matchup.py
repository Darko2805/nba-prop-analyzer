from ..data.models import PlayerStats, TeamProfile, OpponentDefense


def calculate_matchup_factor(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    league_avg: dict,
    prop_type: str,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string).
    Compares player's team offensive strength vs opponent's defensive strength.
    """
    avg_ortg = league_avg.get("ortg", 114.0)
    avg_drtg = league_avg.get("drtg", 114.0)

    if prop_type in ("points", "3pm", "pra"):
        # Offensive matchup: team ORTG vs opponent DRTG
        team_off_edge = player_team.ortg / max(avg_ortg, 1)
        opp_def_weakness = avg_drtg / max(opponent_defense.drtg, 1)
        factor = team_off_edge * opp_def_weakness

        if factor > 1.02:
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
    else:
        factor = 1.0
        note = "~ Matchup data not applicable"

    return factor, note
