# The Dashboard

**Run**: `streamlit run app/app.py`. One Home page plus three left-nav
pages; dark theme ships in `.streamlit/config.toml`.

**Every page carries the same two-league switch**: top-level `NBA | WNBA`
tabs, each nesting that league's content (on Home, the four section
sub-tabs). `?league=wnba` renders the requested league's tab first
(`st.tabs` has no programmatic selection, so the reorder *is* the deep
link; labels are the map keys, so the caller's content stays matched).
The two leagues share every loader, widget, and fallback through a
`league="nba"` default argument -- widget keys are league-suffixed
because Streamlit renders *both* tabs' content in one script run.

## Design contract

- **Live-first, fallback-second.** Every loader in `app/shared.py` tries the
  free ESPN API first (60s `st.cache_data` TTL) and falls back to the
  committed `data/dashboard_*` file only when the call fails (the WNBA's
  carry `_wnba` after the `dashboard_` prefix -- one naming rule in
  `leagues.named_path` shared with the collector that writes them), so the
  same build renders correctly on a data machine, on Streamlit Cloud (no
  raw data, sometimes no direct ESPN reach), and fully offline (the test
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
  whichever is honest. The same rule applies *across* leagues: an NBA
  label never preselects a WNBA selector (the label shapes never collide,
  `2025-26` vs `2026`, so one `?season=` param serves both).
- **Offline is the tested state.** `app/test_app_offline.py` forces *every*
  ESPN call to fail (monkeypatches `espn_api._get_json`) and asserts every
  page still renders its league tabs, section tabs, KPI cards, and empty
  states. The deploy condition is the default test condition.

## Home (`app/app.py`)

Inside each league tab: the KPI strip, hero line, and the four section
sub-tabs (reordered together by `?tab=`). Everything below exists once
per league, with that league's season and data.

1. **Sidebar**: one season selector per league -- NBA every season from
   2010-11 through 2026-27 (defaulting to the newest with collected
   games, 2025-26), WNBA every calendar year 2010 → current (its own
   inventory default, 2026); a "Games collected" inventory expander per
   league listing per-season box-file counts (honest 0 for 2026-27); and
   a "Model & History" expander carrying the validation
   headline (model MAE vs the naive rolling-5 baseline plus the
   validation season, or the train-first note when `models/metrics.json`
   is absent), captioned that the model is NBA-trained and the WNBA tab
   runs no projections. The model's full numbers stay in
   `models/metrics.json`.
2. **KPI strip**: three glass metric cards scoped to the selected season.
   *Games collected* (box-score files for that season, 0 shown honestly
   when none), *Next tip-off* (first future game in the schedule,
   otherwise "—" with help text for finished seasons), *Season scoring
   leader* (per-game points from the committed awards payload, with
   season-aware help when no games are collected). The old Model
   validation KPI moved to the sidebar expander.
3. **Standings**: conference tables, W-L combined, Win%, seed, streak,
   PF/g, PA/g, team **+/-** (signed per-game point differential) and
   **L10** (last-ten record) -- both parsed only when ESPN returns the
   stat, so a missing value renders blank, never a fabricated zero;
   playoff (🟢) and play-in (🟡) zone glyphs appear only when
   the table has real records (preseason all-zero tables get no marks);
   the WNBA pane marks its top-8 playoff field of 15 franchises with 🟢
   and has no play-in row. Standings are pinned to regular-season
   records (`seasontype=2`): October preseason scores never update the
   table (ESPN's default totals fold them in -- verified live Oct 2026,
   the 2026-27 table read 0-2 off preseason before tip-off), so a
   season whose regular season hasn't started says so; a failed live
   fetch over a mismatched committed copy shows no table at all, just a
   note naming both seasons. A **Download standings CSV** button exports both
   conferences with exactly the columns on screen (blanks where ESPN
   omitted a stat, never zeros).
4. **Schedule & Scores**: live scoreboard for *today* (empty + explanatory
   caption in the offseason, the normal Oct–Jun-less state), then the
   selected season from its own committed file under `data/schedules/`
   (`data/schedules_wnba/` in the WNBA pane; 17 files / 20,394 games for
   the NBA -- 19,194 with final scores -- and 17 years / 3,693 for the
   WNBA), regenerated from `data/raw*/schedule.csv` by
   `refresh_dashboard_fallbacks.py` (each file carries its own stamp and
   is rewritten only when rows change; a committed season missing locally
   is preserved). A season still running
   shows the 15 most recent results (day, matchup, final score) and the
   next 25 fixtures, plus a count of past-dated postponed/canceled rows
   excluded from both. A finished season renders the full chronological
   table of every completed game with its final score. A toolbar above
   the tables filters the whole tab: **Team** (any club's fixtures, home
   or away) and **Venue** (home/away relative to that team once one is
   picked, or neutral-site games only) scoping the results, the fixtures,
   the game-detail picker and the export together, with a **Download
   schedule CSV** button taking exactly the selected rows and their
   statuses -- and zero matches says so plainly instead of an empty
   table. Immediately below sits the labeled **{season} postseason**
   section: that season's playoff and play-in rows from their own
   committed tree (`data/postseason/`, `data/postseason_wnba/`), an
   identical chronological table with a **Status** column (Final /
   Scheduled / Postponed, title-cased), scoped by the same Team/Venue
   toolbar, with its own **Download postseason CSV** button and a caption
   stating these rows are display-only -- standings, leaders, GOAT and
   the projection model stay regular-season-only. A season without a
   bracket (or without collected rows) says exactly that
   (`No postseason schedule for 2026-27: the bracket does not exist until
   the regular season ends, or it has not been collected yet`) instead of
   reusing another season's games. Playoff fixtures also join the
   game-detail picker below with a **(PO)** mark, since any event id
   resolves live from the same summary endpoint. Below the tables
   sits the **Game detail: box score & play-by-play** picker (the
   NBA-app-style view): any fixture of the season, finished or upcoming,
   resolves live from ESPN's summary endpoint (cached 15 minutes) into
   both teams' box-score lines (the basic line through the **+/-**
   column, DNP rows marked) plus a
   period/scoring-filterable play-by-play, while a not-yet-played fixture
   shows tip-off, status, venue and TV instead of pretending a box score
   exists. Unreachable ESPN degrades to a caption saying why -- the
   committed schedule carries scores, never per-player lines or PBP.
5. **Awards Ladder**: opens with the selected season's **official
   season awards** headline (MVP first, then Finals MVP, both off
   ESPN's award index, honest pending rows until announced), so the
   page leads with the actual winners; then the selected season's
   MVP / DPOY / 6th Man / MIP races in two columns, then a stat-leaders
   strip behind a PTS/REB/AST/STL/BLK/**3PM**/**+/-** radio (top-10 per-game rates, each row
   carrying the FG/3P made-attempt splits + FG%). Every row is a real
   headshot over a team-logo CSS fallback with the race's stat line and
   score. Computed locally from the collected box scores
   (`src/collector/awards.py`, committed as `data/dashboard_awards.json`
   per league, keyed by season for every collected year NBA 2010-11 →
   present / WNBA 2010 → present); each
   race's caption prints its exact formula verbatim plus the "not
   official NBA voting" ("... WNBA voting" in the WNBA pane) disclaimer. An empty race names the missing prior
   season instead of inventing a winner, and a season with no collected
   games gets a "no collected games yet" note instead of substituted
   races. Ladder rows carry a **rank-movement arrow** (green ▲ / red ▼
   with the places gained or lost) comparing the last two daily race
   snapshots, and an **MVP race trend** chart draws the current leaders'
   scores across those snapshots (top 6 of the latest, absent scores
   left as gaps). With fewer than two snapshots both degrade to a caption
   saying exactly that -- movement and trends are never invented.
   Between the races and the stat leaders sits the
   **official-vs-algorithm scorecard**: one row per race pairing the
   season's actual award winner from ESPN's award index ("Official:
   ...") with the race's homegrown #1 above ("Algorithm #1: ..."), and a
   verdict slot -- green ✓ agree / red ✗ differs, a dash while ESPN
   hasn't announced that season's award yet. The caption keeps both
   sides' provenance explicit (official = the honours' own source,
   algorithm = the transparent formula, never official voting), and a
   season the index hasn't reached prints its honest gap instead of a
   name.
   Below the races, the tab also carries the **official Finals MVP
   tracker**: one race-row per season from ESPN's award index (trophy
   slot, real headshot, winner, franchise, `Finals MVP` score slot) --
   current season first, then the previous ones (latest 10 shown), with
   the current season rendered as an honest "not awarded yet" pending
   row until ESPN publishes a winner (offseason or Finals in progress,
   verified live Oct 2026: WNBA 2026 is 404 until the Finals end). The
   winners ride the committed `dashboard_players.json`'s `finals_mvp`
   map (written by `history.py` next to the honours), team names
   resolve off the committed franchise list (an unknown id keeps the
   row season-only), and the caption prints the index's season coverage
   and file age -- official ESPN award history, unlike the homegrown
   races above.
6. **Court View** (fourth tab, moved here from the deleted Model &
   History page): the selected stat's leaders on a CSS-only hardwood court
   (gradient markings, no images). Rank order fills a 2 G / 2 F / 1 C
   formation from the committed roster map, C row first under the basket;
   overflow and unmapped positions land on a bench strip rather than
   being forced into a slot. Follows that league tab's season selector
   (every collected season, roster map and positions per league). Hovering a card shows the player's full per-game
   line **including the FG/3P shooting splits** (CSS-only tooltip). The
   PuLP optimizer remains a backend component (`src/model/optimizer.py` +
   its tests); this tab moved, it didn't change.

## Left-nav pages

Each page opens on the same `NBA | WNBA` league tabs as Home; the
sidebar shows both leagues' game inventories.

### All-Time Stats (`app/pages/2_All-Time_Stats.py`)

Career totals across **all** collected seasons per league (NBA 2010-11 →
2025-26, WNBA 2010 → 2026; the window stated honestly: the collector
starts at its first year, so this is not
full league history) behind a rank-by radio over counting boards
(PTS/REB/AST/STL/BLK/3PM/+/-) and efficiency boards
(FG%/3P%/FT%/eFG%/TS%). The table shows the full parameter set: GP, MIN,
PTS, REB/ORB/DREB, AST, STL, BLK, TO, +/-, FG/3P/FT made-attempt
splits, and all five percentages. The +/- board and column sum only the
collected box scores (ESPN's career statistics carry no +/- at all): a
career the window never covered is left off that board and shows a
blank cell, never an invented zero. Qualified at ≥41 career GP; %
boards additionally require an attempts floor (≥5 FGA/g, 3P ≥2 3PA/g,
FT ≥1 FTA/g) so a 1-1 shooter can't top FG%.

### GOAT Rankings (`app/pages/3_GOAT_Rankings.py`)

A transparent career composite over **all league history** (one ladder per
league tab), not just this repo's collection window:

- **Data**: ESPN athlete career lines merged by `src/collector/history.py`
  through each league's full-history endpoints: 1,809 NBA players
  (career leaders + official-award winners + 1,382 collected players
  ≥41 GP, 2,562 honours across 20 award types) and 441 WNBA players
  (same recipe + 402 collected ≥41 GP, 807 honours across 15 award
  types), committed in the career sections of `data/dashboard_awards.json`
  (`dashboard_wnba_awards.json` for the WNBA) with the matching
  `..._players.json` carrying the same index for the Profile page. The
  source caption repeats the pool definition and the as-of stamp.
- **Formula** (printed verbatim above each ladder, every weight on
  screen, computed per league): **35%** production (each of career
  PTS/REB/AST/STL/BLK/3PM/+/- at its printed share, scored as a 50/50
  blend of career total and per-game rate vs the pool's best; a stat the
  career never had (impossible-zero totals, pre-1974 STL/BLK, pre-1980
  3PM, no collected-box-score game for +/-) is dropped from his blend
  with the rest rescaled; +/- comes only from this repo's collected box
  scores because ESPN's career statistics carry none -- so it starts with
  the window (2010-11 NBA, 2010 WNBA) and a negative career +/- scores
  zero, never negative credit), **30%** official honours (ESPN's award
  types at points per win: the NBA's 20, MVP 6.0 down to Sixth Man-tier
  0.5; the WNBA's own 15-type table), **25%** peak (best season's
  per-game impact), **10%** championships (title count vs the pool's
  most among qualified players: champion-season rows plus verified
  official-record counts -- for the WNBA that overlay fills the
  1997-2000 Comets and 2001-2002 Sparks titles ESPN's Finals-MVP detail
  omits). Each component normalizes 0–100 against the best qualified
  player; qualified at ≥82 career GP in both leagues.
- **Honours are ESPN's official award names only.** Championships
  score as their own bounded component (🏆×N, normalizing against the
  pool maximum so Russell's 11 sets the ceiling); a ringless career
  scores 0 there (a fact, not a gap), and a count no source can confirm
  drops out with the score rescaling. All-Star game selections don't
  exist in ESPN's awards API, so they're never shown or scored either.
- **Honest gaps.** A career ESPN can't fully cover shows `🏆 —` instead
  of a ring count and blanks seasons/peak with the reason (e.g. the
  champion index starts in 1970, or a career's season rows start too late
  to trust); a career the collector's window never covered drops the +/-
  input from the production blend (named in the row's "no data" line);
  a missing component drops out of the score and the result rescales
  over the weights that are available.
- Explicitly labeled **not an official NBA ranking** (the WNBA pane says
  "not an official WNBA ranking"). Each row carries honour chips
  heaviest-first (top 5, then "+N more") and the four component scores
  behind the headline number. Known ESPN quirks are captioned on the
  page: for the NBA, ABA/NBA totals merged, the mislabelled blocks
  category, late-starting pre-1977 season rows, and ESPN's missing
  rebounds for 11 pre-1974 legends patched from the verified official
  record on career lines and season rows (counted in the source
  caption); for the WNBA, careers whose season rows can't be trusted
  show blank peak/seasons.

### Player Profile (`app/pages/4_Player_Profile.py`)

Up to four players per league tab (defaults to that league's GOAT top
four; a `?player=` deep link preselects the name in every tab whose
index carries it, others honestly fall back), each section at full
width so nothing is squeezed or hidden:

- **Career cards** in a 2×2 grid as **2K-style collectible cards**:
  centred portrait (team-logo CSS fallback) over an uppercase name
  banner, seasons/debut meta, the headline **OVR /100** (the GOAT
  score rounded onto its documented scale -- components normalize 0-100
  against the pool's best and the weights sum to 100, so it needs no
  rescaling; only the ladder's top 25 at ≥82 career GP carry one, other
  careers show an honest dash), a tale-of-the-tape spec panel (RANK =
  GOAT rank, FIGHTS = career games, MVPs = official MVP wins, TITLES =
  championships, PTS and the window-only +/-, each an honest dash when
  the index carries nothing) and a REB/AST/honours footer, with 2K-style
  corners: position + jersey number top-left, every career team
  top-right. Both corners are free of extra requests -- position/jersey
  ride `history.py`'s identity fetch (stored on the entry, so pre-upgrade
  bundles refetch once) and the teams come from `seasons_log`'s own team
  column.
- **Official accolades** as a pivot: one row per ESPN award type at the
  GOAT formula's honour weight (sorted heaviest first), one column per
  selected player, cells = that player's wins with BLANK where he never
  won it (never a zero), and the height sized to the rows so every
  honour is visible -- no scrollbar hiding rows -- plus the "not in
  ESPN's API" honesty caption.
- **Career progression**: an interactive plotly chart with metrics
  PTS/REB/AST/STL/BLK/MIN/FG%/3P%, per-game or season totals
  (percentages ignore the toggle). The x-axis is a **categorical season
  axis** (plotly would otherwise parse `2003-04` as a date and tick every
  3 months), so every selected player's arc lines up per season. An
  **Axis** toggle switches that grid to **Career year**: each player's
  seasons numbered 1, 2, 3 ... from his first (by chronology, never the
  row order ESPN returned), so careers from different eras compare on one
  chart instead of sitting in non-overlapping decades. Hover carries
  season, team, GP and value for every player at once (the calendar
  season stays in the hover on the career grid); the legend toggles
  players off and on; drag to zoom, double-click to reset.
- Per-season rows come from ESPN's athlete statistics (`seasons_log` in
  `data/dashboard_players.json`, `data/dashboard_wnba_players.json`);
  seasons ESPN doesn't cover draw no
  point, and a caption names the affected players instead of
  interpolating. Headshots and the index itself follow the league
  (`/headshots/wnba/...` URLs in the WNBA pane).

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

Every file below exists once per league: the NBA's keep their historical
names, the WNBA's insert `_wnba` right after a `dashboard_` prefix
(`dashboard_teams.json` → `dashboard_wnba_teams.json`) or before the
extension when there is none (`history_cache.json` →
`history_cache_wnba.json`) -- one rule, `leagues.named_path()`, shared by
the collector that writes them and the loaders that read them (the
`data/races_wnba/`, `data/schedules_wnba/` and `data/postseason_wnba/`
directories are the same
rule applied to whole folders).

| File | Written by | Read by |
|---|---|---|
| `data/dashboard_teams.json` | `refresh_dashboard_fallbacks.py` (live) | Teams-source hero note, team labels |
| `data/dashboard_standings.json` | same (live, zero-record gated) | Standings tab |
| `data/schedules/{season}.json` | same (local `schedule.csv` files, one per season across all 17; the WNBA's under `data/schedules_wnba/`) | Schedule tab, KPI next tip-off |
| `data/postseason/{season}.json` | same (local `postseason.csv` files: playoffs + play-in, one per season with a bracket; the WNBA's under `data/postseason_wnba/`) | Schedule tab's Postseason section (its one consumer) |
| `data/dashboard_schedule.json` | same (thin season index: labels + game counts + current season) | Sidebar default season, schedule fallback messages |
| `data/dashboard_positions.json` | same (local position map) | Court View formation |
| `data/dashboard_awards.json` | same (local award math: every collected season's races/leaders, all-time boards, all-history GOAT ladder) | KPI scoring leader, Awards Ladder, Court View, All-Time Stats, GOAT Rankings |
| `data/dashboard_players.json` | same (`history.py` all-history index: careers, honours, GOAT ranks, position/jersey, per-season Finals MVP winners, official race winners) | Player Profile, Awards Ladder (Finals MVP tracker, official-vs-algorithm verdicts) |
| `data/races/{season}.json` | same (daily race snapshots: one per UTC day, written only while a race moves; a finished season freezes after its first) | Ladder movement arrows, MVP race trend |
| `data/processed/dashboard_leaderboards.json` | same (local totals + shooting splits) | committed artifact only; no page reads it since Season Leaders was removed |
| `models/metrics.json`, `models/proj_model.txt` | `train.py` | Sidebar Model & History mention (headline) |
