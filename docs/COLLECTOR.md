# The Collector

Snapshots NBA **and WNBA** data from ESPN's free public APIs into `data/raw/`
(the NBA, its historical name) and `data/raw_wnba/`. Both leagues share every
code path: `src/collector/leagues.py` holds the per-league config (endpoint
slug, season-label rule, award ids, path helpers) and each entry point takes
`--league` (default `nba`). Design mirrors FPL-Analytics' collector: cheap to
run frequently, **idempotent and
resumable**: the filesystem itself is the state, so there is no separate
"last fetched" bookkeeping to drift. A game whose parsed box score already
exists at `data/raw/{season}/games/{game_id}.json` is simply never refetched.

## Endpoints (all free, no auth)

| Purpose | Endpoint | Notes |
|---|---|---|
| Teams | `site.api.espn.com/.../nba/teams` | 1 call, team-id list for everything else |
| Season schedule | `site.api.espn.com/.../nba/teams/{id}/schedule?season={end_year}&seasontype=2` | 30 calls/season; **no season-wide schedule endpoint exists** (verified 404) |
| Game box score | `site.api.espn.com/.../nba/summary?event={id}` | per-game; verified back to **1995-96**; includes DNP rows |
| Standings | `site.web.api.espn.com/apis/v2/.../nba/standings?season={end_year}` | the `site.api` standings path only returns a link stub (verified) |
| Player positions | `site.web.api.espn.com/apis/common/v3/.../nba/teams/{id}/roster?season={end_year}` | **season param ignored**, always current roster (verified: 2011/2026/2027 identical) |
| Day's games | `site.api.espn.com/.../nba/scoreboard?dates=YYYYMMDD` | works for historical dates too |

Every path above is league-parameterized: the `nba` slug swaps for `wnba`
(the core API's `sports.core.api.espn.com/v2/sports/basketball/leagues/wnba`
answers everything the NBA's does except **`/leaders`**, which 404s --
see [KNOWN-ISSUES.md](KNOWN-ISSUES.md)).

### The season-parameter rule

ESPN's `season` parameter is the year a season **ends**: `season=2011` →
2010-11. Only `espn_api.season_param()` / `season_label()` do this
arithmetic; nothing else in the repo should. The WNBA's season label *is*
its calendar year (`2026` → `season=2026`), so its branch passes through
unchanged -- `season_param(label, "wnba")` and `season_param(label, "nba")`
are the only places the two rules live.

### User-Agent gotcha

ESPN's WAF 403s simplistic User-Agents (a bare `Mozilla/5.0` was verified
403 while Python's default urllib UA and a full browser-like header set
returned 200). `espn_api._HEADERS` is the one place this is encoded.

## What one season run writes

```
data/raw/{season}/              # the WNBA's tree mirrors under data/raw_wnba/{year}/
├── schedule.csv        # every regular-season game: id, date, teams, scores, status
└── games/
    └── {game_id}.json  # {"game": meta, "players": [...]}, PARSED rows, one file per FINAL game
data/raw/                # per league: data/raw/ and data/raw_wnba/
├── player_positions.json  # ONE global file: current player → G/F/C (roster endpoint is current-only)
└── collector_state.json   # last run: season, counts, timestamps (--check-only feeds from this)
```

Box-score files store the **parsed form** (the raw payload is multi-MB with
plays/winprobability we never read), ~5–8 KB per game, about 120 MB for the
full 2010-11+ backfill.

Regular season only (`seasontype=2`) **in both leagues**: fantasy leagues
play regular seasons, and the WNBA's schedule endpoint answers the same
season type (its collected counts -- 204 games in the 12-team era, 331 in
2026 -- are regular seasons with no playoff rows).
Playoffs are deliberately excluded, not overlooked.

## Commands

```bash
python src/collector/snapshot.py                    # current season, incremental (the daily mode)
python src/collector/snapshot.py --backfill         # 2010-11 → latest completed, NEWEST FIRST
python src/collector/snapshot.py --from 2018-19 --to 2020-21   # explicit range
python src/collector/snapshot.py --season 2012-13   # one season
python src/collector/snapshot.py --schedule-only    # schedules/positions, no box scores
python src/collector/snapshot.py --check-only       # report pending work; exit 1 if any
python src/collector/snapshot.py --backfill --league wnba   # the WNBA (labels are plain years: 2010, 2011, ...)
python src/collector/refresh_dashboard_fallbacks.py --league nba wnba  # both leagues in one pass
```

- **Newest-first backfill**: the validation season (2024-25) and its
  neighbors are what training needs first: walking backward from there means
  a usable training set exists long before the 2010s tail finishes.
- **Rate limiting**: a process-wide `RateLimiter` enforces `--delay` between
  request *starts* across all worker threads (default `--workers 4`,
  `--delay 0.15`), so raising workers can't silently multiply request rate.
- **Retries**: 429/5xx (and WAF 403) retry with exponential backoff
  (`espn_api._get_json`); a game that still fails is recorded, not fatal:
  its file simply wasn't written, so the next run retries it. One bad game
  can't kill a 19.7k-game backfill.
- **Offseason guard**: a schedule refresh that returns 0 games will not
  clobber an existing good `schedule.csv` (warning instead).

## Automated collection

`.github/workflows/collector.yml` runs daily at 12:20 UTC (after most US
games finish), for **each league in turn**:

1. `snapshot.py --season current` (NBA, then `--league wnba`): schedule
   refresh + newly-final box scores, each league through its own state
   file (`data/raw/`, `data/raw_wnba/`)
2. `refresh_dashboard_fallbacks.py --league nba wnba`: rewrites both
   leagues' committed `data/dashboard_*` (+ `dashboard_wnba_*`)
   fallbacks and the two leaderboards artifacts under `data/processed/`
3. commits any data changes back to `main`

CI (`.github/workflows/ci.yml`) skips pushes where **every** changed file is
under `data/**`/`models/**`: data-only refreshes exercise no code.

In the offseason this run is nearly a no-op: no new final games, schedule
rewritten identically, fallbacks re-stamped.

## Dashboard fallback files

Streamlit Cloud cannot run the collector and `data/raw*/` is gitignored, so
`refresh_dashboard_fallbacks.py` commits small stable copies with honest
`_generated_utc` + `source` stamps. Every file exists once per league --
the WNBA's carry `_wnba` by `leagues.named_path`'s one rule
(`dashboard_teams.json` → `dashboard_wnba_teams.json`):

| File | Content | Source |
|---|---|---|
| `data/dashboard_teams.json` | team list | live ESPN |
| `data/dashboard_standings.json` | the current season once it has real records, else the newest with them (zero-record preseason tables skipped: `season_candidates()` stays completed-first for consumers wanting the last complete table, `standings_candidates()` leads with current because that is the season the app defaults to; verified 2026-27 arrives all-zeros in Sep) | live ESPN |
| `data/dashboard_schedule.json` | every collected season's schedule merged from `data/raw/{season}/schedule.csv` (NBA 17 seasons, 20,394 games with final scores; WNBA 17 calendar years, 3,693; a committed season missing locally is preserved) | local |
| `data/dashboard_positions.json` | current player → position map | local/live |
| `data/dashboard_awards.json` | **every collected season's** MVP/DPOY/6th-Man/MIP races + per-game stat leaders (incl. 3PM, +/-, FG/3P splits), plus the cross-season all-time boards and the all-history GOAT ladder (`awards.py` + `history.py`, both leagues with their own formulas and honours tables) | local (collected box scores + cached ESPN history) |
| `data/dashboard_players.json` | all-history player index for the Player Profile page: career line, per-season rows, official honours, GOAT score/rank (`history.py`) | local |
| `data/races/{season}.json` | daily race snapshots (top rows' rank + score) behind the ladder's movement arrows and MVP trend: one per UTC day, written only while a race moves, same-day replaced, capped | local (the same award math) |
| `data/dashboard_projections.json` | upcoming-game model projections (NBA-trained model; never written for the WNBA) | computed where raw data + model exist |
| `data/processed/dashboard_leaderboards.json` | last completed season's per-player totals (counting stats incl. the collected-window +/-) + shooting splits (FG/3P/FT counts, FG%, eFG%, TS%) + fantasy points; kept as a committed artifact (no page reads it since Season Leaders was removed) | local |
