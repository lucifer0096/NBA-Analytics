"""Refresh the STABLE dashboard fallback files (committed to git).

Streamlit Cloud can't run the collector, and data/raw/ is gitignored -- so
the deployed app needs small, non-timestamped copies of exactly what its
tabs read, committed here (the same pattern as FPL-Analytics' refresh script):

    data/dashboard_teams.json        team list (ids/abbrevs/names)
    data/dashboard_standings.json    latest available season's standings
    data/dashboard_schedule.json     current season schedule + scores
    data/dashboard_positions.json    current player -> position map
    data/dashboard_awards.json       every collected season's MVP/DPOY/6th-Man/
                                     MIP races + stat leaders, plus the
                                     all-NBA-history all-time boards, GOAT
                                     ladder (official honours) and the
                                     per-season games-collected inventory
    data/dashboard_players.json      Player Profile index: career lines,
                                     per-season logs, official honours and
                                     GOAT ranks for every notable player
                                     (largest committed data file -- compact
                                     JSON, only regenerated when the
                                     all-history fetch succeeds)
    data/processed/dashboard_leaderboards.json
                                     last completed season's per-player totals
                                     + shooting splits + fantasy points
    data/processed/history_cache.json
                                     ESPN career/honours cache (gitignore
                                     exception so the daily CI run refetches
                                     only what moved, not ~10k requests)

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
from datetime import date, datetime, timedelta, timezone

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
    """Schedule envelope for EVERY collected season -- the Schedule tab must
    show final scores for previous seasons too, and data/raw/ is gitignored,
    so this committed file is the deployed app's only offline source for them.

    All raw schedule.csvs are merged under "seasons". Seasons already in the
    committed envelope but absent locally are PRESERVED (the CI checkout has
    raw/ for the current season only -- dropping them would wipe history on
    the first daily run). Scores are normalised "94.0" -> "94" so the tab
    doesn't print floats. The legacy top-level "season"/"games" keys keep
    pointing at `season` for any reader of the old single-season shape."""
    import csv

    path = os.path.join(DATA_DIR, "dashboard_schedule.json")
    seasons: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                seasons = dict(json.load(f).get("seasons") or {})
        except (OSError, ValueError):
            seasons = {}
    found = False
    for name in _available_raw_seasons():
        csv_path = os.path.join(RAW_DIR, name, "schedule.csv")
        if not os.path.isfile(csv_path):
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue  # header-only file must not erase a committed season
        for row in rows:
            row["home_score"] = _clean_score(row.get("home_score"))
            row["away_score"] = _clean_score(row.get("away_score"))
        seasons[name] = rows
        found = True
    if not found and not seasons:
        print("  no schedule.csv anywhere -- skipping schedule fallback")
        return {}
    current = season if season in seasons else max(seasons)
    payload = _stamp({"season": current, "games": seasons.get(current) or [],
                      "seasons": seasons}, "local")
    # Largest dashboard file (~20k games): compact JSON like
    # dashboard_players.json, not the indent=1 default.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  wrote {path} ({len(seasons)} seasons, "
          f"{sum(len(g) for g in seasons.values())} games)")
    return payload


def _clean_score(value) -> str:
    """Schedule CSVs store floats ('94.0'); the tab prints '94'. Empty
    (scheduled games) stays empty."""
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return str(int(float(raw)))
    except ValueError:
        return raw


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
    """Per-player season totals (incl. shooting splits + fantasy points under
    default scoring) for the first season with collected games, written as a
    200KB JSON so nobody has to scan 20k raw files with pandas on Streamlit
    Cloud (it could, but the JSON is cheaper). No page reads it since Season
    Leaders was removed; it stays as a committed data artifact."""
    import glob

    import awards

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
                        "games": 0, "minutes": 0, "fantasy_points": 0.0,
                    })
                    entry["games"] += 1
                    entry["minutes"] += row.get("min") or 0
                    for stat in awards.CAREER_SUM_STATS:
                        entry[stat] = (entry.get(stat) or 0) + (row.get(stat) or 0)
        break  # ONE season only (the newest available in the try-order)
    if chosen is None:
        print("  no collected games for any candidate season -- skipping leaderboards")
        return {}

    # Score fantasy points (same default weights the model trains under).
    sys.path.insert(0, os.path.join(REPO_ROOT, "src", "model"))
    import scoring

    leaders = []
    for v in rows_by_player.values():
        row = {**v, "fantasy_points": scoring.score_row(v)}
        row.update(awards.efficiency(row))
        leaders.append(row)
    leaders.sort(key=lambda r: r["fantasy_points"], reverse=True)
    path = os.path.join(PROCESSED_DIR, "dashboard_leaderboards.json")
    _write(path, _stamp({"season": chosen, "leaders": leaders}, "local"))
    return {"season": chosen, "leaders": leaders}


def _games_by_season() -> dict:
    """Collected box-score counts for EVERY season directory -- the sidebar's
    per-season inventory ("games collected number for every season"). A
    season dir that exists but has no games yet (the upcoming one) counts 0
    instead of being silently absent."""
    import glob

    counts = {name: len(glob.glob(os.path.join(RAW_DIR, name, "games",
                                                "*.json")))
              for name in _available_raw_seasons()}
    counts.setdefault(espn_api.current_season_label(), 0)
    return counts


def _write_players(career: dict, history: dict) -> None:
    """data/dashboard_players.json -- the Player Profile index.

    Every all-history pool player with >=41 career GP or at least one
    official honour, carrying his career line, per-season log (the
    progression graph), official honours + points, championships and -- when
    he made the ladder -- GOAT rank/score. Written only after a successful
    history build so a failed fetch never degrades a committed file."""
    import awards

    ladder = {row["player_id"]: row
              for row in (career.get("goat") or {}).get("rows") or []}
    honours_map = history.get("honours") or {}
    players = {}
    for source in history.get("players") or []:
        pid = int(source["player_id"])
        player_honours = honours_map.get(pid) or {}
        if ((source.get("gp") or 0) < awards.ALLTIME_MIN_GP
                and not player_honours):
            continue
        row = dict(source)
        row["honours"] = player_honours
        row["honour_points"] = round(sum(
            awards.GOAT_HONOURS_WEIGHTS.get(name, 0.0) * count
            for name, count in player_honours.items()), 1)
        placed = ladder.get(pid)
        row["goat_rank"] = placed.get("rank") if placed else None
        row["goat_score"] = placed.get("score") if placed else None
        players[str(pid)] = row
    path = os.path.join(DATA_DIR, "dashboard_players.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = _stamp({"players": players,
                      "meta": history.get("meta") or {}}, "local")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  wrote {path} ({len(players)} players)")


def refresh_awards(seasons_to_try: list) -> dict:
    """Award races + stat leaders for EVERY collected season (2010-11 ->),
    plus the all-NBA-history all-time boards, GOAT ladder (official NBA
    honours) and the per-season games inventory.

    Season payloads/games counts are pure-local; the all-history half runs
    through history.py (ESPN + its cache). When THAT fails the previous
    dashboard_awards.json and dashboard_players.json are kept untouched --
    a window-only ladder sneaking in on a bad network day would mislead far
    more than a stale-but-labelled one. Envelope shape: {"seasons": {label:
    payload}, "window", "games_by_season", "alltime", "goat",
    "career_note"} so the sidebar season selector can pick any year."""
    import glob

    import awards
    import history as history_mod

    labels = [s for s in dict.fromkeys(seasons_to_try)
              if glob.glob(os.path.join(RAW_DIR, s, "games", "*.json"))]
    if not labels:
        print("  no collected games for any candidate season -- skipping awards")
        return {}

    season_payloads = []
    for season in labels:
        payload = awards.build_payload(season)
        if payload:
            season_payloads.append(payload)
    if not season_payloads:
        return {}

    players = awards.alltime_players()
    try:
        hist = history_mod.build(players)
    except Exception as exc:  # noqa: BLE001 -- collector must remain resumable
        print(f"  all-history fetch failed ({type(exc).__name__}: {exc}) -- "
              "keeping the previous awards/players files")
        return {}

    career = awards.build_career(season_payloads, players=players, history=hist)
    if not career:
        return {}
    envelope = {"seasons": {p["season"]: p for p in season_payloads}}
    envelope.update(career)
    envelope["games_by_season"] = _games_by_season()
    path = os.path.join(DATA_DIR, "dashboard_awards.json")
    _write(path, _stamp(envelope, "local"))
    _write_players(career, hist)
    return envelope


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
    refresh_awards(candidates)
    # Near-term projections for the deployed app -- silently skipped until
    # train.py has produced models/proj_model.txt (bootstrap state).
    refresh_projections(current)
    print("Done.")


if __name__ == "__main__":
    main()
