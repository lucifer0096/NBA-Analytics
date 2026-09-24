"""Deterministic model tests: scoring, leak-free features, the chronological
split, projection-row building, and optimizer constraints. Synthetic data --
no network, no collected files needed (pytest.ini's default run).
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import features
import load_historical
import optimizer
import scoring
import train

# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def test_score_row_default_weights():
    row = {"pts": 20, "reb": 10, "ast": 5, "stl": 2, "blk": 1, "to": 3}
    # 20*1 + 10*1 + 5*2 + 2*3 + 1*3 + 3*(-1) = 20+10+10+6+3-3 = 46
    assert scoring.score_row(row) == 46.0


def test_score_row_dnp_is_zero():
    assert scoring.score_row({"min": 0, "pts": 0, "reb": 0, "ast": 0,
                              "stl": 0, "blk": 0, "to": 0}) == 0.0


def test_score_dataframe_weights_are_pluggable():
    df = pd.DataFrame([{"pts": 10, "reb": 5, "ast": 0, "stl": 0, "blk": 0, "to": 0}])
    default = scoring.score_dataframe(df)
    assert default["fantasy_points"].iloc[0] == 15.0  # 10*1 + 5*1
    custom = scoring.score_dataframe(df, weights={"pts": 2.0})
    assert custom["fantasy_points"].iloc[0] == 20.0
    # input frame untouched (score_dataframe copies)
    assert "fantasy_points" not in df.columns


# ---------------------------------------------------------------------------
# synthetic player-game history for feature tests
# ---------------------------------------------------------------------------

def _game_rows():
    """Two players across 4 games: p1 plays all four, p2 debuts in game 3.
    team 1 vs team 2 every time (keeps team-form join simple)."""
    rows = []
    dates = ["2021-01-01", "2021-01-03", "2021-01-05", "2021-01-06"]
    for i, date in enumerate(dates):
        rows.append({
            "game_id": 100 + i, "date": date, "season": "2020-21",
            "team_id": 1, "opponent_id": 2, "is_home": i % 2 == 0,
            "player_id": 11, "player_name": "P One", "position": "G",
            "min": 30 + i, "pts": 10 + i, "reb": 5, "ast": 3,
            "stl": 1, "blk": 0, "to": 2,
            "team_score": 100 + i, "opp_score": 95 + i,
            "played": 1, "did_not_play": 0, "starter": True,
            "fantasy_points": float(20 + i),
        })
    for i in (2, 3):  # p2's first two CAREER games are games 3 and 4
        rows.append({
            "game_id": 100 + i, "date": dates[i], "season": "2020-21",
            "team_id": 1, "opponent_id": 2, "is_home": i % 2 == 0,
            "player_id": 22, "player_name": "P Two", "position": "F",
            "min": 20, "pts": 8, "reb": 4, "ast": 2,
            "stl": 0, "blk": 1, "to": 1,
            "team_score": 100 + i, "opp_score": 95 + i,
            "played": 1, "did_not_play": 0, "starter": False,
            "fantasy_points": 15.0,
        })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def featured():
    return features.build_feature_table(_game_rows())


# ---------------------------------------------------------------------------
# feature leakage guarantees
# ---------------------------------------------------------------------------

def test_first_career_game_has_no_rolling_average(featured):
    first = featured[featured["career_game_count"] == 0]
    assert len(first) == 2  # both players' debuts
    assert first["fantasy_points_avg_last_3"].isna().all()
    assert first["fantasy_points_avg_last_5"].isna().all()
    assert first["fantasy_points_avg_last_10"].isna().all()


def test_rolling_average_excludes_own_row(featured):
    """p1's game-2 rolling-3 must equal ONLY game 1's value -- including
    this row's own outcome would make it (20+21)/2 after two games."""
    row = featured[(featured["player_id"] == 11) &
                   (featured["date"] == "2021-01-03")].iloc[0]
    assert row["fantasy_points_avg_last_3"] == 20.0  # only the Jan-01 game
    row3 = featured[(featured["player_id"] == 11) &
                    (featured["date"] == "2021-01-05")].iloc[0]
    assert row3["fantasy_points_avg_last_3"] == pytest.approx((20.0 + 21.0) / 2)


def test_season_game_count_resets(featured):
    counts = featured[featured["player_id"] == 11].sort_values("date")
    assert list(counts["season_game_count"]) == [0, 1, 2, 3]


def test_rest_days_and_back_to_back(featured):
    p1 = featured[featured["player_id"] == 11].sort_values("date")
    rest = list(p1["rest_days"])
    b2b = list(p1["back_to_back"])
    # dates: Jan 1 -> Jan 3 (rest 1), Jan 3 -> Jan 5 (rest 1), Jan 5 -> Jan 6 (B2B)
    assert np.isnan(rest[0])
    assert rest[1:] == [1.0, 1.0, 0.0]
    assert b2b == [0, 0, 0, 1]


def test_team_form_is_lagged_not_same_game(featured):
    """team_form_pf for the FIRST game must be NaN (no prior team game),
    and for the second game it must be game 1's score -- never this game's."""
    first = featured[featured["date"] == "2021-01-01"].iloc[0]
    assert np.isnan(first["team_form_pf"])
    second = featured[featured["date"] == "2021-01-03"].iloc[0]
    assert second["team_form_pf"] == 100.0  # Jan-01's team score, not 101


def test_new_player_baseline_never_includes_own_day(featured):
    debut_day = featured[(featured["player_id"] == 22) &
                         (featured["date"] == "2021-01-05")].iloc[0]
    # Baseline for Jan-05 = expanding mean of Jan-01 and Jan-03 daily means
    # only: days 1&2 mean = ((20+15)/2? no -- Jan-01 has only p1 -> 20;
    # Jan-03 only p1 -> 21) expanding mean of daily means = (20+21)/2 = 20.5.
    assert debut_day["new_player_baseline"] == pytest.approx(20.5)


def test_feature_columns_exist_and_target_not_leaked(featured):
    for column in train.FEATURE_COLUMNS:
        assert column in featured.columns, f"missing feature {column}"
    # The target itself must never be an input.
    assert "fantasy_points" not in train.FEATURE_COLUMNS
    assert "min" not in train.FEATURE_COLUMNS


# ---------------------------------------------------------------------------
# chronological split
# ---------------------------------------------------------------------------

def test_chronological_split_excludes_holdout_and_later():
    df = pd.DataFrame({
        "season": ["2022-23", "2023-24", "2024-25", "2025-26", "2026-27"],
        "fantasy_points": [1.0] * 5,
    })
    split_train, split_val = train.chronological_split(df)
    assert sorted(split_train["season"].unique()) == ["2022-23", "2023-24"]
    assert list(split_val["season"].unique()) == ["2024-25"]
    # holdout + live season in neither
    assert "2025-26" not in set(split_train["season"]) | set(split_val["season"])
    assert "2026-27" not in set(split_train["season"]) | set(split_val["season"])


def test_default_model_seasons_are_disk_driven_and_include_upcoming(monkeypatch):
    monkeypatch.setattr(
        load_historical,
        "available_seasons",
        lambda: ["2009-10", "2010-11", "2025-26", "2026-27", "2027-28"],
    )
    assert load_historical.default_seasons() == [
        "2010-11", "2025-26", "2026-27", "2027-28",
    ]


# ---------------------------------------------------------------------------
# schedule join (load_season_games) -- regression: player rows carry
# is_home/team_id/opponent_id, never home_id, so the cross-check must
# reconstruct the box score's home team instead of reading a column that
# doesn't exist (this crashed the real backfill while unit tests passed).
# ---------------------------------------------------------------------------

def _write_season(tmp_path, monkeypatch, disagree=False):
    """One collected game + its schedule.csv under a temp RAW_DIR."""
    raw = tmp_path / "raw"
    games = raw / "2020-21" / "games"
    games.mkdir(parents=True)
    # Box score: team 1 hosts team 2 (is_home on team 1's rows).
    players = [
        {"game_id": 100, "player_id": 11, "player_name": "P One",
         "team_id": 1, "opponent_id": 2, "is_home": True,
         "min": 30, "pts": 20, "reb": 5, "ast": 3, "stl": 1, "blk": 0, "to": 2},
        {"game_id": 100, "player_id": 22, "player_name": "P Two",
         "team_id": 2, "opponent_id": 1, "is_home": False,
         "min": 28, "pts": 15, "reb": 4, "ast": 2, "stl": 0, "blk": 1, "to": 1},
    ]
    (games / "100.json").write_text(json.dumps({"players": players}))
    # Schedule agrees -- unless the test asks for a home-team mismatch.
    home_id = 2 if disagree else 1
    pd.DataFrame([{
        "game_id": 100, "home_id": home_id, "away_id": 2 if not disagree else 1,
        "home_score": 110, "away_score": 101, "status": "STATUS_FINAL",
    }]).to_csv(raw / "2020-21" / "schedule.csv", index=False)
    monkeypatch.setattr(load_historical, "RAW_DIR", str(raw))
    return load_historical.load_season_games("2020-21")


def test_load_season_games_joins_schedule_scores(tmp_path, monkeypatch):
    df = _write_season(tmp_path, monkeypatch, disagree=False)
    assert not df.empty
    # team_score/opp_score come from the schedule's final scores.
    home = df[df["is_home"]].iloc[0]
    away = df[~df["is_home"]].iloc[0]
    assert (home["team_score"], home["opp_score"]) == (110, 101)
    assert (away["team_score"], away["opp_score"]) == (101, 110)


def test_load_season_games_drops_schedule_context_on_home_disagreement(
        tmp_path, monkeypatch):
    # Schedule says team 2 hosted; the box score says team 1 did. The loader
    # must warn-by-nulling rather than mix the two sources.
    df = _write_season(tmp_path, monkeypatch, disagree=True)
    assert df["team_score"].isna().all()
    assert df["opp_score"].isna().all()


# ---------------------------------------------------------------------------
# projection rows
# ---------------------------------------------------------------------------

def test_build_projection_rows_fills_from_history():
    import predict

    history = _game_rows()
    upcoming = pd.DataFrame([{
        "game_id": 999, "date": "2021-01-09", "season": "2020-21",
        "home_id": 1, "away_id": 2, "status": "STATUS_SCHEDULED",
    }])
    player_teams = pd.DataFrame([
        {"player_id": 11, "team_id": 1, "position": "G"},
        {"player_id": 33, "team_id": 2, "position": "C"},  # never played before
        {"player_id": 44, "team_id": 3, "position": "G"},  # not in this game
    ])
    rows = predict.build_projection_rows(history, upcoming, player_teams)
    assert set(rows["player_id"]) == {11, 33}  # player 44's team isn't playing

    p11 = rows[rows["player_id"] == 11].iloc[0]
    assert p11["is_home"] and p11["opponent_id"] == 2
    # rolling avg filled from real history (not NaN, not the target itself)
    assert p11["fantasy_points_avg_last_5"] == pytest.approx((20 + 21 + 22 + 23) / 4)
    # rest: last game Jan 06 -> Jan 09 = 2 off
    assert p11["rest_days"] == 2

    p33 = rows[rows["player_id"] == 33].iloc[0]
    # brand-new player: no rolling history, but baseline/team-form available
    assert np.isnan(p33["fantasy_points_avg_last_3"])
    assert not np.isnan(p33["new_player_baseline"])


def test_build_projection_rows_does_not_treat_a_future_game_as_prior_history():
    import predict

    history = _game_rows()
    upcoming = pd.DataFrame([
        {"game_id": 999, "date": "2021-01-09", "season": "2020-21",
         "home_id": 1, "away_id": 2, "status": "STATUS_SCHEDULED"},
        {"game_id": 1000, "date": "2021-01-11", "season": "2020-21",
         "home_id": 1, "away_id": 2, "status": "STATUS_SCHEDULED"},
    ])
    player_teams = pd.DataFrame([{"player_id": 11, "team_id": 1, "position": "G"}])
    rows = predict.build_projection_rows(history, upcoming, player_teams)
    second = rows[rows["game_id"] == 1000].iloc[0]

    # The Jan-09 placeholder has no result and must not shrink the rolling
    # window or replace the real last-game/rest signals for Jan-11.
    assert second["fantasy_points_avg_last_3"] == pytest.approx((21 + 22 + 23) / 3)
    assert second["rest_days"] == 4  # Jan-06 -> Jan-11 = four off days
    assert second["career_game_count"] == 4


# ---------------------------------------------------------------------------
# optimizer
# ---------------------------------------------------------------------------

def _pool():
    return pd.DataFrame([
        {"player_id": 1, "player_name": "G1", "position": "G", "projected_points": 50},
        {"player_id": 2, "player_name": "G2", "position": "G", "projected_points": 40},
        {"player_id": 3, "player_name": "G3", "position": "G", "projected_points": 30},
        {"player_id": 4, "player_name": "F1", "position": "F", "projected_points": 45},
        {"player_id": 5, "player_name": "F2", "position": "F", "projected_points": 35},
        {"player_id": 6, "player_name": "C1", "position": "C", "projected_points": 44},
        {"player_id": 7, "player_name": "C2", "position": "C", "projected_points": 10},
    ])


def test_best_lineup_fills_every_slot_once():
    lineup = optimizer.best_lineup(_pool())
    assert len(lineup) == 7  # 2G + 2F + 1C + 2UTIL
    assert lineup["player_id"].is_unique
    assert (lineup["slot"] == "G").sum() == 2
    assert (lineup["slot"] == "F").sum() == 2
    assert (lineup["slot"] == "C").sum() == 1
    assert (lineup["slot"] == "UTIL").sum() == 2


def test_best_lineup_respects_position_eligibility():
    lineup = optimizer.best_lineup(_pool())
    # The C slot must hold a center; G slots only guards.
    c_slot = lineup[lineup["slot"] == "C"].iloc[0]
    assert c_slot["position"] == "C"
    for _, row in lineup[lineup["slot"] == "G"].iterrows():
        assert row["position"] == "G"


def test_best_lineup_maximizes_total():
    lineup = optimizer.best_lineup(_pool())
    total = optimizer.total_points(lineup)
    # Optimal: G1,G2 (90) + F1,F2 (80) + C1 (44) + UTIL = G3(30)+C2(10)... but
    # UTIL should take the two best REMAINING after specialized slots:
    # specialized picks are forced-G (top2 G), forced-F (only 2), forced-C
    # (C1). Remaining pool: G3(30), C2(10) -> UTIL. Total = 90+80+44+40 = 254.
    assert total == pytest.approx(254)


def test_best_lineup_puts_best_center_in_util_when_worthwhile():
    pool = _pool()
    pool.loc[pool["player_id"] == 6, "projected_points"] = 90  # elite C1
    lineup = optimizer.best_lineup(pool)
    c_slot = lineup[lineup["slot"] == "C"].iloc[0]
    util_ids = set(lineup[lineup["slot"] == "UTIL"]["player_id"])
    # C1 (90) should go UTIL, C2 (10) fills the C slot.
    assert c_slot["player_id"] == 7
    assert 6 in util_ids


def test_infeasible_pool_raises():
    pool = _pool()
    pool = pool[pool["position"] != "C"]  # no center at all
    with pytest.raises(optimizer.InfeasiblePool):
        optimizer.best_lineup(pool)


def test_empty_pool_raises():
    with pytest.raises(optimizer.InfeasiblePool):
        optimizer.best_lineup(pd.DataFrame(columns=["player_id", "player_name",
                                                     "position", "projected_points"]))


def test_nba_style_positions_bucket_correctly():
    pool = pd.DataFrame([
        {"player_id": i, "player_name": f"P{i}", "position": pos,
         "projected_points": float(50 - i)}
        for i, pos in enumerate(["PG", "SG", "SF", "PF", "C", "C", "PG"], start=1)
    ])
    lineup = optimizer.best_lineup(pool)
    for _, row in lineup[lineup["slot"] == "G"].iterrows():
        assert row["position"] in ("PG", "SG")
    for _, row in lineup[lineup["slot"] == "F"].iterrows():
        assert row["position"] in ("SF", "PF")
