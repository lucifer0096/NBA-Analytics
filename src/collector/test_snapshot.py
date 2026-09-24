"""Deterministic tests for collector state decisions -- what gets fetched,
what gets skipped, and which seasons a command targets. No network.
"""

import csv
import json
import sys
from pathlib import Path

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
