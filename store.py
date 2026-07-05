"""Coupon persistence and accuracy tracking (SQLite, stdlib only).

Every generated coupon is saved; once its matches finish, settle_pending()
compares each pick with the real score so the panel can show a running
hit-rate. Picks are stored as a JSON blob — the schema is one flat table,
which is plenty at this scale.
"""

import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "data.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- maç günü (YYYY-MM-DD)
    mode TEXT NOT NULL,
    picks_json TEXT NOT NULL,
    total_odds REAL NOT NULL,
    combined_probability REAL NOT NULL,
    created_at TEXT NOT NULL,
    settled_at TEXT,
    hit_count INTEGER
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_SCHEMA)


def pick_hit(market: str, selection: str, home_goals: int, away_goals: int) -> bool:
    """Did this selection win given the final score?"""
    total = home_goals + away_goals
    if market == "match_result":
        return {
            "home": home_goals > away_goals,
            "draw": home_goals == away_goals,
            "away": home_goals < away_goals,
        }[selection]
    if market == "over_under_25":
        return total >= 3 if selection == "over" else total <= 2
    if market == "btts":
        both = home_goals >= 1 and away_goals >= 1
        return both if selection == "yes" else not both
    raise ValueError(f"Bilinmeyen market: {market}")


def save_coupon(date: str, mode: str, picks: list, total_odds: float,
                combined_probability: float) -> None:
    # Store only what settlement and display need; predictions stay lean.
    slim = [{
        "fixture": {
            "fixture_id": p["fixture"]["fixture_id"],
            "home": {"name": p["fixture"]["home"]["name"]},
            "away": {"name": p["fixture"]["away"]["name"]},
        },
        "best_pick": p["best_pick"],
    } for p in picks]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO coupons (date, mode, picks_json, total_odds,"
            " combined_probability, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (date, mode, json.dumps(slim, ensure_ascii=False), total_odds,
             combined_probability, datetime.now(timezone.utc).isoformat()),
        )


def list_coupons(limit: int = 50) -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM coupons ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    coupons = []
    for row in rows:
        c = dict(row)
        c["picks"] = json.loads(c.pop("picks_json"))
        coupons.append(c)
    return coupons


def _empty_bucket() -> dict:
    return {"pick_hits": 0, "pick_total": 0, "coupon_wins": 0, "coupon_total": 0}


def _finalize(bucket: dict) -> dict:
    """Add rate fields (None when no data) to a raw count bucket."""
    pt, ct = bucket["pick_total"], bucket["coupon_total"]
    bucket["pick_rate"] = round(bucket["pick_hits"] / pt, 4) if pt else None
    bucket["coupon_rate"] = round(bucket["coupon_wins"] / ct, 4) if ct else None
    return bucket


def compute_stats(coupons: list) -> dict:
    """Hit statistics over settled coupons: overall + per mode.

    Pick-level: each prediction counts. Coupon-level: a coupon 'wins' only if
    every one of its picks hit (iddaa logic). Unsettled coupons are ignored.
    Caller is responsible for date-windowing the input (e.g. last 30 days).
    """
    overall = _empty_bucket()
    by_mode: dict = {}

    for c in coupons:
        # DB kayıtları 'settled_at' (str), statik site 'settled' (bool) kullanır.
        is_settled = c.get("settled_at") or c.get("settled")
        if not is_settled or not c.get("picks"):
            continue
        picks = c["picks"]
        hits = sum(1 for p in picks if p.get("hit"))
        won = hits == len(picks)

        mode = c.get("mode", "?")
        bucket = by_mode.setdefault(mode, _empty_bucket())
        for target in (overall, bucket):
            target["pick_hits"] += hits
            target["pick_total"] += len(picks)
            target["coupon_wins"] += 1 if won else 0
            target["coupon_total"] += 1

    return {
        "overall": _finalize(overall),
        "by_mode": {m: _finalize(b) for m, b in by_mode.items()},
    }


def settle_pending(get_fixtures_fn) -> int:
    """Evaluate unsettled coupons whose matches have finished.

    get_fixtures_fn(date_str) must return fixture dicts with fixture_id,
    status and goals — injected so this module stays network-free.
    Returns the number of coupons settled.
    """
    with _connect() as conn:
        pending = conn.execute(
            "SELECT * FROM coupons WHERE settled_at IS NULL"
        ).fetchall()

    settled = 0
    for row in pending:
        picks = json.loads(row["picks_json"])
        try:
            fixtures = {f["fixture_id"]: f for f in get_fixtures_fn(row["date"])}
        except Exception:
            # Network hiccup: leave the coupon pending, try again next call.
            continue

        results = []
        for p in picks:
            fx = fixtures.get(p["fixture"]["fixture_id"])
            if fx is None or fx["status"] != "FT":
                results = None  # at least one match unfinished → stay pending
                break
            goals = fx["goals"]
            try:
                hg, ag = int(float(goals["home"])), int(float(goals["away"]))
            except (KeyError, TypeError, ValueError):
                results = None
                break
            bp = p["best_pick"]
            p["hit"] = pick_hit(bp["market"], bp["selection"], hg, ag)
            p["final_score"] = f"{hg}-{ag}"
            results = picks

        if results is None:
            continue

        hit_count = sum(1 for p in results if p["hit"])
        with _connect() as conn:
            conn.execute(
                "UPDATE coupons SET picks_json = ?, settled_at = ?, hit_count = ?"
                " WHERE id = ?",
                (json.dumps(results, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(), hit_count, row["id"]),
            )
        settled += 1
    return settled
