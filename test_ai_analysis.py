"""ai_analysis testleri — Anthropic istemcisi sahte nesneyle taklit edilir."""
import pytest

import ai_analysis
from ai_analysis import AiError, analyze_prediction, build_prompt


def _item():
    return {
        "fixture": {
            "home": {"id": "1", "name": "Ev FC"},
            "away": {"id": "2", "name": "Dep FC"},
            "league": "Test Lig",
            "kickoff": "2026-07-05T17:00Z",
            "league_slug": "swe.1",
            "fixture_id": "100",
            "status": "NS",
        },
        "form": {
            "home": {"scored_avg": 1.5, "conceded_avg": 0.8, "matches": 10},
            "away": {"scored_avg": 2.1, "conceded_avg": 1.3, "matches": 10},
        },
        "prediction": {
            "expected_goals": {"home": 1.4, "away": 1.6},
            "match_result": {"home": 0.35, "draw": 0.26, "away": 0.39},
            "over_under_25": {"over": 0.52, "under": 0.48},
            "btts": {"yes": 0.55, "no": 0.45},
            "most_likely_score": {"home": 1, "away": 1, "probability": 0.12},
        },
        "best_pick": {"label": "Deplasman kazanır", "probability": 0.39, "fair_odds": 2.56,
                      "market": "match_result", "selection": "away"},
    }


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block("text", text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class _FakeClient:
    def __init__(self, messages):
        class _Beta:
            pass
        self.beta = _Beta()
        self.beta.messages = messages


def test_build_prompt_includes_teams_and_stats():
    prompt = build_prompt(_item())
    assert "Ev FC" in prompt and "Dep FC" in prompt
    assert "2.1" in prompt          # away scored_avg
    assert "%39" in prompt          # away win probability


def test_analyze_returns_text():
    messages = _FakeMessages(response=_Response("Deplasman formda, tempolu maç beklenir."))
    result = analyze_prediction(_item(), client=_FakeClient(messages))
    assert "Deplasman formda" in result
    # Fable 5: thinking parametresi gönderilmemeli, fallback tanımlı olmalı.
    assert "thinking" not in messages.last_kwargs
    assert messages.last_kwargs["fallbacks"] == [{"model": ai_analysis.FALLBACK_MODEL}]


def test_analyze_refusal_raises():
    messages = _FakeMessages(response=_Response("", stop_reason="refusal"))
    with pytest.raises(AiError):
        analyze_prediction(_item(), client=_FakeClient(messages))


def test_analyze_api_error_raises_ai_error():
    import anthropic
    messages = _FakeMessages(error=anthropic.APIConnectionError(request=None))
    with pytest.raises(AiError):
        analyze_prediction(_item(), client=_FakeClient(messages))
