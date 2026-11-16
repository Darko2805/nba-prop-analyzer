"""
Two inputs added after the November 2026 backtest (scripts/backtest.py), each sized from results:

  * Teammates out. When a scorer who has been playing sits, his shots go to the players around him.
    The input is the share of the team's scoring that is missing tonight; it lifts the teammate slot.
  * Position defense. What this opponent has allowed to guards, forwards or centers so far this
    season, relative to those players' own averages. It joins the matchup slot.

Both are continuous: the size of the adjustment follows the size of the gap, with no tiers.
Re-run the backtest before changing a constant.
"""
from __future__ import annotations

import unicodedata

from ..config import COMBO_PROP_TYPES
from ..data.team_mapping import normalize_team

# Lift per unit of missing scoring share (a 25-point scorer on a 115-point team is a share of 0.22).
# Fitted on the backtest with who actually sat, then cut by a quarter because a pre-game injury
# report is less certain than the box score.
# Measured slopes: points 0.69, rebounds 0.44, assists 0.83, 3PM 0.56 (points and assists held in both
# halves of the sample; 3PM was noisy, so it gets less).
TEAMMATE_OUT_STRENGTH = {"points": 0.50, "rebounds": 0.30, "assists": 0.60, "3pm": 0.30}
MIN_SCORER_PPG = 10.0     # below this a missing player does not free up meaningful shots
RECENT_TEAM_GAMES = 5     # an absence only counts if he had been playing: his share of these games

# How much of the opponent's position-allowed gap is applied, per prop.
# Deliberately small: the measured slopes (about 0.3-0.4 for points, rebounds and assists) were within
# two margins of error and did not hold in the first half of the sample, when there were few games to
# rank an opponent on. 3PM showed no positive signal and is left out until it does.
POSITION_STRENGTH = {"points": 0.20, "rebounds": 0.20, "assists": 0.20, "3pm": 0.0}
POSITION_SHRINK_GAMES = 30   # player-games before the opponent's own number outweighs "average"
MIN_PLAYER_GAMES = 3

_GROUPS = {"PG": "guards", "SG": "guards", "G": "guards", "SF": "forwards", "PF": "forwards", "F": "forwards", "C": "centers"}
_STAT = {"points": "pts", "rebounds": "reb", "assists": "ast", "3pm": "fg3"}
_ALIAS = {"fgm": "points", "fga": "points", "3pa": "3pm"}


def name_key(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name or "")
    return "".join(c for c in nfkd if c.isalnum() and not unicodedata.combining(c)).lower()


def position_group(position: str):
    return _GROUPS.get((position or "").split("-")[0].strip().upper())


def prop_strength(table: dict, prop_type: str, component_baselines: dict | None = None) -> float:
    """A prop's strength from a per-stat table; a combo is its components weighted by their size."""
    if prop_type in COMBO_PROP_TYPES and component_baselines:
        total = sum(component_baselines.values())
        return sum(table.get(c, 0.0) * v for c, v in component_baselines.items()) / total if total > 0 else 0.0
    return table.get(_ALIAS.get(prop_type, prop_type), 0.0)


def build_position_defense(players: list, logs_by_name: dict) -> dict:
    """
    {(opponent, group, stat): (ratio, player_games)}: what the opponent's rivals of that position
    produced against them, divided by those players' own season averages. 1.10 = 10% more than usual.
    logs_by_name: this season's raw game logs (name -> list of dicts).
    """
    totals = {}
    for p in players:
        group = position_group(p.position)
        games = [g for g in (logs_by_name.get(p.name) or []) if (g.get("minutes") or 0) > 0]
        if not group or len(games) < MIN_PLAYER_GAMES:
            continue
        for prop, field in _STAT.items():
            mean = sum(g.get(field) or 0 for g in games) / len(games)
            if mean <= 0:
                continue
            for g in games:
                opp = normalize_team(g.get("opponent", ""))
                if not opp:
                    continue
                t = totals.setdefault((opp, group, prop), [0.0, 0.0, 0])
                t[0] += g.get(field) or 0
                t[1] += mean
                t[2] += 1
    return {k: (a / m, n) for k, (a, m, n) in totals.items() if m > 0}


def position_gap(position_defense: dict, opponent: str, position: str, prop_type: str):
    """(gap, group, raw ratio, player_games). gap is the allowed ratio minus 1, pulled toward 0 while
    the sample is small. (0, ...) when there is nothing to go on."""
    group = position_group(position)
    entry = position_defense.get((opponent, group, _ALIAS.get(prop_type, prop_type))) if group else None
    if not entry:
        return 0.0, group, None, 0
    ratio, n = entry
    return (ratio - 1.0) * n / (n + POSITION_SHRINK_GAMES), group, ratio, n


def missing_scoring_share(player, players: list, out_names, team_ppg: float, logs_by_name: dict):
    """
    (share, [names]): the part of the team's scoring that is out tonight and had been playing.
    Someone out all season is already reflected in his teammates' numbers, so each absent player
    counts only for the share of the team's last few games he actually played.
    """
    out_keys = {name_key(n) for n in (out_names or ())}
    if not out_keys or not team_ppg or team_ppg <= 0:
        return 0.0, []
    mates = [p for p in players if p.team_abbr == player.team_abbr]
    dates = sorted({g.get("date", "") for p in mates for g in (logs_by_name.get(p.name) or [])})[-RECENT_TEAM_GAMES:]
    if not dates:
        return 0.0, []
    share, names = 0.0, []
    for p in mates:
        if p.name == player.name or p.ppg < MIN_SCORER_PPG or name_key(p.name) not in out_keys:
            continue
        played = {g.get("date", "") for g in (logs_by_name.get(p.name) or [])}
        presence = sum(1 for d in dates if d in played) / len(dates)
        if presence > 0:
            share += presence * p.ppg / team_ppg
            names.append(p.name)
    return share, names
