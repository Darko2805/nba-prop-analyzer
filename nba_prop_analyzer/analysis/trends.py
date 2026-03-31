from typing import List, Optional
from ..data.models import PlayerStats
from ..data.bbref_scraper import GameLog, get_recent_games, get_stat_from_games

# Manual trend overrides set by CLI --trend flag
_manual_trends = {}


def set_manual_trend(player_name: str, factor: float) -> None:
    _manual_trends[player_name.lower()] = factor


def estimate_trend_factor(
    player: PlayerStats,
    prop_type: str,
    game_logs: Optional[List[GameLog]] = None,
) -> tuple:
    """
    Returns (multiplicative_factor, explanation_string).

    If game logs are available, computes trend from last 5 games vs season average.
    Otherwise falls back to manual override or neutral.
    """
    # Manual override takes priority if set
    manual = _manual_trends.get(player.name.lower())
    if manual is not None and manual != 1.0:
        diff_pct = (manual - 1.0) * 100
        if manual > 1.0:
            note = f"+ Manual trend override: {diff_pct:+.1f}% (hot streak)"
        else:
            note = f"- Manual trend override: {diff_pct:+.1f}% (cold streak)"
        return manual, note

    # Use real game logs if available
    if game_logs and len(game_logs) >= 5:
        return _compute_trend_from_logs(player, prop_type, game_logs)

    return 1.0, "~ Trend: neutral (no game logs available, use --trend for override)"


def _compute_trend_from_logs(
    player: PlayerStats,
    prop_type: str,
    game_logs: List[GameLog],
) -> tuple:
    """Compare last 5 games vs last 15 games (or season) to detect hot/cold streaks."""
    last_5 = get_recent_games(game_logs, 5)
    last_5_vals = get_stat_from_games(last_5, prop_type)

    # Use last 15 as the "baseline window" (or all games if fewer)
    baseline_games = get_recent_games(game_logs, 15)
    baseline_vals = get_stat_from_games(baseline_games, prop_type)

    if not last_5_vals or not baseline_vals:
        return 1.0, "~ Trend: insufficient game data"

    last_5_avg = sum(last_5_vals) / len(last_5_vals)
    baseline_avg = sum(baseline_vals) / len(baseline_vals)

    if baseline_avg == 0:
        return 1.0, "~ Trend: baseline average is zero"

    # Trend factor: how much the last 5 deviates from the longer window
    raw_ratio = last_5_avg / baseline_avg

    # Dampen the trend effect (don't let a 5-game sample swing too much)
    # Use 50% of the raw deviation
    dampened_factor = 1.0 + (raw_ratio - 1.0) * 0.5

    # Clamp to reasonable range
    dampened_factor = max(0.85, min(dampened_factor, 1.15))

    diff_pct = (dampened_factor - 1.0) * 100
    stat_label = _stat_label(prop_type)

    if dampened_factor > 1.03:
        note = (
            f"+ Hot streak: L5 avg {last_5_avg:.1f} {stat_label} "
            f"vs L15 avg {baseline_avg:.1f} ({diff_pct:+.1f}%)"
        )
    elif dampened_factor < 0.97:
        note = (
            f"- Cold streak: L5 avg {last_5_avg:.1f} {stat_label} "
            f"vs L15 avg {baseline_avg:.1f} ({diff_pct:+.1f}%)"
        )
    else:
        note = (
            f"~ Trending steady: L5 avg {last_5_avg:.1f} {stat_label} "
            f"vs L15 avg {baseline_avg:.1f}"
        )

    return dampened_factor, note


def get_game_log_summary(
    game_logs: List[GameLog],
    prop_type: str,
    n: int = 5,
) -> Optional[dict]:
    """Return a summary dict of the last N games for display."""
    recent = get_recent_games(game_logs, n)
    if not recent:
        return None

    vals = get_stat_from_games(recent, prop_type)
    if not vals:
        return None

    avg = sum(vals) / len(vals)
    min_val = min(vals)
    max_val = max(vals)

    return {
        "games": n,
        "values": vals,
        "avg": avg,
        "min": min_val,
        "max": max_val,
        "dates": [g.date for g in recent],
        "opponents": [g.opponent for g in recent],
    }


def _stat_label(prop_type: str) -> str:
    return {
        "points": "PTS",
        "rebounds": "REB",
        "assists": "AST",
        "3pm": "3PM",
        "pra": "PRA",
    }.get(prop_type, prop_type.upper())
