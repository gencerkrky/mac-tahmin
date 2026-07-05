"""Flask app: ties the API client and the Poisson engine together.

The API key stays server-side; the browser only ever receives JSON
predictions. Coupon building is a pure function (pick_top_predictions) so it
can be unit-tested without any network.
"""

import math
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from ai_analysis import AiError, analyze_prediction
from api_client import ApiError, LEAGUES, get_fixtures, get_team_form
from poisson import best_pick, predict

load_dotenv()

app = Flask(__name__, static_folder="static")

# Coupon analysis is request-hungry (2 form calls per match); cap candidates
# so one coupon costs at most ~24 of the free tier's ~100 daily requests.
MAX_COUPON_CANDIDATES = 12
DEFAULT_COUPON_SIZE = 5

# Only not-yet-started fixtures make sense for predictions.
UPCOMING_STATUSES = {"NS", "TBD"}

# AI analyses are paid API calls; cache per fixture for the process lifetime.
_ai_cache: dict = {}


def predict_fixture(fx: dict) -> dict:
    """Combine both teams' form into a full prediction for one fixture."""
    home_form = get_team_form(fx["home"]["id"], fx["league_slug"])
    away_form = get_team_form(fx["away"]["id"], fx["league_slug"])
    prediction = predict(
        home_form["scored_avg"], home_form["conceded_avg"],
        away_form["scored_avg"], away_form["conceded_avg"],
    )
    return {
        "fixture": fx,
        "form": {"home": home_form, "away": away_form},
        "prediction": prediction,
        "best_pick": best_pick(prediction),
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
    if not date_str:
        return jsonify({"error": "Geçersiz tarih. Beklenen format: YYYY-MM-DD"}), 400
    if not 1 <= size <= MAX_COUPON_CANDIDATES:
        return jsonify({"error": f"Kupon boyutu 1-{MAX_COUPON_CANDIDATES} arası olmalı"}), 400

    try:
        upcoming = [f for f in get_fixtures(date_str) if f["status"] in UPCOMING_STATUSES]
        candidates = upcoming[:MAX_COUPON_CANDIDATES]
        analysed = [predict_fixture(fx) for fx in candidates]
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502

    result = pick_top_predictions(analysed, size)
    result["analysed_count"] = len(analysed)
    result["skipped_count"] = max(0, len(upcoming) - len(candidates))
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
