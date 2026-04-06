"""
Shot zone exploitation analysis.

Step-by-step evaluation:
  1. Identify player's primary shot zones by frequency
  2. Assess opponent's defensive efficiency in each zone vs league average
  3. Cross-match: player's top zones vs opponent's weak zones
  4. Downstream effects: foul rate / FT exploitation if attacking weak rim defense

Returns a multiplicative factor and a list of annotated step strings.
"""

from ..data.models import PlayerStats, TeamProfile, OpponentDefense

# 2025-26 NBA approximate league-average zone benchmarks
_LEAGUE = {
    "at_rim_acc":    0.645,   # ~64.5% at the rim
    "short_mid_acc": 0.415,   # ~41.5% short mid-range
    "long_mid_acc":  0.400,   # ~40.0% long mid-range
    "three_acc":     0.360,   # ~36.0% from three
    "opp_fta_pg":    25.0,    # ~25 FTA allowed per game
}


def calculate_shot_zone_exploitation(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    prop_type: str,
) -> tuple[float, list[str]]:
    """
    Returns (multiplicative_factor, list_of_step_notes).

    Factor range: 0.82 (opponent locks every zone) – 1.22 (multiple zone gaps exploited).
    Steps contain '+'/'-'/'→' prefixed strings for the UI.
    """
    if prop_type not in ("points", "pra", "3pm"):
        return 1.0, ["~ Zone analysis not applicable for this prop"]

    steps: list[str] = []

    # ── Step 1: player shot distribution ────────────────────────────────
    at_rim   = max(player.at_rim_freq, 0.0)
    long_mid = max(player.mid_range_freq, 0.0)   # field is long mid
    short_mid = max(player.short_mid_freq, 0.0)
    three_rate = max(player.three_point_rate, 0.0)

    # If tracking data is sparse, estimate from three_point_rate
    total_tracked = at_rim + short_mid + long_mid + three_rate
    if total_tracked < 0.40:
        tr = player.three_point_rate or 0.33
        non_three = 1.0 - tr
        at_rim    = non_three * 0.55
        long_mid  = non_three * 0.25
        short_mid = non_three * 0.20
        three_rate = tr

    zones_ranked = sorted(
        [("at-rim", at_rim), ("short-mid", short_mid), ("long-mid", long_mid), ("3PT", three_rate)],
        key=lambda x: -x[1],
    )
    top_zones = [(z, f) for z, f in zones_ranked if f > 0.12]
    zone_str = ", ".join(f"{z} ({f:.0%})" for z, f in top_zones[:3]) or "balanced"
    steps.append(f"→ Shot zones: {zone_str}")

    # ── Step 2: opponent zone defense gaps ───────────────────────────────
    opp_rim_acc    = opponent_defense.opp_at_rim_acc    or _LEAGUE["at_rim_acc"]
    opp_s_mid_acc  = opponent_defense.opp_short_mid_acc or _LEAGUE["short_mid_acc"]
    opp_l_mid_acc  = opponent_defense.opp_long_mid_acc  or _LEAGUE["long_mid_acc"]
    opp_three_acc  = opponent_defense.opp_3p_pct        or _LEAGUE["three_acc"]

    # Gap > 0 means opponent is weaker than league avg in that zone
    rim_gap      = opp_rim_acc   / _LEAGUE["at_rim_acc"]    - 1.0
    s_mid_gap    = opp_s_mid_acc / _LEAGUE["short_mid_acc"] - 1.0
    l_mid_gap    = opp_l_mid_acc / _LEAGUE["long_mid_acc"]  - 1.0
    three_gap    = opp_three_acc / _LEAGUE["three_acc"]     - 1.0

    opp_notes: list[str] = []
    if abs(rim_gap) > 0.015:
        strength = "weak" if rim_gap > 0 else "strong"
        opp_notes.append(f"at-rim {strength} ({opp_rim_acc:.1%})")
    if abs(three_gap) > 0.015:
        strength = "weak" if three_gap > 0 else "strong"
        opp_notes.append(f"3PT {strength} ({opp_three_acc:.1%})")
    if opp_l_mid_acc > 0 and abs(l_mid_gap) > 0.02:
        strength = "weak" if l_mid_gap > 0 else "strong"
        opp_notes.append(f"long-mid {strength} ({opp_l_mid_acc:.1%})")
    if opp_s_mid_acc > 0 and abs(s_mid_gap) > 0.02:
        strength = "weak" if s_mid_gap > 0 else "strong"
        opp_notes.append(f"short-mid {strength} ({opp_s_mid_acc:.1%})")

    if opp_notes:
        steps.append(f"→ Opp defense: {', '.join(opp_notes[:3])}")
    else:
        steps.append("→ Opp defense: average across all zones")

    # ── Step 3: cross-match exploitation score ──────────────────────────
    # Weighted sum of zone gaps, weighted by player's usage in each zone
    weighted_gap = (
        at_rim    * rim_gap
        + short_mid * s_mid_gap
        + long_mid  * l_mid_gap
        + three_rate * three_gap
    )

    exploit_notes: list[str] = []
    if at_rim > 0.22 and rim_gap > 0.015:
        exploit_notes.append(f"rim exploit ({at_rim:.0%} usage, {opp_rim_acc:.1%} allowed)")
    if three_rate > 0.22 and three_gap > 0.015:
        exploit_notes.append(f"3PT gap ({three_rate:.0%} usage, {opp_three_acc:.1%} allowed)")
    if (long_mid > 0.14 or short_mid > 0.12) and (l_mid_gap > 0.025 or s_mid_gap > 0.025):
        exploit_notes.append("mid-range gap")

    coverage_notes: list[str] = []
    if at_rim > 0.22 and rim_gap < -0.015:
        coverage_notes.append(f"shuts down rim ({opp_rim_acc:.1%})")
    if three_rate > 0.22 and three_gap < -0.015:
        coverage_notes.append(f"shuts down 3PT ({opp_three_acc:.1%})")

    if exploit_notes:
        steps.append(f"+ Zone match: {', '.join(exploit_notes[:2])}")
    elif coverage_notes:
        steps.append(f"- Opponent closes primary zones: {', '.join(coverage_notes[:2])}")

    # ── Step 4: foul / FT downstream effect ─────────────────────────────
    foul_boost = 0.0
    if at_rim > 0.25 and player.ft_rate > 0.25:
        opp_fta = opponent_defense.opp_fta or _LEAGUE["opp_fta_pg"]
        foul_excess = (opp_fta - _LEAGUE["opp_fta_pg"]) / _LEAGUE["opp_fta_pg"]
        if foul_excess > 0.04:
            foul_boost = foul_excess * player.ft_rate * 0.4
            steps.append(
                f"+ Foul/bonus factor: opp allows {opp_fta:.1f} FTA/game, "
                f"player FT rate {player.ft_rate:.2f} (P&R/drive exploitation)"
            )

    # ── Final factor ─────────────────────────────────────────────────────
    if prop_type == "3pm":
        # For 3PM props, weight three_gap much more heavily
        base_factor = 1.0 + three_gap * 1.0 + weighted_gap * 0.5
    else:
        # Amplify: weighted_gap of 0.05 → ~+10% factor
        base_factor = 1.0 + weighted_gap * 2.0

    factor = base_factor + foul_boost
    factor = max(0.82, min(factor, 1.22))

    # Summary line
    if factor >= 1.10:
        summary = f"+ Strong zone exploitation (x{factor:.3f}): multiple defensive gaps found"
    elif factor >= 1.04:
        summary = f"+ Favorable zone matchup (x{factor:.3f})"
    elif factor <= 0.90:
        summary = f"- Opponent covers player's primary zones (x{factor:.3f})"
    elif factor <= 0.96:
        summary = f"- Unfavorable zone matchup (x{factor:.3f})"
    else:
        summary = f"~ Neutral zone matchup (x{factor:.3f})"

    steps.insert(0, summary)
    return factor, steps
