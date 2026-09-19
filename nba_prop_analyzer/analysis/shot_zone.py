"""
Shot zone exploitation analysis.

Step-by-step evaluation:
  1. Identify player's primary shot zones by frequency
  2. Assess opponent's defensive efficiency in each zone vs league average
  3. Rank the opponent's defense in each zone against the rest of the league,
     so "bottom third of the league" is an actual checked condition rather
     than just "a bit below average"
  4. Cross-match: player's top zones vs opponent's weak zones, with a
     non-linear bonus once a gap is severe enough to land in the extreme
     thirds of the league
  5. Downstream effects: foul rate / FT exploitation if attacking weak rim defense

Returns a multiplicative factor and a list of annotated step strings.
"""

from __future__ import annotations

from ..data.models import PlayerStats, TeamProfile, OpponentDefense

# 2025-26 NBA approximate league-average zone benchmarks. Used as a fallback
# only when we don't have every team's data to rank against (see
# _zone_weakness below, which is what actually drives the exploitation score).
_LEAGUE = {
    "at_rim_acc":    0.645,   # ~64.5% at the rim
    "short_mid_acc": 0.415,   # ~41.5% short mid-range
    "long_mid_acc":  0.400,   # ~40.0% long mid-range
    "three_acc":     0.360,   # ~36.0% from three
    "opp_fta_pg":    25.0,    # ~25 FTA allowed per game
}

# How hard the extreme-mismatch bonus/penalty can swing a single zone's
# contribution (before usage-weighting), and how sharply it accelerates as
# the opponent's defense in that zone approaches dead-last (or best-in-league).
# These are the only two "hardcoded" numbers left in the severity curve
# itself — everything else is computed live from where the opponent actually
# ranks against the rest of the league, not a fixed ratio-to-average.
_SEVERITY_SCALE = 0.14
_SEVERITY_EXPONENT = 1.6


def _defense_rank(value: float, population: list[float]) -> tuple[int, int]:
    """
    1-indexed rank of `value` among `population` for a "lower is stingier
    defense" stat (accuracy allowed) — 1 = best defense in the league in this
    zone, n = most exploitable. Ties share the lower rank.
    """
    if not population:
        return 0, 0
    n = len(population)
    rank = sum(1 for v in population if v < value) + 1
    return rank, n


def _zone_weakness(value: float, field: str, all_opponent_defenses: dict) -> tuple[float, int, int]:
    """
    Where this opponent's allowed accuracy in one zone ranks against every
    other team's, this season — not against a fixed league-average number.
    Returns (percentile, rank, league_size): percentile 0.0 = the stingiest
    defense in the league in this zone, 1.0 = the most exploitable, 0.5 =
    dead average. Falls back to a neutral 0.5 when we don't have full-league
    data to rank against (e.g. a data source hiccup), so a missing input
    never accidentally reads as an extreme matchup.
    """
    population = [
        getattr(o, field, 0.0) for o in all_opponent_defenses.values()
        if getattr(o, field, 0.0) > 0
    ] if all_opponent_defenses else []
    rank, n = _defense_rank(value, population)
    if n <= 1:
        return 0.5, rank, n
    return (rank - 1) / (n - 1), rank, n


def _severity_bonus(weakness_pct: float) -> float:
    """
    Rewards (or penalizes) a zone matchup based on how far into the extreme
    thirds of the league the opponent's defense actually sits, not just
    whether it's above or below average. Zero at league-average (0.5),
    ramping up convexly toward the bottom third (positive = exploit) and top
    third (negative = shut down) — modest right at the boundary, accelerating
    hard only once the mismatch is genuinely severe (e.g. a bottom-3 defense),
    so a marginal gap isn't rewarded the same as a historic one.
    """
    centered = weakness_pct - 0.5  # -0.5 (best defense) .. +0.5 (worst defense)
    if centered == 0:
        return 0.0
    magnitude = min(abs(centered) * 2, 1.0)  # 0 at average, 1 at the extreme
    sign = 1.0 if centered > 0 else -1.0
    return sign * (magnitude ** _SEVERITY_EXPONENT) * _SEVERITY_SCALE


def calculate_shot_zone_exploitation(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    prop_type: str,
    all_opponent_defenses: dict | None = None,
) -> tuple[float, list[str], dict]:
    """
    Returns (multiplicative_factor, list_of_step_notes, zones).

    Factor range: 0.78 (opponent locks every zone, ranked among the league's
    stingiest) – 1.28 (multiple zone gaps exploited, some in the league's
    bottom third or worse). `all_opponent_defenses` should be every team's
    OpponentDefense (e.g. PropAnalyzer.opponent_defenses) so each zone can be
    ranked against the real league distribution instead of a fixed average;
    when omitted, the severity bonus is skipped and this behaves like the
    plain gap-vs-average calculation only.
    Steps contain '+'/'-'/'→' prefixed strings for the UI.
    zones is keyed by at_rim/short_mid/long_mid/three, each with the player's
    shot frequency in that zone, the opponent's accuracy allowed, the league
    average, and the gap between them (positive = exploitable) — enough to
    drive a half-court exploitation diagram in the UI. Empty when not applicable.
    """
    if prop_type not in ("points", "pra", "3pm", "fgm", "fga", "3pa"):
        return 1.0, ["~ Zone analysis not applicable for this prop"], {}

    all_opponent_defenses = all_opponent_defenses or {}

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

    # ── Step 3: where each zone actually ranks in the league ─────────────
    # This is what "bottom third" means in practice — not "a bit below
    # average", but an explicit rank against the other 29 teams this season.
    rim_pct,   rim_rank,   league_n = _zone_weakness(opp_rim_acc,   "opp_at_rim_acc",    all_opponent_defenses)
    s_mid_pct, s_mid_rank, _        = _zone_weakness(opp_s_mid_acc, "opp_short_mid_acc", all_opponent_defenses)
    l_mid_pct, l_mid_rank, _        = _zone_weakness(opp_l_mid_acc, "opp_long_mid_acc",  all_opponent_defenses)
    three_pct, three_rank, _        = _zone_weakness(opp_three_acc, "opp_3p_pct",        all_opponent_defenses)

    rim_severity    = _severity_bonus(rim_pct)
    s_mid_severity  = _severity_bonus(s_mid_pct)
    l_mid_severity  = _severity_bonus(l_mid_pct)
    three_severity  = _severity_bonus(three_pct)

    # ── Step 4: cross-match exploitation score ──────────────────────────
    # Weighted sum of zone gaps, weighted by player's usage in each zone
    weighted_gap = (
        at_rim    * rim_gap
        + short_mid * s_mid_gap
        + long_mid  * l_mid_gap
        + three_rate * three_gap
    )

    # Same usage-weighting, but for the non-linear extreme-mismatch bonus —
    # a zone the player barely shoots from doesn't get rewarded just because
    # the opponent happens to be historically bad there.
    weighted_severity = (
        at_rim    * rim_severity
        + short_mid * s_mid_severity
        + long_mid  * l_mid_severity
        + three_rate * three_severity
    )

    severity_notes: list[str] = []
    if league_n >= 10:
        if three_rate > 0.20 and three_pct >= 2 / 3:
            severity_notes.append(f"3PT defense ranks {three_rank}/{league_n} (bottom third) — extra exploit bonus")
        elif three_rate > 0.20 and three_pct <= 1 / 3:
            severity_notes.append(f"3PT defense ranks {three_rank}/{league_n} (top third) — extra shutdown penalty")
        if at_rim > 0.20 and rim_pct >= 2 / 3:
            severity_notes.append(f"rim defense ranks {rim_rank}/{league_n} (bottom third) — extra exploit bonus")
        elif at_rim > 0.20 and rim_pct <= 1 / 3:
            severity_notes.append(f"rim defense ranks {rim_rank}/{league_n} (top third) — extra shutdown penalty")
    if severity_notes:
        steps.append(f"→ League rank: {'; '.join(severity_notes[:2])}")

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

    # ── Step 5: foul / FT downstream effect ─────────────────────────────
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
    # weighted_severity/three_severity are the non-linear layer on top of the
    # plain gap-vs-average term: zero for an average defense, growing fast
    # only once a zone's rank is genuinely in the league's extreme thirds.
    if prop_type in ("3pm", "3pa"):
        # For 3-point props (makes or attempts), weight three_gap much more heavily
        base_factor = 1.0 + three_gap * 1.0 + weighted_gap * 0.5 + three_severity * 1.0 + weighted_severity * 0.5
    else:
        # Amplify: weighted_gap of 0.05 → ~+10% factor
        base_factor = 1.0 + weighted_gap * 2.0 + weighted_severity * 1.0

    factor = base_factor + foul_boost
    factor = max(0.78, min(factor, 1.28))

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

    zones = {
        "at_rim": {
            "label": "At Rim", "player_freq": round(at_rim, 3),
            "opp_acc": round(opp_rim_acc, 3), "league_acc": _LEAGUE["at_rim_acc"], "gap": round(rim_gap, 3),
            "rank": rim_rank, "league_size": league_n, "severity": round(rim_severity, 4),
        },
        "short_mid": {
            "label": "Short Mid", "player_freq": round(short_mid, 3),
            "opp_acc": round(opp_s_mid_acc, 3), "league_acc": _LEAGUE["short_mid_acc"], "gap": round(s_mid_gap, 3),
            "rank": s_mid_rank, "league_size": league_n, "severity": round(s_mid_severity, 4),
        },
        "long_mid": {
            "label": "Long Mid", "player_freq": round(long_mid, 3),
            "opp_acc": round(opp_l_mid_acc, 3), "league_acc": _LEAGUE["long_mid_acc"], "gap": round(l_mid_gap, 3),
            "rank": l_mid_rank, "league_size": league_n, "severity": round(l_mid_severity, 4),
        },
        "three": {
            "label": "3PT", "player_freq": round(three_rate, 3),
            "opp_acc": round(opp_three_acc, 3), "league_acc": _LEAGUE["three_acc"], "gap": round(three_gap, 3),
            "rank": three_rank, "league_size": league_n, "severity": round(three_severity, 4),
        },
    }
    return factor, steps, zones
