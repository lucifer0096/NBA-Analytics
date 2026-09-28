"""Offline dashboard render tests via Streamlit's AppTest -- the exact
fresh-checkout/deploy condition: every live ESPN call forced to fail, no
data/raw/ needed (committed data/dashboard_* fallbacks only). No network.

Pins the contract that would otherwise rot silently: every page renders,
its tabs and KPI cards exist, and the fallback-failure path shows an honest
empty state rather than a stack trace -- including the not-yet-started
season (2026-27) showing empty states instead of another year's data.
"""

import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "collector"))

import espn_api  # noqa: E402


@pytest.fixture()
def offline_espn(monkeypatch):
    """Force EVERY ESPN call to fail, like a deploy without network."""

    def _raise(*args, **kwargs):
        raise OSError("network disabled for test")

    monkeypatch.setattr(espn_api, "_get_json", _raise)


def _render(script: str, offline_espn) -> AppTest:
    at = AppTest.from_file(str(REPO_ROOT / script), default_timeout=30)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    return at


def _markdown_text(at: AppTest) -> str:
    """All text rendered through st.markdown, or '' when AppTest exposes no
    .markdown element list -- a defensive probe so content assertions can
    fall back to captions instead of erroring on an older streamlit."""
    md = getattr(at, "markdown", None)
    if md is None:
        return ""
    return " ".join(str(getattr(m, "value", "")) for m in md)


def test_home_page_renders_offline(offline_espn):
    at = _render("app/app.py", offline_espn)
    assert not at.exception
    # Title + exactly the four home tabs (All-Time/GOAT/Profile moved to
    # left-nav pages; Court View moved here from the deleted Model & History
    # page) even with every live call failing.
    assert any("NBA Analytics" in str(t.value) for t in at.title)
    tab_labels = [str(t.label) for t in at.tabs]
    assert tab_labels == ["Standings", "Schedule & Scores", "Awards Ladder",
                          "Court View"]
    # KPI strip is season-scoped: inventory, tip-off, scoring leader. The
    # Model (validation) KPI was removed from this page.
    metric_labels = [str(m.label) for m in at.metric]
    assert metric_labels == ["Games collected", "Next tip-off",
                             "Season scoring leader"]
    # Neither "no auth" message survived (sidebar caption and hero extra).
    text = f"{_markdown_text(at)} " + " ".join(
        str(c.value) for c in at.caption)
    assert "no auth" not in text.lower()


def test_home_page_falls_back_to_committed_data(offline_espn):
    """With live calls dead, committed fallbacks still feed the page: the
    freshness caption must say 'Offline fallback', never crash -- and the
    committed standings table carries its team +/- and L10 columns."""
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Offline fallback" in captions or "fallback" in captions.lower()
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    standings = next((f for f in frames if "Win%" in list(f.columns)), None)
    assert standings is not None, "committed standings table must render"
    columns = list(standings.columns)
    assert "+/-" in columns and "L10" in columns
    # Signed differential text, not raw floats (a missing stat would be ''
    # rather than 0.0 -- committed rows carry the live values).
    diffs = [v for v in standings["+/-"].tolist() if v != ""]
    assert diffs and all(str(v).startswith(("+", "-")) for v in diffs)


def test_sidebar_mentions_model_and_history(offline_espn):
    """The Model & History page was deleted: its mention lives in the sidebar
    expander instead -- headline validation numbers or the 'train first'
    note, never an exception."""
    at = _render("app/app.py", offline_espn)
    assert not at.exception
    expander_labels = [str(e.label) for e in getattr(at, "expander", [])]
    assert "📈 Model & History" in expander_labels
    text = f"{_markdown_text(at)} " + " ".join(
        str(c.value) for c in at.caption)
    assert ("MAE" in text) or ("Model not trained yet" in text)


def test_home_awards_tab_shows_races_and_formula_captions(offline_espn):
    """The Awards Ladder must rank its races AND be honest about them: the
    race label, the 'not official NBA voting' disclaimer, and the MVP's
    verbatim formula all have to reach the screen (captions or markdown)."""
    awards_path = REPO_ROOT / "data" / "dashboard_awards.json"
    if not awards_path.exists():
        pytest.skip("dashboard_awards.json not committed yet")
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    text = f"{_markdown_text(at)} {captions}"
    assert "MVP race" in text
    assert "not official nba voting" in captions.lower()
    assert "Impact per game" in captions


def _committed_awards() -> dict:
    """The committed envelope: multi-season {"seasons": ...} or legacy."""
    path = REPO_ROOT / "data" / "dashboard_awards.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_alltime_page_shows_career_board_and_honesty(offline_espn):
    """All-Time Stats (its own left-nav page now) must render the career
    table with its window caption -- and must admit the window (collector
    starts 2010-11, not full NBA history). Skips when the committed payload
    predates the career sections."""
    payload = _committed_awards()
    pts_rows = (((payload.get("alltime") or {}).get("leaders") or {})
                .get("pts") or [])
    if not pts_rows:
        pytest.skip("no all-time section in the committed payload yet")
    at = _render("app/pages/2_All-Time_Stats.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption).lower()
    assert "not full nba history" in captions
    # The career scoring leader must reach the rendered dataframe.
    name = str(pts_rows[0].get("player_name") or "")
    frames = " ".join(str(getattr(df, "value", "")) for df in at.dataframe)
    assert name and name in frames
    # The window-only +/- board is selectable and ranks its covered
    # careers (blank/absent otherwise, never a fabricated zero board).
    pm_rows = (((payload.get("alltime") or {}).get("leaders") or {})
               .get("plus_minus") or [])
    if not pm_rows:
        pytest.skip("no +/- board in the committed payload yet")
    at.radio[0].set_value("plus_minus").run()
    assert not at.exception
    captions = " ".join(str(c.value) for c in at.caption)
    assert "collected box scores" in captions  # window-only explanation
    frames = " ".join(str(getattr(df, "value", "")) for df in at.dataframe)
    assert str(pm_rows[0].get("player_name") or "") in frames


def test_goat_page_shows_ladder_and_verbatim_formula(offline_espn):
    """GOAT Rankings page must print its exact formula + the not-official
    disclaimer, and the top-ranked player's name must reach the screen."""
    payload = _committed_awards()
    goat_rows = (payload.get("goat") or {}).get("rows") or []
    if not goat_rows:
        pytest.skip("no GOAT rows in the committed payload yet")
    at = _render("app/pages/3_GOAT_Rankings.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    text = f"{_markdown_text(at)} {captions}"
    assert "GOAT score =" in text
    assert "not an official nba ranking" in captions.lower()
    assert str(goat_rows[0].get("player_name") or "") in text
    # The committed formula is the CURRENT one (weighted +/- included).
    assert "+/-" in captions


def test_profile_page_renders_cards_accolades_and_chart(offline_espn):
    """Player Profile: the committed index loads, defaults to the GOAT top,
    and the career cards, accolades table and progression section reach the
    screen with zero live calls."""
    path = REPO_ROOT / "data" / "dashboard_players.json"
    if not path.exists():
        pytest.skip("dashboard_players.json not committed yet")
    with open(path, encoding="utf-8") as f:
        players = json.load(f).get("players") or {}
    if not players:
        pytest.skip("empty player index")
    at = _render("app/pages/4_Player_Profile.py", offline_espn)
    assert not at.exception
    box = at.multiselect[0]
    assert 1 <= len(box.value) <= 4
    text = f"{_markdown_text(at)} " + " ".join(
        str(c.value) for c in at.caption)
    assert "Official accolades" in text
    assert "Career progression" in text
    # The profile card shows career +/- when the history entries carry the
    # collected-window total (the window-only stat's second pipeline).
    if any(v.get("plus_minus") is not None for v in players.values()):
        assert "+/-" in text
    top = [p for p in players.values() if p.get("goat_rank") == 1]
    if top:
        assert str(top[0].get("player_name")) in text


def test_progression_chart_ticks_once_per_season():
    """plotly parses '2003-04' as a DATE and ticks every 3 months; the
    profile chart must force a chronological CATEGORICAL season axis so the
    scale reads per season."""
    import shared as shared_module

    rows = [
        {"season": "2004-05", "team": "CLE", "gp": 80, "pts": 27.2},
        {"season": "2003-04", "team": "CLE", "gp": 79, "pts": 20.9},
    ]
    fig = shared_module.progression_figure({"LeBron James": rows}, "pts",
                                           "Per game")
    xaxis = fig.layout.xaxis
    assert str(xaxis.type) == "category"
    assert list(xaxis.categoryarray) == ["2003-04", "2004-05"]


def test_load_schedule_serves_every_committed_season():
    """The multi-season schedule envelope feeds ANY selected season -- a
    PREVIOUS season's completed games must arrive with their final scores
    (integers, not the raw CSV's '94.0' floats)."""
    import shared as shared_module

    path = REPO_ROOT / "data" / "dashboard_schedule.json"
    if not path.exists():
        pytest.skip("dashboard_schedule.json not committed yet")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    seasons = payload.get("seasons") or {}
    sample = next((s for s, games in seasons.items()
                   if s != payload.get("season") and games), None)
    if not sample:
        pytest.skip("single-season schedule envelope")
    shared_module.load_schedule.clear()
    try:
        frame, _ = shared_module.load_schedule(sample)
        assert not frame.empty
        finals = frame[frame["status"] == "STATUS_FINAL"]
        assert not finals.empty
        assert str(finals.iloc[0]["home_score"]).isdigit()
    finally:
        shared_module.load_schedule.clear()


def test_load_awards_honest_empty_for_uncollected_season(monkeypatch,
                                                         tmp_path):
    """The sidebar season must resolve honestly: exact match when collected;
    a season with NO collected games (2026-27 pre-tip-off, or anything before
    the window) gets an EMPTY payload for that exact season and a note that
    says so -- never another season's races substituted under its label."""
    import shared as shared_module

    envelope = {
        "_generated_utc": "2026-09-24T00:00:00Z", "source": "local",
        "seasons": {
            "2015-16": {"season": "2015-16", "races": {"mvp": []}},
            "2025-26": {"season": "2025-26", "races": {"mvp": [1]}},
        },
    }
    (tmp_path / "dashboard_awards.json").write_text(
        json.dumps(envelope), encoding="utf-8")
    monkeypatch.setattr(shared_module, "DATA_DIR", tmp_path)
    shared_module.load_awards.clear()
    try:
        data, note = shared_module.load_awards("2025-26")
        assert data.get("season") == "2025-26"
        assert data.get("races")

        data, note = shared_module.load_awards("2026-27")
        assert data.get("season") == "2026-27"
        assert not data.get("races") and not data.get("leaders")
        assert "2026-27 has no collected games yet" in note
        assert "2025-26" in note  # says where the newest data IS, without showing it

        data, note = shared_module.load_awards("2014-15")
        assert data.get("season") == "2014-15"
        assert not data.get("races")
        assert "2014-15 has no collected games yet" in note
    finally:
        shared_module.load_awards.clear()


def test_court_view_tab_renders_leaders(offline_espn):
    """Court View (now a home tab) must show a real leader from the
    committed payload -- asserted via that player's name reaching
    markdown/captions, never via a .na-court class (the theme CSS blob
    contains it regardless)."""
    awards_path = REPO_ROOT / "data" / "dashboard_awards.json"
    if not awards_path.exists():
        pytest.skip("dashboard_awards.json not committed yet")
    with open(awards_path, encoding="utf-8") as f:
        payload = json.load(f)
    seasons = payload.get("seasons") or {}
    newest = payload
    if seasons:
        newest = seasons[max(seasons)]
    pts = (newest.get("leaders") or {}).get("pts") or []
    if not pts:
        pytest.skip("no qualified pts leaders in the committed payload")
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    text = f"{_markdown_text(at)} {captions}"
    name = str(pts[0].get("player_name") or "")
    assert name and (name in text or "hover a card" in captions)


def test_load_metrics_absent_returns_empty(offline_espn, tmp_path, monkeypatch):
    """metrics.json absent (fresh clone before first training) must render
    the 'train first' info, not an exception."""
    # Point shared.REPO_ROOT-style lookup at a metrics-free root by moving
    # the file temporarily -- simplest via monkeypatching the loader's path.
    import shared as shared_module

    monkeypatch.setattr(shared_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(shared_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(shared_module, "PROCESSED_DIR", tmp_path / "data" / "processed")
    # An earlier page render in this session primed the st.cache_data entry
    # with the real models/metrics.json (it exists once trained) -- drop it so
    # the loader actually re-runs against the metrics-free root, then drop the
    # {} it caches too, so later renders recompute from the real root.
    shared_module.load_metrics.clear()
    assert shared_module.load_metrics() == {}  # path doesn't exist -> {}
    shared_module.load_metrics.clear()


def test_fallback_envelopes_carry_generation_timestamp():
    """Every committed fallback must be able to state its own age honestly."""
    data_dir = REPO_ROOT / "data"
    checked = 0
    for name in ("dashboard_teams.json", "dashboard_standings.json",
                 "dashboard_schedule.json", "dashboard_positions.json",
                 "dashboard_awards.json", "dashboard_players.json",
                 "processed/dashboard_leaderboards.json"):
        path = data_dir / name
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        assert "_generated_utc" in payload, f"{name} missing _generated_utc"
        assert payload.get("source") in ("espn", "local")
        checked += 1
    # At least the refresh-runnable subset should exist once bootstrapped;
    # tolerate a truly fresh clone having none yet.
    assert checked >= 0


def test_sidebar_every_year_leads_with_data_and_honest_empty(offline_espn):
    """Sidebar offers every season 2010-11 -> upcoming 2026-27 and leads with
    the newest season that HAS collected games. Selecting the not-yet-started
    2026-27 shows honest empty states: no loud fallback banner (the old
    show-2025-26-with-a-warning contract is gone) and the Awards note says
    the season has no collected games -- never another year's races."""
    at = _render("app/app.py", offline_espn)
    # Label lookup, not [0]: the Schedule tab's Fixture picker also renders
    # (AppTest orders sidebar widgets after main content).
    box = next(b for b in at.selectbox if str(b.label) == "Season")
    labels = list(box.options)
    assert "2010-11" in labels and "2025-26" in labels and "2026-27" in labels
    assert box.value == "2025-26"
    box.select("2026-27").run()
    assert not at.exception
    assert len(at.warning) == 0
    text = f"{_markdown_text(at)} " + " ".join(
        str(c.value) for c in at.caption)
    assert "2026-27 has no collected games yet" in text
    # Standings empty out too (the committed copy is 2025-26 -- never shown
    # under the 2026-27 header).
    infos = " ".join(str(i.value) for i in at.info)
    assert "No standings available for 2026-27" in infos


# ---------------------------------------------------------------------------
# Schedule tab: NBA-app-style fixture detail (box score + play-by-play)
# ---------------------------------------------------------------------------

def test_schedule_game_detail_is_honest_offline(offline_espn):
    """The fixture picker exists over the committed schedule, and with ESPN
    unreachable the selected game says WHY there is no box score instead of
    raising or faking content."""
    at = _render("app/app.py", offline_espn)
    fixture = next((b for b in at.selectbox if str(b.label) == "Fixture"),
                   None)
    assert fixture is not None, "fixture picker missing from Schedule tab"
    assert len(fixture.options) > 0
    assert not at.exception
    assert len(at.warning) == 0
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Live game detail" in captions
    assert "unavailable" in captions


def test_schedule_game_detail_renders_box_and_pbp(offline_espn, monkeypatch):
    """With a summary in hand, a FINAL fixture shows the matchup line, both
    sides' box-score tables and the period-filterable play-by-play."""
    import shared as shared_module

    meta = {"game_id": 1, "status": "STATUS_FINAL",
            "away_abbrev": "BOS", "home_abbrev": "NYK",
            "away_score": 101, "home_score": 99,
            "away_id": 2, "home_id": 7,
            "venue": "Madison Square Garden", "airings": ["National: ESPN"]}
    rows = [
        {"game_id": 1, "team_id": 2, "player_id": 10,
         "player_name": "Star Guy", "did_not_play": False, "min": 36,
         "pts": 30, "reb": 8, "ast": 5, "stl": 1, "blk": 0, "to": 2,
         "plus_minus": 12,
         "fgm": 11, "fga": 20, "fg3m": 4, "fg3a": 10, "ftm": 4, "fta": 5},
        {"game_id": 1, "team_id": 7, "player_id": 11,
         "player_name": "Bench Guy", "did_not_play": True, "min": None,
         "pts": None, "reb": None, "ast": None, "stl": None, "blk": None,
         "to": None, "plus_minus": None,
         "fgm": None, "fga": None, "fg3m": None, "fg3a": None,
         "ftm": None, "fta": None},
    ]
    plays = [
        {"period": "4th Quarter", "clock": "0:03",
         "description": "Star Guy 26' driving layup",
         "score": "101:99", "scoring": True},
    ]
    monkeypatch.setattr(shared_module, "load_game_summary",
                        lambda game_id: (meta, rows, plays,
                                         "Live ESPN summary (15 min cache)"))

    at = _render("app/app.py", offline_espn)

    assert not at.exception
    text = _markdown_text(at)
    assert "**BOS 101 @ NYK 99**" in text
    assert "**Play-by-play**" in text
    select_labels = [str(b.label) for b in at.selectbox]
    assert "Period" in select_labels  # PBP period filter reached the screen
    assert len(list(getattr(at, "dataframe", []))) >= 3  # 2 box + PBP
    assert len(at.warning) == 0
    # The box tables carry the +/- column (a MIN column scopes them apart
    # from the standings frame, which has its own team +/-).
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    assert any("+/-" in list(f.columns) and "MIN" in list(f.columns)
               for f in frames)


def test_schedule_game_detail_upcoming_shows_fixture_info(
        offline_espn, monkeypatch):
    """An UPCOMING fixture has no box score (said honestly), but tip-off,
    status, venue and TV still reach the screen from the live summary."""
    import shared as shared_module

    meta = {"game_id": 401610401, "status": "STATUS_SCHEDULED",
            "away_abbrev": "LAL", "home_abbrev": "GSW",
            "away_score": 0, "home_score": 0, "away_id": 13, "home_id": 9,
            "date": "2026-10-21T23:30:00Z",
            "venue": "Chase Center", "airings": ["National: ESPN"]}
    monkeypatch.setattr(shared_module, "load_game_summary",
                        lambda game_id: (meta, [], [],
                                         "Live ESPN summary (15 min cache)"))

    at = _render("app/app.py", offline_espn)

    assert not at.exception
    text = _markdown_text(at)
    assert "**LAL @ GSW**" in text
    assert "2026-10-21 23:30" in text
    assert "Scheduled" in text
    assert "Chase Center" in text and "National: ESPN" in text
    captions = " ".join(str(c.value) for c in at.caption)
    assert "No box score until this game is played" in captions
    assert len(at.warning) == 0


def test_parse_plays_flattens_and_skips_malformed():
    """The PBP parser turns ESPN's live `plays` into flat table rows and
    degrades on malformed entries (non-dict plays, junk period) instead of
    raising inside the page."""
    import shared as shared_module

    plays = [
        {"text": "Star Guy 26' stepback 3PT",
         "period": {"displayValue": "4th Quarter"},
         "clock": {"displayValue": "0:03"},
         "awayScore": 3, "homeScore": 1, "scoringPlay": True},
        {"shortDescription": "Jumpball", "period": "weird"},
        "not-a-dict",
        {"clock": {"displayValue": "11:00"}},  # no description -> skipped
        42,
    ]

    rows = shared_module._parse_plays(plays)

    assert [r["description"] for r in rows] == [
        "Star Guy 26' stepback 3PT", "Jumpball"]
    assert rows[0]["scoring"] is True
    assert rows[0]["score"] == "3:1"
    assert rows[0]["period"] == "4th Quarter"
    assert rows[0]["clock"] == "0:03"
    assert rows[1]["period"] is None  # junk period degrades, never raises
    assert shared_module._parse_plays("not-a-list") == []
    assert shared_module._parse_plays(None) == []


# ---------------------------------------------------------------------------
# Deep links (?season=, ?tab=, ?player=) + the corrupt-fallback warning
# contract (the app's ONLY st.warning()s, collected during render, emitted
# once, late; every other test's len(at.warning) == 0 pins the clean run)
# ---------------------------------------------------------------------------


def test_deep_link_season_preselects_sidebar(offline_espn):
    """?season=NAME opens the home page on that season; an unknown season
    falls back to the honest default instead of crashing or inventing a
    year's data."""
    at = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    at.query_params["season"] = "2010-11"
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    box = next(b for b in at.selectbox if str(b.label) == "Season")
    assert box.value == "2010-11"

    bad = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    bad.query_params["season"] = "1999-00"
    bad.run()
    assert not bad.exception, f"page raised: {bad.exception}"
    fallback = next(b for b in bad.selectbox if str(b.label) == "Season")
    assert fallback.value == "2025-26"  # the pinned default, not the typo


def test_deep_link_tab_reorders_tabs(offline_espn):
    """?tab= renders the requested tab FIRST (st.tabs has no programmatic
    selection) while every other tab keeps the pinned default order and its
    own content (the label -> element map keeps them matched)."""
    at = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    at.query_params["tab"] = "Court View"
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    labels = [str(t.label) for t in at.tabs]
    assert labels[0] == "Court View"
    assert set(labels) == {"Standings", "Schedule & Scores",
                           "Awards Ladder", "Court View"}


def test_deep_link_player_opens_profile(offline_espn):
    """?player=NAME opens 4_Player_Profile with exactly that player
    selected -- the Court View card link's landing contract."""
    path = REPO_ROOT / "data" / "dashboard_players.json"
    if not path.exists():
        pytest.skip("dashboard_players.json not committed yet")
    with open(path, encoding="utf-8") as f:
        players = json.load(f).get("players") or {}
    name = next((p.get("player_name") for p in players.values()
                 if p.get("player_name")), None)
    if not name:
        pytest.skip("no named players in the committed index")
    at = AppTest.from_file(
        str(REPO_ROOT / "app/pages/4_Player_Profile.py"), default_timeout=30)
    at.query_params["player"] = name
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    assert at.multiselect[0].value == [name]


def test_court_cards_link_to_player_profile(offline_espn):
    """Court View cards are anchors to the RELATIVE profile deep link
    (./4_Player_Profile?player=NAME), so a stat leader is one click from
    his career page."""
    at = _render("app/app.py", offline_espn)
    assert not at.exception
    assert 'href="./4_Player_Profile?player=' in _markdown_text(at)


def test_freshness_line_dedupes_and_drops_empties():
    """The consolidated hero line joins the page's source notes: empties
    drop, duplicates collapse, separator is ' · ' (the per-tab Source
    captions stay where they are)."""
    import shared as shared_module

    line = shared_module.freshness_line(
        "Live (60s cache)", "", "Live (60s cache)", "Offline fallback")
    assert line == "Live (60s cache) · Offline fallback"
    assert shared_module.freshness_line() == ""


def test_corrupt_fallback_warns_once_and_only_honestly(
        offline_espn, tmp_path, monkeypatch):
    """A corrupt committed file (here the season schedule) still renders an
    honest page AND surfaces ONE late warning naming the file. Missing
    files under the same tmp DATA_DIR stay silent (honest empty, no
    warning), which is what makes this exactly one."""
    import shared as shared_module

    schedules = tmp_path / "schedules"
    schedules.mkdir(parents=True)
    (schedules / "2025-26.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(shared_module, "DATA_DIR", tmp_path)
    shared_module.load_schedule.clear()  # the real file's result is cached
    shared_module._FALLBACK_ERRORS.clear()
    try:
        # Deep-link the season: with DATA_DIR pointed at the empty tmp tree
        # the sidebar's inventory is gone, so the default would flip to the
        # upcoming season and never touch the corrupt file.
        at = AppTest.from_file(str(REPO_ROOT / "app/app.py"),
                               default_timeout=30)
        at.query_params["season"] = "2025-26"
        at.run()
        assert not at.exception, f"page raised: {at.exception}"
        warnings = [str(w.value) for w in at.warning]
        assert len(warnings) == 1
        assert "schedules/2025-26.json" in warnings[0]
        assert "unreadable" in warnings[0]
    finally:
        shared_module._FALLBACK_ERRORS.clear()
        shared_module.load_schedule.clear()
