"""
Headline factor selection for the Component Breakdown.

Picking "the one thing that matters" isn't just "whichever multiplier is
furthest from 1.0" — a noisy, backward-looking factor (Trend, built from a
handful of recent games) can swing further than a real structural mismatch
(Shot Zone, built from a full season of opponent data) purely by chance,
and would wrongly grab the spotlight under a raw-deviation rule.

Instead, each factor's deviation from neutral (1.0) is weighted by how
central that factor actually is to the specific prop type being bet on,
before picking the winner. Weights are tuned prop-by-prop as the engine
goes through each prop type in turn — a prop type without a tuned table
yet falls back to equal weighting (plain deviation), and the result says
so explicitly (`tuned: False`) rather than silently pretending to be more
considered than it actually is.
"""

from __future__ import annotations

# Per-prop-type importance weights. Higher = more central/reliable for that
# specific prop; lower = real but diffuse, generic, or noisier for it.
# Add a prop type here only once its factors have actually been discussed
# and tuned — anything else falls back to _DEFAULT_WEIGHTS below.
_IMPORTANCE_WEIGHTS: dict[str, dict[str, float]] = {
    "3pm": {"matchup": 0.6, "pace": 0.9, "shot_zone": 1.4, "volume": 1.2, "trend": 0.7},
    "3pa": {"matchup": 0.6, "pace": 0.9, "shot_zone": 1.4, "volume": 1.2, "trend": 0.7},
}

# Points and PRA share the same table: PRA is a genuine 3-way blend of
# points/rebounds/assists (see PropAnalyzer._run_combo_factors), so its
# blended factor values are already honestly proportioned by how much of
# PRA's baseline actually comes from scoring — no separate weighting logic
# needed to keep shot-zone/free-throw from over-influencing PRA the way a
# flat "PRA = points" treatment used to.
_POINTS_WEIGHTS = {"matchup": 0.7, "pace": 0.9, "shot_zone": 1.2, "volume": 1.1, "free_throw": 1.0, "trend": 0.7}
_IMPORTANCE_WEIGHTS["points"] = _POINTS_WEIGHTS
_IMPORTANCE_WEIGHTS["pra"] = _POINTS_WEIGHTS

# Equal weighting (behaves like plain "furthest from 1.0") for any prop
# type not yet in _IMPORTANCE_WEIGHTS above.
_DEFAULT_WEIGHTS = {"matchup": 1.0, "pace": 1.0, "shot_zone": 1.0, "volume": 1.0, "free_throw": 1.0, "trend": 1.0}

_FACTOR_LABELS = {
    "matchup": "Matchup",
    "pace": "Pace",
    "shot_zone": "Shot Zone",
    "volume": "Volume",
    "free_throw": "Free Throws",
    "trend": "Trend",
}


def select_headline_factor(factors: dict[str, float], prop_type: str) -> dict:
    """
    `factors` is the breakdown's raw multipliers, e.g. {"matchup": 0.929,
    "pace": 1.086, "shot_zone": 1.22, "volume": 1.15, "trend": 0.991}.
    Returns the winning factor's name, label, multiplier, deviation from
    neutral, the weight applied, the resulting weighted score, which
    direction it pushes the projection, and whether a tuned weight table
    was actually available for this prop type (False = equal-weight
    fallback, not yet a deliberate ranking).
    """
    weights = _IMPORTANCE_WEIGHTS.get(prop_type, _DEFAULT_WEIGHTS)
    tuned = prop_type in _IMPORTANCE_WEIGHTS

    scored = []
    for name, value in factors.items():
        deviation = abs(value - 1.0)
        weight = weights.get(name, 1.0)
        scored.append((name, value, deviation, weight, deviation * weight))

    scored.sort(key=lambda row: row[4], reverse=True)
    winner_name, winner_value, winner_deviation, winner_weight, winner_score = scored[0]

    return {
        "factor": winner_name,
        "label": _FACTOR_LABELS.get(winner_name, winner_name),
        "multiplier": round(winner_value, 3),
        "deviation": round(winner_deviation, 3),
        "weight": winner_weight,
        "score": round(winner_score, 4),
        "direction": "up" if winner_value > 1.0 else ("down" if winner_value < 1.0 else "neutral"),
        "tuned": tuned,
    }


def summarize_signal_agreement(factors: dict[str, float], threshold: float = 0.02) -> dict:
    """
    Classifies each factor as agreeing with, disagreeing with, or neutral
    relative to the overall direction the combined factors actually moved
    the projection (up or down, from their product) — a plain "how many
    signals point the same way" read, independent of the importance
    weighting above. A factor within `threshold` of 1.0 counts as neutral
    rather than forcing it to a side.
    """
    product = 1.0
    for value in factors.values():
        product *= value
    overall_direction = "up" if product >= 1.0 else "down"

    agree = disagree = neutral = 0
    for value in factors.values():
        if abs(value - 1.0) < threshold:
            neutral += 1
        elif (value > 1.0) == (overall_direction == "up"):
            agree += 1
        else:
            disagree += 1

    return {
        "direction": overall_direction,
        "agree": agree,
        "disagree": disagree,
        "neutral": neutral,
        "directional_total": agree + disagree,
        "total": len(factors),
    }
