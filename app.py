"""Flask app: ties the API client and the Poisson engine together.

The API key stays server-side; the browser only ever receives JSON
predictions. Coupon building is a pure function (pick_top_predictions) so it
can be unit-tested without any network.
"""

import math
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import poisson
import store
from ai_analysis import AiError, analyze_prediction
from api_client import (ApiError, LEAGUES, get_basketball_fixtures,
                        get_basketball_form, get_fixtures, get_h2h,
                        get_league_avg_conceded, get_team_form)
from basketball import predict_basketball
from poisson import best_pick, predict_from_forms

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

# Confidence floor per mode: picks below it never enter the coupon, even if
# that leaves fewer than DEFAULT_COUPON_SIZE legs. Fewer-but-stronger legs
# raise the chance the whole coupon lands ("tam tutan kupon").
COUPON_MIN_PROBABILITY = {"safe": 0.60, "balanced": 0.50, "value": 0.40}

# Only not-yet-started fixtures make sense for predictions.
UPCOMING_STATUSES = {"NS", "TBD"}

# Default league average goals when opponent-strength baseline is unavailable.
DEFAULT_LEAGUE_AVG = 1.35

# AI analyses are paid API calls; cache per fixture for the process lifetime.
_ai_cache: dict = {}

# Re-exported so test_app and callers keep a stable name; logic lives in poisson.
venue_weighted = poisson.venue_weighted


def predict_fixture(fx: dict, min_odds: float = 0.0) -> dict | None:
    """Full prediction for one fixture using the enhanced form model.

    Fetches both teams' form + H2H, then delegates the numeric model to
    poisson.predict_from_forms (same code the backtest uses). A per-fixture
    network failure returns None so one bad match can't sink a whole coupon.
    Returns None when no selection clears min_odds (mode-filtered coupons).
    """
    slug = fx["league_slug"]
    try:
        home_form = get_team_form(fx["home"]["id"], slug)
        away_form = get_team_form(fx["away"]["id"], slug)
        h2h = get_h2h(slug, fx["fixture_id"], fx["home"]["id"])

        # Opponent-strength baseline: mean conceded across both teams' recent
        # opponents (cheap — those teams' forms are cached after this call).
        opp_ids = {m["opponent_id"] for m in home_form["log"]} | \
                  {m["opponent_id"] for m in away_form["log"]}
        league_avg = (get_league_avg_conceded(slug, list(opp_ids))
                      if opp_ids else DEFAULT_LEAGUE_AVG)

        def conceded_of(team_id: str) -> float:
            try:
                return get_team_form(team_id, slug)["conceded_avg"] or league_avg
            except ApiError:
                return league_avg

        prediction = predict_from_forms(
            home_form["log"], away_form["log"],
            home_form["matches"], away_form["matches"],
            h2h=h2h, league_avg=league_avg, conceded_of=conceded_of,
        )
    except ApiError as exc:
        app.logger.warning("Tahmin başarısız (%s): %s", fx.get("fixture_id"), exc)
        return None

    if prediction is None:
        return None
    pick = best_pick(prediction, min_odds=min_odds)
    if pick is None:
        return None
    return {
        "fixture": fx,
        "form": {"home": home_form, "away": away_form},
        "prediction": prediction,
        "best_pick": pick,
    }


def pick_top_predictions(items: list, size: int,
                         min_probability: float = 0.0) -> dict:
    """Pure coupon builder: top-N items by best-pick probability.

    min_probability drops low-confidence picks entirely — a shorter coupon
    beats padding it with weak legs that sink the whole ticket.
    """
    eligible = [i for i in items
                if i["best_pick"]["probability"] >= min_probability]
    ranked = sorted(eligible, key=lambda i: i["best_pick"]["probability"],
                    reverse=True)
    picks = ranked[:size]
    if not picks:
        return {"picks": [], "total_odds": 0, "combined_probability": 0}

    total_odds = math.prod(p["best_pick"]["fair_odds"] for p in picks)
    combined = math.prod(p["best_pick"]["probability"] for p in picks)
    # A zero-probability pick yields infinite odds → invalid JSON (Infinity).
    # Guard so the coupon endpoint always returns finite, serializable numbers.
    if not math.isfinite(total_odds):
        total_odds = 0.0
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

    result = pick_top_predictions(analysed, size, COUPON_MIN_PROBABILITY[mode])
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
    stats = store.compute_stats(coupons)
    return jsonify({"coupons": coupons, "stats": stats})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
