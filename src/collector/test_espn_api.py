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


def test_get_schedule_season_type_selects_the_phase():
    """seasontype is THE phase selector on the team-schedule endpoint:
    2 = regular season (default, byte-identical URL to before), 3 =
    postseason. Everything downstream (snapshot.py's two-phase collection)
    hangs off this one parameter."""
    seen: list = []

    def fake_get_json(url):
        seen.append(url)
        return {"events": []}

    original = espn_api._get_json
    espn_api._get_json = fake_get_json
    try:
        espn_api.get_schedule(2026, 14, "nba")
        espn_api.get_schedule(2026, 14, "nba", season_type=3)
        espn_api.get_schedule(2026, 14, "wnba", season_type=3)
    finally:
        espn_api._get_json = original
    assert seen[0].endswith("/teams/14/schedule?season=2026&seasontype=2")
    assert seen[1].endswith("/teams/14/schedule?season=2026&seasontype=3")
    assert seen[2].endswith("/teams/14/schedule?season=2026&seasontype=3")
    assert "/basketball/wnba/" in seen[2]


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
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {},
                                         None)
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


def test_get_standings_pins_regular_season_records(monkeypatch):
    """seasontype=2 must ride every standings URL: ESPN's default totals
    fold preseason games into the record (verified live Oct 2026: the
    2026-27 table read 0-2 off preseason results), which passes the app's
    has-results guard and prints preseason scores as standings. Completed
    seasons answer identically either way (same verification: NBA
    2025-26, WNBA 2026), so the filter never hides a real table."""
    asked = []

    def _capture(url):
        asked.append(url)
        return {}

    monkeypatch.setattr(espn_api, "_get_json", _capture)
    espn_api.get_standings(2027)
    assert asked == ["https://site.web.api.espn.com/apis/v2/sports/"
                     "basketball/nba/standings?season=2027&seasontype=2"]
    espn_api.get_standings(2026, "wnba")
    assert asked[-1].endswith(
        "/basketball/wnba/standings?season=2026&seasontype=2")


def test_parse_standings_last_ten_and_differential():
    """L10 form and the team's +/- (per-game point differential) parse
    under ESPN's exact stat names, verified live Sep 2026. The minimal
    committed fixture predates both stats, so absent stats stay ABSENT --
    the row carries no fabricated zeros."""
    rows = parsing.parse_standings(_load("standings_2024-25.json"))
    assert all("last_ten" not in r and "differential" not in r
               for r in rows)
    entry = {
        "team": {"id": 5, "displayName": "Cleveland Cavaliers"},
        "stats": [
            {"name": "wins", "displayValue": "64"},
            {"name": "Last Ten Games", "displayValue": "8-2"},
            {"name": "differential", "displayValue": "+8.2"},
            {"name": "streak", "displayValue": "W3"},
        ],
    }
    row = parsing._parse_standing_entry(entry, "Eastern Conference")
    assert row["wins"] == 64 and row["streak"] == "W3"
    assert row["last_ten"] == "8-2"
    assert row["differential"] == 8.2


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
# WNBA: same shapes, single-calendar-year season labels (leagues.py)
# ---------------------------------------------------------------------------

def test_wnba_season_label_is_the_param():
    """A WNBA season IS its calendar year (May -> October): the label, the
    ESPN season param and the round-trip all stay single-year, unlike the
    NBA's ending-year arithmetic."""
    assert espn_api.season_param("2026", "wnba") == 2026
    assert espn_api.season_param("2010", "wnba") == 2010
    assert espn_api.season_label(2026, "wnba") == "2026"
    assert espn_api.season_label(espn_api.season_param("2010", "wnba"),
                                 "wnba") == "2010"
    # NBA defaults unchanged.
    assert espn_api.season_param("2010-11") == 2011
    assert espn_api.season_label(2011) == "2010-11"


def test_wnba_current_season_label_follows_may_october_calendar():
    """WNBA seasons run May -> October: Jan-Oct the calendar year is the
    season (in progress or not yet tipped off), Nov-Dec the off-season
    jumps to the next year -- mirroring how the NBA side jumps from July."""
    from datetime import date

    assert espn_api.current_season_label(date(2026, 5, 10), "wnba") == "2026"
    assert espn_api.current_season_label(date(2026, 9, 28), "wnba") == "2026"
    assert espn_api.current_season_label(date(2026, 10, 31), "wnba") == "2026"
    assert espn_api.current_season_label(date(2026, 11, 1), "wnba") == "2027"
    assert espn_api.current_season_label(date(2026, 12, 31), "wnba") == "2027"
    assert espn_api.current_season_label(date(2027, 4, 1), "wnba") == "2027"
    # NBA behavior untouched.
    assert espn_api.current_season_label(date(2026, 1, 15)) == "2025-26"
    assert espn_api.current_season_label(date(2026, 7, 1)) == "2026-27"


def test_wnba_date_derivation_is_the_calendar_year():
    """A May WNBA game belongs to that calendar year (the NBA rule would
    wrongly file it under the previous year's season)."""
    assert parsing.season_label_from_date("2026-05-10", "wnba") == "2026"
    assert parsing.season_label_from_date("2026-10-05", "wnba") == "2026"
    assert parsing.season_label_from_date("2019-07-01", "wnba") == "2019"
    # NBA rule unchanged.
    assert parsing.season_label_from_date("2026-05-10") == "2025-26"
    assert parsing.season_label_from_date("2026-01-15") == "2025-26"


def test_league_url_builders_swap_the_slug():
    assert "/basketball/wnba" in espn_api.site_api("wnba")
    assert "/basketball/wnba" in espn_api.web_api_v2("wnba")
    assert "/basketball/wnba" in espn_api.web_api_common("wnba")
    assert espn_api.site_api("nba") == espn_api.SITE_API
    with pytest.raises(ValueError):
        espn_api.site_api("nbaall")


def test_parse_wnba_summary_fixture_maps_the_same_labels():
    """Live-captured Sep 2026: the WNBA's box score carries ESPN's exact
    stat labels, so parse_summary works with league='wnba' (which only
    picks the date->season rule: a May 2026 game files under '2026')."""
    meta, rows = parsing.parse_summary(
        _load("wnba_summary_2026_final.json"), league="wnba")
    assert meta["status"] == "STATUS_FINAL"
    assert meta["season"] == "2026"
    assert meta["home_abbrev"] and meta["away_abbrev"]
    played = [r for r in rows if not r["did_not_play"]]
    assert played, "no player lines parsed"
    scorers = [r for r in played if r["pts"]]
    assert scorers, "label mapping failed to score anyone"
    assert any(r.get("plus_minus") is not None for r in played)
    # NBA fixture still parses with the default league.
    _, nba_rows = parsing.parse_summary(_load("summary_1995-96_bulls_hornets.json"))
    assert nba_rows


def test_parse_wnba_standings_fixture():
    """Live-captured Sep 2026: the WNBA's standings tree has the same
    East/West children with entries, so the NBA parser walks it unchanged."""
    rows = parsing.parse_standings(_load("wnba_standings_2026.json"))
    assert len(rows) == 15  # 15 franchises in 2026
    conferences = {r["conference"] for r in rows}
    assert conferences == {"Eastern Conference", "Western Conference"}
    assert all(r["wins"] + r["losses"] >= 0 for r in rows)
    ids = [r["team_id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_parse_wnba_roster_fixture_reads_positions_off_athletes():
    """Live-captured Sep 2026: the WNBA's roster groups carry a null
    group-level position (like the NBA's) while each athlete carries its
    own -- parse_roster reads the athlete first, so rows get real G/F/C."""
    rows = parsing.parse_roster(_load("wnba_roster_atl_2026.json"))
    assert len(rows) == 14
    assert {r["position"] for r in rows} <= {"G", "F", "C"}
    assert {r["position"] for r in rows} & {"G", "F"}
    assert all(r["team_id"] == 20 for r in rows)


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
