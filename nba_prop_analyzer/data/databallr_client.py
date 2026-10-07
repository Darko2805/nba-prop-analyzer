from __future__ import annotations

import requests
from ..config import CURRENT_SEASON_YEAR, PRIOR_SEASON_YEAR, TEAM_BLEND_K, DATABALLR_TEAM_URL, REQUEST_HEADERS
from ..cache import cache
from .models import TeamProfile, OpponentDefense
from .team_mapping import normalize_team
from . import snapshot_store
from .season_blend import blend_weight, blend_dataclass


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


class NoSeasonData(Exception):
    """databallr has no rows for that season yet (e.g. before the first game)."""


def fetch_team_stats_raw(year: int = CURRENT_SEASON_YEAR, min_teams: int = 25) -> dict:
    """
    Raw databallr team/opponent payload for a season, validated before it
    is trusted. Also what the daily refresh snapshots as the fallback.

    The v1 endpoint defaults to leverage=clutch (clutch-time-only stats) and
    has been seen ignoring an unsupported leverage value, so the response is
    checked rather than assumed: wrong leverage or a short team list raises
    instead of quietly skewing every team figure. Early in a new season only
    some teams have played, so that season passes a lower min_teams.
    NoSeasonData means databallr simply has nothing for the season yet.
    """
    params = {"season": year, "leverage": "all"}
    resp = requests.get(DATABALLR_TEAM_URL, params=params, headers=REQUEST_HEADERS, timeout=30)
    if resp.status_code == 404 and "No data found" in resp.text:
        raise NoSeasonData(f"no databallr data for season {year} yet")
    resp.raise_for_status()
    raw = resp.json()
    if raw.get("leverage") != "all":
        raise ValueError(f"databallr returned leverage={raw.get('leverage')!r}, expected 'all'")
    for side in ("team", "opponent"):
        n = len(raw.get(side, {}).get("team_data", []))
        if n < min_teams:
            raise ValueError(f"databallr returned only {n} {side} rows")
    return raw


def _parse_team_raw(raw: dict) -> tuple[dict[str, TeamProfile], dict[str, OpponentDefense], float]:
    """One season's raw payload -> (team profiles, opponent defenses, league TS%)."""
    team_data = raw.get("team", {}).get("team_data", [])
    opp_data = raw.get("opponent", {}).get("team_data", [])
    league_avg_team = raw.get("team", {}).get("league_avg", {})
    league_avg_opp = raw.get("opponent", {}).get("league_avg", {})

    league_avg_ts = _safe_float(league_avg_team, "TsPct", 0.58)
    league_avg_ts = league_avg_ts if league_avg_ts > 0 else 0.58

    # Estimate GP: use average possessions per game (~100 pace * GP)
    # For a full 82-game season, OffPoss is typically ~8000-8200
    # Estimate GP = OffPoss / ~100 (approximate pace)
    def _estimate_gp(off_poss, def_poss):
        avg_poss = (off_poss + def_poss) / 2
        # ~100 possessions per game is league average
        # A team with no games yet has no possessions: that is 0 games, not 1
        # (the new season's weight depends on it). Divisions below use max(gp, 1).
        return round(avg_poss / 100)

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
            fga=total_fga / max(gp, 1),
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
            opp_live_topg=_safe_float(o, "LiveBallTurnovers", 0) / max(gp, 1),
            opp_dead_topg=_safe_float(o, "DeadBallTurnovers", 0) / max(gp, 1),
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
            opp_fga=total_fga / max(gp, 1),
        )

    return team_profiles, opponent_defenses, league_avg_ts


def _league_average(team_profiles: dict, ts_pct: float) -> dict:
    league_avg = {"pace": 100.0, "ortg": 114.0, "drtg": 114.0, "ts_pct": ts_pct, "ppg": 114.0}
    if team_profiles:
        n = len(team_profiles)
        for key, attr in (("pace", "pace"), ("ortg", "ortg"), ("drtg", "drtg"), ("ppg", "ppg"), ("fga", "fga")):
            league_avg[key] = sum(getattr(t, attr) for t in team_profiles.values()) / n
    return league_avg


def _current_season_raw(year: int) -> dict | None:
    """The current season's payload: live, else the committed snapshot of it,
    else None (the season hasn't produced data yet -- the normal pre-opener state)."""
    try:
        return fetch_team_stats_raw(year, min_teams=1)
    except NoSeasonData:
        return snapshot_store.load_team_stats() or None
    except Exception as e:
        snap = snapshot_store.load_team_stats()
        if snap:
            print(f"  Live team stats fetch failed ({e}); using the committed snapshot")
        return snap or None


def _prior_season_raw() -> dict:
    """Last season's payload -- complete and static, so the committed snapshot
    is the source of truth; a live fetch is only the fallback if it's missing."""
    raw = snapshot_store.load_team_stats(prev=True)
    if raw:
        return raw
    print("  No prior-season team snapshot; fetching it live")
    return fetch_team_stats_raw(PRIOR_SEASON_YEAR)


def fetch_team_data(year: int = CURRENT_SEASON_YEAR) -> tuple[
    dict[str, TeamProfile], dict[str, OpponentDefense], dict, dict[str, float]
]:
    """
    Team profiles, opponent defenses, league averages and per-team blend
    weights. Each team's numbers mix the current season with last season's by
    weight = games / (games + TEAM_BLEND_K); a team with no games yet is
    exactly last season's numbers (see season_blend).
    """
    cached = cache.get(f"teams_{year}")
    if cached is not None:
        return cached

    prior_profiles, prior_opps, prior_ts = _parse_team_raw(_prior_season_raw())
    current_raw = _current_season_raw(year)
    if current_raw:
        cur_profiles, cur_opps, cur_ts = _parse_team_raw(current_raw)
    else:
        cur_profiles, cur_opps, cur_ts = {}, {}, prior_ts

    weights = {
        abbr: blend_weight(cur_profiles[abbr].games_played, TEAM_BLEND_K) if abbr in cur_profiles else 0.0
        for abbr in set(prior_profiles) | set(cur_profiles)
    }
    profiles = {
        abbr: blend_dataclass(cur_profiles.get(abbr), prior_profiles.get(abbr), weights[abbr])
        for abbr in weights
    }
    opponents = {
        abbr: blend_dataclass(cur_opps.get(abbr), prior_opps.get(abbr), weights.get(abbr, 0.0))
        for abbr in set(prior_opps) | set(cur_opps)
    }
    mean_weight = sum(weights.values()) / len(weights) if weights else 0.0
    ts_pct = mean_weight * cur_ts + (1 - mean_weight) * prior_ts

    result = (profiles, opponents, _league_average(profiles, ts_pct), weights)
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
