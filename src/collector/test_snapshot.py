"""Deterministic tests for collector state decisions -- what gets fetched,
what gets skipped, and which seasons a command targets. No network.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import snapshot


def test_backfill_range_is_newest_first_and_inclusive(tmp_path, monkeypatch):
    seasons = snapshot._season_range("2010-11", "2012-13")
    assert seasons == ["2012-13", "2011-12", "2010-11"]
    # reversed arguments still produce a sane window (no crash, no inversion)
    assert snapshot._season_range("2012-13", "2010-11") == seasons


def test_season_range_single_season():
    assert snapshot._season_range("2020-21", "2020-21") == ["2020-21"]


def test_latest_completed_season_uses_end_year():
    import datetime

    # During the 2025-26 season...
    assert snapshot.latest_completed_season(datetime.date(2026, 2, 1)) == "2025-26"
    # ...and after it ended, before the next one starts:
    assert snapshot.latest_completed_season(datetime.date(2026, 9, 23)) == "2025-26"
    # After the calendar rolls over (Jan 2027, 2026-27 in progress):
    assert snapshot.latest_completed_season(datetime.date(2027, 1, 10)) == "2026-27"


def test_latest_completed_season_wnba_follows_october_end():
    import datetime

    # The WNBA season runs May -> October: mid-finals (late Sep) the current
    # year is NOT completed yet; from November it is; the following Feb it
    # stays the year that finished.
    assert snapshot.latest_completed_season(
        datetime.date(2026, 9, 28), "wnba") == "2025"
    assert snapshot.latest_completed_season(
        datetime.date(2026, 11, 15), "wnba") == "2026"
    assert snapshot.latest_completed_season(
        datetime.date(2027, 2, 1), "wnba") == "2026"
    assert snapshot.latest_completed_season(
        datetime.date(2026, 5, 1), "wnba") == "2025"


def test_set_league_rebinds_paths_and_season_labels(monkeypatch):
    """set_league() switches every derived module constant together (the
    WNBA's data lives under data/raw_wnba with single-year labels), and the
    NBA's values survive a round trip so one run can never mix conventions."""
    saved = {name: getattr(snapshot, name) for name in
             ("LEAGUE", "RAW_DIR", "STATE_PATH", "BACKFILL_FIRST_SEASON")}
    try:
        snapshot.set_league("wnba")
        assert snapshot.LEAGUE == "wnba"
        assert snapshot.RAW_DIR.endswith("raw_wnba")
        assert "raw_wnba" in snapshot.STATE_PATH
        assert snapshot.BACKFILL_FIRST_SEASON == "2010"
        assert snapshot._season_range("2010", "2012") == ["2012", "2011", "2010"]
        with pytest.raises(ValueError):
            snapshot.set_league("euroleague")
    finally:
        for name, value in saved.items():
            setattr(snapshot, name, value)
    assert snapshot.LEAGUE == "nba"
    assert snapshot._season_range("2010-11", "2012-13") == [
        "2012-13", "2011-12", "2010-11"]


def _write_schedule(tmp_path, monkeypatch, rows):
    raw = tmp_path / "raw"
    (raw / "2012-13" / "games").mkdir(parents=True)
    monkeypatch.setattr(snapshot, "RAW_DIR", str(raw))
    with open(raw / "2012-13" / "schedule.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return raw


def _game(game_id, status):
    return {"game_id": game_id, "date": "2013-01-01", "status": status}


def _stored_game(game_id):
    return {
        "game": {"game_id": game_id},
        "players": [{"team_id": 1}, {"team_id": 2}],
    }


def test_pending_box_scores_only_final_and_missing(tmp_path, monkeypatch):
    raw = _write_schedule(tmp_path, monkeypatch, [
        _game(1, "STATUS_FINAL"),
        _game(2, "STATUS_FINAL"),
        _game(3, "STATUS_SCHEDULED"),
    ])
    games_dir = raw / "2012-13" / "games"
    (games_dir / "1.json").write_text(json.dumps(_stored_game(1)))

    pending = snapshot.pending_box_scores("2012-13", [
        _game(1, "STATUS_FINAL"),
        _game(2, "STATUS_FINAL"),
        _game(3, "STATUS_SCHEDULED"),
    ])
    # game 1 already stored, game 3 not final yet -- only game 2 is pending
    assert pending == [2]


def test_pending_empty_when_everything_stored(tmp_path, monkeypatch):
    raw = _write_schedule(tmp_path, monkeypatch, [_game(10, "STATUS_FINAL")])
    (raw / "2012-13" / "games" / "10.json").write_text(
        json.dumps(_stored_game(10))
    )
    assert snapshot.pending_box_scores("2012-13", [_game(10, "STATUS_FINAL")]) == []


def test_pending_retries_partial_or_corrupt_box_score(tmp_path, monkeypatch):
    raw = _write_schedule(tmp_path, monkeypatch, [_game(10, "STATUS_FINAL")])
    (raw / "2012-13" / "games" / "10.json").write_text("{not-json")
    assert snapshot.pending_box_scores("2012-13", [_game(10, "STATUS_FINAL")]) == [10]


def test_rate_limiter_enforces_min_interval():
    import time

    limiter = snapshot.RateLimiter(0.05)
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    elapsed = time.monotonic() - start
    # 3 waits with 0.05 gap -> at least ~0.10s of enforced spacing
    assert elapsed >= 0.09


def test_rate_limiter_disabled_when_zero():
    import time

    limiter = snapshot.RateLimiter(0)
    start = time.monotonic()
    for _ in range(100):
        limiter.wait()
    assert time.monotonic() - start < 0.5


# ---------------------------------------------------------------------------
# Fault isolation: one bad ESPN call must never kill the whole run (the
# Sep 2026 weekly-refresh failure was ONE team's roster HTTP 500 aborting
# snapshot.py before any season got refreshed).
# ---------------------------------------------------------------------------

def test_snapshot_schedule_tolerates_one_teams_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "RAW_DIR", str(tmp_path / "raw"))

    def get_schedule(season, team_id, league="nba", season_type=2):
        if team_id == 15:
            raise RuntimeError("HTTP Error 500: Internal Server Error")
        return {"team_id": team_id}

    monkeypatch.setattr(snapshot.espn_api, "get_schedule", get_schedule)
    monkeypatch.setattr(snapshot.parsing, "parse_schedule",
                        lambda payload, season, league="nba": [{
                            "game_id": f"{payload['team_id']}-1",
                            "date": "2013-01-01", "status": "STATUS_FINAL",
                            "home_score": "100", "away_score": "90"}])

    out = snapshot.snapshot_schedule("2012-13", [11, 15, 17],
                                     snapshot.RateLimiter(0))

    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert sorted(r["game_id"] for r in rows) == ["11-1", "17-1"]


def test_snapshot_schedule_keeps_existing_when_partial_would_shrink(
        tmp_path, monkeypatch):
    """Partial failures plus a THINNER result than the previous run: the
    existing schedule wins instead of being overwritten with fewer games."""
    _write_schedule(tmp_path, monkeypatch, [
        _game(1, "STATUS_FINAL"), _game(2, "STATUS_FINAL")])

    def get_schedule(season, team_id, league="nba", season_type=2):
        if team_id in (15, 16):
            raise RuntimeError("HTTP Error 500: Internal Server Error")
        return {"team_id": team_id}

    monkeypatch.setattr(snapshot.espn_api, "get_schedule", get_schedule)
    monkeypatch.setattr(snapshot.parsing, "parse_schedule",
                        lambda payload, season, league="nba": [{
                            "game_id": "99", "date": "2013-01-01",
                            "status": "STATUS_FINAL", "home_score": "100",
                            "away_score": "90"}])

    out = snapshot.snapshot_schedule("2012-13", [11, 15, 16],
                                     snapshot.RateLimiter(0))

    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["game_id"] for r in rows] == ["1", "2"]  # existing kept


# ---------------------------------------------------------------------------
# Postseason phase + the play-in gap probe: the only path to games ESPN's
# team-schedule endpoint omits entirely (seasontype=3 serves the playoff
# rounds; play-in answers on NO seasontype -- see snapshot_gap_games)
# ---------------------------------------------------------------------------

def test_postseason_phase_skips_non_playoff_teams_without_failure(
        tmp_path, monkeypatch):
    """A team outside the playoffs answers with NO postseason games: the
    normal answer, never a failure -- and the rows land in postseason.csv,
    the tree refresh copies to its own committed tree."""
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(snapshot, "RAW_DIR", str(raw))

    def get_schedule(season, team_id, league="nba", season_type=2):
        assert season_type == 3  # the phase selector reaches the endpoint
        return {"team_id": team_id}

    monkeypatch.setattr(snapshot.espn_api, "get_schedule", get_schedule)
    # Teams 11 and 17 made the bracket; 16 went home after the regular
    # season (empty parse, not an exception, not a failed call).
    monkeypatch.setattr(snapshot.parsing, "parse_schedule",
                        lambda payload, season, league="nba": (
                            [{"game_id": f"{payload['team_id']}-po",
                              "date": "2013-04-20", "status": "STATUS_FINAL",
                              "home_score": "100", "away_score": "90"}]
                            if payload["team_id"] in (11, 17) else []))

    out = snapshot.snapshot_schedule("2012-13", [11, 16, 17],
                                     snapshot.RateLimiter(0),
                                     phase="postseason")

    assert out.endswith("postseason.csv")
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert sorted(r["game_id"] for r in rows) == ["11-po", "17-po"]


def test_postseason_writes_nothing_without_a_bracket_and_keeps_old_files(
        tmp_path, monkeypatch, capsys):
    """Season whose playoffs don't exist yet: no header-only CSV that
    would read as 'postseason collected, zero games' -- and a file a
    previous run wrote survives the zero-game answer untouched (the same
    offseason guard the regular schedule has)."""
    raw = tmp_path / "raw"
    (raw / "2012-13").mkdir(parents=True)
    monkeypatch.setattr(snapshot, "RAW_DIR", str(raw))
    monkeypatch.setattr(snapshot.espn_api, "get_schedule",
                        lambda season, team_id, league="nba", season_type=2:
                        {"team_id": team_id})
    monkeypatch.setattr(snapshot.parsing, "parse_schedule",
                        lambda payload, season, league="nba": [])

    out = snapshot.snapshot_schedule("2012-13", [11, 17],
                                     snapshot.RateLimiter(0),
                                     phase="postseason")
    assert out == ""
    assert not (raw / "2012-13" / "postseason.csv").exists()

    kept = raw / "2012-13" / "postseason.csv"
    kept.write_text("game_id,date\n9,2013-04-20\n", encoding="utf-8")
    out = snapshot.snapshot_schedule("2012-13", [11, 17],
                                     snapshot.RateLimiter(0),
                                     phase="postseason")
    assert out.endswith("postseason.csv")
    assert kept.read_text(encoding="utf-8") == "game_id,date\n9,2013-04-20\n"
    assert "keeping existing" in capsys.readouterr().out


def test_gap_probe_collects_playin_games_no_seasontype_serves(
        tmp_path, monkeypatch):
    """Play-in games live in the calendar gap between the regular season's
    last day and the playoffs' first game, reachable only via the
    scoreboard: probe ONLY those days (never a whole month), merge real
    parse_schedule rows deduped on game_id, and stay idempotent across
    runs (probing again, adding nothing)."""
    raw = _write_schedule(tmp_path, monkeypatch, [
        {"game_id": "1", "date": "2013-01-05", "status": "STATUS_FINAL"}])
    post = raw / "2012-13" / "postseason.csv"
    fields = ["game_id", "date", "season", "home_id", "home_abbrev",
              "home_score", "away_id", "away_abbrev", "away_score",
              "status", "neutral"]
    with open(post, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"game_id": "2", "date": "2013-01-08",
                         "season": "2012-13", "home_id": "4",
                         "home_abbrev": "CHI", "home_score": "95",
                         "away_id": "2", "away_abbrev": "BOS",
                         "away_score": "91", "status": "STATUS_FINAL",
                         "neutral": "False"})

    asked: list = []

    def get_scoreboard(dates, league="nba"):
        asked.append(dates)
        if dates != "20130106":
            return {"events": []}
        return {"events": [{
            "id": "77", "date": "2013-01-06T00:00Z",
            "competitions": [{
                "status": {"type": {"name": "STATUS_FINAL"}},
                "competitors": [
                    {"homeAway": "home",
                     "team": {"id": "5", "abbreviation": "CLE"},
                     "score": {"displayValue": "94"}},
                    {"homeAway": "away",
                     "team": {"id": "9", "abbreviation": "TOR"},
                     "score": {"displayValue": "90"}},
                ],
            }],
        }]}

    monkeypatch.setattr(snapshot.espn_api, "get_scoreboard", get_scoreboard)

    added = snapshot.snapshot_gap_games("2012-13", snapshot.RateLimiter(0))

    assert added == 1
    assert asked == ["20130106", "20130107"]  # only the gap days
    rows = snapshot.load_postseason("2012-13")
    # date-sorted merge: the Jan 6 play-in lands before the Jan 8 playoff
    # game already on disk; the real parser produced the full row shape.
    assert [r["game_id"] for r in rows] == ["77", "2"]
    assert rows[0]["home_abbrev"] == "CLE"
    assert rows[0]["status"] == "STATUS_FINAL"

    asked.clear()
    # The collected Jan 6 play-in now FLANKS the boundary (last regular
    # Jan 5 -> first postseason Jan 6): the gap is closed, so a second run
    # probes nothing and adds nothing -- idempotent AND converged.
    assert snapshot.snapshot_gap_games("2012-13",
                                       snapshot.RateLimiter(0)) == 0
    assert asked == []
    assert len(snapshot.load_postseason("2012-13")) == 2  # not re-added


def test_gap_probe_is_a_noop_without_a_postseason_file(tmp_path, monkeypatch):
    """A season with no bracket (2026-27 pre-tip-off) or no regular
    schedule makes ZERO scoreboard calls: nothing to probe between."""
    raw = _write_schedule(tmp_path, monkeypatch, [
        {"game_id": "1", "date": "2013-01-05", "status": "STATUS_FINAL"}])
    monkeypatch.setattr(snapshot.espn_api, "get_scoreboard",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no call expected")))
    assert snapshot.snapshot_gap_games("2012-13",
                                       snapshot.RateLimiter(0)) == 0
    assert not (raw / "2012-13" / "postseason.csv").exists()


def test_player_positions_partial_failure_keeps_existing_rows(
        tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(snapshot, "RAW_DIR", str(raw))
    existing = {"updated_utc": "2026-09-01T00:00:00Z",
                "players": [{"player_id": 1, "player_name": "Old Center",
                             "team_id": 15, "position": "C"}]}
    (raw / "player_positions.json").write_text(json.dumps(existing),
                                               encoding="utf-8")

    def get_roster(team_id, season, league="nba"):
        if team_id == 15:
            raise RuntimeError("HTTP Error 500: Internal Server Error")
        return {"team_id": team_id}

    monkeypatch.setattr(snapshot.espn_api, "get_roster", get_roster)
    monkeypatch.setattr(snapshot.parsing, "parse_roster",
                        lambda payload, team_id: [{
                            "player_id": 100 + team_id,
                            "player_name": f"P{team_id}",
                            "team_id": team_id, "position": "G"}])

    out = snapshot.snapshot_player_positions([15, 16],
                                             snapshot.RateLimiter(0),
                                             force=True)

    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert {(r["team_id"], r["player_id"]) for r in data["players"]} == \
        {(15, 1), (16, 116)}


def test_player_positions_total_failure_keeps_existing_file(
        tmp_path, monkeypatch):
    """When EVERY roster call fails, the existing map stays byte-identical
    (old stamp included -- it must not claim a refresh that never happened)
    and the run continues."""
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(snapshot, "RAW_DIR", str(raw))
    existing = {"updated_utc": "2026-09-01T00:00:00Z",
                "players": [{"player_id": 1, "team_id": 15}]}
    (raw / "player_positions.json").write_text(json.dumps(existing),
                                               encoding="utf-8")

    def boom(team_id, season):
        raise RuntimeError("HTTP Error 500: Internal Server Error")

    monkeypatch.setattr(snapshot.espn_api, "get_roster", boom)

    out = snapshot.snapshot_player_positions([15, 16],
                                             snapshot.RateLimiter(0),
                                             force=True)

    assert json.loads(Path(out).read_text(encoding="utf-8")) == existing


def test_main_attempts_every_season_then_exits_nonzero(tmp_path, monkeypatch):
    """A hard failure in ONE season still fails the run (the workflow's
    failure issue must fire) -- but only after every season had its turn,
    so a --backfill isn't thrown away at the first bad season."""
    monkeypatch.setattr(snapshot, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(snapshot, "load_team_ids", lambda: [1, 2])
    monkeypatch.setattr(snapshot, "snapshot_player_positions",
                        lambda *a, **k: "positions.json")
    monkeypatch.setattr(snapshot, "_save_state", lambda results: None)
    attempted = []

    def run_season(season, args, limiter, team_ids):
        attempted.append(season)
        if season == "2011-12":
            raise RuntimeError("boom")
        return {"season": season}

    monkeypatch.setattr(snapshot, "run_season", run_season)
    monkeypatch.setattr(sys, "argv",
                        ["snapshot.py", "--from", "2010-11", "--to", "2012-13"])

    with pytest.raises(SystemExit) as excinfo:
        snapshot.main()

    assert excinfo.value.code == 1
    assert attempted == ["2012-13", "2011-12", "2010-11"]  # ALL attempted


def test_main_all_seasons_ok_returns_without_error(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(snapshot, "load_team_ids", lambda: [1, 2])
    monkeypatch.setattr(snapshot, "snapshot_player_positions",
                        lambda *a, **k: "positions.json")
    monkeypatch.setattr(snapshot, "_save_state", lambda results: None)
    monkeypatch.setattr(snapshot, "run_season",
                        lambda season, args, limiter, team_ids: {"season": season})
    monkeypatch.setattr(sys, "argv", ["snapshot.py", "--season", "2012-13"])

    assert snapshot.main() is None  # no SystemExit on the happy path
