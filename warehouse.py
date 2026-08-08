"""Local match warehouse: ESPN history on disk instead of over the network.

Why this exists: every experiment on the model (backtests, constant tuning,
new signals) previously re-fetched hundreds of team schedules from ESPN,
putting a ~10 minute floor on a single measurement. That cost is what stopped
the model from being tuned against real data. With matches on disk the same
backtest runs in seconds, so a change can actually be evaluated before it
ships.

Scope is deliberately narrow: this module only *stores* what ESPN already
serves. It does not predict, rank or interpret. Two tables:

  matches   — one finished match, with the box-score stats ESPN attaches
  meta      — ingest bookkeeping (per league/season fetch timestamps)

Shot stats are stored alongside goals because shots are a less noisy scoring
signal than goals over small samples; whether they actually improve the model
is a question this warehouse exists to answer, not one it assumes.
"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from api_client import (API_BASE_URL, ESPN_ROOT, LEAGUES, ApiError, _get,
                        _score_value, _sides)

WAREHOUSE_PATH = "warehouse.db"

# ESPN box-score stat names worth keeping. Goals live in their own columns;
# these are the extra per-team numbers the summary endpoint attaches.
BOXSCORE_STATS = ("totalShots", "shotsOnTarget", "possessionPct",
                  "wonCorners", "foulsCommitted", "yellowCards", "redCards")

# Concurrency for schedule/summary fetches. ESPN is keyless but not free of
# rate limits; 8 matches the bulletin fetcher and has proven stable.
INGEST_WORKERS = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    event_id     TEXT PRIMARY KEY,
    league_slug  TEXT NOT NULL,
    season       INTEGER,
    kickoff      TEXT NOT NULL,          -- ISO timestamp, sorts chronologically
    home_id      TEXT NOT NULL,
    away_id      TEXT NOT NULL,
    home_name    TEXT,
    away_name    TEXT,
    home_goals   REAL NOT NULL,
    away_goals   REAL NOT NULL,
    stats_json   TEXT,                   -- box-score stats, NULL until enriched
    fetched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_league_kick
    ON matches (league_slug, kickoff);
-- Form lookups are per team and time-sliced, and a team appears as either
-- side, so both directions need their own index.
CREATE INDEX IF NOT EXISTS idx_matches_home ON matches (home_id, kickoff);
CREATE INDEX IF NOT EXISTS idx_matches_away ON matches (away_id, kickoff);

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _connect(path: str = WAREHOUSE_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str = WAREHOUSE_PATH) -> None:
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- ingest -----------------------------------------------------------------

def _team_schedule(team_id: str, league_slug: str, season: int | None):
    """Raw ESPN schedule payload for one team/season, or None on failure."""
    params = {"season": season} if season else {}
    try:
        return _get(f"{API_BASE_URL}/{league_slug}/teams/{team_id}/schedule", params)
    except ApiError:
        return None


def _league_team_ids(league_slug: str, season: int | None = None) -> list:
    """Team ids in a league, from ESPN's teams endpoint."""
    params = {"season": season} if season else {}
    try:
        payload = _get(f"{API_BASE_URL}/{league_slug}/teams", params)
    except ApiError:
        return []
    ids = []
    for group in payload.get("sports", []):
        for league in group.get("leagues", []):
            for entry in league.get("teams", []):
                tid = str(entry.get("team", {}).get("id", ""))
                if tid:
                    ids.append(tid)
    return ids


def _rows_from_schedule(payload: dict, league_slug: str) -> list:
    """Finished matches in a schedule payload as warehouse rows.

    A match appears in both teams' schedules; event_id is the primary key so
    the second copy is an upsert, not a duplicate.
    """
    season = (payload.get("season") or {}).get("year")
    rows = []
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        status = (competition.get("status") or {}).get("type") or {}
        if not status.get("completed"):
            continue
        try:
            home, away = _sides(competition)
        except (KeyError, StopIteration):
            continue
        hg, ag = _score_value(home), _score_value(away)
        if hg is None or ag is None:
            continue  # completed but scoreless payload = corrupt, not 0-0
        rows.append({
            "event_id": str(event["id"]),
            "league_slug": league_slug,
            "season": season,
            "kickoff": event["date"],
            "home_id": str(home["team"]["id"]),
            "away_id": str(away["team"]["id"]),
            "home_name": home["team"].get("displayName"),
            "away_name": away["team"].get("displayName"),
            "home_goals": hg,
            "away_goals": ag,
        })
    return rows


def upsert_matches(rows: list, path: str = WAREHOUSE_PATH) -> int:
    """Insert/refresh match rows. Existing stats_json is preserved."""
    if not rows:
        return 0
    now = _now()
    with _connect(path) as conn:
        conn.executemany(
            "INSERT INTO matches (event_id, league_slug, season, kickoff,"
            " home_id, away_id, home_name, away_name, home_goals, away_goals,"
            " fetched_at)"
            " VALUES (:event_id, :league_slug, :season, :kickoff, :home_id,"
            " :away_id, :home_name, :away_name, :home_goals, :away_goals,"
            f" '{now}')"
            " ON CONFLICT(event_id) DO UPDATE SET"
            "   home_goals = excluded.home_goals,"
            "   away_goals = excluded.away_goals,"
            "   season     = excluded.season,"
            "   kickoff    = excluded.kickoff",
            rows,
        )
    return len(rows)


def ingest_league(league_slug: str, seasons: list, path: str = WAREHOUSE_PATH) -> int:
    """Download every finished match for a league across the given seasons."""
    total = 0
    for season in seasons:
        team_ids = _league_team_ids(league_slug, season)
        if not team_ids:
            continue
        with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as pool:
            payloads = pool.map(
                lambda tid: _team_schedule(tid, league_slug, season), team_ids)
            rows = []
            for payload in payloads:
                if payload:
                    rows.extend(_rows_from_schedule(payload, league_slug))
        total += upsert_matches(rows, path)
        _set_meta(f"ingest:{league_slug}:{season}", _now(), path)
    return total


def ingest_days(dates: list, path: str = WAREHOUSE_PATH) -> int:
    """Store every finished match on the given dates, across all LEAGUES.

    This is the daily top-up: one scoreboard call per league per date, rather
    than the full per-team schedule crawl that ingest_league does. Cheap
    enough to run every morning, which is what keeps the warehouse from going
    stale between full rebuilds.
    """
    from api_client import get_fixtures

    rows = []
    for date_str in dates:
        try:
            fixtures = get_fixtures(date_str)
        except ApiError:
            continue          # one bad day must not abort the whole top-up
        for fx in fixtures:
            if fx["status"] != "FT":
                continue
            try:
                hg = float(fx["goals"]["home"])
                ag = float(fx["goals"]["away"])
            except (KeyError, TypeError, ValueError):
                continue      # finished but scoreless payload = corrupt
            rows.append({
                "event_id": fx["fixture_id"],
                "league_slug": fx["league_slug"],
                "season": None,       # scoreboard omits it; full ingest fills it
                "kickoff": fx["kickoff"],
                "home_id": fx["home"]["id"],
                "away_id": fx["away"]["id"],
                "home_name": fx["home"]["name"],
                "away_name": fx["away"]["name"],
                "home_goals": hg,
                "away_goals": ag,
            })
    n = upsert_matches(rows, path)
    if dates:
        _set_meta("last_daily_ingest", max(dates), path)
    return n


def catch_up(days_back: int = 7, path: str = WAREHOUSE_PATH) -> int:
    """Top up from the last recorded ingest, bounded by days_back.

    Re-fetches the most recent days even if already stored: a match finished
    after the previous run would otherwise never be picked up, and upsert
    makes re-storing free.
    """
    from datetime import date, timedelta

    today = datetime.now(timezone.utc).date()
    last = get_meta("last_daily_ingest", path)
    start = today - timedelta(days=days_back)
    if last:
        try:
            # Always redo the last stored day; matches settle after midnight.
            start = max(start, date.fromisoformat(last[:10]) - timedelta(days=1))
        except ValueError:
            pass
    dates = []
    day = start
    while day <= today:
        dates.append(day.isoformat())
        day += timedelta(days=1)
    return ingest_days(dates, path)


def _set_meta(key: str, value: str, path: str = WAREHOUSE_PATH) -> None:
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, _now()),
        )


def get_meta(key: str, path: str = WAREHOUSE_PATH):
    with _connect(path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# --- box-score enrichment ---------------------------------------------------

def _summary_stats(league_slug: str, event_id: str) -> dict | None:
    """Per-side box-score stats for one match, keyed 'home'/'away'."""
    try:
        payload = _get(f"{API_BASE_URL}/{league_slug}/summary", {"event": event_id})
    except ApiError:
        return None
    teams = (payload.get("boxscore") or {}).get("teams") or []
    if len(teams) < 2:
        return None
    # ESPN's boxscore.teams carries homeAway on the team entry itself in some
    # payloads and not others; fall back to header order (home first).
    out = {}
    for idx, entry in enumerate(teams):
        stats = {s.get("name"): s.get("displayValue")
                 for s in entry.get("statistics", [])
                 if s.get("name") in BOXSCORE_STATS}
        if not stats:
            continue
        side = entry.get("homeAway") or ("home" if idx == 0 else "away")
        out[side] = stats
    return out or None


def enrich_stats(limit: int = 500, path: str = WAREHOUSE_PATH) -> int:
    """Fetch box-score stats for matches that don't have them yet."""
    with _connect(path) as conn:
        pending = conn.execute(
            "SELECT event_id, league_slug FROM matches WHERE stats_json IS NULL"
            " ORDER BY kickoff DESC LIMIT ?", (limit,)).fetchall()

    def work(row):
        stats = _summary_stats(row["league_slug"], row["event_id"])
        return row["event_id"], stats

    updated = []
    with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as pool:
        for event_id, stats in pool.map(work, pending):
            if stats:
                updated.append((json.dumps(stats, ensure_ascii=False), event_id))

    if updated:
        with _connect(path) as conn:
            conn.executemany(
                "UPDATE matches SET stats_json = ? WHERE event_id = ?", updated)
    return len(updated)


# --- read side (what the model consumes) ------------------------------------

def team_log(team_id: str, before_iso: str | None = None,
             league_slug: str | None = None, limit: int = 30,
             path: str = WAREHOUSE_PATH) -> list:
    """A team's finished matches, most-recent first, in poisson's log shape.

    before_iso restricts to matches strictly before that timestamp — the
    leak-free slice a backtest needs, done in SQL instead of by refetching and
    filtering in Python.
    """
    where = ["(home_id = ? OR away_id = ?)"]
    params = [str(team_id), str(team_id)]
    if before_iso:
        where.append("kickoff < ?")
        params.append(before_iso)
    if league_slug:
        where.append("league_slug = ?")
        params.append(league_slug)
    params.append(limit)

    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM matches WHERE {' AND '.join(where)}"
            " ORDER BY kickoff DESC LIMIT ?", params).fetchall()

    log = []
    for r in rows:
        is_home = str(r["home_id"]) == str(team_id)
        stats = json.loads(r["stats_json"]) if r["stats_json"] else {}
        side, other = ("home", "away") if is_home else ("away", "home")
        log.append({
            "date": r["kickoff"],
            "scored": r["home_goals"] if is_home else r["away_goals"],
            "conceded": r["away_goals"] if is_home else r["home_goals"],
            "venue": side,
            "opponent_id": r["away_id"] if is_home else r["home_id"],
            "stats": stats.get(side, {}),
            "opponent_stats": stats.get(other, {}),
        })
    return log


def league_goal_average(league_slug: str | None = None,
                        before_iso: str | None = None,
                        path: str = WAREHOUSE_PATH) -> float | None:
    """Mean goals per team per match — the model's shrinkage prior.

    Returns None when there is no data, so callers decide the fallback rather
    than silently receiving a made-up constant.
    """
    where, params = [], []
    if league_slug:
        where.append("league_slug = ?")
        params.append(league_slug)
    if before_iso:
        where.append("kickoff < ?")
        params.append(before_iso)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _connect(path) as conn:
        row = conn.execute(
            f"SELECT AVG((home_goals + away_goals) / 2.0) AS avg_goals,"
            f" COUNT(*) AS n FROM matches {clause}", params).fetchone()
    if not row or not row["n"]:
        return None
    return round(row["avg_goals"], 3)


def stats_coverage(path: str = WAREHOUSE_PATH) -> dict:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN stats_json IS NOT NULL THEN 1 ELSE 0 END) AS with_stats"
            " FROM matches").fetchone()
    total = row["total"] or 0
    return {"total": total, "with_stats": row["with_stats"] or 0,
            "pct": round((row["with_stats"] or 0) / total * 100, 1) if total else 0.0}


def summary(path: str = WAREHOUSE_PATH) -> dict:
    with _connect(path) as conn:
        leagues = conn.execute(
            "SELECT league_slug, COUNT(*) AS n, MIN(kickoff) AS first,"
            " MAX(kickoff) AS last FROM matches GROUP BY league_slug"
            " ORDER BY n DESC").fetchall()
    return {"leagues": [dict(r) for r in leagues], **stats_coverage(path)}


def main() -> None:
    """CLI:
      python warehouse.py daily [gun]   — son N günü tazele (varsayılan 7)
      python warehouse.py full [sezon]  — tüm ligleri baştan indir (varsayılan 2)
      python warehouse.py stats [adet]  — eksik kutu skorlarını doldur
      python warehouse.py info          — depo özeti
    """
    import sys
    init_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "info":
        info = summary()
        for lg in info["leagues"]:
            print(f"  {lg['league_slug']:24} {lg['n']:5} maç  "
                  f"{lg['first'][:10]} → {lg['last'][:10]}")
        print(f"\nToplam {info['total']} maç, istatistikli {info['with_stats']} "
              f"(%{info['pct']})")
        return

    if cmd == "daily":
        n = catch_up(int(arg) if arg else 7)
        print(f"Günlük tazeleme: {n} maç kaydedildi")
        return

    if cmd == "stats":
        n = enrich_stats(int(arg) if arg else 500)
        cov = stats_coverage()
        print(f"{n} maça istatistik eklendi — kapsama %{cov['pct']} "
              f"({cov['with_stats']}/{cov['total']})")
        return

    if cmd == "full":
        this_year = datetime.now(timezone.utc).year
        seasons = [this_year - i for i in range(int(arg) if arg else 2)]
        for slug in LEAGUES:
            n = ingest_league(slug, seasons)
            print(f"  {slug:24} {n:5} maç")
        info = summary()
        print(f"\nToplam {info['total']} maç, istatistikli {info['with_stats']} "
              f"(%{info['pct']})")
        return

    print(main.__doc__)


if __name__ == "__main__":
    main()
