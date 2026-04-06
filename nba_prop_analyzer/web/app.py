import sys
import os

# Allow running directly (python web/app.py) or from repo root (gunicorn)
_repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from flask import Flask, render_template, request, jsonify
from nba_prop_analyzer.analysis.prop_analyzer import PropAnalyzer
from nba_prop_analyzer.config import PROP_TYPES
from nba_prop_analyzer.data.team_mapping import ALL_TEAM_ABBRS
from nba_prop_analyzer.data.bbref_scraper import get_stat_from_games

app = Flask(__name__, template_folder="templates", static_folder="static")

# Load data once at startup
print("Loading NBA data from databallr + TeamRankings + basketball-reference...")
analyzer = PropAnalyzer()
analyzer.load_data()
print("Data loaded. Server ready.\n")


@app.route("/")
def index():
    return render_template("index.html", teams=ALL_TEAM_ABBRS, prop_types=PROP_TYPES)


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    player_name = data.get("player", "").strip()
    opponent = data.get("opponent", "").strip().upper()
    prop_type = data.get("prop_type", "points")
    prop_line = float(data.get("line", 0))
    trend = float(data.get("trend", 1.0))

    if not player_name or not opponent or prop_line <= 0:
        return jsonify({"error": "Please fill in all fields with valid values."}), 400

    try:
        pred = analyzer.analyze_prop(
            player_name=player_name,
            opponent_abbr=opponent,
            prop_type=prop_type,
            prop_line=prop_line,
            trend_override=trend,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {e}"}), 500

    # Build response
    bd = pred.breakdown
    result = {
        "player_name": pred.player_name,
        "opponent": pred.opponent,
        "prop_type": pred.prop_type,
        "prop_label": {
            "points": "POINTS",
            "rebounds": "REBOUNDS",
            "assists": "ASSISTS",
            "3pm": "3-POINTERS MADE",
            "pra": "PTS + REB + AST",
        }.get(pred.prop_type, pred.prop_type.upper()),
        "prop_unit": {
            "points": "PTS",
            "rebounds": "REB",
            "assists": "AST",
            "3pm": "3PM",
            "pra": "PRA",
        }.get(pred.prop_type, ""),
        "prop_line": pred.prop_line,
        "season_avg": round(pred.season_avg, 1),
        "predicted_value": round(pred.predicted_value, 1),
        "over_prob": round(pred.over_probability * 100, 1),
        "under_prob": round(pred.under_probability * 100, 1),
        "confidence": pred.confidence,
        "lean": "OVER" if pred.over_probability > 0.55 else ("UNDER" if pred.under_probability > 0.55 else "NO LEAN"),
        "key_factors": pred.key_factors,
        "breakdown": {
            "baseline": round(bd["baseline"], 1),
            "matchup": round(bd["matchup"], 3),
            "pace": round(bd["pace"], 3),
            "shot_zone": round(bd["shot_zone"], 3),
            "volume": round(bd["volume"], 3),
            "trend": round(bd["trend"], 3),
            "after_matchup": round(bd["after_matchup"], 1),
            "after_pace": round(bd["after_pace"], 1),
            "after_zone": round(bd["after_zone"], 1),
            "after_volume": round(bd["after_volume"], 1),
            "final": round(bd["final"], 1),
        },
    }

    # Add game log data if available
    if "last_5_games" in bd:
        s = bd["last_5_games"]
        games = []
        for i in range(len(s["values"])):
            val = s["values"][i]
            games.append({
                "date": s["dates"][i] if i < len(s["dates"]) else "",
                "opponent": s["opponents"][i] if i < len(s["opponents"]) else "",
                "value": int(val),
                "hit": "OVER" if val > pred.prop_line else ("UNDER" if val < pred.prop_line else "PUSH"),
            })
        hit_count = sum(1 for v in s["values"] if v > pred.prop_line)
        result["last_5"] = {
            "games": games,
            "avg": round(s["avg"], 1),
            "min": int(s["min"]),
            "max": int(s["max"]),
            "hit_count": hit_count,
            "total": len(s["values"]),
        }

    if "real_std_dev" in bd:
        result["variance"] = {
            "std_dev": round(bd["real_std_dev"], 1),
            "season_avg": round(bd["real_season_avg"], 1),
            "games_played": bd["games_played"],
        }

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
