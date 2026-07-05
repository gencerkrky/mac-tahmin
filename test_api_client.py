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
