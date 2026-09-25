"""Deterministic offline tests: parsing pinned against REAL recorded ESPN
payloads in fixtures/ -- no network in the default suite (see pytest.ini).

The 1995-96 fixtures are deliberately part of the set: they pin that the
parser survives the oldest data stats.nba-era archives and ESPN both return,
even though the project's modeling window starts at 2010-11.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import espn_api
import parsing

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    import json

    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# season label <-> param (the off-by-one that breaks everything if wrong)
# ---------------------------------------------------------------------------

def test_season_param_is_the_end_year():
    assert espn_api.season_param("2010-11") == 2011
    assert espn_api.season_param("2024-25") == 2025


def test_season_label_round_trips():
    assert espn_api.season_label(2011) == "2010-11"
    assert espn_api.season_label(espn_api.season_param("2026-27")) == "2026-27"


def test_current_season_label_follows_nba_calendar_transition():
    import datetime

    # In January the season currently being played is the one that started
    # last October; in the July-September offseason the upcoming season is
    # already the useful one for schedule collection.
    assert espn_api.current_season_label(datetime.date(2026, 1, 15)) == "2025-26"
    assert espn_api.current_season_label(datetime.date(2026, 6, 30)) == "2025-26"
    assert espn_api.current_season_label(datetime.date(2026, 7, 1)) == "2026-27"
    assert espn_api.current_season_label(datetime.date(2026, 10, 20)) == "2026-27"


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------

def test_parse_teams_fixture():
    rows = parsing.parse_teams(_load("teams.json"))
    assert len(rows) >= 30
    bulls = next(r for r in rows if r["team_id"] == 4)
    assert bulls["abbrev"] == "CHI"


# ---------------------------------------------------------------------------
# WAF header rotation (the runner-side 403 fix)
# ---------------------------------------------------------------------------

def test_get_json_rotates_header_sets_on_403(monkeypatch):
    """A WAF that 403s the first client form must be offered the NEXT header
    set instead of the same rejected headers four times: the probe workflow
    proved site.api 403s the spoofed Chrome UA from runner IPs while the
    honest default client gets 200, so rotation is the fix contract."""
    import io
    import urllib.error
    import urllib.request

    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(dict(req.headers))
        if len(seen) < 3:
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {})
        return io.BytesIO(b'{"ok": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(espn_api.time, "sleep", lambda _s: None)
    assert espn_api._get_json("https://example.test/x") == {"ok": 1}
    # Three attempts: honest default (no UA), curl UA, browser UA.
    assert len(seen) == 3
    assert "User-agent" not in seen[0] and "Accept" not in seen[0]
    assert seen[1].get("User-agent") == "curl/8.5.0"
    assert "Chrome" in seen[2].get("User-agent", "")


# ---------------------------------------------------------------------------
# schedule (1995-96 fixture -- the pre-2010 window)
# ---------------------------------------------------------------------------

def test_parse_schedule_1995_96_fixture():
    rows = parsing.parse_schedule(_load("schedule_1995-96_bulls.json"))
    assert len(rows) == 82  # full pre-play-in NBA season
    first = min(rows, key=lambda r: r["date"])
    # ESPN's season param for this fixture was 1996 -> the 1995-96 season,
    # and the date-derived label must agree.
    assert first["season"] == "1995-96"
    # A team's schedule contains BOTH its home and away games (each of the
    # 82 games involves the requested team exactly once).
    assert all(r["home_id"] == 4 or r["away_id"] == 4 for r in rows)
    assert any(r["home_id"] == 4 for r in rows)
    assert any(r["away_id"] == 4 for r in rows)
    finals = [r for r in rows if r["status"] == "STATUS_FINAL"]
    assert len(finals) == 82
    sample = finals[0]
    assert sample["home_score"] > 0 and sample["away_score"] > 0


def test_season_label_from_date_boundaries():
    assert parsing.season_label_from_date("2010-12-31T00:00Z") == "2010-11"
    assert parsing.season_label_from_date("2011-01-01T00:00Z") == "2010-11"
    assert parsing.season_label_from_date("2011-06-15T00:00Z") == "2010-11"  # Finals
    assert parsing.season_label_from_date("2011-10-25T00:00Z") == "2011-12"


# ---------------------------------------------------------------------------
# summary / box score
# ---------------------------------------------------------------------------

def test_parse_summary_1995_96_fixture():
    meta, rows = parsing.parse_summary(_load("summary_1995-96_bulls_hornets.json"))
    assert meta["status"] == "STATUS_FINAL"
    assert meta["season"] == "1995-96"
    assert meta["home_abbrev"] == "CHI" and meta["away_abbrev"] == "CHA"
    assert meta["home_score"] == 105 and meta["away_score"] == 91
    assert len(rows) == 22  # both teams' lines incl. DNPs

    larry = next(r for r in rows if r["player_name"] == "Larry D. Johnson")
    # Larry Johnson played for Charlotte (the AWAY team) in this game:
    assert not larry["is_home"] and larry["starter"]
    assert not larry["did_not_play"]
    assert larry["min"] == 35 and larry["pts"] == 19 and larry["reb"] == 7
    assert (larry["fgm"], larry["fga"]) == (6, 13)
    assert (larry["fg3m"], larry["fg3a"]) == (0, 2)
    assert (larry["ftm"], larry["fta"]) == (7, 12)
    assert larry["ast"] == 2 and larry["stl"] == 0 and larry["blk"] == 0
    assert larry["opponent_abbrev"] == "CHI"

    # Every row's opponent must be the OTHER team in this game, never itself.
    for row in rows:
        expected = "CHA" if row["is_home"] else "CHI"
        assert row["opponent_abbrev"] == expected


def test_parse_summary_dnp_rows_are_zeroed_not_null():
    meta, rows = parsing.parse_summary(_load("summary_1995-96_bulls_hornets.json"))
    dnps = [r for r in rows if r["did_not_play"]]
    assert dnps, "fixture should contain did-not-play rows"
    for row in dnps:
        assert row["min"] == 0 and row["pts"] == 0 and row["reb"] == 0


# ---------------------------------------------------------------------------
# standings
# ---------------------------------------------------------------------------

def test_parse_standings_2024_25_fixture():
    rows = parsing.parse_standings(_load("standings_2024-25.json"))
    assert len(rows) == 30
    conferences = {r["conference"] for r in rows}
    assert conferences == {"Eastern Conference", "Western Conference"}
    cavs = next(r for r in rows if r["team_id"] == 5)  # Cleveland
    assert cavs["wins"] == 64 and cavs["losses"] == 18
    assert cavs["playoff_seed"] == 1
    # no duplicate team rows (the recursive walk can double-visit)
    ids = [r["team_id"] for r in rows]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------

def test_parse_roster_fixture():
    rows = parsing.parse_roster(_load("roster_chicago_2026.json"))
    assert len(rows) >= 15
    positions = {r["position"] for r in rows}
    assert positions and positions <= {"G", "F", "C"}
    assert all(r["team_id"] == 4 for r in rows)
    assert all(r["player_name"] for r in rows)


# ---------------------------------------------------------------------------
# live API (opt-in: pytest -m live)
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_teams_endpoint():
    teams = espn_api.get_teams()
    assert parsing.parse_teams(teams)


@pytest.mark.live
def test_live_schedule_endpoint_current_season():
    payload = espn_api.get_schedule(
        espn_api.season_param(espn_api.current_season_label()), 4
    )
    assert parsing.parse_schedule(payload)
