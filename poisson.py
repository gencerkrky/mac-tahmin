"""Pure Poisson goal model — no network, no IO; fully unit-testable.

The model follows the standard attack-strength / defence-weakness approach:
each team's scoring and conceding averages are normalised against a global
league average, combined into expected goals, then expanded into a full
scoreline probability matrix via the Poisson distribution.
"""

import math

# Global average goals per team per match. A fixed constant keeps the model
# free of extra API calls; per-league averages add little at this scale.
LEAGUE_AVG_GOALS = 1.35

# Home teams historically score ~10-15% more; away teams slightly fewer.
HOME_ADVANTAGE = 1.15
AWAY_FACTOR = 0.95

# Scoreline matrix upper bound. P(goals > 8) is negligible (<0.1%).
MAX_GOALS = 8


def _poisson_pmf(lam: float, k: int) -> float:
    """P(X = k) for X ~ Poisson(lam)."""
    return math.exp(-lam) * lam**k / math.factorial(k)


def _expected_goals(home_scored, home_conceded, away_scored, away_conceded):
    """Expected goals for each side from raw scoring/conceding averages."""
    home_attack = home_scored / LEAGUE_AVG_GOALS
    home_defence = home_conceded / LEAGUE_AVG_GOALS
    away_attack = away_scored / LEAGUE_AVG_GOALS
    away_defence = away_conceded / LEAGUE_AVG_GOALS

    lam_home = home_attack * away_defence * LEAGUE_AVG_GOALS * HOME_ADVANTAGE
    lam_away = away_attack * home_defence * LEAGUE_AVG_GOALS * AWAY_FACTOR

    # A zero lambda breaks downstream ratios; floor at a tiny positive value.
    return max(lam_home, 0.01), max(lam_away, 0.01)


def predict(home_scored_avg, home_conceded_avg, away_scored_avg, away_conceded_avg):
    """Full prediction set derived from one scoreline probability matrix."""
    lam_home, lam_away = _expected_goals(
        home_scored_avg, home_conceded_avg, away_scored_avg, away_conceded_avg
    )

    home_win = draw = away_win = 0.0
    over_25 = 0.0
    btts_yes = 0.0
    best_score = (0, 0)
    best_prob = 0.0

    for hg in range(MAX_GOALS + 1):
        p_h = _poisson_pmf(lam_home, hg)
        for ag in range(MAX_GOALS + 1):
            p = p_h * _poisson_pmf(lam_away, ag)

            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

            if hg + ag >= 3:
                over_25 += p
            if hg >= 1 and ag >= 1:
                btts_yes += p
            if p > best_prob:
                best_prob = p
                best_score = (hg, ag)

    # Normalise: the truncated matrix sums to slightly under 1.0.
    total = home_win + draw + away_win
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    return {
        "expected_goals": {"home": round(lam_home, 2), "away": round(lam_away, 2)},
        "match_result": {
            "home": round(home_win, 4),
            "draw": round(draw, 4),
            "away": round(away_win, 4),
        },
        "over_under_25": {"over": round(over_25, 4), "under": round(1 - over_25, 4)},
        "btts": {"yes": round(btts_yes, 4), "no": round(1 - btts_yes, 4)},
        "most_likely_score": {
            "home": best_score[0],
            "away": best_score[1],
            "probability": round(best_prob, 4),
        },
    }


# Human-readable labels for every broad-market selection (Turkish UI copy).
_PICK_LABELS = {
    ("match_result", "home"): "Ev sahibi kazanır",
    ("match_result", "draw"): "Beraberlik",
    ("match_result", "away"): "Deplasman kazanır",
    ("over_under_25", "over"): "2.5 Üst",
    ("over_under_25", "under"): "2.5 Alt",
    ("btts", "yes"): "KG Var",
    ("btts", "no"): "KG Yok",
}


def fair_odds(probability: float) -> float:
    """Fair (no-margin) decimal odds implied by a probability."""
    if probability <= 0:
        return float("inf")
    return round(1 / probability, 2)


def best_pick(prediction: dict, min_odds: float = 0.0) -> dict | None:
    """Highest-probability selection across broad markets only.

    Exact scores are deliberately excluded: their probabilities are
    inherently low and would never win, but excluding them makes the
    guarantee explicit.

    min_odds filters to selections whose fair odds meet the threshold
    (coupon modes like "2.00+"); returns None when nothing qualifies.
    """
    best = None
    for (market, selection), label in _PICK_LABELS.items():
        prob = prediction[market][selection]
        odds = fair_odds(prob)
        if odds < min_odds:
            continue
        if best is None or prob > best["probability"]:
            best = {
                "market": market,
                "selection": selection,
                "label": label,
                "probability": prob,
                "fair_odds": odds,
            }
    return best
