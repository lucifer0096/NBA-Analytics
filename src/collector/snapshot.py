"""Snapshot NBA season data from ESPN's public APIs to data/raw/.

Design mirrors FPL-Analytics' snapshot.py: cheap-to-run, idempotent, resumable
-- the filesystem itself is the state (a game's box score that already exists
as data/raw/{season}/games/{game_id}.json is simply never refetched), so
there is no separate "last fetched" bookkeeping to drift out of sync.

What one season run does:
  1. teams (1 call) -- the team-id list every other call needs
  2. schedule: 30 team-schedule calls -> data/raw/{season}/schedule.csv
     (every game appears in both of its teams' schedules; deduped on game_id)
  3. player positions: 30 current-roster calls (cached 7 days) ->
     data/raw/player_positions.json -- ONE global file, because ESPN's
     roster endpoint ignores its season parameter (see
     snapshot_player_positions docstring)
  4. box scores: one summary call per not-yet-fetched FINAL game ->
     data/raw/{season}/games/{game_id}.json (parsed rows, not the raw
     multi-MB payload -- load_historical only ever needs the parsed form)

Usage:
    python src/collector/snapshot.py                    # current season, incremental
    python src/collector/snapshot.py --season 2012-13   # one specific season
    python src/collector/snapshot.py --backfill         # 2010-11 .. latest completed, NEWEST FIRST
    python src/collector/snapshot.py --from 2010-11 --to 2025-26
    python src/collector/snapshot.py --schedule-only    # skip box scores entirely
    python src/collector/snapshot.py --check-only       # report pending work, fetch nothing
    python src/collector/snapshot.py --league wnba --backfill   # WNBA 2010..latest (data/raw_wnba)
    python src/collector/snapshot.py --league wnba              # WNBA current season

Leagues: --league switches ESPN's league slug, the season-label convention
(NBA '2010-11' two-year labels vs the WNBA's single calendar year '2026')
and the data root (data/raw vs data/raw_wnba) -- see leagues.py.

Fault tolerance: every per-item network call (team schedules, rosters, box
scores) is isolated -- a transient 500 on ONE call is counted, reported as a
WARNING line and retried next run instead of killing the whole run (the
Sep 2026 weekly-refresh failure was exactly one team's roster 500 aborting
everything). Genuine outages still fail loudly: no teams list at all, or a
whole season's hard failure after every season had its turn, exits non-zero
so the workflow's failure issue fires.

Why newest-first for --backfill: the model's validation season (2024-25) and
the seasons around it are what training needs first -- walking backward from
there means a usable training set exists long before the 2010s tail finishes,
instead of blocking on ~20k games being fetched in chronological order.

Rate limiting: a single process-wide limiter enforces >= --delay between
request STARTS across all worker threads (not per-thread), so --workers 6
can't turn into 6x the request rate.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_api
from espn_api import current_season_label, season_param
import leagues
import parsing

# Current league for this run (set by --league in main()). One league per
# process: every data path and season convention below is rebound by
# set_league() so the WNBA's single-year labels ('2010', '2026') and
# data/raw_wnba/ tree never mix with the NBA's two-year ones.
LEAGUE = "nba"
RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "raw")
STATE_PATH = os.path.join(RAW_DIR, "collector_state.json")
# The deliberate modeling window (see README): pre-2010 data exists in the
# API (verified -- the 1995-96 fixture in fixtures/ proves it) but is not
# backfilled by default; --from/--to can still reach it explicitly. The
# WNBA's equivalent first season is '2010' (same window policy, one label).
BACKFILL_FIRST_SEASON = "2010-11"


def set_league(league: str) -> str:
    """Switch the collector to `league`: rebind LEAGUE plus every module
    path/season constant derived from it (the WNBA's data lives under
    data/raw_wnba with single-year season labels). Returns the league name."""
    global LEAGUE, RAW_DIR, STATE_PATH, BACKFILL_FIRST_SEASON
    LEAGUE = leagues.validate(league)
    RAW_DIR = leagues.raw_dir(LEAGUE)
    STATE_PATH = os.path.join(RAW_DIR, "collector_state.json")
    BACKFILL_FIRST_SEASON = leagues.first_season(LEAGUE)
    return LEAGUE


class RateLimiter:
    """Enforce a minimum interval between request STARTS across threads."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            pause = self._last + self.min_interval - now
            self._last = max(now, self._last + self.min_interval)
        if pause > 0:
            time.sleep(pause)


def latest_completed_season(today=None, league: str = None) -> str:
    """The most recent season whose games can all be considered complete.

    NBA: year-round this is simply the season that ENDED this calendar year
    before July, i.e. 'the season labelled {y-1}-{y}' -- during Jan-Jun the
    label still refers to the in-progress season (whose finished games we DO
    want; STATUS_FINAL gating in pending_box_scores handles anything still to
    come), and from Jul onward it correctly points at the season that just
    ended while current_season_label() has already moved on to the next one.

    WNBA: the season ends in October, so Nov-Dec the year that just finished
    is the latest completed one and Jan-Oct it is {y-1} (the current year's
    season is still running or has not tipped off yet)."""
    import datetime

    today = today or datetime.date.today()
    league = league or LEAGUE
    if league == "wnba":
        return str(today.year if today.month >= 11 else today.year - 1)
    return f"{today.year - 1}-{str(today.year)[2:]}"


def _season_range(first: str, last: str) -> list:
    """Inclusive season labels, newest first ('2012-13' before '2011-12';
    WNBA: '2012' before '2011')."""
    start_first, start_last = int(first[:4]), int(last[:4])
    if start_first > start_last:
        start_first, start_last = start_last, start_first
    if LEAGUE == "wnba":
        return [str(y) for y in range(start_last, start_first - 1, -1)]
    return [f"{y}-{str(y + 1)[2:]}" for y in range(start_last, start_first - 1, -1)]


def load_team_ids() -> list:
    teams = parsing.parse_teams(espn_api.get_teams(LEAGUE))
    team_ids = [t["team_id"] for t in teams]
    minimum = leagues.cfg(LEAGUE)["min_unique_teams"]
    if len(set(team_ids)) < minimum:
        raise RuntimeError(
            f"teams endpoint returned only {len(set(team_ids))} unique teams "
            f"for {LEAGUE} -- refusing to build a partial snapshot"
        )
    return team_ids


def _schedule_row_quality(row: dict) -> tuple:
    """Prefer the most complete copy when team schedules overlap."""
    final = row.get("status") == "STATUS_FINAL"
    scores = all(row.get(key) not in (None, "") for key in ("home_score", "away_score"))
    return (int(final), int(scores), int(bool(row.get("date"))))


def snapshot_schedule(season: str, team_ids: list, limiter: RateLimiter) -> str:
    """Fetch every team's schedule, dedupe on game_id, write schedule.csv.

    One team's schedule call failing (a transient ESPN 500/403) must not kill
    the run: every game appears in BOTH of its teams' schedules, so a single
    missing team loses nothing (its games arrive via the opponents). Failed
    calls are counted and reported, and if the collected set would SHRINK the
    existing CSV while calls failed, the existing file wins instead of being
    overwritten with a thinner schedule."""
    by_id: dict = {}
    failures: list = []
    for team_id in team_ids:
        limiter.wait()
        try:
            payload = espn_api.get_schedule(season_param(season, LEAGUE),
                                            team_id, LEAGUE)
            parsed = parsing.parse_schedule(payload, season=season,
                                            league=LEAGUE)
        except Exception as e:  # noqa: BLE001 -- recorded, tolerated per team
            failures.append((team_id, f"{type(e).__name__}: {e}"))
            continue
        if not parsed:
            # A real team schedule is never empty (seasontype=2 = the full
            # regular season), so an empty answer is a failed call that
            # happened to return 200.
            failures.append((team_id, "0 games returned"))
            continue
        for row in parsed:
            game_id = row["game_id"]
            previous = by_id.get(game_id)
            if previous is None or _schedule_row_quality(row) > _schedule_row_quality(previous):
                by_id[game_id] = row

    out_dir = os.path.join(RAW_DIR, season)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "schedule.csv")

    if failures:
        team_id, detail = failures[0]
        print(f"  WARNING [{season}]: {len(failures)}/{len(team_ids)} schedule "
              f"calls failed (first: team {team_id}: {detail}) -- games are "
              "deduped from both teams' schedules, so losses are partial")

    if not by_id and os.path.exists(out_path):
        # Offseason/lockout guard: a schedule endpoint answering with zero
        # games (or failing open) must not silently wipe a good CSV that a
        # previous run wrote -- keep the old file and say so.
        print(f"  WARNING [{season}]: schedule endpoints returned 0 games -- "
              f"keeping existing {out_path}")
        return out_path

    if failures and os.path.exists(out_path) and _csv_game_count(out_path) > len(by_id):
        # Partial failure AND fewer games than last time: overwriting would
        # shrink a previously complete schedule, so keep the old file.
        print(f"  WARNING [{season}]: collected {len(by_id)} games but existing "
              f"{out_path} has more while {len(failures)} call(s) failed -- "
              "keeping the existing schedule")
        return out_path

    rows = sorted(by_id.values(), key=lambda r: (r["date"] or "", r["game_id"]))
    fieldnames = list(rows[0].keys()) if rows else [
        "game_id", "date", "season", "home_id", "home_abbrev", "home_score",
        "away_id", "away_abbrev", "away_score", "status", "neutral",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  schedule: {len(rows)} unique games -> {out_path}")
    return out_path


def _csv_game_count(path: str) -> int:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    except OSError:
        return 0


def load_schedule(season: str) -> list:
    path = os.path.join(RAW_DIR, season, "schedule.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def snapshot_player_positions(team_ids: list, limiter: RateLimiter,
                              force: bool = False, max_age_days: int = 7) -> str:
    """Fetch every CURRENT roster -> data/raw/player_positions.json.

    Deliberately ONE global file, not one per season: ESPN's roster endpoint
    IGNORES its season parameter entirely (verified directly -- asking for
    season=2011, 2026 and 2027 all return today's identical 18-man Hawks
    roster), so pretending a per-season rosters.json exists would be storing
    today's roster 16 times under 16 different season labels -- a silent data
    lie. What the endpoint actually gives is today's player->position map,
    refreshed on a max_age_days cadence since NBA rosters DO change during a
    season (signings/waivers). Historical players who've since retired simply
    won't be in this file; see load_historical.py for how that's handled.

    Fault isolation: an ESPN 500 on ONE team's roster call (the Sep 2026
    incident that used to kill the whole run) never raises. That team's rows
    are kept from the existing file instead (positions are current-state
    facts, so a days-old row beats a missing one), and only when EVERY team
    fails is the existing file left untouched so the next run retries."""
    out_path = os.path.join(RAW_DIR, "player_positions.json")
    if not force and os.path.exists(out_path):
        age_days = time.time() - os.path.getmtime(out_path)
        if age_days < max_age_days * 86400:
            return out_path

    existing_rows = []
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                existing = json.load(f)
            existing_rows = existing.get("players") or []
            if not isinstance(existing_rows, list):
                existing_rows = []
        except (OSError, ValueError, TypeError, AttributeError):
            existing_rows = []  # unreadable old file: nothing to keep from it

    rows = []
    failures = []
    for team_id in team_ids:
        limiter.wait()
        try:
            payload = espn_api.get_roster(
                team_id,
                season_param(current_season_label(league=LEAGUE), LEAGUE),
                LEAGUE)
            rows.extend(parsing.parse_roster(payload, team_id=team_id))
        except Exception as e:  # noqa: BLE001 -- tolerated per team
            failures.append((team_id, f"{type(e).__name__}: {e}"))

    if failures and len(failures) == len(team_ids) and team_ids:
        team_id, detail = failures[0]
        print(f"  WARNING: all {len(team_ids)} roster calls failed "
              f"(first: team {team_id}: {detail}) -- keeping the existing "
              "position map untouched, retried next run")
        return out_path

    if failures:
        failed_ids = {team_id for team_id, _ in failures}
        kept = [r for r in existing_rows if r.get("team_id") in failed_ids]
        rows.extend(kept)
        team_id, detail = failures[0]
        print(f"  WARNING: {len(failures)}/{len(team_ids)} roster calls failed "
              f"(first: team {team_id}: {detail}) -- kept {len(kept)} existing "
              "row(s) for those teams")

    os.makedirs(RAW_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "players": rows}, f)
    print(f"  player positions: {len(rows)} rows -> {out_path}")
    return out_path


def _valid_box_score_file(path: str, game_id) -> bool:
    """A file counts as stored only when it is a complete two-team box score."""
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        game = payload.get("game") or {}
        players = payload.get("players") or []
        if not isinstance(game, dict) or not isinstance(players, list) or not players:
            return False
        if int(game.get("game_id", -1)) != int(game_id):
            return False
        return len({row.get("team_id") for row in players if row.get("team_id") is not None}) >= 2
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def pending_box_scores(season: str, schedule_rows: list) -> list:
    """Game ids that are FINAL but whose complete box score isn't on disk yet.
    Non-final games are never fetched (their box score wouldn't exist or
    would be partial) -- they're picked up by a later run instead. Corrupt or
    partial files are also retried rather than silently treated as complete."""
    games_dir = os.path.join(RAW_DIR, season, "games")
    pending = []
    for row in schedule_rows:
        if row.get("status") != "STATUS_FINAL":
            continue
        game_id = row["game_id"]
        if _valid_box_score_file(os.path.join(games_dir, f"{game_id}.json"), game_id):
            continue
        pending.append(game_id)
    return pending


def _fetch_and_store_box_score(game_id: str, season: str,
                               limiter: RateLimiter) -> tuple:
    """Worker body: fetch one game, parse it, store the parsed rows.
    Returns (game_id, ok, detail). Never raises -- one bad game must not kill
    a 20k-game backfill; a failure simply means the file wasn't written, so
    the next run retries it."""
    try:
        limiter.wait()
        payload = espn_api.get_event_summary(game_id, LEAGUE)
        meta, rows = parsing.parse_summary(payload, LEAGUE)
        if not meta or not rows:
            return (game_id, False, "empty parse (box score not available yet?)")
        if int(meta.get("game_id", -1)) != int(game_id):
            return (game_id, False, f"summary game id {meta.get('game_id')} != {game_id}")
        team_ids = {row.get("team_id") for row in rows if row.get("team_id") is not None}
        if len(team_ids) < 2:
            return (game_id, False, "incomplete parse: fewer than two teams")
        out_dir = os.path.join(RAW_DIR, season, "games")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{game_id}.json")
        tmp_path = f"{out_path}.tmp-{os.getpid()}-{threading.get_ident()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"game": meta, "players": rows}, f)
            os.replace(tmp_path, out_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        return (game_id, True, None)
    except Exception as e:  # noqa: BLE001 -- recorded, retried next run
        return (game_id, False, f"{type(e).__name__}: {e}")


def snapshot_box_scores(season: str, game_ids: list, limiter: RateLimiter,
                        workers: int) -> tuple:
    """Fetch+store box scores for `game_ids` with a thread pool.
    Returns (n_ok, failures list)."""
    if not game_ids:
        return (0, [])
    failures = []
    ok = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_fetch_and_store_box_score, gid, season, limiter): gid
            for gid in game_ids
        }
        for i, future in enumerate(as_completed(futures), 1):
            game_id, success, detail = future.result()
            if success:
                ok += 1
            else:
                failures.append((game_id, detail))
            if i % 250 == 0 or i == len(game_ids):
                print(f"  box scores: {i}/{len(game_ids)} fetched "
                      f"({len(failures)} failures)")
    return (ok, failures)


def run_season(season: str, args, limiter: RateLimiter, team_ids: list) -> dict:
    if args.check_only:
        # A health check must be genuinely read-only: use the schedule already
        # on disk rather than refreshing it (which would make a network call
        # and rewrite a file).
        games = load_schedule(season)
        pending = pending_box_scores(season, games)
        final_count = sum(1 for g in games if g.get("status") == "STATUS_FINAL")
        print(f"[{season}] {final_count} final games, {len(pending)} box scores pending")
        return {"season": season, "final_games": final_count, "pending": len(pending)}

    print(f"[{season}] refreshing schedule (espn season="
          f"{season_param(season, LEAGUE)})...")
    snapshot_schedule(season, team_ids, limiter)
    games = load_schedule(season)
    pending = pending_box_scores(season, games)
    final_count = sum(1 for g in games if g.get("status") == "STATUS_FINAL")

    if args.schedule_only:
        print(f"[{season}] --schedule-only: skipping {len(pending)} pending box scores")
        return {"season": season, "final_games": final_count, "pending": len(pending)}

    if pending:
        print(f"[{season}] fetching {len(pending)} box scores "
              f"({args.workers} workers, {args.delay}s rate limit)...")
        ok, failures = snapshot_box_scores(season, pending, limiter, args.workers)
        print(f"[{season}] stored {ok} box scores, {len(failures)} failures")
        for game_id, detail in failures[:5]:
            print(f"    failed {game_id}: {detail}")
        if len(failures) > 5:
            print(f"    ... and {len(failures) - 5} more (all retried next run)")
        return {"season": season, "final_games": final_count,
                "stored": ok, "failures": len(failures)}

    print(f"[{season}] all {final_count} final games already on disk -- nothing to fetch")
    return {"season": season, "final_games": final_count, "stored": 0, "failures": 0}


def _save_state(results: list) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    state = {"last_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "seasons": results}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--league", default="nba", choices=leagues.LEAGUES,
                        help="League to collect: NBA (default) or WNBA "
                             "(single-year season labels, data/raw_wnba).")
    parser.add_argument("--season", default=None,
                        help="Season label like 2012-13 (WNBA: 2012), or 'current' (default).")
    parser.add_argument("--backfill", action="store_true",
                        help=f"Fetch {BACKFILL_FIRST_SEASON}..latest completed season, "
                             "newest first (the one-off historical import).")
    parser.add_argument("--from", dest="from_season", default=None,
                        help="First season of an explicit range (with --to).")
    parser.add_argument("--to", dest="to_season", default=None,
                        help="Last season of an explicit range (with --from).")
    parser.add_argument("--schedule-only", action="store_true",
                        help="Refresh schedules (and the position map) but fetch no box scores.")
    parser.add_argument("--check-only", action="store_true",
                        help="Report pending work per season, fetch nothing "
                             "(exit code 1 if anything is pending).")
    parser.add_argument("--force-rosters", action="store_true",
                        help="Refetch the current player-position map even if "
                             "it's younger than its refresh window.")
    parser.add_argument("--workers", type=int, default=4,
                        help="Thread pool size for box-score fetches (default 4).")
    parser.add_argument("--delay", type=float, default=0.15,
                        help="Minimum seconds between request STARTS, process-wide "
                             "(default 0.15).")
    args = parser.parse_args()
    set_league(args.league)
    print(f"League: {leagues.display(LEAGUE)} ({LEAGUE})")

    if args.backfill:
        seasons = _season_range(BACKFILL_FIRST_SEASON, latest_completed_season())
    elif args.from_season or args.to_season:
        if not (args.from_season and args.to_season):
            parser.error("--from and --to must be used together")
        seasons = _season_range(args.from_season, args.to_season)
    elif args.season and args.season != "current":
        seasons = [args.season]
    else:
        seasons = [current_season_label(league=LEAGUE)]

    print(f"Seasons: {', '.join(seasons)}")
    limiter = RateLimiter(args.delay)
    team_ids = [] if args.check_only else load_team_ids()
    print(f"Teams: {len(team_ids) if not args.check_only else 'not queried (check-only)'}")

    # Positions are a CURRENT-state fact (single global file) -- see
    # snapshot_player_positions docstring for why they're not per-season.
    if not args.check_only:
        snapshot_player_positions(team_ids, limiter, force=args.force_rosters)

    results = []
    season_failures = []
    for season in seasons:
        try:
            results.append(run_season(season, args, limiter, team_ids))
        except Exception as exc:  # noqa: BLE001 -- one season must not waste the rest
            season_failures.append(f"{season}: {type(exc).__name__}: {exc}")
            print(f"  WARNING [{season}]: {type(exc).__name__}: {exc} -- "
                  "continuing with the remaining seasons")

    if not args.check_only:
        _save_state(results)

    if season_failures:
        # Still a failed RUN (the workflow's failure issue must fire for a
        # genuine problem) -- but only after every season had its chance.
        print("Season failures:")
        for line in season_failures:
            print(f"  {line}")
        sys.exit(1)

    if args.check_only:
        pending_total = sum(r.get("pending", 0) for r in results)
        print(f"Total pending box scores: {pending_total}")
        sys.exit(1 if pending_total else 0)


if __name__ == "__main__":
    main()
