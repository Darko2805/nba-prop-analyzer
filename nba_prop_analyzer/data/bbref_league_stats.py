"""
Basketball-Reference league-wide stats (bulk, one page per season).

Replaces databallr's player_stats_with_metrics endpoint, which now requires
signed requests we don't have (see git history for context). Uses the same
site, rate limiter, and cache as bbref_scraper.py's per-player game logs.
"""
import requests
from bs4 import BeautifulSoup

from ..config import REQUEST_HEADERS, CURRENT_SEASON_YEAR
from ..cache import cache
from .models import PlayerStats
from .team_mapping import normalize_team
from .bbref_scraper import _rate_limit


def _safe_float(text: str, default: float = 0.0) -> float:
    try:
        return float(text)
    except (ValueError, TypeError):
        return default


def _safe_int(text: str, default: int = 0) -> int:
    try:
        return int(text)
    except (ValueError, TypeError):
        return default


def fetch_all_players_per_game(season_year: int = CURRENT_SEASON_YEAR) -> list[PlayerStats]:
    """
    Bulk per-game stats for every player, from a single league page.
    Shot-zone frequency fields are left at 0.0 here; they're filled in
    lazily per-player (see bbref_scraper.fetch_shot_zone_profile) since
    bbref only exposes them on a per-player page, not in bulk.
    """
    cache_key = f"bbref_players_pergame_{season_year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"https://www.basketball-reference.com/leagues/NBA_{season_year}_per_game.html"
    _rate_limit()
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.find("table", id="per_game_stats")
    if table is None:
        return []

    players_by_name: dict[str, PlayerStats] = {}
    for row in table.find_all("tr"):
        if row.get("class") and "thead" in " ".join(row.get("class", [])):
            continue

        cells = {}
        for el in row.find_all(["td", "th"]):
            stat = el.get("data-stat", "")
            cells[stat] = el.get_text(strip=True)

        name = cells.get("name_display", "").strip()
        games = _safe_int(cells.get("games", "0"))
        if not name or games == 0:
            continue

        team_raw = cells.get("team_name_abbr", "").strip()

        if name in players_by_name:
            # Traded player: a later per-team row follows the combined "TOT" row.
            # Keep the TOT-derived season averages, just update their current team.
            if team_raw and team_raw != "TOT":
                players_by_name[name].team_abbr = normalize_team(team_raw) or team_raw
            continue

        fga_pg = _safe_float(cells.get("fga_per_g", "0"))
        fg3a_pg = _safe_float(cells.get("fg3a_per_g", "0"))
        fta_pg = _safe_float(cells.get("fta_per_g", "0"))

        players_by_name[name] = PlayerStats(
            name=name,
            team_abbr=normalize_team(team_raw) or team_raw,
            position=cells.get("pos", ""),
            games_played=games,
            mpg=_safe_float(cells.get("mp_per_g", "0")),
            ppg=_safe_float(cells.get("pts_per_g", "0")),
            apg=_safe_float(cells.get("ast_per_g", "0")),
            rpg=_safe_float(cells.get("trb_per_g", "0")),
            orpg=_safe_float(cells.get("orb_per_g", "0")),
            drpg=_safe_float(cells.get("drb_per_g", "0")),
            three_pm_pg=_safe_float(cells.get("fg3_per_g", "0")),
            three_pa_pg=fg3a_pg,
            three_pct=_safe_float(cells.get("fg3_pct", "0")),
            three_point_rate=fg3a_pg / fga_pg if fga_pg > 0 else 0.0,
            ft_rate=fta_pg / fga_pg if fga_pg > 0 else 0.0,
            fta_per100=0.0,
            ts_pct=0.0,
            tsa_per100=0.0,
            offensive_archetype="",
            at_rim_freq=0.0,
            mid_range_freq=0.0,
            o_dpm=0.0,
            d_dpm=0.0,
            ortg_on=0.0,
            drtg_on=0.0,
        )

    players = list(players_by_name.values())
    cache.set(cache_key, players)
    return players


# bbref's "3-10 ft" bucket maps to the model's "short mid-range";
# "10-16 ft" and "16 ft-3pt" are combined into the model's "long mid-range".
def fetch_opponent_zone_defense(season_year: int = CURRENT_SEASON_YEAR) -> dict[str, dict]:
    """
    Per-team opponent FG% allowed by shot distance, from the league shooting
    page's "Opponent Shooting" table. Only covers short/long mid-range —
    at-rim and 3PT opponent defense already come from databallr/TeamRankings.
    """
    cache_key = f"bbref_opp_zone_defense_{season_year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"https://www.basketball-reference.com/leagues/NBA_{season_year}.html"
    _rate_limit()
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.find("table", id="shooting-opponent")
    if table is None:
        return {}

    result: dict[str, dict] = {}
    for row in table.find_all("tr"):
        if row.get("class") and "thead" in " ".join(row.get("class", [])):
            continue

        cells = {}
        for el in row.find_all(["td", "th"]):
            stat = el.get("data-stat", "")
            cells[stat] = el.get_text(strip=True)

        team_raw = cells.get("team", "").strip().rstrip("*")
        if not team_raw:
            continue
        abbr = normalize_team(team_raw)
        if not abbr:
            continue

        short_mid_freq = _safe_float(cells.get("opp_pct_fga_03_10", "0"))
        short_mid_acc = _safe_float(cells.get("opp_fg_pct_03_10", "0"))
        mid_10_16_freq = _safe_float(cells.get("opp_pct_fga_10_16", "0"))
        mid_10_16_acc = _safe_float(cells.get("opp_fg_pct_10_16", "0"))
        mid_16_xx_freq = _safe_float(cells.get("opp_pct_fga_16_xx", "0"))
        mid_16_xx_acc = _safe_float(cells.get("opp_fg_pct_16_xx", "0"))

        long_mid_freq = mid_10_16_freq + mid_16_xx_freq
        long_mid_acc = (
            (mid_10_16_freq * mid_10_16_acc + mid_16_xx_freq * mid_16_xx_acc) / long_mid_freq
            if long_mid_freq > 0
            else 0.0
        )

        result[abbr] = {
            "short_mid_freq": short_mid_freq,
            "short_mid_acc": short_mid_acc,
            "long_mid_freq": long_mid_freq,
            "long_mid_acc": long_mid_acc,
        }

    cache.set(cache_key, result)
    return result
