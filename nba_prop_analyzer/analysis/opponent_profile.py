from ..data.models import PlayerStats, TeamProfile, OpponentDefense


def calculate_opponent_profile_adjustment(
    player: PlayerStats,
    opponent_defense: OpponentDefense,
    league_avg: dict,
    prop_type: str,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string).
    Examines how the opponent's defensive profile matches the player's tendencies.
    """
    if prop_type in ("points", "pra"):
        return _scoring_profile(player, opponent_defense, league_avg)
    elif prop_type == "3pm":
        return _three_point_profile(player, opponent_defense, league_avg)
    elif prop_type == "rebounds":
        return _rebound_profile(player, opponent_defense, league_avg)
    elif prop_type == "assists":
        return _assist_profile(player, opponent_defense, league_avg)
    return 1.0, "~ No profile adjustment"


def _scoring_profile(
    player: PlayerStats,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    """Shot-zone-weighted efficiency adjustment for scoring."""
    avg_ts = league_avg.get("ts_pct", 0.58)
    if avg_ts == 0:
        return 1.0, "~ No TS data"

    # How the opponent affects overall shooting efficiency
    opp_ts_ratio = opp.opp_ts_pct / max(avg_ts, 0.01) if opp.opp_ts_pct > 0 else 1.0

    # Weight by player's shot distribution zones
    rim_weight = player.at_rim_freq
    mid_weight = player.mid_range_freq
    three_weight = player.three_point_rate
    other_weight = max(0, 1.0 - rim_weight - mid_weight - three_weight)

    # Opponent rim defense
    avg_rim_acc = 0.65  # league average at-rim accuracy
    rim_factor = opp.opp_at_rim_acc / max(avg_rim_acc, 0.01) if opp.opp_at_rim_acc > 0 else 1.0

    # Opponent 3PT defense
    avg_3p = 0.36  # league average 3P%
    three_factor = opp.opp_3p_pct / max(avg_3p, 0.01) if opp.opp_3p_pct > 0 else 1.0

    # Composite: weighted average of zone factors
    factor = (
        rim_weight * rim_factor
        + three_weight * three_factor
        + (mid_weight + other_weight) * opp_ts_ratio
    )

    # Clamp to reasonable range
    factor = max(0.88, min(factor, 1.12))

    parts = []
    if three_factor > 1.02 and three_weight > 0.25:
        parts.append(f"opponent allows high 3P% ({opp.opp_3p_pct:.1%})")
    elif three_factor < 0.98 and three_weight > 0.25:
        parts.append(f"opponent limits 3PT shots ({opp.opp_3p_pct:.1%})")
    if rim_factor > 1.02 and rim_weight > 0.25:
        parts.append(f"weak rim protection ({opp.opp_at_rim_acc:.1%} allowed)")
    elif rim_factor < 0.98 and rim_weight > 0.25:
        parts.append(f"strong rim protection ({opp.opp_at_rim_acc:.1%} allowed)")

    if factor > 1.02:
        note = f"+ Favorable shot profile: {', '.join(parts) if parts else 'above-avg efficiency allowed'}"
    elif factor < 0.98:
        note = f"- Unfavorable shot profile: {', '.join(parts) if parts else 'below-avg efficiency allowed'}"
    else:
        note = f"~ Neutral shot profile matchup"

    return factor, note


def _three_point_profile(
    player: PlayerStats,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    """How well the opponent defends the 3-point line."""
    avg_3p_pct = 0.36
    avg_3pa = 35.0  # league avg opponent 3PA per game

    # Efficiency factor
    eff_factor = opp.opp_3p_pct / max(avg_3p_pct, 0.01) if opp.opp_3p_pct > 0 else 1.0
    # Volume factor (do they allow more 3PA?)
    vol_factor = opp.opp_3pa / max(avg_3pa, 1) if opp.opp_3pa > 0 else 1.0

    factor = (eff_factor * 0.6) + (vol_factor * 0.4)
    factor = max(0.85, min(factor, 1.15))

    if factor > 1.03:
        note = f"+ Opponent 3PT defense is weak: {opp.opp_3p_pct:.1%} allowed, {opp.opp_3pa:.1f} 3PA/game"
    elif factor < 0.97:
        note = f"- Opponent 3PT defense is strong: {opp.opp_3p_pct:.1%} allowed, {opp.opp_3pa:.1f} 3PA/game"
    else:
        note = f"~ Neutral 3PT defense matchup"

    return factor, note


def _rebound_profile(
    player: PlayerStats,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    avg_rpg = 44.0  # league average total rebounds per game
    factor = opp.opp_rpg / max(avg_rpg, 1) if opp.opp_rpg > 0 else 1.0
    factor = max(0.90, min(factor, 1.10))

    if factor > 1.02:
        note = f"+ Opponent allows {opp.opp_rpg:.1f} RPG (above avg)"
    elif factor < 0.98:
        note = f"- Opponent limits rebounds to {opp.opp_rpg:.1f} RPG"
    else:
        note = f"~ Neutral rebound profile"
    return factor, note


def _assist_profile(
    player: PlayerStats,
    opp: OpponentDefense,
    league_avg: dict,
) -> tuple[float, str]:
    avg_apg = 25.0
    factor = opp.opp_apg / max(avg_apg, 1) if opp.opp_apg > 0 else 1.0
    factor = max(0.90, min(factor, 1.10))

    if factor > 1.02:
        note = f"+ Opponent allows {opp.opp_apg:.1f} AST/game (above avg)"
    elif factor < 0.98:
        note = f"- Opponent limits assists to {opp.opp_apg:.1f}/game"
    else:
        note = f"~ Neutral assist profile"
    return factor, note
