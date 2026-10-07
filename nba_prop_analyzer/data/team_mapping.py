from __future__ import annotations

import datetime
import re
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
    # Served via our own /img proxy (see web/app.py) so a visitor's browser
    # never contacts ESPN's CDN directly.
    return f"/img/team/{slug}.png" if slug else None


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_for_match(name: str) -> str:
    """Diacritic/case-insensitive, and ignores a trailing generational suffix
    (ESPN says "Jimmy Butler III", basketball-reference says "Jimmy Butler")."""
    nfkd = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    parts = plain.replace(".", "").split()
    if len(parts) > 2 and parts[-1] in _NAME_SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


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
    return _espn_search_player_id(target)


def _espn_search_player_id(normalized_name: str) -> str | None:
    """
    Fallback for players whose team in our (season-stats) data no longer matches
    their live ESPN roster -- anyone traded or signed in the off-season, since
    our snapshot's team is the one they played for last season. One ESPN search
    request by name instead of fetching all 30 rosters. Misses are cached too
    ("" marker) so an unknown name isn't re-queried on every page load.
    """
    cache_key = f"espn_player_search_{normalized_name}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    player_id = ""
    try:
        resp = requests.get(
            "https://site.web.api.espn.com/apis/common/v3/search",
            params={"query": normalized_name, "limit": 5, "type": "player",
                    "sport": "basketball", "league": "nba"},
            timeout=6,
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            if item.get("league") == "nba" and _normalize_for_match(item.get("displayName", "")) == normalized_name:
                player_id = str(item.get("id", ""))
                break
    except Exception:
        player_id = ""
    cache.set(cache_key, player_id)
    return player_id or None


def espn_headshot_url(espn_player_id: str) -> str:
    return f"/img/player/{espn_player_id}.png"


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


def et_date():
    """Today's calendar date in US Eastern time (the timezone NBA schedules and
    game dates use), without needing a tz database on the host: a fixed UTC-5."""
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)).date()


def tonight_date():
    """The slate the site should call "tonight": stays on the previous Eastern
    date until ~6am ET so late West Coast games still count, instead of the
    list flipping to tomorrow at midnight."""
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=10)).date()


def _start_time_label(status_type: dict) -> str:
    state = status_type.get("state")
    if state == "in":
        return "Live"
    if state == "post":
        return "Final"
    m = re.search(r"(\d{1,2}:\d{2})\s*(AM|PM)\s*(E[SD]T)", status_type.get("shortDetail", ""))
    return f"{m.group(1)}{m.group(2)[0].lower()} ET" if m else "TBD"


def fetch_games_on(date) -> list | None:
    """
    The NBA slate for one Eastern-time calendar date, straight from ESPN's
    public scoreboard (the same source the schedule/fatigue features already
    use, and reachable from the host, unlike basketball-reference). Each game:
    {start_time, away_team, home_team, arena, season_type} with season_type
    1 = preseason, 2 = regular season, 3 = postseason.

    Returns None when ESPN can't be reached (and does not cache that), so a
    caller can tell "no games today" apart from "couldn't load the schedule".
    ESPN keys games by the Eastern date, so the evening games of a given day
    are all returned for that day even though they tip off after midnight UTC.
    """
    date_str = date.strftime("%Y%m%d")
    cache_key = f"espn_slate_{date_str}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}",
            timeout=8,
        )
        resp.raise_for_status()
        games = []
        for event in resp.json().get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            sides = {c.get("homeAway"): (c.get("team") or {}).get("displayName", "")
                     for c in competition.get("competitors", [])}
            if not sides.get("away") or not sides.get("home"):
                continue
            games.append({
                "start_time": _start_time_label((event.get("status") or {}).get("type") or {}),
                "away_team": sides["away"],
                "home_team": sides["home"],
                "arena": (competition.get("venue") or {}).get("fullName", ""),
                "season_type": (event.get("season") or {}).get("type", 2),
                "_sort": event.get("date", ""),
            })
        games.sort(key=lambda g: g["_sort"])
        for g in games:
            g.pop("_sort")
    except Exception:
        return None
    cache.set(cache_key, games)
    return games


def _fetch_team_games_on(date) -> dict:
    """
    Like _fetch_teams_playing_on, but keyed by team abbreviation with a
    dict of {home_away, ot_periods, margin} for that game. A rolling
    fatigue read needs more than "did they play": a team's recent road
    load, how many extra (overtime) minutes they've actually logged, and
    how many games went down to the wire -- plain "did they play" isn't
    enough here.

    ot_periods is 0 for a regulation game -- ESPN's own status.period is
    4 for regulation, 5+ for each overtime played (verified against a
    real OT game: PHI 128 @ HOU 122 on 2026-01-10 returned period=5).
    margin is the final score gap (0 if the game hasn't finished yet).
    """
    date_str = date.strftime("%Y%m%d")
    cache_key = f"espn_scoreboard_load_{date_str}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    games = {}
    try:
        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}",
            timeout=8,
        )
        resp.raise_for_status()
        for event in resp.json().get("events", []):
            # Preseason minutes are exhibition minutes, not fatigue.
            if (event.get("season") or {}).get("type") == 1:
                continue
            competition = (event.get("competitions") or [{}])[0]
            competitors = competition.get("competitors", [])
            period = (event.get("status") or {}).get("period", 0)
            ot_periods = max(0, period - 4)

            scores = []
            for c in competitors:
                try:
                    scores.append(int(c.get("score", 0)))
                except (TypeError, ValueError):
                    scores.append(0)
            margin = abs(scores[0] - scores[1]) if len(scores) == 2 else 0

            for c in competitors:
                raw_abbr = (c.get("team") or {}).get("abbreviation", "")
                abbr = normalize_team(raw_abbr) if raw_abbr else None
                if abbr:
                    games[abbr] = {
                        "home_away": c.get("homeAway", ""),
                        "ot_periods": ot_periods,
                        "margin": margin,
                    }
    except Exception:
        games = {}

    cache.set(cache_key, games)
    return games


def fetch_schedule_load(today=None, window_days: int = 14) -> dict:
    """
    Per-team rolling schedule load over the trailing `window_days` days
    (not including today): games played, how many were on the road, how
    many were back-to-backs (zero rest before that game), how many extra
    (overtime) minutes they've logged, and how many games came down to
    a close finish. This is the raw two-week workload behind a fatigue
    read, not just tonight's single rest day.

    extra_minutes is overtime-only (each OT period is 5 real minutes) --
    not an estimate of total minutes played, just the cumulative extra
    time a team's rotation has had to grind through beyond a normal
    48-minute game. close_games counts games decided by 6 points or
    fewer, on the idea that a string of nip-and-tuck finishes wears on a
    team's rotation (extended high-leverage possessions, heavier
    fourth-quarter minutes for starters) even when the final margin
    doesn't show it.

    Always measured as "the `window_days` days before `today`" rather
    than a stored date range, so calling this again tomorrow shifts the
    whole window forward on its own -- yesterday's games age out, no
    date bookkeeping required.
    """
    import datetime as _datetime
    from collections import defaultdict

    if today is None:
        today = _datetime.date.today()

    day_teams: dict = {}
    load: dict = defaultdict(lambda: {
        "games": 0, "away_games": 0, "back_to_backs": 0,
        "extra_minutes": 0, "close_games": 0,
    })

    for days_back in range(1, window_days + 1):
        day = today - _datetime.timedelta(days=days_back)
        games_on_day = _fetch_team_games_on(day)
        day_teams[day] = games_on_day
        for abbr, info in games_on_day.items():
            load[abbr]["games"] += 1
            if info["home_away"] == "away":
                load[abbr]["away_games"] += 1
            load[abbr]["extra_minutes"] += info["ot_periods"] * 5
            if 0 < info["margin"] <= 6:
                load[abbr]["close_games"] += 1

    for days_back in range(1, window_days):
        day = today - _datetime.timedelta(days=days_back)
        prev_day = day - _datetime.timedelta(days=1)
        for abbr in day_teams.get(day, {}):
            if abbr in day_teams.get(prev_day, {}):
                load[abbr]["back_to_backs"] += 1

    return dict(load)


def fetch_injuries() -> dict:
    """
    Real, current injury status per player, keyed by ESPN's own display
    name (e.g. "Jayson Tatum") -- {"status": "Day-To-Day", "comment": "..."}.
    Same site.api.espn.com family already used for the scoreboard above,
    so it works live from Render/Railway with no scraping. Cached for an
    hour via the existing SessionCache, same as everything else here.
    """
    cache_key = "espn_injuries"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {}
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
            timeout=8,
        )
        resp.raise_for_status()
        for team in resp.json().get("injuries", []):
            for inj in team.get("injuries", []):
                athlete = inj.get("athlete", {})
                name = athlete.get("displayName", "")
                if name:
                    result[name] = {
                        "status": inj.get("status", ""),
                        "comment": inj.get("shortComment", ""),
                    }
    except Exception:
        result = {}

    cache.set(cache_key, result)
    return result


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
