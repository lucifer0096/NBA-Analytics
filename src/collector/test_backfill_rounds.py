"""Offline tests for the postseason-round backfill's pure helpers: the
mapping fill (idempotent, committed labels win) and the scoreboard day
candidates (ESPN buckets by US date, which trails UTC)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backfill_postseason_rounds as backfill  # noqa: E402


def test_fill_rounds_needs_a_round_and_never_overwrites():
    rows = [
        {"game_id": "1", "round": "East 1st Round"},      # committed: wins
        {"game_id": "2"},                                  # missing: filled
        {"game_id": "3", "round": ""},                     # empty: filled
        {"game_id": "4"},                                  # unmapped: stays
    ]
    filled = backfill.fill_rounds(rows, {"2": "NBA Finals",
                                         "3": "NBA Play-In",
                                         "1": "WRONG LABEL"})
    assert filled == 2
    assert rows[0]["round"] == "East 1st Round"  # never overwritten
    assert rows[1]["round"] == "NBA Finals"
    assert rows[2]["round"] == "NBA Play-In"
    assert "round" not in rows[3], "an unknown game gains no guessed round"
    # idempotent: annotated rows (1-3) are skipped -- only the
    # newly-mapped game 4 fills on the second pass.
    assert backfill.fill_rounds(rows, {"4": "NBA Finals"}) == 1


def test_day_candidates_cover_the_us_bucket_beside_utc():
    # 02:00Z is still the previous evening in the US: both buckets tried.
    assert backfill._day_candidates("2026-04-15T02:00:00Z") == [
        "20260414", "20260415"]
    # late UTC (US same day): the day itself and the day before still fit.
    assert backfill._day_candidates("2026-04-15T23:30:00Z") == [
        "20260414", "20260415"]
    assert backfill._day_candidates("not-a-date") == []
    assert backfill._day_candidates(None) == []


def test_round_of_event_reads_the_shared_headline_shape():
    event = {"competitions": [{"notes": [
        {"headline": "WEST 1ST ROUND - GAME 1"}]}]}
    assert backfill._round_of_event(event) == "WEST 1ST ROUND"
    assert backfill._round_of_event({"competitions": [{}]}) is None
    assert backfill._round_of_event({}) is None
