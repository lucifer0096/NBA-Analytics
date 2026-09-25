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
