"""store.py testleri — geçici SQLite dosyasıyla, ağ yok."""
import pytest

import store
from store import init_db, pick_hit, save_coupon, list_coupons, settle_pending


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    init_db()


def _pick(fixture_id="100", market="match_result", selection="home",
          home="Ev FC", away="Dep FC"):
    return {
        "fixture": {"fixture_id": fixture_id,
                    "home": {"name": home}, "away": {"name": away}},
        "best_pick": {"market": market, "selection": selection,
                      "label": "Ev sahibi kazanır", "probability": 0.6,
                      "fair_odds": 1.67},
    }


# --- pick_hit: saf değerlendirme mantığı ---

@pytest.mark.parametrize("market,selection,hg,ag,expected", [
    ("match_result", "home", 2, 1, True),
    ("match_result", "home", 1, 1, False),
    ("match_result", "draw", 1, 1, True),
    ("match_result", "away", 0, 1, True),
    ("over_under_25", "over", 2, 1, True),
    ("over_under_25", "over", 1, 1, False),
    ("over_under_25", "under", 1, 1, True),
    ("btts", "yes", 1, 1, True),
    ("btts", "yes", 2, 0, False),
    ("btts", "no", 2, 0, True),
])
def test_pick_hit(market, selection, hg, ag, expected):
    assert pick_hit(market, selection, hg, ag) is expected


# --- kayıt + listeleme ---

def test_save_and_list_coupon():
    save_coupon("2026-07-07", "value", [_pick()], total_odds=1.67,
                combined_probability=0.6)
    coupons = list_coupons()
    assert len(coupons) == 1
    c = coupons[0]
    assert c["date"] == "2026-07-07"
    assert c["mode"] == "value"
    assert c["settled_at"] is None
    assert c["picks"][0]["fixture"]["fixture_id"] == "100"


# --- sonuçlandırma ---

def _fixtures_ft(hg, ag, status="FT"):
    def fake_get_fixtures(date_str):
        return [{"fixture_id": "100", "status": status,
                 "goals": {"home": str(hg), "away": str(ag)}}]
    return fake_get_fixtures


def test_settle_marks_hit():
    save_coupon("2026-07-07", "safe", [_pick()], 1.67, 0.6)
    settle_pending(_fixtures_ft(2, 0))          # ev kazandı → tutar
    c = list_coupons()[0]
    assert c["settled_at"] is not None
    assert c["hit_count"] == 1
    assert c["picks"][0]["hit"] is True


def test_settle_marks_miss():
    save_coupon("2026-07-07", "safe", [_pick()], 1.67, 0.6)
    settle_pending(_fixtures_ft(0, 3))          # deplasman kazandı → tutmaz
    c = list_coupons()[0]
    assert c["hit_count"] == 0
    assert c["picks"][0]["hit"] is False


def test_settle_skips_unfinished_matches():
    save_coupon("2026-07-07", "safe", [_pick()], 1.67, 0.6)
    settle_pending(_fixtures_ft(0, 0, status="NS"))   # maç oynanmadı
    assert list_coupons()[0]["settled_at"] is None    # beklemede kalır


# --- istatistik penceresi ---

from store import compute_stats


def _settled_coupon(mode, hits, total):
    """total maç, hits tanesi tutmuş bir sonuçlanmış kupon sözlüğü."""
    picks = [{"hit": i < hits} for i in range(total)]
    return {"mode": mode, "settled_at": "2026-07-07T00:00:00Z",
            "hit_count": hits, "picks": picks}


def test_compute_stats_pick_and_coupon_level():
    coupons = [
        _settled_coupon("safe", 3, 3),      # tam tuttu (kupon kazandı)
        _settled_coupon("safe", 2, 3),      # 2/3 (kupon kaybetti)
        _settled_coupon("value", 0, 2),     # hiç
    ]
    stats = compute_stats(coupons)
    overall = stats["overall"]
    # Tahmin bazında: (3+2+0) / (3+3+2) = 5/8
    assert overall["pick_hits"] == 5
    assert overall["pick_total"] == 8
    assert overall["pick_rate"] == pytest.approx(5/8, abs=0.001)
    # Kupon bazında: 3 kupondan 1'i tam tuttu
    assert overall["coupon_wins"] == 1
    assert overall["coupon_total"] == 3


def test_compute_stats_per_mode():
    coupons = [_settled_coupon("safe", 3, 3), _settled_coupon("value", 1, 4)]
    stats = compute_stats(coupons)
    assert stats["by_mode"]["safe"]["pick_rate"] == pytest.approx(1.0)
    assert stats["by_mode"]["value"]["pick_rate"] == pytest.approx(0.25)


def test_compute_stats_ignores_unsettled():
    coupons = [_settled_coupon("safe", 2, 2),
               {"mode": "safe", "settled_at": None, "picks": [{}, {}]}]
    stats = compute_stats(coupons)
    assert stats["overall"]["pick_total"] == 2      # beklemedeki sayılmaz


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats["overall"]["pick_total"] == 0
    assert stats["overall"]["pick_rate"] is None


# --- analiz: pazar bazlı + güven aralığı (kalibrasyon) ---

def _apick(label, prob, hit):
    return {"best_pick": {"label": label, "probability": prob}, "hit": hit}


def _settled_with_picks(mode, picks):
    return {"mode": mode, "settled_at": "2026-07-07T00:00:00Z", "picks": picks}


def test_compute_stats_by_market_groups_by_label():
    coupons = [_settled_with_picks("safe", [
        _apick("2.5 Alt", 0.7, True),
        _apick("2.5 Alt", 0.6, False),
        _apick("KG Var", 0.65, True),
    ])]
    bm = compute_stats(coupons)["by_market"]
    assert bm["2.5 Alt"] == {"hits": 1, "total": 2}
    assert bm["KG Var"] == {"hits": 1, "total": 1}


def test_compute_stats_calibration_buckets_by_probability():
    coupons = [_settled_with_picks("safe", [
        _apick("A", 0.55, True),    # 50-60
        _apick("B", 0.58, False),   # 50-60
        _apick("C", 0.72, True),    # 70-80
        _apick("D", 0.85, False),   # 80+
    ])]
    cal = compute_stats(coupons)["calibration"]
    assert cal["50-60%"] == {"hits": 1, "total": 2}
    assert cal["70-80%"] == {"hits": 1, "total": 1}
    assert cal["80%+"] == {"hits": 0, "total": 1}


def test_compute_stats_analysis_absent_without_pick_detail():
    # Detay (best_pick/probability) olmayan eski kayıtlar analizi çökertmemeli.
    stats = compute_stats([_settled_coupon("safe", 2, 3)])
    assert stats["by_market"] == {}
    assert all(v["total"] == 0 for v in stats["calibration"].values())


# --- market güvenilirliği (kupon kurucunun ceza/ödül katsayıları) ---

from store import RELIABILITY_PRIOR_WEIGHT, market_reliability


def _rpick(market, selection, prob, hit):
    return {"best_pick": {"market": market, "selection": selection,
                          "probability": prob}, "hit": hit}


def test_market_reliability_penalizes_overconfident_market():
    # 20 tahmin, iddia %60, gerçekleşen %40 → ham oran 0.4/0.6 ≈ 0.667.
    # 20/(20+12) ağırlıkla 1.0'a doğru çekilir → ~0.79.
    picks = [_rpick("btts", "no", 0.60, i < 8) for i in range(20)]
    ratios = market_reliability([_settled_with_picks("safe", picks)])
    weight = 20 / (20 + RELIABILITY_PRIOR_WEIGHT)
    expected = weight * (0.40 / 0.60) + (1 - weight)
    assert ratios[("btts", "no")] == pytest.approx(expected, abs=0.001)
    assert ratios[("btts", "no")] < 1.0


def test_market_reliability_clamp_floors_extreme_penalty():
    # Çok büyük ve çok kötü örneklem: kırpma alt sınırı devreye girer.
    picks = [_rpick("btts", "no", 0.70, False)] * 200
    ratios = market_reliability([_settled_with_picks("safe", picks)])
    assert ratios[("btts", "no")] == pytest.approx(0.75)


def test_market_reliability_shrinks_small_samples_toward_neutral():
    # Küçük örneklem: ham oran uçuk olsa bile düzeltme 1.0'a yakın kalmalı,
    # ama sert eşikteki gibi tamamen yok sayılmamalı.
    picks = [_rpick("btts", "no", 0.60, False)] * 3      # ham oran 0.0
    ratio = market_reliability([_settled_with_picks("safe", picks)])[("btts", "no")]
    assert ratio < 1.0                                    # ceza uygulanıyor
    assert ratio > 0.75                                   # ama kırpma sınırına inmiyor


def test_market_reliability_larger_sample_penalizes_harder():
    # Aynı ham oran, daha çok maç → düzeltme 1.0'dan daha uzağa gider.
    small = market_reliability([_settled_with_picks(
        "safe", [_rpick("btts", "no", 0.60, False)] * 3)])[("btts", "no")]
    large = market_reliability([_settled_with_picks(
        "safe", [_rpick("btts", "no", 0.60, False)] * 40)])[("btts", "no")]
    assert large < small


def test_market_reliability_shrinkage_is_neutral_at_prior_weight():
    # n == RELIABILITY_PRIOR_WEIGHT → ham oran ile 1.0 arasında tam ortada.
    n = RELIABILITY_PRIOR_WEIGHT
    picks = [_rpick("btts", "yes", 0.50, i < n // 2) for i in range(n)]
    ratio = market_reliability([_settled_with_picks("safe", picks)])[("btts", "yes")]
    raw = 0.5 / 0.50                                      # gerçekleşen / iddia = 1.0
    assert ratio == pytest.approx(0.5 * raw + 0.5 * 1.0, abs=0.01)


def test_market_reliability_ignores_unsettled_and_detail_free():
    picks = [_rpick("btts", "yes", 0.6, True)] * 20
    unsettled = {"mode": "safe", "settled_at": None, "picks": picks}
    no_detail = _settled_with_picks("safe", [{"best_pick": {}, "hit": True}] * 20)
    assert market_reliability([unsettled, no_detail]) == {}


def test_market_reliability_caps_boost():
    # Sürekli tutan market bile 1.05'ten fazla şişirilmez.
    picks = [_rpick("over_under_25", "over", 0.55, True)] * 20
    ratios = market_reliability([_settled_with_picks("safe", picks)])
    assert ratios[("over_under_25", "over")] == pytest.approx(1.05)
