# Changelog

Reverse-chronological log of user-visible changes with the reasoning
behind each batch. Automated `data:` collector refreshes are omitted --
they carry no code change. Every batch below landed with its tests
green and CI green.

## 2026-10-24

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
- **Operational check**: confirm the game-night crons fire on their
  first scheduled window (01:40-06:40 and 12:20 UTC).
- Extend the "(N h ago)" age from the hero to the per-tab Source
  captions.
- Mobile / responsive visual QA at phone widths (Streamlit-native
  layout, unverified in a real browser).
- Investigate test-suite runtime drift (~58s -> 75-200s band; no
  failures).
- Accessibility / contrast audit of the custom CSS at default text
  sizes.
