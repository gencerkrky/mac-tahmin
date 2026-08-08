"""warehouse.py testleri — ağ yok, SQLite geçici dosyada."""
import json

import pytest

import warehouse as W


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "wh.db")
    W.init_db(path)
    return path


def _row(event_id, kickoff, home_id="1", away_id="2", hg=1.0, ag=0.0,
         league="swe.1", season=2026):
    return {"event_id": event_id, "league_slug": league, "season": season,
            "kickoff": kickoff, "home_id": home_id, "away_id": away_id,
            "home_name": f"T{home_id}", "away_name": f"T{away_id}",
            "home_goals": hg, "away_goals": ag}


def test_upsert_is_idempotent_on_event_id(db):
    # Aynı maç iki takımın çizelgesinde görünür; ikinci kez yazmak kopya üretmemeli.
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z")], db)
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z")], db)
    assert W.summary(db)["total"] == 1


def test_upsert_refreshes_score(db):
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z", hg=1.0, ag=0.0)], db)
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z", hg=3.0, ag=2.0)], db)
    log = W.team_log("1", path=db)
    assert log[0]["scored"] == 3.0 and log[0]["conceded"] == 2.0


def test_team_log_orients_home_and_away(db):
    W.upsert_matches([
        _row("e1", "2026-05-01T15:00Z", home_id="1", away_id="2", hg=2.0, ag=1.0),
        _row("e2", "2026-05-08T15:00Z", home_id="2", away_id="1", hg=0.0, ag=3.0),
    ], db)
    log = W.team_log("1", path=db)
    assert len(log) == 2
    recent, older = log            # most-recent first
    assert recent["venue"] == "away" and recent["scored"] == 3.0
    assert recent["opponent_id"] == "2"
    assert older["venue"] == "home" and older["scored"] == 2.0


def test_team_log_before_iso_excludes_later_matches(db):
    # Sızıntı önleme: kesim anındaki ve sonrasındaki maçlar görünmemeli.
    W.upsert_matches([
        _row("e1", "2026-05-01T15:00Z"),
        _row("e2", "2026-05-08T15:00Z"),
        _row("e3", "2026-05-15T15:00Z"),
    ], db)
    log = W.team_log("1", before_iso="2026-05-08T15:00Z", path=db)
    assert [m["date"] for m in log] == ["2026-05-01T15:00Z"]


def test_team_log_respects_limit_and_league(db):
    W.upsert_matches([_row(f"e{i}", f"2026-05-{i:02d}T15:00Z") for i in range(1, 10)], db)
    W.upsert_matches([_row("x1", "2026-05-20T15:00Z", league="nor.1")], db)
    assert len(W.team_log("1", limit=3, path=db)) == 3
    assert len(W.team_log("1", league_slug="nor.1", path=db)) == 1


def test_league_goal_average(db):
    # (2+1)/2 = 1.5 ve (0+0)/2 = 0 → ortalama 0.75
    W.upsert_matches([
        _row("e1", "2026-05-01T15:00Z", hg=2.0, ag=1.0),
        _row("e2", "2026-05-08T15:00Z", hg=0.0, ag=0.0),
    ], db)
    assert W.league_goal_average("swe.1", path=db) == pytest.approx(0.75)


def test_league_goal_average_none_when_empty(db):
    # Veri yokken uydurma sabit dönmemeli — çağıran karar versin.
    assert W.league_goal_average("swe.1", path=db) is None


def test_league_goal_average_time_sliced(db):
    W.upsert_matches([
        _row("e1", "2026-05-01T15:00Z", hg=4.0, ag=4.0),   # 4.0 ort
        _row("e2", "2026-06-01T15:00Z", hg=0.0, ag=0.0),
    ], db)
    assert W.league_goal_average("swe.1", before_iso="2026-05-15T00:00Z",
                                 path=db) == pytest.approx(4.0)


def test_stats_attach_to_correct_side(db):
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z", home_id="1", away_id="2")], db)
    stats = {"home": {"totalShots": "14"}, "away": {"totalShots": "5"}}
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE matches SET stats_json = ? WHERE event_id = ?",
                     (json.dumps(stats), "e1"))
    home_log = W.team_log("1", path=db)[0]
    away_log = W.team_log("2", path=db)[0]
    assert home_log["stats"]["totalShots"] == "14"
    assert home_log["opponent_stats"]["totalShots"] == "5"
    assert away_log["stats"]["totalShots"] == "5"          # ters taraftan bakınca


def test_team_log_without_stats_gives_empty_dicts(db):
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z")], db)
    m = W.team_log("1", path=db)[0]
    assert m["stats"] == {} and m["opponent_stats"] == {}


def test_rows_from_schedule_skips_unfinished_and_corrupt():
    def event(eid, completed, home_score, away_score):
        return {"id": eid, "date": "2026-05-01T15:00Z", "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": "A"},
                 "score": {"value": home_score}},
                {"homeAway": "away", "team": {"id": "2", "displayName": "B"},
                 "score": {"value": away_score}},
            ],
            "status": {"type": {"completed": completed}}}]}
    payload = {"season": {"year": 2026}, "events": [
        event("ok", True, 2, 1),
        event("unfinished", False, 0, 0),
        event("corrupt", True, None, 1),      # bitmiş ama skorsuz → 0-0 sayılmamalı
    ]}
    rows = W._rows_from_schedule(payload, "swe.1")
    assert [r["event_id"] for r in rows] == ["ok"]
    assert rows[0]["season"] == 2026


def test_stats_coverage_reports_progress(db):
    W.upsert_matches([_row("e1", "2026-05-01T15:00Z"),
                      _row("e2", "2026-05-02T15:00Z")], db)
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE matches SET stats_json = '{}' WHERE event_id = 'e1'")
    cov = W.stats_coverage(db)
    assert cov["total"] == 2 and cov["with_stats"] == 1 and cov["pct"] == 50.0


def _fixture(fid, status="FT", hg="2", ag="1", kickoff="2026-05-01T15:00Z"):
    return {"fixture_id": fid, "status": status, "kickoff": kickoff,
            "league_slug": "swe.1", "league": "Allsvenskan",
            "home": {"id": "1", "name": "A"}, "away": {"id": "2", "name": "B"},
            "goals": {"home": hg, "away": ag}}


def test_ingest_days_stores_only_finished(monkeypatch, db):
    import api_client
    monkeypatch.setattr(api_client, "get_fixtures", lambda d: [
        _fixture("f1"),
        _fixture("f2", status="NS", hg=None, ag=None),
        _fixture("f3", hg=None),          # bitmiş ama skor bozuk
    ])
    n = W.ingest_days(["2026-05-01"], db)
    assert n == 1
    assert W.summary(db)["total"] == 1


def test_ingest_days_survives_a_failing_day(monkeypatch, db):
    import api_client
    def flaky(date_str):
        if date_str == "2026-05-01":
            raise api_client.ApiError("down")
        return [_fixture("f9", kickoff="2026-05-02T15:00Z")]
    monkeypatch.setattr(api_client, "get_fixtures", flaky)
    n = W.ingest_days(["2026-05-01", "2026-05-02"], db)
    assert n == 1                          # kötü gün tüm tazelemeyi düşürmemeli


def test_ingest_days_records_progress_marker(monkeypatch, db):
    import api_client
    monkeypatch.setattr(api_client, "get_fixtures", lambda d: [])
    W.ingest_days(["2026-05-01", "2026-05-03"], db)
    assert W.get_meta("last_daily_ingest", db) == "2026-05-03"


def test_ingest_days_upserts_rather_than_duplicating(monkeypatch, db):
    import api_client
    monkeypatch.setattr(api_client, "get_fixtures", lambda d: [_fixture("f1")])
    W.ingest_days(["2026-05-01"], db)
    W.ingest_days(["2026-05-01"], db)       # aynı maç tekrar
    assert W.summary(db)["total"] == 1


def test_meta_roundtrip(db):
    assert W.get_meta("nope", db) is None
    W._set_meta("k", "v", db)
    assert W.get_meta("k", db) == "v"
