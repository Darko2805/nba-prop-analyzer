import math
from typing import List, Optional
from scipy.stats import norm, poisson
from ..config import STAT_CV
from ..data.models import PlayerStats


def estimate_std_dev(mean: float, prop_type: str, game_values: Optional[List[float]] = None) -> float:
    """
    Estimate standard deviation.
    If real game-log values are provided, compute actual std dev.
    Otherwise fall back to empirical coefficient of variation.
    """
    if game_values and len(game_values) >= 10:
        # Use real variance from game logs
        n = len(game_values)
        avg = sum(game_values) / n
        variance = sum((v - avg) ** 2 for v in game_values) / (n - 1)
        real_std = math.sqrt(variance)
        # Blend real std dev with empirical (70% real, 30% empirical) for stability
        empirical_std = max(mean * STAT_CV.get(prop_type, 0.30), 0.5)
        blended = real_std * 0.7 + empirical_std * 0.3
        return max(blended, 0.5)

    # Fallback: empirical coefficient of variation
    cv = STAT_CV.get(prop_type, 0.30)
    return max(mean * cv, 0.5)


def estimate_probability(
    predicted_value: float,
    prop_line: float,
    prop_type: str,
    game_values: Optional[List[float]] = None,
) -> tuple:
    """
    Returns (over_probability, under_probability).

    Uses Gaussian CDF for most props, Poisson for 3PM (discrete, low-count).
    If game_values are provided, uses real variance instead of estimates.
    """
    if prop_type == "3pm":
        return _poisson_probability(predicted_value, prop_line)

    std_dev = estimate_std_dev(predicted_value, prop_type, game_values)

    over_prob = 1 - norm.cdf(prop_line, loc=predicted_value, scale=std_dev)
    under_prob = norm.cdf(prop_line, loc=predicted_value, scale=std_dev)

    return over_prob, under_prob


def _poisson_probability(
    predicted_value: float,
    prop_line: float,
) -> tuple:
    """
    For 3PM props, use Poisson distribution (discrete, low counts).
    P(Over X.5) = P(X >= ceil(X.5)) = 1 - P(X <= floor(X.5))
    """
    mu = max(predicted_value, 0.1)
    # Prop lines are typically X.5 (e.g., 3.5)
    threshold = int(prop_line)  # floor: for 3.5, need >= 4 for over

    over_prob = 1 - poisson.cdf(threshold, mu)
    under_prob = poisson.cdf(threshold, mu)

    return over_prob, under_prob


def classify_confidence(over_prob: float) -> str:
    """Classify prediction confidence based on how far from 50/50."""
    edge = abs(over_prob - 0.5)
    if edge > 0.15:
        return "HIGH"
    elif edge > 0.08:
        return "MEDIUM"
    else:
        return "LOW"
