from __future__ import annotations

import sys
import os
import re
import datetime
import threading
import concurrent.futures
import requests
from collections import OrderedDict

# Allow running directly (python web/app.py) or from repo root (gunicorn)
_repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from flask import Flask, Response, abort, render_template, request, jsonify, redirect, url_for
from nba_prop_analyzer.analysis.prop_analyzer import PropAnalyzer
from nba_prop_analyzer.analysis.headline import select_headline_factor, summarize_signal_agreement
from nba_prop_analyzer.cache import cache
from nba_prop_analyzer.config import PROP_TYPES, COMBO_PROP_TYPES, LEGAL_VERSION, MIN_AGE
from nba_prop_analyzer.data.team_mapping import (
    ALL_TEAM_ABBRS, TEAM_COLORS, TEAM_FULL_NAMES, normalize_team, team_logo_url,
    find_espn_player_id, espn_headshot_url, fetch_back_to_back_teams, fetch_three_in_four_teams,
    fetch_schedule_load, fetch_injuries, fetch_games_on, tonight_date, et_date,
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


# Real per-game season-average attribute backing each single-stat prop type's
# suggested line -- same "nearest 0.5 of season average" convention as
# Quick Looks/Biggest Edges, never a fabricated number. Combo prop types
# (pra, pts_ast, ...) aren't listed here; they're summed from their
# COMBO_PROP_TYPES components instead.
_PROP_BASELINE_ATTR = {
    "points": "ppg", "rebounds": "rpg", "assists": "apg",
    "3pm": "three_pm_pg", "3pa": "three_pa_pg",
    "fgm": "fgm_pg", "fga": "fga_pg", "turnovers": "topg",
}


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


def build_biggest_edges(analyzer: PropAnalyzer, games_today: list, limit: int = 8) -> list:
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
    is_home_by_team = {}
    for g in games_today:
        away_abbr = normalize_team(g["away_team"])
        home_abbr = normalize_team(g["home_team"])
        if away_abbr and home_abbr:
            todays_opponent_by_team[away_abbr] = home_abbr
            todays_opponent_by_team[home_abbr] = away_abbr
            is_home_by_team[away_abbr] = False
            is_home_by_team[home_abbr] = True

    try:
        injuries_by_player = fetch_injuries()
    except Exception:
        injuries_by_player = {}

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

            # The predicted-value number's color is the actual verdict (same
            # >55% threshold as everywhere else the app defines "lean" --
            # app.py's /analyze route, track_record.py's snapshot), never the
            # headline factor's own direction. The headline factor is chosen
            # by loudest weighted deviation, not by which way it argues, so
            # it can point opposite the real lean (e.g. a strong OVER whose
            # single loudest factor is still a bearish one) -- coloring the
            # number by the factor instead of the verdict was showing red on
            # a real projected OVER, which read as a contradiction.
            lean_direction = "up" if pred.over_probability > 0.55 else ("down" if pred.under_probability > 0.55 else "neutral")

            # L10 avg/hit-rate reuses analyze_prop()'s own game-log summary
            # (the same data "Recent Form vs the Line" already shows for a
            # single analysis) instead of a second, separate game-log fetch.
            recent = bd.get("recent_games")
            if recent and recent.get("values"):
                l10_values = recent["values"]
                hits_of_10 = sum(1 for v in l10_values if v > line)
                l10_avg = round(recent["avg"], 1)
                l10_hit_pct = round(hits_of_10 / len(l10_values) * 100)
            else:
                l10_avg = hits_of_10 = l10_hit_pct = None

            inj = injuries_by_player.get(player.name)

            candidates.append({
                "player": player.name,
                "team": player.team_abbr,
                "opponent": opponent,
                "is_home": is_home_by_team.get(normalize_team(player.team_abbr), False),
                "prop_type": prop_type,
                "prop_unit": _EDGE_PROP_UNIT[prop_type],
                "line": line,
                "predicted_value": round(pred.predicted_value, 1),
                "edge": round(pred.predicted_value - line, 1),
                "headline_note": note,
                "lean_direction": lean_direction,
                "l10_avg": l10_avg,
                "l10_hit_pct": l10_hit_pct,
                "l10_hits": hits_of_10,
                "injury_status": inj["status"] if inj else None,
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


def build_fatigue_watch(games_today: list, limit: int = 6) -> list:
    """
    Ranks tonight's playing teams by their rolling two-week schedule
    load -- games, road games, back-to-backs, overtime minutes, and
    close-game count over the last 14 days, weighted toward road load
    (see fatigue_watch.compute_fatigue_scores) -- for the homepage's
    "Fatigue Watch" section. This looks at the last two weeks;
    annotate_schedule_fatigue() above only flags tonight's single
    back-to-back/3-in-4 status, a different, narrower signal shown on
    the schedule cards.

    The window is always "the 14 days before today," so this updates on
    its own every day as the calendar moves -- nothing here stores or
    needs updating for a specific date range.
    """
    try:
        schedule_load = fetch_schedule_load()
    except Exception:
        schedule_load = {}
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


def _slate_kind(games: list) -> str | None:
    """preseason / regular / postseason, from ESPN's per-game season type."""
    if not games:
        return None
    kinds = {g["season_type"] for g in games}
    if kinds == {1}:
        return "preseason"
    if kinds == {3}:
        return "postseason"
    return "regular"


def _find_next_slate(after) -> dict | None:
    """The next day (within 10 days) that has games, for the empty-day message.
    One cached ESPN request per day, fetched in parallel, and the answer itself
    is cached so a quiet stretch doesn't re-scan on every page view."""
    cache_key = f"next_slate_{after.isoformat()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    days = [after + datetime.timedelta(days=i) for i in range(1, 11)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(fetch_games_on, days))
    found = None
    for day, games in zip(days, results):
        if games:
            found = {"label": f"{day:%a, %b} {day.day}", "count": len(games), "kind": _slate_kind(games)}
            break
    if None not in results:  # don't remember an answer built on a failed lookup
        cache.set(cache_key, found or {})
    return found


def build_tonight() -> dict:
    """
    Tonight's slate, live from ESPN: {ok, date, games, kind, next_up}.
    ok=False means the schedule couldn't be loaded (as opposed to a genuinely
    empty day). next_up is only filled on an empty day.
    """
    today = tonight_date()
    games = fetch_games_on(today)
    if games is None:
        return {"ok": False, "date": today, "games": [], "kind": None, "next_up": None}
    return {
        "ok": True,
        "date": today,
        "games": games,
        "kind": _slate_kind(games),
        "next_up": None if games else _find_next_slate(today),
    }


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

# The login session lives in a signed cookie, so it needs the standard cookie
# hardening. SECURE is tied to FLASK_SECRET_KEY being set (i.e. a real
# deployment) so plain-http local dev can still set the cookie.
_PRODUCTION = bool(os.environ.get("FLASK_SECRET_KEY"))
app.config.update(
    SESSION_COOKIE_SECURE=_PRODUCTION,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
if auth.is_configured() and not _PRODUCTION:
    print("WARNING: Supabase auth is configured but FLASK_SECRET_KEY is not set -- "
          "sessions are signed with a public default key and can be forged.")


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if _PRODUCTION:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
    return resp


@app.context_processor
def inject_user():
    return {"current_user": auth.current_user(), "auth_configured": auth.is_configured()}


@app.context_processor
def inject_legal():
    # Operator details come from env vars so they aren't hardcoded in a public
    # repo and can be set/changed on the host without a deploy of new text.
    return {
        "legal": {
            "operator": os.environ.get("LEGAL_OPERATOR", "").strip(),
            "contact_email": os.environ.get("LEGAL_CONTACT_EMAIL", "").strip(),
            "jurisdiction": os.environ.get("LEGAL_JURISDICTION", "").strip(),
            "postal_address": os.environ.get("LEGAL_POSTAL_ADDRESS", "").strip(),
            "version": LEGAL_VERSION,
        },
        "min_age": MIN_AGE,
        "current_year": datetime.date.today().year,
    }

# Load data once at startup
print("Loading NBA data from databallr + TeamRankings + basketball-reference...")
analyzer = PropAnalyzer()
analyzer.load_data()
print("Data loaded. Server ready.\n")


@app.route("/")
def index():
    tonight = build_tonight()
    games = tonight["games"]
    # "Edges" and "fatigue" are regular-season claims: preseason rotations are
    # short and unpredictable and the stats are last season's, so those two
    # sections (and the opponent prefill on Quick Looks) wait for real games.
    in_season = tonight["kind"] in ("regular", "postseason")
    popular_bets = build_popular_bets(analyzer, games if in_season else []) if analyzer.is_ready() else []
    biggest_edges = build_biggest_edges(analyzer, games) if (analyzer.is_ready() and in_season) else []
    fatigue_watch = build_fatigue_watch(games) if in_season else []
    games_annotated = annotate_schedule_fatigue(games) if in_season else games
    model_inputs = build_model_inputs()
    return render_template(
        "index.html",
        teams=ALL_TEAM_ABBRS,
        prop_types=PROP_TYPES,
        tonight=tonight,
        games_today=games_annotated,
        popular_bets=popular_bets,
        model_inputs=model_inputs,
        biggest_edges=biggest_edges,
        fatigue_watch=fatigue_watch,
    )


# Team logos and player headshots live on ESPN's CDN. Loading them straight
# from the browser would hand every visitor's IP to ESPN, so they're fetched
# server-side and re-served from our own domain (kept in memory only).
_IMG_SOURCES = {
    "team": ("https://a.espncdn.com/i/teamlogos/nba/500/{}.png", re.compile(r"[a-z]{2,5}")),
    "player": ("https://a.espncdn.com/i/headshots/nba/players/full/{}.png", re.compile(r"\d{3,9}")),
}
_IMG_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_IMG_CACHE_MAX = 100
_IMG_LOCK = threading.Lock()


@app.route("/img/<kind>/<key>.png")
def proxied_image(kind, key):
    source = _IMG_SOURCES.get(kind)
    if not source or not source[1].fullmatch(key):
        abort(404)
    cache_key = f"{kind}/{key}"
    with _IMG_LOCK:
        body = _IMG_CACHE.get(cache_key)
        if body is not None:
            _IMG_CACHE.move_to_end(cache_key)
    if body is None:
        try:
            upstream = requests.get(source[0].format(key), timeout=6)
        except requests.RequestException:
            abort(404)
        if upstream.status_code != 200 or not upstream.headers.get("content-type", "").startswith("image/"):
            abort(404)
        body = upstream.content
        with _IMG_LOCK:
            _IMG_CACHE[cache_key] = body
            while len(_IMG_CACHE) > _IMG_CACHE_MAX:
                _IMG_CACHE.popitem(last=False)
    resp = Response(body, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/data-version")
def data_version():
    # Lets the daily refresh confirm a deploy picked up the new snapshot
    # (and doubles as a cheap health/freshness check).
    meta = snapshot_store.load_meta()
    resp = jsonify({
        "refreshed_at": meta.get("refreshed_at"),
        "logs_refreshed_at": meta.get("logs_refreshed_at"),
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


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

    if is_paid:
        for row in history:
            player = find_player(row["player"], analyzer.players) if analyzer.is_ready() else None
            espn_id = find_espn_player_id(row["player"], player.team_abbr) if player else None
            row["headshot"] = espn_headshot_url(espn_id) if espn_id else None

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
    # The real Eastern-date slate, regular/postseason only: preseason games
    # aren't a meaningful basis for a graded track record.
    games = fetch_games_on(et_date()) or []
    games = [g for g in games if g["season_type"] in (2, 3)]
    count = track_record.snapshot_todays_predictions(analyzer, games)
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


@app.route("/api/suggest-line")
def api_suggest_line():
    # Backs the Analyze form's auto-filled Line field: a real season-average
    # starting point for whatever player/prop is currently selected, not a
    # blank box -- the user can still type over it before running. Same
    # baseline convention as Quick Looks/Biggest Edges, just looked up live
    # for any player instead of a fixed roster.
    if not analyzer.is_ready():
        return jsonify({"line": None})

    name = (request.args.get("player") or "").strip()
    prop_type = (request.args.get("prop_type") or "points").strip()
    if not name:
        return jsonify({"line": None})

    player = find_player(name, analyzer.players)
    if not player:
        return jsonify({"line": None})

    if prop_type in COMBO_PROP_TYPES:
        baseline = sum(getattr(player, _PROP_BASELINE_ATTR[p], 0) for p in COMBO_PROP_TYPES[prop_type])
    else:
        baseline = getattr(player, _PROP_BASELINE_ATTR.get(prop_type, "ppg"), 0)

    if baseline <= 0:
        return jsonify({"line": None})
    return jsonify({"line": _round_to_half(baseline)})


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
    if data.get("accept_terms") is not True:
        return jsonify({"error": f"You must be {MIN_AGE} or older and accept the Terms and Privacy Policy to create an account."}), 400

    redirect_to = f"{request.url_root.rstrip('/')}/auth/callback"
    consent = {
        "age_confirmed": MIN_AGE,
        "terms_version": LEGAL_VERSION,
        "terms_accepted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "marketing_opt_in": data.get("marketing_opt_in") is True,
    }
    try:
        auth.sign_up(email, password, name, redirect_to, consent)
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
