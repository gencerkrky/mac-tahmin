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

# Share of a match's expected goals that fall in the first half. Historically
# ~45% of goals come before the break (teams open up more in the second half).
FIRST_HALF_SHARE = 0.45

# How many top scorelines to expose to the UI (like a bookmaker's score list).
SCORELINE_LIST_SIZE = 20

# Shrinkage prior weight: a form average based on n matches is blended with
# the league average as n/(n+K). Small samples (cup qualifiers, season start)
# no longer produce extreme, overconfident expected goals.
SHRINKAGE_K = 5

# How much head-to-head history can pull the form average at full strength.
H2H_MAX_WEIGHT = 0.25
# Meetings needed for full H2H weight; fewer meetings scale linearly.
H2H_FULL_MEETINGS = 4

# Recency decay: each older match counts (1 - RECENCY_DECAY) as much as the
# one after it, so recent form dominates without ignoring older matches.
RECENCY_DECAY = 0.12

# Minimum venue-specific matches before we trust a home/away split; below this
# we blend toward the team's overall form so a 1-game sample can't dominate.
MIN_VENUE_MATCHES = 4

# How strongly to correct a goal tally for the opponent's defensive quality.
# 1.0 = full correction, 0 = ignore opponent. Kept moderate to avoid overfitting.
OPPONENT_ADJ_STRENGTH = 0.5


def shrink_to_league_avg(avg: float, matches: int) -> float:
    """Pull a small-sample average toward the league average (Bayes shrinkage)."""
    weight = matches / (matches + SHRINKAGE_K)
    return round(weight * avg + (1 - weight) * LEAGUE_AVG_GOALS, 3)


def blend_with_h2h(form_avg: float, h2h_avg: float, meetings: int) -> float:
    """Mix head-to-head scoring history into the form average.

    H2H is a weak but real signal (styles that consistently trouble each
    other); its weight grows with the number of meetings and is capped.
    """
    if meetings <= 0:
        return form_avg
    weight = H2H_MAX_WEIGHT * min(1.0, meetings / H2H_FULL_MEETINGS)
    return round((1 - weight) * form_avg + weight * h2h_avg, 3)


def weighted_average(values: list) -> float:
    """Recency-weighted mean of a chronological list (oldest first).

    The most recent match gets weight 1, each older one is multiplied by
    (1 - RECENCY_DECAY) again, so form trends are captured.
    """
    if not values:
        return 0.0
    n = len(values)
    weights = [(1 - RECENCY_DECAY) ** (n - 1 - i) for i in range(n)]
    total_w = sum(weights)
    return round(sum(v * w for v, w in zip(values, weights)) / total_w, 3)


def adjust_for_opponent(goals: float, opponent_conceded_avg: float,
                        league_avg: float) -> float:
    """Scale a goal tally by how good the opponent's defence was.

    Scoring against a stingy defence (conceded < league avg) counts for more;
    scoring against a leaky one counts for less. The correction is dampened by
    OPPONENT_ADJ_STRENGTH so a single soft opponent can't dominate.
    """
    if opponent_conceded_avg <= 0:
        return goals
    # ratio < 1 → opponent tougher than average → inflate the goals.
    ratio = league_avg / opponent_conceded_avg
    adjusted = goals * (1 + OPPONENT_ADJ_STRENGTH * (ratio - 1))
    return round(max(adjusted, 0.0), 3)


def venue_weighted(log: list, venue: str, key: str) -> float:
    """Recency-weighted average of `key` (scored/conceded) for one venue.

    Falls back to the full log when the team has too few matches at that venue,
    so a side with only away games still gets a usable home estimate. The log
    is most-recent-first; weighting expects oldest-first, so it's reversed.
    """
    if not log:
        return 0.0
    venue_vals = [m[key] for m in log if m["venue"] == venue]
    all_vals = [m[key] for m in log]
    if len(venue_vals) >= MIN_VENUE_MATCHES:
        return weighted_average(list(reversed(venue_vals)))
    if not venue_vals:
        return weighted_average(list(reversed(all_vals)))
    venue_w = weighted_average(list(reversed(venue_vals)))
    all_w = weighted_average(list(reversed(all_vals)))
    frac = len(venue_vals) / MIN_VENUE_MATCHES
    return round(frac * venue_w + (1 - frac) * all_w, 3)


def _opponent_adjusted_attack(log, venue, league_avg, conceded_of):
    """Venue-weighted goals scored, each corrected for the opponent's defence."""
    if not log:
        return 0.0
    venue_matches = [m for m in log if m["venue"] == venue]
    matches = venue_matches if len(venue_matches) >= MIN_VENUE_MATCHES else log
    adjusted = [adjust_for_opponent(m["scored"], conceded_of(m["opponent_id"]),
                                    league_avg)
                for m in matches]
    return weighted_average(list(reversed(adjusted)))


def predict_from_forms(home_log, away_log, home_matches, away_matches,
                       h2h=None, league_avg=LEAGUE_AVG_GOALS, conceded_of=None):
    """The full model on pre-fetched match logs — shared by live and backtest.

    home_log/away_log: most-recent-first match records with keys
    scored/conceded/venue/opponent_id. conceded_of(team_id)->float supplies
    opponent defensive strength (defaults to league_avg, i.e. no correction).
    h2h: optional {home_scored_avg, away_scored_avg, meetings} dict.
    Returns a predict() dict, or None if either side has no matches.
    """
    if not home_log or not away_log:
        return None
    if conceded_of is None:
        conceded_of = lambda _tid: league_avg

    home_attack = _opponent_adjusted_attack(home_log, "home", league_avg, conceded_of)
    away_attack = _opponent_adjusted_attack(away_log, "away", league_avg, conceded_of)
    home_def = venue_weighted(home_log, "home", "conceded")
    away_def = venue_weighted(away_log, "away", "conceded")

    hs = shrink_to_league_avg(home_attack, home_matches)
    hc = shrink_to_league_avg(home_def, home_matches)
    as_ = shrink_to_league_avg(away_attack, away_matches)
    ac = shrink_to_league_avg(away_def, away_matches)

    m = h2h["meetings"] if h2h else 0
    h2h_home = h2h["home_scored_avg"] if h2h else 0.0
    h2h_away = h2h["away_scored_avg"] if h2h else 0.0
    return predict(
        blend_with_h2h(hs, h2h_home, m),
        blend_with_h2h(hc, h2h_away, m),
        blend_with_h2h(as_, h2h_away, m),
        blend_with_h2h(ac, h2h_home, m),
    )


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


def _score_matrix(lam_home: float, lam_away: float) -> list:
    """Normalised P(home=h, away=a) for h,a in 0..MAX_GOALS."""
    matrix = [[_poisson_pmf(lam_home, h) * _poisson_pmf(lam_away, a)
               for a in range(MAX_GOALS + 1)]
              for h in range(MAX_GOALS + 1)]
    total = sum(p for row in matrix for p in row)
    return [[p / total for p in row] for row in matrix]


def _result(hg: int, ag: int) -> str:
    """'1' home win, '0' draw, '2' away win (bookmaker notation)."""
    return "1" if hg > ag else "0" if hg == ag else "2"


def _htft(lam_home: float, lam_away: float) -> dict:
    """Half-time/full-time probabilities via independent half matrices.

    First and second halves are modelled as independent Poisson draws whose
    rates sum to the full-match expectation; the 9 HT×FT outcomes are
    accumulated over their joint distribution.
    """
    lh1, la1 = lam_home * FIRST_HALF_SHARE, lam_away * FIRST_HALF_SHARE
    lh2, la2 = lam_home - lh1, lam_away - la1
    h1 = _score_matrix(lh1, la1)
    h2 = _score_matrix(lh2, la2)

    out = {f"{ht}/{ft}": 0.0
           for ht in ("1", "0", "2") for ft in ("1", "0", "2")}
    for hh1 in range(MAX_GOALS + 1):
        for ah1 in range(MAX_GOALS + 1):
            p1 = h1[hh1][ah1]
            if p1 == 0:
                continue
            ht = _result(hh1, ah1)
            for hh2 in range(MAX_GOALS + 1):
                for ah2 in range(MAX_GOALS + 1):
                    p = p1 * h2[hh2][ah2]
                    ft = _result(hh1 + hh2, ah1 + ah2)
                    out[f"{ht}/{ft}"] += p
    return {k: round(v, 4) for k, v in out.items()}


def predict(home_scored_avg, home_conceded_avg, away_scored_avg, away_conceded_avg):
    """Full prediction set derived from one scoreline probability matrix."""
    lam_home, lam_away = _expected_goals(
        home_scored_avg, home_conceded_avg, away_scored_avg, away_conceded_avg
    )
    matrix = _score_matrix(lam_home, lam_away)

    home_win = draw = away_win = 0.0
    btts_yes = 0.0
    odd = 0.0
    # Over probabilities for total-goal lines 1.5 / 2.5 / 3.5.
    over = {1: 0.0, 2: 0.0, 3: 0.0}  # keyed by integer floor of the line
    scorelines = []

    for hg in range(MAX_GOALS + 1):
        for ag in range(MAX_GOALS + 1):
            p = matrix[hg][ag]
            total = hg + ag

            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

            if hg >= 1 and ag >= 1:
                btts_yes += p
            if total % 2 == 1:
                odd += p
            for line in over:
                if total > line:      # >1 covers 1.5, >2 covers 2.5, >3 covers 3.5
                    over[line] += p

            scorelines.append({"home": hg, "away": ag, "probability": round(p, 4)})

    scorelines.sort(key=lambda s: s["probability"], reverse=True)
    best = scorelines[0]

    return {
        "expected_goals": {"home": round(lam_home, 2), "away": round(lam_away, 2)},
        "match_result": {
            "home": round(home_win, 4),
            "draw": round(draw, 4),
            "away": round(away_win, 4),
        },
        "double_chance": {
            "1X": round(home_win + draw, 4),
            "12": round(home_win + away_win, 4),
            "X2": round(draw + away_win, 4),
        },
        "over_under_25": {"over": round(over[2], 4), "under": round(1 - over[2], 4)},
        "over_under": {
            "1.5": {"over": round(over[1], 4), "under": round(1 - over[1], 4)},
            "2.5": {"over": round(over[2], 4), "under": round(1 - over[2], 4)},
            "3.5": {"over": round(over[3], 4), "under": round(1 - over[3], 4)},
        },
        "btts": {"yes": round(btts_yes, 4), "no": round(1 - btts_yes, 4)},
        "odd_even": {"odd": round(odd, 4), "even": round(1 - odd, 4)},
        "htft": _htft(lam_home, lam_away),
        "most_likely_score": {
            "home": best["home"], "away": best["away"],
            "probability": best["probability"],
        },
        "scorelines": scorelines[:SCORELINE_LIST_SIZE],
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
