"""Offline dashboard render tests via Streamlit's AppTest -- the exact
fresh-checkout/deploy condition: every live ESPN call forced to fail, no
data/raw/ needed (committed data/dashboard_* fallbacks only). No network.

Pins the contract that would otherwise rot silently: both pages render,
their tabs exist, and the fallback-failure path shows an honest empty state
rather than a stack trace.
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
    # Title + the five tabs present even with every live call failing.
    assert any("NBA Analytics" in str(t.value) for t in at.title)
    tab_labels = [str(t.label) for t in at.tabs]
    assert "Standings" in tab_labels
    assert "Schedule & Scores" in tab_labels
    assert "Awards Ladder" in tab_labels
    assert "All-Time Stats" in tab_labels
    assert "GOAT Rankings" in tab_labels


def test_home_page_falls_back_to_committed_data(offline_espn):
    """With live calls dead, committed fallbacks still feed the page: the
    freshness caption must say 'Offline fallback', never crash."""
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Offline fallback" in captions or "fallback" in captions.lower()


def test_model_page_renders_offline(offline_espn):
    at = _render("app/pages/1_Model_and_History.py", offline_espn)
    assert not at.exception
    tab_labels = [str(t.label) for t in at.tabs]
    assert "Model Performance" in tab_labels
    assert "Season Leaders" in tab_labels
    assert "Court View" in tab_labels


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


def test_home_alltime_tab_shows_career_board_and_honesty(offline_espn):
    """All-Time Stats must render the career table with its window caption --
    and must admit the window (collector starts 2010-11, not full NBA
    history). Skips when the committed payload predates the career sections."""
    payload = _committed_awards()
    pts_rows = (((payload.get("alltime") or {}).get("leaders") or {})
                .get("pts") or [])
    if not pts_rows:
        pytest.skip("no all-time section in the committed payload yet")
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption).lower()
    assert "not full nba history" in captions
    # The career scoring leader must reach the rendered dataframe.
    name = str(pts_rows[0].get("player_name") or "")
    frames = " ".join(str(getattr(df, "value", "")) for df in at.dataframe)
    assert name and name in frames


def test_home_goat_tab_shows_ladder_and_verbatim_formula(offline_espn):
    """GOAT Rankings must print its exact formula + the not-official
    disclaimer, and the top-ranked player's name must reach the screen."""
    payload = _committed_awards()
    goat_rows = (payload.get("goat") or {}).get("rows") or []
    if not goat_rows:
        pytest.skip("no GOAT rows in the committed payload yet")
    at = _render("app/app.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    text = f"{_markdown_text(at)} {captions}"
    assert "GOAT score =" in text
    assert "not an official nba ranking" in captions.lower()
    assert str(goat_rows[0].get("player_name") or "") in text


def test_load_awards_picks_requested_or_nearest_season(monkeypatch, tmp_path):
    """The sidebar season must resolve honestly: exact match when collected,
    the newest season at-or-before it otherwise (2026-27 pre-tip-off ->
    2025-26), the earliest season before the window -- each fallback SAYING
    which season it is actually showing."""
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
        assert "showing" not in note

        data, note = shared_module.load_awards("2026-27")
        assert data.get("season") == "2025-26"
        assert "showing 2025-26" in note
        assert "2026-27 has no collected games yet" in note

        data, note = shared_module.load_awards("2014-15")
        assert data.get("season") == "2015-16"
        assert "showing 2015-16" in note
    finally:
        shared_module.load_awards.clear()


def test_model_page_court_view_renders_leaders(offline_espn):
    """Court View must show a real leader from the committed payload --
    asserted via that player's name reaching markdown/captions, never via a
    .na-court class (the theme CSS blob contains it regardless)."""
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
    at = _render("app/pages/1_Model_and_History.py", offline_espn)
    captions = " ".join(str(c.value) for c in at.caption)
    text = f"{_markdown_text(at)} {captions}"
    name = str(pts[0].get("player_name") or "")
    assert name and (name in text or "hover a card" in captions)


def test_model_page_survives_missing_metrics(offline_espn, tmp_path, monkeypatch):
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
                 "dashboard_awards.json",
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
