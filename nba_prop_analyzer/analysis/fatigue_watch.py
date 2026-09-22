"""
Turns each team's rolling two-week schedule load (see
nba_prop_analyzer.data.team_mapping.fetch_schedule_load) into a single
continuous fatigue read for the homepage's "Fatigue Watch" preview.

Ranked against the rest of the league the same way severity.py ranks
matchup factors -- a team with the 12th-busiest two weeks and one with
the 13th-busiest get almost the same score, rather than a fixed
"3+ games in 14 days = fatigued" cutoff putting them in different
buckets. Road games are weighted more heavily than the raw games/
back-to-back count when blending into the final score, since travel
fatigue compounds on top of the games themselves.
"""

from __future__ import annotations

from .severity import weakness_percentile

_INTENSITY_WEIGHT = 0.45
_AWAY_WEIGHT = 0.55


def compute_fatigue_scores(schedule_load: dict) -> dict:
    """
    schedule_load: {abbr: {"games": int, "away_games": int,
    "back_to_backs": int}}, as returned by fetch_schedule_load() -- one
    entry per team with at least one game in the window.

    Returns the same dict with "intensity_percentile", "away_percentile"
    and "fatigue_score" added per team -- all 0..1, 0.5 = league-average
    two-week load, 1.0 = the busiest/most road-heavy team in the league
    right now.
    """
    if not schedule_load:
        return {}

    intensity_population = [t["games"] + t["back_to_backs"] for t in schedule_load.values()]
    away_population = [t["away_games"] for t in schedule_load.values()]

    scored = {}
    for abbr, team in schedule_load.items():
        intensity_raw = team["games"] + team["back_to_backs"]
        intensity_pct, _, _ = weakness_percentile(intensity_raw, intensity_population)
        away_pct, _, _ = weakness_percentile(team["away_games"], away_population)

        scored[abbr] = {
            **team,
            "intensity_percentile": round(intensity_pct, 3),
            "away_percentile": round(away_pct, 3),
            "fatigue_score": round(_INTENSITY_WEIGHT * intensity_pct + _AWAY_WEIGHT * away_pct, 3),
        }
    return scored
