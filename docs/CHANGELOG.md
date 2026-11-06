# Changelog

Reverse-chronological log of user-visible changes with the reasoning
behind each batch. Automated `data:` collector refreshes are omitted --
they carry no code change. Every batch below landed with its tests
green and CI green.

## 2026-11-07

### feat(awards): name the ESPN-vs-app split on the Awards Ladder

In-season the tab rendered two MVP ladders with near-identical rows and
no way to tell them apart at a glance -- the live ESPN tracker up top,
the committed app race further down. The labels now say which is which:

- The live sections are **"MVP progress tracker -- ESPN, live"** and
  **"ROY progress tracker -- ESPN, live"** (source + freshness in the
  header; placement unchanged -- they stay at the top of the tab, and
  past seasons still skip them entirely).
- The five committed ladders now sit under one group header,
  **"Calculated races -- the app's own math"** -- everything under it
  is computed here from collected box scores, nothing fetched live.
  Individual race labels (MVP race, ROY race, ...) are unchanged, so
  captions, trend links and docs keep their names.

README, DASHBOARD and DEPLOYMENT quote the labels verbatim; all three
updated in the same commit.

### feat(awards): MVP/ROY/MIP rank by Game Score -- the whole box score, not just PTS+REB+AST

The three impact races priced counting volume only (`PTS + 0.75·REB +
1.0·AST` per game): identical points read identical whether they came
on 12 or 24 shots, and steals, blocks, turnovers and fouls moved no
score. They now rank by **Hollinger Game Score** -- the published
box-score formula, implemented from one `GMSC_WEIGHTS` dict whose
weights also RENDER the on-screen caption (the goat_formula rule:
what's printed is what's computed):

- **MVP / Finals MVP**: Game Score per game x the same win-percentage
  multiplier (0.6x floor, 1.0x at a perfect record). The Finals
  calculator inherits by construction -- `finals_mvp_rows` IS `mvp_rows`
  over series aggregates -- and its caption is rendered from the same
  helper, so the two can't diverge.
- **ROY**: Game Score per game, still no team-record scaling (the award
  goes to the best rookie, not the best team's).
- **MIP**: year-over-year change in Game Score per **36 minutes**: a
  rotation bump alone (same totals over more minutes) now reads as what
  it is -- no improvement -- while genuine per-minute growth ranks.
  Seasons with no recorded minutes are skipped, never compared at an
  invented rate.
- The GOAT ladder's peak term keeps its original impact-per-game
  definition (its caption describes it in prose); DPOY and 6th-Man are
  untouched -- this batch changes exactly the three races it names.
- Aggregates now sum personal fouls too (`RACE_SUM_STATS`, Game Score's
  -0.4/foul term) without leaking a key into history's career lines.
  The live MVP/ROY trackers call the same row functions, so they inherit
  automatically, and `parse_athlete_stats` now returns the full Game
  Score input set (ftm/fta/oreb/dreb/stl/blk/pf/to -- each name verified
  present on ESPN's season-statistics payload, Nov 2026).
- Race history notes the switch honestly: today's snapshot is the first
  under the new formula, so each trend shows one visible step there
  (older snapshots keep the formula they were recorded under) and
  movement arrows can show one day of formula-induced movement.
- Both leagues' payloads were rebuilt locally the way cron runs it: NBA
  2026-27 MVP Luka 28.65 (Wemby #2), 2025-26 Jokic 25.63, WNBA 2026
  A'ja Wilson 20.58 -- plus the usual local-rebuild drift in the
  `dashboard_*` artifacts (including the leaderboard season, which the
  runner rebuilds as 2026-27 since raw/ is gitignored there); next cron
  reconciles.

### fix(injuries): the past-season box stops claiming a season is "being played"

Offseason edge found on review: ESPN's current-season label rolls past
the season the sidebar defaults to (WNBA in November: report label
2027, selected 2026), and the Injuries tab's past-season box then read
"covers the season being played (2027) only" while nothing was being
played. It now says the report is current-state only, names ESPN's
current label, and keeps the same honest empty. Docs: the `/injuries`
season-param finding (any `season=` is ignored -- verified Nov 2026,
byte-identical rows) logged in KNOWN-ISSUES with its guards, the new
file added to COLLECTOR's table, and the stale "not-yet-started
2026-27" clause dropped from the app-test module docstring.

## 2026-11-06

### fix(awards): the early-season scoring bar hid Luka -- Maxey topped the tile at 8 GP while the actual leader sat at 7

The ranking itself was never wrong (per-game rate, ties by total): the
QUALIFIER was. `min_games_for` floored every in-progress season at 8 GP,
so the 2026-27 leaders stayed empty until Nov 3, and on Nov 3-4 -- Luka
Doncic at 7 GP (38.5 PPG) while Tyrese Maxey had 8 -- the KPI tile read
**Maxey 29.1** as "Season scoring leader" with Luka silently excluded.
ESPN's own PPG list carried Luka throughout; our committed data shows
him jumping straight to #1 (38.5) the day he hit 8 GP (Nov 5).

- The bar now tracks the season for its first 16 team games: half the
  busiest team's games, rounded UP, floored at 1 -- `in_season_min_gp`,
  the same rule the live MVP/ROY trackers already used (opening night
  ranks whoever played; the caption always states the bar). From 16
  team games on it is the ORIGINAL formula, so every completed season
  keeps its byte-identical qualifier (82 -> 41, 66 -> 33, and the int()
  rounding unchanged: 73 -> 36).
- One rule feeds every committed race (MVP/DPOY/6th-Man/MIP/ROY), every
  stat-leader category and the on-screen "needs >=N GP" captions -- the
  committed ladder and the live tracker now agree bar-for-bar during
  the season (the old test that pinned the live bar strictly below the
  committed one asserts equality instead).
- This batch ships the rebuilt 2026-27 payload under the new bar (123
  box scores collected locally exactly the way the cron does: Luka #1
  at 38.5, Curry visible at 7 GP, Maxey back to #5), while every past
  season's payload is content-identical -- verified against HEAD: only
  2026-27 changed, 2025-26 keeps its 41-GP bar.

### feat(injuries): an Injuries tab per league -- ESPN's current report, live-first

The fifth home tab (between Standings and the Awards Ladder) shows
ESPN's current injury report: one table of Status | Player | Team |
Detail | Updated, sorted Out first by status severity then team and
player (unknown status text is kept and sorted last, never dropped),
with ESPN's short report note as the Detail and the report's own
timestamp rendered on the viewer's clock (missing date stays blank).

- `espn_api.get_injuries` + `parsing.parse_injuries` flatten the
  team-grouped `/injuries` payload (long comments dropped as table
  noise); a payload without the `injuries` field RAISES, so a shape
  change falls back instead of rendering "nobody is hurt".
- `refresh_dashboard_fallbacks` writes `data/dashboard_injuries.json`
  (WNBA: `_wnba`) on every run: a failed fetch or unrecognized shape
  keeps the committed report (an outage never blanks the tab), while
  ESPN's own empty answer -- the offseason state -- is written as such.
- The app loader is live-first (600s cache) with the committed file as
  the offline fallback, and since the endpoint has NO per-season
  history a past season selected in the sidebar says exactly that
  instead of wearing today's rows under its header.

### refactor(ui): a declutter pass over the Home tabs -- one fact per surface

Reviewed every rendered element of all five tabs (element dump first,
then the code) and removed what said the same thing twice:

- **Awards Ladder**: the wrapper caption ("Homegrown transparent
  metrics... Each race's caption states the exact formula") -- every
  race caption below already prints exactly that, verbatim; the
  headline caption's "-- the same source as the honours and the tracker
  below" clause and the verdict's "(the honours' own source)" -- the
  source is now named once per caption, not cross-referenced twice.
- **Finals MVP shown twice**: the selected season's row appeared in the
  headline AND as the tracker's first row -- the tracker now lists the
  seasons around the selected one (the headline owns its own), so a
  pending dash no longer renders twice for the current season either.
- **Court View**: "positions are ESPN's coarse G/F/C..." printed twice
  in adjacent captions (render caption + formation note) -- the
  formation note keeps only the formation, the optimizer provenance and
  the PG/SG -> G mapping; the WNBA variant's broken "src/model/'s"
  wording fixed on the way.
- **MIP empty state**: "it compares against 2025-26, which has no
  collected box scores in this build" contradicted the sidebar's own
  1,231-game inventory for 2025-26 -- it now says the refresh didn't
  collect them (the daily run gathers the current season).
- Schedule & Scores and Standings were reviewed and left alone: their
  Today-vs-Upcoming and headline-vs-table overlap is the featured/list
  pattern, not duplication.

## 2026-10-26

### feat(roy): the Rookie of the Year race -- full MVP parity on the Awards Ladder

The dashboard's flagship ladder had no homegrown ROY counterpart -- the
rookie award existed only as a GOAT-honours weight. Five surfaces now
mirror the MVP's, each honest about the one question box scores can't
answer: WHO is a rookie.

- **Collector**: `race_award_names.roy = "Rookie of the Year"` in both
  league configs -- `history.py`'s generic name mapping picks up every
  season's official ROY winner with zero parser change (ESPN award type
  35; the committed award cache already carries the full history).
- `awards.roy_rows` ranks exactly the given rookie set by pure impact
  per game, `(PTS + 0.75·REB + 1.0·AST)/GP` -- **no win-percentage
  scaling** (the award goes to the best rookie, not the best team's) --
  at the season's own games-played qualifier. `build_payload` gains a
  `rookies` parameter and only then writes the race: box scores can't
  tell a rookie from a ten-year vet, and first appearance in the
  collected window would crown 2010 call-ups.
- `refresh_dashboard_fallbacks` builds the all-history index BEFORE the
  season payloads and injects each season's start-year debuters
  (fresh history entries win over the committed index, which stays the
  fallback when history fails; no debut records anywhere ships the four
  box-score races and says why).
- The identity source needed a fix first: ESPN carries `debutYear` on
  NBA athlete profiles ONLY through the 2020 debut class (verified live
  Oct 2026 -- later profiles omit the key entirely, e.g. Flagg's), so
  every rookie pool came out empty. The rows-derived debut fallback now
  extends to the NBA (rows are keyed by the season's END year: first
  row 2004 = the 2003-04 debut, hence -1), complete-but-debut-less
  bundles refetch once in either league to pick it up, and the career
  trust check lands exactly on the derived boundary.
- **Awards Ladder**: headline now reads MVP → **ROY** → Finals MVP
  (honest pending rows until announced), plus the fifth race block with
  its verbatim formula, a dedicated **rookie-identity caption** (ESPN
  debut records; live tracker confirms with the draft class), the
  official-vs-algorithm verdict row, and a **ROY trend chart** over the
  daily snapshots with the same two-snapshot honesty and movement
  arrows as MVP.
- **Live tracker**: a **ROY progress tracker** beside the MVP's, same
  gate (only while the season is being played). ESPN's leaderboards
  pick the candidates; each candidate's athlete profile supplies the
  draft class (rookie = drafted in the season's START year -- the 2025
  draft plays 2025-26, the 2026 WNBA draft plays 2026; the draft
  endpoints 404 on both ESPN hosts, so the profile is the authoritative
  source) and the rendered row's name. Only confirmed rookies' own
  season totals feed the committed formula at the in-season qualifier,
  with no standings call. Undrafted first-year players (no draft year
  on ESPN) are skipped and said in the note, never guessed -- and
  rookies below the committed index's 41-GP gate (missing from the
  race's debut data until indexed) are covered here.

+11 tests (roy rows/season-start-year/payload units, the refresh's
three identity paths, the fifth official-winner pin -- 2025-26 Cooper
Flagg -- the cfg name guard, headline/trend/verdict/identity/live app
assertions, `race_meta`'s record-free line, the live tracker's
draft-class filter unit, and the debut fallback on BOTH league paths);
docs (README, DASHBOARD, COLLECTOR, DEPLOYMENT) updated to the
five-race surface.

## 2026-10-24

### feat(ui): the responsive pass -- small-screen audit + fixes, in code

A static CSS/code audit of every custom style (no browser available to
eyeball -- stated honestly, device QA stays in the backlog):

- the profile card's stat grid auto-fits
  (`repeat(auto-fit, minmax(84px, 1fr))`) instead of forcing three
  columns on a 360 px screen, and the name banner wraps long names;
- one `@media (max-width: 640px)` block trims fixed paddings (hero
  strip, section labels, metric tiles, race rows) and narrows the
  shot-chart cards -- the only fixed-width elements in the app;
- verified already-responsive, untouched: the hero/court/bench strips
  (`flex-wrap`), race rows (`min-width: 0` bodies), every table and
  chart (`width="stretch"` + native horizontal scroll), and the
  Streamlit-native stacking of `st.columns` and tabs;
- contrast verified against the pinned dark theme (#E8ECF1 on
  #0B0F17): the smallest captions at 0.66 opacity compute ≈7:1 --
  no opacity churn needed.

+1 test pinning the rules; DASHBOARD.md's UI-pass section documents
the small-screen behaviour.

### feat(postseason): bracket-grouped playoff table -- round data off ESPN's per-game notes

The Schedule tab's postseason section now reads like a bracket: one
bold-labeled table per round in bracket order (Play-In first, opening
rounds with East before West, Semifinals, Conference Finals, Finals),
from a new `round` field every playoff row carries.

- **Collector**: `parse_schedule` captures ESPN's per-game round note
  (`parsing.round_label` -- the segment before the first spaced dash,
  en dashes included: `'East 1st Round - Game 3'` -> `'East 1st
  Round'`) as an **absent key** when no note exists, keeping the
  regular schedules tree byte-identical; snapshot's two CSV writers
  take an ordered key union so mixed rows can't raise.
- **Data**: `backfill_postseason_rounds.py` annotated every committed
  bracket in one pass -- **1,702 games across 33 seasons (16 NBA /
  17 WNBA), 0 unknown** -- via 452 ESPN calls (team schedules
  `seasontype=3`; the scoreboard for play-in games, which answer under
  no seasontype), then the normal refresh path rewrote the files. A
  second local-only pass corrected one en-dash-tailed label with zero
  calls; the script is committed as idempotent maintenance (zero
  calls on annotated seasons).
- **App**: `round_display` title-cases the shouting old spellings
  ('EASTERN CONFERENCE 1st ROUND' -> 'Eastern Conference 1st Round')
  with acronyms intact; `round_rank` orders the bracket across every
  era's wording -- semifinals check before finals (the substring
  hides inside), bare 'East Finals' ranks as a conference's so the
  league's Finals stays last, the WNBA's four-round era sequences
  First/Second Round; unlabelled rows group under 'Round not
  recorded', files with no round data keep the plain table, and the
  CSV export gains the Round column.
- Tests: +7 (round labels across eras and dash styles, the
  absent-key contract, backfill helpers, rank ordering, grouped
  rendering with exact row conservation). **222 passed**, ruff clean;
  DASHBOARD.md + COLLECTOR.md updated.

### feat(app): the age rides every Source caption

`shared.source_caption()` is now the single Source line every section
ends with: the freshness note localized to the viewer's clock **and
aged** off the same stamp -- "(2 h ago)" had been hero-only. The
Finals-MVP composite caption keeps its inline form and gains the same
suffix. +1 test (215 total).

### fix(app): the live Status cell shows its quarter -- ESPN's scoreboard period is a bare int

The clock batch read the period only in the `{'displayValue': 'Q3'}`
dict shape; a probe against tonight's live slate showed the LIVE
scoreboard answers `period: 0` / `3` as a **bare int** (plus a
formatted `displayClock`), so live cells fell back to bare time --
no quarter. `shared._period_label()` now resolves both shapes ("Q3"
from the int, "OT"/"2OT" from 5+, None from the pre-game 0) and the
parser prefers ESPN's formatted `displayClock`. Verified against the
live payload (2026-10-24); the parser test carries the real live
shape plus overtime and the dict shape.

### `ad090cd` feat(app): live clock in the status, numeric tables, Schedule-first landing

*The app shows what it knows, as clocks and numbers.*

- A live game's Status cell carries its **real period and clock**
  ("Q3 4:32"): `load_scoreboard` keeps ESPN's `status.period` /
  `status.clock` raw; `shared.status_detail()` composes them at
  display time. Any other state keeps the human word; a missing period
  or clock falls back to the word -- never an invented time.
- **Scores, GB and Win% render as numbers**: right-aligned columns,
  numerically sorting headers ("12.5" no longer sorts before "2.5");
  ESPN's "-" GB leader mark becomes the 0.0 it means; GB joins the
  on-screen standings table (previously CSV-only). Blanks stay blank;
  CSV exports keep the raw strings.
- The **today slate captions its source** ("Live (60s cache)") like
  every other section -- the live note used to surface only when the
  slate was empty.
- **Game detail renders in-progress games' live box score and
  play-by-play**, not just finals: the summary endpoint has shipped
  the live box all along; the final-only gate was hiding it. The
  matchup line carries the status word so a half-played game never
  reads final.
- **Schedule & Scores leads the sub-tabs** -- the landing view is
  today's slate, not the standings (`?tab=` still reorders; three
  pinned tab-order tests updated deliberately).
- Fixture picker labels use the "·" separator the results table
  already had; hero freshness notes end with their **age** ("(2 h
  ago)" -- `freshness_age`, computed at render from the same stamp the
  note displays).

Tests: 214 passed, 1 skipped (+3 new: status composition, freshness
age, live in-progress game detail).

### `c313bb8` polish(app): UI neatness pass -- status words, hero-first layout, quieter charts

*Less plumbing on screen.*

- `shared.status_label()`: raw `STATUS_*` enums became words
  (Scheduled / In progress / Final / Postponed / ...) on today's
  slate, the postseason table (which used to show "In_Progress") and
  the game-detail line. CSV exports keep the enum.
- **Hero line above the KPI strip**: season pill, the reader's local
  "Today (NZDT): date" pill (moved out of the sidebar, where it was
  easy to miss), and the consolidated freshness -- context before
  content.
- "Next tip-off" KPI shows the local **time**, not just the day.
- "No games on {today}" became an `st.info` block like every other
  empty state.
- One **merged inventory expander** for both leagues (grand total,
  per-league groups) on the home sidebar and all three left-nav pages.
- Charts render through `shared.CHART_CONFIG`: no floating Plotly
  modebar, no logo.
- Scoring-leader PPG as a muted delta under the name
  (`delta_color="off"`); "LAL @ BOS · 105:98" results separator.

Tests: 211 passed, 1 skipped (+1 new: status-label words).

### `9d0ec09` fix(app): ESPN's string scoreboard scores -- today's slate no longer blanks

The actual cause of "the schedule for today is not showing": ESPN now
ships the scoreboard competitor `score` as a bare `"125"` string
(schedules/summaries still carry `{'displayValue': '125'}`), the old
`.get("displayValue")` raised `AttributeError` inside the per-event
loop, the honest-empty handler swallowed it, and the tab reported
"Offline fallback" **while ESPN was answering**. `_score_display`
accepts both shapes; `_game_started` keeps a `state: pre` game's
placeholder "0" off the screen (unplayed = blank, the schedule
tables' own rule). Live-verified: 12 events parsed, finals with
scores, unstarted games blank.

### `c91c22f` fix(app): every game time in the viewer's local timezone

- `viewer_tz()` reads `st.context.timezone` (bare mode -> UTC, tests
  stay deterministic); the zone name is part of the today-scoreboard
  cache key, because Streamlit's cache is shared across sessions and
  one viewer's clock may not leak to another.
- **Today's slate follows the READER's calendar day**: the three
  covering ESPN buckets are fetched (ESPN filters `dates=` by US
  date) and kept to tips inside the local day -- a New Zealand
  evening's games finally appear, under the US's previous date.
- Every displayed stamp localizes (`local_dates`, `local_stamp`,
  `localize_note` at render, never inside a cache); zoned "Tip-off
  (NZDT)" headers; CSV exports keep raw UTC (data, not display).

Tests: 210 passed (+7 new).

### `61a57f2` fix(collector): game-night refresh cadence + stamp-stable writes

- Game-night crons (`40 1..6 * * *`, `20 12 * * *` UTC); workflow
  renamed "Data collector (game-night refreshes)".
- **Stamp-stable writes**: `STAMP_KEYS` gating, snapshot gates and the
  `build_career` as-of removal mean a rebuild that changes no content
  writes no file -- quiet diffs, no timestamp-only commit spam.

## Next up (designed, not yet implemented)

- **Series sub-headers on the bracket** (e.g. "BOS leads 3-2" under
  each round group) -- rounds shipped 2026-10-24; series records would
  need ESPN summary calls per playoff game.
- Extend the "(N h ago)" age from the hero to the per-tab Source
  captions.
- Mobile / responsive visual QA at phone widths (Streamlit-native
  layout, unverified in a real browser).
- Investigate test-suite runtime drift (~58s -> 75-200s band; no
  failures).
- Accessibility / contrast audit of the custom CSS at default text
  sizes.
