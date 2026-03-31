CURRENT_SEASON_YEAR = 2026
DATABALLR_PLAYER_URL = "https://api.databallr.com/api/supabase/player_stats_with_metrics"
DATABALLR_TEAM_URL = "https://api.databallr.com/api/supabase/team_stats"
ESPN_OPPONENT_STATS_URL = "https://www.espn.com/nba/stats/team/_/view/opponent"
ESPN_TEAMS_API_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"

PROP_TYPES = ["points", "rebounds", "assists", "3pm", "pra"]

# Empirical coefficients of variation for NBA per-game stats
STAT_CV = {
    "points": 0.30,
    "rebounds": 0.35,
    "assists": 0.35,
    "3pm": 0.50,
    "pra": 0.22,
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
