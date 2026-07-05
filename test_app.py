"""app.py testleri — kupon seçimi saf fonksiyon olarak, rotalar test client ile."""
import pytest

import app as app_module
from app import app, pick_top_predictions


def _item(prob, odds):
    return {"best_pick": {"probability": prob, "fair_odds": odds}}


def test_pick_top_predictions_orders_and_limits():
    items = [_item(0.55, 1.82), _item(0.79, 1.27), _item(0.61, 1.64)]
    coupon = pick_top_predictions(items, size=2)
    probs = [i["best_pick"]["probability"] for i in coupon["picks"]]
    assert probs == [0.79, 0.61]                     # descending, top-2 only


def test_pick_top_predictions_metrics():
    items = [_item(0.5, 2.0), _item(0.5, 2.0)]
    coupon = pick_top_predictions(items, size=2)
    assert coupon["total_odds"] == pytest.approx(4.0)
    assert coupon["combined_probability"] == pytest.approx(0.25)


def test_pick_top_predictions_empty():
    coupon = pick_top_predictions([], size=5)
    assert coupon["picks"] == []
    assert coupon["total_odds"] == 0
    assert coupon["combined_probability"] == 0


def test_fixtures_route_validates_date():
    client = app.test_client()
    resp = client.get("/api/fixtures?date=bozuk-tarih")
    assert resp.status_code == 400


def test_predict_route_requires_fixture_id():
    client = app.test_client()
    resp = client.get("/api/predict")
    assert resp.status_code == 400


def test_coupon_route_rejects_invalid_mode():
    client = app.test_client()
    resp = client.get("/api/coupon?date=2026-07-05&mode=yanlis")
    assert resp.status_code == 400


def test_fixtures_route_maps_api_error_to_502(monkeypatch):
    def boom(date_str):
        raise app_module.ApiError("kota doldu")
    monkeypatch.setattr(app_module, "get_fixtures", boom)
    client = app.test_client()
    resp = client.get("/api/fixtures?date=2026-07-05")
    assert resp.status_code == 502
    assert "kota" in resp.get_json()["error"]
