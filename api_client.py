"""ESPN public soccer API client with in-memory caching.

ESPN's site API is keyless and quota-free, and (unlike API-Football's free
tier) serves both future fixtures and each team's season results — exactly
what the form model needs. All HTTP concerns live here: timeouts, error
normalisation and caching. Callers receive plain dicts and a single
exception type (ApiError) whose message is safe to show to end users.
"""

from concurrent.futures import ThreadPoolExecutor

import requests

ESPN_ROOT = "https://site.api.espn.com/apis/site/v2/sports"
API_BASE_URL = f"{ESPN_ROOT}/soccer"
REQUEST_TIMEOUT_SECONDS = 15

# Basketball leagues (ESPN sport=basketball). Same scoreboard/schedule shape
# as soccer, but scores are points and the model is basketball.py.
BASKETBALL_LEAGUES = {
    "nba": "NBA (ABD)",
    "wnba": "WNBA (ABD Kadınlar)",
    "nba-development": "G League (ABD)",
    "mens-college-basketball": "NCAA Erkek (ABD)",
}
BASKETBALL_FORM_GAMES = 10

# How many most-recent finished matches feed the form averages. Larger than
# before so home/away splits still have enough games each.
FORM_MATCH_COUNT = 30

# Fallback profile when a team has no finished matches yet: assume a
# league-average side (mirrors poisson.LEAGUE_AVG_GOALS).
FALLBACK_GOAL_AVG = 1.35

# Leagues shown in the bulletin and analysed by the coupon (ESPN slugs).
# Big European leagues pause in summer; the Nordic/American summer leagues
# keep the system testable year-round.
LEAGUES = {
    "fifa.world": "Dünya Kupası 2026",
    "uefa.champions": "Şampiyonlar Ligi",
    "uefa.champions_qual": "Şampiyonlar Ligi Eleme",
    "uefa.europa": "Avrupa Ligi",
    "uefa.europa_qual": "Avrupa Ligi Eleme",
    "uefa.europa.conf": "Konferans Ligi",
    "uefa.europa.conf_qual": "Konferans Ligi Eleme",
    "tur.1": "Süper Lig (Türkiye)",
    "eng.1": "Premier League (İngiltere)",
    "esp.1": "La Liga (İspanya)",
    "ita.1": "Serie A (İtalya)",
    "ger.1": "Bundesliga (Almanya)",
    "fra.1": "Ligue 1 (Fransa)",
    "ned.1": "Eredivisie (Hollanda)",
    "por.1": "Primeira Liga (Portekiz)",
    "bel.1": "Pro League (Belçika)",
    "nor.1": "Eliteserien (Norveç)",
    "swe.1": "Allsvenskan (İsveç)",
    "fin.1": "Veikkausliiga (Finlandiya)",
    "irl.1": "Premier Division (İrlanda)",
    "isl.1": "Besta deild (İzlanda)",
    "jpn.1": "J1 League (Japonya)",
    "kor.1": "K League 1 (G. Kore)",
    "arg.1": "Liga Profesional (Arjantin)",
    "arg.2": "Primera Nacional (Arjantin)",
    "mex.1": "Liga MX (Meksika)",
    "bra.1": "Serie A (Brezilya)",
    "bra.2": "Serie B (Brezilya)",
    "ecu.1": "Liga Pro (Ekvador)",
    "col.1": "Primera A (Kolombiya)",
    "chi.1": "Primera División (Şili)",
    "usa.1": "MLS (ABD)",
    "usa.usl.1": "USL Championship (ABD)",
    "chn.1": "Süper Lig (Çin)",
    "aus.1": "A-League (Avustralya)",
}

# Scoreboard calls are independent; fetch them concurrently so a ~28-league
# bulletin loads in ~1 network round trip instead of 28 sequential ones.
FETCH_WORKERS = 8

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

    def _fetch(slug):
        try:
            return slug, _get(f"{API_BASE_URL}/{slug}/scoreboard", {"dates": espn_date})
        except ApiError:
            return slug, None

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(_fetch, LEAGUES))

    fixtures = []
    for slug, payload in results:
        if payload is None:
            continue
        league_name = LEAGUES[slug]
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

    fixtures.sort(key=lambda f: f["kickoff"])
    _cache[cache_key] = fixtures
    return fixtures


def _score_value(competitor: dict) -> float:
    """ESPN scores appear as {'value': 2.0} in schedules, '2' in scoreboards."""
    score = competitor.get("score") or {}
    if isinstance(score, dict):
        return float(score.get("value", 0))
    return float(score)


def get_basketball_fixtures(date_str: str) -> list:
    """Basketball games on a date across BASKETBALL_LEAGUES (points, not goals)."""
    cache_key = ("bball_fixtures", date_str)
    if cache_key in _cache:
        return _cache[cache_key]

    espn_date = date_str.replace("-", "")

    def _fetch(slug):
        try:
            return slug, _get(f"{ESPN_ROOT}/basketball/{slug}/scoreboard",
                              {"dates": espn_date})
        except ApiError:
            return slug, None

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(_fetch, BASKETBALL_LEAGUES))

    fixtures = []
    for slug, payload in results:
        if payload is None:
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
                "league": BASKETBALL_LEAGUES[slug],
                "sport": "basketball",
                "home": {"id": str(home["team"]["id"]), "name": home["team"]["displayName"]},
                "away": {"id": str(away["team"]["id"]), "name": away["team"]["displayName"]},
                "goals": {"home": home.get("score"), "away": away.get("score")},
            })

    fixtures.sort(key=lambda f: f["kickoff"])
    _cache[cache_key] = fixtures
    return fixtures


def get_basketball_form(team_id: str, league_slug: str) -> dict:
    """Points scored/allowed averages over a basketball team's recent games."""
    cache_key = ("bball_form", league_slug, team_id)
    if cache_key in _cache:
        return _cache[cache_key]

    payload = _get(f"{ESPN_ROOT}/basketball/{league_slug}/teams/{team_id}/schedule", {})

    finished = []
    for event in payload.get("events", []):
        competition = event["competitions"][0]
        if not competition["status"]["type"]["completed"]:
            continue
        finished.append((event["date"], competition))
    finished.sort(key=lambda pair: pair[0], reverse=True)
    finished = finished[:BASKETBALL_FORM_GAMES]

    scored = conceded = 0.0
    games = 0
    for _, competition in finished:
        home, away = _sides(competition)
        if str(home["team"]["id"]) == str(team_id):
            scored += _score_value(home)
            conceded += _score_value(away)
        else:
            scored += _score_value(away)
            conceded += _score_value(home)
        games += 1

    if games == 0:
        from basketball import LEAGUE_AVG_POINTS
        form = {"scored_avg": LEAGUE_AVG_POINTS, "conceded_avg": LEAGUE_AVG_POINTS,
                "games": 0}
    else:
        form = {"scored_avg": round(scored / games, 1),
                "conceded_avg": round(conceded / games, 1), "games": games}

    _cache[cache_key] = form
    return form


def get_h2h(league_slug: str, event_id: str, home_team_id: str) -> dict:
    """Head-to-head goal averages between the two sides of a fixture.

    ESPN's summary endpoint lists past meetings from one team's perspective
    ('vs' = that team at home, '@' = away). A failed/malformed response is a
    prediction-quality loss, not a fatal error — return zero meetings so the
    model falls back to pure form.
    """
    cache_key = ("h2h", event_id)
    if cache_key in _cache:
        return _cache[cache_key]

    empty = {"home_scored_avg": 0.0, "away_scored_avg": 0.0, "meetings": 0}
    try:
        payload = _get(f"{API_BASE_URL}/{league_slug}/summary", {"event": event_id})
    except ApiError:
        return empty

    groups = payload.get("headToHeadGames") or []
    if not groups:
        _cache[cache_key] = empty
        return empty

    group = groups[0]
    group_team_id = str(group.get("team", {}).get("id", ""))
    group_is_home_side = group_team_id == str(home_team_id)

    team_goals = opp_goals = 0.0
    meetings = 0
    for game in group.get("events", []):
        try:
            hg = float(game["homeTeamScore"])
            ag = float(game["awayTeamScore"])
        except (KeyError, TypeError, ValueError):
            continue
        # 'vs' → group team hosted this meeting, '@' → played away.
        if game.get("atVs") == "vs":
            team_goals += hg
            opp_goals += ag
        else:
            team_goals += ag
            opp_goals += hg
        meetings += 1

    if meetings == 0:
        _cache[cache_key] = empty
        return empty

    team_avg = round(team_goals / meetings, 3)
    opp_avg = round(opp_goals / meetings, 3)
    h2h = {
        "home_scored_avg": team_avg if group_is_home_side else opp_avg,
        "away_scored_avg": opp_avg if group_is_home_side else team_avg,
        "meetings": meetings,
    }
    _cache[cache_key] = h2h
    return h2h


def get_team_form(team_id: str, league_slug: str) -> dict:
    """Recent match log for a team, split by venue, most-recent first.

    Returns raw per-match records (goals for/against, venue, opponent id) so
    the prediction layer can apply recency weighting, home/away splitting and
    opponent-strength correction. Aggregate averages are included for the
    fallback / display path.
    """
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
    finished.sort(key=lambda pair: pair[0], reverse=True)
    finished = finished[:FORM_MATCH_COUNT]

    matches = []  # most-recent first
    for _, competition in finished:
        home, away = _sides(competition)
        if str(home["team"]["id"]) == str(team_id):
            matches.append({
                "scored": _score_value(home), "conceded": _score_value(away),
                "venue": "home", "opponent_id": str(away["team"]["id"]),
            })
        else:
            matches.append({
                "scored": _score_value(away), "conceded": _score_value(home),
                "venue": "away", "opponent_id": str(home["team"]["id"]),
            })

    if not matches:
        form = {"scored_avg": FALLBACK_GOAL_AVG, "conceded_avg": FALLBACK_GOAL_AVG,
                "matches": 0, "log": []}
    else:
        form = {
            "scored_avg": round(sum(m["scored"] for m in matches) / len(matches), 3),
            "conceded_avg": round(sum(m["conceded"] for m in matches) / len(matches), 3),
            "matches": len(matches),
            "log": matches,
        }

    _cache[cache_key] = form
    return form


def get_league_avg_conceded(league_slug: str, team_ids: list) -> float:
    """Average goals conceded per team across the given teams in a league.

    Used as the opponent-strength baseline. Falls back to the global constant
    if no data is available. Each team's form is cached, so this is cheap when
    the teams were already fetched for predictions.
    """
    totals = []
    for tid in team_ids:
        try:
            form = get_team_form(tid, league_slug)
        except ApiError:
            continue
        if form["matches"] > 0:
            totals.append(form["conceded_avg"])
    if not totals:
        return FALLBACK_GOAL_AVG
    return round(sum(totals) / len(totals), 3)
