"""Static site generator for the daily auto-coupon (GitHub Pages).

Runs in GitHub Actions every morning:
1. Settles previously published coupons against final scores (history.json
   is fetched from the live site by the workflow before this script runs).
2. Generates today's coupons in all three modes + the 2-day bulletin with
   full predictions.
3. Writes everything into public/ for the Pages deploy.

No secrets required: ESPN is keyless; the AI layer is intentionally not part
of the static site (it needs a paid key and a backend).
"""

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from api_client import (get_basketball_fixtures, get_basketball_form,
                        get_fixtures)
from app import (COUPON_MODES, DEFAULT_COUPON_SIZE, MAX_COUPON_CANDIDATES,
                 UPCOMING_STATUSES, pick_top_predictions, predict_fixture)
from basketball import predict_basketball
from store import compute_stats, pick_hit

# Accuracy window: only coupons from the last N days count toward the shown
# hit-rate, so the figure reflects the model's recent performance.
STATS_WINDOW_DAYS = 30

OUTPUT_DIR = Path("public")
HISTORY_PATH = OUTPUT_DIR / "history.json"
BULLETIN_DAYS = 2  # today + tomorrow


def load_history() -> list:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text())
        except (OSError, ValueError):
            return []
    return []


def settle_history(history: list) -> None:
    """Fill hit/miss on pending entries whose matches have finished."""
    for coupon in history:
        if coupon.get("settled"):
            continue
        try:
            fixtures = {f["fixture_id"]: f for f in get_fixtures(coupon["date"])}
        except Exception:
            continue  # network hiccup: stays pending until the next run

        all_finished = True
        for p in coupon["picks"]:
            fx = fixtures.get(p["fixture"]["fixture_id"])
            if fx is None or fx["status"] != "FT":
                all_finished = False
                break
            try:
                hg = int(float(fx["goals"]["home"]))
                ag = int(float(fx["goals"]["away"]))
            except (KeyError, TypeError, ValueError):
                all_finished = False
                break
            bp = p["best_pick"]
            p["hit"] = pick_hit(bp["market"], bp["selection"], hg, ag)
            p["final_score"] = f"{hg}-{ag}"

        if all_finished:
            coupon["settled"] = True
            coupon["hit_count"] = sum(1 for p in coupon["picks"] if p.get("hit"))


def slim_pick(item: dict) -> dict:
    return {
        "fixture": {
            "fixture_id": item["fixture"]["fixture_id"],
            "home": {"name": item["fixture"]["home"]["name"]},
            "away": {"name": item["fixture"]["away"]["name"]},
            "league": item["fixture"]["league"],
        },
        "best_pick": item["best_pick"],
    }


def generate_daily_coupons(today: str, history: list) -> list:
    """One fresh coupon per mode; also appended to history for tracking."""
    already = {(c["date"], c["mode"]) for c in history}
    coupons = []
    upcoming = [f for f in get_fixtures(today) if f["status"] in UPCOMING_STATUSES]
    candidates = upcoming[:MAX_COUPON_CANDIDATES]

    for mode, min_odds in COUPON_MODES.items():
        analysed = [item for fx in candidates
                    if (item := predict_fixture(fx, min_odds=min_odds)) is not None]
        result = pick_top_predictions(analysed, DEFAULT_COUPON_SIZE)
        coupon = {
            "date": today,
            "mode": mode,
            "picks": [slim_pick(p) for p in result["picks"]],
            "total_odds": result["total_odds"],
            "combined_probability": result["combined_probability"],
            "settled": False,
            "hit_count": None,
        }
        coupons.append(coupon)
        # Aynı gün + mod için geçmişe ikinci kez yazma (manuel tekrar koşular).
        if coupon["picks"] and (today, mode) not in already:
            history.append(coupon)
    return coupons


def build_bulletin(start: date) -> list:
    days = []
    for offset in range(BULLETIN_DAYS):
        day = (start + timedelta(days=offset)).isoformat()
        entries = []
        for fx in get_fixtures(day):
            entry = {"fixture": fx}
            if fx["status"] in UPCOMING_STATUSES:
                item = predict_fixture(fx)
                if item is not None:
                    entry["prediction"] = item["prediction"]
                    entry["best_pick"] = item["best_pick"]
            entries.append(entry)
        days.append({"date": day, "matches": entries})
    return days


def build_basketball(start: date) -> list:
    """Basketball bulletin with per-game predictions (separate model)."""
    days = []
    for offset in range(BULLETIN_DAYS):
        day = (start + timedelta(days=offset)).isoformat()
        entries = []
        for fx in get_basketball_fixtures(day):
            entry = {"fixture": fx}
            if fx["status"] in UPCOMING_STATUSES:
                try:
                    hf = get_basketball_form(fx["home"]["id"], fx["league_slug"])
                    af = get_basketball_form(fx["away"]["id"], fx["league_slug"])
                    entry["prediction"] = predict_basketball(
                        hf["scored_avg"], hf["conceded_avg"],
                        af["scored_avg"], af["conceded_avg"])
                except Exception:
                    pass  # veri eksikse tahminsiz göster
            entries.append(entry)
        days.append({"date": day, "matches": entries})
    return days


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = date.today()

    history = load_history()
    settle_history(history)
    coupons = generate_daily_coupons(today.isoformat(), history)
    bulletin = build_bulletin(today)

    # Son STATS_WINDOW_DAYS güne düşen kuponlarla isabet istatistiği.
    cutoff = (today - timedelta(days=STATS_WINDOW_DAYS)).isoformat()
    recent = [c for c in history if c["date"] >= cutoff]
    stats = compute_stats(recent)
    stats["window_days"] = STATS_WINDOW_DAYS
    stats["settled_coupons"] = stats["overall"]["coupon_total"]

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": bulletin,
        "basketball": build_basketball(today),
        "coupons": coupons,
        "history": sorted(history, key=lambda c: c["date"], reverse=True)[:60],
        "stats": stats,
    }

    (OUTPUT_DIR / "data.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False), encoding="utf-8")
    shutil.copy("site_template/index.html", OUTPUT_DIR / "index.html")

    total_matches = sum(len(d["matches"]) for d in bulletin)
    print(f"Üretildi: {total_matches} maç, {len(coupons)} kupon, "
          f"{len(history)} geçmiş kaydı")


if __name__ == "__main__":
    main()
