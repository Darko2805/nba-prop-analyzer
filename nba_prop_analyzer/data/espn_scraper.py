import requests
from ..config import ESPN_TEAMS_API_URL, REQUEST_HEADERS
from ..cache import cache
from .team_mapping import normalize_team


def fetch_espn_opponent_stats() -> dict[str, dict]:
    """
    Fetch per-game opponent stats for all 30 teams using ESPN's public API.
    Returns dict keyed by team abbreviation with defensive stats.
    """
    cached = cache.get("espn_opponent_stats")
    if cached is not None:
        return cached

    result: dict[str, dict] = {}

    # First get all 30 teams and their IDs
    try:
        resp = requests.get(ESPN_TEAMS_API_URL, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        cache.set("espn_opponent_stats", result)
        return result

    sports = data.get("sports", [{}])
    leagues = sports[0].get("leagues", [{}]) if sports else [{}]
    teams = leagues[0].get("teams", []) if leagues else []

    for t in teams:
        team_info = t.get("team", {})
        team_id = team_info.get("id")
        team_abbr_raw = team_info.get("abbreviation", "")
        abbr = normalize_team(team_abbr_raw) or team_abbr_raw

        if not team_id or not abbr:
            continue

        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/statistics"
            sresp = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
            if not sresp.ok:
                continue
            sdata = sresp.json()
        except Exception:
            continue

        categories = sdata.get("results", {}).get("stats", {}).get("categories", [])
        stats: dict[str, float] = {}
        for cat in categories:
            for s in cat.get("stats", []):
                name = s.get("name", "")
                val = s.get("value")
                if val is not None:
                    try:
                        stats[name] = float(val)
                    except (ValueError, TypeError):
                        pass

        if stats:
            result[abbr] = stats

    cache.set("espn_opponent_stats", result)
    return result
