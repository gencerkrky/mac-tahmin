"""AI match analysis via Claude Fable 5.

The statistical engine (poisson.py) owns the numbers; this module only asks
the model to interpret them — form context, risks, what the probabilities
mean — in plain Turkish. The model never invents probabilities.

Fable 5 notes (per current API):
- Thinking is always on; the `thinking` parameter must be omitted entirely.
- Safety classifiers can return stop_reason "refusal"; we opt into the
  server-side fallback so such requests are transparently re-served by
  Claude Opus 4.8 inside the same call.
"""

import anthropic

ANALYSIS_MODEL = "claude-fable-5"
FALLBACK_MODEL = "claude-opus-4-8"
FALLBACK_BETA = "server-side-fallback-2026-06-01"

# Analyses are short; a tight cap keeps per-request cost predictable.
MAX_ANALYSIS_TOKENS = 2048

SYSTEM_PROMPT = (
    "Sen deneyimli bir futbol analistisin. Sana bir maçın form verileri ve "
    "Poisson modelinin hesapladığı olasılıklar verilecek. Görevin bu sayıları "
    "yorumlamak: takımların formunu, maçın olası senaryosunu ve modelin en "
    "güvendiği tahminin mantığını kısa ve net anlat. Kurallar: en fazla 120 "
    "kelime; yeni olasılık uydurma, verilen sayıları kullan; kesinlik garantisi "
    "verme; kumar teşviki yapma, bunun istatistiksel bir tahmin olduğunu belirt."
)


class AiError(Exception):
    """AI call failure with a user-presentable message."""


def _pct(p: float) -> str:
    return f"%{round(p * 100)}"


def build_prompt(item: dict) -> str:
    """Compact, deterministic summary of one analysed fixture for the model."""
    fx = item["fixture"]
    home, away = fx["home"]["name"], fx["away"]["name"]
    hf, af = item["form"]["home"], item["form"]["away"]
    p = item["prediction"]
    mr = p["match_result"]
    score = p["most_likely_score"]
    best = item["best_pick"]

    return (
        f"Maç: {home} - {away} ({fx['league']})\n"
        f"Form (son {hf['matches']} maç): {home} maç başı {hf['scored_avg']} gol attı, "
        f"{hf['conceded_avg']} yedi. {away} maç başı {af['scored_avg']} attı, "
        f"{af['conceded_avg']} yedi.\n"
        f"Model olasılıkları: ev {_pct(mr['home'])}, beraberlik {_pct(mr['draw'])}, "
        f"deplasman {_pct(mr['away'])}. 2.5 Üst {_pct(p['over_under_25']['over'])}, "
        f"KG Var {_pct(p['btts']['yes'])}. "
        f"En olası skor {score['home']}-{score['away']} ({_pct(score['probability'])}).\n"
        f"Modelin en güvendiği tahmin: {best['label']} {_pct(best['probability'])} "
        f"(adil oran {best['fair_odds']}).\n"
        f"Bu maçı analiz et."
    )


def analyze_prediction(item: dict, client: anthropic.Anthropic | None = None) -> str:
    """One short Turkish analysis for an analysed fixture."""
    if client is None:
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    try:
        response = client.beta.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=MAX_ANALYSIS_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks=[{"model": FALLBACK_MODEL}],
            output_config={"effort": "low"},  # short interpretive task
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(item)}],
        )
    except anthropic.AuthenticationError as exc:
        raise AiError("Anthropic API anahtarı geçersiz (.env dosyasını kontrol edin)") from exc
    except anthropic.RateLimitError as exc:
        raise AiError("AI servisi yoğun, biraz sonra tekrar deneyin") from exc
    except anthropic.APIConnectionError as exc:
        raise AiError("AI servisine bağlanılamadı") from exc
    except anthropic.APIStatusError as exc:
        raise AiError(f"AI isteği başarısız: {exc.message}") from exc

    # Whole fallback chain declined — no usable text.
    if response.stop_reason == "refusal":
        raise AiError("AI bu içerik için yanıt üretmedi")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AiError("AI boş yanıt döndürdü")
    return text
