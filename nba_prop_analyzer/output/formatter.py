from ..data.models import PropPrediction


PROP_LABELS = {
    "points": "POINTS",
    "rebounds": "REBOUNDS",
    "assists": "ASSISTS",
    "3pm": "3-POINTERS MADE",
    "pra": "PTS + REB + AST",
}

PROP_UNITS = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "3pm": "3PM",
    "pra": "PRA",
}


def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def _confidence_color(confidence: str) -> str:
    if confidence == "HIGH":
        return "**"
    elif confidence == "MEDIUM":
        return "*"
    return ""


def format_prediction(pred: PropPrediction) -> str:
    label = PROP_LABELS.get(pred.prop_type, pred.prop_type.upper())
    unit = PROP_UNITS.get(pred.prop_type, "")

    lines = []
    lines.append("")
    lines.append("=" * 62)
    lines.append(f"  NBA PROP ANALYSIS: {pred.player_name} vs {pred.opponent} - {label}")
    lines.append("=" * 62)
    lines.append(f"  Prop Line:            {pred.prop_line}")
    lines.append(f"  Season Average:       {pred.season_avg:.1f} {unit}")
    lines.append(f"  Adjusted Projection:  {pred.predicted_value:.1f} {unit}")
    lines.append("")

    over_pct = pred.over_probability
    under_pct = pred.under_probability

    over_str = f"  OVER  {pred.prop_line}:   {over_pct:6.1%}   {_bar(over_pct)}"
    under_str = f"  UNDER {pred.prop_line}:   {under_pct:6.1%}   {_bar(under_pct)}"
    lines.append(over_str)
    lines.append(under_str)
    lines.append("")
    lines.append(f"  Confidence: {pred.confidence}")

    # Recommendation
    if over_pct > 0.55:
        lines.append(f"  >> Lean: OVER {pred.prop_line}")
    elif under_pct > 0.55:
        lines.append(f"  >> Lean: UNDER {pred.prop_line}")
    else:
        lines.append(f"  >> No strong lean (close to 50/50)")

    lines.append("")
    lines.append("  Key Factors:")
    for factor in pred.key_factors:
        lines.append(f"    {factor}")

    lines.append("")
    lines.append("  Component Breakdown:")
    bd = pred.breakdown
    lines.append(f"    Baseline:            {bd['baseline']:.1f} {unit}")
    lines.append(f"    Matchup adj:         x{bd['matchup']:.3f}  -> {bd['after_matchup']:.1f}")
    lines.append(f"    Pace adj:            x{bd['pace']:.3f}  -> {bd['after_pace']:.1f}")
    lines.append(f"    Opponent profile:    x{bd['opponent_profile']:.3f}  -> {bd['after_opp']:.1f}")
    lines.append(f"    Volume adj:          x{bd['volume']:.3f}  -> {bd['after_volume']:.1f}")
    lines.append(f"    Trend:               x{bd['trend']:.3f}  -> {bd['final']:.1f}")
    lines.append(f"    Final projection:    {bd['final']:.1f} {unit}")

    # Game log section (if available from basketball-reference)
    if "last_5_games" in bd:
        lines.append("")
        lines.append("  " + "-" * 58)
        lines.append("  Last 5 Games (basketball-reference):")
        summary = bd["last_5_games"]
        vals = summary["values"]
        opps = summary["opponents"]
        dates = summary["dates"]
        for i, (d, o, v) in enumerate(zip(dates, opps, vals)):
            marker = ""
            if v > pred.prop_line:
                marker = " OVER"
            elif v < pred.prop_line:
                marker = " UNDER"
            else:
                marker = " PUSH"
            lines.append(f"    {d}  vs {o:3s}  {v:5.0f} {unit}{marker}")

        hit_count = sum(1 for v in vals if v > pred.prop_line)
        lines.append(f"    L5 avg: {summary['avg']:.1f} | Range: {summary['min']:.0f}-{summary['max']:.0f} | Hit rate: {hit_count}/{len(vals)} OVER")

    if "real_std_dev" in bd:
        lines.append("")
        lines.append(f"  Variance (from {bd['games_played']} games):")
        lines.append(f"    Real season avg: {bd['real_season_avg']:.1f} {unit}")
        lines.append(f"    Real std dev:    {bd['real_std_dev']:.1f} {unit}")

    lines.append("=" * 62)
    lines.append("")

    return "\n".join(lines)
