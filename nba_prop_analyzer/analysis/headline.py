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

# Equal weighting (behaves like plain "furthest from 1.0") for any prop
# type not yet in _IMPORTANCE_WEIGHTS above.
_DEFAULT_WEIGHTS = {"matchup": 1.0, "pace": 1.0, "shot_zone": 1.0, "volume": 1.0, "trend": 1.0}

_FACTOR_LABELS = {
    "matchup": "Matchup",
    "pace": "Pace",
    "shot_zone": "Shot Zone",
    "volume": "Volume",
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
