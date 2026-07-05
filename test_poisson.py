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


from poisson import (blend_with_h2h, shrink_to_league_avg,
                     weighted_average, adjust_for_opponent)


def test_weighted_average_recent_matches_count_more():
    # Eski maçlar 0 gol, son maç 4 gol. Ağırlıklı ortalama basit ortalamadan yüksek.
    values = [0, 0, 0, 0, 4]     # kronolojik: en eski → en yeni
    simple = sum(values) / len(values)          # 0.8
    weighted = weighted_average(values)
    assert weighted > simple                    # son maç daha ağır
    assert weighted <= 4


def test_weighted_average_empty_is_zero():
    assert weighted_average([]) == 0.0


def test_weighted_average_uniform_equals_simple():
    # Tüm değerler eşitse ağırlık fark etmez.
    assert weighted_average([2, 2, 2]) == pytest.approx(2.0)


def test_adjust_for_opponent_strong_opponent_boosts_value():
    # Zayıf savunmaya (çok gol yiyen) atılan gol daha az değerli.
    # Rakip lig ortalamasından çok gol yiyorsa (zayıf), gol enflasyonu düşürülür.
    weak_def = adjust_for_opponent(goals=3, opponent_conceded_avg=2.5, league_avg=1.35)
    strong_def = adjust_for_opponent(goals=3, opponent_conceded_avg=0.5, league_avg=1.35)
    # Güçlü savunmaya atılan 3 gol, zayıf savunmaya atılandan daha değerli.
    assert strong_def > weak_def


def test_adjust_for_opponent_average_opponent_unchanged():
    v = adjust_for_opponent(goals=2, opponent_conceded_avg=1.35, league_avg=1.35)
    assert v == pytest.approx(2.0)


from poisson import predict_from_forms


def _mlog(entries):
    # entries: (scored, conceded, venue) most-recent first
    return [{"scored": s, "conceded": c, "venue": v, "opponent_id": "x"}
            for s, c, v in entries]


def test_predict_from_forms_none_when_empty_log():
    assert predict_from_forms([], _mlog([(1, 1, "away")]), 0, 1) is None
    assert predict_from_forms(_mlog([(1, 1, "home")]), [], 1, 0) is None


def test_predict_from_forms_strong_home_favoured():
    home = _mlog([(3, 0, "home")] * 6)      # evde bol gol, hiç yemiyor
    away = _mlog([(0, 3, "away")] * 6)      # deplasmanda gol atamıyor, çok yiyor
    p = predict_from_forms(home, away, 6, 6)
    assert p["match_result"]["home"] > 0.6
    assert p["expected_goals"]["home"] > p["expected_goals"]["away"]


def test_predict_from_forms_h2h_shifts_result():
    home = _mlog([(1, 1, "home")] * 6)
    away = _mlog([(1, 1, "away")] * 6)
    base = predict_from_forms(home, away, 6, 6)
    # Güçlü H2H ev golü lehine sonucu kaydırmalı.
    with_h2h = predict_from_forms(home, away, 6, 6,
                                  h2h={"home_scored_avg": 3.0, "away_scored_avg": 0.0,
                                       "meetings": 4})
    assert with_h2h["expected_goals"]["home"] > base["expected_goals"]["home"]


def test_shrink_pulls_small_samples_toward_league_avg():
    # 1 maçlık uçuk ortalama (5.0) lig ortalamasına ciddi yaklaşmalı.
    shrunk = shrink_to_league_avg(5.0, matches=1)
    assert LEAGUE_AVG_GOALS < shrunk < 5.0
    assert shrunk < 2.5                          # güçlü çekim


def test_shrink_keeps_large_samples_mostly_intact():
    shrunk = shrink_to_league_avg(2.0, matches=10)
    assert abs(shrunk - 2.0) < abs(shrink_to_league_avg(2.0, matches=1) - 2.0)
    assert shrunk > 1.7                          # 10 maçlık veri ağır basar


def test_shrink_no_matches_returns_league_avg():
    assert shrink_to_league_avg(0.0, matches=0) == LEAGUE_AVG_GOALS


def test_blend_with_h2h_moves_toward_h2h():
    blended = blend_with_h2h(form_avg=1.0, h2h_avg=3.0, meetings=5)
    assert 1.0 < blended < 3.0


def test_blend_with_h2h_no_meetings_returns_form():
    assert blend_with_h2h(form_avg=1.4, h2h_avg=0.0, meetings=0) == 1.4


# --- Genişletilmiş tahminler ---

def test_scoreline_list_sorted_and_complete():
    p = predict(1.6, 1.1, 1.3, 1.2)
    scores = p["scorelines"]
    # En olası skorla başlamalı, azalan olasılıkla sıralı.
    assert scores[0]["home"] == p["most_likely_score"]["home"]
    assert scores[0]["away"] == p["most_likely_score"]["away"]
    probs = [s["probability"] for s in scores]
    assert probs == sorted(probs, reverse=True)
    # Olasılıklar makul bir orana kadar toplamalı (ilk ~15 skor > %80).
    assert sum(probs[:15]) > 0.8


def test_extra_over_under_lines():
    p = predict(2.0, 1.5, 1.8, 1.4)
    ou = p["over_under"]
    # Daha düşük çizgi daha yüksek üst olasılığı vermeli.
    assert ou["1.5"]["over"] > ou["2.5"]["over"] > ou["3.5"]["over"]
    for line in ("1.5", "2.5", "3.5"):
        assert ou[line]["over"] + ou[line]["under"] == pytest.approx(1.0, abs=0.01)


def test_odd_even_sums_to_one():
    p = predict(1.4, 1.2, 1.5, 1.3)
    oe = p["odd_even"]
    assert oe["odd"] + oe["even"] == pytest.approx(1.0, abs=0.01)


def test_double_chance_from_match_result():
    p = predict(2.2, 0.7, 0.8, 2.0)
    dc = p["double_chance"]
    mr = p["match_result"]
    assert dc["1X"] == pytest.approx(mr["home"] + mr["draw"], abs=0.001)
    assert dc["12"] == pytest.approx(mr["home"] + mr["away"], abs=0.001)
    assert dc["X2"] == pytest.approx(mr["draw"] + mr["away"], abs=0.001)


def test_htft_probabilities_sum_to_one():
    p = predict(1.8, 1.0, 1.2, 1.3)
    htft = p["htft"]
    assert sum(htft.values()) == pytest.approx(1.0, abs=0.02)
    # Güçlü ev sahibinde 1/1 en olası İY/MS kombinasyonu olmalı.
    strong = predict(2.6, 0.5, 0.6, 2.3)["htft"]
    assert strong["1/1"] == max(strong.values())
