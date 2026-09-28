"""Offline test for the all-NBA-history pipeline (history.py).

build(live=False) runs entirely from the committed cache in
data/processed/history_cache.json: award listings and athlete bundles come
from disk, and _save_cache is patched to a no-op so the test never
rewrites the committed file (build saves twice). This pins the payload the
GOAT ladder and Player Profile consume, with no network.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "collector"))

import history  # noqa: E402


def test_build_offline_from_committed_cache(monkeypatch):
    """The all-history build assembles real career entries from the
    committed cache: LeBron's full career (>=20 seasons, an ESPN career
    line, per-season rows for the progression chart) and a non-empty
    official-honours map, with no network and no cache rewrite."""
    if not (REPO_ROOT / "data" / "processed" / "history_cache.json").exists():
        pytest.skip("history_cache.json not committed yet")
    monkeypatch.setattr(history, "_save_cache", lambda cache: None)
    result = history.build(window_players=[], cache=history._load_cache(),
                           live=False)
    players = result["players"]
    assert players, "no career entries assembled from the cache"
    by_pid = {p.get("player_id"): p for p in players}
    lebron = by_pid.get(1966)
    assert lebron is not None, "LeBron missing from the all-history pool"
    assert (lebron.get("seasons") or 0) >= 20
    assert lebron.get("line_source") == "espn"
    assert lebron.get("seasons_log"), "per-season rows feed the chart"
    assert result["honours"], "official honours map is empty"
    assert result["meta"]["stamp"]
    assert result["meta"]["pool"] >= len(result["honours"])
    # Official-record ring counts land on careers ESPN's champion index
    # can't vouch for, and meta counts them for the on-screen caption.
    assert by_pid[4145]["championships"] == 6   # Kareem (rows start 1976)
    assert by_pid[4152]["championships"] == 11  # Bill Russell (pre-1970)
    assert result["meta"]["official_champions"] >= 2
    # Official-record rebound overlay: ESPN's broken reb 0 replaced in the
    # career line, and on the season rows so the peak and the profile
    # progression chart read real numbers (Russell's rows carried 0.0).
    assert by_pid[4142]["reb"] == 23924         # Wilt's career rebounds
    assert result["meta"]["official_reb_lines"] >= 10
    log = by_pid[4152].get("seasons_log") or []
    assert log and any(entry["reb"] for entry in log), "reb overlay missed"
    assert (by_pid[4152].get("peak_impact") or 0) > 40  # was 23.4 at 0 reb


def test_line_keys_exclude_window_only_plus_minus():
    """ESPN's career statistics carry no +/- split (verified Sep 2026):
    _LINE_KEYS must exclude it or _parse_line would fabricate a 0 on every
    ESPN line, and build_career's history-over-window merge would clobber
    the real collected-window total with that zero."""
    import awards

    assert "plus_minus" in awards.CAREER_SUM_STATS   # summed from box scores
    assert "plus_minus" not in history._LINE_KEYS    # never from ESPN lines
    line = history._parse_line({"splits": {"categories": [{"stats": [
        {"name": "gamesPlayed", "value": 82},
        {"name": "points", "value": 1000}]}]}})
    assert line is not None
    assert line["gp"] == 82 and line["pts"] == 1000
    assert "plus_minus" not in line


def test_official_championships_table():
    """OFFICIAL_CHAMPIONSHIPS answers before any derivation runs: the ids
    it lists return the verified official-record count even with an empty
    entry, ids it doesn't list keep the old None behaviour, and the table
    only ever holds non-negative int counts keyed by int id."""
    assert history._championships({}, {}, 4145) == 6
    assert history._championships({"rows": [], "debut": 1969}, {},
                                  4152) == 11
    assert history._championships({}, {}, 9999999) is None
    for pid, count in history.OFFICIAL_CHAMPIONSHIPS.items():
        assert isinstance(pid, int)
        assert isinstance(count, int) and count >= 0
