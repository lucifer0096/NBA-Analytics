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

    def get_schedule(season, team_id):
        if team_id == 15:
            raise RuntimeError("HTTP Error 500: Internal Server Error")
        return {"team_id": team_id}

    monkeypatch.setattr(snapshot.espn_api, "get_schedule", get_schedule)
    monkeypatch.setattr(snapshot.parsing, "parse_schedule",
                        lambda payload, season: [{
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

    def get_schedule(season, team_id):
        if team_id in (15, 16):
            raise RuntimeError("HTTP Error 500: Internal Server Error")
        return {"team_id": team_id}

    monkeypatch.setattr(snapshot.espn_api, "get_schedule", get_schedule)
    monkeypatch.setattr(snapshot.parsing, "parse_schedule",
                        lambda payload, season: [{
                            "game_id": "99", "date": "2013-01-01",
                            "status": "STATUS_FINAL", "home_score": "100",
                            "away_score": "90"}])

    out = snapshot.snapshot_schedule("2012-13", [11, 15, 16],
                                     snapshot.RateLimiter(0))

    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["game_id"] for r in rows] == ["1", "2"]  # existing kept


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

    def get_roster(team_id, season):
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
