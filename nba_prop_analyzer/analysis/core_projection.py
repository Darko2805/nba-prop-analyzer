"""
Minutes and form (the trend slot), and how strongly the opponent factors apply.

The model keeps its eight factors. Two things were resized after a walk-forward backtest
(scripts/backtest.py, 7,944 projections, Oct 20 - Nov 14 2026) showed the factors at full strength
made the projection 4-7% LESS accurate than the plain season average on points, rebounds, assists
and 3PM: the opponent factors now move the number a quarter as far (FACTOR_STRENGTH), and the trend
slot became the estimate below, the only thing tested that beat the average on all four props in
both halves of the sample.

Two ideas, both measured rather than assumed:
  * A player's per-minute rate this season is steadied with last season's, which counts as
    RATE_PRIOR_GAMES games' worth of minutes. Early on last season dominates; by mid-season it
    barely matters. (Dropping last season outright at game five over-reacted: at ~8 games played
    the best-fitting mix was still roughly 60-70% this season, 30-40% last.)
  * Tonight's minutes are expected to look like the last five games', nudged slightly toward the
    player's usual load. A role change shows up in minutes before it shows up in the average.

Re-run the backtest before changing either constant.
"""
from __future__ import annotations

from ..data.bbref_scraper import get_stat_from_games

RATE_PRIOR_GAMES = 8        # last season's per-minute rate counts as this many games
MINUTES_PRIOR_GAMES = 1     # the usual load counts as this many games next to the last five
RECENT_GAMES = 5
MIN_PRIOR_GAMES = 10        # fewer than this last season is not a history worth anchoring to
# How much of each opponent factor's swing is kept. At full size the eight multiplied factors made the
# projection 4-7% less accurate than the season average; at a quarter they keep their direction and
# their say, and the projection beats the average on all four props.
FACTOR_STRENGTH = 0.25

# Level: across 3,700 projections for players with 6+ games, results came in below the projection by
# a steady margin (points 6%, rebounds 6%, assists 7%, 3PM 1%), in both halves of the sample. Nights
# cut short by injury, foul trouble and blowouts are part of every real outcome and of no average
# built from recent full games. Applied a little short of the measured gap.
LEVEL = {"points": 0.95, "rebounds": 0.95, "assists": 0.94, "3pm": 1.0}


def level_for(prop_type: str, component_baselines: dict | None = None) -> float:
    if component_baselines:
        total = sum(component_baselines.values())
        return sum(LEVEL.get(c, 1.0) * v for c, v in component_baselines.items()) / total if total > 0 else 1.0
    return LEVEL.get({"fgm": "points", "fga": "points", "3pa": "3pm"}.get(prop_type, prop_type), 1.0)


def _played(games) -> list:
    return [g for g in (games or []) if (g.minutes or 0) > 0]


def core_estimate(prop_type: str, current_logs, prior_logs):
    """
    (estimate, details) for one stat, or (None, {}) when there are no usable games at all.
    details: expected_minutes, recent_minutes, usual_minutes, games (this season).
    """
    cur = _played(current_logs)
    prior = _played(prior_logs)
    if len(prior) < MIN_PRIOR_GAMES:
        prior = []
    if not cur and not prior:
        return None, {}

    cur_min = sum(g.minutes for g in cur)
    cur_stat = sum(get_stat_from_games(cur, prop_type))
    if prior:
        prior_min = sum(g.minutes for g in prior)
        prior_rate = sum(get_stat_from_games(prior, prop_type)) / prior_min
        usual = prior_min / len(prior)
        anchor_min = RATE_PRIOR_GAMES * usual
        rate = (cur_stat + prior_rate * anchor_min) / (cur_min + anchor_min)
    else:
        rate = cur_stat / cur_min
        usual = cur_min / len(cur)

    recent = [g.minutes for g in cur[-RECENT_GAMES:]]
    expected = (sum(recent) + MINUTES_PRIOR_GAMES * usual) / (len(recent) + MINUTES_PRIOR_GAMES)
    return rate * expected, {
        "expected_minutes": expected,
        "recent_minutes": sum(recent) / len(recent) if recent else None,
        "usual_minutes": usual,
        "games": len(cur),
    }


def core_note(ratio: float, details: dict) -> str:
    """One line for the breakdown: what moved the number off the season average, in plain terms."""
    if not details:
        return "~ Minutes and form: no game logs for this player, the season average stands"
    sign = "+" if ratio > 1.02 else ("-" if ratio < 0.98 else "~")
    recent, usual = details.get("recent_minutes"), details["usual_minutes"]
    if recent is None:
        return f"~ Minutes and form: no games yet this season, expecting the usual {usual:.0f} minutes"
    games = "game" if details["games"] == 1 else "games"
    last = min(details["games"], RECENT_GAMES)
    return (f"{sign} Minutes and form: {recent:.1f} min over the last {last} {games if last == 1 else 'games'} "
            f"(usual {usual:.1f}), expecting {details['expected_minutes']:.1f} tonight at a per-minute rate "
            f"steadied with last season, set to the level real results have come in at")



def resize_factor(factor: float) -> float:
    """An opponent factor at the strength the results support: same direction, a quarter of the swing."""
    return 1.0 + FACTOR_STRENGTH * (factor - 1.0)
