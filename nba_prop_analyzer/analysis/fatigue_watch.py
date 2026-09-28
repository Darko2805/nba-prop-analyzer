"""
Turns each team's rolling two-week schedule load (see
nba_prop_analyzer.data.team_mapping.fetch_schedule_load) into a single
continuous fatigue read for the homepage's "Fatigue Watch" preview.

Ranked against the rest of the league the same way severity.py ranks
matchup factors -- a team with the 12th-busiest two weeks and one with
the 13th-busiest get almost the same score, rather than a fixed
"3+ games in 14 days = fatigued" cutoff putting them in different
buckets. Four inputs, each ranked as its own league percentile, then
blended: raw schedule intensity (games + back-to-backs), road-game
load, overtime minutes logged, and close-game count. Road load is
still the single heaviest weight (travel fatigue compounds on top of
the games themselves), with OT minutes and close games added as
smaller, real contributors rather than folded into the intensity
number -- a back-to-back and a back-to-back that also went to two
overtimes are not the same fatigue event.
"""

from __future__ import annotations

from .severity import weakness_percentile

_INTENSITY_WEIGHT = 0.30
_AWAY_WEIGHT = 0.40
_MINUTES_WEIGHT = 0.15
_CLOSE_GAMES_WEIGHT = 0.15


def compute_fatigue_scores(schedule_load: dict) -> dict:
    """
    schedule_load: {abbr: {"games": int, "away_games": int,
    "back_to_backs": int, "extra_minutes": int, "close_games": int}},
    as returned by fetch_schedule_load() -- one entry per team with at
    least one game in the window.

    Returns the same dict with a percentile (0..1, 0.5 = league-average)
    for each of the four inputs plus the blended "fatigue_score" -- 1.0
    is the most fatigued team in the league on that measure right now.
    """
    if not schedule_load:
        return {}

    intensity_population = [t["games"] + t["back_to_backs"] for t in schedule_load.values()]
    away_population = [t["away_games"] for t in schedule_load.values()]
    minutes_population = [t["extra_minutes"] for t in schedule_load.values()]
    close_games_population = [t["close_games"] for t in schedule_load.values()]

    scored = {}
    for abbr, team in schedule_load.items():
        intensity_raw = team["games"] + team["back_to_backs"]
        intensity_pct, _, _ = weakness_percentile(intensity_raw, intensity_population)
        away_pct, _, _ = weakness_percentile(team["away_games"], away_population)
        minutes_pct, _, _ = weakness_percentile(team["extra_minutes"], minutes_population)
        close_games_pct, _, _ = weakness_percentile(team["close_games"], close_games_population)

        fatigue_score = (
            _INTENSITY_WEIGHT * intensity_pct
            + _AWAY_WEIGHT * away_pct
            + _MINUTES_WEIGHT * minutes_pct
            + _CLOSE_GAMES_WEIGHT * close_games_pct
        )

        scored[abbr] = {
            **team,
            "intensity_percentile": round(intensity_pct, 3),
            "away_percentile": round(away_pct, 3),
            "minutes_percentile": round(minutes_pct, 3),
            "close_games_percentile": round(close_games_pct, 3),
            "fatigue_score": round(fatigue_score, 3),
        }
    return scored
