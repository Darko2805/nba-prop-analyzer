import requests
from ..config import (
    CURRENT_SEASON_YEAR, DATABALLR_PLAYER_URL, DATABALLR_TEAM_URL, REQUEST_HEADERS
)
from ..cache import cache
from .models import PlayerStats, TeamProfile, OpponentDefense
from .team_mapping import normalize_team


def _safe_float(data: dict, key: str, default: float = 0.0) -> float:
    val = data.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(data: dict, key: str, default: int = 0) -> int:
    val = data.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def fetch_all_players(year: int = CURRENT_SEASON_YEAR) -> list[PlayerStats]:
    cached = cache.get(f"players_{year}")
    if cached is not None:
        return cached

    params = {
        "year": year,
        "playoffs": 0,
        "min_minutes": 100,
        "limit": 600,
    }
    resp = requests.get(DATABALLR_PLAYER_URL, params=params, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    players = []
    for p in raw:
        mpg = _safe_float(p, "MPG")
        if mpg == 0:
            continue

        three_pa_100 = _safe_float(p, "3PA_100")
        three_pct = _safe_float(p, "3P_PERC")
        # per-100 is per 100 possessions while on court; scale to per-game
        three_pa_pg = three_pa_100 * mpg / 48.0 if three_pa_100 else 0
        three_pm_pg = three_pa_pg * three_pct if three_pct else 0

        team_raw = p.get("TeamAbbreviation", p.get("Team", ""))
        team_abbr = normalize_team(str(team_raw)) or str(team_raw)

        tsa100 = max(_safe_float(p, "TSA100", 1), 1)
        at_rim_fga100 = _safe_float(p, "d_AtRimFGA_Per100")
        long_mid_fga100 = _safe_float(p, "d_LongMidRangeFGA_Per100")
        short_mid_fga100 = _safe_float(p, "d_ShortMidRangeFGA_Per100")

        # Zone accuracy: try multiple field name patterns
        at_rim_acc = (
            _safe_float(p, "d_AtRimAccuracy")
            or _safe_float(p, "AtRimAccuracy")
            or _safe_float(p, "d_AtRimFG_Pct")
        )
        short_mid_acc = (
            _safe_float(p, "d_ShortMidRangeAccuracy")
            or _safe_float(p, "ShortMidRangeAccuracy")
        )
        long_mid_acc = (
            _safe_float(p, "d_LongMidRangeAccuracy")
            or _safe_float(p, "LongMidRangeAccuracy")
        )

        player = PlayerStats(
            name=p.get("Name", p.get("player_name", "Unknown")),
            team_abbr=team_abbr,
            position=p.get("Pos2", p.get("Position", "")),
            games_played=_safe_int(p, "GamesPlayed"),
            mpg=mpg,
            ppg=_safe_float(p, "basic_PPG"),
            apg=_safe_float(p, "basic_APG"),
            rpg=_safe_float(p, "basic_REB"),
            orpg=_safe_float(p, "basic_ORB"),
            drpg=_safe_float(p, "basic_DRB"),
            three_pm_pg=three_pm_pg,
            three_pa_pg=three_pa_pg,
            three_pct=three_pct,
            three_point_rate=_safe_float(p, "3PR"),
            ft_rate=_safe_float(p, "FTR"),
            fta_per100=_safe_float(p, "FTA_100"),
            ts_pct=_safe_float(p, "TS_pct"),
            tsa_per100=_safe_float(p, "TSA100"),
            offensive_archetype=p.get("Offensive Archetype", ""),
            at_rim_freq=at_rim_fga100 / tsa100,
            mid_range_freq=long_mid_fga100 / tsa100,
            short_mid_freq=short_mid_fga100 / tsa100,
            at_rim_acc=at_rim_acc,
            short_mid_acc=short_mid_acc,
            long_mid_acc=long_mid_acc,
            o_dpm=_safe_float(p, "o_dpm"),
            d_dpm=_safe_float(p, "d_dpm"),
            ortg_on=_safe_float(p, "rortg_on"),
            drtg_on=_safe_float(p, "rdrtg_on"),
        )
        players.append(player)

    cache.set(f"players_{year}", players)
    return players


def fetch_team_data(year: int = CURRENT_SEASON_YEAR) -> tuple[
    dict[str, TeamProfile], dict[str, OpponentDefense], dict
]:
    cached = cache.get(f"teams_{year}")
    if cached is not None:
        return cached

    params = {"year": year}
    resp = requests.get(DATABALLR_TEAM_URL, params=params, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    team_data = raw.get("team", {}).get("team_data", [])
    opp_data = raw.get("opponent", {}).get("team_data", [])
    league_avg_team = raw.get("team", {}).get("league_avg", {})
    league_avg_opp = raw.get("opponent", {}).get("league_avg", {})

    league_avg_ts = _safe_float(league_avg_team, "TsPct", 0.58)

    # We'll compute league averages from team-level data below
    league_avg = {
        "pace": 100.0,
        "ortg": 114.0,
        "drtg": 114.0,
        "ts_pct": league_avg_ts if league_avg_ts > 0 else 0.58,
        "ppg": 114.0,
    }

    # Estimate GP: use average possessions per game (~100 pace * GP)
    # For a full 82-game season, OffPoss is typically ~8000-8200
    # Estimate GP = OffPoss / ~100 (approximate pace)
    def _estimate_gp(off_poss, def_poss):
        avg_poss = (off_poss + def_poss) / 2
        # ~100 possessions per game is league average
        gp = round(avg_poss / 100)
        return max(gp, 1)

    # Build team profiles
    team_profiles = {}
    for t in team_data:
        abbr = normalize_team(t.get("TeamAbbreviation", t.get("Name", "")))
        if not abbr:
            continue

        off_poss = _safe_float(t, "OffPoss", 1)
        def_poss = _safe_float(t, "DefPoss", 1)
        points = _safe_float(t, "Points")
        opp_points = _safe_float(t, "OpponentPoints")
        gp = _safe_int(t, "GamesPlayed", 0) or _estimate_gp(off_poss, def_poss)

        pace = (off_poss + def_poss) / max(gp, 1)
        ortg = (points / max(off_poss, 1)) * 100
        drtg = (opp_points / max(def_poss, 1)) * 100

        fg3a = _safe_float(t, "FG3A")
        fg2a = _safe_float(t, "FG2A")
        total_fga = fg3a + fg2a

        team_profiles[abbr] = TeamProfile(
            name=t.get("Name", abbr),
            abbreviation=abbr,
            games_played=gp,
            pace=pace,
            ortg=ortg,
            drtg=drtg,
            ts_pct=_safe_float(t, "TsPct"),
            at_rim_freq=_safe_float(t, "AtRimFrequency"),
            at_rim_acc=_safe_float(t, "AtRimAccuracy"),
            long_mid_freq=_safe_float(t, "LongMidRangeFrequency"),
            long_mid_acc=_safe_float(t, "LongMidRangeAccuracy"),
            short_mid_freq=_safe_float(t, "ShortMidRangeFrequency"),
            short_mid_acc=_safe_float(t, "ShortMidRangeAccuracy"),
            three_point_rate=fg3a / max(total_fga, 1),
            ppg=points / max(gp, 1),
            rpg=_safe_float(t, "TotalRebounds", 0) / max(gp, 1) if t.get("TotalRebounds") else 44.0,
            apg=_safe_float(t, "Assists", 0) / max(gp, 1) if t.get("Assists") else 25.0,
        )

    # Build opponent defense profiles
    opponent_defenses: dict[str, OpponentDefense] = {}
    for o in opp_data:
        abbr = normalize_team(o.get("TeamAbbreviation", o.get("Name", "")))
        if not abbr:
            continue

        off_poss = _safe_float(o, "OffPoss", 1)
        def_poss = _safe_float(o, "DefPoss", 1)
        points = _safe_float(o, "Points")
        gp = _safe_int(o, "GamesPlayed", 0) or _estimate_gp(off_poss, def_poss)

        team_profile = team_profiles.get(abbr)
        team_drtg = team_profile.drtg if team_profile else 114.0
        team_pace = team_profile.pace if team_profile else 100.0

        # Compute per-game opponent stats; derive 3P% from totals if needed
        fg3a_total = _safe_float(o, "FG3A", 0)
        fg3m_total = _safe_float(o, "FG3M", 0)
        fta_total = _safe_float(o, "FTA", 0)
        ftm_total = _safe_float(o, "FTM", 0)
        fg2a_total = _safe_float(o, "FG2A", 0)
        total_fga = fg3a_total + fg2a_total

        # Use NonHeaveFg3Pct if FG3M is not directly available
        if o.get("FG3M") is not None and fg3m_total > 0 and fg3a_total > 0:
            opp_3p_pct = fg3m_total / fg3a_total
        else:
            opp_3p_pct = _safe_float(o, "NonHeaveFg3Pct", 0.36)
        opp_ft_pct = ftm_total / max(fta_total, 1) if fta_total > 0 else 0.78
        opp_fg_pct = _safe_float(o, "FGPct", 0) or (points * 0.44 / max(total_fga, 1))  # rough estimate

        opponent_defenses[abbr] = OpponentDefense(
            team_name=o.get("Name", abbr),
            abbreviation=abbr,
            opp_ppg=points / max(gp, 1),
            opp_fg_pct=opp_fg_pct,
            opp_3pm=fg3a_total * opp_3p_pct / max(gp, 1),
            opp_3pa=fg3a_total / max(gp, 1),
            opp_3p_pct=opp_3p_pct,
            opp_ftm=ftm_total / max(gp, 1),
            opp_fta=fta_total / max(gp, 1),
            opp_ft_pct=opp_ft_pct,
            opp_rpg=_safe_float(o, "TotalRebounds", 0) / max(gp, 1) if o.get("TotalRebounds") else 44.0,
            opp_apg=_safe_float(o, "Assists", 0) / max(gp, 1) if o.get("Assists") else 25.0,
            opp_topg=_safe_float(o, "Turnovers", 0) / max(gp, 1),
            opp_ts_pct=_safe_float(o, "TsPct"),
            opp_at_rim_freq=_safe_float(o, "AtRimFrequency"),
            opp_at_rim_acc=_safe_float(o, "AtRimAccuracy"),
            drtg=team_drtg,
            pace=team_pace,
            opp_long_mid_freq=_safe_float(o, "LongMidRangeFrequency"),
            opp_long_mid_acc=_safe_float(o, "LongMidRangeAccuracy"),
            opp_short_mid_freq=_safe_float(o, "ShortMidRangeFrequency"),
            opp_short_mid_acc=_safe_float(o, "ShortMidRangeAccuracy"),
            opp_three_freq=fg3a_total / max(total_fga, 1) if total_fga > 0 else 0.0,
        )

    # Compute league averages from team data
    if team_profiles:
        league_avg["pace"] = sum(t.pace for t in team_profiles.values()) / len(team_profiles)
        league_avg["ortg"] = sum(t.ortg for t in team_profiles.values()) / len(team_profiles)
        league_avg["drtg"] = sum(t.drtg for t in team_profiles.values()) / len(team_profiles)
        league_avg["ppg"] = sum(t.ppg for t in team_profiles.values()) / len(team_profiles)

    result = (team_profiles, opponent_defenses, league_avg)
    cache.set(f"teams_{year}", result)
    return result


def _normalize_name(name: str) -> str:
    """Strip diacritics for fuzzy matching."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def find_player(name: str, players: list):
    name_lower = _normalize_name(name)
    # Exact match (with diacritics stripped)
    for p in players:
        if _normalize_name(p.name) == name_lower:
            return p
    # Substring match
    for p in players:
        if name_lower in _normalize_name(p.name):
            return p
    # Reverse substring (player name in query)
    for p in players:
        if _normalize_name(p.name) in name_lower:
            return p
    # Last name match
    for p in players:
        parts = _normalize_name(p.name).split()
        if parts and parts[-1] == name_lower:
            return p
    return None
