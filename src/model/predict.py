"""Load the trained projection model and score features -- for validation
rows and for UPCOMING games.

The upcoming-game path (`build_projection_rows`) is the interesting part:
ESPN gives future schedule (opponent, home/away, date) but no stats, so we
appended placeholder rows (stats NaN) to the collected history and run the
SAME leak-free feature pipeline over the combined table. Rolling features
then fill themselves from the player's real past (shift(1) sees only earlier
rows), rest_days falls out of the calendar gap to their last game, and team/
opponent form rolls forward from the last played team-games. The placeholder
rows' own NaN targets can't leak -- they have no targets.
"""

import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train import prepare_x  # noqa: E402
import features as feature_builders  # noqa: E402

MODEL_PATH = os.path.join("models", "proj_model.txt")


def load_model(path: str = None) -> lgb.Booster:
    return lgb.Booster(model_file=path or MODEL_PATH)


def predict_points(df: pd.DataFrame, model: lgb.Booster = None) -> np.ndarray:
    """Projected fantasy points per row of `df` (must already carry every
    FEATURE_COLUMNS input -- i.e. gone through build_feature_table). Missing
    values stay NaN: LightGBM handles them natively via learned default
    split directions, same as during training. Clipped at 0 -- you can't
    score negative fantasy points under the default weights... modulo the
    turnover penalty on a catastrophic game, which clipping deliberately
    suppresses as an optimistic-but-standard fantasy convention."""
    model = model or load_model()
    X = prepare_x(df)
    return np.clip(model.predict(X), 0, None)


def build_projection_rows(history: pd.DataFrame,
                           upcoming_games: pd.DataFrame,
                           player_teams: pd.DataFrame) -> pd.DataFrame:
    """Player x upcoming-game rows, feature-ready, for games not yet played.

    history: collected player-game rows (load_historical output) -- the
        factual base the rolling features draw from.
    upcoming_games: schedule rows still to be played, with the same columns
        the collector's schedule.csv has (game_id, date, season, home_id,
        away_id, status...). Games WITHOUT a concrete date are skipped (no
        rest_days is computable for those).
    player_teams: (player_id, team_id) candidates -- typically the current
        position map (load_historical.load_positions()), since projections
        only ever concern players active NOW.

    Returns rows with FEATURE_COLUMNS computed and stats NaN; call
    predict_points on the result."""
    upcoming = upcoming_games.copy()
    upcoming = upcoming[upcoming["date"].notna()]
    if upcoming.empty:
        return pd.DataFrame()

    # One row per (player, upcoming game) for players on either team.
    home = upcoming[["game_id", "date", "season", "home_id", "away_id"]].rename(
        columns={"home_id": "team_id", "away_id": "opponent_id"}
    )
    home["is_home"] = True
    away = upcoming[["game_id", "date", "season", "home_id", "away_id"]].rename(
        columns={"away_id": "team_id", "home_id": "opponent_id"}
    )
    away["is_home"] = False
    team_games = pd.concat([home, away], ignore_index=True)

    candidates = player_teams[["player_id", "team_id", "position"]].copy()
    placeholders = team_games.merge(candidates, on="team_id", how="inner")
    if placeholders.empty:
        return pd.DataFrame()

    placeholders["player_name"] = pd.NA
    placeholders["did_not_play"] = 0
    placeholders["played"] = np.nan
    for column in ("min", "pts", "reb", "ast", "fantasy_points",
                   "team_score", "opp_score", "starter"):
        placeholders[column] = np.nan
    placeholders["opponent_abbrev"] = pd.NA
    placeholders["team_abbrev"] = pd.NA

    # Build each target date against only the real history available before
    # that date. If every future placeholder were concatenated at once, a
    # player's second upcoming game would incorrectly treat the first
    # placeholder as a prior game (shifting counts, rest, and rolling form
    # with no actual result). Grouping by date keeps same-day games together
    # while preserving the leak-free chronology across game days.
    history_dates = pd.to_datetime(
        history["date"], format="mixed", utc=True, errors="coerce"
    )
    projected_frames = []
    for date, date_rows in placeholders.groupby("date", sort=True):
        target_date = pd.to_datetime(date, format="mixed", utc=True).date()
        prior_history = history.loc[history_dates.dt.date < target_date].drop(
            columns=["_history_date"], errors="ignore"
        )
        combined = pd.concat([prior_history, date_rows], ignore_index=True, sort=False)
        featured = feature_builders.build_feature_table(combined)
        target_keys = set(zip(date_rows["game_id"], date_rows["player_id"]))
        projected_frames.append(featured[
            featured.apply(
                lambda row: (row["game_id"], row["player_id"]) in target_keys,
                axis=1,
            )
        ].copy())

    if not projected_frames:
        return pd.DataFrame()
    return pd.concat(projected_frames, ignore_index=True).reset_index(drop=True)


def _schedule_for_cli():
    """Prefer the current/upcoming season's schedule for CLI projections.

    `available_seasons()` only reports directories containing collected games,
    which is the wrong choice during an offseason: the new season has a real
    schedule but no games yet, while the previous season's schedule is fully
    final. The CLI therefore checks schedule-bearing directories explicitly.
    """
    import espn_api
    import load_historical

    raw_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))
    raw_dir = os.path.join(raw_dir, "data", "raw")
    current = espn_api.current_season_label()
    candidates = [current]
    if os.path.isdir(raw_dir):
        candidates.extend(sorted(
            (name for name in os.listdir(raw_dir)
             if name[:4].isdigit() and "-" in name
             and os.path.isfile(os.path.join(raw_dir, name, "schedule.csv"))),
            reverse=True,
        ))
    for season in dict.fromkeys(candidates):
        schedule = load_historical.load_schedule(season)
        if not schedule.empty:
            return season, schedule
    return current, pd.DataFrame()


def project_upcoming(history: pd.DataFrame,
                      schedule: pd.DataFrame,
                      player_teams: pd.DataFrame,
                      game_ids: list = None,
                      model: lgb.Booster = None) -> pd.DataFrame:
    """End-to-end: upcoming schedule -> player projections DataFrame with a
    `projected_points` column (plus player position/team for the optimizer).

    `game_ids` restricts projection to specific games (e.g. tomorrow's);
    default = every schedule row not yet STATUS_FINAL."""
    upcoming = schedule[schedule["status"] != "STATUS_FINAL"].copy()
    if game_ids is not None:
        upcoming = upcoming[upcoming["game_id"].isin(game_ids)]
    if upcoming.empty:
        return pd.DataFrame()

    rows = build_projection_rows(history, upcoming, player_teams)
    if rows.empty:
        return pd.DataFrame()

    rows["projected_points"] = predict_points(rows, model=model)
    keep = ["game_id", "date", "season", "player_id", "team_id",
            "opponent_id", "is_home", "position", "projected_points"]
    return rows[keep].sort_values(
        ["date", "projected_points"], ascending=[True, False]
    ).reset_index(drop=True)


if __name__ == "__main__":
    import load_historical

    history = load_historical.load_all_seasons()
    schedule_season, schedule = _schedule_for_cli()
    positions = load_historical.load_positions()
    if schedule.empty or positions.empty:
        raise SystemExit("Need a collected schedule + player_positions.json "
                         "(run the collector first).")

    print(f"Projecting {schedule_season} schedule ({len(schedule):,} games)...")
    projections = project_upcoming(history, schedule, positions)
    if projections.empty:
        print("No upcoming scheduled games to project.")
    else:
        print(projections.head(20).to_string(index=False))
        print(f"\n{len(projections)} player-game projections across "
              f"{projections['game_id'].nunique()} games")
