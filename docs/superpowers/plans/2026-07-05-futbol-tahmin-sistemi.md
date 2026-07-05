# Futbol Tahmin Sistemi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** API-Football verisiyle Poisson modeli üzerinden maç tahminleri (1/X/2, 2.5 Ü/A, KG, en olası skor) üreten ve "en emin N maç" kuponu oluşturan Flask web uygulaması.

**Architecture:** Saf istatistik motoru (`poisson.py`, IO'suz) + API istemcisi (`api_client.py`, önbellekli) + Flask rotaları (`app.py`) + tek sayfa vanilla JS arayüz (`static/index.html`). API anahtarı yalnızca sunucuda.

**Tech Stack:** Python 3, Flask, requests, python-dotenv, pytest. Frontend: vanilla HTML/JS/CSS (bağımlılıksız).

## Global Constraints

- API anahtarı `.env` dosyasından okunur (`API_FOOTBALL_KEY`); frontend'e asla gönderilmez. `.env` git'e girmez (`.gitignore`'da mevcut).
- Ücretsiz kota ~100 istek/gün: takım formu ve fixture listesi bellek içi önbelleğe alınır; kupon analizi en fazla `MAX_COUPON_CANDIDATES = 12` maçla sınırlıdır.
- Sihirli değer yok: tüm sabitler modül başında isimli sabit olarak tanımlanır.
- Her dış çağrı (HTTP) hata yakalamalı; hatalar anlaşılır mesajla JSON olarak döner.
- Yorumlar İngilizce, *neden*i açıklar.
- Kesin skor kendi gerçek (düşük) yüzdesiyle gösterilir; en yüksek olasılıklı tahmin geniş kategorilerden (1/X/2, Ü/A, KG) seçilir.
- venv: `.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/pytest` kullanılır.
- Çalışma dizini: `/Users/belliklidamla/Desktop/gncr/iddaa test` (boşluk içerir — komutlarda tırnak zorunlu).

---

### Task 1: Proje kurulumu (bağımlılıklar + .env şablonu)

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.env` (kullanıcının mevcut anahtarıyla; git'e girmez)

**Interfaces:**
- Produces: kurulu paketler (flask, requests, python-dotenv, pytest); `.env` içinde `API_FOOTBALL_KEY`.

- [ ] **Step 1: requirements.txt yaz**

```
flask>=3.0
requests>=2.31
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2: .env.example yaz**

```
# API-Football anahtarınız: https://dashboard.api-football.com/profile?access
API_FOOTBALL_KEY=your_key_here
```

- [ ] **Step 3: .env yaz (gerçek anahtar)**

Kullanıcının API-Football anahtarını `.env` dosyasına yaz (anahtar bu plana
YAZILMAZ — konuşma geçmişinde/panoda mevcut; `.env` `.gitignore`'dadır):

```
API_FOOTBALL_KEY=<kullanıcının_anahtarı>
```

- [ ] **Step 4: Paketleri kur ve doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pip install -q -r requirements.txt && .venv/bin/python -c "import flask, requests, dotenv, pytest; print('deps OK')"`
Expected: `deps OK`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example
git commit -m "chore: proje bağımlılıkları ve env şablonu"
```

---

### Task 2: Poisson motoru — skor matrisi ve predict()

**Files:**
- Create: `poisson.py`
- Test: `test_poisson.py`

**Interfaces:**
- Produces:
  - `predict(home_scored_avg: float, home_conceded_avg: float, away_scored_avg: float, away_conceded_avg: float) -> dict` — dönen sözlük:
    ```python
    {
      "expected_goals": {"home": float, "away": float},
      "match_result": {"home": float, "draw": float, "away": float},   # olasılıklar, ~1.0 toplam
      "over_under_25": {"over": float, "under": float},
      "btts": {"yes": float, "no": float},
      "most_likely_score": {"home": int, "away": int, "probability": float},
    }
    ```
  - Modül sabitleri: `LEAGUE_AVG_GOALS = 1.35`, `HOME_ADVANTAGE = 1.15`, `AWAY_FACTOR = 0.95`, `MAX_GOALS = 8`

- [ ] **Step 1: Başarısız testleri yaz** (`test_poisson.py`)

```python
"""poisson.py birim testleri — motor saf fonksiyondur, ağ gerektirmez."""
import pytest

from poisson import predict, LEAGUE_AVG_GOALS


# Ortalama bir takımın girdileri: lig ortalamasında atar ve yer.
AVG = LEAGUE_AVG_GOALS


def test_probabilities_sum_to_one():
    p = predict(AVG, AVG, AVG, AVG)
    mr = p["match_result"]
    assert mr["home"] + mr["draw"] + mr["away"] == pytest.approx(1.0, abs=0.01)
    ou = p["over_under_25"]
    assert ou["over"] + ou["under"] == pytest.approx(1.0, abs=0.01)
    kg = p["btts"]
    assert kg["yes"] + kg["no"] == pytest.approx(1.0, abs=0.01)


def test_equal_teams_home_advantage():
    # Eşit güçte takımlarda ev avantajı ev galibiyetini öne geçirmeli.
    p = predict(AVG, AVG, AVG, AVG)
    assert p["match_result"]["home"] > p["match_result"]["away"]


def test_strong_home_team_favoured():
    # Çok gol atan / az yiyen ev sahibi, zayıf deplasmana karşı net favori.
    p = predict(2.5, 0.5, 0.7, 2.2)
    assert p["match_result"]["home"] > 0.6
    assert p["expected_goals"]["home"] > p["expected_goals"]["away"]


def test_high_scoring_teams_favour_over():
    p = predict(2.4, 1.8, 2.1, 1.9)
    assert p["over_under_25"]["over"] > p["over_under_25"]["under"]


def test_most_likely_score_structure():
    p = predict(AVG, AVG, AVG, AVG)
    s = p["most_likely_score"]
    assert isinstance(s["home"], int) and isinstance(s["away"], int)
    # Kesin skor olasılığı doğası gereği düşüktür ama sıfır olamaz.
    assert 0.0 < s["probability"] < 0.5


def test_zero_averages_do_not_crash():
    # Hiç gol atamayan takım: model çökmemeli, deplasman/beraberlik öne çıkmalı.
    p = predict(0.0, 1.0, 1.0, 1.0)
    assert p["match_result"]["home"] < p["match_result"]["away"] + p["match_result"]["draw"]
```

- [ ] **Step 2: Testlerin başarısız olduğunu doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_poisson.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'poisson'`

- [ ] **Step 3: poisson.py'yi yaz**

```python
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
```

- [ ] **Step 4: Testlerin geçtiğini doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_poisson.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add poisson.py test_poisson.py
git commit -m "feat: Poisson tahmin motoru (skor matrisi, 1X2, Ü/A, KG, en olası skor)"
```

---

### Task 3: Poisson motoru — best_pick() ve fair_odds()

**Files:**
- Modify: `poisson.py` (dosya sonuna ekle)
- Test: `test_poisson.py` (dosya sonuna ekle)

**Interfaces:**
- Consumes: Task 2'deki `predict()` çıktı sözlüğü.
- Produces:
  - `fair_odds(probability: float) -> float` — `1/p`, 2 ondalık; `p <= 0` için `float("inf")`.
  - `best_pick(prediction: dict) -> dict` — geniş kategorilerden (1/X/2, Ü/A, KG) en yüksek olasılıklıyı seçer:
    ```python
    {"market": "match_result", "selection": "home", "label": "Ev sahibi kazanır",
     "probability": 0.79, "fair_odds": 1.27}
    ```
    `market` ∈ `{"match_result", "over_under_25", "btts"}`; `selection` ilgili alt anahtar.

- [ ] **Step 1: Başarısız testleri ekle** (`test_poisson.py` sonuna)

```python
from poisson import best_pick, fair_odds


def test_fair_odds_inverse_of_probability():
    assert fair_odds(0.5) == pytest.approx(2.0)
    assert fair_odds(0.79) == pytest.approx(1.27, abs=0.01)


def test_fair_odds_zero_probability_is_infinite():
    assert fair_odds(0.0) == float("inf")


def test_best_pick_selects_highest_broad_market():
    # Güçlü ev sahibi: en emin tahmin 'ev kazanır' olmalı, kesin skor asla seçilmez.
    p = predict(2.5, 0.5, 0.7, 2.2)
    pick = best_pick(p)
    assert pick["market"] == "match_result"
    assert pick["selection"] == "home"
    assert pick["probability"] == p["match_result"]["home"]
    assert pick["fair_odds"] == fair_odds(pick["probability"])


def test_best_pick_never_returns_exact_score():
    p = predict(1.0, 1.0, 1.0, 1.0)
    assert best_pick(p)["market"] != "most_likely_score"
```

- [ ] **Step 2: Testlerin başarısız olduğunu doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_poisson.py -v`
Expected: yeni 4 test FAIL — `ImportError: cannot import name 'best_pick'`

- [ ] **Step 3: poisson.py sonuna ekle**

```python
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


def best_pick(prediction: dict) -> dict:
    """Highest-probability selection across broad markets only.

    Exact scores are deliberately excluded: their probabilities are
    inherently low and would never win, but excluding them makes the
    guarantee explicit.
    """
    best = None
    for (market, selection), label in _PICK_LABELS.items():
        prob = prediction[market][selection]
        if best is None or prob > best["probability"]:
            best = {
                "market": market,
                "selection": selection,
                "label": label,
                "probability": prob,
                "fair_odds": fair_odds(prob),
            }
    return best
```

- [ ] **Step 4: Testlerin geçtiğini doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_poisson.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add poisson.py test_poisson.py
git commit -m "feat: en güvenli tahmin seçimi (best_pick) ve adil oran hesabı"
```

---

### Task 4: API istemcisi — fixtures, takım formu, önbellek

**Files:**
- Create: `api_client.py`
- Test: `test_api_client.py`

**Interfaces:**
- Consumes: `.env` içindeki `API_FOOTBALL_KEY` (dotenv `app.py`'de yüklenir; testlerde monkeypatch).
- Produces:
  - `LEAGUES: dict[int, str]` — analiz edilen lig id → ad eşlemesi.
  - `ApiError(Exception)` — mesajı kullanıcıya gösterilebilir API/ağ hatası.
  - `get_fixtures(date_str: str) -> list[dict]` — o tarihte `LEAGUES` kapsamındaki maçlar; her öğe:
    ```python
    {"fixture_id": int, "kickoff": "2026-07-05T17:00:00+00:00", "status": "NS",
     "league_id": int, "league": "Süper Lig", "country": "Turkey",
     "home": {"id": int, "name": str}, "away": {"id": int, "name": str},
     "goals": {"home": int | None, "away": int | None}}
    ```
  - `get_team_form(team_id: int) -> dict` — `{"scored_avg": float, "conceded_avg": float, "matches": int}` (son 10 bitmiş maç).
  - `clear_cache() -> None` — testler için önbelleği sıfırlar.

- [ ] **Step 1: Başarısız testleri yaz** (`test_api_client.py`)

```python
"""api_client testleri — HTTP, monkeypatch ile taklit edilir; gerçek ağ yok."""
import pytest

import api_client
from api_client import ApiError, get_fixtures, get_team_form, clear_cache


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    clear_cache()


def _fixture_payload():
    # Minimal but structurally faithful API-Football /fixtures response.
    def item(fid, league_id, league, country, home_id, home, away_id, away):
        return {
            "fixture": {"id": fid, "date": "2026-07-05T17:00:00+00:00",
                        "status": {"short": "NS"}},
            "league": {"id": league_id, "name": league, "country": country},
            "teams": {"home": {"id": home_id, "name": home},
                      "away": {"id": away_id, "name": away}},
            "goals": {"home": None, "away": None},
        }
    in_scope_league = next(iter(api_client.LEAGUES))
    return {"errors": [], "response": [
        item(1, in_scope_league, "Test Lig", "Turkey", 10, "Ev FC", 20, "Dep FC"),
        item(2, 999999, "Kapsam Dışı Lig", "Nowhere", 30, "A", 40, "B"),
    ]}


def _form_payload():
    # Two finished matches for team 10: scored 3+1=4, conceded 1+0=1.
    return {"errors": [], "response": [
        {"fixture": {"status": {"short": "FT"}},
         "teams": {"home": {"id": 10}, "away": {"id": 99}},
         "goals": {"home": 3, "away": 1}},
        {"fixture": {"status": {"short": "FT"}},
         "teams": {"home": {"id": 88}, "away": {"id": 10}},
         "goals": {"home": 0, "away": 1}},
    ]}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def test_get_fixtures_filters_to_leagues(monkeypatch):
    calls = []
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(_fixture_payload()))
    result = get_fixtures("2026-07-05")
    assert len(result) == 1                      # out-of-scope league filtered out
    assert result[0]["fixture_id"] == 1
    assert result[0]["home"]["name"] == "Ev FC"


def test_get_fixtures_cached_per_date(monkeypatch):
    calls = []
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(_fixture_payload()))
    get_fixtures("2026-07-05")
    get_fixtures("2026-07-05")
    assert len(calls) == 1                       # second hit served from cache


def test_get_team_form_averages(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse(_form_payload()))
    form = get_team_form(10)
    assert form["matches"] == 2
    assert form["scored_avg"] == pytest.approx(2.0)    # (3 + 1) / 2
    assert form["conceded_avg"] == pytest.approx(0.5)  # (1 + 0) / 2


def test_api_error_on_api_level_errors(monkeypatch):
    monkeypatch.setattr(api_client.requests, "get",
                        lambda *a, **k: FakeResponse({"errors": {"token": "invalid"}, "response": []}))
    with pytest.raises(ApiError):
        get_fixtures("2026-07-05")


def test_api_error_on_network_failure(monkeypatch):
    def boom(*a, **k):
        raise api_client.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(api_client.requests, "get", boom)
    with pytest.raises(ApiError):
        get_team_form(10)
```

- [ ] **Step 2: Testlerin başarısız olduğunu doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api_client'`

- [ ] **Step 3: api_client.py'yi yaz**

```python
"""API-Football client with in-memory caching.

All HTTP concerns live here: auth header, timeouts, error normalisation and
quota-friendly caching. Callers receive plain dicts and a single exception
type (ApiError) whose message is safe to show to end users.
"""

import os

import requests

API_BASE_URL = "https://v3.football.api-sports.io"
REQUEST_TIMEOUT_SECONDS = 15

# How many most-recent finished matches feed the form averages.
FORM_MATCH_COUNT = 10

# Leagues analysed by the coupon and fixture list (API-Football league ids).
# Big European leagues pause in summer; the Nordic/Eastern summer leagues
# keep the system testable year-round.
LEAGUES = {
    203: "Süper Lig (Türkiye)",
    39: "Premier League (İngiltere)",
    140: "La Liga (İspanya)",
    135: "Serie A (İtalya)",
    78: "Bundesliga (Almanya)",
    61: "Ligue 1 (Fransa)",
    103: "Eliteserien (Norveç)",
    113: "Allsvenskan (İsveç)",
    244: "Veikkausliiga (Finlandiya)",
    71: "Serie A (Brezilya)",
    253: "MLS (ABD)",
}

# Process-lifetime cache: fixtures keyed by date, form keyed by team id.
# The free tier allows ~100 requests/day; repeat lookups must not burn quota.
_cache: dict = {}


class ApiError(Exception):
    """API/network failure with a user-presentable message."""


def clear_cache() -> None:
    _cache.clear()


def _get(path: str, params: dict) -> dict:
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        raise ApiError("API_FOOTBALL_KEY tanımlı değil (.env dosyasını kontrol edin)")

    try:
        response = requests.get(
            f"{API_BASE_URL}{path}",
            headers={"x-apisports-key": api_key},
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"API-Football isteği başarısız: {exc}") from exc

    payload = response.json()
    # API-Football reports quota/auth problems inside a 200 body.
    if payload.get("errors"):
        raise ApiError(f"API-Football hata döndürdü: {payload['errors']}")
    return payload


def get_fixtures(date_str: str) -> list:
    """Fixtures on a date, filtered to LEAGUES. One request per date (cached)."""
    cache_key = ("fixtures", date_str)
    if cache_key in _cache:
        return _cache[cache_key]

    payload = _get("/fixtures", {"date": date_str})
    fixtures = []
    for item in payload.get("response", []):
        league_id = item["league"]["id"]
        if league_id not in LEAGUES:
            continue
        fixtures.append({
            "fixture_id": item["fixture"]["id"],
            "kickoff": item["fixture"]["date"],
            "status": item["fixture"]["status"]["short"],
            "league_id": league_id,
            "league": item["league"]["name"],
            "country": item["league"]["country"],
            "home": {"id": item["teams"]["home"]["id"], "name": item["teams"]["home"]["name"]},
            "away": {"id": item["teams"]["away"]["id"], "name": item["teams"]["away"]["name"]},
            "goals": item["goals"],
        })

    _cache[cache_key] = fixtures
    return fixtures


def get_team_form(team_id: int) -> dict:
    """Scoring/conceding averages over the team's last finished matches."""
    cache_key = ("form", team_id)
    if cache_key in _cache:
        return _cache[cache_key]

    payload = _get("/fixtures", {"team": team_id, "last": FORM_MATCH_COUNT})

    scored = conceded = matches = 0
    for item in payload.get("response", []):
        if item["fixture"]["status"]["short"] != "FT":
            continue
        goals_home = item["goals"]["home"]
        goals_away = item["goals"]["away"]
        if goals_home is None or goals_away is None:
            continue
        if item["teams"]["home"]["id"] == team_id:
            scored += goals_home
            conceded += goals_away
        else:
            scored += goals_away
            conceded += goals_home
        matches += 1

    if matches == 0:
        # New team / no data: fall back to a league-average profile so the
        # model still produces a (low-confidence) prediction.
        form = {"scored_avg": 1.35, "conceded_avg": 1.35, "matches": 0}
    else:
        form = {
            "scored_avg": round(scored / matches, 3),
            "conceded_avg": round(conceded / matches, 3),
            "matches": matches,
        }

    _cache[cache_key] = form
    return form
```

- [ ] **Step 4: Testlerin geçtiğini doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_api_client.py -v`
Expected: 5 passed

- [ ] **Step 5: Tüm testler hâlâ geçiyor mu**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest -v`
Expected: 15 passed

- [ ] **Step 6: Commit**

```bash
git add api_client.py test_api_client.py
git commit -m "feat: API-Football istemcisi (lig filtresi, form ortalamaları, önbellek)"
```

---

### Task 5: Flask uygulaması — rotalar ve kupon mantığı

**Files:**
- Create: `app.py`
- Test: `test_app.py`

**Interfaces:**
- Consumes: `api_client.get_fixtures/get_team_form/ApiError/LEAGUES`, `poisson.predict/best_pick/fair_odds`.
- Produces:
  - `predict_fixture(fx: dict) -> dict` — bir fixture sözlüğünü tahmine çevirir (fixture + prediction + best_pick birleşik).
  - `pick_top_predictions(items: list[dict], size: int) -> dict` — saf kupon seçimi:
    ```python
    {"picks": [item, ...],              # best_pick olasılığına göre azalan, ilk `size`
     "total_odds": float,               # adil oranların çarpımı
     "combined_probability": float}     # olasılıkların çarpımı
    ```
  - HTTP: `GET /` (index.html), `GET /api/leagues`, `GET /api/fixtures?date=`, `GET /api/predict?fixture=`, `GET /api/coupon?date=&size=`.
  - Sabit: `MAX_COUPON_CANDIDATES = 12`, `DEFAULT_COUPON_SIZE = 5`.

- [ ] **Step 1: Başarısız testleri yaz** (`test_app.py`)

```python
"""app.py testleri — kupon seçimi saf fonksiyon olarak, rotalar test client ile."""
import pytest

import app as app_module
from app import app, pick_top_predictions


def _item(prob, odds):
    return {"best_pick": {"probability": prob, "fair_odds": odds}}


def test_pick_top_predictions_orders_and_limits():
    items = [_item(0.55, 1.82), _item(0.79, 1.27), _item(0.61, 1.64)]
    coupon = pick_top_predictions(items, size=2)
    probs = [i["best_pick"]["probability"] for i in coupon["picks"]]
    assert probs == [0.79, 0.61]                     # descending, top-2 only


def test_pick_top_predictions_metrics():
    items = [_item(0.5, 2.0), _item(0.5, 2.0)]
    coupon = pick_top_predictions(items, size=2)
    assert coupon["total_odds"] == pytest.approx(4.0)
    assert coupon["combined_probability"] == pytest.approx(0.25)


def test_pick_top_predictions_empty():
    coupon = pick_top_predictions([], size=5)
    assert coupon["picks"] == []
    assert coupon["total_odds"] == 0
    assert coupon["combined_probability"] == 0


def test_fixtures_route_validates_date():
    client = app.test_client()
    resp = client.get("/api/fixtures?date=bozuk-tarih")
    assert resp.status_code == 400


def test_predict_route_requires_fixture_id():
    client = app.test_client()
    resp = client.get("/api/predict")
    assert resp.status_code == 400


def test_fixtures_route_maps_api_error_to_502(monkeypatch):
    def boom(date_str):
        raise app_module.ApiError("kota doldu")
    monkeypatch.setattr(app_module, "get_fixtures", boom)
    client = app.test_client()
    resp = client.get("/api/fixtures?date=2026-07-05")
    assert resp.status_code == 502
    assert "kota" in resp.get_json()["error"]
```

- [ ] **Step 2: Testlerin başarısız olduğunu doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: app.py'yi yaz**

```python
"""Flask app: ties the API client and the Poisson engine together.

The API key stays server-side; the browser only ever receives JSON
predictions. Coupon building is a pure function (pick_top_predictions) so it
can be unit-tested without any network.
"""

import math
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from api_client import ApiError, LEAGUES, get_fixtures, get_team_form
from poisson import best_pick, predict

load_dotenv()

app = Flask(__name__, static_folder="static")

# Coupon analysis is request-hungry (2 form calls per match); cap candidates
# so one coupon costs at most ~24 of the free tier's ~100 daily requests.
MAX_COUPON_CANDIDATES = 12
DEFAULT_COUPON_SIZE = 5

# Only not-yet-started fixtures make sense for predictions.
UPCOMING_STATUSES = {"NS", "TBD"}


def predict_fixture(fx: dict) -> dict:
    """Combine both teams' form into a full prediction for one fixture."""
    home_form = get_team_form(fx["home"]["id"])
    away_form = get_team_form(fx["away"]["id"])
    prediction = predict(
        home_form["scored_avg"], home_form["conceded_avg"],
        away_form["scored_avg"], away_form["conceded_avg"],
    )
    return {
        "fixture": fx,
        "form": {"home": home_form, "away": away_form},
        "prediction": prediction,
        "best_pick": best_pick(prediction),
    }


def pick_top_predictions(items: list, size: int) -> dict:
    """Pure coupon builder: top-N items by best-pick probability."""
    ranked = sorted(items, key=lambda i: i["best_pick"]["probability"], reverse=True)
    picks = ranked[:size]
    if not picks:
        return {"picks": [], "total_odds": 0, "combined_probability": 0}

    total_odds = math.prod(p["best_pick"]["fair_odds"] for p in picks)
    combined = math.prod(p["best_pick"]["probability"] for p in picks)
    return {
        "picks": picks,
        "total_odds": round(total_odds, 2),
        "combined_probability": round(combined, 4),
    }


def _parse_date(raw: str):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/leagues")
def leagues():
    return jsonify({"leagues": LEAGUES})


@app.get("/api/fixtures")
def fixtures():
    date_str = _parse_date(request.args.get("date", ""))
    if not date_str:
        return jsonify({"error": "Geçersiz tarih. Beklenen format: YYYY-MM-DD"}), 400
    try:
        return jsonify({"date": date_str, "fixtures": get_fixtures(date_str)})
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/predict")
def predict_route():
    fixture_id = request.args.get("fixture", type=int)
    date_str = _parse_date(request.args.get("date", ""))
    if fixture_id is None or not date_str:
        return jsonify({"error": "fixture ve date parametreleri zorunlu"}), 400
    try:
        fx = next((f for f in get_fixtures(date_str) if f["fixture_id"] == fixture_id), None)
        if fx is None:
            return jsonify({"error": "Maç bulunamadı"}), 404
        return jsonify(predict_fixture(fx))
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/coupon")
def coupon():
    date_str = _parse_date(request.args.get("date", ""))
    size = request.args.get("size", default=DEFAULT_COUPON_SIZE, type=int)
    if not date_str:
        return jsonify({"error": "Geçersiz tarih. Beklenen format: YYYY-MM-DD"}), 400
    if not 1 <= size <= MAX_COUPON_CANDIDATES:
        return jsonify({"error": f"Kupon boyutu 1-{MAX_COUPON_CANDIDATES} arası olmalı"}), 400

    try:
        upcoming = [f for f in get_fixtures(date_str) if f["status"] in UPCOMING_STATUSES]
        candidates = upcoming[:MAX_COUPON_CANDIDATES]
        analysed = [predict_fixture(fx) for fx in candidates]
    except ApiError as exc:
        return jsonify({"error": str(exc)}), 502

    result = pick_top_predictions(analysed, size)
    result["analysed_count"] = len(analysed)
    result["skipped_count"] = max(0, len(upcoming) - len(candidates))
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
```

- [ ] **Step 4: Testlerin geçtiğini doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest test_app.py -v`
Expected: 6 passed

- [ ] **Step 5: Tüm testler**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest`
Expected: 21 passed

- [ ] **Step 6: Commit**

```bash
git add app.py test_app.py
git commit -m "feat: Flask rotaları ve otomatik kupon mantığı"
```

---

### Task 6: Web arayüzü — tek sayfa

**Files:**
- Create: `static/index.html`

**Interfaces:**
- Consumes: `GET /api/fixtures?date=`, `GET /api/predict?fixture=&date=`, `GET /api/coupon?date=&size=` (Task 5 JSON şemaları).

- [ ] **Step 1: static/index.html'i yaz**

Tam içerik (tek dosya, bağımlılıksız):

```html
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maç Tahmin Paneli</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --line:#334155; --text:#e2e8f0;
          --muted:#94a3b8; --accent:#fbbf24; --good:#34d399; }
  * { box-sizing:border-box; margin:0; }
  body { font-family:-apple-system,system-ui,sans-serif; background:var(--bg);
         color:var(--text); padding:1rem; max-width:960px; margin:0 auto; }
  h1 { font-size:1.3rem; margin-bottom:.75rem; }
  .bar { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin-bottom:1rem; }
  select,button { background:var(--card); color:var(--text); border:1px solid var(--line);
                  border-radius:8px; padding:.5rem .75rem; font-size:.95rem; }
  button.primary { background:var(--accent); color:#111; font-weight:600; cursor:pointer; }
  .note { color:var(--muted); font-size:.8rem; margin:.5rem 0 1rem; }
  .match { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:.7rem .9rem; margin-bottom:.5rem; cursor:pointer; }
  .match:hover { border-color:var(--accent); }
  .match .meta { color:var(--muted); font-size:.78rem; }
  .match .teams { font-weight:600; margin-top:.15rem; }
  .pred { margin-top:.6rem; border-top:1px dashed var(--line); padding-top:.6rem;
          display:grid; gap:.25rem; font-size:.9rem; }
  .pred .row { display:flex; justify-content:space-between; }
  .pred .star { color:var(--accent); font-weight:700; }
  .odds { color:var(--good); }
  .odds.hot { color:#fb7185; font-weight:700; } /* fair odds >= 2.00: value flag */
  #coupon { background:var(--card); border:1px solid var(--accent); border-radius:10px;
            padding:1rem; margin-bottom:1rem; display:none; }
  #coupon h2 { font-size:1.05rem; color:var(--accent); margin-bottom:.5rem; }
  #coupon .totals { margin-top:.6rem; font-weight:600; }
  .err { color:#f87171; margin:.5rem 0; }
  .spin { color:var(--muted); }
</style>
</head>
<body>
<h1>⚽ Maç Tahmin Paneli</h1>

<div class="bar">
  <label>Tarih: <select id="dateSel"></select></label>
  <label>Kupon: <select id="sizeSel">
    <option>3</option><option selected>5</option><option>7</option>
  </select> maç</label>
  <button class="primary" id="couponBtn">🎫 En Emin Kuponu Oluştur</button>
</div>
<p class="note">Olasılıklar Poisson modelinin tahminidir; kesinlik garanti etmez.
Oranlar modelin <em>adil oranlarıdır</em>, bahis şirketi oranı değildir.</p>

<div id="msg"></div>
<div id="coupon"></div>
<div id="list"></div>

<script>
const list = document.getElementById("list");
const msg = document.getElementById("msg");
const couponBox = document.getElementById("coupon");
const dateSel = document.getElementById("dateSel");

// Next 4 days as selectable dates (today first).
for (let i = 0; i < 4; i++) {
  const d = new Date(); d.setDate(d.getDate() + i);
  const iso = d.toISOString().slice(0, 10);
  const opt = new Option(i === 0 ? `Bugün (${iso})` : iso, iso);
  dateSel.add(opt);
}

const pct = p => (p * 100).toFixed(0) + "%";

async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function predRows(p, best) {
  const rows = [
    ["match_result", "home", "Ev sahibi kazanır", p.match_result.home],
    ["match_result", "draw", "Beraberlik", p.match_result.draw],
    ["match_result", "away", "Deplasman kazanır", p.match_result.away],
    ["over_under_25", "over", "2.5 Üst", p.over_under_25.over],
    ["over_under_25", "under", "2.5 Alt", p.over_under_25.under],
    ["btts", "yes", "KG Var", p.btts.yes],
    ["btts", "no", "KG Yok", p.btts.no],
  ];
  const html = rows.map(([m, s, label, prob]) => {
    const isBest = best.market === m && best.selection === s;
    const cls = isBest ? "star" : "";
    const star = isBest ? "⭐ " : "";
    const odds = 1 / prob;
    const oddsCls = odds >= 2 ? "odds hot" : "odds";  // 2.00+ = value pick
    return `<div class="row ${cls}"><span>${star}${label}</span>
            <span>${pct(prob)} <span class="${oddsCls}">@${odds.toFixed(2)}</span></span></div>`;
  }).join("");
  const s = p.most_likely_score;
  return html + `<div class="row"><span>En olası skor</span>
    <span>${s.home}-${s.away} (${pct(s.probability)})</span></div>`;
}

async function loadFixtures() {
  list.innerHTML = ""; msg.innerHTML = '<p class="spin">Maçlar yükleniyor…</p>';
  try {
    const data = await getJSON(`/api/fixtures?date=${dateSel.value}`);
    msg.innerHTML = "";
    if (!data.fixtures.length) {
      msg.innerHTML = '<p class="note">Bu tarihte seçili liglerde maç yok.</p>'; return;
    }
    for (const f of data.fixtures) {
      const el = document.createElement("div");
      el.className = "match";
      el.innerHTML = `<div class="meta">${f.country} · ${f.league} · ${f.kickoff.slice(11,16)} (${f.status})</div>
        <div class="teams">${f.home.name} — ${f.away.name}</div>
        <div class="pred" hidden></div>`;
      el.addEventListener("click", () => showPrediction(el, f), { once: false });
      list.appendChild(el);
    }
  } catch (e) { msg.innerHTML = `<p class="err">${e.message}</p>`; }
}

async function showPrediction(el, f) {
  const box = el.querySelector(".pred");
  if (!box.hidden) { box.hidden = true; return; }
  if (box.dataset.loaded) { box.hidden = false; return; }
  box.hidden = false; box.innerHTML = '<span class="spin">Analiz ediliyor…</span>';
  try {
    const d = await getJSON(`/api/predict?fixture=${f.fixture_id}&date=${dateSel.value}`);
    box.innerHTML = predRows(d.prediction, d.best_pick);
    box.dataset.loaded = "1";
  } catch (e) { box.innerHTML = `<span class="err">${e.message}</span>`; }
}

document.getElementById("couponBtn").addEventListener("click", async () => {
  couponBox.style.display = "block";
  couponBox.innerHTML = '<span class="spin">Maçlar analiz ediliyor (biraz sürebilir)…</span>';
  const size = document.getElementById("sizeSel").value;
  try {
    const c = await getJSON(`/api/coupon?date=${dateSel.value}&size=${size}`);
    if (!c.picks.length) {
      couponBox.innerHTML = '<p class="note">Bu tarihte analiz edilecek başlamamış maç yok.</p>'; return;
    }
    const rows = c.picks.map(p => {
      const f = p.fixture, b = p.best_pick;
      return `<div class="row"><span>${f.home.name} — ${f.away.name}</span>
        <span class="star">${b.label} ${pct(b.probability)} <span class="odds">@${b.fair_odds}</span></span></div>`;
    }).join("");
    const skipped = c.skipped_count
      ? `<p class="note">Kota koruması: ${c.skipped_count} maç analiz dışı bırakıldı.</p>` : "";
    couponBox.innerHTML = `<h2>🎫 En Emin ${c.picks.length} Maç</h2><div class="pred">${rows}</div>
      <div class="totals">Toplam adil oran: <span class="odds">${c.total_odds}</span> ·
      Birleşik isabet: ${pct(c.combined_probability)}</div>${skipped}`;
  } catch (e) { couponBox.innerHTML = `<p class="err">${e.message}</p>`; }
});

dateSel.addEventListener("change", loadFixtures);
loadFixtures();
</script>
</body>
</html>
```

- [ ] **Step 2: Sunucuyu başlat ve sayfanın yüklendiğini doğrula**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && (.venv/bin/python app.py &) && sleep 2 && curl -s http://127.0.0.1:5001/ | head -5 && curl -s "http://127.0.0.1:5001/api/leagues" | head -3`
Expected: HTML başlangıcı (`<!DOCTYPE html>`) ve liglerin JSON'u

- [ ] **Step 3: Sunucuyu durdur**

Run: `pkill -f "python app.py" || true`

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: tek sayfa web arayüzü (maç listesi, tahmin kartı, kupon)"
```

---

### Task 7: Uçtan uca doğrulama (gerçek API)

**Files:**
- Modify: yok (yalnızca doğrulama; hata çıkarsa ilgili dosya düzeltilir)

**Interfaces:**
- Consumes: tüm sistem.

- [ ] **Step 1: Tüm birim testleri çalıştır**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && .venv/bin/pytest -q`
Expected: 21 passed

- [ ] **Step 2: Sunucuyu başlat, gerçek fixture çek**

Run: `cd "/Users/belliklidamla/Desktop/gncr/iddaa test" && (.venv/bin/python app.py &) && sleep 2 && curl -s "http://127.0.0.1:5001/api/fixtures?date=$(date +%Y-%m-%d)" | .venv/bin/python -m json.tool | head -30`
Expected: seçili liglerden maç listesi JSON'u (yaz döneminde İskandinav ligleri dolu olmalı)

- [ ] **Step 3: Gerçek tahmin çek**

Önceki adımın çıktısından bir `fixture_id` al; sonra:

Run: `curl -s "http://127.0.0.1:5001/api/predict?fixture=<ID>&date=$(date +%Y-%m-%d)" | .venv/bin/python -m json.tool`
Expected: `prediction` + `best_pick` alanlı JSON; olasılıklar 0-1 arası

- [ ] **Step 4: Gerçek kupon çek**

Run: `curl -s "http://127.0.0.1:5001/api/coupon?date=$(date +%Y-%m-%d)&size=5" | .venv/bin/python -m json.tool | head -40`
Expected: 5 pick'li kupon; `total_odds` ve `combined_probability` dolu

- [ ] **Step 5: Sunucuyu durdur, commit**

```bash
pkill -f "python app.py" || true
git add -A
git commit -m "test: uçtan uca doğrulama tamam" --allow-empty
```
