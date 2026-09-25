"""Offline tests for refresh_dashboard_fallbacks.py's committed-data writes.

Three contracts that would otherwise rot silently:

- the split schedule layout (one data/schedules/{season}.json per season +
  a thin index), including the legacy merged-envelope migration and the
  idempotence that keeps daily runs from churning stamps/git;
- race-history snapshots (one per UTC day, content-gated so a finished
  season freezes instead of growing forever);
- the CI-survival merge: every CI checkout has raw/ for the current season
  only, so refresh_awards must preserve the committed historical season
  payloads, the games inventory and the players projection instead of
  rebuilding everything from a one-season box scan.

Everything runs against tmp_path with monkeypatched dirs: no network, no
data/raw writes, no touching the real committed files.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import refresh_dashboard_fallbacks as refresh

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_FIELDS = ["game_id", "date", "season", "home_id", "home_abbrev",
                   "home_score", "away_id", "away_abbrev", "away_score",
                   "status", "neutral"]


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point every data dir the refresh script writes at tmp_path."""
    raw = tmp_path / "raw"
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "races").mkdir()
    raw.mkdir()
    monkeypatch.setattr(refresh, "RAW_DIR", str(raw))
    monkeypatch.setattr(refresh, "DATA_DIR", str(data))
    monkeypatch.setattr(refresh, "PROCESSED_DIR", str(data / "processed"))
    monkeypatch.setattr(refresh, "SCHEDULES_DIR", str(data / "schedules"))
    monkeypatch.setattr(refresh, "RACES_DIR", str(data / "races"))
    return {"raw": raw, "data": data}


def _write_csv(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SCHEDULE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _row(game_id: str, season: str, status: str = "STATUS_FINAL",
         home_score: str = "94.0", away_score: str = "88.0") -> dict:
    return {"game_id": game_id, "date": "2016-04-13T23:00Z", "season": season,
            "home_id": "4", "home_abbrev": "CHI", "home_score": home_score,
            "away_id": "2", "away_abbrev": "BOS", "away_score": away_score,
            "status": status, "neutral": "False"}


# ---------------------------------------------------------------------------
# refresh_schedule: split files + thin index
# ---------------------------------------------------------------------------

def test_refresh_schedule_splits_and_migrates(isolated):
    """The legacy merged envelope is materialised into per-season files (with
    its ORIGINAL stamp, not a fresh one), local raw csvs win with cleaned
    scores, and the index carries only {season: {games}} counts."""
    legacy = {
        "season": "2015-16", "games": [],
        "seasons": {"2015-16": [_row("1", "2015-16")]},
        "_generated_utc": "2026-09-24T00:00:00Z", "source": "local",
    }
    (isolated["data"] / "dashboard_schedule.json").write_text(
        json.dumps(legacy), encoding="utf-8")
    _write_csv(isolated["raw"] / "2026-27" / "schedule.csv",
               [_row("2", "2026-27"),
                _row("3", "2026-27", "STATUS_SCHEDULED", "", "")])

    index = refresh.refresh_schedule("2026-27")

    old = json.loads((isolated["data"] / "schedules" / "2015-16.json")
                     .read_text(encoding="utf-8"))
    assert old["games"] == [_row("1", "2015-16")]
    assert old["_generated_utc"] == "2026-09-24T00:00:00Z"  # stamp preserved

    new = json.loads((isolated["data"] / "schedules" / "2026-27.json")
                     .read_text(encoding="utf-8"))
    assert new["games"][0]["home_score"] == "94"  # "94.0" cleaned
    assert new["games"][0]["away_score"] == "88"
    assert new["games"][1]["home_score"] == ""  # scheduled stays empty

    assert index["season"] == "2026-27"
    assert index["seasons"] == {"2015-16": {"games": 1},
                                "2026-27": {"games": 2}}
    assert "games" not in index  # no merged 4.5MB blob in the index
    assert index["source"] == "local"
    assert index["_generated_utc"] == max(old["_generated_utc"],
                                          new["_generated_utc"])


def test_refresh_schedule_is_idempotent(isolated):
    """A second run with unchanged csvs rewrites nothing: every per-season
    stamp (and the index stamp derived from them) stays byte-identical, so
    daily CI runs only touch the season that actually changed."""
    _write_csv(isolated["raw"] / "2024-25" / "schedule.csv",
               [_row("1", "2024-25")])
    refresh.refresh_schedule("2026-27")
    paths = sorted((isolated["data"] / "schedules").glob("*.json"))
    before = {p.name: p.read_text(encoding="utf-8") for p in paths}
    before_index = (isolated["data"] / "dashboard_schedule.json").read_text(
        encoding="utf-8")

    refresh.refresh_schedule("2026-27")

    after = {p.name: p.read_text(encoding="utf-8") for p in paths}
    assert after == before
    assert (isolated["data"] / "dashboard_schedule.json").read_text(
        encoding="utf-8") == before_index


def test_refresh_schedule_keeps_committed_seasons_absent_locally(isolated):
    """A CI checkout's raw/ holds one season; the other committed seasons
    must survive a refresh untouched."""
    (isolated["data"] / "schedules").mkdir(parents=True)
    committed = {"season": "2010-11", "games": [_row("1", "2010-11")],
                 "_generated_utc": "2026-09-24T00:00:00Z", "source": "local"}
    (isolated["data"] / "schedules" / "2010-11.json").write_text(
        json.dumps(committed), encoding="utf-8")

    index = refresh.refresh_schedule("2026-27")

    assert index["seasons"]["2010-11"] == {"games": 1}
    kept = json.loads((isolated["data"] / "schedules" / "2010-11.json")
                      .read_text(encoding="utf-8"))
    assert kept == committed


# ---------------------------------------------------------------------------
# _record_race_history: daily snapshots, content-gated
# ---------------------------------------------------------------------------

def test_record_race_history_gates_on_content_and_day(isolated):
    payload = {"season": "2030-31",
               "races": {"mvp": [{"player_id": 1, "player_name": "A",
                                  "rank": 1, "score": 50.0,
                                  "extra": "dropped"}],
                         "dpoy": []}}
    path = isolated["data"] / "races" / "2030-31.json"

    refresh._record_race_history([payload])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["snapshots"]) == 1
    snap = doc["snapshots"][0]
    assert snap["races"]["mvp"][0] == {"player_id": 1, "player_name": "A",
                                       "rank": 1, "score": 50.0}

    # Nothing moved since the earlier snapshot: no append.
    doc["snapshots"][0]["date"] = "2000-01-01"
    path.write_text(json.dumps(doc), encoding="utf-8")
    refresh._record_race_history([payload])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [s["date"] for s in doc["snapshots"]] == ["2000-01-01"]

    # Moved on a later day: appended as a new snapshot.
    moved = json.loads(json.dumps(payload))
    moved["races"]["mvp"][0]["score"] = 55.0
    refresh._record_race_history([moved])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [s["date"] for s in doc["snapshots"]] == ["2000-01-01",
                                                     doc["snapshots"][-1]["date"]]
    assert len(doc["snapshots"]) == 2
    assert doc["snapshots"][0]["races"]["mvp"][0]["score"] == 50.0

    # Same day, content changed again (late box scores): replaced, not grown.
    moved["races"]["mvp"][0]["score"] = 60.0
    refresh._record_race_history([moved])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["snapshots"]) == 2
    assert doc["snapshots"][-1]["races"]["mvp"][0]["score"] == 60.0

    # A frozen season (content identical to the last snapshot) stops forever.
    doc["snapshots"][-1]["date"] = "2000-01-02"
    path.write_text(json.dumps(doc), encoding="utf-8")
    refresh._record_race_history([moved])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["snapshots"]) == 2
    assert doc["snapshots"][-1]["date"] == "2000-01-02"


def test_record_race_history_caps_snapshots(isolated):
    """The newest RACE_HISTORY_CAP snapshots are kept: an over-full history
    appends (content moved) and drops the oldest entries in the same write."""
    path = isolated["data"] / "races" / "2030-31.json"
    doc = {"season": "2030-31",
           "snapshots": [{"date": "2000-01-01", "races": {"mvp": [{"x": i}]}}
                         for i in range(refresh.RACE_HISTORY_CAP + 5)]}
    path.write_text(json.dumps(doc), encoding="utf-8")

    refresh._record_race_history([{"season": "2030-31", "races": {"mvp": []}}])

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["snapshots"]) == refresh.RACE_HISTORY_CAP


# ---------------------------------------------------------------------------
# refresh_awards: the CI-survival merge
# ---------------------------------------------------------------------------

@pytest.fixture()
def partial_raw_env(isolated):
    """A CI-shaped checkout: raw/ has ONE season, while the committed
    envelope and players projection know all of history."""
    game = isolated["raw"] / "2024-25" / "games" / "1.json"
    game.parent.mkdir(parents=True)
    game.write_text("{}", encoding="utf-8")

    real_data = REPO_ROOT / "data"
    real_awards = json.loads((real_data / "dashboard_awards.json").read_text(
        encoding="utf-8"))
    real_players = json.loads((real_data / "dashboard_players.json").read_text(
        encoding="utf-8"))
    prior = {
        "seasons": {k: real_awards["seasons"][k]
                    for k in ("2010-11", "2024-25")
                    if k in real_awards["seasons"]},
        "games_by_season": {k: real_awards["games_by_season"][k]
                            for k in ("2010-11", "2024-25")
                            if k in real_awards["games_by_season"]},
        "goat": real_awards.get("goat"),
        "alltime": real_awards.get("alltime"),
        "window": real_awards.get("window"),
        "career_note": real_awards.get("career_note"),
        "_generated_utc": "2026-09-20T00:00:00Z",
        "source": "local",
    }
    assert len(prior["seasons"]) == 2, "committed envelope lacks fixtures"
    (isolated["data"] / "dashboard_awards.json").write_text(
        json.dumps(prior), encoding="utf-8")
    slice_players = dict(list(real_players["players"].items())[:5])
    (isolated["data"] / "dashboard_players.json").write_text(
        json.dumps({"players": slice_players,
                    "_generated_utc": "2026-09-20T00:00:00Z",
                    "source": "local"}), encoding="utf-8")
    return {"prior": prior, "players": slice_players}


def _fresh_payload(season: str) -> dict:
    return {"season": season,
            "races": {"mvp": [{"player_id": 9, "player_name": "Fresh",
                               "rank": 1, "score": 42.0}]},
            "leaders": {}}


def test_refresh_awards_preserves_history_when_raw_is_partial(
        isolated, partial_raw_env, monkeypatch):
    """The one-season CI checkout: fresh races for the local season, every
    committed historical payload + inventory count preserved, and the window
    pool fed from the COMMITTED players projection (not the one-season box
    scan). History failure keeps the previous career half while the freshly
    computed races still land."""
    import awards
    import history

    monkeypatch.setattr(awards, "build_payload",
                        lambda season, raw_dir=None: _fresh_payload(season))
    captured = {}

    def _boom(window_players, cache=None, live=True):
        captured["players"] = window_players
        raise RuntimeError("offline")

    monkeypatch.setattr(history, "build", _boom)

    envelope = refresh.refresh_awards(["2010-11", "2024-25", "2026-27"])

    # Local season rebuilt, the absent season's payload preserved verbatim.
    assert envelope["seasons"]["2010-11"] == \
        partial_raw_env["prior"]["seasons"]["2010-11"]
    assert envelope["seasons"]["2024-25"]["races"]["mvp"][0]["player_id"] == 9
    # Inventory: prior counts preserved, the locally-observed season wins.
    assert envelope["games_by_season"]["2010-11"] == \
        partial_raw_env["prior"]["games_by_season"]["2010-11"]
    assert envelope["games_by_season"]["2024-25"] == 1  # local box file
    # Window pool came from the committed projection, not raw.
    assert [p["player_id"] for p in captured["players"]] == \
        [p["player_id"] for p in partial_raw_env["players"].values()]
    # History failed: career half preserved, players file untouched.
    assert envelope["goat"] == partial_raw_env["prior"]["goat"]
    assert envelope["career_note"] == partial_raw_env["prior"]["career_note"]
    players_on_disk = json.loads(
        (isolated["data"] / "dashboard_players.json").read_text(
            encoding="utf-8"))
    assert set(players_on_disk["players"]) == set(partial_raw_env["players"])
    # The local season's race got snapshotted.
    assert (isolated["data"] / "races" / "2024-25.json").exists()


def test_refresh_awards_window_metadata_spans_merged_seasons(
        isolated, partial_raw_env, monkeypatch):
    """When history succeeds, the rebuilt career half's window metadata must
    describe the MERGED collected window (both seasons), not just the one
    season the local raw happens to carry."""
    import awards
    import history

    monkeypatch.setattr(awards, "build_payload",
                        lambda season, raw_dir=None: _fresh_payload(season))
    real_players = json.loads(
        (REPO_ROOT / "data" / "dashboard_players.json").read_text(
            encoding="utf-8"))
    sample = [dict(p) for p in list(real_players["players"].values())[:2]]

    monkeypatch.setattr(
        history, "build",
        lambda window_players, cache=None, live=True: {
            "players": sample,
            "honours": {},
            "champions": {},
            "meta": {"pool": len(window_players), "espn_lines": 2,
                     "window_fallback": 0, "honour_wins": 0, "award_types": 0,
                     "award_seasons": 0, "champion_years": 0},
            "stamp": "2026-09-26T00:00:00Z",
        })

    envelope = refresh.refresh_awards(["2024-25", "2026-27"])

    assert envelope["window"]["first"] == "2010-11"
    assert envelope["window"]["last"] == "2024-25"
    assert envelope["window"]["seasons"] == 2
    assert envelope["window"]["players"] == 5  # committed projection length
    written = json.loads(
        (isolated["data"] / "dashboard_players.json").read_text(
            encoding="utf-8"))
    assert set(written["players"]) == {str(p["player_id"]) for p in sample}
