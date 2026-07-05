"""api_client testleri — HTTP, monkeypatch ile taklit edilir; gerçek ağ yok."""
import pytest

import api_client
from api_client import ApiError, get_fixtures, get_team_form, clear_cache


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    clear_cache()


def _fixture_payload():
    # Minimal but structurally faithful API-Football /fixtures response.
    def item(fid, league_id, league, country, home_id, home, away_id, away):
        return {
            "fixture": {"id": fid, "date": "2026-07-05T17:00:00+00:00",
                        "status": {"short": "NS"}},
            "league": {"id": league_id, "name": league, "country": country},
            "teams": {"home": {"id": home_id, "name": home},
                      "away": {"id": away_id, "name": away}},
            "goals": {"home": None, "away": None},
        }
    in_scope_league = next(iter(api_client.LEAGUES))
    return {"errors": [], "response": [
        item(1, in_scope_league, "Test Lig", "Turkey", 10, "Ev FC", 20, "Dep FC"),
        item(2, 999999, "Kapsam Dışı Lig", "Nowhere", 30, "A", 40, "B"),
    ]}


def _form_payload():
    # Two finished matches for team 10: scored 3+1=4, conceded 1+0=1.
    return {"errors": [], "response": [
        {"fixture": {"status": {"short": "FT"}},
         "teams": {"home": {"id": 10}, "away": {"id": 99}},
         "goals": {"home": 3, "away": 1}},
        {"fixture": {"status": {"short": "FT"}},
         "teams": {"home": {"id": 88}, "away": {"id": 10}},
         "goals": {"home": 0, "away": 1}},
    ]}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def test_get_fixtures_filters_to_leagues(monkeypatch):
    calls = []
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(_fixture_payload()))
    result = get_fixtures("2026-07-05")
    assert len(result) == 1                      # out-of-scope league filtered out
    assert result[0]["fixture_id"] == 1
    assert result[0]["home"]["name"] == "Ev FC"


def test_get_fixtures_cached_per_date(monkeypatch):
    calls = []
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(_fixture_payload()))
    get_fixtures("2026-07-05")
    get_fixtures("2026-07-05")
    assert len(calls) == 1                       # second hit served from cache


def test_get_team_form_averages(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_form_payload()))
    form = get_team_form(10)
    assert form["matches"] == 2
    assert form["scored_avg"] == pytest.approx(2.0)    # (3 + 1) / 2
    assert form["conceded_avg"] == pytest.approx(0.5)  # (1 + 0) / 2


def test_api_error_on_api_level_errors(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse({"errors": {"token": "invalid"}, "response": []}))
    with pytest.raises(ApiError):
        get_fixtures("2026-07-05")


def test_api_error_on_network_failure(monkeypatch):
    def boom(*a, **k):
        raise api_client.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(api_client.requests, "get", boom)
    with pytest.raises(ApiError):
        get_team_form(10)
