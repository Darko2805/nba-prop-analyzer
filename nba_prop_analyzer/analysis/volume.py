from __future__ import annotations

from ..data.models import PlayerStats, TeamProfile, OpponentDefense
from .severity import weakness_percentile, severity_bonus


def calculate_volume_adjustment(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    league_avg: dict,
    prop_type: str,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    """
    Compares the player's volume (shots, rebounds, assists) against
    what the opponent typically allows. Returns (factor, explanation).
    `all_opponent_defenses` (every team's OpponentDefense) is only used by
    the 3PT path so far, to rank the opponent's 3PA-allowed against the
    full league instead of a fixed average — see severity.py.
    """
    if prop_type == "points":
        return _scoring_volume(player, player_team, opponent_defense, league_avg, all_opponent_defenses)
    elif prop_type in ("fgm", "fga"):
        return _fg_volume(player, player_team, opponent_defense, league_avg, all_opponent_defenses)
    elif prop_type in ("3pm", "3pa"):
        return _three_point_volume(player, player_team, opponent_defense, league_avg, all_opponent_defenses)
    elif prop_type == "rebounds":
        return _rebound_volume(player, player_team, opponent_defense, league_avg, all_opponent_defenses)
    elif prop_type == "assists":
        return _assist_volume(player, player_team, opponent_defense, league_avg, all_opponent_defenses)
    elif prop_type == "turnovers":
        return _turnover_volume(player, player_team)
    return 1.0, "~ No volume adjustment"


_PPG_SEVERITY_SCALE = 0.10
_PPG_SEVERITY_EXPONENT = 1.6


def _scoring_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    """Compare player scoring volume vs opponent points allowed."""
    avg_ppg = league_avg.get("ppg", 114.0)

    # Opponent allows X points per game - how does that compare?
    opp_ppg_ratio = opp.opp_ppg / max(avg_ppg, 1) if opp.opp_ppg > 0 else 1.0

    # Player's share of team scoring
    player_share = player.ppg / max(player_team.ppg, 1) if player_team.ppg > 0 else 0.2

    # Volume factor: if opponent gives up more points, there's more to go around
    base_factor = 1.0 + (opp_ppg_ratio - 1.0) * player_share * 2

    severity = 0.0
    rank = n = 0
    if all_opponent_defenses and opp.opp_ppg > 0:
        population = [o.opp_ppg for o in all_opponent_defenses.values() if o.opp_ppg > 0]
        pct, rank, n = weakness_percentile(opp.opp_ppg, population)
        severity = severity_bonus(pct, scale=_PPG_SEVERITY_SCALE, exponent=_PPG_SEVERITY_EXPONENT) * player_share

    factor = base_factor + severity
    factor = max(0.88, min(factor, 1.12))

    if n >= 10 and (rank / n >= 2 / 3 or rank / n <= 1 / 3) and abs(severity) > 0.01:
        strength = "high-volume" if severity > 0 else "low-volume"
        note = f"{'+' if severity > 0 else '-'} Opponent is a {strength} scoring matchup: allows {opp.opp_ppg:.1f} PPG, ranks {rank}/{n} in the league"
    elif factor > 1.02:
        note = f"+ Volume boost: opponent allows {opp.opp_ppg:.1f} PPG (player has {player_share:.1%} of team scoring)"
    elif factor < 0.98:
        note = f"- Volume drop: opponent limits scoring to {opp.opp_ppg:.1f} PPG"
    else:
        note = f"~ Volume neutral: opponent PPG allowed near average"
    return factor, note


_3PA_SEVERITY_SCALE = 0.10
_3PA_SEVERITY_EXPONENT = 1.6


def _three_point_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    avg_3pa_allowed = 35.0
    base_factor = opp.opp_3pa / max(avg_3pa_allowed, 1) if opp.opp_3pa > 0 else 1.0

    severity = 0.0
    rank = n = 0
    if all_opponent_defenses:
        population = [o.opp_3pa for o in all_opponent_defenses.values() if o.opp_3pa > 0]
        pct, rank, n = weakness_percentile(opp.opp_3pa, population)
        severity = severity_bonus(pct, scale=_3PA_SEVERITY_SCALE, exponent=_3PA_SEVERITY_EXPONENT)

    factor = base_factor + severity
    factor = max(0.85, min(factor, 1.15))

    if n >= 10 and abs(severity) > 0.02:
        if severity > 0:
            note = f"+ High-volume 3PT defense: opponent allows {opp.opp_3pa:.1f} 3PA/game, ranks {rank}/{n} in the league"
        else:
            note = f"- Low-volume 3PT defense: opponent allows {opp.opp_3pa:.1f} 3PA/game, ranks {rank}/{n} in the league"
    elif factor > 1.03:
        note = f"+ Opponent allows {opp.opp_3pa:.1f} 3PA/game (high volume)"
    elif factor < 0.97:
        note = f"- Opponent limits to {opp.opp_3pa:.1f} 3PA/game (low volume)"
    else:
        note = f"~ 3PT volume neutral"
    return factor, note


_RPG_SEVERITY_SCALE = 0.09
_RPG_SEVERITY_EXPONENT = 1.6


def _rebound_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    avg_team_rpg = 44.0
    team_rpg = player_team.rpg if player_team.rpg > 0 else avg_team_rpg
    player_share = player.rpg / max(team_rpg, 1)

    opp_rpg_ratio = opp.opp_rpg / max(avg_team_rpg, 1) if opp.opp_rpg > 0 else 1.0
    base_factor = 1.0 + (opp_rpg_ratio - 1.0) * player_share * 2

    severity = 0.0
    rank = n = 0
    if all_opponent_defenses and opp.opp_rpg > 0:
        population = [o.opp_rpg for o in all_opponent_defenses.values() if o.opp_rpg > 0]
        pct, rank, n = weakness_percentile(opp.opp_rpg, population)
        severity = severity_bonus(pct, scale=_RPG_SEVERITY_SCALE, exponent=_RPG_SEVERITY_EXPONENT) * player_share

    factor = base_factor + severity
    factor = max(0.88, min(factor, 1.12))

    if n >= 10 and (rank / n >= 2 / 3 or rank / n <= 1 / 3) and abs(severity) > 0.01:
        strength = "gives up a lot of boards" if severity > 0 else "boxes out well"
        note = f"{'+' if severity > 0 else '-'} Opponent {strength}: allows {opp.opp_rpg:.1f} RPG, ranks {rank}/{n} in the league"
    elif factor > 1.02:
        note = f"+ Rebound volume up: opponent allows {opp.opp_rpg:.1f} RPG"
    elif factor < 0.98:
        note = f"- Rebound volume down: opponent limits to {opp.opp_rpg:.1f} RPG"
    else:
        note = f"~ Rebound volume neutral"
    return factor, note


_FGA_SEVERITY_SCALE = 0.09
_FGA_SEVERITY_EXPONENT = 1.6


def _fg_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    """Shot-attempt volume: opponent's FGA allowed vs league average, weighted by the player's shot share."""
    avg_fga = league_avg.get("fga", 88.0)
    opp_fga_ratio = opp.opp_fga / max(avg_fga, 1) if opp.opp_fga > 0 else 1.0

    player_share = player.fga_pg / max(player_team.fga, 1) if player_team.fga > 0 else 0.15

    base_factor = 1.0 + (opp_fga_ratio - 1.0) * player_share * 2

    severity = 0.0
    rank = n = 0
    if all_opponent_defenses and opp.opp_fga > 0:
        population = [o.opp_fga for o in all_opponent_defenses.values() if o.opp_fga > 0]
        pct, rank, n = weakness_percentile(opp.opp_fga, population)
        severity = severity_bonus(pct, scale=_FGA_SEVERITY_SCALE, exponent=_FGA_SEVERITY_EXPONENT) * player_share

    factor = base_factor + severity
    factor = max(0.88, min(factor, 1.12))

    if n >= 10 and (rank / n >= 2 / 3 or rank / n <= 1 / 3) and abs(severity) > 0.01:
        strength = "high-volume" if severity > 0 else "low-volume"
        note = f"{'+' if severity > 0 else '-'} Opponent is a {strength} shot-attempt matchup: allows {opp.opp_fga:.1f} FGA/game, ranks {rank}/{n} in the league"
    elif factor > 1.02:
        note = f"+ Shot volume boost: opponent allows {opp.opp_fga:.1f} FGA/game (player takes {player_share:.1%} of team shots)"
    elif factor < 0.98:
        note = f"- Shot volume drop: opponent limits shots to {opp.opp_fga:.1f} FGA/game"
    else:
        note = f"~ Shot volume neutral: opponent FGA allowed near average"
    return factor, note


def _turnover_volume(
    player: PlayerStats,
    player_team: TeamProfile,
) -> tuple[float, str]:
    """
    Player-exposure factor: how much of this player's own team's shot
    volume runs through their hands, which is what actually exposes them
    to a defense's ball pressure. This used to be a second copy of
    matchup.py's opponent-forced-turnover ratio (opp_topg/13.5), which
    double-counted the same opponent-pressure signal twice in the same
    breakdown -- that signal now lives in matchup.py only. This factor
    instead separates a high-usage ball-handler (more exposed to any
    given pressure level) from a low-usage play-finisher (less exposed),
    independent of who the opponent is.
    """
    avg_fga = player_team.fga if player_team.fga > 0 else 88.0
    player_share = player.fga_pg / max(avg_fga, 1)
    avg_share = 0.20  # a roughly average share of a team's shot volume
    exposure_ratio = player_share / avg_share
    factor = 1.0 + (exposure_ratio - 1.0) * 0.3
    factor = max(0.90, min(factor, 1.10))

    if factor > 1.02:
        note = f"+ High ball-handling exposure: {player_share:.1%} of {player_team.abbreviation}'s shot volume"
    elif factor < 0.98:
        note = f"- Low ball-handling exposure: {player_share:.1%} of {player_team.abbreviation}'s shot volume"
    else:
        note = f"~ Exposure neutral: typical usage share"
    return factor, note


_APG_VOLUME_SEVERITY_SCALE = 0.08
_APG_VOLUME_SEVERITY_EXPONENT = 1.6


def _assist_volume(
    player: PlayerStats,
    player_team: TeamProfile,
    opp: OpponentDefense,
    league_avg: dict,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, str]:
    avg_team_apg = 25.0
    team_apg = player_team.apg if player_team.apg > 0 else avg_team_apg
    player_share = player.apg / max(team_apg, 1)

    opp_apg_ratio = opp.opp_apg / max(avg_team_apg, 1) if opp.opp_apg > 0 else 1.0
    base_factor = 1.0 + (opp_apg_ratio - 1.0) * player_share * 2

    severity = 0.0
    rank = n = 0
    if all_opponent_defenses and opp.opp_apg > 0:
        population = [o.opp_apg for o in all_opponent_defenses.values() if o.opp_apg > 0]
        pct, rank, n = weakness_percentile(opp.opp_apg, population)
        severity = severity_bonus(pct, scale=_APG_VOLUME_SEVERITY_SCALE, exponent=_APG_VOLUME_SEVERITY_EXPONENT) * player_share

    factor = base_factor + severity
    factor = max(0.88, min(factor, 1.12))

    if n >= 10 and (rank / n >= 2 / 3 or rank / n <= 1 / 3) and abs(severity) > 0.01:
        strength = "high-volume" if severity > 0 else "low-volume"
        note = f"{'+' if severity > 0 else '-'} Opponent is a {strength} assist matchup: allows {opp.opp_apg:.1f} APG, ranks {rank}/{n} in the league"
    elif factor > 1.02:
        note = f"+ Assist volume up: opponent allows {opp.opp_apg:.1f} APG"
    elif factor < 0.98:
        note = f"- Assist volume down: opponent limits to {opp.opp_apg:.1f} APG"
    else:
        note = f"~ Assist volume neutral"
    return factor, note
