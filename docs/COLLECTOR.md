# The Collector

Snapshots NBA data from ESPN's free public APIs into `data/raw/`. Design
mirrors FPL-Analytics' collector: cheap to run frequently, **idempotent and
resumable** — the filesystem itself is the state, so there is no separate
"last fetched" bookkeeping to drift. A game whose parsed box score already
exists at `data/raw/{season}/games/{game_id}.json` is simply never refetched.

## Endpoints (all free, no auth)

| Purpose | Endpoint | Notes |
|---|---|---|
| Teams | `site.api.espn.com/.../nba/teams` | 1 call, team-id list for everything else |
| Season schedule | `site.api.espn.com/.../nba/teams/{id}/schedule?season={end_year}&seasontype=2` | 30 calls/season; **no season-wide schedule endpoint exists** (verified 404) |
| Game box score | `site.api.espn.com/.../nba/summary?event={id}` | per-game; verified back to **1995-96**; includes DNP rows |
| Standings | `site.web.api.espn.com/apis/v2/.../nba/standings?season={end_year}` | the `site.api` standings path only returns a link stub (verified) |
| Player positions | `site.web.api.espn.com/apis/common/v3/.../nba/teams/{id}/roster?season={end_year}` | **season param ignored** — always current roster (verified: 2011/2026/2027 identical) |
| Day's games | `site.api.espn.com/.../nba/scoreboard?dates=YYYYMMDD` | works for historical dates too |

### The season-parameter rule

ESPN's `season` parameter is the year a season **ends**: `season=2011` →
2010-11. Only `espn_api.season_param()` / `season_label()` do this
arithmetic; nothing else in the repo should.

### User-Agent gotcha

ESPN's WAF 403s simplistic User-Agents (a bare `Mozilla/5.0` was verified
403 while Python's default urllib UA and a full browser-like header set
returned 200). `espn_api._HEADERS` is the one place this is encoded.

## What one season run writes

```
data/raw/{season}/
├── schedule.csv        # every regular-season game: id, date, teams, scores, status
└── games/
    └── {game_id}.json  # {"game": meta, "players": [...]} — PARSED rows, one file per FINAL game
data/raw/
├── player_positions.json  # ONE global file: current player → G/F/C (roster endpoint is current-only)
└── collector_state.json   # last run: season, counts, timestamps (--check-only feeds from this)
```

Box-score files store the **parsed form** (the raw payload is multi-MB with
plays/winprobability we never read), ~5–8 KB per game — about 120 MB for the
full 2010-11+ backfill.

Regular season only (`seasontype=2`): fantasy leagues play regular seasons.
Playoffs are deliberately excluded, not overlooked.

## Commands

```bash
python src/collector/snapshot.py                    # current season, incremental (the daily mode)
python src/collector/snapshot.py --backfill         # 2010-11 → latest completed, NEWEST FIRST
python src/collector/snapshot.py --from 2018-19 --to 2020-21   # explicit range
python src/collector/snapshot.py --season 2012-13   # one season
python src/collector/snapshot.py --schedule-only    # schedules/positions, no box scores
python src/collector/snapshot.py --check-only       # report pending work; exit 1 if any
```

- **Newest-first backfill**: the validation season (2024-25) and its
  neighbors are what training needs first — walking backward from there means
  a usable training set exists long before the 2010s tail finishes.
- **Rate limiting**: a process-wide `RateLimiter` enforces `--delay` between
  request *starts* across all worker threads (default `--workers 4`,
  `--delay 0.15`), so raising workers can't silently multiply request rate.
- **Retries**: 429/5xx (and WAF 403) retry with exponential backoff
  (`espn_api._get_json`); a game that still fails is recorded, not fatal —
  its file simply wasn't written, so the next run retries it. One bad game
  can't kill a 19.7k-game backfill.
- **Offseason guard**: a schedule refresh that returns 0 games will not
  clobber an existing good `schedule.csv` (warning instead).

## Automated collection

`.github/workflows/collector.yml` runs daily at 12:20 UTC (after most US
games finish):

1. `snapshot.py --season current` — schedule refresh + newly-final box scores
2. `refresh_dashboard_fallbacks.py` — rewrites the committed `data/dashboard_*`
   fallbacks and `data/processed/dashboard_leaderboards.json`
3. commits any data changes back to `main`

CI (`.github/workflows/ci.yml`) skips pushes where **every** changed file is
under `data/**`/`models/**` — data-only refreshes exercise no code.

In the offseason this run is nearly a no-op: no new final games, schedule
rewritten identically, fallbacks re-stamped.

## Dashboard fallback files

Streamlit Cloud cannot run the collector and `data/raw/` is gitignored, so
`refresh_dashboard_fallbacks.py` commits small stable copies with honest
`_generated_utc` + `source` stamps:

| File | Content | Source |
|---|---|---|
| `data/dashboard_teams.json` | team list | live ESPN |
| `data/dashboard_standings.json` | newest season **with real records** (zero-record preseason tables skipped — verified 2026-27 arrives all-zeros in Sep) | live ESPN |
| `data/dashboard_schedule.json` | current season schedule (from local `schedule.csv`, no API call) | local |
| `data/dashboard_positions.json` | current player → position map | local/live |
| `data/dashboard_projections.json` | upcoming-game model projections | computed where raw data + model exist |
| `data/processed/dashboard_leaderboards.json` | last completed season's per-player totals + fantasy points | local |
