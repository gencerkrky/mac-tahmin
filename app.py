"""Flask app: ties the API client and the Poisson engine together.

The API key stays server-side; the browser only ever receives JSON
predictions. Coupon building is a pure function (pick_top_predictions) so it
can be unit-tested without any network.
"""

import math
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import store
from ai_analysis import AiError, analyze_prediction
from api_client import (ApiError, LEAGUES, get_basketball_fixtures,
                        get_basketball_form, get_fixtures, get_h2h,
                        get_league_avg_conceded, get_team_form)
from basketball import predict_basketball
from poisson import (adjust_for_opponent, best_pick, blend_with_h2h,
                     predict, shrink_to_league_avg, weighted_average)

load_dotenv()

app = Flask(__name__, static_folder="static")
store.init_db()

# ESPN is quota-free; the cap only bounds coupon latency (2 form calls/match).
MAX_COUPON_CANDIDATES = 20
DEFAULT_COUPON_SIZE = 5

# Coupon modes: minimum fair odds a pick must have to enter the coupon.
# "safe" takes the most probable pick regardless of odds; "value" mimics the
# user's iddaa habit of only playing 2.00+ selections.
COUPON_MODES = {"safe": 0.0, "balanced": 1.5, "value": 2.0}

# Only not-yet-started fixtures make sense for predictions.
UPCOMING_STATUSES = {"NS", "TBD"}

# Minimum venue-specific matches before we trust a home/away split; below this
# we blend toward the team's overall form so a 1-game sample can't dominate.
MIN_VENUE_MATCHES = 4


def venue_weighted(log: list, venue: str, key: str) -> float:
    """Recency-weighted average of `key` (scored/conceded) for one venue.

    Falls back to the full log when the team has too few matches at that venue,
    so a side with only away games still gets a usable home estimate.
    """
    if not log:
        return 0.0
    venue_vals = [m[key] for m in log if m["venue"] == venue]
    all_vals = [m[key] for m in log]
    # weighted_average expects oldest-first; the log is most-recent-first.
    if len(venue_vals) >= MIN_VENUE_MATCHES:
        return weighted_average(list(reversed(venue_vals)))
    if not venue_vals:
        return weighted_average(list(reversed(all_vals)))
    # Blend the thin venue sample with overall form.
    venue_w = weighted_average(list(reversed(venue_vals)))
    all_w = weighted_average(list(reversed(all_vals)))
    frac = len(venue_vals) / MIN_VENUE_MATCHES
    return round(frac * venue_w + (1 - frac) * all_w, 3)

# AI analyses are paid API calls; cache per fixture for the process lifetime.
_ai_cache: dict = {}


def _opponent_adjusted_avg(log: list, venue: str, league_avg: float,
                           get_conceded) -> float:
    """Venue-weighted goals scored, each match corrected for how tough that
    opponent's defence was. get_conceded(opponent_id) -> conceded avg."""
    if not log:
        return 0.0
    venue_matches = [m for m in log if m["venue"] == venue]
    matches = venue_matches if len(venue_matches) >= MIN_VENUE_MATCHES else log
    adjusted = [adjust_for_opponent(m["scored"], get_conceded(m["opponent_id"]),
                                    league_avg) for m in matches]
    # weighted_average expects oldest-first.
    return weighted_average(list(reversed(adjusted)))


def predict_fixture(fx: dict, min_odds: float = 0.0) -> dict | None:
    """Full prediction for one fixture using the enhanced form model.

    Layers, in order: home/away split → recency weighting → opponent-strength
    correction → small-sample shrinkage → head-to-head blend.
    Returns None when no selection clears min_odds (mode-filtered coupons).
    """
    slug = fx["league_slug"]
    home_form = get_team_form(fx["home"]["id"], slug)
    away_form = get_team_form(fx["away"]["id"], slug)
    h2h = get_h2h(slug, fx["fixture_id"], fx["home"]["id"])

    # Opponent-strength baseline: average conceded across both teams' recent
    # opponents (cheap — those teams' forms are cached after this call).
    opp_ids = {m["opponent_id"] for m in home_form["log"]} | \
              {m["opponent_id"] for m in away_form["log"]}
    league_avg = get_league_avg_conceded(slug, list(opp_ids)) if opp_ids else 1.35

    def conceded_of(team_id: str) -> float:
        try:
            return get_team_form(team_id, slug)["conceded_avg"] or league_avg
        except ApiError:
            return league_avg

    # Expected goals: attack (opponent-adjusted, venue-specific) meets the
    # opposing side's venue-specific defence.
    home_attack = _opponent_adjusted_avg(home_form["log"], "home", league_avg, conceded_of)
    away_attack = _opponent_adjusted_avg(away_form["log"], "away", league_avg, conceded_of)
    home_def = venue_weighted(home_form["log"], "home", "conceded")
    away_def = venue_weighted(away_form["log"], "away", "conceded")

    # Shrink thin samples toward the league average.
    hs = shrink_to_league_avg(home_attack, home_form["matches"])
    hc = shrink_to_league_avg(home_def, home_form["matches"])
    as_ = shrink_to_league_avg(away_attack, away_form["matches"])
    ac = shrink_to_league_avg(away_def, away_form["matches"])

    m = h2h["meetings"]
    prediction = predict(
        blend_with_h2h(hs, h2h["home_scored_avg"], m),
        blend_with_h2h(hc, h2h["away_scored_avg"], m),
        blend_with_h2h(as_, h2h["away_scored_avg"], m),
        blend_with_h2h(ac, h2h["home_scored_avg"], m),
    )
    pick = best_pick(prediction, min_odds=min_odds)
    if pick is None:
        return None
    return {
        "fixture": fx,
        "form": {"home": home_form, "away": away_form},
        "prediction": prediction,
        "best_pick": pick,
    }


def pick_top_predictions(items: list, size: int) -> dict:
    """Pure coupon builder: top-N items by best-pick probability."""
    ranked = sorted(items, key=lambda i: i["best_pick"]["probability"], reverse=True)
    picks = ranked[:size]
    if not picks:
        return {"picks": [], "total_odds": 0, "combined_probability": 0}

    total_odds = math.prod(p["best_pick"]["fair_odds"] for p in picks)
    combined = math.prod(p["best_pick"]["probability"] for p in picks)
    return {
        "picks": picks,
        "total_odds": round(total_odds, 2),
        "combined_probability": round(combined, 4),
    }


def _parse_date(raw: str):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/leagues")
def leagues():
    return jsonify({"leagues": LEAGUES})


@app.get("/api/fixtures")
def fixtures():
    date_str = _parse_date(request.args.get("date", ""))
    if not date_str:
        return jsonify({"error": "Geçersiz tarih. Beklenen format: YYYY-MM-DD"}), 400
    try:
        return jsonify({"date": date_str, "fixtures": get_fixtures(date_str)})
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/basketball/fixtures")
def basketball_fixtures():
    date_str = _parse_date(request.args.get("date", ""))
    if not date_str:
        return jsonify({"error": "Geçersiz tarih. Beklenen format: YYYY-MM-DD"}), 400
    try:
        return jsonify({"date": date_str, "fixtures": get_basketball_fixtures(date_str)})
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/basketball/predict")
def basketball_predict_route():
    fixture_id = request.args.get("fixture", "")
    date_str = _parse_date(request.args.get("date", ""))
    if not fixture_id.isdigit() or not date_str:
        return jsonify({"error": "fixture ve date parametreleri zorunlu"}), 400
    try:
        fx = next((f for f in get_basketball_fixtures(date_str)
                   if f["fixture_id"] == fixture_id), None)
        if fx is None:
            return jsonify({"error": "Maç bulunamadı"}), 404
        home_form = get_basketball_form(fx["home"]["id"], fx["league_slug"])
        away_form = get_basketball_form(fx["away"]["id"], fx["league_slug"])
        prediction = predict_basketball(
            home_form["scored_avg"], home_form["conceded_avg"],
            away_form["scored_avg"], away_form["conceded_avg"],
        )
        return jsonify({"fixture": fx, "form": {"home": home_form, "away": away_form},
                        "prediction": prediction, "best_pick": prediction["best_pick"]})
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/predict")
def predict_route():
    # ESPN event ids are strings; validate as non-empty digits.
    fixture_id = request.args.get("fixture", "")
    date_str = _parse_date(request.args.get("date", ""))
    if not fixture_id.isdigit() or not date_str:
        return jsonify({"error": "fixture ve date parametreleri zorunlu"}), 400
    try:
        fx = next((f for f in get_fixtures(date_str) if f["fixture_id"] == fixture_id), None)
        if fx is None:
            return jsonify({"error": "Maç bulunamadı"}), 404
        return jsonify(predict_fixture(fx))
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/analyze")
def analyze():
    fixture_id = request.args.get("fixture", "")
    date_str = _parse_date(request.args.get("date", ""))
    if not fixture_id.isdigit() or not date_str:
        return jsonify({"error": "fixture ve date parametreleri zorunlu"}), 400

    if fixture_id in _ai_cache:
        return jsonify({"analysis": _ai_cache[fixture_id], "cached": True})

    try:
        fx = next((f for f in get_fixtures(date_str) if f["fixture_id"] == fixture_id), None)
        if fx is None:
            return jsonify({"error": "Maç bulunamadı"}), 404
        analysis = analyze_prediction(predict_fixture(fx))
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502
    except AiError as exc:
        return jsonify({"error": str(exc)}), 502

    _ai_cache[fixture_id] = analysis
    return jsonify({"analysis": analysis, "cached": False})


@app.get("/api/coupon")
def coupon():
    date_str = _parse_date(request.args.get("date", ""))
    size = request.args.get("size", default=DEFAULT_COUPON_SIZE, type=int)
    mode = request.args.get("mode", "safe")
    if not date_str:
        return jsonify({"error": "Geçersiz tarih. Beklenen format: YYYY-MM-DD"}), 400
    if not 1 <= size <= MAX_COUPON_CANDIDATES:
        return jsonify({"error": f"Kupon boyutu 1-{MAX_COUPON_CANDIDATES} arası olmalı"}), 400
    if mode not in COUPON_MODES:
        return jsonify({"error": f"Geçersiz mod. Seçenekler: {', '.join(COUPON_MODES)}"}), 400

    min_odds = COUPON_MODES[mode]
    try:
        upcoming = [f for f in get_fixtures(date_str) if f["status"] in UPCOMING_STATUSES]
        candidates = upcoming[:MAX_COUPON_CANDIDATES]
        analysed = [item for fx in candidates
                    if (item := predict_fixture(fx, min_odds=min_odds)) is not None]
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502

    result = pick_top_predictions(analysed, size)
    result["analysed_count"] = len(analysed)
    result["skipped_count"] = max(0, len(upcoming) - len(candidates))

    # Persist for the accuracy tracker; failure to save must not break the UI.
    if result["picks"]:
        try:
            store.save_coupon(date_str, mode, result["picks"],
                              result["total_odds"], result["combined_probability"])
        except Exception as exc:
            app.logger.error("Kupon kaydedilemedi: %s", exc)

    return jsonify(result)


@app.get("/api/history")
def history():
    # Settle anything whose matches have finished, then report.
    try:
        store.settle_pending(get_fixtures)
    except Exception as exc:
        app.logger.error("Sonuçlandırma hatası: %s", exc)

    coupons = store.list_coupons()
    settled = [c for c in coupons if c["settled_at"]]
    total_picks = sum(len(c["picks"]) for c in settled)
    total_hits = sum(c["hit_count"] for c in settled)
    return jsonify({
        "coupons": coupons,
        "stats": {
            "settled_coupons": len(settled),
            "total_picks": total_picks,
            "total_hits": total_hits,
            "hit_rate": round(total_hits / total_picks, 4) if total_picks else None,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)
