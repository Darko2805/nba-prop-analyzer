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


def balanced_edges(by_prop: dict, limit: int = 8, props: tuple = CORE_PROPS) -> list:
    """
    by_prop: prop type -> candidates sorted strongest first (each a dict with a "player").
    Returns up to `limit` candidates: the strongest of each prop in turn, then the next strongest of
    each, and so on, skipping a player who already has a slot.
    """
    queues = {prop: list(by_prop.get(prop, [])) for prop in props}
    chosen, seen = [], set()
    while len(chosen) < limit and any(queues.values()):
        for prop in props:
            queue = queues[prop]
            while queue and queue[0]["player"] in seen:
                queue.pop(0)
            if not queue:
                continue
            pick = queue.pop(0)
            seen.add(pick["player"])
            chosen.append(pick)
            if len(chosen) >= limit:
                break
    return chosen
