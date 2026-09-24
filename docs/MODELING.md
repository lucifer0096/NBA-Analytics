# Modeling

How the projection model turns collected box scores into leak-free fantasy
projections, and how the optimizer turns projections into lineups.

## Training data

`load_historical.py` unifies every finalized game under
`data/raw/{season}/games/` into one table:

- **Window**: 2010-11 → present (the deliberate focus — see README). Every
  season comes from this project's own collector, so there is **one schema
  the whole window**: no per-era column reconciliation (the single hardest
  part of the FPL-Analytics loader simply doesn't exist here).
- **Identity**: `player_id` is ESPN's athlete id, stable across seasons
  (verified against the fixture + roster endpoint). Rolling features group by
  it directly — no FPL-style "id resets every season" trap.
- **Rows**: one per player per game, **including did-not-play appearances**
  (min=0, every stat 0, `did_not_play=True`). They're real fantasy outcomes
  (a manager who started that player scored 0) and the model's availability
  signal — ~35–45% of rows, mirroring FPL's finding that non-playing rows
  dominate error budgets.
- **Team context**: each season's `schedule.csv` joins final team scores onto
  player rows (with a cross-check: box-score and schedule home/away
  assignments must agree — two different ESPN endpoints writing disagreeing
  context would silently corrupt team-form features).
- **Positions**: joined from `player_positions.json`, which is **current-state
  only** (ESPN's roster endpoint ignores its season param — see
  KNOWN-ISSUES.md). Retired players come back `UNK`. Position is
  deliberately **not a model feature**: its missingness would encode "how
  modern is this row".

### Target

`fantasy_points` = configurable weighted sum of the box score
(`scoring.py`, default: PTS×1 + REB×1 + AST×2 + STL×3 + BLK×3 + TO×−1).
The weights are a pluggable dict — swap them and the same pipeline trains
under your league's scoring. 9-category leagues are deliberately *not*
modeled as one additive number (category value depends on the rest of the
lineup); see README Future Improvements.

## Feature engineering (all leak-free)

Every feature is computable **before the game starts**, and every rolling
stat is `shift(1)`-ed within each player's own chronology first, so a row can
never see its own outcome:

| Feature family | Columns | Notes |
|---|---|---|
| Rolling form | `fantasy_points/min/pts/reb/ast_avg_last_{3,5,10}` | shift(1) then rolling, per player, across season boundaries (SEASON_ORDER makes October follow June) |
| Availability | `minutes_last_game`, `played_last_game` | prior appearance only |
| Rest | `rest_days` (0 = back-to-back), `back_to_back` | from real calendar dates — All-Star breaks and suspensions are genuine rest |
| Experience | `season_game_count`, `career_game_count` | cumcount = games strictly before this one |
| Team form | `team_form_pf`, `team_form_pa` | team's own last-5 scoring/defense, lagged one team-game |
| Matchup | `opp_form_pa`, `opp_form_pf` | opponent's last-5 defense/offense, lagged — softer matchup = higher `opp_form_pa` |
| Rookie baseline | `new_player_baseline` | league-wide daily mean → expanding mean → shifted one day; zero-history rows get an honest prior, and a season's first day inherits the previous season's final level |

Guarantees pinned by tests (`test_model.py`): first-career-game rows carry no
rolling average; a rolling-3 over two games equals game 1's value alone
(including the current row would change it); team form for game N is game
N−1's score; the new-player baseline never includes its own day.

## Training

`train.py` — chronological split, never random:

- **train**: 2010-11 … 2023-24
- **validation**: 2024-25 (the most recent complete season at build time)
- **final holdout**: 2025-26 — excluded from training *and* tuning; run it
  once, when no decisions remain

The split is by season **label comparison** (`season < "2024-25"`), so
appending a newly-collected season can never silently contaminate the
historical validation result. Input columns are an explicit
`FEATURE_COLUMNS` allowlist — a leaky column added to the feature table
later can't become a model input by default.

Architectures trained every run:

1. **Single-stage** LightGBM regressor on all rows (this is what
   `predict.py` loads — `models/proj_model.txt`)
2. **Two-stage** comparison: P(plays) classifier × E[points | plays]
   regressor — the hypothesis (FPL's was right there) that a single model
   spends its error budget distinguishing DNP from played. Kept as a
   documented comparison, reported in `metrics.json`; the two-stage's
   play-classifier ROC-AUC is reported too.
3. **Naive baseline**: the player's own rolling-5 average. If the model
   can't beat this, the dashboard says so in red — a projection that can't
   beat last-5-means isn't worth a manager's trust.

Diagnostics reported: MAE split played-only vs did-not-play-only (the FPL
project's error-budget lesson, checked here the same way).

## Projections for upcoming games

`predict.build_projection_rows()` appends **placeholder rows** (stats NaN)
for each (player, upcoming game) to the collected history and runs the *same*
feature pipeline over the combined table:

- rolling features fill from the player's real past (shift(1) sees only
  earlier rows — placeholders sit at the end, so they consume history and
  nothing consumes them);
- `rest_days` falls out of the calendar gap to the player's last game;
- team/opponent form rolls forward from the last played team-games;
- placeholder targets are NaN — there is no target to leak.

`project_upcoming()` wraps: upcoming schedule → rows → `projected_points`
(clipped ≥ 0), carrying position + team for the optimizer.

## Lineup Optimizer

`optimizer.py` — a PuLP MILP, so the answer is **provably optimal** for the
chosen pool and slots (not a greedy approximation):

- **Slots** (default): 2 G · 2 F · 1 C · 2 UTIL — the common 7-man
  start. Platform-agnostic: no salary cap, because the project targets no
  platform; add a cost column + budget constraint for a
  DraftKings-style variant (Future Improvements).
- **Eligibility is structural**: an ineligible (player, slot) pair simply
  isn't a variable, so "C slot needs a center" can't be violated — it fails
  to exist rather than being penalized. ESPN's coarse G/F/C positions are
  bucketed, with PG/SG→G and SF/PF→F accepted from other tools' pools.
- **Objective**: maximize Σ projected_points, one assignment per player.
- **Infeasibility is explicit**: pools that can't fill every slot (no
  center, too few players) raise `InfeasiblePool` with the reason — the
  solver's arbitrary infeasible status never surfaces as a silent empty
  lineup.

Tests pin optimality against pools with hand-computed optima, including the
interesting case: an elite center must land in UTIL while a lesser center
fills the C slot (the greedy "best player plays his own position" instinct
loses points here).

## Metrics & artifacts

`train.py` writes `models/metrics.json` (validation MAE/RMSE for single,
two-stage, naive, played/DNP splits, play-classifier AUC, row counts) and
model files; only `proj_model.txt` + `metrics.json` are committed (the
dashboard loads them where training can't run), everything else regenerates.
