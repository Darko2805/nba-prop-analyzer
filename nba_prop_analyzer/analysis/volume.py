from ..data.models import PlayerStats, TeamProfile, OpponentDefense


def calculate_volume_adjustment(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    league_avg: dict,
    prop_type: str,
) -> tuple[float, str]:
    """
    Compares the player's volume (shots, rebounds, assists) against
    what the opponent typically allows. Returns (factor, explanation).
    """
    if prop_type in ("points", "pra"):
        return _scoring_volume(player, player_team, opponent_defense, league_avg)
    elif prop_type in ("fgm", "fga"):
        return _fg_volume(player, player_team, opponent_defense, league_avg)
    elif prop_type in ("3pm", "3pa"):
        return _three_point_volume(player, player_team, opponent_defense, league_avg)
    elif prop_type == "rebounds":
        return _rebound_volume(player, player_team, opponent_defense, league_avg)
    elif prop_type == "assists":
        return _assist_volume(player, player_team, opponent_defense, league_avg)
    elif prop_type == "turnovers":
        return _turnover_volume(player, opponent_defense)
    return 1.0, "~ No volume adjustment"


def _scoring_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    """Compare player scoring volume vs opponent points allowed."""
    avg_ppg = league_avg.get("ppg", 114.0)

    # Opponent allows X points per game - how does that compare?
    opp_ppg_ratio = opp.opp_ppg / max(avg_ppg, 1) if opp.opp_ppg > 0 else 1.0

    # Player's share of team scoring
    player_share = player.ppg / max(player_team.ppg, 1) if player_team.ppg > 0 else 0.2

    # Volume factor: if opponent gives up more points, there's more to go around
    factor = 1.0 + (opp_ppg_ratio - 1.0) * player_share * 2
    factor = max(0.92, min(factor, 1.08))

    if factor > 1.02:
        note = f"+ Volume boost: opponent allows {opp.opp_ppg:.1f} PPG (player has {player_share:.1%} of team scoring)"
    elif factor < 0.98:
        note = f"- Volume drop: opponent limits scoring to {opp.opp_ppg:.1f} PPG"
    else:
        note = f"~ Volume neutral: opponent PPG allowed near average"
    return factor, note


def _three_point_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    avg_3pa_allowed = 35.0
    factor = opp.opp_3pa / max(avg_3pa_allowed, 1) if opp.opp_3pa > 0 else 1.0
    factor = max(0.90, min(factor, 1.10))

    if factor > 1.03:
        note = f"+ Opponent allows {opp.opp_3pa:.1f} 3PA/game (high volume)"
    elif factor < 0.97:
        note = f"- Opponent limits to {opp.opp_3pa:.1f} 3PA/game (low volume)"
    else:
        note = f"~ 3PT volume neutral"
    return factor, note


def _rebound_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    avg_team_rpg = 44.0
    team_rpg = player_team.rpg if player_team.rpg > 0 else avg_team_rpg
    player_share = player.rpg / max(team_rpg, 1)

    opp_rpg_ratio = opp.opp_rpg / max(avg_team_rpg, 1) if opp.opp_rpg > 0 else 1.0
    factor = 1.0 + (opp_rpg_ratio - 1.0) * player_share * 2
    factor = max(0.92, min(factor, 1.08))

    if factor > 1.02:
        note = f"+ Rebound volume up: opponent allows {opp.opp_rpg:.1f} RPG"
    elif factor < 0.98:
        note = f"- Rebound volume down: opponent limits to {opp.opp_rpg:.1f} RPG"
    else:
        note = f"~ Rebound volume neutral"
    return factor, note


def _fg_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    """Shot-attempt volume: opponent's FGA allowed vs league average, weighted by the player's shot share."""
    avg_fga = league_avg.get("fga", 88.0)
    opp_fga_ratio = opp.opp_fga / max(avg_fga, 1) if opp.opp_fga > 0 else 1.0

    player_share = player.fga_pg / max(player_team.fga, 1) if player_team.fga > 0 else 0.15

    factor = 1.0 + (opp_fga_ratio - 1.0) * player_share * 2
    factor = max(0.92, min(factor, 1.08))

    if factor > 1.02:
        note = f"+ Shot volume boost: opponent allows {opp.opp_fga:.1f} FGA/game (player takes {player_share:.1%} of team shots)"
    elif factor < 0.98:
        note = f"- Shot volume drop: opponent limits shots to {opp.opp_fga:.1f} FGA/game"
    else:
        note = f"~ Shot volume neutral: opponent FGA allowed near average"
    return factor, note


def _turnover_volume(
    player: PlayerStats,
    opp: OpponentDefense,
) -> tuple[float, str]:
    """Turnover volume driven by opponent's ball pressure (forced-turnover rate), not team shot share."""
    avg_topg = 13.5  # league average team turnovers forced per game
    factor = opp.opp_topg / max(avg_topg, 1) if opp.opp_topg > 0 else 1.0
    factor = max(0.88, min(factor, 1.12))

    if factor > 1.03:
        note = f"+ Opponent forces extra turnovers: {opp.opp_topg:.1f} TOV/game allowed"
    elif factor < 0.97:
        note = f"- Opponent avoids forcing turnovers: {opp.opp_topg:.1f} TOV/game allowed"
    else:
        note = f"~ Turnover volume neutral"
    return factor, note


def _assist_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    avg_team_apg = 25.0
    team_apg = player_team.apg if player_team.apg > 0 else avg_team_apg
    player_share = player.apg / max(team_apg, 1)

    opp_apg_ratio = opp.opp_apg / max(avg_team_apg, 1) if opp.opp_apg > 0 else 1.0
    factor = 1.0 + (opp_apg_ratio - 1.0) * player_share * 2
    factor = max(0.92, min(factor, 1.08))

    if factor > 1.02:
        note = f"+ Assist volume up: opponent allows {opp.opp_apg:.1f} APG"
    elif factor < 0.98:
        note = f"- Assist volume down: opponent limits to {opp.opp_apg:.1f} APG"
    else:
        note = f"~ Assist volume neutral"
    return factor, note
