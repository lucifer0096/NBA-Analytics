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
  View, All-Time Stats, GOAT Rankings) say `Computed from collected box
  scores — as of <stamp>` instead of ever implying "live". The Awards
  Ladder and Court View follow the sidebar's season selector across **every
  collected season (2010-11 → present)**; selecting a season with no
  collected games (e.g. 2026-27 pre-tip-off) falls back to the nearest
  season at or before it and the caption says exactly which one is on
  screen.
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
4. **Awards Ladder** — the selected season's MVP / DPOY / 6th Man / MIP
   races in two columns, then a stat-leaders strip behind a
   PTS/REB/AST/STL/BLK/**3PM** radio (top-10 per-game rates, each row
   carrying the FG/3P made-attempt splits + FG%). Every row is a real
   headshot over a team-logo CSS fallback with the race's stat line and
   score. Computed locally from the collected box scores
   (`src/collector/awards.py`, committed as `data/dashboard_awards.json`,
   keyed by season for every collected year 2010-11 → present); each
   race's caption prints its exact formula verbatim plus the "not official
   NBA voting" disclaimer, and an empty MIP race says which prior season
   is missing instead of inventing a winner.
5. **All-Time Stats** — career totals across **all** collected seasons
   (2010-11 → 2025-26, the window stated honestly: the collector starts at
   2010-11, so this is not full NBA history) behind a rank-by radio over
   counting boards (PTS/REB/AST/STL/BLK/3PM) and efficiency boards
   (FG%/3P%/FT%/eFG%/TS%). The table shows the full parameter set: GP,
   MIN, PTS, REB/ORB/DREB, AST, STL, BLK, TO, FG/3P/FT made-attempt
   splits, and all five percentages. Qualified at ≥41 career GP; % boards
   additionally require an attempts floor (≥5 FGA/g, 3P ≥2 3PA/g,
   FT ≥1 FTA/g) so a 1-1 shooter can't top FG%.
6. **GOAT Rankings** — a transparent career composite: **40%** production
   (career PTS/REB/AST/STL/BLK/3PM totals vs the window's best), **35%**
   award-race résumé (top-10 finishes worth 11−rank points, weighted
   MVP ×1.0 / DPOY ×0.8 / 6th ×0.5 / MIP ×0.4 across every collected
   season), **25%** peak (best season's per-game impact) — each component
   normalized 0–100, qualified at ≥82 career games. The exact formula
   prints verbatim above the ladder, the 🏆/🛡/🪑/📈 title chips count
   rank-1 finishes in this repo's homegrown races (explicitly not official
   NBA awards), and every row shows the three component scores behind the
   headline number.

## Model & History (`app/pages/1_Model_and_History.py`)

1. **Model Performance** — hero with validation/holdout seasons, MAE
   comparison chart + table (single-stage vs two-stage vs naive rolling-5),
   an explicit success/warning banner on whether the model actually beat
   naive (shown deliberately: a projection that can't beat last-5-means
   isn't worth trusting), played-vs-DNP error-budget split, play-classifier
   AUC.
2. **Season Leaders** — top-25 from the committed leaderboards, rank-by
   radio (fantasy pts / pts / reb / ast / 3PM), 🥇🥈🥉 podium styling, and
   shooting columns: FG and 3P made-attempt splits plus FG%, 3P%, eFG%,
   TS% (computed from the collected box scores, not copied anywhere).
3. **Court View** — the selected stat's leaders on a CSS-only hardwood court
   (gradient markings, no images): rank order fills a 2 G / 2 F / 1 C
   formation from the committed roster map, C row first under the basket;
   overflow and unmapped positions land on a bench strip rather than being
   forced into a slot. Follows the sidebar's season selector (every
   collected season). Hovering a card shows the player's full per-game
   line **including the FG/3P shooting splits** (CSS-only tooltip). The
   PuLP optimizer remains a backend component (`src/model/optimizer.py` +
   its tests) — this tab changed, it didn't.

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
| `data/dashboard_awards.json` | same (local award math, every collected season + career sections) | KPI scoring leader, Awards Ladder, Court View, All-Time Stats, GOAT Rankings |
| `data/processed/dashboard_leaderboards.json` | same (local totals + shooting splits) | Season Leaders |
| `models/metrics.json`, `models/proj_model.txt` | `train.py` | Model Performance tab |
