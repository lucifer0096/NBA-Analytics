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
  which season is on screen.
- **Offline is the tested state.** `app/test_app_offline.py` forces *every*
  ESPN call to fail (monkeypatches `espn_api._get_json`) and asserts both
  pages still render all their tabs — the deploy condition is the default
  test condition.

## Home (`app/app.py`)

1. **KPI strip** — four glass metric cards: games collected (final / total),
   next tip-off date, season fantasy leader (from committed leaderboards),
   and validation MAE vs the naive baseline.
2. **Standings** — conference tables, W-L combined, Win%, seed, streak,
   PF/g, PA/g; playoff (🟢) and play-in (🟡) zone glyphs appear only when
   the table has real records (preseason all-zero tables get no marks).
3. **Schedule & Scores** — live scoreboard for *today* (empty + explanatory
   caption in the offseason, which is the normal Oct–Jun-less state), then
   the selected season's 15 most recent results and next 15 fixtures from
   the collector's schedule.
4. **Projections** — per-game-day table of model-projected fantasy points
   with player names/team abbrevs (joined in `load_projections` from the
   committed position + team files, since raw history isn't on the deploy
   box) and a progress-bar column for the projection itself.

## Model & History (`app/pages/1_Model_and_History.py`)

1. **Model Performance** — hero with validation/holdout seasons, MAE
   comparison chart + table (single-stage vs two-stage vs naive rolling-5),
   an explicit success/warning banner on whether the model actually beat
   naive (shown deliberately: a projection that can't beat last-5-means
   isn't worth trusting), played-vs-DNP error-budget split, play-classifier
   AUC.
2. **Season Leaders** — top-25 from the committed leaderboards, rank-by
   radio (fantasy pts / pts / reb / ast), 🥇🥈🥉 podium styling.
3. **Lineup Optimizer** — pick a game day + team filter + pool size, then
   `optimizer.best_lineup()` solves the MILP; results render as
   slot-colored player cards (G blue / F green / C orange / UTIL gold) with
   a big optimal-total readout. Pool candidates sit in an expander; genuine
   infeasibility surfaces `InfeasiblePool`'s reason instead of a blank.

## UI pass (presentation layer)

All styling is one CSS block in `shared.inject_css()` — CSS-only, no JS:

- accent-gradient page title, hero strip with season/data-age pills,
- glass metric cards, accent-underlined tabs, uppercase section labels,
- slot chips + player cards for the optimizer,
- every color derived from Streamlit's own theme tokens
  (`--primary-color`, `--secondary-background-color`, `--text-color`), so a
  theme change can't break contrast;
- all transitions/animations disabled under
  `prefers-reduced-motion: reduce`.

## Fallback files the pages read

| File | Written by | Read by |
|---|---|---|
| `data/dashboard_teams.json` | `refresh_dashboard_fallbacks.py` (live) | KPI/team labels, projection team abbrevs |
| `data/dashboard_standings.json` | same (live, zero-record gated) | Standings tab |
| `data/dashboard_schedule.json` | same (local `schedule.csv`) | KPI next tip-off, Schedule tab |
| `data/dashboard_positions.json` | same (local position map) | Optimizer pool |
| `data/dashboard_projections.json` | same (model, 21-day window) | Projections tab, Optimizer |
| `data/processed/dashboard_leaderboards.json` | same (local totals) | KPI leader, Season Leaders |
| `models/metrics.json`, `models/proj_model.txt` | `train.py` | Model Performance tab |
