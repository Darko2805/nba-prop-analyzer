"""
Pure helpers behind the Analyze form's player search and "next game" fill-in.

Kept free of Flask and of the app's loaded data so they can be tested on their
own: callers pass in the player list, the already-fetched schedule slates and
the team-name normalizer.
"""
from __future__ import annotations

import datetime
import re
import unicodedata


def normalize(text: str) -> str:
    """Lowercase, no diacritics, no punctuation: "Dončić", "De'Aaron" and
    "Porter Jr." all become something a person can type on any keyboard."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    s = re.sub(r"[.'’]", "", s)
    s = re.sub(r"[-_]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def search_players(players, query: str, limit: int = 8) -> list:
    """
    Players whose name matches a typed fragment of the first OR last name, best first:
      0  several words typed and the full name starts with them   ("le ja", "luka don")
      1  every typed word starts some word of the name            ("bro", "james", "jackson jr")
      2  every typed word appears somewhere in the name           ("ebron")
    Within a tier the player with more minutes comes first, so a one-word search
    like "bro" shows the rotation players (Brown, Brooks) before deep-bench
    first-name matches. Needs at least two characters.
    """
    q = normalize(query)
    if len(q) < 2:
        return []
    tokens = q.split()

    best = {}
    for p in players:
        if not getattr(p, "ppg", 0) and not getattr(p, "mpg", 0):
            continue  # never played: nothing to analyze
        name = normalize(p.name)
        words = name.split()
        if len(tokens) > 1 and name.startswith(q):
            tier = 0
        elif all(any(w.startswith(t) for w in words) for t in tokens):
            tier = 1
        elif all(t in name for t in tokens):
            tier = 2
        else:
            continue
        key = (tier, -(getattr(p, "mpg", 0) or 0), p.name)
        if p.name not in best or key < best[p.name][0]:
            best[p.name] = (key, p)
    return [p for _, p in sorted(best.values(), key=lambda kp: kp[0])[:limit]]


def exact_player(players, name: str):
    """The one player whose normalized name equals `name`, else None (no guessing)."""
    target = normalize(name)
    if not target:
        return None
    for p in players:
        if normalize(p.name) == target:
            return p
    return None


def next_game(team_abbr: str, slates: dict, normalize_team):
    """
    The team's next game that hasn't finished, from slates = {date: [game, ...] | None}
    (None = the schedule couldn't be loaded for that day). Returns (game | None, schedule_ok):
      (game, True)   found; game = {date, opponent, home, time}
      (None, True)   the schedule loaded but the team doesn't play in the window
      (None, False)  the schedule was unavailable before a game was found
    """
    for day in sorted(slates):
        games = slates[day]
        if games is None:
            return None, False
        for g in games:
            away, home = normalize_team(g["away_team"]), normalize_team(g["home_team"])
            if team_abbr not in (away, home) or g.get("start_time") == "Final":
                continue
            is_home = team_abbr == home
            return {"date": day, "opponent": away if is_home else home,
                    "home": is_home, "time": g.get("start_time", "")}, True
    return None, True


def when_label(game_date: datetime.date, today: datetime.date) -> str:
    if game_date == today:
        return "Tonight"
    if game_date == today + datetime.timedelta(days=1):
        return "Tomorrow"
    return f"{game_date:%a}, {game_date:%b} {game_date.day}"


def matchup_label(game: dict, today: datetime.date) -> str:
    """e.g. "Tonight · vs NYK · 7:30p ET" or "Fri, Oct 23 · @ BOS"."""
    parts = [when_label(game["date"], today), f"{'vs' if game['home'] else '@'} {game['opponent']}"]
    if game.get("time") and game["time"] != "TBD":
        parts.append(game["time"])
    return " · ".join(parts)
