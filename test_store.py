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
