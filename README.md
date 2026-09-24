# NBA Analytics

A platform-agnostic NBA fantasy projection model, lineup optimizer, and dashboard — built on **ESPN's free public APIs** (no auth, no API key), with a deliberate modeling window of **2010-11 → present**. Structured as the basketball sibling of this author's [FPL-Analytics](https://github.com/lucifer0096/FPL-Analytics): collector → leak-free features → LightGBM projections → PuLP optimizer → Streamlit dashboard, with offline-first tests and daily GitHub Actions automation.

**Why no fantasy platform?** This project deliberately targets no Yahoo/ESPN/Sorare league: "fantasy points" here are a *configurable weighted sum* of a box score (`src/model/scoring.py`), and the optimizer encodes the *structure* every points-league lineup shares (slot types, one player per slot) rather than one platform's salary cap. Point the weights at your league's scoring and the same pipeline serves any of them.

**Why ESPN's APIs?** Researched against the alternatives first (see [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md)): `stats.nba.com` is free and deep but blocks whole networks (verified unreachable here, including via proxy), basketball-reference blocks bots, balldontlie now requires a registered key, and NBA's own CDNs 403. ESPN's `site.api.espn.com` family needs nothing, reaches **1995-96** (verified on a real fixture), and its per-game summary endpoint returns full box scores — 30 schedule calls + one summary call per game covers a whole season.

## Quickstart

```bash
pip install -r requirements.txt   # exact tested pins — Python 3.12+ (3.14 matches CI)

streamlit run app/app.py          # dashboard; runs offline from committed data/dashboard_* fallbacks

python src/collector/snapshot.py --backfill          # one-off: 2010-11 → latest season, newest first
python src/collector/snapshot.py                     # daily: current season, incremental (resumable)

python src/model/load_historical.py                  # raw box scores → data/processed/historical_games.parquet
python src/model/features.py                         # → features.parquet (leak-free feature table)
python src/model/train.py                            # → models/proj_model.txt + metrics.json

pytest -q                         # deterministic tests + offline dashboard render tests (~10s, no network)
pytest -m live                    # live ESPN-API checks (needs network)
ruff check app src                # lint — the same rule set CI gates on
```

Dependency layout mirrors FPL-Analytics: `requirements.txt` = exact pins everything installs, `requirements.in` = ranges to re-resolve from, `requirements-dev.txt` = dev-only lint. R users: see [r/](r/) for the EDA scripts (Python owns the production pipeline; R explores it).

## Status

**Stage 1 (done): data collector.** A thin client for ESPN's public APIs (`teams`, `schedule`, `summary`, `standings`, `roster`, `scoreboard`) that snapshots each season's schedule and every finalized game's box score to `data/raw/`, idempotently (the filesystem is the state — an already-fetched game is never refetched), rate-limited, and resumable. Runs daily via GitHub Actions — see [docs/COLLECTOR.md](docs/COLLECTOR.md).

**Stage 2 (done): projection model.** Player-game rows unified 2010-11 → present into one table (one schema the whole window — no cross-era reconciliation needed, unlike the FPL loader), leak-free rolling form/availability/rest/matchup features, chronological train/validation/holdout split (2024-25 validation, 2025-26 untouched), LightGBM single-stage + two-stage comparison against a naive rolling-5 baseline. See [docs/MODELING.md](docs/MODELING.md).

**Stage 3 (done): lineup optimizer.** PuLP MILP over a projected player pool: 2 G / 2 F / 1 C / 2 UTIL slots with structural position eligibility, provably optimal answers, honest infeasibility errors. Verified against synthetic pools with known optima. See [docs/MODELING.md](docs/MODELING.md#lineup-optimizer).

**Stage 4 (done): dashboard.** Two-page Streamlit app — Standings / Schedule & Scores / Projections on Home, Model Performance / Season Leaders / Lineup Optimizer on page 2. Live-first from ESPN with 60s cache, committed `data/dashboard_*` fallbacks for offline deploys, and freshness captions that always say which they're showing. See [docs/DASHBOARD.md](docs/DASHBOARD.md).

**Stage 5 (in progress): live season.** The 2026-27 schedule (1,200 games) is captured; the daily workflow will collect its box scores as games finalize from late October. The historical backfill (2010-11 → 2025-26, ~19,700 games) runs newest-first so training-ready seasons land first.

## Deploying to Streamlit Community Cloud

The app is designed to deploy as-is from a fresh clone — it runs entirely
from the committed `data/dashboard_*` fallbacks when it can't reach the
collector's raw data (which Streamlit Cloud never has, since `data/raw/` is
gitignored):

1. Push this repo to GitHub (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
   for the exact commands if you're pushing it for the first time).
2. On [share.streamlit.io](https://share.streamlit.io): **New app** →
   pick the repo → **Main file path** `app/app.py` → **Deploy**.
   Python version follows `requirements.txt` (3.14, same as CI).
3. The theme ships with the repo (`.streamlit/config.toml`) — no Cloud-side
   configuration needed.

What works on a deployed instance: Standings/Schedule (live ESPN first,
committed fallback second), Season Leaders, and the Optimizer over the
committed projection window. What needs a data machine: historical raw data
and retraining — the daily GitHub Actions workflow
(`.github/workflows/collector.yml`) refreshes the committed fallback files
(and projections, once `models/proj_model.txt` exists) so the deployed app
stays current without ever seeing `data/raw/`.

## Project Structure

```text
NBA-Analytics/
├── src/
│   ├── collector/
│   │   ├── espn_api.py                   # Thin client + season-param helpers (ESPN season = END year)
│   │   ├── parsing.py                    # Payload → flat rows (pure, fixture-pinned)
│   │   ├── snapshot.py                   # Idempotent/resumable season snapshots (--backfill/--check-only)
│   │   ├── refresh_dashboard_fallbacks.py# Stable data/dashboard_* copies for offline deploys
│   │   ├── fixtures/                     # Real recorded payloads (incl. a 1995-96 game)
│   │   ├── test_espn_api.py              # Parsing pinned against fixtures + opt-in live tests
│   │   └── test_snapshot.py              # State decisions: what's fetched, skipped, targeted
│   └── model/
│       ├── scoring.py                    # Configurable fantasy weights (the "platform" is here)
│       ├── load_historical.py            # data/raw/* → one training table (+ positions, team scores)
│       ├── features.py                   # Leak-free rolling form/rest/matchup/baseline features
│       ├── train.py                      # Chronological split, LightGBM, metrics.json
│       ├── predict.py                    # Score history rows + build upcoming-game projections
│       ├── optimizer.py                  # PuLP best-lineup (G/F/C/UTIL slots)
│       └── test_model.py                 # Scoring, leakage guarantees, split, projections, optimizer
├── app/
│   ├── app.py                            # Home: Standings, Schedule & Scores, Projections
│   ├── shared.py                         # Live-first loaders with committed fallbacks + age notes
│   ├── test_app_offline.py               # AppTest renders with EVERY ESPN call forced to fail
│   └── pages/1_Model_and_History.py      # Model Performance, Season Leaders, Lineup Optimizer
├── r/                                    # EDA in R (arrow reads the same parquet)
├── docs/
│   ├── COLLECTOR.md                      # Endpoints, files written, scheduling, resumability
│   ├── MODELING.md                       # Data, features, training, optimizer internals
│   ├── DASHBOARD.md                      # Tab-by-tab docs and design rationale
│   └── KNOWN-ISSUES.md                   # Dated investigation log (data-source selection, WAF, API quirks)
├── data/
│   ├── raw/                              # Gitignored — regenerates from the collector
│   ├── processed/                        # Gitignored except dashboard_leaderboards.json
│   └── dashboard_*.json                  # Committed offline fallbacks (regenerated daily by CI)
├── models/                               # Gitignored except proj_model.txt + metrics.json
├── requirements.in / .txt / -dev.txt     # Ranges / exact pins / lint-only
├── pytest.ini                            # Default run = no network (`-m "not live"`)
├── ruff.toml                             # Pinned lint rules CI gates on
└── .github/workflows/                    # CI + daily collector
```

## Data Sources

- **ESPN `site.api.espn.com`** (free, no auth): `teams`, per-team `schedule?season={end_year}&seasontype=2`, per-game `summary` (full box score — verified back to 1995-96), `scoreboard?dates=`.
- **ESPN `site.web.api.espn.com`**: `v2 .../standings` (the `site.api` standings path only returns a link stub), `common/v3 .../roster` (player positions — **season param ignored**, always current roster; see KNOWN-ISSUES).
- **Season parameter gotcha**: ESPN's `season=2011` means *2010-11* (the year it ends). `espn_api.season_param()` / `season_label()` are the only place that arithmetic should ever live.
- **Window**: modeling focuses on 2010-11 → present. Pre-2010 data exists in the API (the repo's fixtures prove it) and `--from/--to` can reach it, but it is not backfilled by default — different era, diminishing returns for projection features.
- Regular season only (`seasontype=2`): fantasy leagues play regular seasons; playoffs are deliberately out of scope.

## Running the Collector

`pip install -r requirements.txt`, then `python src/collector/snapshot.py --backfill` once, and `python src/collector/snapshot.py` for incremental refreshes. Everything the collector writes, the rate-limit/retry behavior, and the daily automation: **[docs/COLLECTOR.md](docs/COLLECTOR.md)**.

## Projection Model

Leak-free feature engineering, the chronological split, single- vs two-stage architecture, and the naive baseline comparison: **[docs/MODELING.md](docs/MODELING.md)**.

## Lineup Optimizer

Slot structure, eligibility rules, infeasibility handling, and optimality tests: **[docs/MODELING.md](docs/MODELING.md#lineup-optimizer)**.

## Dashboard

**`streamlit run app/app.py`** — two pages, live-first with offline fallbacks, freshness stated honestly on every tab: **[docs/DASHBOARD.md](docs/DASHBOARD.md)**.

## Known Issues Found & Fixed

The investigation log — ESPN WAF blocking User-Agents, the standings link-stub, the roster endpoint ignoring `season`, box-score vs schedule score shape differences, and more — each with the evidence and the permanent guard left behind: **[docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md)**.

## Future Improvements

- Historical player positions: ESPN's roster endpoint is current-only (verified), so ~retired players' rows carry `position=UNK`. Options researched: per-athlete bio calls (~2k+ calls, one per unique historical player) or box-score lineup inference. Position is deliberately *not* a model feature (its missingness would encode row era) — only the optimizer uses it, and only for current players.
- Per-category projections for 9-category leagues: score each stat separately (`scoring.py` already exposes the category list) and let the drafter weight categories, since category value depends on the rest of the lineup — an additive "9-cat points" target would be a modeling lie.
- Salary-cap optimizer variant (DraftKings-style): the MILP already separates objective from slot structure; adding a cost column and budget constraint is the natural extension once a cost source (a platform or an ADP proxy) is chosen.
- Injury/outcome signal: same lesson as FPL-Analytics — the gap to naive baselines concentrates in did-not-play rows, where historical stats can't see a coach's game-time decision. A real availability feed is the lever, not more box-score history.
- Retrain once 2026-27 box scores accumulate (validation window deliberately fixed at 2024-25 so early-season re-runs stay comparable).
- Wire `predict.project_upcoming()` into the dashboard's live path when running where raw data exists (today the Projections tab serves the committed fallback everywhere; the refresh script computes it where the data lives).
