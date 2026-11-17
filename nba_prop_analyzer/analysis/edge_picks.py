"""
Choosing which edges to feature across several prop types.

Each prop type is ranked on its own (a points edge and an assists edge are not scored on the same
scale: scoring props carry the loudest factors), so one combined ranking would let points crowd
out everything else. The balanced list instead takes the best remaining edge of each prop in turn,
so every prop is represented, and gives each player at most one slot.
"""
from __future__ import annotations

# Round-robin order for the default view.
CORE_PROPS = ("points", "3pm", "rebounds", "assists")


def balanced_edges(by_prop: dict, limit: int = 8, props: tuple = CORE_PROPS, mix_sides: bool = False) -> list:
    """
    by_prop: prop type -> candidates sorted strongest first (each a dict with a "player").
    Returns up to `limit` candidates: the strongest of each prop in turn, then the next strongest of
    each, and so on, skipping a player who already has a slot.

    mix_sides (the homepage list only): most leans are Unders, because players beat their own average
    in well under half their games. So that the list is not a wall of one colour, each prop's turn
    takes its strongest lean on whichever side (candidate["lean_direction"], "up" or "down") the list
    has fewer of so far, when it has one. Every row is still a real lean; only the order of choice
    changes.
    """
    queues = {prop: list(by_prop.get(prop, [])) for prop in props}
    chosen, seen = [], set()
    sides = {"up": 0, "down": 0}
    while len(chosen) < limit and any(queues.values()):
        for prop in props:
            queue = queues[prop] = [c for c in queues[prop] if c["player"] not in seen]
            if not queue:
                continue
            pick = queue[0]
            if mix_sides and sides["up"] != sides["down"]:
                wanted = "up" if sides["up"] < sides["down"] else "down"
                pick = next((c for c in queue if c.get("lean_direction") == wanted), pick)
            queue.remove(pick)
            seen.add(pick["player"])
            if pick.get("lean_direction") in sides:
                sides[pick["lean_direction"]] += 1
            chosen.append(pick)
            if len(chosen) >= limit:
                break
    return chosen


def game_edges(by_prop: dict, teams, limit: int = 10) -> list:
    """One game's leans: each player's most confident prop, most confident first."""
    best = {}
    for rows in by_prop.values():
        for c in rows:
            if c.get("team") in teams and c.get("lean_pct", 0) > best.get(c["player"], {}).get("lean_pct", -1):
                best[c["player"]] = c
    return sorted(best.values(), key=lambda c: c.get("lean_pct", 0), reverse=True)[:limit]
