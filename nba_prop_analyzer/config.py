CURRENT_SEASON_YEAR = 2026
DATABALLR_TEAM_URL = "https://api.databallr.com/api/supabase/team_stats"
ESPN_OPPONENT_STATS_URL = "https://www.espn.com/nba/stats/team/_/view/opponent"
ESPN_TEAMS_API_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"

PROP_TYPES = [
    "points", "rebounds", "assists", "3pm", "pra",
    "fgm", "fga", "3pa", "turnovers",
    "pts_ast", "pts_reb", "ast_reb",
]

# Two-stat combo props are computed by running each component through the full
# pipeline separately and blending, weighted by each component's baseline share.
# (pra is a 3-way combo but is simple/dominant enough to keep as its own
# first-class type below, same as before.)
COMBO_PROP_TYPES = {
    "pts_ast": ("points", "assists"),
    "pts_reb": ("points", "rebounds"),
    "ast_reb": ("assists", "rebounds"),
}

# Empirical coefficients of variation for NBA per-game stats
STAT_CV = {
    "points": 0.30,
    "rebounds": 0.35,
    "assists": 0.35,
    "3pm": 0.50,
    "pra": 0.22,
    "fgm": 0.28,
    "fga": 0.22,
    "3pa": 0.32,
    "turnovers": 0.45,
    "pts_ast": 0.26,
    "pts_reb": 0.27,
    "ast_reb": 0.32,
}

# Composite model weights
WEIGHTS = {
    "baseline": 0.40,
    "matchup": 0.25,
    "pace": 0.15,
    "opponent_profile": 0.12,
    "trend": 0.08,
}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
