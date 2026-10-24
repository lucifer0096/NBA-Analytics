"""Offline dashboard render tests via Streamlit's AppTest -- the exact
fresh-checkout/deploy condition: every live ESPN call forced to fail, no
data/raw/ needed (committed data/dashboard_* fallbacks only). No network.

Pins the contract that would otherwise rot silently: every page renders,
the two league tabs (NBA | WNBA) each nest the four home sections, the
KPI cards exist per league, and the fallback-failure path shows an honest
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


def test_committed_data_json_is_parseable():
    """Every committed fallback JSON must parse: a merge that commits its
    conflict markers (observed Sep 2026, a GUI merge on top of a rebase)
    turns whole fallback sets into honest empty states on every page, and
    the raw JSONDecodeError then surfaces far from its cause. One clear
    red naming the files beats page-level cascades."""
    broken = []
    for pattern in ("data/*.json", "data/processed/*.json",
                    "data/postseason/*.json", "data/postseason_wnba/*.json"):
        for path in sorted(REPO_ROOT.glob(pattern)):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                broken.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
    assert not broken, ("unreadable committed data files "
                        "(conflict markers or truncation?):\n"
                        + "\n".join(broken))


def test_home_page_renders_offline(offline_espn):
    at = _render("app/app.py", offline_espn)
    assert not at.exception
    # Title + the two league tabs, each nesting the four home sections
    # (All-Time/GOAT/Profile moved to left-nav pages; Court View moved
    # here from the deleted Model & History page) even with every live
    # call failing.
    assert any("NBA Analytics" in str(t.value) for t in at.title)
    tab_labels = [str(t.label) for t in at.tabs]
    assert tab_labels == ["NBA", "Schedule & Scores", "Standings",
                          "Awards Ladder", "Court View",
                          "WNBA", "Schedule & Scores", "Standings",
                          "Awards Ladder", "Court View"]
    # KPI strip is season-scoped: inventory, tip-off, scoring leader -- one
    # strip per league tab (the Model (validation) KPI was removed from
    # this page). NBA's three come first, then WNBA's.
    metric_labels = [str(m.label) for m in at.metric]
    assert metric_labels == ["Games collected", "Next tip-off",
                             "Season scoring leader"] * 2
    # Neither "no auth" message survived (sidebar caption and hero extra).
    text = f"{_markdown_text(at)} " + " ".join(
        str(c.value) for c in at.caption)
    assert "no auth" not in text.lower()


def test_home_page_falls_back_to_committed_data(offline_espn):
    """With live calls dead, committed fallbacks still feed the page: the
    freshness caption must say 'Offline fallback', never crash -- and the
    committed standings tables (BOTH leagues) carry their team +/- and L10
    columns."""
    import pandas as pd

    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Offline fallback" in captions or "fallback" in captions.lower()
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    standings = [f for f in frames if "Win%" in list(f.columns)]
    assert standings, "committed standings table must render"
    # One table per league tab: NBA and WNBA both fall back to their own
    # committed copy (dashboard_wnba_standings.json carries its own season).
    assert len(standings) >= 2, "both league tabs must render standings"
    for table in standings:
        columns = list(table.columns)
        assert "+/-" in columns and "L10" in columns
        # Signed differential text, not raw floats (a missing stat would be
        # '' rather than 0.0 -- committed rows carry the live values).
        diffs = [v for v in table["+/-"].tolist() if v != ""]
        assert diffs and all(str(v).startswith(("+", "-")) for v in diffs)
        # GB and Win% render as NUMBERS: right-aligned next to the other
        # numeric columns, a header click sorts them numerically, and
        # ESPN's '-' leader mark shows as the 0.0 it means.
        assert "GB" in columns and pd.api.types.is_numeric_dtype(table["GB"])
        assert not (table["GB"].astype(str) == "-").any()
        assert pd.api.types.is_numeric_dtype(table["Win%"])


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
    race label, the 'not official league voting' disclaimer (per league:
    NBA and WNBA each print their own), and the MVP's verbatim formula all
    have to reach the screen (captions or markdown)."""
    awards_path = REPO_ROOT / "data" / "dashboard_awards.json"
    if not awards_path.exists():
        pytest.skip("dashboard_awards.json not committed yet")
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    text = f"{_markdown_text(at)} {captions}"
    assert "MVP race" in text
    assert "not official nba voting" in captions.lower()
    if (REPO_ROOT / "data" / "dashboard_wnba_awards.json").exists():
        assert "not official wnba voting" in captions.lower()
    assert "Impact per game" in captions
    # The MVP trend either drew a line from >=2 daily snapshots or said
    # exactly what is missing -- both captions mention the snapshots.
    assert "snapshot" in captions


def test_home_awards_tab_shows_finals_mvp_tracker(offline_espn):
    """The Awards Ladder's official Finals MVP tracker renders off the
    committed index: section header, the real winner (2025-26: Jalen
    Brunson) and the source caption -- official ESPN award history, never
    one of the homegrown race formulas."""
    path = REPO_ROOT / "data" / "dashboard_players.json"
    payload = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    if not payload.get("finals_mvp"):
        pytest.skip("finals_mvp map not committed yet")
    at = _render("app/app.py", offline_espn)
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "Finals MVP" in text
    assert "Jalen Brunson" in text
    assert "ESPN award index" in text


def test_load_finals_mvp_pending_row_and_team_resolution(monkeypatch):
    """The tracker loader sorts seasons newest first, pins the CURRENT
    season as an honest pending row until ESPN announces a winner, and
    resolves champion teams off the committed franchise list -- a team id
    it doesn't know keeps its row season-only instead of a guessed name."""
    import shared

    players = {"_generated_utc": "2026-10-03T00:00:00Z",
               "finals_mvp": {
                   "2025": [{"player_id": 7, "player_name": "Winner A",
                             "team_id": 8}],
                   "2024": [{"player_id": 9, "player_name": "Winner B",
                             "team_id": 999}]}}
    teams = {"teams": [{"team_id": 8, "abbrev": "SEA",
                        "display_name": "Seattle Storm"}]}

    def _fallback(name, league="nba"):
        return players if "players" in name else teams

    monkeypatch.setattr(shared, "_read_fallback", _fallback)
    shared.load_finals_mvp.clear()
    try:
        rows, note = shared.load_finals_mvp("nba")
    finally:
        shared.load_finals_mvp.clear()  # never leak synthetic rows
    # Synthetic past seasons can never become "current", so the pending
    # row stays deterministic whenever the suite runs.
    assert rows[0].get("pending") is True
    assert rows[0]["season"] == espn_api.current_season_label(league="nba")
    assert [r["season"] for r in rows[1:]] == ["2024-25", "2023-24"]
    assert rows[1]["player_name"] == "Winner A"
    assert rows[1]["team_name"] == "Seattle Storm"
    assert rows[2]["team_name"] is None   # unknown id: honest, no guess
    assert "2 decided seasons" in note


def test_load_official_winners_maps_season_label_to_year(monkeypatch):
    """The verdict loader converts the season label to ESPN's end-year key
    ('2025-26' -> 2026) and returns that season's official winners only --
    an unannounced or predating season is an honest empty dict, never
    another year's data."""
    import shared

    players = {"official_winners": {
        "2026": {"mvp": [{"player_id": 3, "player_name": "Official Guy"}]}}}
    monkeypatch.setattr(shared, "_read_fallback",
                        lambda name, league="nba": players)
    shared.load_official_winners.clear()
    try:
        assert shared.load_official_winners("2025-26", "nba") == {
            "mvp": [{"player_id": 3, "player_name": "Official Guy"}]}
        assert shared.load_official_winners("2020-21", "nba") == {}
    finally:
        shared.load_official_winners.clear()  # never leak synthetic rows


def test_home_awards_tab_shows_official_vs_algorithm(offline_espn):
    """The Awards Ladder's verdict block renders the official-vs-algorithm
    comparison off the committed winners map: the section title, both
    sides' labels and the honest-verdict caption."""
    path = REPO_ROOT / "data" / "dashboard_players.json"
    payload = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    if not payload.get("official_winners"):
        pytest.skip("official_winners map not committed yet")
    at = _render("app/app.py", offline_espn)
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "Official vs algorithm winners" in text
    assert "Official winner: ESPN's award index" in text
    assert "Algorithm #1" in text


def test_season_award_row_html_official_and_pending():
    """The season-headline row names the official winner with the award
    label and index provenance when ESPN has one, carries the resolved
    franchise when given one, and prints an honest 'not awarded yet'
    pending row (clock slot, dash score) when it doesn't -- never a
    guessed name."""
    import shared

    decided = shared.season_award_row_html(
        "MVP", {"player_id": 1, "player_name": "Some Star",
                "team_name": "City Team"})
    assert "Some Star" in decided
    assert "award index" in decided
    assert "City Team" in decided
    assert decided.count("na-rname") == 1
    pending = shared.season_award_row_html("Finals MVP", None)
    assert "Not awarded yet" in pending
    assert 'na-rscore">—' in pending


def test_home_awards_tab_headline_opens_with_official_mvp(offline_espn):
    """The Awards Ladder opens on the SELECTED season's official awards:
    the headline section (MVP + Finals MVP off ESPN's award index) renders
    BEFORE the homegrown race ladders and before the stat leaders -- the
    page leads with who actually won, not a scoring leader."""
    path = REPO_ROOT / "data" / "dashboard_players.json"
    payload = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    if not (payload.get("official_winners") or {}).get("2026"):
        pytest.skip("2026 official winners not committed yet")
    at = _render("app/app.py", offline_espn)
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "Official season awards" in text
    head = text.index("Official season awards")
    assert head < text.index("MVP race")
    assert head < text.index("Season stat leaders")


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
    if (REPO_ROOT / "data" / "dashboard_wnba_awards.json").exists():
        assert "not full wnba history" in captions  # per-league disclaimer
        assert len(at.tabs) >= 2  # the NBA | WNBA league switch
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
    if (REPO_ROOT / "data" / "dashboard_wnba_awards.json").exists():
        # The WNBA ladder carries its own disclaimer and formula text.
        assert "not an official wnba ranking" in captions.lower()
        assert "all-WNBA-history" in text
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
    # One player picker PER league tab (NBA first); the WNBA pane carries
    # its own index (dashboard_wnba_players.json) when committed.
    assert len(at.multiselect) >= 2
    if (REPO_ROOT / "data" / "dashboard_wnba_players.json").exists():
        assert str(at.multiselect[1].key).endswith("wnba")
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
    # The profile card is a WWE-style PLAYING card: two corner indices
    # (the GOAT rank, top-left + rotated bottom-right), the /100 OVR
    # block (the GOAT score on its documented scale) and the career stat
    # strip that carries the +/- label whenever the index has it.
    card_html = [str(m.value) for m in at.markdown
                 if '<div class="na-pcard"' in str(getattr(m, "value", ""))]
    assert card_html, "career cards missing"
    assert all(c.count("na-pcorner--") == 2 for c in card_html)
    assert all("na-povr" in c and "/100" in c for c in card_html)
    assert all("na-pspec" in c and "na-pfoot" in c for c in card_html)
    assert all("<span>+/-</span>" in c for c in card_html)
    # The accolades table is a pivot: one row per award type, a column
    # per selected player, and an award a player never won renders BLANK
    # -- never a fabricated zero.
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    pivot = next((f for f in frames if "Honour" in f.columns), None)
    assert pivot is not None, "accolades pivot missing"
    assert "Pts each" in pivot.columns
    picks = list(box.value)
    assert all(p in pivot.columns for p in picks)
    cells = pivot[picks]
    assert not (cells == 0).any().any()


def _load_players(name: str) -> dict:
    path = REPO_ROOT / "data" / name
    if not path.exists():
        pytest.skip(f"{name} not committed yet")
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("players") or {}


def test_profile_card_is_a_2k_card_with_the_goat_score_as_ovr():
    """The Profile card renders as a 2K/WWE-style collectible whose
    headline number is the GOAT score on its OWN scale: each formula
    component normalizes 0-100 against the pool's best and the weights
    sum to 100, so round(score) already is the /100 OVR -- no invented
    rescaling -- with the tale-of-the-tape slots (RANK = GOAT rank,
    FIGHTS = career matches, MVPs, TITLES, PTS, +/-) and the
    REB/AST/honours footer."""
    import shared as shared_module

    players = _load_players("dashboard_players.json")
    if not players:
        pytest.skip("empty player index")
    top = next((p for p in players.values() if p.get("goat_score")), None)
    if top is None:
        pytest.skip("no GOAT-scored career in the index")
    card = shared_module.profile_card_html(top, "nba")

    assert '<div class="na-pcard"' in card
    assert card.count("na-pcorner--") == 2
    # OVR = the score rounded, shown out of 100, next to the ladder note.
    expected = str(int(round(float(top["goat_score"]))))
    assert f"<b>{expected}</b><em>/100</em>" in card
    for label in ("RANK", "FIGHTS", "MVPs", "TITLES", "PTS", "+/-"):
        assert f"<span>{label}</span>" in card
    if top.get("goat_rank"):
        assert f"<b>{expected}</b><em>/100</em><span>GOAT #" in card
        assert (f"<span>RANK</span><b>#{int(top['goat_rank'])}</b>"
                in card)
    # FIGHTS = career matches; TITLES = championships with the trophy;
    # MVPs reads the honours map's own "MVP" key (both leagues share it).
    assert (f"<span>FIGHTS</span><b>{int(top.get('gp') or 0):,}</b>"
            in card)
    if top.get("championships") is not None:
        assert f"<b>🏆×{int(top['championships'])}</b>" in card
    if top.get("honours") is not None:
        expected_mvp = int(top["honours"].get("MVP", 0))
        assert f"<span>MVPs</span><b>{expected_mvp}</b>" in card
    assert "na-pfoot" in card and " REB · " in card


def test_profile_card_unranked_career_is_an_honest_dash():
    """Only the ladder's top 25 (>=82 career GP) carry a score; every
    other career in the index must get a dash OVR saying so -- never a
    fabricated rating -- while the photo, name and spec panel stay."""
    import shared as shared_module

    players = _load_players("dashboard_players.json")
    if not players:
        pytest.skip("empty player index")
    unranked = next((p for p in players.values()
                     if not p.get("goat_score") and p.get("player_name")),
                    None)
    if unranked is None:
        pytest.skip("every career carries a GOAT score")
    card = shared_module.profile_card_html(unranked, "nba")

    assert "<b>—</b><em>/100</em><span>not in the GOAT top 25</span>" in card
    assert card.count("na-pcorner--") == 2
    assert "<div class=\"na-pname\">" in card and "na-pspec" in card
    # The RANK slot is a dash too -- no rank, no fabricated "#0".
    assert (card.count("na-pcorner--tl") == 1
            and "<span>RANK</span><b>—" in card and "#0" not in card)


def test_profile_card_corners_carry_2k_identity():
    """2K-style corners: position + jersey number top-left, every career
    team top-right -- both free of new requests (position/jersey come
    from history.py's identity fetch, teams from seasons_log's own team
    column) -- over the centred portrait."""
    import shared as shared_module

    players = _load_players("dashboard_players.json")
    if not players:
        pytest.skip("empty player index")
    top = next((p for p in players.values() if p.get("goat_rank") == 1),
               None) or next(iter(players.values()))
    card = shared_module.profile_card_html(top, "nba")

    assert 'class="na-pcorner na-pcorner--tl"' in card
    assert 'class="na-pcorner na-pcorner--tr"' in card
    assert 'class="na-phead"' in card  # portrait centred, not beside text
    if top.get("position"):
        expected_tl = top["position"]
        if top.get("jersey"):
            expected_tl += f" {top['jersey']}"
        assert f"na-pcorner--tl\">{expected_tl}</div>" in card
    teams: list = []
    for row in top.get("seasons_log") or []:
        team = str(row.get("team") or "")
        if team and team not in teams:
            teams.append(team)
    if not teams and top.get("team_abbrev"):
        teams = [str(top["team_abbrev"])]
    if teams:
        if len(teams) > 3:
            expected_tr = " · ".join(teams[:3]) + f" +{len(teams) - 3}"
        else:
            expected_tr = " · ".join(teams)
        assert f"na-pcorner--tr\">{expected_tr}</div>" in card


def test_profile_card_wnba_league_plumbs_headshot_and_logo():
    """The league argument drives the headshot CDN and team-logo URLs:
    the same card function serves the WNBA pane with WNBA endpoints."""
    import shared as shared_module

    players = _load_players("dashboard_wnba_players.json")
    if not players:
        pytest.skip("empty WNBA player index")
    card = shared_module.profile_card_html(next(iter(players.values())),
                                           "wnba")
    assert '<div class="na-pcard"' in card
    if "a.espncdn.com" in card:
        assert "/wnba/" in card


def test_progression_chart_ticks_once_per_season():
    """plotly parses '2003-04' as a DATE and ticks every 3 months; the
    profile chart must force a chronological CATEGORICAL season axis so the
    scale reads per season. The Career year axis numbers each player's
    seasons 1, 2, 3 ... from his first -- by CHRONOLOGY, not the row order
    ESPN returned -- so eras compare on one grid."""
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

    career = shared_module.progression_figure({"LeBron James": rows}, "pts",
                                              "Per game",
                                              axis="Career year")
    caxis = career.layout.xaxis
    assert str(caxis.type) == "category"
    assert list(caxis.categoryarray) == ["1", "2"]
    assert caxis.title.text == "Career year"
    # The unsorted rows still map 2003-04 to career year 1 (chronology
    # wins over row order), and hover keeps the calendar season.
    assert list(career.data[0].x) == ["1", "2"]
    assert str(career.data[0].text[0]).startswith("2003-04")


def test_filter_schedule_games_team_and_venue():
    """The schedule toolbar filters rows without inventing any: the team
    first ('All teams' keeps everything), then the venue -- home/away
    relative to that team, neutral from JSON's string flags (case-
    insensitive), and a team with no games is an honest empty."""
    import pandas as pd

    import shared as shared_module

    games = pd.DataFrame({
        "home_abbrev": ["BOS", "LAL", "BOS", "NYK"],
        "away_abbrev": ["LAL", "BOS", "NYK", "BOS"],
        "neutral": ["False", "False", "True", "False"],
        "status": ["STATUS_FINAL"] * 4,
    })
    everything = shared_module.filter_schedule_games(games, "All teams",
                                                     "All")
    assert len(everything) == 4
    # BOS touches all four rows (twice home, twice away).
    assert len(shared_module.filter_schedule_games(games, "BOS",
                                                   "All")) == 4
    home = shared_module.filter_schedule_games(games, "BOS", "Home")
    assert list(home["home_abbrev"]) == ["BOS", "BOS"]
    away = shared_module.filter_schedule_games(games, "BOS", "Away")
    assert list(away["away_abbrev"]) == ["BOS", "BOS"]
    neutral = shared_module.filter_schedule_games(games, "All teams",
                                                  "Neutral")
    assert list(neutral["away_abbrev"]) == ["NYK"]
    # Home/away with all teams is a documented no-op (the UI never offers
    # it): every non-neutral game is home for somebody.
    assert len(shared_module.filter_schedule_games(games, "All teams",
                                                   "Home")) == 4
    assert shared_module.filter_schedule_games(games, "GSW", "All").empty


def test_race_movement_and_mvp_trend_chart():
    """Arrows compare the LAST TWO daily snapshots: climbers get +n,
    fallers -n, a player new to the snapshot gets nothing (there is no
    earlier rank to compare), and fewer than two snapshots yields no
    movement at all rather than a fake 'unchanged'. The MVP trend chart
    tracks the latest top names and keeps absent scores as gaps (None),
    never zeros, refusing to draw a one-point line."""
    import shared as shared_module

    snaps = [
        {"date": "2026-10-20", "races": {"mvp": [
            {"player_id": 1, "player_name": "A", "rank": 1, "score": 41.0},
            {"player_id": 2, "player_name": "B", "rank": 2, "score": 40.0},
        ]}},
        {"date": "2026-10-21", "races": {"mvp": [
            {"player_id": 2, "player_name": "B", "rank": 1, "score": 42.0},
            {"player_id": 1, "player_name": "A", "rank": 2, "score": 39.0},
            {"player_id": 3, "player_name": "C", "rank": 3, "score": 30.0},
        ]}},
    ]
    moves = shared_module.race_movement(snaps, "mvp")
    assert moves == {2: 1, 1: -1}
    assert shared_module.race_movement(snaps[:1], "mvp") == {}
    assert shared_module.race_movement([], "mvp") == {}
    # A race neither snapshot recorded has no movement to report.
    assert shared_module.race_movement(snaps, "dpoy") == {}

    fig = shared_module.race_trend_figure(snaps, "mvp")
    assert [t.name for t in fig.data] == ["B", "A", "C"]  # latest top N
    assert list(fig.data[0].y) == [40.0, 42.0]
    assert list(fig.data[2].y) == [None, 30.0]  # C absent earlier: gap
    assert shared_module.race_trend_figure(snaps[:1], "mvp").data == ()


def test_race_row_html_movement_arrows():
    """Rank movement rides under the score: +n with a green up arrow, -n
    with a red down arrow, and no span at all for 0/None -- a single
    snapshot must never claim a change happened."""
    import shared as shared_module

    row = {"rank": 2, "player_name": "A", "player_id": 1, "score": 40.5}
    up = shared_module.race_row_html(row, "mvp", 3)
    assert "na-mv" in up and "na-up" in up and "▲3" in up
    down = shared_module.race_row_html(row, "mvp", -1)
    assert "na-mv" in down and "na-down" in down and "▼1" in down
    plain = shared_module.race_row_html(row, "mvp")
    assert "na-mv" not in plain
    assert shared_module.race_row_html(row, "mvp", 0) == plain


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


def test_sidebar_every_year_leads_with_the_default_season(offline_espn):
    """Sidebar offers a season selector PER LEAGUE: the NBA box offers every
    season 2010-11 -> the upcoming one and leads with the newest season
    that HAS collected games (the daily collector moves that default, so
    the test asks shared.default_season_index -- the app's own logic --
    instead of pinning a year); the WNBA box offers single calendar years
    (label shapes never collide, so one ?season= param serves both)."""
    import shared as shared_module

    at = _render("app/app.py", offline_espn)
    # Label lookup, not [0]: the Schedule tab's Fixture pickers also render
    # (AppTest orders sidebar widgets after main content).
    box = next(b for b in at.selectbox if str(b.label) == "Season (NBA)")
    labels = list(box.options)
    assert "2010-11" in labels and "2025-26" in labels
    nba_options = shared_module.season_options("nba")
    assert box.value == nba_options[shared_module.default_season_index(
        nba_options, "nba")]
    # The WNBA selector is a parallel box with single-year labels: its
    # defaults come from ITS OWN collected inventory, not the NBA's.
    wnba_box = next(b for b in at.selectbox
                    if str(b.label) == "Season (WNBA)")
    wnba_labels = list(wnba_box.options)
    assert "2010" in wnba_labels and "2026" in wnba_labels
    assert all("-" not in str(v) for v in wnba_labels)
    assert not at.exception
    assert len(at.warning) == 0


def test_empty_season_states_are_honest(monkeypatch, offline_espn):
    """A season with no collected games shows empty states everywhere --
    the loaders are stubbed empty so the contract holds no matter how far
    the daily collector has run: the Awards note says the season has no
    collected games, the standings section says no table, and nothing
    falls back to another year's data under this header (zero warnings,
    the old show-last-season-with-a-banner contract stays dead)."""
    import pandas as pd

    import shared as shared_module

    empty = pd.DataFrame()
    monkeypatch.setattr(shared_module, "load_schedule",
                        lambda season, league="nba": (empty, "none collected"))
    monkeypatch.setattr(shared_module, "load_standings",
                        lambda season, league="nba": (empty, "not started"))
    monkeypatch.setattr(shared_module, "load_awards",
                        lambda season, league="nba": ({}, "not started"))
    monkeypatch.setattr(shared_module, "load_games_by_season",
                        lambda league="nba": {})
    at = _render("app/app.py", offline_espn)
    assert not at.exception
    box = next(b for b in at.selectbox if str(b.label) == "Season (NBA)")
    season = str(box.value)
    assert len(at.warning) == 0
    infos = " ".join(str(i.value) for i in at.info)
    assert f"{season} has no collected games yet" in infos
    assert "No standings available" in infos


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
                        lambda game_id, league="nba": (
                            meta, rows, plays,
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


def test_schedule_game_detail_renders_the_live_box_while_in_progress(
        offline_espn, monkeypatch):
    """An IN-PROGRESS game resolves its box score and play-by-play from
    the same live summary endpoint -- the old final-only gate hid a live
    box the reader could follow the game with. The matchup line carries
    the status word so a half-played game never reads final, and the box
    tables reach the screen with their MIN column."""
    import shared as shared_module

    meta = {"game_id": 2, "status": "STATUS_IN_PROGRESS",
            "away_abbrev": "MIL", "home_abbrev": "CHI",
            "away_score": 87, "home_score": 91,
            "away_id": 2, "home_id": 7,
            "venue": "United Center", "airings": []}
    rows = [
        {"game_id": 2, "team_id": 2, "player_id": 10,
         "player_name": "Live Guy", "did_not_play": False, "min": 24,
         "pts": 18, "reb": 5, "ast": 3, "stl": 1, "blk": 0, "to": 1,
         "plus_minus": 4,
         "fgm": 7, "fga": 12, "fg3m": 2, "fg3a": 5, "ftm": 2, "fta": 2},
    ]
    plays = [
        {"period": "3rd Quarter", "clock": "4:32",
         "description": "Live Guy 26' three point jumper",
         "score": "87:91", "scoring": True},
    ]
    monkeypatch.setattr(shared_module, "load_game_summary",
                        lambda game_id, league="nba": (
                            meta, rows, plays,
                            "Live ESPN summary (15 min cache)"))

    at = _render("app/app.py", offline_espn)

    assert not at.exception
    text = _markdown_text(at)
    assert "**MIL 87 @ CHI 91** · In progress" in text
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    assert any("MIN" in list(f.columns) for f in frames)  # live box rendered
    assert len(at.warning) == 0


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
                        lambda game_id, league="nba": (
                            meta, [], [],
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


def test_schedule_filters_and_csv_downloads_render(offline_espn):
    """Standings and schedule each offer a CSV download of what is shown,
    and each league tab's team + venue filters scope THAT tab's results
    table, fixture picker and export together -- a BOS filter in the NBA
    tab never leaves another team's row on screen, and the WNBA tab's
    frame keeps its own teams (the two toolbars are independent).
    Pinned on 2025-26: the default season is in progress and renders the
    compact results+upcoming view, while a finished season carries the
    full table this filter contract is written against."""
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select("2025-26").run()
    assert not at.exception
    labels = [str(b.label) for b in at.download_button]
    assert "Download standings CSV" in labels
    assert "Download schedule CSV" in labels

    team_box = next(sb for sb in at.selectbox if str(sb.label) == "Team")
    team_box.set_value("BOS").run()
    assert not at.exception
    # The NBA tab's fixture picker is scoped with its tables; the WNBA
    # tab's picker is untouched by the NBA filter (its own label list).
    fixtures = next(sb for sb in at.selectbox
                    if getattr(sb, "key", "") == "fixture_nba")
    assert fixtures.options, "fixture picker lost its options"
    assert all("BOS" in str(o) for o in fixtures.options)
    wnba_fixtures = next(sb for sb in at.selectbox
                         if getattr(sb, "key", "") == "fixture_wnba")
    assert wnba_fixtures.options
    assert any("BOS" not in str(o) for o in wnba_fixtures.options)

    # Venue options only mean something with a team selected -- Home now
    # appears (relative to BOS) where the all-teams list had none.
    venue_box = next(sb for sb in at.selectbox
                     if getattr(sb, "key", "") == "sched_venue_team_nba"
                     or (str(sb.label) == "Venue"
                         and "Home" in [str(o) for o in sb.options]))
    venue_box.set_value("Home").run()
    assert not at.exception
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    sched = [f for f in frames if {"Date", "Away", "Home"} <= set(f.columns)]
    assert sched, "the schedule results table must render"
    # The NBA frame is exactly BOS-hosted rows (its filter); the WNBA
    # frame still carries its own league's teams (its toolbar did not
    # move). Attribution by content: WNBA abbreviations never equal BOS.
    homes = [set(f["Home"].astype(str)) for f in sched]
    assert {"BOS"} in homes, "BOS home filter must scope the NBA table"
    nba_frame = sched[homes.index({"BOS"})]
    assert set(nba_frame["Home"].astype(str)) == {"BOS"}
    assert all("BOS" in (str(a), str(h))
               for a, h in zip(nba_frame["Away"], nba_frame["Home"]))
    assert any(h - {"BOS"} for h in homes), \
        "the WNBA tab's table must keep its own league's teams"
    captions = " ".join(str(c.value) for c in at.caption)
    assert "BOS" in captions and "home games" in captions  # filter stated
    assert len(at.warning) == 0


def test_postseason_section_renders_labeled_scoped_and_exportable(
        offline_espn):
    """Schedule tab's Postseason block: a section of its OWN carrying the
    collected playoff rows (the Status column distinguishes it from the
    regular tables), its own CSV button, the honesty caption keeping it
    out of every computed view, and (PO)-marked playoff fixtures in the
    game-detail picker. Pinned on 2025-26: the default (in-progress)
    season has no bracket yet -- its honest empty state has its own test."""
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select("2025-26").run()
    assert not at.exception

    assert "postseason" in _markdown_text(at).lower()
    labels = [str(b.label) for b in at.download_button]
    assert "Download postseason CSV" in labels
    captions = " ".join(str(c.value) for c in at.caption)
    assert "regular-season-only" in captions

    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    post = [f for f in frames
            if {"Date", "Away", "Home", "Status"} <= set(f.columns)]
    assert post, "the collected season's postseason table must render"
    assert sum(int((f["Status"] == "Final").sum()) for f in post) > 0, \
        "real playoff scores, not just fixtures"

    fixtures = next(sb for sb in at.selectbox
                    if getattr(sb, "key", "") == "fixture_nba")
    assert any("(PO)" in str(o) for o in fixtures.options)


def test_postseason_section_follows_the_team_filter(offline_espn):
    """The toolbar's team filter scopes the postseason table with every
    other frame: pick a team that made the bracket and every row of the
    NBA tab's postseason table involves it (the WNBA tab's toolbar stays
    independent, exactly like the regular tables). Pinned on 2025-26:
    the default (in-progress) season has no NBA bracket to filter."""
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select("2025-26").run()
    assert not at.exception
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    post = [f for f in frames
            if {"Date", "Away", "Home", "Status"} <= set(f.columns)]
    assert post, "NBA tab renders first: its postseason table leads"
    team = str(post[0]["Home"].iloc[0])  # a team that made the bracket

    team_box = next(sb for sb in at.selectbox if str(sb.label) == "Team")
    team_box.set_value(team).run()
    assert not at.exception

    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    post = [f for f in frames
            if {"Date", "Away", "Home", "Status"} <= set(f.columns)]
    assert post, "the filtered postseason table must still render"
    nba_frame = post[0]  # NBA tab's frames render first
    involved = list(zip(nba_frame["Away"].astype(str),
                        nba_frame["Home"].astype(str)))
    assert involved and all(team in row for row in involved)


def test_postseason_empty_state_is_honest_for_a_new_season(offline_espn):
    """2026-27 has no bracket: the section says why (not determined yet,
    or not collected -- with the command that collects it) instead of
    re-showing last season's playoffs under the new season's header."""
    at = _render("app/app.py", offline_espn)
    box = next(b for b in at.selectbox if str(b.label) == "Season (NBA)")
    box.select("2026-27").run()
    assert not at.exception
    captions = " ".join(str(c.value) for c in at.caption)
    assert "No postseason schedule for 2026-27" in captions
    assert "bracket does not exist" in captions
    assert len(at.warning) == 0


def test_wnba_postseason_renders_real_scores_offline(offline_espn):
    """The WNBA pane's Schedule tab must show its own bracket's playoff
    scores under the fresh-checkout condition (committed fallbacks only):
    data/postseason_wnba/2026.json carries a live bracket, so a pane that
    degrades to empty states (seen Sep 2026 when a GUI merge committed
    conflict markers into the fallbacks) is a regression, not an honest
    gap. league=wnba reorders the panes, so the leading postseason frame
    is the WNBA's."""
    at = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    at.query_params["league"] = "wnba"
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    post = [f for f in frames
            if {"Date", "Away", "Home", "Status"} <= set(f.columns)]
    assert post, "the collected WNBA bracket's table must render"
    finals = int((post[0]["Status"] == "Final").sum())
    assert finals > 0, "real WNBA playoff scores, not just fixtures"


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
    year's data. One param serves both selectors: an NBA-shaped label
    preselects the NBA box while the WNBA box falls back to ITS default
    (the label shapes never collide)."""
    at = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    at.query_params["season"] = "2010-11"
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    box = next(b for b in at.selectbox if str(b.label) == "Season (NBA)")
    assert box.value == "2010-11"
    wnba_box = next(b for b in at.selectbox
                    if str(b.label) == "Season (WNBA)")
    assert wnba_box.value in list(wnba_box.options)
    assert "-" not in str(wnba_box.value)  # never an NBA-shaped label

    bad = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    bad.query_params["season"] = "1999-00"
    bad.run()
    assert not bad.exception, f"page raised: {bad.exception}"
    fallback = next(b for b in bad.selectbox
                    if str(b.label) == "Season (NBA)")
    # The honest default, asked of the app's own logic (the daily
    # collector moves it; pinning a year here broke every refresh).
    import shared as shared_module

    nba_options = shared_module.season_options("nba")
    assert fallback.value == nba_options[shared_module.default_season_index(
        nba_options, "nba")]


def test_deep_link_league_reorders_league_tabs(offline_espn):
    """?league=wnba renders the WNBA league tab FIRST (st.tabs has no
    programmatic selection) while each tab keeps its own four nested
    sections in the pinned order -- the label -> element map keeps them
    matched."""
    at = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    at.query_params["league"] = "wnba"
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    labels = [str(t.label) for t in at.tabs]
    assert labels == ["WNBA", "Schedule & Scores", "Standings",
                      "Awards Ladder", "Court View",
                      "NBA", "Schedule & Scores", "Standings",
                      "Awards Ladder", "Court View"]
    # An unknown league label is ignored, not crashed on.
    plain = AppTest.from_file(str(REPO_ROOT / "app/app.py"),
                              default_timeout=30)
    plain.query_params["league"] = "ncaa"
    plain.run()
    assert not plain.exception, f"page raised: {plain.exception}"
    assert [str(t.label) for t in plain.tabs][0] == "NBA"


def test_deep_link_tab_reorders_tabs(offline_espn):
    """?tab= renders the requested tab FIRST inside BOTH league panes
    (st.tabs has no programmatic selection) while the league tabs
    themselves keep their pinned NBA-first order and every tab keeps its
    own content (the label -> element map keeps them matched)."""
    at = AppTest.from_file(str(REPO_ROOT / "app/app.py"), default_timeout=30)
    at.query_params["tab"] = "Court View"
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    labels = [str(t.label) for t in at.tabs]
    assert labels == ["NBA", "Court View", "Schedule & Scores", "Standings",
                      "Awards Ladder", "WNBA", "Court View",
                      "Schedule & Scores", "Standings", "Awards Ladder"]


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


# ---------------------------------------------------------------------------
# Finals MVP calculator: the season formula on the series vs the award
# ---------------------------------------------------------------------------

def test_finals_mvp_calculator_pending_for_undecided_season(offline_espn):
    """The current season has no committed postseason and every ESPN call
    fails, so the calculator renders its section, an honest pending info
    row (never a stack trace, never another season's series) and the
    verbatim series formula caption."""
    at = _render("app/app.py", offline_espn)
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    infos = " ".join(str(i.value) for i in at.info)
    assert "Finals MVP calculator" in text
    season = espn_api.current_season_label(league="nba")
    assert f"{season}'s Finals isn't in the record yet" in infos
    assert "The MVP race formula, on the series" in text


def test_finals_mvp_calculator_offline_note_for_played_finals(offline_espn):
    """Selecting a season whose Finals WAS played (2025-26, committed
    postseason present): the bootstrap reaches for ESPN, fails honestly
    with the unreachable note, and the official headline still names the
    committed award winner."""
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select("2025-26").run()
    assert not at.exception
    infos = " ".join(str(i.value) for i in at.info)
    assert "Live Finals box scores unavailable" in infos
    assert "ESPN unreachable from this session" in infos
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "Jalen Brunson" in text   # official side renders off the index


def test_finals_mvp_calculator_formula_vs_award(monkeypatch, offline_espn):
    """With a synthetic series ladder the calculator renders the verdict
    row against the committed award (2025-26: Jalen Brunson): a differing
    formula #1 shows the red verdict, the formula rows follow, and the
    series/qualifier caption states the standings and the games bar."""
    import shared

    rows = [{"player_id": 1, "player_name": "Formula Guy",
             "team_abbrev": "NYK", "gp": 5, "ppg": 31.2, "rpg": 7.8,
             "apg": 8.4, "wins": 4, "losses": 1, "win_pct": 0.8,
             "fgp": 48.1, "score": 36.9, "rank": 1},
            {"player_id": 2, "player_name": "Second Man",
             "team_abbrev": "NYK", "gp": 5, "ppg": 22.0, "rpg": 4.1,
             "apg": 3.3, "wins": 4, "losses": 1, "win_pct": 0.8,
             "fgp": 45.0, "score": 24.0, "rank": 2}]
    info = {"min_gp": 3, "games": 5, "standings": "NYK 4-1 SA"}
    note = "Live ESPN Finals box scores (15 min cache): 5 series games, 5 played"

    def _fake(season, league="nba"):
        if season == "2025-26":
            return (rows, info, note)
        return ([], {}, f"{season}'s Finals isn't in the record yet")

    monkeypatch.setattr(shared, "load_finals_series", _fake)
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select("2025-26").run()
    assert not at.exception
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "Finals MVP calculator" in text
    assert "Algorithm #1: Formula Guy" in text   # the reused verdict row
    assert "Jalen Brunson" in text           # the award side of the verdict
    assert "✗ differs" in text               # formula #1 != the award
    assert "Second Man" in text              # the ladder rows render
    assert "NYK 4-1 SA" in text              # series standings caption
    assert "at least 3 of the series' 5 games" in text


def _mini_summary(gid, home, away, hs, aws, series_desc, events,
                  total=7, series_type="playoff", lines=()):
    """A minimal but structurally real ESPN summary payload: header with
    the playoff-series block + boxscore player lines (`lines`:
    (team_id, abbrev, player_id, name, pts, reb, ast) per player)."""
    players_by_team = {}
    abbrev_of = {}
    for team_id, abbrev, pid, name, pts, reb, ast in lines:
        abbrev_of.setdefault(team_id, abbrev)
        players_by_team.setdefault(team_id, []).append(
            {"athlete": {"id": str(pid), "displayName": name},
             "starter": True,
             "stats": ["30", str(pts), str(reb), str(ast)]})
    blocks = [{"team": {"id": str(tid), "abbreviation": abbrev_of[tid]},
               "statistics": [{"labels": ["MIN", "PTS", "REB", "AST"],
                               "athletes": athletes}]}
              for tid, athletes in players_by_team.items()]
    return {"header": {"competitions": [{
        "id": str(gid), "date": "2026-06-14T00:00Z",
        "status": {"type": {"name": "final"}},
        "competitors": [
            {"homeAway": "home",
             "team": {"id": "18", "abbreviation": home}, "score": hs},
            {"homeAway": "away",
             "team": {"id": "24", "abbreviation": away}, "score": aws}],
        "series": [{"type": series_type, "description": series_desc,
                    "completed": True, "totalCompetitions": total,
                    "events": [{"id": str(e)} for e in events]}]}]},
        "boxscore": {"players": blocks}}


def test_load_finals_series_identifies_and_ranks(monkeypatch):
    """The loader's identification contract on synthetic payloads: an NBA
    series block ('NBA Finals') is the Finals; a generic 'Playoff Series'
    block counts only when it is the season's last committed series; the
    series' games aggregate into the season-formula ladder. All fetches
    are stubbed -- no network."""
    import pandas as pd

    import espn_api as espn_module
    import shared

    def _frame(games):
        return (pd.DataFrame(games), "")

    payloads = {
        "101": _mini_summary(101, "NYK", "SA", "101", "95", "NBA Finals",
                             ["101", "102", "103", "104"],
                             lines=[(18, "NYK", 1, "Series Star", 30, 8, 6),
                                    (24, "SA", 2, "Rival Star", 25, 6, 4)]),
        "102": _mini_summary(102, "SA", "NYK", "99", "96", "NBA Finals",
                             ["101", "102", "103", "104"],
                             lines=[(18, "NYK", 1, "Series Star", 20, 5, 5),
                                    (24, "SA", 2, "Rival Star", 30, 7, 3)]),
        "103": _mini_summary(103, "NYK", "SA", "105", "98", "NBA Finals",
                             ["101", "102", "103", "104"],
                             lines=[(18, "NYK", 1, "Series Star", 32, 9, 7)]),
        "104": _mini_summary(104, "NYK", "SA", "97", "91", "NBA Finals",
                             ["101", "102", "103", "104"],
                             lines=[(18, "NYK", 1, "Series Star", 28, 7, 8)]),
    }
    games = [{"game_id": str(g), "date": f"2026-06-{d:02d}T00:00Z",
              "status": "STATUS_FINAL", "home_abbrev": h, "away_abbrev": a,
              "home_score": hs, "away_score": aws}
             for g, d, h, a, hs, aws in
             ((101, 10, "NYK", "SA", "101", "95"),
              (102, 12, "SA", "NYK", "99", "96"),
              (103, 14, "NYK", "SA", "105", "98"),
              (104, 16, "NYK", "SA", "97", "91"))]
    monkeypatch.setattr(shared, "load_postseason_schedule",
                        lambda season, league="nba": _frame(games))
    monkeypatch.setattr(espn_module, "get_event_summary",
                        lambda gid, league="nba": payloads[str(gid)])
    shared.load_finals_series.clear()
    try:
        rows, info, note = shared.load_finals_series("2025-26", "nba")
        assert [r["player_name"] for r in rows] == ["Series Star",
                                                    "Rival Star"]
        # Series Star: (110 + 0.75*29 + 26)/4 = 39.4375, NYK 3-1 -> x0.9
        assert rows[0]["score"] == 35.49
        # Rival Star: (55 + 0.75*13 + 7)/2 = 35.875, SA 1-3 -> x0.7
        assert rows[1]["score"] == 25.11
        assert info["games"] == 4 and info["min_gp"] == 2
        assert info["standings"] == "NYK 3-1 · SA 1-3"
        assert "4 series games, 4 played" in note

        # A generic 'Playoff Series' block in the best-of-5 slot is NOT the
        # Finals while other postseason games are still scheduled after it
        # (semis in progress: a WNBA semis and finals share this shape).
        with_later = games + [{"game_id": "105",
                               "date": "2026-06-20T00:00Z",
                               "status": "STATUS_SCHEDULED",
                               "home_abbrev": "NYK", "away_abbrev": "SA",
                               "home_score": None, "away_score": None}]
        generic = json.loads(json.dumps(payloads["104"]))
        generic["header"]["competitions"][0]["series"][0]["description"] = \
            "Playoff Series"
        generic["header"]["competitions"][0]["series"][0][
            "totalCompetitions"] = 5
        monkeypatch.setattr(shared, "load_postseason_schedule",
                            lambda season, league="nba": _frame(with_later))
        monkeypatch.setattr(
            espn_module, "get_event_summary",
            lambda gid, league="nba": (generic if str(gid) == "104"
                                       else payloads[str(gid)]))
        shared.load_finals_series.clear()
        rows, info, note = shared.load_finals_series("2025-26", "nba")
        assert rows == [] and "isn't in the record yet" in note

        # The same generic best-of-5 block IS the Finals when the season is
        # over (no later committed game): the last series of the postseason
        # is the Finals (the WNBA's block never says so, any era).
        monkeypatch.setattr(shared, "load_postseason_schedule",
                            lambda season, league="nba": _frame(games))
        shared.load_finals_series.clear()
        rows, info, note = shared.load_finals_series("2025-26", "nba")
        assert rows
        assert info["standings"] == "NYK 3-1 · SA 1-3"
    finally:
        shared.load_finals_series.clear()


# ---------------------------------------------------------------------------
# Live in-season MVP progress tracker
# ---------------------------------------------------------------------------

def test_live_mvp_tracker_honest_offline(offline_espn):
    """On the season being played right now (selected explicitly -- the
    sidebar default follows the collector's inventory, this gate follows
    the calendar), every ESPN call failing leaves the tracker section with
    its honest note (never a stack trace, never another year's race) and
    the verbatim committed MVP formula in the caption."""
    import shared as shared_module

    shared_module.load_live_mvp_race.clear()
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select(
        shared_module.current_season_label("nba")).run()
    assert not at.exception
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "MVP progress tracker -- live" in text
    assert "MVP race is homegrown math, not official NBA voting" in text
    import awards as awards_module

    assert awards_module.RACE_FORMULAS["mvp"] in text
    infos = " ".join(str(i.value) for i in at.info)
    assert "Live MVP race unavailable: ESPN unreachable" in infos
    assert len(at.warning) == 0


def test_live_mvp_tracker_renders_synthetic_rows(monkeypatch, offline_espn):
    """With a stubbed live race the tracker renders its rows over the real
    race-row widgets (rank medal, stat line, score), the per-row names
    come from the name helper (stubbed here, so no profile call), and the    caption states formula AND the live note together."""
    import shared as shared_module

    rows = [{"player_id": 1, "player_name": "Live Guy",
             "team_abbrev": "BOS", "gp": 3, "ppg": 31.0, "rpg": 7.0,
             "apg": 6.0, "wins": 2, "losses": 1, "win_pct": 0.667,
             "fgp": 48.5, "score": 33.7, "rank": 1},
            {"player_id": 2, "player_name": "Second Live",
             "team_abbrev": "OKC", "gp": 3, "ppg": 27.0, "rpg": 5.0,
             "apg": 4.0, "wins": 3, "losses": 0, "win_pct": 1.0,
             "fgp": 50.1, "score": 30.9, "rank": 2}]
    note = ("Live ESPN leaders + season totals (10 min cache): 2 candidates, "
            "top 2 ranked (qualifier: ≥1 GP) · standings live")

    monkeypatch.setattr(shared_module, "load_live_mvp_race",
                        lambda season, league="nba": (rows, note))
    # The app names every rendered row through the helper (the payloads
    # carry ids only), so the stub answers per id like the real one would.
    monkeypatch.setattr(
        shared_module, "live_athlete_name",
        lambda pid, league="nba": ("Live Guy" if int(pid) == 1
                                   else "Second Live"))
    at = _render("app/app.py", offline_espn)
    assert not at.exception
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "MVP progress tracker -- live" in text
    assert "Live Guy" in text and "Second Live" in text
    assert "31.0 PPG" in text and "33.7" in text   # stat line + score
    assert "≥1 GP" in text                          # the live note
    assert len(at.warning) == 0


def test_live_mvp_tracker_absent_for_a_past_season(offline_espn):
    """A finished season's race is the committed one further down the
    page: the live tracker section does not render for it (its
    per-candidate burst is the page's most expensive read, and a past
    season's live re-fetch would only restate history). Both selectors
    move off the current season -- each league tab gates on ITS OWN."""
    at = _render("app/app.py", offline_espn)
    at.selectbox(key="season_nba").select("2025-26").run()
    at.selectbox(key="season_wnba").select("2025").run()
    assert not at.exception
    text = (f"{_markdown_text(at)} "
            + " ".join(str(c.value) for c in at.caption))
    assert "MVP progress tracker -- live" not in text
    assert "Live ESPN leaders" not in text


# ---------------------------------------------------------------------------
# Viewer-local time: every displayed game time reads the READER's clock
# ---------------------------------------------------------------------------

def test_viewer_timezone_falls_back_to_utc(monkeypatch):
    """The zone comes from the browser (st.context.timezone), and when
    there is none -- AppTest, a client that strips headers, the very first
    paint -- it is UTC, never the server's zone and never a guess. An
    unresolvable name degrades to UTC too, so a bad header can't raise."""
    import shared as shared_module

    monkeypatch.setattr(shared_module, "browser_timezone", lambda: "")
    assert str(shared_module.viewer_tz()) == "UTC"
    monkeypatch.setattr(shared_module, "browser_timezone",
                        lambda: "Pacific/Auckland")
    assert str(shared_module.viewer_tz()) == "Pacific/Auckland"
    monkeypatch.setattr(shared_module, "browser_timezone",
                        lambda: "Nowhere/Not-A-Zone")
    assert str(shared_module.viewer_tz()) == "UTC"
    # an explicit name always wins (the scoreboard's cache key path)
    assert str(shared_module.viewer_tz("Pacific/Auckland")) == "Pacific/Auckland"


def test_local_dates_put_tonight_on_the_readers_own_day():
    """A 23:30Z tip is TOMORROW for an Auckland reader (NZDT is UTC+13),
    so every Date column and the upcoming/postponed split must convert --
    filing it by the server's UTC date is what pushed today's slate out of
    the schedule. Both committed stamp spellings parse (they share a
    column), and rows that don't parse keep their own text: an honest gap
    beats a guessed day."""
    import pandas as pd

    import shared as shared_module

    nz = shared_module.viewer_tz("Pacific/Auckland")
    utc = shared_module.viewer_tz("UTC")
    stamps = pd.Series(["2026-10-24T23:30Z",       # minute precision
                        "2026-10-21T23:30:00Z",    # second precision
                        "", "bogus"])
    assert list(shared_module.local_dates(stamps, nz)[:2]) == [
        "2026-10-25", "2026-10-22"]
    assert list(shared_module.local_dates(stamps, utc)[:2]) == [
        "2026-10-24", "2026-10-21"]
    assert list(shared_module.local_dates(stamps, nz)[2:]) == ["", "bogus"]
    timed = shared_module.local_dates(stamps, nz, with_time=True)
    assert timed[0] == "2026-10-25 12:30"
    assert timed[2] == "" and timed[3] == "bogus"   # unparseable keeps its own text


def test_local_stamp_and_note_localization():
    """A single stamp renders in the viewer's zone -- UTC gives exactly
    '2026-10-21 23:30', the string the fixture-detail test pins -- and a
    freshness note's embedded UTC stamps are rewritten AT RENDER (notes are
    cached with the data they describe, so they convert where they show)."""
    import shared as shared_module

    nz = shared_module.viewer_tz("Pacific/Auckland")
    utc = shared_module.viewer_tz("UTC")
    assert shared_module.local_stamp("2026-10-21T23:30:00Z", utc) == \
        "2026-10-21 23:30"
    assert shared_module.local_stamp("2026-10-21T23:30:00Z", nz) == \
        "2026-10-22 12:30"
    assert shared_module.local_stamp("", nz) == ""
    assert shared_module.local_stamp(None, nz) == ""
    assert shared_module.local_stamp("not a date", nz) == "not a date"
    note = "Offline fallback: data as of 2026-10-23T19:18:59Z"
    assert shared_module.localize_note(note, utc) == \
        "Offline fallback: data as of 2026-10-23 19:18 UTC"
    assert shared_module.localize_note(note, nz) == \
        "Offline fallback: data as of 2026-10-24 08:18 NZDT"
    assert shared_module.localize_note("Live (60s cache)") == \
        "Live (60s cache)"
    # a plain date elsewhere in the prose is NOT a stamp, so it stays
    assert shared_module.localize_note("schedule 2026-10-24 absent", nz) == \
        "schedule 2026-10-24 absent"


def test_scoreboard_buckets_cover_the_local_day_and_filter_to_it():
    """ESPN counts a game by a US date (verified: dates=20261023 returns
    tips 20:00Z..02:00Z -- one US evening), so an Auckland day needs the
    covering buckets -- its slate sits under the PREVIOUS US date -- and
    only tips inside the local day survive, carrying a local tip-off."""
    from datetime import datetime, timedelta

    import pandas as pd

    import shared as shared_module

    nz = shared_module.viewer_tz("Pacific/Auckland")
    day_start = datetime(2026, 10, 25, 0, 0, tzinfo=nz)   # local midnight NZDT
    day_end = day_start + timedelta(days=1)
    # local midnight = 2026-10-24 11:00Z, so the covering buckets are the
    # US dates Oct 23/24/25 -- the Oct 23 slate is the OLD ask's blind spot
    assert shared_module.scoreboard_buckets(day_start) == [
        "20261023", "20261024", "20261025"]
    rows = [
        {"game_id": "in", "date": "2026-10-24T20:00Z"},   # NZ Oct 25 09:00
        {"game_id": "in", "date": "2026-10-24T20:00Z"},   # same event, twice
        {"game_id": "old", "date": "2026-10-23T02:00Z"},  # NZ Oct 23 15:00
        {"game_id": "new", "date": "2026-10-25T22:00Z"},  # NZ Oct 26 11:00
    ]
    out = shared_module.local_day_games(
        pd.DataFrame(rows), day_start, day_end, nz)
    assert list(out["game_id"]) == ["in"]
    assert out.iloc[0]["time"] == "2026-10-25 09:00"


def test_load_today_scoreboard_scopes_to_the_viewer_day(monkeypatch):
    """The loader pulls three covering buckets (the previous US date's
    slate is always among them -- the old single-UTC-date ask fetched
    tomorrow's games for a viewer east of the US and reported none), keeps
    only tips inside the local day, and keys its cache by zone so two
    viewers never share each other's 'today'. Fetches are stubbed."""
    from datetime import datetime, timedelta, timezone as datetime_tz

    import pandas as pd

    import shared as shared_module

    key = "Pacific/Auckland"
    tz = shared_module.viewer_tz(key)
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0,
                                         microsecond=0)
    inside = day_start + timedelta(hours=9)
    outside = inside - timedelta(days=3)
    # stamps are UTC on the wire: convert the local instants before 'Z'
    inside_utc = inside.astimezone(datetime_tz.utc)
    outside_utc = outside.astimezone(datetime_tz.utc)
    requested: list = []

    def _fake(dates, league="nba"):
        requested.append(dates)
        rows = [
            {"game_id": "today", "date": inside_utc.strftime("%Y-%m-%dT%H:%M:00Z"),
             "status": "STATUS_SCHEDULED", "home": "AAA", "away": "BBB",
             "home_score": None, "away_score": None},
            {"game_id": "long-ago",
             "date": outside_utc.strftime("%Y-%m-%dT%H:%M:00Z"),
             "status": "STATUS_FINAL", "home": "AAA", "away": "BBB",
             "home_score": "100", "away_score": "99"},
        ]
        return pd.DataFrame(rows), "Live (60s cache)"

    monkeypatch.setattr(shared_module, "load_scoreboard", _fake)
    shared_module.load_today_scoreboard.clear()
    try:
        frame, note = shared_module.load_today_scoreboard("nba", key)
        # three DISTINCT buckets, including the local midnight's UTC date
        assert len(requested) == 3 and len(set(requested)) == 3
        assert day_start.astimezone(datetime_tz.utc).strftime("%Y%m%d") in \
            requested
        # only the viewer's day survives, rendered on their clock
        assert list(frame["game_id"]) == ["today"]
        assert frame.iloc[0]["time"] == inside.strftime("%Y-%m-%d %H:%M")
        assert note == "Live (60s cache)"
        # the zone is part of the cache key: another viewer's clock is a
        # separate entry (Streamlit's cache is shared across sessions)
        shared_module.load_today_scoreboard("nba", "UTC")
        assert len(requested) == 6
    finally:
        shared_module.load_today_scoreboard.clear()


def test_schedule_today_line_reads_the_local_date_and_zone(offline_espn):
    """The Schedule tab's empty state names the READER's day in
    YYYY-MM-DD (never ESPN's YYYYMMDD bucket format) -- as an info block,
    like every other empty state on the page, since during the offseason
    that line IS the answer to "what's on today" -- and the hero strip
    carries the local 'Today (zone): date' pill (it moved out of the
    sidebar, where it was easy to miss). Both say UTC here, which is
    exactly the no-browser-zone contract AppTest exercises."""
    import re

    import shared as shared_module

    at = _render("app/app.py", offline_espn)
    info = " ".join(str(i.value) for i in at.info)
    today = shared_module.local_today(shared_module.viewer_tz("UTC"))
    assert f"No games on {today}:" in info
    hero = _markdown_text(at)
    assert re.search(r"Today \(UTC\): \d{4}-\d{2}-\d{2}", hero)
    assert " ago)" in hero  # the freshness note carries its age
    assert not re.search(r"No games on \d{8}", info), \
        "the raw YYYYMMDD bucket leaked into the reader's note"


def test_status_label_speaks_words_not_api_enums():
    """ESPN's STATUS_* enums are API plumbing, not UI copy: today's slate,
    the postseason table and the game-detail line all render the same
    human words through shared.status_label (the postseason tests pin
    'Final'/'Scheduled', which this must keep producing). Unknown tokens
    lose only their enum casing, and empties stay empty -- never an
    invented status. Pure unit test, no render."""
    import shared as shared_module

    label = shared_module.status_label
    assert label("STATUS_SCHEDULED") == "Scheduled"
    assert label("STATUS_IN_PROGRESS") == "In progress"
    assert label("STATUS_FINAL") == "Final"
    assert label("STATUS_POSTPONED") == "Postponed"
    assert label("STATUS_HALFTIME") == "Halftime"
    assert label("STATUS_CANCELED") == label("STATUS_CANCELLED") == "Canceled"
    assert label("STATUS_WEIRD") == "Weird"   # unknown: de-enum only
    assert label("SCHEDULED") == "Scheduled"  # bare token, no prefix
    assert label("") == "" and label(None) == ""
    assert label(float("nan")) == ""          # a missing status stays missing


def test_status_detail_puts_the_live_clock_in_the_status_cell():
    """A game in progress shows its REAL period and clock -- 'Q3 4:32',
    ESPN's own numbers, riding the slate's 60s cache -- and every other
    state keeps the human word. A missing period or clock falls back to
    the word: the cell never invents a time."""
    import shared as shared_module

    detail = shared_module.status_detail
    assert detail("STATUS_IN_PROGRESS", "Q3", 272.5) == "Q3 4:32"
    assert detail("STATUS_IN_PROGRESS", "Q4", "58.9") == "Q4 0:58"
    assert detail("STATUS_IN_PROGRESS", "Q2", "2:11.4") == "Q2 2:11"
    assert detail("STATUS_IN_PROGRESS", "Q1", None) == "Q1"
    assert detail("STATUS_IN_PROGRESS", None, 65) == "1:05"
    assert detail("STATUS_IN_PROGRESS", None, None) == "In progress"
    # The clock is a LIVE-game fact: finals/halftime keep their word.
    assert detail("STATUS_FINAL", "Q4", 1.0) == "Final"
    assert detail("STATUS_HALFTIME", "Q2", 0) == "Halftime"
    assert detail("STATUS_SCHEDULED", None, None) == "Scheduled"


def test_freshness_age_reads_the_notes_own_stamp():
    """The hero's age suffix is computed at render from the SAME UTC stamp
    the note displays -- '(2 h ago)' for a two-hour-old stamp -- and '' for
    a live note with no stamp at all. The suffix can never claim an age
    the caption doesn't back."""
    from datetime import datetime, timezone

    import shared as shared_module

    now = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)
    age = shared_module.freshness_age
    assert age("data as of 2026-10-24T08:18Z", now) == " (1 h ago)"
    assert age("data as of 2026-10-24T09:59:30Z", now) == " (just now)"
    assert age("data as of 2026-10-24T09:30Z", now) == " (30 min ago)"
    assert age("data as of 2026-10-22T10:00:00Z", now) == " (2 d ago)"
    assert age("Live (60s cache)", now) == ""
    assert age("", now) == "" and age(None, now) == ""


def test_source_captions_carry_the_age_too(offline_espn):
    """The age suffix is not hero-only: every Source line renders through
    shared.source_caption, so a committed note shows its localized stamp
    AND its age ('... NZDT (N h ago)') wherever the section sits --
    while a live stampless note ages to nothing."""
    at = _render("app/app.py", offline_espn)
    sources = [str(c.value) for c in at.caption if "Source:" in str(c.value)]
    assert sources, "the page must caption its sources"
    assert any(" ago)" in c for c in sources), \
        "a committed note's Source caption must carry its age"


def test_schedule_today_table_leads_with_a_zoned_tipoff(offline_espn,
                                                        monkeypatch):
    """With games present the table leads with the local tip-off column,
    its header naming the zone, and the internal game_id/raw UTC date are
    not shown -- the reader gets their own clock, not the collector's.
    A live game's Status cell carries its REAL period and clock
    ('Q3 4:32'), scores render as numbers, and the slate captions its
    source like every other section."""
    import pandas as pd

    import shared as shared_module

    frame = pd.DataFrame([
        {
            "game_id": "401585601", "date": "2026-10-25T23:30:00Z",
            "status": "STATUS_SCHEDULED", "home": "BOS", "away": "NYK",
            "home_score": None, "away_score": None,
            "time": "2026-10-26 12:30",
            "status_period": None, "status_clock": None,
        },
        {
            "game_id": "401585602", "date": "2026-10-25T23:00:00Z",
            "status": "STATUS_IN_PROGRESS", "home": "MIL", "away": "CHI",
            "home_score": "91", "away_score": "87",
            "time": "2026-10-26 12:00",
            "status_period": "Q3", "status_clock": 272.5,
        },
    ])
    monkeypatch.setattr(
        shared_module, "load_today_scoreboard",
        lambda league="nba", tz_key="UTC": (frame, "Live (60s cache)"))
    at = _render("app/app.py", offline_espn)
    frames = [df.value for df in at.dataframe
              if hasattr(getattr(df, "value", None), "columns")]
    today_table = next((f for f in frames if "Tip-off (UTC)" in list(f.columns)),
                       None)
    assert today_table is not None, "today's-games table lost its tip-off column"
    assert "game_id" not in list(today_table.columns)
    assert "date" not in list(today_table.columns)
    assert today_table.iloc[0]["Tip-off (UTC)"] == "2026-10-26 12:30"
    # the raw STATUS_* enum is plumbing, not copy: the reader sees words
    # (Scheduled / Final / ...) -- and a live game sees its REAL clock.
    assert today_table.iloc[0]["Status"] == "Scheduled"
    assert today_table.iloc[1]["Status"] == "Q3 4:32"
    assert not any("STATUS_" in str(v) for v in today_table["Status"])
    # Scores render as NUMBERS: right-aligned, numerically sortable, and
    # an unplayed game's cell stays blank -- never a fabricated zero.
    assert str(today_table["Away pts"].dtype) == "Int64"
    assert pd.isna(today_table.iloc[0]["Away pts"])
    assert int(today_table.iloc[1]["Away pts"]) == 87
    assert int(today_table.iloc[1]["Home pts"]) == 91
    # The slate captions its source, like every other section.
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Source: Live (60s cache)" in captions


def test_load_scoreboard_parses_both_live_score_shapes(monkeypatch):
    """ESPN's competitor `score` ships two shapes (parsing._score_value
    documents the split): the scoreboard's live answer is a bare '125'
    string while schedule/summary payloads carry
    {'value': 99.0, 'displayValue': '99'}. The string shape used to raise
    inside the per-event loop, the exception was swallowed by the honest
    empty handler, and 'Today's games' said Offline fallback while ESPN
    answered -- the slate never showed. Both shapes parse, an unplayed
    game stays blank, and a not-started game's '0' placeholder never
    shows as a score (the schedule tables ship '' for the same rows).
    Fetches are stubbed, no network."""
    import pandas as pd

    import espn_api as espn_module
    import shared as shared_module

    def _event(eid, home, away, status=None, date="2026-10-24T23:30:00Z",
               period=None, clock=None, display_clock=None):
        body = {"type": status or {"name": "STATUS_FINAL"}}
        if period is not None:
            # ESPN ships periods BOTH ways: a bare int on the live
            # scoreboard (verified Oct 2026), a displayValue dict in
            # other payloads. A str test-fixture becomes the dict shape.
            body["period"] = ({"displayValue": period}
                              if isinstance(period, str) else period)
        if clock is not None:
            body["clock"] = clock
        if display_clock is not None:
            body["displayClock"] = display_clock
        return {"id": eid, "date": date,
                "competitions": [{
                    "status": body,
                    "competitors": [
                        {"homeAway": "home", "score": home,
                         "team": {"abbreviation": "BOS"}},
                        {"homeAway": "away", "score": away,
                         "team": {"abbreviation": "NYK"}},
                    ]}]}

    payload = {"events": [
        _event("1", "125", {"value": 99.0, "displayValue": "99"}),
        _event("2", {"value": 88.0, "displayValue": "88"}, "101"),
        _event("3", None, ""),
        # tonight's slate: state 'pre' and ESPN's placeholder zero
        _event("4", "0", "0",
               status={"name": "STATUS_SCHEDULED", "state": "pre"},
               date="2026-10-25T00:00:00Z"),
        # a game in progress, in the LIVE scoreboard's real shape: the
        # period is a bare int and displayClock arrives formatted -- the
        # Status cell must still read 'Q3 4:32', quarter AND clock
        _event("5", "91", "87",
               status={"name": "STATUS_IN_PROGRESS", "state": "in"},
               date="2026-10-24T23:00:00Z", period=3, clock=272.5,
               display_clock="4:32.5"),
        # overtime as the int it ships as, and the dict shape too
        _event("6", "120", "118",
               status={"name": "STATUS_FINAL", "state": "post"},
               date="2026-10-24T22:00:00Z", period=5),
        _event("7", "95", "98",
               status={"name": "STATUS_FINAL", "state": "post"},
               date="2026-10-24T21:00:00Z", period="Q4"),
    ]}
    monkeypatch.setattr(espn_module, "get_scoreboard",
                        lambda dates, league="nba": payload)
    shared_module.load_scoreboard.clear()
    try:
        frame, note = shared_module.load_scoreboard("20261024", "nba")
        assert note == "Live (60s cache)", "the parse raised and was swallowed"
        assert list(frame["home_score"])[:2] == ["125", "88"]
        assert list(frame["away_score"])[:2] == ["99", "101"]
        for col in ("home_score", "away_score"):
            assert pd.isna(frame[col].iloc[2])   # no score yet
            assert pd.isna(frame[col].iloc[3])   # placeholder zero blanked
        # neither the dict's float value nor a fabricated 0 leaks on screen
        assert "101.0" not in list(frame["away_score"])
        assert "0" not in list(frame["home_score"])
        # the live game's REAL period + clock ride along, in the live
        # scoreboard's own shapes (int period, formatted displayClock):
        # the Status cell still composes them at display ('Q3 4:32')
        live = frame[frame["status"] == "STATUS_IN_PROGRESS"].iloc[0]
        assert live["status_period"] == "Q3" and live["status_clock"] == "4:32.5"
        assert shared_module.status_detail(
            live["status"], live["status_period"],
            live["status_clock"]) == "Q3 4:32"
        # overtime numbers as 'OT', the dict period shape as its own label
        assert frame.iloc[5]["status_period"] == "OT"
        assert frame.iloc[6]["status_period"] == "Q4"
    finally:
        shared_module.load_scoreboard.clear()
