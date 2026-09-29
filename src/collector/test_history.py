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


def test_set_league_rebinds_endpoints_and_cache(monkeypatch):
    """set_league() switches the whole career pipeline together: endpoint
    slug, ESPN award-type ids (15 WNBA honours incl. Finals MVP 257), the
    1997 champion-index start and the per-league cache file -- then the NBA
    configuration survives a round trip so runs can't mix honours tables."""
    saved = {name: getattr(history, name) for name in
             ("LEAGUE", "BASE", "WEB_STATS_URL", "CACHE_PATH",
              "AWARD_TYPE_IDS", "FINALS_MVP_ID", "CHAMPION_FIRST_YEAR")}
    try:
        history.set_league("wnba")
        assert history.LEAGUE == "wnba"
        assert history.BASE.endswith("/leagues/wnba")
        assert "/wnba" in history.WEB_STATS_URL
        assert history.CACHE_PATH.endswith("history_cache_wnba.json")
        assert history.FINALS_MVP_ID == 257
        assert history.CHAMPION_FIRST_YEAR == 1997
        assert len(history.AWARD_TYPE_IDS) == 15
        assert 257 in history.AWARD_TYPE_IDS
        assert 43 not in history.AWARD_TYPE_IDS  # NBA Finals MVP
        with pytest.raises(ValueError):
            history.set_league("euroleague")
    finally:
        for name, value in saved.items():
            setattr(history, name, value)
    assert history.LEAGUE == "nba"
    assert history.FINALS_MVP_ID == 43
    assert history.CACHE_PATH.endswith("history_cache.json")


def test_honours_wnba_verified_champion_overlay_fills_espn_gaps(monkeypatch):
    """ESPN's WNBA Finals-MVP detail omits the winner's team ref for
    1997-2002 (verified live Sep 2026): the verified six-season overlay
    fills ONLY those gaps, and ESPN's own team refs still win where they
    exist -- so the champion index covers every season from 1997."""
    saved = {name: getattr(history, name) for name in
             ("LEAGUE", "BASE", "WEB_STATS_URL", "CACHE_PATH",
              "AWARD_TYPE_IDS", "FINALS_MVP_ID", "CHAMPION_FIRST_YEAR")}
    try:
        history.set_league("wnba")
        base = ("https://sports.core.api.espn.com/v2/sports/basketball/"
                "leagues/wnba/seasons/{}/awards/257?lang=en&region=us")
        cache = {
            "listings": {"257": [base.format(1997), base.format(2005)]},
            "awards": {
                # ESPN gap year: winner carries no team ref.
                "257-1997": {"name": "Finals MVP", "athletes": [141],
                             "champion": None,
                             "fetched": "2026-01-01T00:00:00Z"},
                # ESPN ref present: 2005 champion = team 8 (Seattle).
                "257-2005": {"name": "Finals MVP", "athletes": [7],
                             "champion": 8,
                             "fetched": "2026-01-01T00:00:00Z"},
            },
        }
        by_player, champions, meta = history.honours(cache, live=False)
        assert champions[1997] == 4   # overlay (Houston Comets)
        assert champions[2005] == 8   # ESPN's own ref wins
        # The six gap years are league record, present even when this
        # fixture's index doesn't list them, plus the ESPN-ref season.
        assert set(champions) >= {1997, 1998, 1999, 2000, 2001, 2002, 2005}
        assert meta["champion_years"] == len(champions)
        assert by_player.get(141) == {"Finals MVP": 1}
    finally:
        for name, value in saved.items():
            setattr(history, name, value)


def test_fetch_athlete_debut_fallback_wnba_vs_nba(monkeypatch):
    """ESPN gives WNBA athletes no debutYear (verified Sep 2026): the debut
    derives from the first row year, so the trust rule can run at all; the
    NBA path is untouched -- no marker, no debut, honestly untrusted."""
    rows = [[2019, 1, "ATL", 30], [2020, 1, "ATL", 32],
            [2021, 2, "NYL", 28]]
    monkeypatch.setattr(history, "_get",
                        lambda url: {"displayName": "Test Player"})
    monkeypatch.setattr(history, "_parse_line", lambda payload: {"gp": 90})
    monkeypatch.setattr(history, "_parse_rows",
                        lambda payload: (list(rows), "NYL"))
    now = history._now()
    saved = {name: getattr(history, name) for name in
             ("LEAGUE", "BASE", "WEB_STATS_URL", "CACHE_PATH",
              "AWARD_TYPE_IDS", "FINALS_MVP_ID", "CHAMPION_FIRST_YEAR")}
    try:
        history.set_league("wnba")
        entry = history._fetch_athlete(1, now)
        assert entry["debut"] == 2019          # first row year
        assert entry["complete"] is True
        assert history._trusted(entry)          # 2019 <= 2019 + 1
        # The trade-split third row doesn't confuse min().
        assert len(entry["rows"]) == 3
    finally:
        for name, value in saved.items():
            setattr(history, name, value)
    nba_entry = history._fetch_athlete(1, now)
    assert nba_entry["debut"] is None           # NBA: no marker, no invention
    assert not history._trusted(nba_entry)


def test_fetch_athlete_stores_position_and_jersey(monkeypatch):
    """The identity fetch keeps the 2K-corner vitals (position + jersey)
    from the same payload it already reads -- both leagues carry them for
    retired players too (verified Sep 2026: Jordan G/23, Wilson C/22) --
    and an ESPN absence stays an honest None, never a guess."""
    monkeypatch.setattr(history, "_get",
                        lambda url: {"displayName": "Test Player",
                                     "position": {"abbreviation": "G"},
                                     "jersey": "23"})
    monkeypatch.setattr(history, "_parse_line", lambda payload: {"gp": 90})
    monkeypatch.setattr(history, "_parse_rows",
                        lambda payload: ([[2020, 1, "ATL", 30]], "ATL"))
    entry = history._fetch_athlete(1, history._now())
    assert entry["position"] == "G"
    assert entry["jersey"] == "23"
    # Empty strings / a position object without an abbreviation -> None.
    monkeypatch.setattr(history, "_get",
                        lambda url: {"displayName": "X", "jersey": "",
                                     "position": {}})
    bare = history._fetch_athlete(1, history._now())
    assert bare["position"] is None and bare["jersey"] is None


def test_entry_age_ok_refetches_pre_debut_wnba_bundles(monkeypatch):
    """Pre-upgrade WNBA bundles (complete but debut-less) refetch once so
    the trust rule can run; fresh NBA bundles with a debut stay cached."""
    now = history._now()
    stamp = history._iso(now)
    # Vitals keys present (that absence is the OTHER upgrade trigger --
    # see test_entry_age_ok_refetches_pre_vitals_bundles); debut is the
    # only thing varying in this test.
    fresh = {"complete": True, "debut": 2019, "fetched": stamp,
             "rows": [[2019, 1, "ATL", 30]],
             "position": "G", "jersey": "23"}
    saved = {name: getattr(history, name) for name in
             ("LEAGUE", "BASE", "WEB_STATS_URL", "CACHE_PATH",
              "AWARD_TYPE_IDS", "FINALS_MVP_ID", "CHAMPION_FIRST_YEAR")}
    try:
        history.set_league("wnba")
        debutless = dict(fresh)
        debutless["debut"] = None
        assert not history._entry_age_ok(debutless, now)   # refetch trigger
        assert history._entry_age_ok(fresh, now)           # resolved stays
    finally:
        for name, value in saved.items():
            setattr(history, name, value)
    assert history._entry_age_ok(fresh, now)               # NBA path unchanged


def test_entry_age_ok_refetches_pre_vitals_bundles():
    """Bundles predating the position/jersey upgrade refetch once (the
    key's absence IS the upgrade marker); an explicit None is a real
    ESPN absence and ages normally by TTL instead of refetching forever."""
    now = history._now()
    stamp = history._iso(now)
    pre_vitals = {"complete": True, "debut": 2019, "fetched": stamp,
                  "rows": [[2019, 1, "ATL", 30]]}
    upgraded = dict(pre_vitals, position=None, jersey=None)
    assert not history._entry_age_ok(pre_vitals, now)   # refetch trigger
    assert history._entry_age_ok(upgraded, now)         # absence is data


def test_championships_wnba_counts_via_overlay_and_blank_honest_gaps(monkeypatch):
    """End of the chain: a trusted WNBA career's titles count against the
    champion index (ESPN refs + verified 1997-2002 overlay); a career the
    index can't cover, or an untrusted one, stays an honest blank -- never
    a wrong number."""
    saved = {name: getattr(history, name) for name in
             ("LEAGUE", "BASE", "WEB_STATS_URL", "CACHE_PATH",
              "AWARD_TYPE_IDS", "FINALS_MVP_ID", "CHAMPION_FIRST_YEAR")}
    try:
        history.set_league("wnba")
        champions = {2001: 6, 2002: 6, 2005: 8}
        title_holder = {"debut": 2001,
                        "rows": [[2001, 6, "LOS", 30], [2002, 6, "LOS", 30],
                                 [2005, 8, "SEA", 30]]}
        assert history._championships(title_holder, champions, 1) == 3
        # 2003 not in the index -> None (gap years stay blank, not guessed).
        gap_span = {"debut": 2002,
                    "rows": [[2002, 6, "LOS", 30], [2003, 6, "LOS", 30]]}
        assert history._championships(gap_span, champions, 1) is None
        # No debut -> untrusted -> None.
        assert history._championships(
            {"debut": None, "rows": [[2001, 6, "LOS", 30]]},
            champions, 1) is None
    finally:
        for name, value in saved.items():
            setattr(history, name, value)


def test_leader_ids_wnba_empty_without_leaders_endpoint(monkeypatch):
    """ESPN's core API exposes no WNBA leaders endpoint (verified live Sep
    2026): the seed set is empty instead of raising -- the pool comes from
    the honours index + collected-window players. The NBA path keeps its
    live-or-cached requirement."""
    saved = {name: getattr(history, name) for name in
             ("LEAGUE", "BASE", "WEB_STATS_URL", "CACHE_PATH",
              "AWARD_TYPE_IDS", "FINALS_MVP_ID", "CHAMPION_FIRST_YEAR")}
    try:
        history.set_league("wnba")
        # Even an empty cache must not raise for the WNBA.
        assert history.leader_ids({"leaders": {}}, live=False) == set()
        # Live is equally empty -- no endpoint is ever asked for.
        assert history.leader_ids({}, live=True) == set()
    finally:
        for name, value in saved.items():
            setattr(history, name, value)
    # NBA: no live fetch and no cache still raises (single-league invariant).
    with pytest.raises(RuntimeError, match="career leaders"):
        history.leader_ids({"leaders": {}}, live=False)


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
    # The 2K-corner vitals ride the same build: keys always set (an ESPN
    # absence is an explicit None, not a silent gap).
    assert "position" in lebron and "jersey" in lebron
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
