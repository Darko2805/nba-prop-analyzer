import os

# Seasons are named by the year they end in (2026 = 2025-26). The prior
# season is complete and static; the current one fills in game by game.
CURRENT_SEASON_YEAR = 2027
PRIOR_SEASON_YEAR = 2026

# Early-season numbers. Teams: weight on the current season = games / (games + K), so a team's own
# early games earn trust continuously (rosters turn over, hence the larger K).
TEAM_BLEND_K = 15
# Players use a simple rule instead, so the number a visitor sees can be explained in a sentence:
#   no games yet this season   -> last season's numbers
#   1 to 4 games               -> last season and this season so far count equally
#   5 or more games            -> this season's numbers alone
# A player with no last-season row (a rookie, or someone who missed the year) uses this season's real
# numbers alone from the first game: nothing is invented to stand in for a history they do not have.
PLAYER_FULL_TRUST_GAMES = 5
PLAYER_EARLY_WEIGHT = 0.5
DATABALLR_TEAM_URL = "https://api.databallr.com/v1/nba/teams/stats"
ESPN_OPPONENT_STATS_URL = "https://www.espn.com/nba/stats/team/_/view/opponent"
ESPN_TEAMS_API_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"

PROP_TYPES = [
    "points", "rebounds", "assists", "3pm", "pra",
    "fgm", "fga", "3pa", "turnovers",
    "pts_ast", "pts_reb", "ast_reb",
]

# Combo props are computed by running each component through the full
# pipeline separately and blending, weighted by each component's baseline
# share. pra is a genuine 3-way combo (not a first-class type sharing
# points' factors directly) so that e.g. shot-zone exploitation, which
# only speaks to the scoring third of PRA, is naturally diluted to
# whatever share of PRA's baseline actually comes from points, rather
# than carrying its full points-calibrated strength into the combined number.
COMBO_PROP_TYPES = {
    "pts_ast": ("points", "assists"),
    "pts_reb": ("points", "rebounds"),
    "ast_reb": ("assists", "rebounds"),
    "pra": ("points", "rebounds", "assists"),
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

# Bump when /terms or /privacy change materially; recorded with each signup.
LEGAL_VERSION = "2026-10-07"
MIN_AGE = 18

# basketball-reference blocks the production host's IP (403), so a live scrape
# inside a web request can only waste time there: every call waits out a 3 s
# rate limit behind a global lock and then fails. Production therefore runs on
# the committed snapshot alone; a player missing from it gets season averages.
# Local development (no FLASK_SECRET_KEY) still falls back to live fetches, and
# ALLOW_LIVE_BBREF=1 forces them on anywhere.
LIVE_BBREF_FALLBACK = os.environ.get("ALLOW_LIVE_BBREF") == "1" or not os.environ.get("FLASK_SECRET_KEY")
