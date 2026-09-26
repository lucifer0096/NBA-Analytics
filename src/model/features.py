"""Feature engineering for the NBA fantasy projection model.

Same principle as FPL-Analytics' features.py: this game's own stats are the
TARGET, never an input -- the signal for predicting a player's NEXT game is
their recent form, minutes stability, rest, and the matchup. Everything
rolling/lagged here is computed only from rows strictly earlier than the row
being built (shifted by 1 within each player/team's own chronology), so no
feature can see its own row's outcome.

Identity: rows group by `player_id` (ESPN's stable athlete id -- no
season-reset trap like FPL's `element`) ordered by (season_rank, date).
Team-level features build from one-row-per-team-game tables the same way.
"""

import re

import pandas as pd

# Season order matters for anything that rolls across a season boundary: a
# player's form entering October of a new season should still reflect their
# final games of the previous spring, not reset to NaN. Kept explicit (like
# FPL's SEASON_ORDER) so ordering never depends on filesystem/dict order, and
# every unknown future label sorts after every known one.
SEASON_ORDER = [f"{y}-{str(y + 1)[2:]}" for y in range(2010, 2040)]


def _season_start_year(season: str) -> int:
    match = re.fullmatch(r"(\d{4})-\d{2}", str(season))
    if match is None:
        raise ValueError(f"Invalid season label: {season!r}; expected YYYY-YY")
    return int(match.group(1))


def _season_rank_map(seasons) -> dict:
    known = {season: index for index, season in enumerate(SEASON_ORDER)}
    unknown = sorted(set(map(str, seasons)) - set(known), key=_season_start_year)
    return {season: index for index, season in enumerate(SEASON_ORDER + unknown)}


def _season_sort_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    season_rank = _season_rank_map(df["season"].dropna().unique())
    df["_season_rank"] = df["season"].map(season_rank)
    if df["_season_rank"].isna().any():
        unknown = df.loc[df["_season_rank"].isna(), "season"].unique()
        raise ValueError(f"Unknown season(s) not in SEASON_ORDER: {unknown}")
    return df


def _player_sort(df: pd.DataFrame) -> pd.DataFrame:
    """Stable player chronology: (season rank, then game date within it)."""
    return df.sort_values(
        ["player_id", "_season_rank", "date"], kind="mergesort"
    ).reset_index(drop=True)


def add_rolling_form_features(df: pd.DataFrame,
                              windows: tuple = (3, 5, 10)) -> pd.DataFrame:
    """Per-player rolling averages of fantasy points, minutes and the raw
    counting stats -- shift(1) first so the current row's own outcome is
    excluded (the leak this structure exists to prevent). min_periods=1 so a
    player's 2nd career game already has a 1-game average."""
    df = _season_sort_key(df)
    df = _player_sort(df)
    grouped = df.groupby("player_id", sort=False)

    stat_columns = ["fantasy_points", "min", "pts", "reb", "ast"]
    for window in windows:
        for column in stat_columns:
            df[f"{column}_avg_last_{window}"] = (
                grouped[column]
                .transform(lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean())
            )

    # Counts from PRIOR rows only: cumcount() at this row is the number of
    # earlier rows, which is exactly "games before this one".
    df["career_game_count"] = grouped.cumcount()

    def _season_count(group: pd.DataFrame) -> pd.Series:
        return group.groupby("season").cumcount()

    df["season_game_count"] = (
        df.groupby("player_id", sort=False)
        .apply(_season_count, include_groups=False)
        .reset_index(level=0, drop=True)
    )
    return df.drop(columns=["_season_rank"])


def add_availability_features(df: pd.DataFrame) -> pd.DataFrame:
    """Minutes stability + rest: how the last game went (played? how many
    minutes?) and how many days off precede THIS game. Rest is a real NBA
    signal (B2B rest/underperformance) and is known before the game."""
    df = _season_sort_key(df)
    df = _player_sort(df)
    grouped = df.groupby("player_id", sort=False)

    df["minutes_last_game"] = grouped["min"].shift(1)
    df["played_last_game"] = (
        df["minutes_last_game"].fillna(0).gt(0).astype(int)
    )

    # "Played 3 of his last 5" availability signal as a 0-5 count of prior
    # appearances: shift(1) first so this row's own game never enters its
    # own window (the leak the structure above exists to prevent), and a
    # career with no prior game stays NaN rather than claiming 0 of 5.
    def _prior_appearances(s: pd.Series) -> pd.Series:
        prior = s.shift(1)
        played = prior.gt(0).astype(float).where(prior.notna())
        return played.rolling(5, min_periods=1).sum()

    df["games_played_last_5"] = grouped["min"].transform(_prior_appearances)

    # Calendar days since this player's previous game (NaN on the first game
    # of a career/season-gap). Computed from real dates, not row gaps --
    # trade suspensions and All-Star breaks are genuine rest.
    df["_date_ts"] = pd.to_datetime(df["date"], format="mixed", utc=True, errors="coerce")
    prev_date = grouped["_date_ts"].shift(1)
    gap_days = (df["_date_ts"] - prev_date).dt.days
    df["rest_days"] = (gap_days - 1).clip(lower=0)  # 0 = back-to-back
    df["back_to_back"] = gap_days.eq(1).astype(int)
    return df.drop(columns=["_season_rank", "_date_ts"])


def _build_team_game_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, team, game): that team's score and opponent
    score. POST-game results -- used only lagged (shifted by 1) below, never
    joined onto a player row as a same-game feature (that would leak the
    game's own outcome).

    Derivation from the PLAYER rows rather than schedule.csv directly keeps
    this function total over any input table (including projection
    placeholders, whose scores are NaN -- rolling windows skip those)."""
    keys = ["season", "game_id", "team_id"]
    columns = keys + ["date", "is_home", "team_score", "opp_score"]
    base = df[columns].drop_duplicates()  # collapse the ~24 player rows per team-game

    # A team plays exactly one game at a time; MORE THAN ONE surviving row
    # for the same (season, game, team) can only mean contradictory scores
    # (the FPL loader hit this class of bug via mid-season transfers).
    # Drop those keys entirely rather than silently keeping whichever score
    # happened to sort first.
    contradiction = base.groupby(keys).size()
    bad = contradiction[contradiction > 1]
    if len(bad):
        print(f"  Dropping {len(bad)} team-games with contradictory scores "
              f"(same team+game, multiple results)")
        base = base.merge(bad.rename("_bad"), on=keys, how="left")
        base = base[base["_bad"].isna()].drop(columns="_bad")

    return base.drop_duplicates(subset=keys).copy()


def add_team_form_features(df: pd.DataFrame, windows: tuple = (5,)) -> pd.DataFrame:
    """Lagged rolling team strength, joined twice: the player's OWN team's
    recent scoring/defense ('team_form_*') and their OPPONENT's recent
    defense ('opp_form_pa' -- how many points the opponent has been allowing,
    i.e. a softer/harder matchup). All shifted by one team-game first, so a
    game's own result never feeds its own row."""
    df = _season_sort_key(df)
    team_game = _build_team_game_table(df)
    team_game["_season_rank"] = team_game["season"].map(_season_rank_map(team_game["season"]))
    team_game = team_game.sort_values(
        ["team_id", "_season_rank", "date"], kind="mergesort"
    ).reset_index(drop=True)
    grouped = team_game.groupby("team_id", sort=False)

    for window in windows:
        for column in ["team_score", "opp_score"]:
            team_game[f"{column}_avg_last_{window}"] = (
                grouped[column]
                .transform(lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean())
            )

    own = team_game.rename(columns={
        "team_score_avg_last_5": "team_form_pf",
        "opp_score_avg_last_5": "team_form_pa",
    })[["season", "game_id", "team_id", "team_form_pf", "team_form_pa"]]

    opp = team_game.rename(columns={
        "team_id": "opponent_id",
        "opp_score_avg_last_5": "opp_form_pa",
        "team_score_avg_last_5": "opp_form_pf",
    })[["season", "game_id", "opponent_id", "opp_form_pa", "opp_form_pf"]]

    df = df.merge(own, on=["season", "game_id", "team_id"], how="left")
    df = df.merge(opp, on=["season", "game_id", "opponent_id"], how="left")
    return df.drop(columns=["_season_rank"])


def add_new_player_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """What an average player scored in games played BEFORE this date,
    league-wide -- the honest fallback for rows with no rolling history yet
    (rookies, new arrivals), which every season has (~50-90 players).

    Leakage-safe by construction: daily league means -> expanding mean over
    time -> shifted one day, then merged back on (season, date). A game's own
    results therefore never enter its own day's baseline, and a season's
    first day picks up the previous season's final level (seasons are
    concatenated chronologically before the expanding mean)."""
    df = _season_sort_key(df)
    work = df[["season", "_season_rank", "date", "fantasy_points"]].copy()

    day_mean = (
        work.groupby(["season", "_season_rank", "date"], sort=True)["fantasy_points"]
        .mean()
        .reset_index(name="_day_mean")
        .sort_values(["_season_rank", "date"])
    )
    day_mean["new_player_baseline"] = (
        day_mean["_day_mean"].expanding(min_periods=1).mean().shift(1)
    )

    df = df.merge(
        day_mean[["season", "date", "new_player_baseline"]],
        on=["season", "date"], how="left",
    )
    return df.drop(columns=["_season_rank"])


def build_feature_table(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full feature pipeline. `fantasy_points` (this row's own
    outcome) stays the training target; every added column is lagged/rolling
    or known pre-game (rest, home, matchup)."""
    df = add_rolling_form_features(df)
    df = add_availability_features(df)
    df = add_team_form_features(df)
    df = add_new_player_baseline(df)
    return df


if __name__ == "__main__":
    import os

    in_path = os.path.join("data", "processed", "historical_games.parquet")
    df = pd.read_parquet(in_path)
    print(f"Loaded {len(df):,} rows")

    featured = build_feature_table(df)
    print(f"Built features, {featured.shape[1]} columns")

    # Leakage sanity check: a player's FIRST career game must carry no
    # rolling average of their own points (career_game_count == 0 rows).
    first_games = featured[featured["career_game_count"] == 0]
    has_signal = first_games["fantasy_points_avg_last_3"].notna().sum()
    print(f"First-career-game rows with a rolling avg (must be 0): {has_signal}")

    out_path = os.path.join("data", "processed", "features.parquet")
    featured.to_parquet(out_path, index=False)
    print(f"Saved to {out_path}")
