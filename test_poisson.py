"""poisson.py birim testleri — motor saf fonksiyondur, ağ gerektirmez."""
import pytest

from poisson import predict, LEAGUE_AVG_GOALS


# Ortalama bir takımın girdileri: lig ortalamasında atar ve yer.
AVG = LEAGUE_AVG_GOALS


def test_probabilities_sum_to_one():
    p = predict(AVG, AVG, AVG, AVG)
    mr = p["match_result"]
    assert mr["home"] + mr["draw"] + mr["away"] == pytest.approx(1.0, abs=0.01)
    ou = p["over_under_25"]
    assert ou["over"] + ou["under"] == pytest.approx(1.0, abs=0.01)
    kg = p["btts"]
    assert kg["yes"] + kg["no"] == pytest.approx(1.0, abs=0.01)


def test_equal_teams_home_advantage():
    # Eşit güçte takımlarda ev avantajı ev galibiyetini öne geçirmeli.
    p = predict(AVG, AVG, AVG, AVG)
    assert p["match_result"]["home"] > p["match_result"]["away"]


def test_strong_home_team_favoured():
    # Çok gol atan / az yiyen ev sahibi, zayıf deplasmana karşı net favori.
    p = predict(2.5, 0.5, 0.7, 2.2)
    assert p["match_result"]["home"] > 0.6
    assert p["expected_goals"]["home"] > p["expected_goals"]["away"]


def test_high_scoring_teams_favour_over():
    p = predict(2.4, 1.8, 2.1, 1.9)
    assert p["over_under_25"]["over"] > p["over_under_25"]["under"]


def test_most_likely_score_structure():
    p = predict(AVG, AVG, AVG, AVG)
    s = p["most_likely_score"]
    assert isinstance(s["home"], int) and isinstance(s["away"], int)
    # Kesin skor olasılığı doğası gereği düşüktür ama sıfır olamaz.
    assert 0.0 < s["probability"] < 0.5


def test_zero_averages_do_not_crash():
    # Hiç gol atamayan takım: model çökmemeli, deplasman/beraberlik öne çıkmalı.
    p = predict(0.0, 1.0, 1.0, 1.0)
    assert p["match_result"]["home"] < p["match_result"]["away"] + p["match_result"]["draw"]


from poisson import best_pick, fair_odds


def test_fair_odds_inverse_of_probability():
    assert fair_odds(0.5) == pytest.approx(2.0)
    assert fair_odds(0.79) == pytest.approx(1.27, abs=0.01)


def test_fair_odds_zero_probability_is_infinite():
    assert fair_odds(0.0) == float("inf")


def test_best_pick_selects_highest_broad_market():
    # Güçlü ev sahibi: en emin tahmin 'ev kazanır' olmalı, kesin skor asla seçilmez.
    p = predict(2.5, 0.5, 0.7, 2.2)
    pick = best_pick(p)
    assert pick["market"] == "match_result"
    assert pick["selection"] == "home"
    assert pick["probability"] == p["match_result"]["home"]
    assert pick["fair_odds"] == fair_odds(pick["probability"])


def test_best_pick_never_returns_exact_score():
    p = predict(1.0, 1.0, 1.0, 1.0)
    assert best_pick(p)["market"] != "most_likely_score"


def test_best_pick_min_odds_filters_low_odds():
    # 'Cesur' kupon modu: yalnızca adil oranı eşiğin üstündeki seçimlerden,
    # olasılığı en yüksek olan dönmeli.
    p = predict(2.5, 0.5, 0.7, 2.2)          # ev kazanır ~%70+, oranı < 2.00
    pick = best_pick(p, min_odds=2.0)
    assert pick is not None
    assert pick["fair_odds"] >= 2.0
    # Eşiği geçenler arasında en olası seçim olmalı.
    all_probs = [p[m][s] for (m, s) in [
        ("match_result", "home"), ("match_result", "draw"), ("match_result", "away"),
        ("over_under_25", "over"), ("over_under_25", "under"),
        ("btts", "yes"), ("btts", "no"),
    ] if 1 / p[m][s] >= 2.0]
    assert pick["probability"] == pytest.approx(max(all_probs), abs=0.001)


def test_best_pick_min_odds_none_when_impossible():
    p = predict(1.0, 1.0, 1.0, 1.0)
    assert best_pick(p, min_odds=100.0) is None
