"""Offline tests for the per-league configuration (leagues.py).

The WNBA addition moves every NBA-shaped assumption (season labels, data
paths, ESPN award-type ids, honours tables) into leagues.py, so these tests
pin the config itself: names validate, paths never collide between leagues,
season conventions differ exactly where they must, and the honours tables
are the live-verified ESPN award names with matching label keys.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "collector"))

import awards  # noqa: E402
import leagues  # noqa: E402


def test_validate_rejects_unknown_league():
    assert leagues.validate("nba") == "nba"
    assert leagues.validate("wnba") == "wnba"
    with pytest.raises(ValueError, match="unknown league"):
        leagues.validate("nbaall")
    with pytest.raises(ValueError):
        leagues.cfg("euroleague")


def test_display_and_season_conventions_per_league():
    assert leagues.display("nba") == "NBA"
    assert leagues.display("wnba") == "WNBA"
    # Two-calendar-year labels for the NBA, single calendar year for the
    # WNBA -- the directory regexes must match exactly those.
    nba_re = leagues.season_dir_re("nba")
    wnba_re = leagues.season_dir_re("wnba")
    assert nba_re.fullmatch("2010-11") and not nba_re.fullmatch("2010")
    assert wnba_re.fullmatch("2010") and not wnba_re.fullmatch("2010-11")
    assert leagues.first_season("nba") == "2010-11"
    assert leagues.first_season("wnba") == "2010"
    # Same collection-window policy: both start in 2010.
    assert leagues.first_season("nba")[:4] == leagues.first_season("wnba")


def test_award_config_per_league():
    nba = leagues.cfg("nba")
    wnba = leagues.cfg("wnba")
    assert nba["finals_mvp_id"] == 43 and nba["champion_first_year"] == 1970
    # WNBA Finals MVP (257) doubles as the champion index from the league's
    # first season (1997); 15 usable award types (240 is empty).
    assert wnba["finals_mvp_id"] == 257
    assert wnba["champion_first_year"] == 1997
    assert len(wnba["award_type_ids"]) == 15
    assert 257 in wnba["award_type_ids"] and 240 not in wnba["award_type_ids"]
    assert nba["official_overrides"] and not wnba["official_overrides"]
    # Current-franchise floors: exact for the NBA, an expansion-proof floor
    # for the WNBA (15 franchises in 2026).
    assert nba["min_unique_teams"] == 30
    assert 10 <= wnba["min_unique_teams"] < 15
    # Champion overlay: none for the NBA (ESPN refs cover 1970+); the WNBA's
    # six ESPN team-ref gaps (Houston x4, LA x2) are league record.
    assert nba["verified_champions"] == {}
    assert wnba["verified_champions"] == {
        1997: 4, 1998: 4, 1999: 4, 2000: 4, 2001: 6, 2002: 6}


def test_data_paths_never_collide_and_nba_keeps_historical_names():
    # The NBA keeps the pre-WNBA file names (no 246MB rename); every WNBA
    # file carries '_wnba'.
    assert leagues.raw_dir("nba").endswith("/data/raw")
    assert leagues.raw_dir("wnba").endswith("/data/raw_wnba")
    assert leagues.races_dir("nba").endswith("/data/races")
    assert leagues.races_dir("wnba").endswith("/data/races_wnba")
    assert leagues.schedules_dir("nba").endswith("/data/schedules")
    assert leagues.schedules_dir("wnba").endswith("/data/schedules_wnba")
    assert leagues.history_cache_path("nba").endswith(
        "/data/processed/history_cache.json")
    assert leagues.history_cache_path("wnba").endswith(
        "/data/processed/history_cache_wnba.json")
    for stem in ("dashboard_teams", "dashboard_standings",
                 "dashboard_schedule", "dashboard_positions",
                 "dashboard_awards", "dashboard_players"):
        nba_name = f"{stem}.json"
        wnba_name = nba_name.replace("dashboard_", "dashboard_wnba_", 1)
        nba_path = leagues.dashboard_path(nba_name, "nba")
        wnba_path = leagues.dashboard_path(nba_name, "wnba")
        assert nba_path.endswith(f"/{nba_name}")
        assert wnba_path.endswith(f"/{wnba_name}")
        assert nba_path != wnba_path


def test_wnba_honours_table_is_the_live_verified_names():
    """Keys are ESPN's exact WNBA award names (live-verified Sep 2026); the
    label table mirrors the weight table key-for-key and every weight is a
    positive point value the GOAT ladder can normalise."""
    weights = leagues.WNBA_GOAT_HONOURS_WEIGHTS
    labels = leagues.WNBA_GOAT_HONOUR_LABELS
    assert set(weights) == set(labels)
    assert weights == {
        "MVP": 6.0,
        "Finals MVP": 5.0,
        "Defensive Player of the Year": 4.5,
        "All-WNBA 1st Team": 3.0,
        "All-Defensive 1st Team": 2.5,
        "All-WNBA 2nd Team": 2.0,
        "All-Defensive 2nd Team": 1.5,
        "Rookie of the Year": 1.5,
        "Sixth Player of the Year": 1.5,
        "Most Improved Player": 1.5,
        "All-Star MVP": 1.5,
        "Commissioner's Cup MVP": 1.0,
        "All-Rookie Team": 0.5,
        "WNBA Sportsmanship Award": 0.25,
        "Seasonlong Community Assist Award": 0.25,
    }
    assert all(w > 0 for w in weights.values())
    # No fabricated ties to NBA-only award names (the WNBA has no All-NBA
    # teams or conference Finals MVPs).
    assert "All-NBA 1st Team" not in weights
    assert "NBA Cup MVP" not in weights


def test_awards_aliases_the_league_tables():
    """awards keeps its original import paths but the tables live in
    leagues.py -- one definition per league, shared with the app."""
    assert awards.GOAT_HONOURS_WEIGHTS is leagues.GOAT_HONOURS_WEIGHTS
    assert awards.GOAT_HONOUR_LABELS is leagues.GOAT_HONOUR_LABELS
    assert awards.honours_weights("wnba") is leagues.WNBA_GOAT_HONOURS_WEIGHTS
    assert awards.honour_labels("wnba") is leagues.WNBA_GOAT_HONOUR_LABELS
    assert awards.honours_weights("nba") is awards.GOAT_HONOURS_WEIGHTS
