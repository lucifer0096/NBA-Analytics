"""Refresh the STABLE dashboard fallback files (committed to git).

Streamlit Cloud can't run the collector, and data/raw/ is gitignored -- so
the deployed app needs small, non-timestamped copies of exactly what its
tabs read, committed here (the same pattern as FPL-Analytics' refresh script):

    data/dashboard_teams.json        team list (ids/abbrevs/names)
    data/dashboard_standings.json    latest available season's standings
    data/dashboard_schedule.json     current season schedule + scores
    data/dashboard_positions.json    current player -> position map
    data/processed/dashboard_leaderboards.json
                                     last completed season's per-player totals

Each file carries its own {"_generated_utc": ..., "source": "espn"|"local"}
envelope so the dashboard can show data age honestly instead of implying
live freshness when it's serving the fallback.

Run: python src/collector/refresh_dashboard_fallbacks.py
(That's exactly what the daily GitHub Actions workflow does after
snapshot.py, then commits data/ back to main.)
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_api
import parsing

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
DATA_DIR = os.path.join(REPO_ROOT, "data")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
SEASON_DIR_RE = re.compile(r"^\d{4}-\d{2}$")


def _available_raw_seasons() -> list:
    """Collected season directories, newest first (raw data is local-only)."""
    if not os.path.isdir(RAW_DIR):
        return []
    return sorted(
        (name for name in os.listdir(RAW_DIR)
         if SEASON_DIR_RE.fullmatch(name)
         and os.path.isdir(os.path.join(RAW_DIR, name))),
        reverse=True,
    )


def season_candidates() -> list:
    """Seasons worth refreshing, with the completed season first.

    During the July-September offseason the current/upcoming season's
    standings endpoint can return 30 zero rows before tip-off. Preferring the
    latest completed season keeps the fallback truthful, while the current
    season is still tried as a fallback for January-June in-progress tables.
    """
    current = espn_api.current_season_label()
    today = datetime.now(timezone.utc).date()
    completed = f"{today.year - 1}-{str(today.year)[2:]}"
    candidates = [completed, current]
    candidates.extend(_available_raw_seasons())
    # Stable de-duplication while preserving the deliberate priority order.
    return list(dict.fromkeys(candidates))


def _has_played_standings(rows: list) -> bool:
    """Whether a standings payload represents games already played."""
    for row in rows:
        for key in ("wins", "losses"):
            try:
                if float(row.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"  wrote {path}")


def _stamp(payload: dict, source: str) -> dict:
    payload["_generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["source"] = source
    return payload


def refresh_teams() -> dict:
    rows = parsing.parse_teams(espn_api.get_teams())
    path = os.path.join(DATA_DIR, "dashboard_teams.json")
    _write(path, _stamp({"teams": rows}, "espn"))
    return {"teams": rows}


def refresh_standings(seasons_to_try: list) -> dict:
    """Standings for the first season in `seasons_to_try` that ESPN actually
    answers with entries for AND that has real results -- a not-yet-started
    season answers fine but with every win total 0 (verified Sep 2026: the
    2026-27 table is all zeros), which is technically-live but useless as a
    fallback. Only if NO candidate has a nonzero record do the zero rows get
    written at all (still better than shipping nothing)."""
    zero_rows = None
    zero_season = None
    for season in seasons_to_try:
        try:
            rows = parsing.parse_standings(
                espn_api.get_standings(espn_api.season_param(season))
            )
        except Exception as e:  # noqa: BLE001 -- try the next candidate season
            print(f"  standings {season}: {e}")
            continue
        if not rows:
            continue
        has_results = _has_played_standings(rows)
        if has_results:
            path = os.path.join(DATA_DIR, "dashboard_standings.json")
            _write(path, _stamp({"season": season, "standings": rows}, "espn"))
            return {"season": season, "standings": rows}
        if zero_rows is None:
            zero_rows, zero_season = rows, season
    if zero_rows is not None:
        print(f"  standings: only zero-record tables found -- "
              f"writing {zero_season} anyway")
        path = os.path.join(DATA_DIR, "dashboard_standings.json")
        _write(path, _stamp({"season": zero_season, "standings": zero_rows}, "espn"))
        return {"season": zero_season, "standings": zero_rows}
    print("  WARNING: no standings fetched from any candidate season")
    return {}


def refresh_schedule(season: str) -> dict:
    """Current season schedule from the collector's own schedule.csv (no API
    call -- the daily workflow runs snapshot.py first, so this file is fresh
    exactly when it matters)."""
    import csv

    path_in = os.path.join(RAW_DIR, season, "schedule.csv")
    if not os.path.exists(path_in):
        print(f"  no schedule.csv for {season} yet -- skipping schedule fallback")
        return {}
    with open(path_in, newline="", encoding="utf-8") as f:
        games = list(csv.DictReader(f))
    path = os.path.join(DATA_DIR, "dashboard_schedule.json")
    _write(path, _stamp({"season": season, "games": games}, "local"))
    return {"season": season, "games": games}


def refresh_positions() -> dict:
    """Copy the collector's current position map (or fetch it if missing)."""
    path_in = os.path.join(RAW_DIR, "player_positions.json")
    if os.path.exists(path_in):
        with open(path_in, encoding="utf-8") as f:
            payload = json.load(f)
        players = payload.get("players", payload if isinstance(payload, list) else [])
        source = "local"
    else:
        team_ids = [t["team_id"] for t in parsing.parse_teams(espn_api.get_teams())]
        players = []
        for team_id in team_ids:
            players.extend(parsing.parse_roster(
                espn_api.get_roster(team_id, espn_api.season_param(
                    espn_api.current_season_label())), team_id=team_id))
        source = "espn"
    path = os.path.join(DATA_DIR, "dashboard_positions.json")
    _write(path, _stamp({"players": players}, source))
    return {"players": players}


def refresh_leaderboards(seasons_to_try: list) -> dict:
    """Per-player season totals (incl. fantasy points under default scoring)
    for the first season with collected games -- powers the dashboard's
    historical-leaderboard tab offline, without pandas on Streamlit Cloud
    having to scan 20k raw files... (it could, but a 200KB JSON is cheaper)."""
    import glob

    rows_by_player: dict = {}
    chosen = None
    for season in seasons_to_try:
        files = glob.glob(os.path.join(RAW_DIR, season, "games", "*.json"))
        if not files:
            continue
        chosen = season
        for path in files:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            for row in payload.get("players") or []:
                if not row.get("did_not_play"):
                    entry = rows_by_player.setdefault(row["player_id"], {
                        "player_id": row["player_id"],
                        "player_name": row["player_name"],
                        "team_abbrev": row.get("team_abbrev"),
                        "games": 0, "minutes": 0, "pts": 0, "reb": 0,
                        "ast": 0, "fantasy_points": 0.0,
                    })
                    entry["games"] += 1
                    entry["minutes"] += row.get("min") or 0
                    entry["pts"] += row.get("pts") or 0
                    entry["reb"] += row.get("reb") or 0
                    entry["ast"] += row.get("ast") or 0
        break  # ONE season only (the newest available in the try-order)
    if chosen is None:
        print("  no collected games for any candidate season -- skipping leaderboards")
        return {}

    # Score fantasy points (same default weights the model trains under).
    sys.path.insert(0, os.path.join(REPO_ROOT, "src", "model"))
    import scoring

    leaders = sorted(
        ({**v, "fantasy_points": scoring.score_row(v)} for v in rows_by_player.values()),
        key=lambda r: r["fantasy_points"], reverse=True,
    )
    path = os.path.join(PROCESSED_DIR, "dashboard_leaderboards.json")
    _write(path, _stamp({"season": chosen, "leaders": leaders}, "local"))
    return {"season": chosen, "leaders": leaders}


def _as_date(value) -> date | None:
    """Parse the ISO timestamps ESPN puts in schedule rows."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def refresh_projections(season: str, horizon_days: int = 21) -> dict:
    """Build a small upcoming-game projection fallback when a model exists.

    The deployed Streamlit app cannot rebuild the historical feature table
    from ~20,000 raw game files, so the collector publishes projections for a
    rolling near-term window here. Missing model/training data is a normal
    bootstrap state and must not break the daily collector: the previous
    fallback (if any) is deliberately left untouched.
    """
    import pandas as pd

    schedule_path = os.path.join(RAW_DIR, season, "schedule.csv")
    model_path = os.path.join(REPO_ROOT, "models", "proj_model.txt")
    positions_path = os.path.join(RAW_DIR, "player_positions.json")
    if not os.path.exists(schedule_path):
        print("  no current schedule -- skipping projections")
        return {}
    if not os.path.exists(model_path):
        print("  no trained model -- skipping projections (train.py first)")
        return {}
    if not os.path.exists(positions_path):
        print("  no current positions -- skipping projections")
        return {}

    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "src", "model"))
        import load_historical
        import predict

        with open(schedule_path, newline="", encoding="utf-8") as f:
            schedule = pd.read_csv(f)
        schedule["game_id"] = schedule["game_id"].astype(str)
        today = datetime.now(timezone.utc).date()
        dates = schedule["date"].map(_as_date)
        window_end = today + timedelta(days=horizon_days)
        selected = schedule[
            (dates >= today) & (dates <= window_end)
            & (schedule["status"] != "STATUS_FINAL")
        ]
        if selected.empty:
            print(f"  no {season} games in the next {horizon_days} days -- "
                  "keeping existing projections")
            return {}

        history = load_historical.load_all_seasons()
        positions = load_historical.load_positions()
        model = predict.load_model(model_path)
        projections = predict.project_upcoming(
            history, schedule, positions,
            game_ids=selected["game_id"].tolist(), model=model,
        )
        if projections.empty:
            print("  projection model produced no rows -- keeping existing file")
            return {}
        records = json.loads(projections.to_json(orient="records"))
        payload = _stamp({
            "season": season,
            "window_days": horizon_days,
            "games": int(projections["game_id"].nunique()),
            "projections": records,
        }, "local")
        path = os.path.join(DATA_DIR, "dashboard_projections.json")
        _write(path, payload)
        return payload
    except Exception as e:  # noqa: BLE001 -- collector must remain resumable
        print(f"  WARNING: projection refresh skipped: {type(e).__name__}: {e}")
        return {}


def main() -> None:
    current = espn_api.current_season_label()
    candidates = season_candidates()

    print("Refreshing dashboard fallbacks...")
    refresh_teams()
    refresh_standings(candidates)
    refresh_schedule(current)
    refresh_positions()
    refresh_leaderboards(candidates)
    # Near-term projections for the deployed app -- silently skipped until
    # train.py has produced models/proj_model.txt (bootstrap state).
    refresh_projections(current)
    print("Done.")


if __name__ == "__main__":
    main()
