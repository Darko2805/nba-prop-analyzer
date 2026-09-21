import sys
import os

# Allow running directly (python web/app.py) or from repo root (gunicorn)
_repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from flask import Flask, render_template, request, jsonify, redirect, url_for
from nba_prop_analyzer.analysis.prop_analyzer import PropAnalyzer
from nba_prop_analyzer.analysis.headline import select_headline_factor, summarize_signal_agreement
from nba_prop_analyzer.config import PROP_TYPES
from nba_prop_analyzer.data.team_mapping import (
    ALL_TEAM_ABBRS, TEAM_COLORS, TEAM_FULL_NAMES, normalize_team, team_logo_url,
    find_espn_player_id, espn_headshot_url,
)
from nba_prop_analyzer.data.bbref_scraper import get_stat_from_games
from nba_prop_analyzer.data.databallr_client import find_player
from nba_prop_analyzer.data import snapshot_store, news, blog, track_record
from nba_prop_analyzer.web import auth, usage
from werkzeug.middleware.proxy_fix import ProxyFix

# A handful of recognizable stars to power the homepage's "Popular Bets"
# quick-picks. Lines are computed from each player's real season average
# (nearest 0.5), not fabricated — this is a shortcut into a real analysis,
# not a claim about an actual sportsbook line.
POPULAR_PLAYERS = [
    "LeBron James", "Stephen Curry", "Luka Doncic", "Nikola Jokic",
    "Giannis Antetokounmpo", "Shai Gilgeous-Alexander", "Jayson Tatum", "Anthony Edwards",
]


def _round_to_half(value: float) -> float:
    return round(value * 2) / 2


def build_popular_bets(analyzer: PropAnalyzer, games_today: list) -> list:
    """Curated quick-pick cards: player, a default points line from their real
    season average, and their opponent tonight if they're actually playing."""
    todays_opponent_by_team = {}
    for g in games_today:
        away_abbr = normalize_team(g["away_team"])
        home_abbr = normalize_team(g["home_team"])
        if away_abbr and home_abbr:
            todays_opponent_by_team[away_abbr] = home_abbr
            todays_opponent_by_team[home_abbr] = away_abbr

    picks = []
    for name in POPULAR_PLAYERS:
        player = find_player(name, analyzer.players)
        if not player or player.ppg <= 0:
            continue
        espn_id = find_espn_player_id(player.name, player.team_abbr)
        picks.append({
            "player": player.name,
            "team": player.team_abbr,
            "prop_type": "points",
            "line": _round_to_half(player.ppg),
            "opponent": todays_opponent_by_team.get(player.team_abbr),
            "headshot": espn_headshot_url(espn_id) if espn_id else None,
        })
    return picks

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-key-set-FLASK_SECRET_KEY-in-production")
# Render sits behind a reverse proxy -- without this, request.remote_addr is
# the proxy's address for every visitor, which would make usage.py's
# per-IP anonymous rate limit useless (everyone looks like the same IP).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)


@app.context_processor
def inject_user():
    return {"current_user": auth.current_user(), "auth_configured": auth.is_configured()}

# Load data once at startup
print("Loading NBA data from databallr + TeamRankings + basketball-reference...")
analyzer = PropAnalyzer()
analyzer.load_data()
print("Data loaded. Server ready.\n")


@app.route("/")
def index():
    games_today = snapshot_store.load_games_today()
    popular_bets = build_popular_bets(analyzer, games_today) if analyzer.is_ready() else []
    return render_template(
        "index.html",
        teams=ALL_TEAM_ABBRS,
        prop_types=PROP_TYPES,
        games_today=games_today,
        popular_bets=popular_bets,
    )


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/blog")
def blog_list():
    return render_template("blog_list.html", posts=blog.list_posts())


@app.route("/blog/<slug>")
def blog_post(slug):
    post = blog.get_post(slug)
    if post is None:
        return render_template("blog_list.html", posts=blog.list_posts(), not_found=True), 404
    return render_template("blog_post.html", post=post)


@app.route("/internal/snapshot-track-record")
def snapshot_track_record():
    # Manual trigger until this is wired into the existing daily
    # scheduled task -- fails closed (unlike usage.py's fail-open
    # pattern) since this is an admin action, not a real-user request.
    expected = os.environ.get("TRACK_RECORD_KEY")
    given = request.args.get("key")
    if not expected or given != expected:
        return jsonify({
            "error": "Not found",
            "debug_expected_len": len(expected) if expected else 0,
            "debug_given_len": len(given) if given else 0,
            "debug_match": given == expected,
        }), 404
    if not analyzer.is_ready():
        return jsonify({"error": "Data not ready"}), 503
    games_today = snapshot_store.load_games_today()
    count = track_record.snapshot_todays_predictions(analyzer, games_today)
    return jsonify({"recorded": count})


@app.route("/api/news")
def api_news():
    try:
        return jsonify({"items": news.fetch_news()})
    except Exception as e:
        return jsonify({"items": [], "error": str(e)}), 502


@app.route("/analyze", methods=["POST"])
def analyze():
    if not analyzer.is_ready():
        return jsonify({
            "error": "NBA stats data is temporarily unavailable from our data provider. Please try again later."
        }), 503

    data = request.get_json()
    player_name = data.get("player", "").strip()
    opponent = data.get("opponent", "").strip().upper()
    prop_type = data.get("prop_type", "points")
    prop_line = float(data.get("line", 0))
    trend = float(data.get("trend", 1.0))

    if not player_name or not opponent or prop_line <= 0:
        return jsonify({"error": "Please fill in all fields with valid values."}), 400

    # Tier gating: paid is unlimited, signed-up "free" gets a 5-analysis
    # batch every 4 days, anonymous gets 1/day by IP. Checked read-only
    # here so a validation/analysis failure below doesn't burn someone's
    # quota; the matching record_* call only runs after a real success.
    user = auth.current_user()
    client_ip = usage.get_client_ip(request)
    if user is None:
        allowed, limit_msg = usage.check_anon_usage(client_ip)
        if not allowed:
            return jsonify({"error": limit_msg, "code": "usage_limit_anon"}), 429
    elif user.get("tier") != "paid":
        allowed, limit_msg = usage.check_signup_usage(user["id"])
        if not allowed:
            return jsonify({"error": limit_msg, "code": "usage_limit_free"}), 429

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
    player_obj = find_player(player_name, analyzer.players)
    player_team = player_obj.team_abbr if player_obj else None
    espn_id = find_espn_player_id(pred.player_name, player_team) if player_team else None
    result = {
        "player_name": pred.player_name,
        "player_team": player_team,
        "player_team_color": TEAM_COLORS.get(player_team, "#8b8d97"),
        "player_team_name": TEAM_FULL_NAMES.get(player_team, ""),
        "player_team_logo": team_logo_url(player_team),
        "player_headshot": espn_headshot_url(espn_id) if espn_id else None,
        "opponent": pred.opponent,
        "prop_type": pred.prop_type,
        "prop_label": {
            "points": "POINTS",
            "rebounds": "REBOUNDS",
            "assists": "ASSISTS",
            "3pm": "3-POINTERS MADE",
            "pra": "PTS + REB + AST",
            "fgm": "FIELD GOALS MADE",
            "fga": "FIELD GOALS ATTEMPTED",
            "3pa": "3-POINTERS ATTEMPTED",
            "turnovers": "TURNOVERS",
            "pts_ast": "PTS + AST",
            "pts_reb": "PTS + REB",
            "ast_reb": "AST + REB",
        }.get(pred.prop_type, pred.prop_type.upper()),
        "prop_unit": {
            "points": "PTS",
            "rebounds": "REB",
            "assists": "AST",
            "3pm": "3PM",
            "pra": "PRA",
            "fgm": "FGM",
            "fga": "FGA",
            "3pa": "3PA",
            "turnovers": "TOV",
            "pts_ast": "PTS+AST",
            "pts_reb": "PTS+REB",
            "ast_reb": "AST+REB",
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
            "free_throw": round(bd["free_throw"], 3),
            "teammate_efficiency": round(bd["teammate_efficiency"], 3),
            "rest_fatigue": round(bd["rest_fatigue"], 3),
            "trend": round(bd["trend"], 3),
            "after_matchup": round(bd["after_matchup"], 1),
            "after_pace": round(bd["after_pace"], 1),
            "after_zone": round(bd["after_zone"], 1),
            "after_volume": round(bd["after_volume"], 1),
            "after_ft": round(bd["after_ft"], 1),
            "after_teammate": round(bd["after_teammate"], 1),
            "after_rest": round(bd["after_rest"], 1),
            "final": round(bd["final"], 1),
        },
    }

    # Zone exploitation breakdown, for the half-court diagram (absent when not
    # applicable to this prop type, e.g. rebounds/assists/turnovers)
    if "zones" in bd:
        result["zones"] = bd["zones"]

    # Which single factor should headline the breakdown — weighted by how
    # central each factor actually is to this prop type, not just whichever
    # multiplier is furthest from 1.0. See headline.py.
    factor_values = {
        "matchup": bd["matchup"],
        "pace": bd["pace"],
        "shot_zone": bd["shot_zone"],
        "volume": bd["volume"],
        "free_throw": bd["free_throw"],
        "teammate_efficiency": bd["teammate_efficiency"],
        "rest_fatigue": bd["rest_fatigue"],
        "trend": bd["trend"],
    }
    headline = select_headline_factor(factor_values, pred.prop_type)
    headline["note"] = bd.get(f"{headline['factor']}_note", "")
    result["headline_factor"] = headline
    result["signal_summary"] = summarize_signal_agreement(factor_values)

    # Add recent game-by-game data if available, for the hit-rate chart
    if "recent_games" in bd:
        s = bd["recent_games"]
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
        result["recent_games"] = {
            "games": games,
            "avg": round(s["avg"], 1),
            "min": int(s["min"]),
            "max": int(s["max"]),
            "hit_count": hit_count,
            "total": len(s["values"]),
        }

    if "vs_opponent" in bd:
        result["vs_opponent"] = {
            "avg": round(bd["vs_opponent"]["avg"], 1),
            "games": bd["vs_opponent"]["games"],
        }

    if "real_std_dev" in bd:
        result["variance"] = {
            "std_dev": round(bd["real_std_dev"], 1),
            "season_avg": round(bd["real_season_avg"], 1),
            "games_played": bd["games_played"],
        }

    if user is None:
        usage.record_anon_usage(client_ip)
    elif user.get("tier") != "paid":
        usage.record_signup_usage(user["id"])

    return jsonify(result)


@app.route("/auth/signup", methods=["POST"])
def auth_signup():
    if not auth.is_configured():
        return jsonify({"error": "Sign-in isn't configured on this server yet."}), 503

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    if not email or "@" not in email:
        return jsonify({"error": "Please enter a valid email address."}), 400
    if not name:
        return jsonify({"error": "Please enter your name."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    redirect_to = f"{request.url_root.rstrip('/')}/auth/callback"
    try:
        auth.sign_up(email, password, name, redirect_to)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({"message": f"We emailed a confirmation link to {email}. Open it on this device to activate your account."})


@app.route("/auth/login", methods=["POST"])
def auth_login():
    if not auth.is_configured():
        return jsonify({"error": "Sign-in isn't configured on this server yet."}), 503

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Please enter your email and password."}), 400

    try:
        user = auth.sign_in_with_password(email, password)
        name = (user.get("user_metadata") or {}).get("name", "")
        profile = auth.upsert_profile(user["id"], user["email"], name)
        auth.log_in_session(profile)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 401

    return jsonify({"name": profile.get("name"), "tier": profile.get("tier")})


@app.route("/auth/callback")
def auth_callback():
    # The session tokens arrive in the URL fragment (#access_token=...), which
    # never reaches the server — this page's own JS reads it and posts it to
    # /auth/complete-login. See auth.py's module docstring for why.
    return render_template("auth_callback.html")


@app.route("/auth/complete-login", methods=["POST"])
def auth_complete_login():
    if not auth.is_configured():
        return jsonify({"error": "Sign-in isn't configured on this server yet."}), 503

    data = request.get_json(silent=True) or {}
    access_token = data.get("access_token")
    if not access_token:
        return jsonify({"error": "Missing access token."}), 400

    try:
        user = auth.get_user_from_token(access_token)
        name = (user.get("user_metadata") or {}).get("name", "")
        profile = auth.upsert_profile(user["id"], user["email"], name)
        auth.log_in_session(profile)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"name": profile.get("name"), "tier": profile.get("tier")})


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    auth.log_out_session()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
