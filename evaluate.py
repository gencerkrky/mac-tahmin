"""Model evaluation over the local warehouse — no network, seconds not minutes.

backtest.py answers "how did the live pipeline do on these dates" and pays
ESPN latency for every team it touches. This module answers "is variant A of
the model better than variant B" over the whole stored history, which is the
question that actually decides whether a change ships.

Two things it reports that raw hit-rate hides:
  Brier score  — how good the probabilities are, not just the top pick. A
                 model that says 51% and is right slightly more often scores
                 better than one that says 90% and is right just as often.
  calibration  — does "70%" mean 70%? Reported per confidence band, since a
                 model can be well calibrated overall and badly calibrated
                 exactly where the coupon builder picks from.
"""

import math
from collections import defaultdict

import warehouse as W
from poisson import best_pick, predict_from_forms

# Minimum matches each side needs before a fixture is predictable at all.
MIN_LOG = 3

# Confidence bands for the calibration table, as (label, lower bound).
BANDS = (("50-60%", 0.50), ("60-70%", 0.60), ("70-80%", 0.70), ("80%+", 0.80))


def _band(p: float) -> str:
    label = BANDS[0][0]
    for name, lo in BANDS:
        if p >= lo:
            label = name
    return label


def pick_hit(market: str, selection: str, hg: float, ag: float) -> bool:
    """Did this selection win? Mirrors store.pick_hit, kept local so the
    evaluator has no dependency on the persistence layer."""
    total = hg + ag
    if market == "match_result":
        return {"home": hg > ag, "draw": hg == ag, "away": hg < ag}[selection]
    if market == "over_under_25":
        return total >= 3 if selection == "over" else total <= 2
    if market == "btts":
        both = hg >= 1 and ag >= 1
        return both if selection == "yes" else not both
    raise ValueError(f"Bilinmeyen market: {market}")


def _conceded_map(logs: dict) -> dict:
    """team_id -> mean goals conceded, from already-loaded logs."""
    out = {}
    for tid, log in logs.items():
        if log:
            out[tid] = sum(m["conceded"] for m in log) / len(log)
    return out


def evaluate(matches: list, predict_fn=None, league_avg_fn=None,
             path: str = W.WAREHOUSE_PATH, log_limit: int = 30) -> dict:
    """Run the model over stored matches and score it.

    matches: rows from load_matches(). Each is predicted using ONLY matches
    that kicked off earlier, so there is no look-ahead.
    predict_fn: swap in a model variant; defaults to predict_from_forms.
    league_avg_fn: (league_slug, kickoff) -> float prior; defaults to the
    warehouse's own time-sliced league average.
    """
    predict_fn = predict_fn or predict_from_forms
    rows = []

    for m in matches:
        cutoff = m["kickoff"]
        slug = m["league_slug"]
        home_log = W.team_log(m["home_id"], before_iso=cutoff, league_slug=slug,
                              limit=log_limit, path=path)
        away_log = W.team_log(m["away_id"], before_iso=cutoff, league_slug=slug,
                              limit=log_limit, path=path)
        if len(home_log) < MIN_LOG or len(away_log) < MIN_LOG:
            continue

        if league_avg_fn is not None:
            league_avg = league_avg_fn(slug, cutoff)
        else:
            league_avg = W.league_goal_average(slug, before_iso=cutoff, path=path)
        if league_avg is None:
            continue

        # Opponent strength from the two sides' own opponents, time-sliced.
        opp_ids = {x["opponent_id"] for x in home_log} | \
                  {x["opponent_id"] for x in away_log}
        opp_logs = {tid: W.team_log(tid, before_iso=cutoff, league_slug=slug,
                                    limit=log_limit, path=path)
                    for tid in opp_ids}
        conceded = _conceded_map(opp_logs)

        prediction = predict_fn(
            home_log, away_log, len(home_log), len(away_log),
            h2h=_h2h(home_log, m["away_id"]),
            league_avg=league_avg,
            conceded_of=lambda tid: conceded.get(tid, league_avg),
        )
        if prediction is None:
            continue
        pick = best_pick(prediction)
        if pick is None:
            continue

        hg, ag = m["home_goals"], m["away_goals"]
        rows.append({
            "event_id": m["event_id"],
            "league": slug,
            "kickoff": cutoff,
            "market": pick["market"],
            "selection": pick["selection"],
            "label": pick["label"],
            "prob": pick["probability"],
            "hit": pick_hit(pick["market"], pick["selection"], hg, ag),
            "prediction": prediction,
            "hg": hg, "ag": ag,
        })
    return score(rows)


def _h2h(home_log: list, away_id: str) -> dict | None:
    """Leak-free H2H from the home side's own pre-cutoff log, same venue only."""
    meetings = [m for m in home_log
                if m["opponent_id"] == str(away_id) and m["venue"] == "home"]
    if not meetings:
        return None
    return {
        "home_scored_avg": sum(m["scored"] for m in meetings) / len(meetings),
        "away_scored_avg": sum(m["conceded"] for m in meetings) / len(meetings),
        "meetings": len(meetings),
    }


def score(rows: list) -> dict:
    """Hit rate, Brier score and per-band calibration for scored rows."""
    if not rows:
        return {"n": 0, "hit_rate": None, "brier": None, "calibration": {},
                "by_market": {}, "rows": []}

    hits = sum(1 for r in rows if r["hit"])
    brier = sum((r["prob"] - (1.0 if r["hit"] else 0.0)) ** 2
                for r in rows) / len(rows)

    bands = defaultdict(lambda: {"n": 0, "hits": 0, "claimed": 0.0})
    markets = defaultdict(lambda: {"n": 0, "hits": 0, "claimed": 0.0})
    for r in rows:
        for bucket in (bands[_band(r["prob"])], markets[r["label"]]):
            bucket["n"] += 1
            bucket["hits"] += 1 if r["hit"] else 0
            bucket["claimed"] += r["prob"]

    def finish(d):
        return {k: {"n": v["n"],
                    "claimed": round(v["claimed"] / v["n"], 4),
                    "real": round(v["hits"] / v["n"], 4),
                    "gap": round(v["hits"] / v["n"] - v["claimed"] / v["n"], 4)}
                for k, v in d.items()}

    cal = finish(bands)
    # Sample-weighted mean |claimed - real|: one number for "is the confidence
    # meaningful", comparable across variants.
    cal_err = sum(abs(b["gap"]) * b["n"] for b in cal.values()) / len(rows)

    return {
        "n": len(rows),
        "hit_rate": round(hits / len(rows), 4),
        "brier": round(brier, 4),
        "calibration_error": round(cal_err, 4),
        "calibration": cal,
        "by_market": finish(markets),
        "rows": rows,
    }


def load_matches(league_slug: str | None = None, since: str | None = None,
                 limit: int | None = None, path: str = W.WAREHOUSE_PATH) -> list:
    """Stored matches in chronological order, ready for evaluate()."""
    where, params = [], []
    if league_slug:
        where.append("league_slug = ?")
        params.append(league_slug)
    if since:
        where.append("kickoff >= ?")
        params.append(since)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"SELECT * FROM matches {clause} ORDER BY kickoff"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with W._connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def report(result: dict, title: str = "") -> None:
    if title:
        print(f"\n=== {title} ===")
    if not result["n"]:
        print("  değerlendirilebilir maç yok")
        return
    print(f"  n={result['n']}  isabet=%{result['hit_rate']*100:.1f}  "
          f"Brier={result['brier']:.4f}  kalib.hatası={result['calibration_error']:.4f}")
    for band, _ in BANDS:
        b = result["calibration"].get(band)
        if b:
            print(f"    {band:8} n={b['n']:4}  iddia %{b['claimed']*100:4.0f} → "
                  f"gerçek %{b['real']*100:4.0f}  ({b['gap']*100:+.0f} puan)")


def main() -> None:
    import sys
    league = sys.argv[1] if len(sys.argv) > 1 else None
    matches = load_matches(league)
    print(f"{len(matches)} depolanmış maç" + (f" ({league})" if league else ""))
    report(evaluate(matches), "MEVCUT MODEL")


if __name__ == "__main__":
    main()
