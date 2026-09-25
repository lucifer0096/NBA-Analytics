"""Invariants over the COMMITTED data files (no data/raw, no network).

The dashboard's honesty contract rests on these artifacts agreeing with
each other: the schedule index counts exactly the rows the per-season files
hold, every race payload sits behind a games-collected count, and the
player index's GOAT ranks are the ladder ranks from dashboard_awards.json.
A collector bug or a partial CI run that breaks any of these would mislabel
data in the UI long before a human notices, so they are pinned against the
real committed files here (complements test_refresh.py, which pins how the
files get WRITTEN).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

sys.path.insert(0, str(REPO_ROOT / "src" / "collector"))
import refresh_dashboard_fallbacks as refresh  # noqa: E402


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_schedule_index_counts_match_the_per_season_files():
    """The thin index's per-season counts are the sizes the files actually
    have -- the sidebar's season list and the loader's honest 'index says N
    games' fallback both read those numbers."""
    index = _load(DATA_DIR / "dashboard_schedule.json")
    seasons = index.get("seasons")
    assert isinstance(seasons, dict) and seasons
    assert index["source"] in ("espn", "local")
    assert index.get("_generated_utc")
    for label, entry in seasons.items():
        payload = _load(DATA_DIR / "schedules" / f"{label}.json")
        assert payload["season"] == label
        assert len(payload["games"]) == entry["games"], label


def test_schedule_rows_belong_to_their_season_and_scores_are_honest():
    """Row-level honesty: finals carry digit scores, fixtures carry none,
    and ESPN's postponed/canceled '0-0' placeholders never pose as results
    (the schedule tab filters on STATUS_FINAL, so placeholders never render
    -- they are kept verbatim because rewriting them would invent data)."""
    files = sorted((DATA_DIR / "schedules").glob("*.json"))
    assert files, "committed per-season schedule files expected"
    for path in files:
        label = path.stem
        for row in _load(path)["games"]:
            assert {"game_id", "date", "season", "status"} <= set(row), \
                (label, row)
            assert row["season"] == label, (label, row["game_id"])
            home = str(row.get("home_score", ""))
            away = str(row.get("away_score", ""))
            status = row["status"]
            if status == "STATUS_FINAL":
                assert home.isdigit() and away.isdigit(), (label, row)
            elif status == "STATUS_SCHEDULED":
                assert not home and not away, (label, row)
            elif status in ("STATUS_POSTPONED", "STATUS_CANCELED"):
                assert home in ("", "0") and away in ("", "0"), (label, row)


def test_every_awards_season_is_backed_by_collected_games():
    """No race payload without box scores behind it: every season the
    awards envelope publishes has a games-collected count >= 1 in the same
    file (the sidebar inventory the KPIs read from)."""
    awards = _load(DATA_DIR / "dashboard_awards.json")
    inventory = awards.get("games_by_season") or {}
    assert awards.get("_generated_utc")
    assert inventory, "games_by_season inventory missing"
    for label in awards["seasons"]:
        assert inventory.get(label, 0) >= 1, label


def test_player_goat_ranks_are_the_ladder_s_ranks():
    """The Player Profile index and the GOAT page must never disagree:
    every rank stored on a player row IS the ladder's rank for that player,
    ranks are unique, and nobody sits on the ladder below the career-GP
    qualifier."""
    import awards

    awards_envelope = _load(DATA_DIR / "dashboard_awards.json")
    players = _load(DATA_DIR / "dashboard_players.json")["players"]
    ladder = {row["player_id"]: row["rank"]
              for row in awards_envelope["goat"]["rows"]}
    ranked = [row for row in players.values()
              if row.get("goat_rank") is not None]
    assert ranked
    for row in ranked:
        assert row.get("player_name")
        assert ladder.get(row["player_id"]) == row["goat_rank"], \
            row["player_name"]
        assert (row.get("gp") or 0) >= awards.GOAT_MIN_CAREER_GP, \
            row["player_name"]
    assert len({row["goat_rank"] for row in ranked}) == len(ranked)


def test_race_histories_are_ordered_capped_and_well_formed():
    """Race snapshots (the Awards Ladder's movement/trend source) are
    date-ordered, within the collector's caps, and carry the exact fields
    the ladder joins on."""
    files = sorted((DATA_DIR / "races").glob("*.json"))
    assert files, "committed race snapshot files expected"
    for path in files:
        doc = _load(path)
        assert doc["season"] == path.stem
        snapshots = doc["snapshots"]
        assert snapshots
        assert len(snapshots) <= refresh.RACE_HISTORY_CAP
        dates = [snap["date"] for snap in snapshots]
        assert dates == sorted(dates), path.name
        for snap in snapshots:
            for race, rows in snap["races"].items():
                assert len(rows) <= refresh.RACE_SNAPSHOT_TOP, (path.name, race)
                ranks = [row["rank"] for row in rows]
                assert ranks == sorted(ranks), (path.name, race)
                assert len(ranks) == len(set(ranks)), (path.name, race)
                for row in rows:
                    assert {"player_id", "player_name", "rank", "score"} \
                        <= set(row), (path.name, race)
                    assert isinstance(row["score"], (int, float))
