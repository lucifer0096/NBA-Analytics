"""Scoring: box-score stats -> the model's weighted target.

The target (column `fantasy_points`, kept as the pipeline's canonical
name) is a CONFIGURABLE weighted sum of a box score, defaulting to a
standard points-league scoring line. Swap the weights dict and the same
pipeline trains under any other scoring rules.

9-category leagues are deliberately NOT modeled here: category leagues
score by RANKING participants within each category across a week, which
is not a per-player additive quantity -- a player's "9-cat value" depends
on who else is in the lineup. What DOES work for category leagues (and is
future work, see docs/MODELING.md) is modeling each stat category
separately and letting the consumer weight them. This module provides
the per-category pieces that would feed that.

Every column name here matches parsing.py's player-game row schema.
"""

# Standard points-league default: the common 1-pt-per-point line used by
# most public points leagues, with the usual defensive-event bonuses and
# turnover penalty. These are PROJECT defaults, not any platform's published
# scheme (the project deliberately targets no platform).
DEFAULT_POINTS_WEIGHTS = {
    "pts": 1.0,
    "reb": 1.0,
    "ast": 2.0,
    "stl": 3.0,
    "blk": 3.0,
    "to": -1.0,
}

# Common alternative: ESPN-style default points scoring (rebounds/assists
# worth fractional amounts) -- kept as a second preset to prove the weights
# really are pluggable, not hardcoded into the model.
ESPN_STYLE_WEIGHTS = {
    "pts": 1.0,
    "reb": 1.2,
    "ast": 1.5,
    "stl": 3.0,
    "blk": 3.0,
    "to": -1.0,
}

# Stats a score can be built from -- the canonical stat list both
# the scorer and the future per-category model share.
STAT_COLUMNS = ["pts", "reb", "ast", "stl", "blk", "to"]


def score_row(row: dict, weights: dict = None) -> float:
    """Scoring target for ONE player-game row under `weights`."""
    weights = DEFAULT_POINTS_WEIGHTS if weights is None else weights
    total = 0.0
    for stat, weight in weights.items():
        value = row.get(stat) or 0
        total += weight * value
    return float(total)


def score_dataframe(df, weights: dict = None):
    """Add a `fantasy_points` column to a player-game DataFrame (vectorized;
    DNP rows score 0 since every stat column is 0 there)."""
    weights = DEFAULT_POINTS_WEIGHTS if weights is None else weights
    total = None
    for stat, weight in weights.items():
        if stat not in df.columns:
            continue
        contribution = df[stat].fillna(0) * weight
        total = contribution if total is None else total + contribution
    df = df.copy()
    df["fantasy_points"] = (total if total is not None else 0.0).astype(float)
    return df
