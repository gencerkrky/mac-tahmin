"""ESPN public soccer API client with in-memory caching.

ESPN's site API is keyless and quota-free, and (unlike API-Football's free
tier) serves both future fixtures and each team's season results — exactly
what the form model needs. All HTTP concerns live here: timeouts, error
normalisation and caching. Callers receive plain dicts and a single
exception type (ApiError) whose message is safe to show to end users.
"""

import requests

API_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
REQUEST_TIMEOUT_SECONDS = 15

# How many most-recent finished matches feed the form averages.
FORM_MATCH_COUNT = 10

# Fallback profile when a team has no finished matches yet: assume a
# league-average side (mirrors poisson.LEAGUE_AVG_GOALS).
FALLBACK_GOAL_AVG = 1.35

# Leagues shown in the bulletin and analysed by the coupon (ESPN slugs).
# Big European leagues pause in summer; the Nordic/American summer leagues
# keep the system testable year-round.
LEAGUES = {
    "fifa.world": "Dünya Kupası 2026",
    "tur.1": "Süper Lig (Türkiye)",
    "eng.1": "Premier League (İngiltere)",
    "esp.1": "La Liga (İspanya)",
    "ita.1": "Serie A (İtalya)",
    "ger.1": "Bundesliga (Almanya)",
    "fra.1": "Ligue 1 (Fransa)",
    "nor.1": "Eliteserien (Norveç)",
    "swe.1": "Allsvenskan (İsveç)",
    "fin.1": "Veikkausliiga (Finlandiya)",
    "bra.1": "Serie A (Brezilya)",
    "usa.1": "MLS (ABD)",
}

# ESPN match states → our short status codes (NS = not started).
_STATE_TO_STATUS = {"pre": "NS", "in": "LIVE", "post": "FT"}

# Process-lifetime cache: scoreboards keyed by date, form keyed by team.
_cache: dict = {}


class ApiError(Exception):
    """API/network failure with a user-presentable message."""


def clear_cache() -> None:
    _cache.clear()


def _get(url: str, params: dict | None = None) -> dict:
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"ESPN isteği başarısız: {exc}") from exc
    except ValueError as exc:  # invalid JSON body
        raise ApiError(f"ESPN geçersiz yanıt döndürdü: {exc}") from exc


def _sides(competition: dict) -> tuple[dict, dict]:
    """(home, away) competitor dicts of an ESPN competition."""
    competitors = competition["competitors"]
    home = next(c for c in competitors if c["homeAway"] == "home")
    away = next(c for c in competitors if c["homeAway"] == "away")
    return home, away


def get_fixtures(date_str: str) -> list:
    """All fixtures on a date across LEAGUES. One scoreboard call per league.

    A single league failing (ESPN hiccup, dormant competition) must not
    blank the whole bulletin, so per-league errors are skipped.
    """
    cache_key = ("fixtures", date_str)
    if cache_key in _cache:
        return _cache[cache_key]

    espn_date = date_str.replace("-", "")
    fixtures = []
    for slug, league_name in LEAGUES.items():
        try:
            payload = _get(f"{API_BASE_URL}/{slug}/scoreboard", {"dates": espn_date})
        except ApiError:
            continue
        for event in payload.get("events", []):
            competition = event["competitions"][0]
            home, away = _sides(competition)
            state = competition["status"]["type"]["state"]
            fixtures.append({
                "fixture_id": str(event["id"]),
                "kickoff": event["date"],
                "status": _STATE_TO_STATUS.get(state, state.upper()),
                "league_slug": slug,
                "league": league_name,
                "home": {"id": str(home["team"]["id"]), "name": home["team"]["displayName"]},
                "away": {"id": str(away["team"]["id"]), "name": away["team"]["displayName"]},
                "goals": {"home": home.get("score"), "away": away.get("score")},
            })

    _cache[cache_key] = fixtures
    return fixtures


def _score_value(competitor: dict) -> float:
    """ESPN scores appear as {'value': 2.0} in schedules, '2' in scoreboards."""
    score = competitor.get("score") or {}
    if isinstance(score, dict):
        return float(score.get("value", 0))
    return float(score)


def get_team_form(team_id: str, league_slug: str) -> dict:
    """Scoring/conceding averages over the team's last finished matches."""
    cache_key = ("form", league_slug, team_id)
    if cache_key in _cache:
        return _cache[cache_key]

    payload = _get(f"{API_BASE_URL}/{league_slug}/teams/{team_id}/schedule")

    finished = []
    for event in payload.get("events", []):
        competition = event["competitions"][0]
        if not competition["status"]["type"]["completed"]:
            continue
        finished.append((event["date"], competition))
    # Most recent first; keep only the form window.
    finished.sort(key=lambda pair: pair[0], reverse=True)
    finished = finished[:FORM_MATCH_COUNT]

    scored = conceded = 0.0
    matches = 0
    for _, competition in finished:
        home, away = _sides(competition)
        if str(home["team"]["id"]) == str(team_id):
            scored += _score_value(home)
            conceded += _score_value(away)
        else:
            scored += _score_value(away)
            conceded += _score_value(home)
        matches += 1

    if matches == 0:
        # New team / no data: league-average profile keeps the model running
        # with an honest low-confidence prediction.
        form = {"scored_avg": FALLBACK_GOAL_AVG, "conceded_avg": FALLBACK_GOAL_AVG,
                "matches": 0}
    else:
        form = {
            "scored_avg": round(scored / matches, 3),
            "conceded_avg": round(conceded / matches, 3),
            "matches": matches,
        }

    _cache[cache_key] = form
    return form
