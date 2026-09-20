from __future__ import annotations

from ..data.models import PlayerStats, TeamProfile, OpponentDefense
from .severity import weakness_percentile, severity_bonus

_DRTG_SEVERITY_SCALE = 0.09
_DRTG_SEVERITY_EXPONENT = 1.6

_FG_MISS_SEVERITY_SCALE = 0.08
_FG_MISS_SEVERITY_EXPONENT = 1.6


def calculate_matchup_factor(
    player: PlayerStats,
    player_team: TeamProfile,
    opponent_defense: OpponentDefense,
    league_avg: dict,
    prop_type: str,
    all_opponent_defenses: dict | None = None,
    all_team_profiles: dict | None = None,
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
        # Rebounds only exist because shots get missed, so this is built
        # around the two most mechanically direct drivers of miss rate --
        # more so than for points or assists, per Darko: opponent eFG%
        # (drives DEFENSIVE-rebound opportunity) and the player's own
        # team's shooting efficiency (drives OFFENSIVE-rebound opportunity,
        # since a team missing its own shots creates its own OREB chances).
        # DRTG was doing this job by proxy before, but it's diluted by
        # turnovers forced and free-throw efficiency, neither of which
        # produces a rebound. The two sides are blended by THIS player's
        # own actual OREB/DREB split (player.orpg / player.drpg), not a
        # generic assumption, since that split varies a lot by role.
        avg_efg_pct = 0.545  # ~league-average eFG%
        opp_fga = opponent_defense.opp_fga
        opp_efg_pct = (
            opponent_defense.opp_fg_pct + 0.5 * (opponent_defense.opp_3pm / opp_fga)
            if opp_fga > 0 else avg_efg_pct
        )
        # Lower opponent eFG% allowed -> more misses -> more DEFENSIVE rebounds
        def_miss_factor = avg_efg_pct / max(opp_efg_pct, 0.01)

        # True Shooting % is the closest already-available proxy for the
        # team's own eFG% (models.py has no team-level FGM/3PM to compute
        # a literal eFG%) -- it also folds in FT efficiency, which the
        # opponent side deliberately avoids, so this side is a looser fit.
        avg_ts = 0.565
        team_ts = player_team.ts_pct or avg_ts
        # Lower own-team shooting -> more of the team's own misses -> more OFFENSIVE rebounds
        off_miss_factor = avg_ts / max(team_ts, 0.01)

        total_reb = max(player.rpg, 0.1)
        dreb_share = player.drpg / total_reb if player.drpg > 0 else 0.7
        oreb_share = player.orpg / total_reb if player.orpg > 0 else 0.3

        base_factor = def_miss_factor * dreb_share + off_miss_factor * oreb_share

        def_severity = off_severity = 0.0
        def_rank = def_n = off_rank = off_n = 0
        def_pct = off_pct = 0.5
        if all_opponent_defenses and opp_fga > 0:
            opp_efg_population = [
                o.opp_fg_pct + 0.5 * (o.opp_3pm / o.opp_fga)
                for o in all_opponent_defenses.values() if o.opp_fga > 0
            ]
            def_pct, def_rank, def_n = weakness_percentile(opp_efg_pct, opp_efg_population)
            # Exploitable direction here is the OPPOSITE of every other
            # stat this engine ranks (lower eFG% allowed = more misses =
            # more boards), so the percentile is inverted before scoring.
            def_severity = severity_bonus(1.0 - def_pct, scale=_FG_MISS_SEVERITY_SCALE, exponent=_FG_MISS_SEVERITY_EXPONENT)
        if all_team_profiles:
            ts_population = [t.ts_pct for t in all_team_profiles.values() if t.ts_pct > 0]
            off_pct, off_rank, off_n = weakness_percentile(team_ts, ts_population)
            off_severity = severity_bonus(1.0 - off_pct, scale=_FG_MISS_SEVERITY_SCALE, exponent=_FG_MISS_SEVERITY_EXPONENT)

        factor = base_factor + dreb_share * def_severity + oreb_share * off_severity
        factor = max(0.85, min(factor, 1.15))

        if def_n >= 10 and (def_pct <= 1 / 3 or def_pct >= 2 / 3) and abs(def_severity) > 0.015:
            if def_severity > 0:
                note = f"+ {opponent_defense.abbreviation} defense forces misses: {opp_efg_pct:.1%} eFG allowed (ranks {def_rank}/{def_n} stingiest) — extra defensive-rebound chances"
            else:
                note = f"- {opponent_defense.abbreviation} defense allows efficient shooting: {opp_efg_pct:.1%} eFG allowed (ranks {def_rank}/{def_n} stingiest) — fewer defensive-rebound chances"
        elif off_n >= 10 and (off_pct <= 1 / 3 or off_pct >= 2 / 3) and abs(off_severity) > 0.015:
            if off_severity > 0:
                note = f"+ {player_team.abbreviation} shoots poorly ({team_ts:.1%} TS, ranks {off_rank}/{off_n}) — extra offensive-rebound chances for their own misses"
            else:
                note = f"- {player_team.abbreviation} shoots efficiently ({team_ts:.1%} TS, ranks {off_rank}/{off_n}) — fewer offensive-rebound chances"
        elif factor > 1.02:
            note = f"+ Shooting-efficiency matchup favors extra rebound chances"
        elif factor < 0.98:
            note = f"- Shooting-efficiency matchup limits rebound chances"
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
