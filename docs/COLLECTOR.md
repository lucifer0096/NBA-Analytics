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
| Postseason schedule | same path with `seasontype=3` | same 30 calls, phase-separated into `postseason.csv`; a team outside the bracket answers 0 games (skipped, never a failure) |
| Game box score | `site.api.espn.com/.../nba/summary?event={id}` | per-game; verified back to **1995-96**; includes DNP rows |
| Standings | `site.web.api.espn.com/apis/v2/.../nba/standings?season={end_year}` | the `site.api` standings path only returns a link stub (verified) |
| Player positions | `site.web.api.espn.com/apis/common/v3/.../nba/teams/{id}/roster?season={end_year}` | **season param ignored**, always current roster (verified: 2011/2026/2027 identical) |
| Day's games | `site.api.espn.com/.../nba/scoreboard?dates=YYYYMMDD` | works for historical dates too; also the **only** home of play-in games -- they answer on no seasontype at all (verified Apr 2026 across all 30 teams), so `snapshot_gap_games` probes just the days between the regular season's last game and round one |

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
├── postseason.csv      # playoffs + play-in (seasons with a bracket only; a bracketless season writes no file at all)
└── games/
    └── {game_id}.json  # {"game": meta, "players": [...]}, PARSED rows, one file per FINAL REGULAR-SEASON game
data/raw/                # per league: data/raw/ and data/raw_wnba/
├── player_positions.json  # ONE global file: current player → G/F/C (roster endpoint is current-only)
└── collector_state.json   # last run: season, counts, timestamps (--check-only feeds from this)
```

Box-score files store the **parsed form** (the raw payload is multi-MB with
plays/winprobability we never read), ~5–8 KB per game, about 120 MB for the
full 2010-11+ backfill.

Regular-season-only **math** in both leagues: standings, per-game leaders
and the model's training frame are all built on regular-season box scores
(`seasontype=2`), and the WNBA's schedule endpoint answers the same
season type (its collected counts -- 204 games in the 12-team era, 331 in
2026 -- are regular seasons).

The postseason is collected **in the same run, into its own file**:
`seasontype=3` writes `postseason.csv` (playoff rounds; a team outside the
bracket answers 0 games, skipped without a failure), the play-in gap probe
merges the games ESPN files under no seasontype at all, and refresh copies
the rows to their own committed trees (`data/postseason/`,
`data/postseason_wnba/`). Box scores stay regular-season-only by design --
no computed view reads a per-player postseason line, so playoff rows can
never enter a frame that says regular season. The Schedule tab's labeled
Postseason section is the one consumer.

## Commands

```bash
python src/collector/snapshot.py                    # current season, incremental (the daily mode)
python src/collector/snapshot.py --backfill         # 2010-11 → latest completed, NEWEST FIRST
python src/collector/snapshot.py --from 2018-19 --to 2020-21   # explicit range
python src/collector/snapshot.py --season 2012-13   # one season
python src/collector/snapshot.py --schedule-only    # schedules (regular + postseason + play-in probe)/positions, no box scores
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
  can't kill a 19.1k-game backfill.
- **Offseason guard**: a schedule refresh that returns 0 games will not
  clobber an existing good `schedule.csv` (warning instead).
- **Postseason phase**: `snapshot_schedule(..., phase="postseason")` runs in
  the same pass against `seasontype=3` (0 games = missed playoffs, skipped
  without a failure; a bracketless season writes no file rather than a
  header-only one), then `snapshot_gap_games` hits `scoreboard?dates=` for
  only the days between the regular season's last game and round one,
  merging new `game_id`s -- idempotent, and the probe converges to zero
  calls once the play-in games close the gap. The regular-season contracts
  above are untouched: `schedule.csv` and its guards are byte-identical.

## Automated collection

`.github/workflows/collector.yml` runs daily at 12:20 UTC (after most US
games finish; GitHub's batched scheduler often fires hours later, so the
data commits typically land later in the day), for **each league in turn**:

1. `snapshot.py --season current` (NBA, then `--league wnba`): schedule
   refresh (regular + postseason + the play-in gap probe) + newly-final
   box scores, each league through its own state
   file (`data/raw/`, `data/raw_wnba/`)
2. `refresh_dashboard_fallbacks.py --league nba wnba`: rewrites both
   leagues' committed `data/dashboard_*` (+ `dashboard_wnba_*`)
   fallbacks, the per-season schedule + postseason files, and the two
   leaderboards artifacts under `data/processed/`
3. commits any data changes back to `main`

CI (`.github/workflows/ci.yml`) skips pushes where **every** changed file is
under `data/**`/`models/**`: data-only refreshes exercise no code.

In the offseason this run is nearly a no-op: no new final games, schedule
rewritten identically, fallbacks re-stamped.

## Dashboard fallback files

Streamlit Cloud cannot run the collector (`data/raw/` is gitignored) and no
view can regenerate history itself, so `refresh_dashboard_fallbacks.py`
commits small stable copies with honest `_generated_utc` + `source` stamps.
Every file exists once per league --
the WNBA's carry `_wnba` by `leagues.named_path`'s one rule
(`dashboard_teams.json` → `dashboard_wnba_teams.json`), except the two
per-season trees, whose FOLDER carries the suffix
(`data/schedules_wnba/`, `data/postseason_wnba/`):

| File | Content | Source |
|---|---|---|
| `data/dashboard_teams.json` | team list | live ESPN |
| `data/dashboard_standings.json` | the current season once it has real records, else the newest with them (zero-record tables skipped: `season_candidates()` stays completed-first for consumers wanting the last complete table, `standings_candidates()` leads with current because that is the season the app defaults to; `seasontype=2` pins regular-season records so October preseason scores never count -- verified live Oct 2026, ESPN's default totals read 0-2 off preseason before tip-off) | live ESPN |
| `data/schedules/{season}.json` | one season's full schedule from `data/raw*/schedule.csv`, with its own generation stamp, rewritten only when rows change (NBA: 17 files / 20,394 games, 19,194 with final scores; the WNBA's under `data/schedules_wnba/`: 17 files / 3,693; a committed season missing locally is preserved) | local |
| `data/postseason/{season}.json` | one season's POSTSEASON schedule (playoffs + play-in) from `data/raw*/postseason.csv`: same clean/preserve/rewrite-only-on-change contracts, own stamp, **no index** -- the Schedule tab reads the selected season's file directly and shows an honest empty when it's absent (NBA: 16 files / 1,384 games -- the bracketless 2026-27 has no file; the WNBA's under `data/postseason_wnba/`: 17 files / 307, live bracket included). The one consumer; nothing in `schedules/`, `processed/` or the model ever reads this tree | local |
| `data/dashboard_schedule.json` | thin season index (labels, per-season game counts, current season) behind the sidebar's default season and the schedule tab's honest "index says N games" fallback messages | local |
| `data/dashboard_positions.json` | current player → position map | local/live |
| `data/dashboard_awards.json` | **every collected season's** MVP/DPOY/6th-Man/MIP races + per-game stat leaders (incl. 3PM, +/-, FG/3P splits), plus the cross-season all-time boards and the all-history GOAT ladder (`awards.py` + `history.py`, both leagues with their own formulas and honours tables) | local (collected box scores + cached ESPN history) |
| `data/dashboard_players.json` | all-history player index for the Player Profile page: career line, per-season rows, official honours, GOAT score/rank, position/jersey, plus the per-season Finals MVP winners map (`finals_mvp`: winner + champion team ref per season, the Awards Ladder tracker's feed) and the four races' official winners map (`official_winners`: winners per season and race, the season-headline rows and official-vs-algorithm verdicts' feed) (`history.py`) | local |
| `data/races/{season}.json` | daily race snapshots (top rows' rank + score) behind the ladder's movement arrows and MVP trend: one per UTC day, written only while a race moves, same-day replaced, capped; the WNBA's under `data/races_wnba/` | local (the same award math) |
| `data/dashboard_projections.json` | upcoming-game model projections (NBA-trained model; never written for the WNBA) | computed where raw data + model exist |
| `data/processed/dashboard_leaderboards.json` | last completed season's per-player totals (counting stats incl. the collected-window +/-) + shooting splits (FG/3P/FT counts, FG%, eFG%, TS%) + the `fantasy_points` scoring target; kept as a committed artifact (no page reads it since Season Leaders was removed) | local |
