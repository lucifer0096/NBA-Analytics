"""Refresh the STABLE dashboard fallback files (committed to git).

Streamlit Cloud can't run the collector, and data/raw/ is gitignored -- so
the deployed app needs small, non-timestamped copies of exactly what its
tabs read, committed here (the same pattern as FPL-Analytics' refresh script):

    data/dashboard_teams.json        team list (ids/abbrevs/names)
    data/dashboard_standings.json    latest available season's standings
    data/dashboard_schedule.json     thin index: which per-season files exist
    data/schedules/{season}.json     one season's schedule + final scores
                                     (the app reads only the selected
                                     season's file, not a 4.5MB merged blob)
    data/races/{season}.json         daily race snapshots -> the Awards
                                     Ladder's movement arrows + trend
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
SCHEDULES_DIR = os.path.join(DATA_DIR, "schedules")
RACES_DIR = os.path.join(DATA_DIR, "races")
SEASON_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

# Race history bounds: one snapshot per UTC day per season, newest capped
# (a full NBA season is ~200 days of games; 90 keeps the trend chart's
# window wide while the file stays a few hundred KB at most).
RACE_HISTORY_CAP = 90
RACE_SNAPSHOT_TOP = 25  # players kept per race (ladders render far fewer)


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


def _read_json(path: str, default):
    """json.load with a type-checked default -- a corrupt or wrong-shaped
    committed file degrades to `default` (the caller keeps honest state)
    instead of raising inside the collector."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


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
    """Schedule data as ONE file per season under data/schedules/ plus a thin
    index at data/dashboard_schedule.json (the sidebar reads the index; the
    Schedule tab reads only the selected season's file -- the merged 4.5MB
    envelope used to be parsed for one season's worth of rows).

    Each per-season file carries its own _generated_utc, so the freshness
    caption is honest per season. A file is rewritten only when its rows
    actually CHANGED, so daily runs don't churn stamps (or git) for seasons
    nobody is playing; the index's stamp is the max child stamp for the same
    reason.

    Seasons present in the committed files but absent locally are PRESERVED
    (the CI checkout has raw/ for the current season only -- dropping them
    would wipe history on the first daily run), and the legacy merged
    envelope (top-level "seasons" of row lists) is read once as a migration
    source. Scores are normalised "94.0" -> "94" so the tab doesn't print
    floats."""
    import csv

    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    seasons_rows: dict = {}
    stamps: dict = {}

    # 1. Committed per-season files (the normal path).
    for fname in sorted(os.listdir(SCHEDULES_DIR)):
        if not fname.endswith(".json"):
            continue
        payload = _read_json(os.path.join(SCHEDULES_DIR, fname), {})
        rows = payload.get("games")
        if isinstance(rows, list) and rows:
            label = payload.get("season") or fname[:-5]
            seasons_rows[label] = rows
            stamps[label] = payload.get("_generated_utc")

    # 2. Legacy merged envelope: migration source only.
    legacy = _read_json(os.path.join(DATA_DIR, "dashboard_schedule.json"), {})
    for label, rows in (legacy.get("seasons") or {}).items():
        if isinstance(rows, list) and rows and label not in seasons_rows:
            seasons_rows[label] = rows
            stamps[label] = legacy.get("_generated_utc")

    # 3. Local raw csvs win -- but only rewrite seasons whose rows changed.
    changed = 0
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
        if seasons_rows.get(name) == rows:
            continue  # unchanged: keep the file and its stamp as they are
        payload = _stamp({"season": name, "games": rows}, "local")
        with open(os.path.join(SCHEDULES_DIR, f"{name}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        seasons_rows[name] = rows
        stamps[name] = payload["_generated_utc"]
        changed += 1

    if not seasons_rows:
        print("  no schedule.csv anywhere -- skipping schedule fallback")
        return {}

    current = season if season in seasons_rows else max(seasons_rows)
    index = {
        "season": current,
        "seasons": {label: {"games": len(rows)}
                    for label, rows in sorted(seasons_rows.items())},
        "_generated_utc": max(s for s in stamps.values() if s)
                          or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "local",
    }
    with open(os.path.join(DATA_DIR, "dashboard_schedule.json"), "w",
              encoding="utf-8") as f:
        json.dump(index, f, indent=1)
    print(f"  wrote schedule index ({len(seasons_rows)} seasons, "
          f"{sum(len(g) for g in seasons_rows.values())} games, "
          f"{changed} season file(s) updated)")
    return index


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


def _record_race_history(payloads: list) -> None:
    """Append today's race state to data/races/{season}.json -- the Awards
    Ladder's movement arrows and trend chart read these snapshots.

    Rules that keep it honest and small: one snapshot per UTC day, written
    only when the race content actually MOVED (a finished season freezes
    after its first snapshot or two instead of growing forever), the same
    day's snapshot replaced rather than duplicated when late box scores
    change it, and only the newest RACE_HISTORY_CAP snapshots kept."""
    if not payloads:
        return
    os.makedirs(RACES_DIR, exist_ok=True)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    for payload in payloads:
        races = payload.get("races") or {}
        label = payload.get("season")
        if not races or not label:
            continue
        projection = {
            key: [{field: row.get(field)
                   for field in ("player_id", "player_name", "rank", "score")}
                  for row in (rows or [])[:RACE_SNAPSHOT_TOP]]
            for key, rows in races.items()
        }
        path = os.path.join(RACES_DIR, f"{label}.json")
        doc = _read_json(path, None)
        if not isinstance(doc, dict) or not isinstance(doc.get("snapshots"),
                                                       list):
            doc = {"season": label, "snapshots": []}
        snapshots = doc["snapshots"]
        last = snapshots[-1] if snapshots else None
        if last is not None and last.get("races") == projection:
            if last.get("date") != today:
                continue  # nothing moved since the last snapshot
        elif last is not None and last.get("date") == today:
            pass  # same day, race content changed: replace below
        else:
            snapshots.append({"date": today, "races": projection})
            del snapshots[:-RACE_HISTORY_CAP]
        doc.update({"season": label, "snapshots": snapshots})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        print(f"  race history: {label} -> {path} "
              f"({len(snapshots)} snapshot(s))")


def refresh_awards(seasons_to_try: list) -> dict:
    """Award races + stat leaders for EVERY collected season (2010-11 ->),
    plus the all-NBA-history all-time boards, GOAT ladder (official NBA
    honours) and the per-season games inventory.

    Season payloads/games counts are pure-local; the all-history half runs
    through history.py (ESPN + its cache). When THAT fails the previous
    career sections and dashboard_players.json are kept, and the freshly
    computed season races still land -- a window-only ladder sneaking in on
    a bad network day would mislead far more than a stale-but-labelled one.

    CI SURVIVAL (why merged state exists): every CI checkout has data/raw/
    for the CURRENT season only, so rebuilding `seasons` from scratch would
    erase the 16 historical payloads on the first successful run, collapse
    the games inventory to one entry, and feed history.build() a one-season
    window pool (shrinking the GOAT ladder and the player index). So:
    local seasons are rebuilt, every other committed season/inventory count
    is preserved, and when raw coverage is incomplete the window pool comes
    from the committed dashboard_players.json projection instead of the
    local box scan. Envelope shape: {"seasons": {label: payload}, "window",
    "games_by_season", "alltime", "goat", "career_note"}."""
    import glob

    import awards
    import history as history_mod

    prior = _read_json(os.path.join(DATA_DIR, "dashboard_awards.json"), {})
    prior_seasons = dict(prior.get("seasons") or {})
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
    rebuilt = {p["season"]: p for p in season_payloads}
    merged_seasons = {**{label: rows for label, rows in prior_seasons.items()
                         if label not in rebuilt}, **rebuilt}
    if not merged_seasons:
        return {}
    if season_payloads:
        _record_race_history(season_payloads)

    if prior_seasons and not set(prior_seasons) <= set(labels):
        # Partial local raw (every CI run): reuse the committed player
        # projection as the window pool -- it already carries career lines,
        # seasons, peak and team for everyone the box scan would find.
        players_payload = _read_json(
            os.path.join(DATA_DIR, "dashboard_players.json"), {})
        window_players = list((players_payload.get("players") or {}).values())
        print(f"  window pool: {len(window_players)} committed players "
              "(raw covers one season only)")
    else:
        window_players = awards.alltime_players()
    if not window_players:
        print("  no window players to build careers from -- keeping the "
              "previous career sections")
        window_players = None

    hist = None
    if window_players:
        try:
            hist = history_mod.build(window_players)
        except Exception as exc:  # noqa: BLE001 -- collector must stay resumable
            print(f"  all-history fetch failed ({type(exc).__name__}: {exc}) -- "
                  "keeping the previous career sections/players file")

    # Career half: freshly built when history succeeded, else whatever the
    # committed envelope already carries (its window/alltime/goat/career_note).
    envelope = {key: value for key, value in prior.items()
                if key not in ("seasons", "games_by_season",
                               "_generated_utc", "source")}
    if hist and window_players:
        career = awards.build_career(list(merged_seasons.values()),
                                     players=window_players, history=hist)
        if career:
            envelope.update(career)
        else:
            print("  career build empty -- keeping previous career sections")
    envelope["seasons"] = merged_seasons
    envelope["games_by_season"] = {
        **(prior.get("games_by_season") or {}), **_games_by_season()}
    path = os.path.join(DATA_DIR, "dashboard_awards.json")
    _write(path, _stamp(envelope, "local"))
    if hist:
        _write_players(envelope, hist)
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
