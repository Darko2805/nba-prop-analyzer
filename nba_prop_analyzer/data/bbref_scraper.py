"""
Basketball-Reference game log scraper.
Respects rate limits: 1 request per 3 seconds, caches aggressively.
"""
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Optional

from ..config import REQUEST_HEADERS
from ..cache import cache

# Rate limiter: track last request time
_last_request_time = 0.0
_RATE_LIMIT_SECONDS = 3.0


@dataclass
class GameLog:
    date: str
    opponent: str
    result: str
    minutes: float
    pts: int
    reb: int
    ast: int
    fg3: int
    fg3a: int
    orb: int
    drb: int
    fga: int
    fgm: int
    fta: int
    ftm: int


def _rate_limit():
    """Ensure at least 3 seconds between requests to basketball-reference."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_SECONDS:
        time.sleep(_RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


def _build_player_id(name: str) -> str:
    """
    Convert player name to basketball-reference ID format.
    Format: first 5 chars of last name + first 2 chars of first name + '01'
    Example: Stephen Curry -> curryst01, LeBron James -> jamesle01
    """
    # Strip diacritics
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in normalized if not unicodedata.combining(c))

    parts = ascii_name.strip().split()
    if len(parts) < 2:
        return ""

    first_name = parts[0].lower()
    last_name = parts[-1].lower()

    # Remove non-alpha characters
    first_name = "".join(c for c in first_name if c.isalpha())
    last_name = "".join(c for c in last_name if c.isalpha())

    # basketball-reference format: first 5 of last + first 2 of first + 01
    player_id = last_name[:5] + first_name[:2] + "01"
    return player_id


def _get_season_year() -> int:
    """Basketball-reference uses the ending year (2024-25 season = 2025)."""
    from ..config import CURRENT_SEASON_YEAR
    return CURRENT_SEASON_YEAR


def fetch_game_logs(player_name: str, season_year: Optional[int] = None) -> List[GameLog]:
    """
    Fetch game logs for a player from basketball-reference.
    Returns list of GameLog entries, most recent last.
    """
    if season_year is None:
        season_year = _get_season_year()

    cache_key = f"bbref_gamelog_{player_name}_{season_year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    player_id = _build_player_id(player_name)
    if not player_id:
        return []

    first_letter = player_id[0]
    url = f"https://www.basketball-reference.com/players/{first_letter}/{player_id}/gamelog/{season_year}"

    _rate_limit()

    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        if resp.status_code == 429:
            print("    [bbref] Rate limited, waiting 10s...")
            time.sleep(10)
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)

        if resp.status_code != 200:
            # Try alternate ID (e.g., 02 suffix for players sharing name patterns)
            alt_id = player_id[:-2] + "02"
            url = f"https://www.basketball-reference.com/players/{first_letter}/{alt_id}/gamelog/{season_year}"
            _rate_limit()
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
            if resp.status_code != 200:
                return []

    except requests.RequestException:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="player_game_log_reg")
    if not table:
        return []

    tbody = table.find("tbody")
    if not tbody:
        return []

    games = []
    for row in tbody.find_all("tr"):
        # Skip header rows interspersed in tbody
        if row.get("class") and "thead" in " ".join(row.get("class", [])):
            continue

        cells = {}
        for el in row.find_all(["td", "th"]):
            stat = el.get("data-stat", "")
            cells[stat] = el.get_text(strip=True)

        # Skip rows without points (DNP, inactive, etc.)
        pts_str = cells.get("pts", "")
        if not pts_str or not pts_str.isdigit():
            continue

        # Parse minutes (format: "36:35")
        mp_str = cells.get("mp", "0:00")
        try:
            parts = mp_str.split(":")
            minutes = int(parts[0]) + int(parts[1]) / 60 if len(parts) == 2 else float(parts[0])
        except (ValueError, IndexError):
            minutes = 0

        game = GameLog(
            date=cells.get("date", ""),
            opponent=cells.get("opp_name_abbr", ""),
            result=cells.get("game_result", ""),
            minutes=minutes,
            pts=int(cells.get("pts", 0)),
            reb=int(cells.get("trb", 0)),
            ast=int(cells.get("ast", 0)),
            fg3=int(cells.get("fg3", 0)),
            fg3a=int(cells.get("fg3a", 0)),
            orb=int(cells.get("orb", 0)),
            drb=int(cells.get("drb", 0)),
            fga=int(cells.get("fga", 0)),
            fgm=int(cells.get("fg", 0)),
            fta=int(cells.get("fta", 0)),
            ftm=int(cells.get("ft", 0)),
        )
        games.append(game)

    cache.set(cache_key, games)
    return games


def get_recent_games(games: List[GameLog], n: int = 10) -> List[GameLog]:
    """Return the last N games from the game log."""
    return games[-n:] if len(games) >= n else games


def get_stat_from_games(games: List[GameLog], prop_type: str) -> List[float]:
    """Extract the relevant stat values from a list of game logs."""
    values = []
    for g in games:
        if prop_type == "points":
            values.append(float(g.pts))
        elif prop_type == "rebounds":
            values.append(float(g.reb))
        elif prop_type == "assists":
            values.append(float(g.ast))
        elif prop_type == "3pm":
            values.append(float(g.fg3))
        elif prop_type == "pra":
            values.append(float(g.pts + g.reb + g.ast))
    return values
