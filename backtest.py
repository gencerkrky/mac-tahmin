"""Backtest the prediction model against already-played matches.

For each finished match we rebuild each team's form using ONLY the matches
played strictly before that fixture's date (no look-ahead leakage), run the
EXACT model the live site uses (poisson.predict_from_forms — including
opponent-strength correction), then compare its top pick to the real result.

Usage:
    python3 backtest.py 2026-07-05             # single day
    python3 backtest.py 2026-07-04 2026-07-05  # date range (inclusive)
"""

import sys
from datetime import date, timedelta

from api_client import ApiError, get_fixtures, get_team_form
from poisson import best_pick, predict_from_forms


def _matches_before(team_id, league_slug, cutoff_iso):
    """A team's form-log entries strictly before cutoff (leak-free)."""
    form = get_team_form(team_id, league_slug)
    return [m for m in form.get("log", []) if m["date"] < cutoff_iso]


def _conceded_before(team_id, league_slug, cutoff_iso):
    """Opponent's conceded average using only pre-cutoff matches."""
    log = _matches_before(team_id, league_slug, cutoff_iso)
    if not log:
        return None
    return sum(m["conceded"] for m in log) / len(log)


def actual_pick_hit(pick, hg, ag):
    """Did the model's top pick match the real score?"""
    market, sel = pick["market"], pick["selection"]
    total = hg + ag
    if market == "match_result":
        return {"home": hg > ag, "draw": hg == ag, "away": hg < ag}[sel]
    if market == "over_under_25":
        return (total >= 3) if sel == "over" else (total <= 2)
    if market == "btts":
        both = hg >= 1 and ag >= 1
        return both if sel == "yes" else not both
    return False


def backtest_fixture(fx):
    """Predict one finished fixture from pre-match data; None if unpredictable."""
    slug = fx["league_slug"]
    goals = fx["goals"]
    try:
        hg, ag = int(float(goals["home"])), int(float(goals["away"]))
    except (KeyError, TypeError, ValueError):
        return None

    cutoff = fx["kickoff"]  # ISO timestamp; only earlier matches are "known"
    home_log = _matches_before(fx["home"]["id"], slug, cutoff)
    away_log = _matches_before(fx["away"]["id"], slug, cutoff)
    if not home_log or not away_log:
        return None

    # Opponent-strength baseline from both teams' pre-match opponents.
    opp_ids = {m["opponent_id"] for m in home_log} | \
              {m["opponent_id"] for m in away_log}
    conceded_cache = {}
    for tid in opp_ids:
        c = _conceded_before(tid, slug, cutoff)
        if c is not None:
            conceded_cache[tid] = c
    league_avg = (round(sum(conceded_cache.values()) / len(conceded_cache), 3)
                  if conceded_cache else 1.35)

    def conceded_of(team_id):
        return conceded_cache.get(team_id, league_avg)

    # H2H is intentionally omitted in backtest: get_h2h needs a live summary
    # endpoint and can't be time-sliced, so we test the pure form model.
    prediction = predict_from_forms(
        home_log, away_log, len(home_log), len(away_log),
        h2h=None, league_avg=league_avg, conceded_of=conceded_of,
    )
    if prediction is None:
        return None

    pick = best_pick(prediction)
    return {
        "match": f"{fx['home']['name']} {hg}-{ag} {fx['away']['name']}",
        "pick": pick["label"], "prob": pick["probability"],
        "hit": actual_pick_hit(pick, hg, ag),
    }


def run(date_from, date_to):
    d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    rows = []
    day = d0
    while day <= d1:
        try:
            fixtures = get_fixtures(day.isoformat())
        except ApiError as exc:
            print(f"  {day} atlandı (API hatası: {exc})")
            day += timedelta(days=1)
            continue
        for fx in fixtures:
            if fx["status"] != "FT":
                continue
            try:
                row = backtest_fixture(fx)
            except ApiError:
                continue  # one team's form unavailable — skip this match
            if row is not None:
                rows.append(row)
        day += timedelta(days=1)
    return rows


def summarize(rows):
    """Overall + confidence-bucketed hit rates (calibration check)."""
    buckets = {"50-60%": [], "60-70%": [], "70-80%": [], "80%+": []}
    for r in rows:
        p = r["prob"]
        key = ("80%+" if p >= 0.8 else "70-80%" if p >= 0.7
               else "60-70%" if p >= 0.6 else "50-60%")
        buckets[key].append(r["hit"])
    return buckets


def main():
    args = sys.argv[1:]
    if not args:
        print("Kullanım: python3 backtest.py <tarih> [bitiş-tarihi]")
        return
    date_from = args[0]
    date_to = args[1] if len(args) > 1 else args[0]

    rows = run(date_from, date_to)
    if not rows:
        print("Bu aralıkta oynanmış ve tahmin edilebilir maç yok.")
        return

    hits = sum(1 for r in rows if r["hit"])
    print(f"\n{date_from} → {date_to} BACKTEST ({len(rows)} maç)\n")
    for r in rows:
        mark = "✅" if r["hit"] else "❌"
        print(f"  {mark} {r['match']}  →  {r['pick']} (%{r['prob']*100:.0f})")

    print(f"\nGENEL İSABET: {hits}/{len(rows)} (%{hits/len(rows)*100:.0f})")
    print("\nGÜVEN ARALIĞINA GÖRE (kalibrasyon):")
    for k, v in summarize(rows).items():
        if v:
            print(f"  Model {k:8} dedi → gerçekte {sum(v)}/{len(v)} tuttu "
                  f"(%{sum(v)/len(v)*100:.0f})")


if __name__ == "__main__":
    main()
