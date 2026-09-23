from __future__ import annotations

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
    find_espn_player_id, espn_headshot_url, fetch_back_to_back_teams, fetch_three_in_four_teams,
    fetch_schedule_load,
)
from nba_prop_analyzer.analysis.fatigue_watch import compute_fatigue_scores
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


_EDGE_PROP_BASELINE = {"points": "ppg", "rebounds": "rpg", "assists": "apg"}
_EDGE_PROP_UNIT = {"points": "PTS", "rebounds": "REB", "assists": "AST"}


def build_biggest_edges(analyzer: PropAnalyzer, games_today: list, limit: int = 3) -> list:
    """
    The engine's own output, surfaced on the homepage instead of only
    after you run an analysis yourself: for tonight's tracked players,
    checks points/rebounds/assists and ranks whichever headline factors
    came out most extreme across ALL of them -- literally what Component
    Breakdown would show if you ran that exact matchup, not a separate
    "homepage" claim. Reuses track_record.py's player list so there's one
    curated roster to keep in sync, not two. Capped at one slot per
    player so the same standout name can't fill the whole list just
    because it's extreme in more than one stat.
    """
    todays_opponent_by_team = {}
    for g in games_today:
        away_abbr = normalize_team(g["away_team"])
        home_abbr = normalize_team(g["home_team"])
        if away_abbr and home_abbr:
            todays_opponent_by_team[away_abbr] = home_abbr
            todays_opponent_by_team[home_abbr] = away_abbr

    candidates = []
    for name in track_record.TRACKED_PLAYERS:
        player = find_player(name, analyzer.players)
        if not player:
            continue
        opponent = todays_opponent_by_team.get(normalize_team(player.team_abbr))
        if not opponent:
            continue

        for prop_type, baseline_attr in _EDGE_PROP_BASELINE.items():
            baseline = getattr(player, baseline_attr, 0)
            if baseline <= 0:
                continue
            line = _round_to_half(baseline)
            try:
                pred = analyzer.analyze_prop(
                    player_name=player.name,
                    opponent_abbr=opponent,
                    prop_type=prop_type,
                    prop_line=line,
                    trend_override=1.0,
                )
            except Exception:
                continue

            bd = pred.breakdown
            factor_values = {
                k: bd[k] for k in
                ["matchup", "pace", "shot_zone", "volume", "free_throw", "teammate_efficiency", "rest_fatigue", "trend"]
            }
            headline = select_headline_factor(factor_values, prop_type)
            note = bd.get(f"{headline['factor']}_note", "").lstrip("+-~ ").strip()
            if not note or headline["deviation"] < 0.01:
                continue  # nothing notable enough to feature

            candidates.append({
                "player": player.name,
                "team": player.team_abbr,
                "opponent": opponent,
                "prop_type": prop_type,
                "prop_unit": _EDGE_PROP_UNIT[prop_type],
                "line": line,
                "predicted_value": round(pred.predicted_value, 1),
                "headline_note": note,
                "headline_direction": headline["direction"],
                "score": headline["score"],
                "espn_id": find_espn_player_id(player.name, player.team_abbr),
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)

    seen_players = set()
    top = []
    for c in candidates:
        if c["player"] in seen_players:
            continue
        seen_players.add(c["player"])
        c["headshot"] = espn_headshot_url(c.pop("espn_id")) if c["espn_id"] else None
        top.append(c)
        if len(top) >= limit:
            break

    return top


def annotate_schedule_fatigue(games_today: list) -> list:
    """Tags any team in tonight's slate playing on a back-to-back or a 3rd game in 4 nights."""
    try:
        b2b_teams = fetch_back_to_back_teams()
        tin4_teams = fetch_three_in_four_teams()
    except Exception:
        b2b_teams, tin4_teams = set(), set()

    def _tag(abbr: str) -> str | None:
        if abbr in b2b_teams:
            return "B2B"
        if abbr in tin4_teams:
            return "3RD/4"
        return None

    annotated = []
    for g in games_today:
        away_abbr = normalize_team(g["away_team"])
        home_abbr = normalize_team(g["home_team"])
        annotated.append({
            **g,
            "away_fatigue": _tag(away_abbr),
            "home_fatigue": _tag(home_abbr),
        })
    return annotated


# Plausible (not real) two-week loads for the off-season preview schedule's
# teams only -- fetch_schedule_load() legitimately returns nothing right now
# since ESPN has no real games in the last 14 days for anyone. Used only
# while is_preview is True, and always shown under the same PREVIEW tag as
# the rest of the demo schedule -- never presented as real.
_SAMPLE_FATIGUE_LOAD = {
    "BOS": {"games": 8, "away_games": 6, "back_to_backs": 3},
    "LAL": {"games": 6, "away_games": 2, "back_to_backs": 1},
    "DEN": {"games": 5, "away_games": 1, "back_to_backs": 0},
    "GSW": {"games": 7, "away_games": 4, "back_to_backs": 2},
    "MIA": {"games": 6, "away_games": 5, "back_to_backs": 1},
    "OKC": {"games": 5, "away_games": 2, "back_to_backs": 0},
}


def build_fatigue_watch(games_today: list, is_preview: bool = False, limit: int = 6) -> list:
    """
    Ranks tonight's playing teams by their rolling two-week schedule
    load -- games, road games, and back-to-backs over the last 14 days,
    weighted toward road load (see fatigue_watch.compute_fatigue_scores)
    -- for the homepage's "Fatigue Watch" preview. This looks at the
    last two weeks; annotate_schedule_fatigue() above only flags
    tonight's single back-to-back/3-in-4 status, a different, narrower
    signal shown on the schedule cards.

    The window is always "the 14 days before today," so this updates on
    its own every day as the calendar moves -- nothing here stores or
    needs updating for a specific date range.
    """
    try:
        schedule_load = fetch_schedule_load()
    except Exception:
        schedule_load = {}
    if not schedule_load and is_preview:
        schedule_load = _SAMPLE_FATIGUE_LOAD
    scored = compute_fatigue_scores(schedule_load)

    opponent_of = {}
    for g in games_today:
        away_abbr = normalize_team(g["away_team"])
        home_abbr = normalize_team(g["home_team"])
        opponent_of[away_abbr] = home_abbr
        opponent_of[home_abbr] = away_abbr

    watch = [
        {
            "team": abbr,
            "team_name": TEAM_FULL_NAMES.get(abbr, abbr),
            "opponent": opponent_of[abbr],
            **data,
        }
        for abbr, data in scored.items()
        if abbr in opponent_of
    ]
    watch.sort(key=lambda t: t["fatigue_score"], reverse=True)
    return watch[:limit]


def build_model_inputs() -> list:
    """
    Real, season-long team stats -- not tied to tonight's schedule, so
    unlike Biggest Edges/Fatigue Watch this has real content year-round,
    including the off-season, since it reads the same full-season team
    profiles/opponent-defense data already loaded at startup that
    pace.py/shot_zone.py/volume.py/free_throw.py/teammate_efficiency.py
    actually rank teams against. Nothing here is a separate, made-up
    statistic -- it's the real inputs a live analysis would read.
    """
    if not analyzer.is_ready():
        return []
    profiles = analyzer.team_profiles
    defenses = analyzer.opponent_defenses
    if not profiles or not defenses:
        return []

    items = []

    fastest = max((a for a in profiles if profiles[a].pace > 0), key=lambda a: profiles[a].pace, default=None)
    if fastest:
        items.append({
            "team": fastest, "team_name": TEAM_FULL_NAMES.get(fastest, fastest),
            "factor": "Pace",
            "detail": f"Pace rating {profiles[fastest].pace:.1f} — fastest team in the league",
        })

    softest_rim = max((a for a in defenses if defenses[a].opp_at_rim_acc > 0), key=lambda a: defenses[a].opp_at_rim_acc, default=None)
    if softest_rim:
        items.append({
            "team": softest_rim, "team_name": TEAM_FULL_NAMES.get(softest_rim, softest_rim),
            "factor": "Shot Zone",
            "detail": f"Allows {defenses[softest_rim].opp_at_rim_acc * 100:.1f}% shooting at the rim — most exploitable interior defense in the league",
        })

    leakiest = max((a for a in defenses if defenses[a].opp_ppg > 0), key=lambda a: defenses[a].opp_ppg, default=None)
    if leakiest:
        items.append({
            "team": leakiest, "team_name": TEAM_FULL_NAMES.get(leakiest, leakiest),
            "factor": "Volume",
            "detail": f"Allows {defenses[leakiest].opp_ppg:.1f} PPG — most points surrendered in the league",
        })

    foulingest = max((a for a in defenses if defenses[a].opp_fta > 0), key=lambda a: defenses[a].opp_fta, default=None)
    if foulingest:
        items.append({
            "team": foulingest, "team_name": TEAM_FULL_NAMES.get(foulingest, foulingest),
            "factor": "Free Throws",
            "detail": f"Sends opponents to the line {defenses[foulingest].opp_fta:.1f} times/game — most in the league",
        })

    sharpest = max((a for a in profiles if profiles[a].ts_pct > 0), key=lambda a: profiles[a].ts_pct, default=None)
    if sharpest:
        items.append({
            "team": sharpest, "team_name": TEAM_FULL_NAMES.get(sharpest, sharpest),
            "factor": "Teammate Efficiency",
            "detail": f"{profiles[sharpest].ts_pct * 100:.1f}% true shooting as a team — most efficient offense in the league",
        })

    return items

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
    biggest_edges = build_biggest_edges(analyzer, games_today) if analyzer.is_ready() else []
    games_today_annotated = annotate_schedule_fatigue(games_today)
    # True only while off-season placeholder games are loaded for a demo --
    # set via meta.json's games_today_is_preview key, cleared automatically
    # the next time the real daily snapshot refresh runs (it never writes
    # this key, since it always loads the real schedule).
    is_preview_schedule = bool(snapshot_store.load_meta().get("games_today_is_preview"))
    fatigue_watch = build_fatigue_watch(games_today, is_preview=is_preview_schedule) if games_today else []
    model_inputs = build_model_inputs()
    return render_template(
        "index.html",
        teams=ALL_TEAM_ABBRS,
        prop_types=PROP_TYPES,
        games_today=games_today_annotated,
        popular_bets=popular_bets,
        model_inputs=model_inputs,
        biggest_edges=biggest_edges,
        fatigue_watch=fatigue_watch,
        is_preview_schedule=is_preview_schedule,
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


@app.route("/track-record")
def track_record_page():
    history = track_record.fetch_scored_history()
    summary = track_record.summarize_history(history)
    user = auth.current_user()
    is_paid = bool(user and user.get("tier") == "paid")
    return render_template(
        "track_record.html",
        summary=summary,
        # Detail rows are the paid perk; the aggregate above is shown to
        # everyone since it's the trust-building headline, not the receipts.
        history=history if is_paid else [],
        is_paid=is_paid,
        tracked_count=len(track_record.TRACKED_PLAYERS),
    )


@app.route("/internal/snapshot-track-record")
def snapshot_track_record():
    # Manual trigger until this is wired into the existing daily
    # scheduled task -- fails closed (unlike usage.py's fail-open
    # pattern) since this is an admin action, not a real-user request.
    expected = os.environ.get("TRACK_RECORD_KEY")
    if not expected or request.args.get("key") != expected:
        return jsonify({"error": "Not found"}), 404
    if not analyzer.is_ready():
        return jsonify({"error": "Data not ready"}), 503
    games_today = snapshot_store.load_games_today()
    count = track_record.snapshot_todays_predictions(analyzer, games_today)
    return jsonify({"recorded": count})


@app.route("/internal/score-track-record")
def score_track_record():
    # Same manual-trigger pattern as snapshot-track-record, same secret.
    # Meant to run the day after a snapshot, once the daily game-log
    # refresh has picked up the completed games.
    expected = os.environ.get("TRACK_RECORD_KEY")
    if not expected or request.args.get("key") != expected:
        return jsonify({"error": "Not found"}), 404
    count = track_record.record_outcomes()
    return jsonify({"scored": count})


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
        return jsonify({"error": str(e), "error_code": e.code}), 502

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


@app.route("/auth/forgot-password", methods=["POST"])
def auth_forgot_password():
    if not auth.is_configured():
        return jsonify({"error": "Sign-in isn't configured on this server yet."}), 503

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Please enter a valid email address."}), 400

    redirect_to = f"{request.url_root.rstrip('/')}/auth/callback"
    try:
        auth.request_password_reset(email, redirect_to)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 502

    # Deliberately the same message regardless of whether the account exists,
    # matching Supabase's own anti-enumeration behavior here.
    return jsonify({"message": f"If an account exists for {email}, we've sent a password reset link."})


@app.route("/auth/reset-password", methods=["POST"])
def auth_reset_password():
    if not auth.is_configured():
        return jsonify({"error": "Sign-in isn't configured on this server yet."}), 503

    data = request.get_json(silent=True) or {}
    token_hash = data.get("token_hash")
    access_token = data.get("access_token")
    new_password = data.get("password") or ""
    if not token_hash and not access_token:
        return jsonify({"error": "Missing reset token."}), 400
    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    try:
        if token_hash:
            session_data = auth.verify_otp(token_hash, "recovery")
            access_token = session_data["access_token"]
        auth.update_password(access_token, new_password)
        user = auth.get_user_from_token(access_token)
        name = (user.get("user_metadata") or {}).get("name", "")
        profile = auth.upsert_profile(user["id"], user["email"], name)
        auth.log_in_session(profile)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"name": profile.get("name"), "tier": profile.get("tier")})


@app.route("/auth/confirm-signup", methods=["POST"])
def auth_confirm_signup():
    # Companion to /auth/reset-password's token_hash path, for the
    # Confirm Sign Up email link -- see verify_otp()'s docstring for why
    # this is only ever called from an explicit button click.
    if not auth.is_configured():
        return jsonify({"error": "Sign-in isn't configured on this server yet."}), 503

    data = request.get_json(silent=True) or {}
    token_hash = data.get("token_hash")
    if not token_hash:
        return jsonify({"error": "Missing confirmation token."}), 400

    try:
        session_data = auth.verify_otp(token_hash, "signup")
        access_token = session_data["access_token"]
        user = auth.get_user_from_token(access_token)
        name = (user.get("user_metadata") or {}).get("name", "")
        profile = auth.upsert_profile(user["id"], user["email"], name)
        auth.log_in_session(profile)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"name": profile.get("name"), "tier": profile.get("tier")})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
