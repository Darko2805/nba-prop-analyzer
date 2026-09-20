from __future__ import annotations

import unicodedata

import requests

from ..cache import cache

TEAM_ABBR_MAP = {
    "Atlanta Hawks": "ATL", "Hawks": "ATL", "ATL": "ATL",
    "Boston Celtics": "BOS", "Celtics": "BOS", "BOS": "BOS",
    "Brooklyn Nets": "BKN", "Nets": "BKN", "BKN": "BKN", "BK": "BKN",
    "Charlotte Hornets": "CHA", "Hornets": "CHA", "CHA": "CHA", "CHO": "CHA",
    "Chicago Bulls": "CHI", "Bulls": "CHI", "CHI": "CHI",
    "Cleveland Cavaliers": "CLE", "Cavaliers": "CLE", "Cavs": "CLE", "CLE": "CLE",
    "Dallas Mavericks": "DAL", "Mavericks": "DAL", "Mavs": "DAL", "DAL": "DAL",
    "Denver Nuggets": "DEN", "Nuggets": "DEN", "DEN": "DEN",
    "Detroit Pistons": "DET", "Pistons": "DET", "DET": "DET",
    "Golden State Warriors": "GSW", "Warriors": "GSW", "GSW": "GSW", "GS": "GSW",
    "Houston Rockets": "HOU", "Rockets": "HOU", "HOU": "HOU",
    "Indiana Pacers": "IND", "Pacers": "IND", "IND": "IND",
    "LA Clippers": "LAC", "Clippers": "LAC", "LAC": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL", "Lakers": "LAL", "LAL": "LAL",
    "Memphis Grizzlies": "MEM", "Grizzlies": "MEM", "MEM": "MEM",
    "Miami Heat": "MIA", "Heat": "MIA", "MIA": "MIA",
    "Milwaukee Bucks": "MIL", "Bucks": "MIL", "MIL": "MIL",
    "Minnesota Timberwolves": "MIN", "Timberwolves": "MIN", "Wolves": "MIN", "MIN": "MIN",
    "New Orleans Pelicans": "NOP", "Pelicans": "NOP", "NOP": "NOP", "NO": "NOP",
    "New York Knicks": "NYK", "Knicks": "NYK", "NYK": "NYK", "NY": "NYK",
    "Oklahoma City Thunder": "OKC", "Thunder": "OKC", "OKC": "OKC",
    "Orlando Magic": "ORL", "Magic": "ORL", "ORL": "ORL",
    "Philadelphia 76ers": "PHI", "76ers": "PHI", "Sixers": "PHI", "PHI": "PHI",
    "Phoenix Suns": "PHX", "Suns": "PHX", "PHX": "PHX", "PHO": "PHX",
    "Portland Trail Blazers": "POR", "Trail Blazers": "POR", "Blazers": "POR", "POR": "POR",
    "Sacramento Kings": "SAC", "Kings": "SAC", "SAC": "SAC",
    "San Antonio Spurs": "SAS", "Spurs": "SAS", "SAS": "SAS", "SA": "SAS",
    "Toronto Raptors": "TOR", "Raptors": "TOR", "TOR": "TOR",
    "Utah Jazz": "UTA", "Jazz": "UTA", "UTA": "UTA", "UTAH": "UTA",
    "Washington Wizards": "WAS", "Wizards": "WAS", "WAS": "WAS", "WSH": "WAS",
}

ALL_TEAM_ABBRS = sorted(set(TEAM_ABBR_MAP.values()))

# Primary brand color per team, used to tint the shot-zone court with the
# player's own team rather than a generic accent color.
TEAM_COLORS = {
    "ATL": "#E03A3E", "BKN": "#000000", "BOS": "#007A33", "CHA": "#1D1160",
    "CHI": "#CE1141", "CLE": "#860038", "DAL": "#00538C", "DEN": "#0E2240",
    "DET": "#C8102E", "GSW": "#1D428A", "HOU": "#CE1141", "IND": "#002D62",
    "LAC": "#C8102E", "LAL": "#552583", "MEM": "#5D76A9", "MIA": "#98002E",
    "MIL": "#00471B", "MIN": "#0C2340", "NOP": "#0C2340", "NYK": "#006BB6",
    "OKC": "#007AC1", "ORL": "#0077C0", "PHI": "#006BB6", "PHX": "#1D1160",
    "POR": "#E03A3E", "SAC": "#5A2D81", "SAS": "#C4CED4", "TOR": "#CE1141",
    "UTA": "#002B5C", "WAS": "#002B5C",
}

TEAM_FULL_NAMES = {
    "ATL": "ATLANTA HAWKS", "BKN": "BROOKLYN NETS", "BOS": "BOSTON CELTICS",
    "CHA": "CHARLOTTE HORNETS", "CHI": "CHICAGO BULLS", "CLE": "CLEVELAND CAVALIERS",
    "DAL": "DALLAS MAVERICKS", "DEN": "DENVER NUGGETS", "DET": "DETROIT PISTONS",
    "GSW": "GOLDEN STATE WARRIORS", "HOU": "HOUSTON ROCKETS", "IND": "INDIANA PACERS",
    "LAC": "LA CLIPPERS", "LAL": "LOS ANGELES LAKERS", "MEM": "MEMPHIS GRIZZLIES",
    "MIA": "MIAMI HEAT", "MIL": "MILWAUKEE BUCKS", "MIN": "MINNESOTA TIMBERWOLVES",
    "NOP": "NEW ORLEANS PELICANS", "NYK": "NEW YORK KNICKS", "OKC": "OKLAHOMA CITY THUNDER",
    "ORL": "ORLANDO MAGIC", "PHI": "PHILADELPHIA 76ERS", "PHX": "PHOENIX SUNS",
    "POR": "PORTLAND TRAIL BLAZERS", "SAC": "SACRAMENTO KINGS", "SAS": "SAN ANTONIO SPURS",
    "TOR": "TORONTO RAPTORS", "UTA": "UTAH JAZZ", "WAS": "WASHINGTON WIZARDS",
}

# ESPN's public team-logo CDN slug per team (verified reachable, image/png).
# Hotlinked at render time rather than downloaded/stored, so we never host or
# redistribute the logo image ourselves.
TEAM_LOGO_SLUGS = {
    "ATL": "atl", "BKN": "bkn", "BOS": "bos", "CHA": "cha", "CHI": "chi",
    "CLE": "cle", "DAL": "dal", "DEN": "den", "DET": "det", "GSW": "gs",
    "HOU": "hou", "IND": "ind", "LAC": "lac", "LAL": "lal", "MEM": "mem",
    "MIA": "mia", "MIL": "mil", "MIN": "min", "NOP": "no", "NYK": "ny",
    "OKC": "okc", "ORL": "orl", "PHI": "phi", "PHX": "phx", "POR": "por",
    "SAC": "sac", "SAS": "sa", "TOR": "tor", "UTA": "utah", "WAS": "wsh",
}


def team_logo_url(team_abbr: str) -> str | None:
    slug = TEAM_LOGO_SLUGS.get(team_abbr)
    return f"https://a.espncdn.com/i/teamlogos/nba/500/{slug}.png" if slug else None


def _normalize_for_match(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _espn_roster(team_abbr: str) -> list[dict]:
    """Fetches a team's live ESPN roster (name + ESPN athlete id per player),
    cached for an hour so analyzing several players on the same team doesn't
    re-fetch. Returns [] on any failure — a missing headshot is never fatal."""
    slug = TEAM_LOGO_SLUGS.get(team_abbr)
    if not slug:
        return []
    cache_key = f"espn_roster_{team_abbr}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{slug}/roster",
            timeout=6,
        )
        resp.raise_for_status()
        roster = resp.json().get("athletes", [])
    except Exception:
        roster = []
    cache.set(cache_key, roster)
    return roster


def find_espn_player_id(player_name: str, team_abbr: str) -> str | None:
    """Looks up a player's ESPN athlete id by name within their team's live
    roster, so we can hotlink their official headshot without maintaining
    our own name-to-id mapping. Diacritic/case-insensitive, same approach as
    the bbref player matching elsewhere in this app."""
    target = _normalize_for_match(player_name)
    for athlete in _espn_roster(team_abbr):
        if _normalize_for_match(athlete.get("fullName", "")) == target:
            return athlete.get("id")
    return None


def espn_headshot_url(espn_player_id: str) -> str:
    return f"https://a.espncdn.com/i/headshots/nba/players/full/{espn_player_id}.png"


def _fetch_teams_playing_on(date) -> set:
    """
    Team abbreviations with a game on the given date, from ESPN's public
    scoreboard API. Unlike basketball-reference, this works live from
    Render (no 403), so no snapshot/pre-fetch pipeline is needed — cached
    per calendar date so a long-lived server still picks up a new day's
    game without a restart. Returns an empty set on any failure so a
    schedule hiccup never breaks an analysis.
    """
    date_str = date.strftime("%Y%m%d")
    cache_key = f"espn_scoreboard_{date_str}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    teams = set()
    try:
        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}",
            timeout=8,
        )
        resp.raise_for_status()
        for event in resp.json().get("events", []):
            competitors = (event.get("competitions") or [{}])[0].get("competitors", [])
            for c in competitors:
                raw_abbr = (c.get("team") or {}).get("abbreviation", "")
                abbr = normalize_team(raw_abbr) if raw_abbr else None
                if abbr:
                    teams.add(abbr)
    except Exception:
        teams = set()

    cache.set(cache_key, teams)
    return teams


def fetch_back_to_back_teams(today=None) -> set:
    """
    Team abbreviations that played YESTERDAY relative to `today` — i.e.
    candidates for "on the second night of a back-to-back" if they also
    have a game today.
    """
    import datetime as _datetime

    if today is None:
        today = _datetime.date.today()
    yesterday = today - _datetime.timedelta(days=1)
    return _fetch_teams_playing_on(yesterday)


def fetch_three_in_four_teams(today=None) -> set:
    """
    Team abbreviations with at least 2 games in the 3 nights before
    `today` — i.e. playing their 3rd game in a 4-night window tonight if
    they also have a game today. A milder, more cumulative fatigue
    condition than a literal back-to-back (and the two overlap often but
    not always — e.g. a team that played two nights ago and last night
    is both; a team that played three and one nights ago but rested
    yesterday hits this without being on a back-to-back tonight).
    """
    import datetime as _datetime
    from collections import Counter

    if today is None:
        today = _datetime.date.today()

    game_counts: Counter = Counter()
    for days_back in (1, 2, 3):
        day = today - _datetime.timedelta(days=days_back)
        for abbr in _fetch_teams_playing_on(day):
            game_counts[abbr] += 1

    return {abbr for abbr, count in game_counts.items() if count >= 2}


def normalize_team(name: str):
    name = name.strip()
    if name.upper() in TEAM_ABBR_MAP:
        return TEAM_ABBR_MAP[name.upper()]
    if name in TEAM_ABBR_MAP:
        return TEAM_ABBR_MAP[name]
    for key, abbr in TEAM_ABBR_MAP.items():
        if name.lower() in key.lower() or key.lower() in name.lower():
            return abbr
    return None
