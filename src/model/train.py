"""Train the NBA fantasy projection model.

Chronological split, never random -- this is time-series data, and a random
split would let the model see the future (train on March while validating on
January of the same season):

- train: every season before 2024-25 (2010-11 .. 2023-24)
- validation: 2024-25 (the most recent complete season at model-build time)
- final holdout: 2025-26 -- never touched by any training/tuning decision

Only the explicit FEATURE_COLUMNS allowlist below is used as input --
deliberately an allowlist, not "everything except known-bad columns", so a
leaky column added to the feature table later can't silently become a model
input.

TARGET: fantasy_points under the default configured scoring (scoring.py).
Rows include did-not-play games (fantasy_points == 0, ~35-45% of rows) --
they're real outcomes for a fantasy manager who started that player, and
their share mirrors FPL-Analytics' finding that non-playing rows dominate
error budgets. A two-stage model (P(plays) x E[points|plays]) is trained
alongside the single-stage one as a comparison for exactly that reason --
here too (see metrics.json) -- with the single-stage model used at inference
unless the numbers say otherwise.
"""

import json
import os

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, roc_auc_score

VALIDATION_SEASON = "2024-25"
FINAL_HOLDOUT_SEASON = "2025-26"  # never touched during training/tuning
EXCLUDED_SEASONS = [FINAL_HOLDOUT_SEASON]

TARGET_COLUMN = "fantasy_points"
PLAYED_COLUMN = "played"  # derived in load_historical: min > 0

# Every entry is either a rolling/lagged stat (shifted by 1 in features.py --
# only information available BEFORE this game) or a pre-game-known fact
# (rest_days/back_to_back, is_home, opponent identity via opp_form_*,
# career/season game counts, the league-wide new-player baseline).
# `position` is deliberately absent: it's current-state data (see
# load_historical) whose missingness would encode row era.
FEATURE_COLUMNS = [
    "is_home",
    "rest_days",
    "back_to_back",
    "season_game_count",
    "career_game_count",
    "played_last_game",
    "minutes_last_game",
    "fantasy_points_avg_last_3",
    "min_avg_last_3",
    "pts_avg_last_3",
    "fantasy_points_avg_last_5",
    "min_avg_last_5",
    "pts_avg_last_5",
    "reb_avg_last_5",
    "ast_avg_last_5",
    "fantasy_points_avg_last_10",
    "min_avg_last_10",
    "team_form_pf",
    "team_form_pa",
    "opp_form_pa",
    "opp_form_pf",
    "new_player_baseline",
]


def load_training_data(path: str = None) -> pd.DataFrame:
    path = path or os.path.join("data", "processed", "features.parquet")
    df = pd.read_parquet(path)
    df = df[~df["season"].isin(EXCLUDED_SEASONS)].copy()
    if PLAYED_COLUMN not in df.columns:
        df[PLAYED_COLUMN] = (df["min"].fillna(0) > 0).astype(int)
    return df


def chronological_split(df: pd.DataFrame) -> tuple:
    """Everything before VALIDATION_SEASON trains; VALIDATION_SEASON itself
    is the evaluation window; later seasons (including the holdout) stay out
    of both, so appending a newly-collected season can never silently change
    the historical validation result."""
    train = df[df["season"] < VALIDATION_SEASON].copy()
    val = df[df["season"] == VALIDATION_SEASON].copy()
    return train, val


def prepare_x(df: pd.DataFrame) -> pd.DataFrame:
    return df[FEATURE_COLUMNS].copy()


def evaluate(name: str, y_true, y_pred) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    print(f"{name}: MAE={mae:.3f}  RMSE={rmse:.3f}")
    return {"mae": float(mae), "rmse": float(rmse)}


METRICS_PATH = os.path.join("models", "metrics.json")

_REG_PARAMS = dict(
    objective="regression",
    n_estimators=500,
    learning_rate=0.03,
    max_depth=6,
    num_leaves=31,
    min_child_samples=30,
    random_state=42,
    verbose=-1,
)


def train_single_stage(X_train: pd.DataFrame, y_train: pd.Series) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(**_REG_PARAMS)
    model.fit(X_train, y_train)
    return model


def train_play_classifier(X_train: pd.DataFrame, played_train: pd.Series) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(
        objective="binary", n_estimators=300, learning_rate=0.05,
        max_depth=6, num_leaves=31, min_child_samples=30,
        random_state=42, verbose=-1,
    )
    model.fit(X_train, played_train)
    return model


def train_points_given_played(X_train: pd.DataFrame, y_train: pd.Series) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(**_REG_PARAMS)
    model.fit(X_train, y_train)
    return model


def main() -> None:
    df = load_training_data()
    print(f"Loaded {len(df):,} rows (after excluding {EXCLUDED_SEASONS})")

    train_df, val_df = chronological_split(df)
    print(f"Train: {len(train_df):,} rows (seasons before {VALIDATION_SEASON})")
    print(f"Validation: {len(val_df):,} rows (season {VALIDATION_SEASON})")
    if val_df.empty:
        raise SystemExit(
            f"Validation season {VALIDATION_SEASON} has no rows -- collect it "
            f"(`snapshot.py --season {VALIDATION_SEASON}`) or move VALIDATION_SEASON."
        )

    X_train, X_val = prepare_x(train_df), prepare_x(val_df)
    y_train = train_df[TARGET_COLUMN].astype(float)
    y_val = val_df[TARGET_COLUMN].astype(float)

    metrics = {
        "validation_season": VALIDATION_SEASON,
        "final_holdout_season": FINAL_HOLDOUT_SEASON,
        "n_train_rows": int(len(train_df)),
        "n_validation_rows": int(len(val_df)),
    }

    print("\nTraining single-stage model...")
    single_model = train_single_stage(X_train, y_train)
    single_pred = np.clip(single_model.predict(X_val), 0, None)
    print(f"\n=== Validation results (season {VALIDATION_SEASON}) ===")
    metrics["single_stage"] = evaluate("Single-stage model", y_val, single_pred)

    print("\nTraining two-stage model (P(plays) x E[points|plays])...")
    played_train = train_df[PLAYED_COLUMN]
    play_clf = train_play_classifier(X_train, played_train)
    played_mask = played_train == 1
    points_model = train_points_given_played(X_train[played_mask], y_train[played_mask])

    play_proba = play_clf.predict_proba(X_val)[:, 1]
    points_given_played = np.clip(points_model.predict(X_val), 0, None)
    two_stage_pred = play_proba * points_given_played

    play_auc = roc_auc_score(val_df[PLAYED_COLUMN], play_proba)
    print(f"Play classifier AUC: {play_auc:.3f}")
    metrics["two_stage"] = evaluate(
        "Two-stage model (P(plays) x E[points|plays])", y_val, two_stage_pred
    )
    metrics["two_stage"]["play_classifier_auc"] = float(play_auc)

    naive_pred = val_df["fantasy_points_avg_last_5"].fillna(0).clip(lower=0)
    metrics["naive_baseline"] = evaluate(
        "Naive baseline (player's own rolling-5 average)", y_val, naive_pred
    )

    # Diagnostic: how much error sits on non-playing rows (the FPL project
    # found most of its gap there -- worth checking here for the same reason).
    played_mask_val = val_df[PLAYED_COLUMN] == 1
    print(f"\n=== Diagnostics: PLAYED rows only "
          f"(n={played_mask_val.sum():,}/{len(val_df):,}) ===")
    metrics["single_stage_played_only"] = evaluate(
        "Single-stage (played only)", y_val[played_mask_val],
        single_pred[played_mask_val.values],
    )
    metrics["single_stage_dnp_only"] = evaluate(
        "Single-stage (did-not-play only)",
        y_val[~played_mask_val], single_pred[~played_mask_val.values],
    )

    print("\n=== Feature importance (single-stage) ===")
    importance = pd.Series(
        single_model.feature_importances_, index=FEATURE_COLUMNS
    ).sort_values(ascending=False)
    print(importance.to_string())

    os.makedirs("models", exist_ok=True)
    single_model.booster_.save_model(os.path.join("models", "proj_model.txt"))
    play_clf.booster_.save_model(os.path.join("models", "proj_model_play_classifier.txt"))
    points_model.booster_.save_model(os.path.join("models", "proj_model_points_given_played.txt"))
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print("\nSaved 3 model files and metrics.json to models/")


if __name__ == "__main__":
    main()
