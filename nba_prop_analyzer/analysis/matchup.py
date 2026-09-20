from __future__ import annotations

from ..data.models import PlayerStats, TeamProfile, OpponentDefense
from .severity import weakness_percentile, severity_bonus

_DRTG_SEVERITY_SCALE = 0.09
_DRTG_SEVERITY_EXPONENT = 1.6

_FG_MISS_SEVERITY_SCALE = 0.08
_FG_MISS_SEVERITY_EXPONENT = 1.6

_APG_SEVERITY_SCALE = 0.08
_APG_SEVERITY_EXPONENT = 1.6

_TOPG_SEVERITY_SCALE = 0.06
_TOPG_SEVERITY_EXPONENT = 1.6

_STEAL_SEVERITY_SCALE = 0.06
_STEAL_SEVERITY_EXPONENT = 1.6
_DEADBALL_SEVERITY_SCALE = 0.04
_DEADBALL_SEVERITY_EXPONENT = 1.6


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
        # Real scraped value (TeamRankings' "Opponent Effective FG%" page)
        # when available; falls back to a derived estimate from FG%+3PM/FGA
        # for the rare case the scrape failed for this team.
        opp_fga = opponent_defense.opp_fga
        opp_efg_pct = opponent_defense.opp_efg_pct or (
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
        if all_opponent_defenses:
            opp_efg_population = [
                o.opp_efg_pct or (o.opp_fg_pct + 0.5 * (o.opp_3pm / o.opp_fga) if o.opp_fga > 0 else 0.0)
                for o in all_opponent_defenses.values()
            ]
            opp_efg_population = [v for v in opp_efg_population if v > 0]
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
        # Two components: how many assists the opponent's defense directly
        # allows (a fairly blunt team-level number), and how much ball
        # pressure it applies -- opponent turnovers forced. More forced
        # turnovers means more possessions end before a shot ever goes up,
        # which is a prerequisite for an assist, so it pulls the OPPOSITE
        # direction from the AST-allowed ratio: a defense can force a lot
        # of turnovers and still be generous with assists on whatever
        # possessions DO convert, but fewer possessions reaching a shot at
        # all should modestly suppress the assist opportunity. Both sides
        # ranked against the league.
        avg_apg = 25.0  # league average team assists allowed
        opp_apg = opponent_defense.opp_apg or avg_apg
        apg_ratio = opp_apg / max(avg_apg, 1)

        avg_topg = 13.5  # league average team turnovers forced (matches the turnovers branch below)
        opp_topg = opponent_defense.opp_topg or avg_topg
        pressure_ratio = avg_topg / max(opp_topg, 1)

        base_factor = apg_ratio * pressure_ratio

        apg_severity = 0.0
        apg_rank = apg_n = 0
        apg_pct = 0.5
        if all_opponent_defenses and opp_apg > 0:
            population = [o.opp_apg for o in all_opponent_defenses.values() if o.opp_apg > 0]
            apg_pct, apg_rank, apg_n = weakness_percentile(opp_apg, population)
            apg_severity = severity_bonus(apg_pct, scale=_APG_SEVERITY_SCALE, exponent=_APG_SEVERITY_EXPONENT)

        topg_severity = 0.0
        topg_rank = topg_n = 0
        topg_pct = 0.5
        if all_opponent_defenses and opp_topg > 0:
            population = [o.opp_topg for o in all_opponent_defenses.values() if o.opp_topg > 0]
            topg_pct, topg_rank, topg_n = weakness_percentile(opp_topg, population)
            # Direction inverted relative to every other stat this engine
            # ranks: a HIGH forced-turnover rank is BAD for assist
            # opportunity, not good, so the sign is flipped.
            topg_severity = -severity_bonus(topg_pct, scale=_TOPG_SEVERITY_SCALE, exponent=_TOPG_SEVERITY_EXPONENT)

        factor = base_factor + apg_severity + topg_severity
        factor = max(0.85, min(factor, 1.15))

        if apg_n >= 10 and (apg_pct <= 1 / 3 or apg_pct >= 2 / 3) and abs(apg_severity) > 0.015:
            tier = "bottom third" if apg_severity > 0 else "top third"
            note = f"{'+' if apg_severity > 0 else '-'} {opponent_defense.abbreviation} allows {opp_apg:.1f} AST/game — ranks {apg_rank}/{apg_n} in the league ({tier})"
        elif topg_n >= 10 and (topg_pct <= 1 / 3 or topg_pct >= 2 / 3) and abs(topg_severity) > 0.015:
            # tier describes where the STAT itself ranks (top third = most
            # ball pressure, ascending by raw TOV forced); the effect on
            # assists runs the opposite way, which is what topg_severity's
            # sign (and the wording below) captures instead.
            tier = "top third" if topg_pct >= 2 / 3 else "bottom third"
            note = f"{'-' if topg_severity < 0 else '+'} {opponent_defense.abbreviation} forces {opp_topg:.1f} TOV/game — ranks {topg_rank}/{topg_n} in ball pressure ({tier}), {'fewer' if topg_severity < 0 else 'more'} possessions reach a shot"
        elif factor > 1.02:
            note = f"+ Opponent allows {opp_apg:.1f} AST/game (above avg)"
        elif factor < 0.98:
            note = f"- Opponent limits assists to {opp_apg:.1f}/game"
        else:
            note = f"~ Neutral assist matchup"

    elif prop_type == "turnovers":
        # Split into two distinct defensive identities instead of one
        # blended forced-turnover ratio: steals (active ball pressure --
        # a scheme that actively hunts takeaways) and dead-ball turnovers
        # (travels, bad passes out of bounds, offensive fouls -- forced
        # less by the defense's own pressure and more by whatever the
        # opponent's own sloppiness hands them). Both push the SAME
        # direction for a turnovers prop (either one means more expected
        # turnovers), unlike assists' AST-allowed/TOV-forced pair which
        # pull opposite ways -- so each is ranked against the league
        # independently and both bonuses add on top of the same base
        # ratio, rather than one inverting the other.
        avg_topg = 13.5  # league average team turnovers forced per game
        live = opponent_defense.opp_live_topg
        dead = opponent_defense.opp_dead_topg
        total = live + dead
        base_factor = total / max(avg_topg, 1) if total > 0 else 1.0

        live_severity = 0.0
        live_rank = live_n = 0
        live_pct = 0.5
        if all_opponent_defenses and live > 0:
            population = [o.opp_live_topg for o in all_opponent_defenses.values() if o.opp_live_topg > 0]
            live_pct, live_rank, live_n = weakness_percentile(live, population)
            live_severity = severity_bonus(live_pct, scale=_STEAL_SEVERITY_SCALE, exponent=_STEAL_SEVERITY_EXPONENT)

        dead_severity = 0.0
        dead_rank = dead_n = 0
        dead_pct = 0.5
        if all_opponent_defenses and dead > 0:
            population = [o.opp_dead_topg for o in all_opponent_defenses.values() if o.opp_dead_topg > 0]
            dead_pct, dead_rank, dead_n = weakness_percentile(dead, population)
            dead_severity = severity_bonus(dead_pct, scale=_DEADBALL_SEVERITY_SCALE, exponent=_DEADBALL_SEVERITY_EXPONENT)

        factor = base_factor + live_severity + dead_severity
        factor = max(0.85, min(factor, 1.15))

        if live_n >= 10 and (live_pct <= 1 / 3 or live_pct >= 2 / 3) and abs(live_severity) > 0.01:
            tier = "top third" if live_pct >= 2 / 3 else "bottom third"
            note = f"{'+' if live_severity > 0 else '-'} {opponent_defense.abbreviation} forces {live:.1f} steals/game — ranks {live_rank}/{live_n} in active ball pressure ({tier})"
        elif dead_n >= 10 and (dead_pct <= 1 / 3 or dead_pct >= 2 / 3) and abs(dead_severity) > 0.01:
            tier = "top third" if dead_pct >= 2 / 3 else "bottom third"
            note = f"{'+' if dead_severity > 0 else '-'} {opponent_defense.abbreviation} forces {dead:.1f} dead-ball TOV/game — ranks {dead_rank}/{dead_n} ({tier}), opponent sloppiness more than pressure"
        elif factor > 1.03:
            note = f"+ High-pressure defense: opponent forces {total:.1f} TOV/game"
        elif factor < 0.97:
            note = f"- Low-pressure defense: opponent forces only {total:.1f} TOV/game"
        else:
            note = f"~ Neutral turnover matchup"
    else:
        factor = 1.0
        note = "~ Matchup data not applicable"

    return factor, note
