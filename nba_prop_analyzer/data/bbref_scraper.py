"""
Basketball-Reference game log scraper.
Respects rate limits: 1 request per 3 seconds, caches aggressively.
"""
import json
import os
import re
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
    tov: int = 0


def _rate_limit():
    """Ensure at least 3 seconds between requests to basketball-reference."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_SECONDS:
        time.sleep(_RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _letters(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalpha())


def _id_stems(name: str) -> list:
    """
    The `last5 + first2` stems basketball-reference might use for this name, most likely first.
    Generational suffixes are dropped (the id of "Jaren Jackson Jr." starts with "jacks", not "jr"),
    and a two-word surname also gets a stem from both words ("Tristan Da Silva" -> "dasiltr").
    """
    normalized = unicodedata.normalize("NFKD", name)
    parts = "".join(c for c in normalized if not unicodedata.combining(c)).strip().split()
    while len(parts) > 1 and _letters(parts[-1]) in _NAME_SUFFIXES:
        parts.pop()
    if len(parts) < 2:
        return []
    first = _letters(parts[0])[:2]
    stems = [_letters(parts[-1])[:5] + first]
    if len(parts) >= 3:
        stems.append(_letters("".join(parts[1:]))[:5] + first)
    return list(dict.fromkeys(st for st in stems if len(st) >= 3))


def _build_player_id(name: str) -> str:
    """
    The usual basketball-reference id: first 5 letters of the surname + first 2 of the first name + '01'
    (Stephen Curry -> curryst01). Only a first guess: see resolve_player_id for the ones where NN differs.
    """
    stems = _id_stems(name)
    return stems[0] + "01" if stems else ""


# basketball-reference ids are `last5 + first2 + NN`, where NN counts everyone with the same
# letters (davisan01 is a retired Antonio Davis, davisan02 is Anthony Davis). Guessing "01"
# silently returns the wrong person's page, so the page's own name is checked, and an id found
# to differ from the guess is remembered in a small committed file so it is only looked up once.
_IDS_PATH = os.path.join(os.path.dirname(__file__), "snapshot", "player_ids.json")
_ids_cache = None
_ID_SUFFIXES = range(1, 8)


def _name_key(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[.'\u2019\-]", " ", s)).strip()


def _load_known_ids() -> dict:
    global _ids_cache
    if _ids_cache is None:
        try:
            with open(_IDS_PATH) as f:
                _ids_cache = json.load(f)
        except (OSError, ValueError):
            _ids_cache = {}
    return _ids_cache


def _remember_id(player_name: str, player_id: str) -> None:
    ids = _load_known_ids()
    if ids.get(player_name) == player_id or player_id == _build_player_id(player_name):
        return  # the plain guess needs no entry
    ids[player_name] = player_id
    try:
        os.makedirs(os.path.dirname(_IDS_PATH), exist_ok=True)
        with open(_IDS_PATH, "w") as f:
            json.dump(ids, f, indent=1, sort_keys=True)
    except OSError:
        pass  # read-only host: it just won't persist


def _h1_text(html: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def _page_is_for(html: str, player_name: str) -> bool:
    """True when the page's heading starts with this player's name ("Anthony Davis 2025-26 Game Log")."""
    heading, wanted = _name_key(_h1_text(html)), _name_key(player_name)
    return bool(wanted) and (heading == wanted or heading.startswith(wanted + " "))


def _http_get(url: str):
    """One polite GET; None on a network error. Retries once after a 429."""
    _rate_limit()
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        if resp.status_code == 429:
            print("    [bbref] Rate limited, waiting 10s...")
            time.sleep(10)
            _rate_limit()
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        return resp
    except requests.RequestException:
        return None


def resolve_player_id(player_name: str) -> str:
    """The player's real id, found on the main player pages ('' when no page has their name)."""
    for stem in _id_stems(player_name):
        for n in _ID_SUFFIXES:
            pid = f"{stem}{n:02d}"
            resp = _http_get(f"https://www.basketball-reference.com/players/{pid[0]}/{pid}.html")
            if resp is None:
                return ""        # network trouble or blocked: do not guess
            if resp.status_code == 404:
                break            # ids are consecutive: past the last one for this stem, try the next stem
            if resp.status_code == 200 and _page_is_for(resp.text, player_name):
                return pid
    return ""


def _fetch_player_section(player_name: str, section: str, season_year: int):
    """
    The player's `gamelog` or `shooting` page for a season, for the RIGHT person, or None.
    The usual id is tried first (one request); a page that names someone else triggers the lookup.
    """
    pid = _load_known_ids().get(player_name) or _build_player_id(player_name)
    if not pid:
        return None

    def url_for(player_id):
        return f"https://www.basketball-reference.com/players/{player_id[0]}/{player_id}/{section}/{season_year}"

    resp = _http_get(url_for(pid))
    if resp is None:
        return None
    if resp.status_code == 200 and _page_is_for(resp.text, player_name):
        return resp
    if resp.status_code not in (200, 404):
        return None          # blocked or erroring: do not start guessing
    real = resolve_player_id(player_name)
    if not real or real == pid:
        return None
    _remember_id(player_name, real)
    resp = _http_get(url_for(real))
    return resp if resp is not None and resp.status_code == 200 and _page_is_for(resp.text, player_name) else None


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

    resp = _fetch_player_section(player_name, "gamelog", season_year)
    if resp is None:
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
            tov=int(cells.get("tov") or 0),
        )
        games.append(game)

    cache.set(cache_key, games)
    return games


def fetch_shot_zone_profile(player_name: str, season_year: Optional[int] = None) -> dict:
    """
    Per-player shot-distance frequency breakdown, from the player's bbref
    shooting-splits page. Returns {} if the player/page/section isn't found —
    callers should treat that as "no data" and fall back accordingly.
    """
    if season_year is None:
        season_year = _get_season_year()

    cache_key = f"bbref_shotzone_{player_name}_{season_year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    resp = _fetch_player_section(player_name, "shooting", season_year)
    if resp is None:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="shooting")
    if table is None:
        return {}

    zone_fga = {}
    current_section = None
    for row in table.find_all("tr"):
        cells = {}
        for el in row.find_all(["td", "th"]):
            stat = el.get("data-stat", "")
            cells[stat] = el.get_text(strip=True)

        if cells.get("split_value") == "Value":
            continue  # section header row

        split_id = cells.get("split_id", "")
        if split_id:
            current_section = split_id

        if current_section == "Shot Distance":
            label = cells.get("split_value", "")
            fga_str = cells.get("fga", "")
            if not label or not fga_str:
                continue
            try:
                zone_fga[label] = float(fga_str)
            except ValueError:
                continue

    total = sum(zone_fga.values())
    if total <= 0:
        return {}

    at_rim = zone_fga.get("At Rim", 0.0)
    short_mid = zone_fga.get("3 to <10 ft", 0.0)
    long_mid = zone_fga.get("10 to <16 ft", 0.0) + zone_fga.get("16 ft to <3-pt", 0.0)

    profile = {
        "at_rim_freq": at_rim / total,
        "short_mid_freq": short_mid / total,
        "mid_range_freq": long_mid / total,
    }
    cache.set(cache_key, profile)
    return profile


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
        elif prop_type == "fgm":
            values.append(float(g.fgm))
        elif prop_type == "fga":
            values.append(float(g.fga))
        elif prop_type == "3pa":
            values.append(float(g.fg3a))
        elif prop_type == "turnovers":
            values.append(float(g.tov))
        elif prop_type == "pts_ast":
            values.append(float(g.pts + g.ast))
        elif prop_type == "pts_reb":
            values.append(float(g.pts + g.reb))
        elif prop_type == "ast_reb":
            values.append(float(g.ast + g.reb))
    return values
