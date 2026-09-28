# NBA Analytics

An NBA **and WNBA** analytics platform built on **ESPN's free public APIs** (no auth, no API key): a dual-league collector that snapshots every finalized game's box score (**NBA 2010-11 → present, WNBA 2010 → present**) feeding a Streamlit dashboard -- standings, schedules and scores, awards ladders, all-time boards, GOAT rankings and player profiles, every page switching between the two leagues -- plus a projection model (LightGBM over leak-free features; the target is a *configurable weighted sum* of a box score, `src/model/scoring.py`) and a PuLP lineup optimizer as backend components, with offline-first tests and daily GitHub Actions automation. Structured as the basketball sibling of this author's [FPL-Analytics](https://github.com/lucifer0096/FPL-Analytics): collector → leak-free features → projections → optimizer → dashboard.

**Why ESPN's APIs?** Researched against the alternatives first (see [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md)): `stats.nba.com` is free and deep but blocks whole networks (verified unreachable here, including via proxy), basketball-reference blocks bots, balldontlie now requires a registered key, and NBA's own CDNs 403. ESPN's `site.api.espn.com` family needs nothing, reaches **1995-96** (verified on a real fixture), and its per-game summary endpoint returns full box scores: 30 schedule calls + one summary call per game covers a whole season.

## Quickstart

```bash
pip install -r requirements.txt   # exact tested pins, Python 3.12+ (3.14 matches CI)

streamlit run app/app.py          # dashboard; runs offline from committed data/dashboard_* fallbacks

python src/collector/snapshot.py --backfill          # one-off: 2010-11 → latest season, newest first
python src/collector/snapshot.py                     # daily: current season, incremental (resumable)
python src/collector/snapshot.py --backfill --league wnba   # same for the WNBA (2010 → current)
python src/collector/refresh_dashboard_fallbacks.py --league nba wnba  # dashboard fallbacks, both leagues

python src/model/load_historical.py                  # raw box scores → data/processed/historical_games.parquet
python src/model/features.py                         # → features.parquet (leak-free feature table)
python src/model/train.py                            # → models/proj_model.txt + metrics.json

pytest -q                         # deterministic tests + offline dashboard render tests (~10s, no network)
pytest -m live                    # live ESPN-API checks (needs network)
ruff check app src                # lint, the same rule set CI gates on
```

Dependency layout mirrors FPL-Analytics: `requirements.txt` = exact pins everything installs, `requirements.in` = ranges to re-resolve from, `requirements-dev.txt` = dev-only lint. R users: see [r/](r/) for the EDA scripts (Python owns the production pipeline; R explores it).

## Status

**Stage 1 (done): data collector.** A thin client for ESPN's public APIs (`teams`, `schedule`, `summary`, `standings`, `roster`, `scoreboard`) that snapshots each season's schedule and every finalized game's box score to `data/raw/`, idempotently (the filesystem is the state: an already-fetched game is never refetched), rate-limited, and resumable. Runs daily via GitHub Actions for **both leagues** -- the NBA and the WNBA share every collector code path through a per-league config module (`src/collector/leagues.py`) and land in `data/raw/` vs `data/raw_wnba/`; see [docs/COLLECTOR.md](docs/COLLECTOR.md).

**Stage 2 (done): projection model.** Player-game rows unified 2010-11 → present into one table (one schema the whole window: no cross-era reconciliation needed, unlike the FPL loader), leak-free rolling form/availability/rest/matchup features, chronological train/validation/holdout split (2024-25 validation, 2025-26 untouched), LightGBM single-stage + two-stage comparison against a naive rolling-5 baseline. See [docs/MODELING.md](docs/MODELING.md).

**Stage 3 (done): lineup optimizer.** PuLP MILP over a projected player pool: 2 G / 2 F / 1 C / 2 UTIL slots with structural position eligibility, provably optimal answers, honest infeasibility errors. Verified against synthetic pools with known optima. See [docs/MODELING.md](docs/MODELING.md#lineup-optimizer).

**Stage 4 (done): dashboard.** Four-page Streamlit app where **every page opens on two league tabs (NBA | WNBA)**: Home nests its four tabs (Standings, Schedule & Scores, Awards Ladder, Court View) inside each league, and the left-nav pages (All-Time Stats, GOAT Rankings, Player Profile) switch league through the same tabs (`?league=wnba` renders the WNBA tab first). Live-first from ESPN with 60s cache, committed `data/dashboard_*` fallbacks (the WNBA's carry `_wnba`, e.g. `data/dashboard_wnba_teams.json`) for offline deploys, and freshness captions that always say which they're showing. The Awards Ladder follows that league's sidebar season selector across **every collected season (NBA 2010-11 → present, WNBA 2010 → present)**, ranking MVP/DPOY/6th-Man/MIP races and per-game stat leaders (3PM and +/- included, FG/3P shooting splits on every row) with transparent homegrown formulas (each printed verbatim on screen, explicitly not official NBA/WNBA voting), plus rank-movement arrows and an MVP trend chart fed by the daily race snapshots. The Schedule tab reads its season's committed file under `data/schedules/` (`data/schedules_wnba/` in the WNBA pane; NBA: 17 seasons / 20,394 games, 19,194 already final; WNBA: 17 calendar years / 3,693): a finished season renders every completed game with its final score, a running season shows recent results plus next fixtures (team and venue filters scope the whole tab, and a CSV download takes the selected rows), and a season with no collected games (2026-27 pre-tip-off) shows an honest empty state instead of another year's data. All-Time Stats shows career totals with the full parameter set (+/-, FG/3P/FT splits, FG%/3P%/eFG%/TS%) over the collector's stated window, qualified at ≥41 career GP (the +/- board ranks only careers the collected box scores cover). GOAT Rankings composites **all-history** careers per league (1,809 NBA players, 441 WNBA): 35% production (a 50/50 blend of career totals and per-game rates vs the pool's best, stats a career never had dropped and rescaled, window-only +/- floored at zero for negative careers) + 30% official ESPN honours (20 NBA award types / the WNBA's 15, every weight printed) + 25% peak + 10% championships (title count vs the pool maximum, verified official-record counts where ESPN's index can't reach -- including the WNBA titles ESPN's Finals-MVP detail omits), qualified at ≥82 GP, with the exact formula on screen and an explicit "not an official NBA/WNBA ranking" disclaimer. Player Profile puts up to four careers at once (cards with two readable meta lines, the full-width accolades pivot where every award row is visible, interactive per-season progression chart on a season or career-year axis), and the Court View slots stat leaders onto a CSS-drawn court. See [docs/DASHBOARD.md](docs/DASHBOARD.md).

**Stage 5 (in progress): live season.** The 2026-27 schedule (1,200 games) is captured; the daily workflow will collect its box scores as games finalize from late October. The historical backfill (2010-11 → 2025-26: 19,121 box scores collected) runs newest-first so training-ready seasons land first. The WNBA side is already live: 2010 → 2026 collected (3,682 regular-season games, same `seasontype=2` policy as the NBA) and refreshed by the same daily workflow.

## Deploying to Streamlit Community Cloud

The app is designed to deploy as-is from a fresh clone. It runs entirely
from the committed `data/dashboard_*` fallbacks when it can't reach the
collector's raw data (which Streamlit Cloud never has, since `data/raw/` and
`data/raw_wnba/` are gitignored):

1. Push this repo to GitHub (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
   for the exact commands if you're pushing it for the first time).
2. On [share.streamlit.io](https://share.streamlit.io): **New app** →
   pick the repo → **Main file path** `app/app.py` → **Deploy**.
   Python version follows `requirements.txt` (3.14, same as CI).
3. The theme ships with the repo (`.streamlit/config.toml`), so there is
   no Cloud-side configuration needed.

What works on a deployed instance: Standings and Schedule (live ESPN first,
committed fallback second), the Awards Ladder and Court View (committed
award races computed from collected box scores), and All-Time Stats, GOAT
Rankings, and Player Profile (committed all-history payloads). What needs
a data machine: historical raw data and retraining. The daily GitHub
Actions workflow (`.github/workflows/collector.yml`) refreshes the
committed fallback files so the deployed app stays current without ever
seeing `data/raw/`.

## Project Structure

```text
NBA-Analytics/
├── src/
│   ├── collector/
│   │   ├── leagues.py                    # Per-league config (NBA/WNBA) + THE path rule named_path()
│   │   ├── espn_api.py                   # Thin client + season params (NBA = END year, WNBA = calendar year)
│   │   ├── parsing.py                    # Payload → flat rows (pure, fixture-pinned)
│   │   ├── snapshot.py                   # Idempotent/resumable season snapshots (--league/--backfill/--check-only)
│   │   ├── refresh_dashboard_fallbacks.py# Stable data/dashboard_* (+ _wnba) copies for offline deploys
│   │   ├── awards.py                      # Races + stat leaders per season, all-time boards, GOAT ladder (local box-score math)
│   │   ├── history.py                     # All-history career lines + official honours behind GOAT/Profile (both leagues)
│   │   ├── fixtures/                     # Real recorded payloads (incl. a 1995-96 game)
│   │   ├── test_espn_api.py              # Parsing pinned against fixtures + opt-in live tests
│   │   ├── test_snapshot.py              # State decisions: what's fetched, skipped, targeted
│   │   ├── test_leagues.py               # Per-league config + named_path + verified_champions pins
│   │   ├── test_awards.py                # Qualifiers, race math, aggregation edge cases
│   │   ├── test_data_invariants.py       # Cross-artifact sanity over the committed data files
│   │   ├── test_refresh.py               # Fallback refresh contracts (schedule split, stamps, artifacts)
│   │   └── test_history.py               # All-history build runs offline from the committed cache
│   └── model/
│       ├── scoring.py                    # Configurable scoring weights (the target's weighted sum lives here)
│       ├── load_historical.py            # data/raw/* → one training table (+ positions, team scores)
│       ├── features.py                   # Leak-free rolling form/rest/matchup/baseline features
│       ├── train.py                      # Chronological split, LightGBM, metrics.json
│       ├── predict.py                    # Score history rows + build upcoming-game projections
│       ├── optimizer.py                  # PuLP best-lineup (G/F/C/UTIL slots)
│       ├── guardrail.py                  # Retrain gate: refuse a validation-MAE regression
│       ├── test_model.py                 # Scoring, leakage guarantees, split, projections, optimizer
│       └── test_guardrail.py             # Guardrail's pass/fail cases
├── app/
│   ├── app.py                            # Home: NBA | WNBA tabs → Standings, Schedule & Scores, Awards Ladder, Court View
│   ├── shared.py                         # Live-first loaders (league-aware) with committed fallbacks + age notes
│   ├── test_app_offline.py               # AppTest renders with EVERY ESPN call forced to fail
│   └── pages/
│       ├── 2_All-Time_Stats.py           # Career boards across the collected window (both leagues)
│       ├── 3_GOAT_Rankings.py            # All-history GOAT ladders (NBA + WNBA), formula printed verbatim
│       └── 4_Player_Profile.py           # Cards, accolades, per-season progression chart (both leagues)
├── r/                                    # EDA in R (arrow reads the same parquet)
├── docs/
│   ├── COLLECTOR.md                      # Endpoints, files written, scheduling, resumability
│   ├── MODELING.md                       # Data, features, training, optimizer internals
│   ├── DASHBOARD.md                      # Page-by-page docs and design rationale
│   ├── DEPLOYMENT.md                     # GitHub push + Streamlit Cloud, what works offline
│   └── KNOWN-ISSUES.md                   # Dated investigation log (data-source selection, WAF, API quirks)
├── data/
│   ├── raw/ (+ raw_wnba/)                # Gitignored, regenerates from the collector
│   ├── schedules/ (+ _wnba/)             # Committed per-season schedule files (one JSON per season)
│   ├── races/ (+ _wnba/)                 # Committed daily award-race snapshots (arrows + MVP trend)
│   ├── processed/                        # Gitignored except the 2 leaderboards, 2 history caches, historical_games.parquet
│   └── dashboard_*.json                  # Committed offline fallbacks (regenerated daily by CI)
├── models/                               # Gitignored except proj_model.txt + metrics.json
├── requirements.in / .txt / -dev.txt     # Ranges / exact pins / lint-only
├── pytest.ini                            # Default run = no network (`-m "not live"`)
├── ruff.toml                             # Pinned lint rules CI gates on
└── .github/workflows/                    # CI + daily collector + weekly history refresh + monthly retrain + ESPN probe
```

## Data Sources

- **ESPN `site.api.espn.com`** (free, no auth): `teams`, per-team `schedule?season={end_year}&seasontype=2`, per-game `summary` (full box score, verified back to 1995-96), `scoreboard?dates=`.
- **ESPN `site.web.api.espn.com`**: `v2 .../standings` (the `site.api` standings path only returns a link stub), `common/v3 .../roster` (player positions: **season param ignored**, always current roster; see KNOWN-ISSUES).
- **Season parameter gotcha**: for the NBA, ESPN's `season=2011` means *2010-11* (the year it ends); the WNBA's season label *is* its calendar year (`season=2026`). `espn_api.season_param()` / `season_label()` are the only place that arithmetic should ever live.
- **Window**: modeling focuses on 2010-11 → present. Pre-2010 data exists in the API (the repo's fixtures prove it) and `--from/--to` can reach it, but it is not backfilled by default (different era, diminishing returns for projection features).
- Regular season only (`seasontype=2`) in both leagues: standings, per-game leaders and the model's training frame are all built on the regular season; playoffs are deliberately out of scope, not overlooked.

## Running the Collector

`pip install -r requirements.txt`, then `python src/collector/snapshot.py --backfill` once, and `python src/collector/snapshot.py` for incremental refreshes (the WNBA: the same commands with `--league wnba`). Everything the collector writes, the rate-limit/retry behavior, and the daily automation: **[docs/COLLECTOR.md](docs/COLLECTOR.md)**.

## Projection Model

Leak-free feature engineering, the chronological split, single- vs two-stage architecture, and the naive baseline comparison: **[docs/MODELING.md](docs/MODELING.md)**.

## Lineup Optimizer

Slot structure, eligibility rules, infeasibility handling, and optimality tests: **[docs/MODELING.md](docs/MODELING.md#lineup-optimizer)**.

## Dashboard

**`streamlit run app/app.py`**: every page opens on the `NBA | WNBA` league
tabs -- Home's (Standings, Schedule & Scores, Awards Ladder, Court View)
inside each -- plus left-nav pages (All-Time Stats, GOAT Rankings, Player
Profile), live-first with offline fallbacks, freshness stated honestly on
every screen: **[docs/DASHBOARD.md](docs/DASHBOARD.md)**.

## Known Issues Found & Fixed

The investigation log (ESPN WAF blocking User-Agents, the standings link-stub, the roster endpoint ignoring `season`, box-score vs schedule score shape differences, and more), each with the evidence and the permanent guard left behind: **[docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md)**.

## Future Improvements

- Historical player positions: ESPN's roster endpoint is current-only (verified), so ~retired players' rows carry `position=UNK`. Options researched: per-athlete bio calls (~2k+ calls, one per unique historical player) or box-score lineup inference. Position is deliberately *not* a model feature (its missingness would encode row era); only the optimizer uses it, and only for current players.
- Per-category projections for 9-category leagues: score each stat separately (`scoring.py` already exposes the category list) and let the consumer weight categories, since category value depends on the rest of the lineup; an additive "9-cat points" target would be a modeling lie.
- Salary-cap optimizer variant: the MILP already separates objective from slot structure; adding a cost column and budget constraint is the natural extension once a cost source (an ADP proxy or a pricing feed) is chosen.
- Injury/outcome signal: same lesson as FPL-Analytics: the gap to naive baselines concentrates in did-not-play rows, where historical stats can't see a coach's game-time decision. A real availability feed is the lever, not more box-score history.
- Retrain once 2026-27 box scores accumulate (validation window deliberately fixed at 2024-25 so early-season re-runs stay comparable).
- Wire `predict.project_upcoming()` into a dashboard surface again (the Projections tab was replaced by the Awards Ladder; the daily refresh still calls `refresh_projections()`, which writes the rolling 21-day window once the schedule has games inside it), or surface it as another Home tab once 2026-27 games give it something current to project.
