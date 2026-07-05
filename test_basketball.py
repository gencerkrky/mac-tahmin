"""basketball.py testleri — saf istatistik, ağ yok."""
import pytest

from basketball import predict_basketball, LEAGUE_AVG_POINTS


def test_probabilities_sum_to_one():
    p = predict_basketball(105, 100, 98, 103)
    mr = p["match_result"]
    assert mr["home"] + mr["away"] == pytest.approx(1.0, abs=0.01)
    tl = p["total_line"]
    assert tl["over"] + tl["under"] == pytest.approx(1.0, abs=0.01)


def test_stronger_team_favoured():
    # Çok sayı atan / az yiyen ev sahibi net favori olmalı.
    p = predict_basketball(115, 95, 98, 112)
    assert p["match_result"]["home"] > 0.7
    assert p["expected_points"]["home"] > p["expected_points"]["away"]


def test_home_advantage_breaks_ties():
    p = predict_basketball(LEAGUE_AVG_POINTS, LEAGUE_AVG_POINTS,
                           LEAGUE_AVG_POINTS, LEAGUE_AVG_POINTS)
    assert p["match_result"]["home"] > p["match_result"]["away"]


def test_spread_and_total_present():
    p = predict_basketball(110, 105, 104, 108)
    # Beklenen fark (handikap) ve toplam çizgisi dolu olmalı.
    assert isinstance(p["spread"], float)
    assert p["expected_total"] > 150            # basketbolda toplam yüksek
    assert 0 < p["total_line"]["over"] < 1


def test_best_pick_structure():
    p = predict_basketball(115, 95, 98, 112)
    bp = p["best_pick"]
    assert bp["market"] in ("match_result", "total_line")
    assert 0.5 <= bp["probability"] <= 1.0
    assert bp["fair_odds"] == round(1 / bp["probability"], 2)
