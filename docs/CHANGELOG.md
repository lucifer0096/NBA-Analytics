# Changelog

Reverse-chronological log of user-visible changes with the reasoning
behind each batch. Automated `data:` collector refreshes are omitted --
they carry no code change. Every batch below landed with its tests
green and CI green.

## 2026-10-24

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

- **Bracket-grouped postseason table** (round/series sub-headers) --
  needs the collector to persist round/series fields first; today's
  postseason files don't carry them.
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
