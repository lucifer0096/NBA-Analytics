"""Load this project's OWN collector output into one training table.

Unlike FPL-Analytics (which needs a third-party archive for finished
seasons), every season here comes from the same place: this repo's own
collector snapshots under data/raw/{season}/games/*.json -- one parsed
box-score file per finalized game, written by snapshot.py. That means:

- one schema for the whole 2010-11..present window (no per-era column
  reconciliation -- the single hardest part of the FPL loader simply
  doesn't exist here);
- player identity is ESPN's athlete id, which is stable across seasons
  (verified: box-score athlete.id is the same numeric id the roster
  endpoint uses), so rolling features can group by `player_id` directly
  -- no FPL-style "element id resets every season" trap;
- team scores/schedule context join from each season's schedule.csv,
  which the same collector wrote (giving every player row its game's
  final team scores -- the input to team/opponent form features).

POSITION: joined from data/raw/player_positions.json, the collector's
CURRENT-state position map (see snapshot_player_positions for why it's
current-only: ESPN's roster endpoint ignores its season param). Active
players get G/F/C; players who've since retired come back UNK. Position
is intentionally NOT a model feature (it would encode "how modern is
this row" via missingness) -- it exists for the lineup optimizer's slot
constraints, which only ever act on today's players anyway.
"""

import glob
import json
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))

import scoring  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
PROCESSED_DIR = os.path.join(REPO_ROOT, "data", "processed")

SEASON_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

# The deliberate modeling window: pre-2010 seasons exist in the API but are
# not part of the default table. The upper bound is deliberately dynamic --
# once the collector starts a new season, its collected games are included
# without a code change or a stale season-list update.
MIN_MODELING_SEASON = 2010


def default_seasons() -> list:
    """Collected seasons from the modeling window, oldest first.

    This is based on what is actually on disk rather than a hard-coded
    `2010-11..2026-27` list. That keeps the current/upcoming season in the
    training/projection input automatically while still excluding pre-2010
    history by default.
    """
    return sorted(
        season for season in available_seasons()
        if int(season[:4]) >= MIN_MODELING_SEASON
    )


def available_seasons() -> list:
    """Season labels that actually have collected games on disk."""
    if not os.path.isdir(RAW_DIR):
        return []
    seasons = [
        name for name in os.listdir(RAW_DIR)
        if SEASON_DIR_RE.match(name)
        and os.path.isdir(os.path.join(RAW_DIR, name, "games"))
    ]
    return sorted(seasons)


def load_positions() -> pd.DataFrame:
    """Current player -> position (G/F/C) map; empty frame if never fetched."""
    path = os.path.join(RAW_DIR, "player_positions.json")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["player_id", "position", "team_id"])
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload.get("players") if isinstance(payload, dict) else payload
    df = pd.DataFrame(rows or [])
    if df.empty:
        return pd.DataFrame(columns=["player_id", "position", "team_id"])
    # A player can appear on two current rosters' lists (rare trades between
    # the two fetches) -- keep the first; position is what matters here.
    return df.drop_duplicates(subset="player_id")[["player_id", "position", "team_id"]]


def load_schedule(season: str) -> pd.DataFrame:
    """One season's schedule.csv (the same file the collector maintains)."""
    path = os.path.join(RAW_DIR, season, "schedule.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def load_season_games(season: str) -> pd.DataFrame:
    """All parsed box scores for one season -> one player-game DataFrame,
    with the game's final team scores and schedule context joined in."""
    games_dir = os.path.join(RAW_DIR, season, "games")
    files = sorted(glob.glob(os.path.join(games_dir, "*.json")))
    if not files:
        return pd.DataFrame()

    player_rows = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        players = payload.get("players") or []
        player_rows.extend(players)
    df = pd.DataFrame(player_rows)
    df["season"] = season

    # Join team-level game context (final scores, home assignment as the
    # SCHEDULE sees it) from schedule.csv -- used by team/opponent form.
    sched = load_schedule(season)
    if not sched.empty:
        sched = sched[["game_id", "home_score", "away_score", "home_id", "away_id"]].copy()
        merged = df.merge(sched, on="game_id", how="left", suffixes=("", "_sched"))
        # Cross-check: box-score and schedule home/away assignment must agree
        # for rows where both exist (they're written by two different ESPN
        # endpoints -- a mismatch would silently corrupt team form).
        both = merged["home_id_sched"].notna()
        disagrees = both & (merged["home_id"] != merged["home_id_sched"])
        if disagrees.any():
            print(f"  WARNING [{season}]: {int(disagrees.sum())} rows where box-score "
                  f"and schedule disagree on home team -- schedule context dropped "
                  f"for those rows")
        merged.loc[disagrees, ["home_score", "away_score", "home_id_sched", "away_id_sched"]] = pd.NA
        df = merged.drop(columns=["home_id_sched", "away_id_sched"])
        df["team_score"] = df["home_score"].where(df["is_home"], df["away_score"])
        df["opp_score"] = df["away_score"].where(df["is_home"], df["home_score"])
        df = df.drop(columns=["home_score", "away_score"])
    else:
        df["team_score"] = pd.NA
        df["opp_score"] = pd.NA

    return df


def load_all_seasons(seasons: list = None) -> pd.DataFrame:
    """The full training table: every collected season in the default
    2010-onward modeling window (including the current/upcoming season once
    it has games), positions joined, fantasy_points scored under the default
    weights."""
    if seasons is None:
        seasons = default_seasons()

    frames = []
    for season in seasons:
        df = load_season_games(season)
        if df.empty:
            print(f"  [{season}] no collected games yet -- skipped")
            continue
        print(f"  [{season}] {len(df):,} player-game rows "
              f"({df['game_id'].nunique():,} games)")
        frames.append(df)

    if not frames:
        raise FileNotFoundError(
            "No collected games under data/raw/*/games/ -- run "
            "`python src/collector/snapshot.py --backfill` first."
        )

    combined = pd.concat(frames, ignore_index=True)

    positions = load_positions()
    if positions.empty:
        print("  WARNING: no player_positions.json -- position column is all UNK")
        combined["position"] = "UNK"
        combined["current_team_id"] = pd.NA
    else:
        combined = combined.merge(
            positions[["player_id", "position", "team_id"]].rename(
                columns={"team_id": "current_team_id"}
            ),
            on="player_id", how="left",
        )
        combined["position"] = combined["position"].fillna("UNK")

    # Fantasy points under the configured scoring (the model's target).
    combined = scoring.score_dataframe(combined)
    combined["played"] = (combined["min"].fillna(0) > 0).astype(int)

    print(f"Total: {len(combined):,} player-game rows across "
          f"{len(frames)} seasons, {combined['game_id'].nunique():,} games "
          f"({combined['played'].mean():.0%} played).")
    return combined


if __name__ == "__main__":
    df = load_all_seasons()
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    out_path = os.path.join(PROCESSED_DIR, "historical_games.parquet")
    df.to_parquet(out_path, index=False)
    print(f"Saved to {out_path}")
    print(df["season"].value_counts().sort_index().to_string())
