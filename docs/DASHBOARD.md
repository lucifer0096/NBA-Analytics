# The Dashboard

**Run**: `streamlit run app/app.py`. One Home page plus three left-nav
pages; dark theme ships in `.streamlit/config.toml`.

## Design contract

- **Live-first, fallback-second.** Every loader in `app/shared.py` tries the
  free ESPN API first (60s `st.cache_data` TTL) and falls back to the
  committed `data/dashboard_*` file only when the call fails, so the same
  build renders correctly on a data machine, on Streamlit Cloud (no raw
  data, sometimes no direct ESPN reach), and fully offline (the test
  suite's condition).
- **Honest freshness.** Every tab shows a caption from `data_age_note()`:
  `Live (60s cache)` when the API answered, `Offline fallback` plus the
  committed `_generated_utc` stamp when it didn't. Tabs with no live API
  (Awards Ladder, Court View, All-Time Stats, GOAT Rankings, Player
  Profile) say the data was computed from collected box scores, with the
  stamp, instead of ever implying "live".
- **Honest empties, never borrowed data.** A season with no collected
  games never shows another season under its label: standings renders an
  empty state naming the season (or "not started yet" when the live table
  arrives all zeros), the Awards Ladder notes that the season has no
  collected games yet plus where the newest data is, and the Schedule tab
  shows its own committed rows for that season or an empty table,
  whichever is honest.
- **Offline is the tested state.** `app/test_app_offline.py` forces *every*
  ESPN call to fail (monkeypatches `espn_api._get_json`) and asserts every
  page still renders its tabs, KPI cards, and empty states. The deploy
  condition is the default test condition.

## Home (`app/app.py`)

1. **Sidebar**: every season from 2010-11 through 2026-27, defaulting to
   the newest with collected games (2025-26); a "Games collected"
   inventory expander listing per-season box-file counts (honest 0 for
   2026-27); and a "Model & History" expander carrying the validation
   headline (model MAE vs the naive rolling-5 baseline plus the
   validation season, or the train-first note when `models/metrics.json`
   is absent). The model's full numbers stay in `models/metrics.json`.
2. **KPI strip**: three glass metric cards scoped to the selected season.
   *Games collected* (box-score files for that season, 0 shown honestly
   when none), *Next tip-off* (first future game in the schedule,
   otherwise "—" with help text for finished seasons), *Season scoring
   leader* (per-game points from the committed awards payload, with
   season-aware help when no games are collected). The old Model
   validation KPI moved to the sidebar expander.
3. **Standings**: conference tables, W-L combined, Win%, seed, streak,
   PF/g, PA/g; playoff (🟢) and play-in (🟡) zone glyphs appear only when
   the table has real records (preseason all-zero tables get no marks). A
   season that hasn't tipped off says so; a failed live fetch over a
   mismatched committed copy shows no table at all, just a note naming
   both seasons.
4. **Schedule & Scores**: live scoreboard for *today* (empty + explanatory
   caption in the offseason, the normal Oct–Jun-less state), then the
   selected season from the committed **multi-season envelope**
   (17 seasons, 20,394 games with final scores; regenerated from
   `data/raw/*/schedule.csv` by `refresh_dashboard_fallbacks.py`, and a
   committed season missing locally is preserved). A season still running
   shows the 15 most recent results (day, matchup, final score) and the
   next 25 fixtures, plus a count of past-dated postponed/canceled rows
   excluded from both. A finished season renders the full chronological
   table of every completed game with its final score.
5. **Awards Ladder**: the selected season's MVP / DPOY / 6th Man / MIP
   races in two columns, then a stat-leaders strip behind a
   PTS/REB/AST/STL/BLK/**3PM** radio (top-10 per-game rates, each row
   carrying the FG/3P made-attempt splits + FG%). Every row is a real
   headshot over a team-logo CSS fallback with the race's stat line and
   score. Computed locally from the collected box scores
   (`src/collector/awards.py`, committed as `data/dashboard_awards.json`,
   keyed by season for every collected year 2010-11 → present); each
   race's caption prints its exact formula verbatim plus the "not
   official NBA voting" disclaimer. An empty race names the missing prior
   season instead of inventing a winner, and a season with no collected
   games gets a "no collected games yet" note instead of substituted
   races.
6. **Court View** (fourth tab, moved here from the deleted Model &
   History page): the selected stat's leaders on a CSS-only hardwood court
   (gradient markings, no images). Rank order fills a 2 G / 2 F / 1 C
   formation from the committed roster map, C row first under the basket;
   overflow and unmapped positions land on a bench strip rather than
   being forced into a slot. Follows the sidebar's season selector (every
   collected season). Hovering a card shows the player's full per-game
   line **including the FG/3P shooting splits** (CSS-only tooltip). The
   PuLP optimizer remains a backend component (`src/model/optimizer.py` +
   its tests); this tab moved, it didn't change.

## Left-nav pages

### All-Time Stats (`app/pages/2_All-Time_Stats.py`)

Career totals across **all** collected seasons (2010-11 → 2025-26, the
window stated honestly: the collector starts at 2010-11, so this is not
full NBA history) behind a rank-by radio over counting boards
(PTS/REB/AST/STL/BLK/3PM) and efficiency boards
(FG%/3P%/FT%/eFG%/TS%). The table shows the full parameter set: GP, MIN,
PTS, REB/ORB/DREB, AST, STL, BLK, TO, FG/3P/FT made-attempt splits, and
all five percentages. Qualified at ≥41 career GP; % boards additionally
require an attempts floor (≥5 FGA/g, 3P ≥2 3PA/g, FT ≥1 FTA/g) so a 1-1
shooter can't top FG%.

### GOAT Rankings (`app/pages/3_GOAT_Rankings.py`)

A transparent career composite over **all NBA history**, not just this
repo's collection window:

- **Data**: ESPN athlete career lines merged by
  `src/collector/history.py` for 1,809 players (all-history career
  leaders + official-award winners + the 1,382 collected players ≥41 GP),
  committed in the career sections of `data/dashboard_awards.json`
  (GOAT rows) with `data/dashboard_players.json` carrying the same index
  for the Profile page. The source caption repeats the pool definition
  and the as-of stamp.
- **Formula** (printed verbatim above the ladder, every weight on
  screen): **40%** production (career PTS/REB/AST/STL/BLK/3PM totals vs
  the pool's best), **35%** official honours (ESPN's 20 award types at
  points per win, MVP 6.0 down to Sixth Man-tier 0.5), **25%** peak
  (best season's per-game impact). Each component normalizes 0–100
  against the best qualified player; qualified at ≥82 career GP.
- **Honours are ESPN's official award names only.** Championships are
  displayed (🏆×N) but never scored; All-Star game selections don't exist
  in ESPN's awards API, so they're never shown or scored either.
- **Honest gaps.** A career ESPN can't fully cover shows `🏆 —` instead
  of a ring count and blanks seasons/peak with the reason (e.g. the
  champion index starts in 1970, or a career's season rows start too late
  to trust); a missing component drops out of the score and the result
  rescales over the weights that are available.
- Explicitly labeled **not an official NBA ranking**. Each row carries
  honour chips heaviest-first (top 5, then "+N more") and the three
  component scores behind the headline number. Known ESPN quirks are
  captioned on the page (ABA/NBA totals merged, Wilt's rebound total
  absent from ESPN's leaders, the mislabelled blocks category,
  late-starting pre-1977 season rows).

### Player Profile (`app/pages/4_Player_Profile.py`)

Up to four players (defaults to the GOAT top four) on **one screen**:

- **Career cards** in a 2×2 grid beside a height-capped **Official
  accolades** table (wins per ESPN award type × the GOAT formula's
  honour weight, with the "not in ESPN's API" honesty caption).
- **Career progression**: an interactive plotly chart with metrics
  PTS/REB/AST/STL/BLK/MIN/FG%/3P%, per-game or season totals
  (percentages ignore the toggle). The x-axis is a **categorical season
  axis** (plotly would otherwise parse `2003-04` as a date and tick every
  3 months), so every selected player's arc lines up per season. Hover
  carries season, team, GP and value for every player at once; the
  legend toggles players off and on; drag to zoom, double-click to reset.
- Per-season rows come from ESPN's athlete statistics (`seasons_log` in
  `data/dashboard_players.json`); seasons ESPN doesn't cover draw no
  point, and a caption names the affected players instead of
  interpolating.

## UI pass (presentation layer)

All styling is one CSS block in `shared.inject_css()`, CSS-only with no JS:

- accent-gradient page title, hero strip with season/data-age pills,
- glass metric cards, accent-underlined tabs, uppercase section labels,
- slot chips, award-race/leader rows, and court cards whose stat tooltips
  are pure CSS (`:hover`), including the image fallback (headshot `<img>`
  over a team-logo `background-image`, so a CDN 404 just shows the logo);
- every color derived from Streamlit's own theme tokens
  (`--primary-color`, `--secondary-background-color`, `--text-color`), so
  a theme change can't break contrast;
- all transitions/animations disabled under
  `prefers-reduced-motion: reduce`.

## Fallback files the pages read

| File | Written by | Read by |
|---|---|---|
| `data/dashboard_teams.json` | `refresh_dashboard_fallbacks.py` (live) | Teams-source hero note, team labels |
| `data/dashboard_standings.json` | same (live, zero-record gated) | Standings tab |
| `data/dashboard_schedule.json` | same (local `schedule.csv` files, merged across all 17 seasons) | KPI next tip-off, Schedule tab |
| `data/dashboard_positions.json` | same (local position map) | Court View formation |
| `data/dashboard_awards.json` | same (local award math: every collected season's races/leaders, all-time boards, all-history GOAT ladder) | KPI scoring leader, Awards Ladder, Court View, All-Time Stats, GOAT Rankings |
| `data/dashboard_players.json` | same (`history.py` all-history index: careers, honours, GOAT ranks) | Player Profile |
| `data/processed/dashboard_leaderboards.json` | same (local totals + shooting splits) | committed artifact only; no page reads it since Season Leaders was removed |
| `models/metrics.json`, `models/proj_model.txt` | `train.py` | Sidebar Model & History mention (headline) |
