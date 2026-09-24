# The Dashboard

**Run**: `streamlit run app/app.py` — two pages, dark theme shipped in
`.streamlit/config.toml`.

## Design contract

- **Live-first, fallback-second.** Every loader in `app/shared.py` tries the
  free ESPN API first (60s `st.cache_data` TTL) and falls back to the
  committed `data/dashboard_*` file only when the call fails — so the same
  build renders correctly on a data machine, on Streamlit Cloud (no raw
  data, sometimes no direct ESPN reach), and fully offline (the test
  suite's condition).
- **Honest freshness.** Every tab shows a caption from
  `data_age_note()`: `Live (60s cache)` vs `Offline fallback — data as of
  <_generated_utc>`. Nothing implies freshness it doesn't have; when the
  fallback carries a different season than requested (e.g. 2025-26
  standings while 2026-27 hasn't tipped off), the caption says exactly
  which season is on screen. Tabs with no live API (Awards Ladder, Court
  View) say `Computed from collected box scores — as of <stamp>` instead of
  ever implying "live".
- **Offline is the tested state.** `app/test_app_offline.py` forces *every*
  ESPN call to fail (monkeypatches `espn_api._get_json`) and asserts both
  pages still render all their tabs — the deploy condition is the default
  test condition.

## Home (`app/app.py`)

1. **KPI strip** — four glass metric cards: games collected (final / total),
   next tip-off date, season scoring leader (per-game points from the
   committed awards payload), and validation MAE vs the naive baseline.
2. **Standings** — conference tables, W-L combined, Win%, seed, streak,
   PF/g, PA/g; playoff (🟢) and play-in (🟡) zone glyphs appear only when
   the table has real records (preseason all-zero tables get no marks).
3. **Schedule & Scores** — live scoreboard for *today* (empty + explanatory
   caption in the offseason, which is the normal Oct–Jun-less state), then
   the selected season's 15 most recent results and next 15 fixtures from
   the collector's schedule.
4. **Awards Ladder** — MVP / DPOY / 6th Man / MIP races in two columns, then
   a stat-leaders strip behind a PTS/REB/AST/STL/BLK radio (top-10
   per-game rates). Every row is a real headshot over a team-logo CSS
   fallback with the race's stat line and score. Computed locally from the
   collected box scores (`src/collector/awards.py`, committed as
   `data/dashboard_awards.json`); each race's caption prints its exact
   formula verbatim plus the "not official NBA voting" disclaimer, and an
   empty MIP race says which prior season is missing instead of inventing
   a winner.

## Model & History (`app/pages/1_Model_and_History.py`)

1. **Model Performance** — hero with validation/holdout seasons, MAE
   comparison chart + table (single-stage vs two-stage vs naive rolling-5),
   an explicit success/warning banner on whether the model actually beat
   naive (shown deliberately: a projection that can't beat last-5-means
   isn't worth trusting), played-vs-DNP error-budget split, play-classifier
   AUC.
2. **Season Leaders** — top-25 from the committed leaderboards, rank-by
   radio (fantasy pts / pts / reb / ast), 🥇🥈🥉 podium styling.
3. **Court View** — the selected stat's leaders on a CSS-only hardwood court
   (gradient markings, no images): rank order fills a 2 G / 2 F / 1 C
   formation from the committed roster map, C row first under the basket;
   overflow and unmapped positions land on a bench strip rather than being
   forced into a slot. Hovering a card shows the player's full per-game
   line (CSS-only tooltip). The PuLP optimizer remains a backend component
   (`src/model/optimizer.py` + its tests) — this tab changed, it didn't.

## UI pass (presentation layer)

All styling is one CSS block in `shared.inject_css()` — CSS-only, no JS:

- accent-gradient page title, hero strip with season/data-age pills,
- glass metric cards, accent-underlined tabs, uppercase section labels,
- slot chips, award-race/leader rows, and court cards whose stat tooltips
  are pure CSS (`:hover`) — no JS anywhere, including the image fallback
  (headshot `<img>` over a team-logo `background-image`, so a CDN 404 just
  shows the logo);
- every color derived from Streamlit's own theme tokens
  (`--primary-color`, `--secondary-background-color`, `--text-color`), so a
  theme change can't break contrast;
- all transitions/animations disabled under
  `prefers-reduced-motion: reduce`.

## Fallback files the pages read

| File | Written by | Read by |
|---|---|---|
| `data/dashboard_teams.json` | `refresh_dashboard_fallbacks.py` (live) | Teams-source hero note, team labels |
| `data/dashboard_standings.json` | same (live, zero-record gated) | Standings tab |
| `data/dashboard_schedule.json` | same (local `schedule.csv`) | KPI next tip-off, Schedule tab |
| `data/dashboard_positions.json` | same (local position map) | Court View formation |
| `data/dashboard_awards.json` | same (local award math) | KPI scoring leader, Awards Ladder, Court View |
| `data/processed/dashboard_leaderboards.json` | same (local totals) | Season Leaders |
| `models/metrics.json`, `models/proj_model.txt` | `train.py` | Model Performance tab |
