"""api_client testleri — HTTP, monkeypatch ile taklit edilir; gerçek ağ yok."""
import pytest

import api_client
from api_client import ApiError, get_fixtures, get_team_form, clear_cache


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_cache()


def _scoreboard_payload():
    # Minimal but structurally faithful ESPN scoreboard response.
    def event(eid, home_id, home, away_id, away, state):
        return {
            "id": eid,
            "date": "2026-07-05T15:00Z",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"id": home_id, "displayName": home},
                     "score": "0"},
                    {"homeAway": "away", "team": {"id": away_id, "displayName": away},
                     "score": "0"},
                ],
                "status": {"type": {"state": state, "completed": False}},
            }],
            "status": {"type": {"state": state}},
        }
    return {"events": [
        event("100", "10", "Ev FC", "20", "Dep FC", "pre"),
        event("101", "30", "Canli FC", "40", "Rakip FC", "in"),
    ]}


def _schedule_payload():
    # Two completed matches for team 10: scored 3+1=4, conceded 1+0=1.
    def event(date, home_id, home_score, away_id, away_score, completed=True):
        return {
            "date": date,
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"id": home_id},
                     "score": {"value": home_score}},
                    {"homeAway": "away", "team": {"id": away_id},
                     "score": {"value": away_score}},
                ],
                "status": {"type": {"completed": completed}},
            }],
        }
    return {"events": [
        event("2026-06-20T15:00Z", "10", 3, "99", 1),
        event("2026-06-27T15:00Z", "88", 0, "10", 1),
        event("2026-07-10T15:00Z", "10", 0, "77", 0, completed=False),  # upcoming: ignored
    ]}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def test_get_fixtures_maps_events(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_scoreboard_payload()))
    result = get_fixtures("2026-07-05")
    # One scoreboard call per league; every league returns the same 2 events here.
    per_league = 2
    assert len(result) == per_league * len(api_client.LEAGUES)
    first = result[0]
    assert first["fixture_id"] == "100"
    assert first["status"] == "NS"                    # 'pre' mapped to NS
    assert first["home"]["name"] == "Ev FC"
    assert first["league_slug"] in api_client.LEAGUES
    live = result[1]
    assert live["status"] == "LIVE"                   # 'in' mapped to LIVE


def test_get_fixtures_cached_per_date(monkeypatch):
    calls = []
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(_scoreboard_payload()))
    get_fixtures("2026-07-05")
    n = len(calls)
    get_fixtures("2026-07-05")
    assert len(calls) == n                            # second hit fully cached


def test_get_team_form_averages(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_schedule_payload()))
    form = get_team_form("10", "swe.1")
    assert form["matches"] == 2                       # upcoming match ignored
    assert form["scored_avg"] == pytest.approx(2.0)   # (3 + 1) / 2
    assert form["conceded_avg"] == pytest.approx(0.5) # (1 + 0) / 2
    # Yeni: maç maçlık log, iç/dış ve rakip bilgisiyle.
    assert len(form["log"]) == 2
    assert {m["venue"] for m in form["log"]} == {"home", "away"}
    assert form["log"][0]["opponent_id"]              # rakip id dolu


def test_get_team_form_backfills_previous_seasons(monkeypatch):
    # Bu sezon hiç bitmiş maç yok (eleme turu) → önceki sezondan form çekilmeli.
    seasons_asked = []
    def fake_get(url, params=None, **kwargs):
        if params and "season" in params:
            seasons_asked.append(params["season"])
            if params["season"] == 2025:
                return FakeResponse(_schedule_payload())
            return FakeResponse({"events": []})       # 2024'te de veri yok
        return FakeResponse({"season": {"year": 2026}, "events": []})
    monkeypatch.setattr(api_client.requests, "get", fake_get)
    form = get_team_form("10", "uefa.europa.conf_qual")
    assert seasons_asked == [2025, 2024]              # geriye doğru denendi
    assert form["matches"] == 2                       # önceki sezonun 2 maçı
    assert form["scored_avg"] == pytest.approx(2.0)


def test_get_team_form_fallback_error_keeps_current_data(monkeypatch):
    # Önceki sezon isteği patlarsa eldeki (boş) veriyle lig ortalamasına düşer.
    def fake_get(url, params=None, **kwargs):
        if params and "season" in params:
            raise api_client.requests.exceptions.ConnectionError("down")
        return FakeResponse({"season": {"year": 2026}, "events": []})
    monkeypatch.setattr(api_client.requests, "get", fake_get)
    form = get_team_form("10", "uefa.europa.conf_qual")
    assert form["matches"] == 0
    assert form["scored_avg"] == form["conceded_avg"] > 0


def test_get_team_form_no_data_falls_back(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse({"events": []}))
    form = get_team_form("10", "swe.1")
    assert form["matches"] == 0
    assert form["scored_avg"] == form["conceded_avg"] > 0  # league-average profile


def _h2h_payload():
    # ESPN summary headToHeadGames: group team is one side; atVs 'vs' = home.
    return {"headToHeadGames": [{
        "team": {"id": "10"},
        "events": [
            # takım 10 evinde 3-1 kazandı → 10: 3 gol, rakip: 1
            {"atVs": "vs", "homeTeamScore": "3", "awayTeamScore": "1"},
            # takım 10 deplasmanda 0-2 kazandı → 10: 2 gol, rakip: 0
            {"atVs": "@", "homeTeamScore": "0", "awayTeamScore": "2"},
        ],
    }]}


def test_get_h2h_averages(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_h2h_payload()))
    h2h = api_client.get_h2h("swe.1", "999", home_team_id="10")
    assert h2h["meetings"] == 2
    assert h2h["home_scored_avg"] == pytest.approx(2.5)   # (3 + 2) / 2
    assert h2h["away_scored_avg"] == pytest.approx(0.5)   # (1 + 0) / 2


def _h2h_venue_payload():
    """Takım 10'un ev maçları ile deplasman maçları belirgin şekilde farklı."""
    return {"headToHeadGames": [{
        "team": {"id": "10"},
        "events": [
            # 10 evinde iki kez farklı kazandı → evde 3 gol/maç, 0 yedi
            {"atVs": "vs", "homeTeamScore": "3", "awayTeamScore": "0"},
            {"atVs": "vs", "homeTeamScore": "3", "awayTeamScore": "0"},
            # 10 deplasmanda iki kez farklı kaybetti → orada 0 attı, 4 yedi
            {"atVs": "@", "homeTeamScore": "4", "awayTeamScore": "0"},
            {"atVs": "@", "homeTeamScore": "4", "awayTeamScore": "0"},
        ],
    }]}


def test_get_h2h_prefers_same_venue_meetings(monkeypatch):
    # Takım 10 bu maçın ev sahibi → yalnız onun ev sahibi olduğu H2H'ler sayılmalı.
    # Tüm maçlar karışsaydı 1.5-2.0 çıkardı; venue ayrımıyla 3.0-0.0 olmalı.
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_h2h_venue_payload()))
    h2h = api_client.get_h2h("swe.1", "v1", home_team_id="10")
    assert h2h["meetings"] == 2
    assert h2h["home_scored_avg"] == pytest.approx(3.0)
    assert h2h["away_scored_avg"] == pytest.approx(0.0)


def test_get_h2h_same_venue_flips_with_home_side(monkeypatch):
    # Aynı veri, ama bu kez takım 20 ev sahibi → 10'un deplasman maçları geçerli.
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_h2h_venue_payload()))
    h2h = api_client.get_h2h("swe.1", "v2", home_team_id="20")
    assert h2h["meetings"] == 2
    assert h2h["home_scored_avg"] == pytest.approx(4.0)   # 20 evinde 4 attı
    assert h2h["away_scored_avg"] == pytest.approx(0.0)


def test_get_h2h_falls_back_when_too_few_same_venue(monkeypatch):
    # Tek ev maçı var (eşik 2) → tüm karşılaşmaların ortalamasına düşer.
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_h2h_payload()))
    h2h = api_client.get_h2h("swe.1", "v3", home_team_id="10")
    assert h2h["meetings"] == 2                            # ikisi de sayıldı
    assert h2h["home_scored_avg"] == pytest.approx(2.5)


def test_get_h2h_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise api_client.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(api_client.requests, "get", boom)
    h2h = api_client.get_h2h("swe.1", "999", home_team_id="10")
    assert h2h["meetings"] == 0                            # sessizce değil: güvenli varsayılan


def test_api_error_on_network_failure(monkeypatch):
    def boom(*a, **k):
        raise api_client.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(api_client.requests, "get", boom)
    with pytest.raises(ApiError):
        get_team_form("10", "swe.1")


def test_get_fixtures_tolerates_single_league_failure(monkeypatch):
    # One league endpoint failing must not blank the whole bulletin.
    calls = []
    def flaky(url, *a, **k):
        calls.append(url)
        if len(calls) == 1:
            raise api_client.requests.exceptions.ConnectionError("down")
        return FakeResponse(_scoreboard_payload())
    monkeypatch.setattr(api_client.requests, "get", flaky)
    result = get_fixtures("2026-07-05")
    assert len(result) == 2 * (len(api_client.LEAGUES) - 1)


def test_fallback_goal_avg_matches_model_prior():
    # api_client modelden bağımsız kalsın diye sabit kopyalanmıştır;
    # ikisi ayrışırsa gol beklentisi sessizce tutarsızlaşır.
    from poisson import LEAGUE_AVG_GOALS
    assert api_client.FALLBACK_GOAL_AVG == LEAGUE_AVG_GOALS
