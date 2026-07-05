"""Pure basketball prediction model — no network, no IO.

Basketball is high-scoring and roughly normally distributed, so the Poisson
goal model is wrong here. Instead each team's expected points come from its
own scoring average and the opponent's defensive average; the winning
probability and the total line follow from a normal approximation of the
point margin and the point total.
"""

import math

# League-average points per team per game (NBA/WNBA ballpark).
LEAGUE_AVG_POINTS = 100.0

# Home teams score a few more points on average.
HOME_ADVANTAGE_POINTS = 3.0

# Standard deviation of the point margin (empirical NBA ~12) and of the
# combined total (~15). Used for the normal-approximation probabilities.
MARGIN_STD = 12.0
TOTAL_STD = 15.0

# Bookmaker-style total line to price over/under against (points).
TOTAL_LINE_STEP = 0.5


def _normal_cdf(x: float, mean: float, std: float) -> float:
    """P(X <= x) for X ~ Normal(mean, std)."""
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))


def _expected_points(home_scored, home_conceded, away_scored, away_conceded):
    """Blend each team's offence with the opponent's defence."""
    home = (home_scored + away_conceded) / 2 + HOME_ADVANTAGE_POINTS
    away = (away_scored + home_conceded) / 2
    return round(home, 1), round(away, 1)


def _fair_odds(probability: float) -> float:
    if probability <= 0:
        return float("inf")
    return round(1 / probability, 2)


def predict_basketball(home_scored_avg, home_conceded_avg,
                       away_scored_avg, away_conceded_avg):
    """Win probabilities, point spread and over/under for one game."""
    exp_home, exp_away = _expected_points(
        home_scored_avg, home_conceded_avg, away_scored_avg, away_conceded_avg
    )
    margin = exp_home - exp_away          # >0 favours the home team
    total = exp_home + exp_away

    # P(home wins) = P(margin > 0) under Normal(margin, MARGIN_STD).
    home_win = 1 - _normal_cdf(0, margin, MARGIN_STD)
    away_win = 1 - home_win

    # Round the total to the nearest .5 line, then price over/under.
    line = round(total * 2) / 2 + TOTAL_LINE_STEP
    over = 1 - _normal_cdf(line, total, TOTAL_STD)

    match_result = {"home": round(home_win, 4), "away": round(away_win, 4)}
    total_line = {"over": round(over, 4), "under": round(1 - over, 4)}

    # Best pick: the most confident of moneyline vs the total line.
    candidates = [
        ("match_result", "home", home_win, "Ev sahibi kazanır"),
        ("match_result", "away", away_win, "Deplasman kazanır"),
        ("total_line", "over", over, f"{line} Üst"),
        ("total_line", "under", 1 - over, f"{line} Alt"),
    ]
    market, selection, prob, label = max(candidates, key=lambda c: c[2])

    return {
        "sport": "basketball",
        "expected_points": {"home": exp_home, "away": exp_away},
        "expected_total": round(total, 1),
        "spread": round(margin, 1),
        "line": line,
        "match_result": match_result,
        "total_line": total_line,
        "best_pick": {
            "market": market, "selection": selection, "label": label,
            "probability": round(prob, 4), "fair_odds": _fair_odds(prob),
        },
    }
