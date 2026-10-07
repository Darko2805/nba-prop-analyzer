"""
TeamRankings.com scraper for NBA opponent defensive stats.
All pages use a consistent static HTML table with data-sort attributes.
"""
import requests
from bs4 import BeautifulSoup
from ..cache import cache
from .team_mapping import normalize_team

_BASE = "https://www.teamrankings.com/nba/stat/"

# Map short display names used by TeamRankings to canonical forms
# normalize_team handles most; these cover edge cases
_TR_NAME_OVERRIDES = {
    "Okla City": "OKC",
    "LA Lakers": "LAL",
    "LA Clippers": "LAC",
    "Golden State": "GSW",
    "GS Warriors": "GSW",
    "NY Knicks": "NYK",
    "NJ Nets": "BKN",
    "NO Pelicans": "NOP",
    "New Orleans": "NOP",
    "SA Spurs": "SAS",
    "San Antonio": "SAS",
    "Phx Suns": "PHX",
}

STAT_PAGES = {
    "opp_ppg":    "opponent-points-per-game",
    "opp_3pm":    "opponent-three-pointers-made-per-game",
    "opp_3pa":    "opponent-three-pointers-attempted-per-game",
    "opp_3p_pct": "opponent-three-point-pct",
    "opp_rpg":    "opponent-total-rebounds-per-game",
    "opp_apg":    "opponent-assists-per-game",
    "opp_fta":    "opponent-free-throws-attempted-per-game",
    "opp_ftm":    "opponent-free-throws-made-per-game",
    "opp_efg_pct": "opponent-effective-field-goal-pct",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _resolve_abbr(team_text: str) -> str:
    """Convert TeamRankings display name to team abbreviation."""
    text = team_text.strip()
    if text in _TR_NAME_OVERRIDES:
        return _TR_NAME_OVERRIDES[text]
    abbr = normalize_team(text)
    return abbr or ""


def _fetch_stat_page(slug: str) -> tuple[dict[str, float], str]:
    """
    Fetch one TeamRankings stat page and return ({team_abbr: season_value},
    season_label). The label is the table's season column header: TeamRankings
    names a season by the year it STARTS in ("2025" = 2025-26), and the column
    shows whichever season it currently treats as current.
    Uses the data-sort attribute on <td> elements for clean numeric values.
    """
    url = _BASE + slug
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return {}, ""

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_=lambda c: c and "tr-table" in c)
    if not table:
        return {}, ""

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    label = headers[2] if len(headers) > 2 else ""

    tbody = table.find("tbody")
    if not tbody:
        return {}, label

    result = {}
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Team name is in the second cell (index 1)
        team_cell = cells[1]
        link = team_cell.find("a")
        team_text = link.get_text(strip=True) if link else team_cell.get_text(strip=True)
        abbr = _resolve_abbr(team_text)
        if not abbr:
            continue

        # Season value is in the third cell (index 2), use data-sort for raw float
        val_cell = cells[2]
        raw = val_cell.get("data-sort", "")
        try:
            val = float(raw)
        except (ValueError, TypeError):
            # Fallback to text, strip % sign
            txt = val_cell.get_text(strip=True).replace("%", "")
            try:
                val = float(txt)
            except (ValueError, TypeError):
                continue

        result[abbr] = val

    return result, label


def fetch_teamrankings_opponent_stats() -> tuple[dict[str, dict], str]:
    """
    Fetch all opponent defensive stats from TeamRankings for all 30 teams.
    Returns (stats keyed by team abbreviation, season_label) -- the label says
    which season those numbers are (see _fetch_stat_page), so callers can tell
    the new season's numbers from last season's.
    """
    cached = cache.get("teamrankings_opponent_stats")
    if cached is not None:
        return cached

    combined: dict[str, dict] = {}
    label = ""

    for stat_key, slug in STAT_PAGES.items():
        page_data, page_label = _fetch_stat_page(slug)
        label = label or page_label
        for abbr, val in page_data.items():
            if abbr not in combined:
                combined[abbr] = {}
            combined[abbr][stat_key] = val

    result = (combined, label)
    cache.set("teamrankings_opponent_stats", result)
    return result
