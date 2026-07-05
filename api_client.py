"""API-Football client with in-memory caching.

All HTTP concerns live here: auth header, timeouts, error normalisation and
quota-friendly caching. Callers receive plain dicts and a single exception
type (ApiError) whose message is safe to show to end users.
"""

import os

import requests

API_BASE_URL = "https://v3.football.api-sports.io"
REQUEST_TIMEOUT_SECONDS = 15

# How many most-recent finished matches feed the form averages.
FORM_MATCH_COUNT = 10

# Leagues analysed by the coupon and fixture list (API-Football league ids).
# Big European leagues pause in summer; the Nordic/Eastern summer leagues
# keep the system testable year-round.
LEAGUES = {
    203: "Süper Lig (Türkiye)",
    39: "Premier League (İngiltere)",
    140: "La Liga (İspanya)",
    135: "Serie A (İtalya)",
    78: "Bundesliga (Almanya)",
    61: "Ligue 1 (Fransa)",
    103: "Eliteserien (Norveç)",
    113: "Allsvenskan (İsveç)",
    244: "Veikkausliiga (Finlandiya)",
    71: "Serie A (Brezilya)",
    253: "MLS (ABD)",
}

# Process-lifetime cache: fixtures keyed by date, form keyed by team id.
# The free tier allows ~100 requests/day; repeat lookups must not burn quota.
_cache: dict = {}


class ApiError(Exception):
    """API/network failure with a user-presentable message."""


def clear_cache() -> None:
    _cache.clear()


def _get(path: str, params: dict) -> dict:
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        raise ApiError("API_FOOTBALL_KEY tanımlı değil (.env dosyasını kontrol edin)")

    try:
        response = requests.get(
            f"{API_BASE_URL}{path}",
            headers={"x-apisports-key": api_key},
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"API-Football isteği başarısız: {exc}") from exc

    payload = response.json()
    # API-Football reports quota/auth problems inside a 200 body.
    if payload.get("errors"):
        raise ApiError(f"API-Football hata döndürdü: {payload['errors']}")
    return payload


def get_fixtures(date_str: str) -> list:
    """Fixtures on a date, filtered to LEAGUES. One request per date (cached)."""
    cache_key = ("fixtures", date_str)
    if cache_key in _cache:
        return _cache[cache_key]

    payload = _get("/fixtures", {"date": date_str})
    fixtures = []
    for item in payload.get("response", []):
        league_id = item["league"]["id"]
        if league_id not in LEAGUES:
            continue
        fixtures.append({
            "fixture_id": item["fixture"]["id"],
            "kickoff": item["fixture"]["date"],
            "status": item["fixture"]["status"]["short"],
            "league_id": league_id,
            "league": item["league"]["name"],
            "country": item["league"]["country"],
            "home": {"id": item["teams"]["home"]["id"], "name": item["teams"]["home"]["name"]},
            "away": {"id": item["teams"]["away"]["id"], "name": item["teams"]["away"]["name"]},
            "goals": item["goals"],
        })

    _cache[cache_key] = fixtures
    return fixtures


def get_team_form(team_id: int) -> dict:
    """Scoring/conceding averages over the team's last finished matches."""
    cache_key = ("form", team_id)
    if cache_key in _cache:
        return _cache[cache_key]

    payload = _get("/fixtures", {"team": team_id, "last": FORM_MATCH_COUNT})

    scored = conceded = matches = 0
    for item in payload.get("response", []):
        if item["fixture"]["status"]["short"] != "FT":
            continue
        goals_home = item["goals"]["home"]
        goals_away = item["goals"]["away"]
        if goals_home is None or goals_away is None:
            continue
        if item["teams"]["home"]["id"] == team_id:
            scored += goals_home
            conceded += goals_away
        else:
            scored += goals_away
            conceded += goals_home
        matches += 1

    if matches == 0:
        # New team / no data: fall back to a league-average profile so the
        # model still produces a (low-confidence) prediction.
        form = {"scored_avg": 1.35, "conceded_avg": 1.35, "matches": 0}
    else:
        form = {
            "scored_avg": round(scored / matches, 3),
            "conceded_avg": round(conceded / matches, 3),
            "matches": matches,
        }

    _cache[cache_key] = form
    return form
