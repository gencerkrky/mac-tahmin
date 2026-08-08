"""generate_site.py'nin saf yardımcıları — ağ yok."""
import pytest

from generate_site import FORM_SUMMARY_MATCHES, form_summary


def _form(entries, matches=None):
    """entries: (scored, conceded, venue) en yeni önce."""
    log = [{"date": f"2026-05-{20-i:02d}T15:00Z", "scored": s, "conceded": c,
            "venue": v, "opponent_id": "x"}
           for i, (s, c, v) in enumerate(entries)]
    return {"log": log, "matches": matches if matches is not None else len(log),
            "scored_avg": round(sum(e[0] for e in entries) / len(entries), 3),
            "conceded_avg": round(sum(e[1] for e in entries) / len(entries), 3)}


def test_recent_form_sequence_newest_first():
    # G (2-0), M (0-1), B (1-1)
    s = form_summary(_form([(2, 0, "home"), (0, 1, "away"), (1, 1, "home")]))
    assert s["recent_form"] == "GMB"


def test_recent_form_capped_at_summary_length():
    s = form_summary(_form([(1, 0, "home")] * 20))
    assert len(s["recent_form"]) == FORM_SUMMARY_MATCHES
    assert len(s["recent"]) == FORM_SUMMARY_MATCHES


def test_venue_splits_are_separate():
    s = form_summary(_form([
        (3, 0, "home"), (2, 1, "home"),      # evde: 2.5 attı, 0.5 yedi
        (0, 2, "away"), (1, 3, "away"),      # deplasmanda: 0.5 attı, 2.5 yedi
    ]))
    assert s["home_matches"] == 2 and s["away_matches"] == 2
    assert s["home_scored_avg"] == pytest.approx(2.5)
    assert s["home_conceded_avg"] == pytest.approx(0.5)
    assert s["away_scored_avg"] == pytest.approx(0.5)
    assert s["away_conceded_avg"] == pytest.approx(2.5)


def test_venue_avg_none_when_no_matches_at_venue():
    # Sadece ev maçı olan takım: deplasman ortalaması uydurulmamalı.
    s = form_summary(_form([(1, 0, "home"), (2, 1, "home")]))
    assert s["away_matches"] == 0
    assert s["away_scored_avg"] is None and s["away_conceded_avg"] is None


def test_recent_entries_carry_readable_fields():
    s = form_summary(_form([(2, 1, "home")]))
    entry = s["recent"][0]
    assert entry["date"] == "2026-05-20"          # sadece gün, saat değil
    assert entry["venue"] == "home"
    assert entry["scored"] == 2 and entry["conceded"] == 1


def test_empty_log_does_not_crash():
    s = form_summary({"log": [], "matches": 0, "scored_avg": 1.35,
                      "conceded_avg": 1.35})
    assert s["recent_form"] == ""
    assert s["recent"] == []
    assert s["home_scored_avg"] is None and s["away_scored_avg"] is None
    assert s["matches"] == 0


def test_missing_log_key_is_tolerated():
    # Eski/bozuk form kaydı siteyi düşürmemeli.
    s = form_summary({"matches": 0})
    assert s["recent_form"] == "" and s["recent"] == []
