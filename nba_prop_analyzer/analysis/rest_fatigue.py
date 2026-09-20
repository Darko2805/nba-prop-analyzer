"""
Rest / schedule-congestion factor — specifically for FGM.

"Running on Empty" (a peer-reviewed study of 20,000+ NBA games) found
teams on the second night of a back-to-back shoot at a lower eFG%, grab
fewer offensive rebounds, and play at a slower pace — while their
opponents shoot MORE efficiently against them. Effect size is modest
(0.5-2%) but real, and it's an eFG%/conversion effect specifically, so
it applies to FGM and is a near-zero no-op for FGA (mirroring how
free_throw.py and teammate_efficiency.py are correctly no-op'd where
they don't apply) and every other prop.

A milder, more cumulative version of the same fatigue also applies to a
team playing its 3rd game in a 4-night window — real, but a smaller
effect than a literal back-to-back. The two conditions often overlap
(a team on a back-to-back that also played two nights ago hits both),
so back-to-back takes precedence when both are true for the same team
rather than stacking two penalties for one fatigue effect.

Not redundant with pace.py: that factor ranks a team's SEASON-AVERAGE
pace, which has no idea whether tonight specifically is a back-to-back
or a 3-in-4 — this is a genuinely separate, date-specific signal pace.py
can't see.

This is a binary/tiered condition (which schedule bucket a team falls
into tonight), not a continuous stat with a league distribution to rank
against, so it doesn't use the severity curve in severity.py — the
effect sizes below are fixed, study-grounded constants rather than
something to rank/scale.
"""

from __future__ import annotations

_OWN_TEAM_B2B_PENALTY = 0.98     # own team on a back-to-back: ~2% eFG dip -> fewer makes
_OPPONENT_B2B_BONUS = 1.015      # opponent on a back-to-back: defends less effectively
_OWN_TEAM_3IN4_PENALTY = 0.99    # own team's 3rd game in 4 nights: milder dip
_OPPONENT_3IN4_BONUS = 1.0075    # opponent's 3rd game in 4 nights: milder defensive dip


def calculate_rest_factor(
    player_team_abbr: str,
    opponent_abbr: str,
    prop_type: str,
    teams_on_b2b: set | None = None,
    teams_on_3in4: set | None = None,
) -> tuple[float, str]:
    """
    Returns (multiplicative_factor, explanation_string). Only applies to
    FGM — an eFG%/conversion effect, not a volume effect, so FGA (and
    every other prop) gets a no-op 1.0.
    """
    if prop_type != "fgm":
        return 1.0, "~ Rest/schedule effect not applicable for this prop"

    teams_on_b2b = teams_on_b2b or set()
    teams_on_3in4 = teams_on_3in4 or set()
    factor = 1.0
    notes = []

    if player_team_abbr in teams_on_b2b:
        factor *= _OWN_TEAM_B2B_PENALTY
        notes.append(f"{player_team_abbr} is on a back-to-back tonight (fatigue dips eFG%)")
    elif player_team_abbr in teams_on_3in4:
        factor *= _OWN_TEAM_3IN4_PENALTY
        notes.append(f"{player_team_abbr} is playing its 3rd game in 4 nights (mild fatigue)")

    if opponent_abbr in teams_on_b2b:
        factor *= _OPPONENT_B2B_BONUS
        notes.append(f"{opponent_abbr} is on a back-to-back tonight (fatigued defense)")
    elif opponent_abbr in teams_on_3in4:
        factor *= _OPPONENT_3IN4_BONUS
        notes.append(f"{opponent_abbr} is playing its 3rd game in 4 nights (defense slightly worn down)")

    if notes:
        sign = "+" if factor > 1.0 else "-"
        note = f"{sign} Schedule fatigue: {'; '.join(notes)}"
    else:
        note = "~ No back-to-back/3-in-4 schedule effect tonight"

    return factor, note
