"""Lineup optimizer: best Fantasy roster from a player pool, via PuLP.

Platform-agnostic by design (README: no Yahoo/ESPN fantasy target): what's
encoded here is the STRUCTURE every points-league lineup has -- a fixed set
of roster slots, each accepting certain positions, each player used at most
once -- not any one platform's salary cap or exact slot names. The scoring
itself comes from projected_points (predict.py), which comes from the
configured weights (scoring.py); swap those and the same optimizer solves
under your league's rules.

Default slots follow the common 7-man start (2 G / 2 F / 1 C / 2 UTIL).
ESPN's positions are coarse G/F/C (its roster endpoint -- see
load_historical), so PG/SG-style inputs are bucketed into G/F for
compatibility with other tools' pools.
"""

import pandas as pd
from pulp import LpMaximize, LpProblem, LpStatus, LpVariable, lpSum, PULP_CBC_CMD

# Position -> the specialized slot buckets it may fill (UTIL is implicit:
# every known position may fill UTIL).
POSITION_BUCKETS = {
    "G": {"G"}, "PG": {"G"}, "SG": {"G"},
    "F": {"F"}, "SF": {"F"}, "PF": {"F"},
    "C": {"C"},
}

DEFAULT_SLOTS = {"G": 2, "F": 2, "C": 1, "UTIL": 2}


class InfeasiblePool(ValueError):
    """The pool can't fill the requested slots (e.g. no center available)."""


def _bucket(position: str) -> set:
    return POSITION_BUCKETS.get(str(position).upper(), set())


def best_lineup(pool: pd.DataFrame,
                slots: dict = None,
                pred_col: str = "projected_points") -> pd.DataFrame:
    """Optimal lineup maximizing the sum of `pred_col` under slot rules.

    pool: DataFrame with player_id, player_name, position, pred_col
          (extra columns like team_id pass through on the result).
    slots: {bucket: count}, DEFAULT_SLOTS if omitted. Bucket keys are
          G/F/C/UTIL.
    Returns one row per assigned slot, ordered G..F..C..UTIL, with a
    `slot` column; total projected points maximize over valid assignments.

    Raises InfeasiblePool when no assignment fills every slot (the solver
    would otherwise return an arbitrary infeasible/None status)."""
    slots = DEFAULT_SLOTS if slots is None else dict(slots)
    if pool.empty:
        raise InfeasiblePool("empty player pool")

    usable = pool[pool[pred_col].notna()].copy()
    if usable.empty:
        raise InfeasiblePool(f"no pool rows with a {pred_col!r} value")

    problem = LpProblem("best_lineup", LpMaximize)
    players = list(usable.index)

    # x[player, bucket] only exists where eligible -- an invalid (player,
    # bucket) pair simply isn't a variable, which is what makes "C slot
    # needs a center" structural rather than a penalty term.
    variables = {}
    for idx in players:
        eligible = _bucket(usable.loc[idx, "position"])
        for bucket, count in slots.items():
            if count > 0 and (bucket == "UTIL" or bucket in eligible):
                variables[(idx, bucket)] = LpVariable(
                    f"x_{idx}_{bucket}", cat="Binary"
                )

    if not variables:
        raise InfeasiblePool("no eligible player/slot assignments at all")

    problem += lpSum(
        float(usable.loc[idx, pred_col]) * var
        for (idx, bucket), var in variables.items()
    )

    # Fill every slot bucket exactly its count...
    for bucket, count in slots.items():
        assigned = [var for (idx, b), var in variables.items() if b == bucket]
        if len(assigned) < count:
            raise InfeasiblePool(
                f"only {len(assigned)} player(s) eligible for {bucket} slot(s) "
                f"(need {count}) -- pool positions: "
                f"{sorted(usable['position'].dropna().unique())}"
            )
        problem += lpSum(assigned) == count

    # ...and use each player at most once across all buckets.
    for idx in players:
        mine = [var for (i, _), var in variables.items() if i == idx]
        if mine:
            problem += lpSum(mine) <= 1

    problem.solve(PULP_CBC_CMD(msg=False))
    if LpStatus[problem.status] != "Optimal":
        raise InfeasiblePool(f"solver status {LpStatus[problem.status]}")

    chosen = []
    for (idx, bucket), var in variables.items():
        if var.value() and var.value() > 0.5:
            row = usable.loc[idx].to_dict()
            row["slot"] = bucket
            chosen.append(row)
    result = pd.DataFrame(chosen)

    # Display order: specialized slots before UTIL, stable within a bucket
    # by projected points descending.
    order = {bucket: i for i, bucket in enumerate(
        [b for b in slots if b != "UTIL"] + ["UTIL"]
    )}
    result["_ord"] = result["slot"].map(order)
    result = result.sort_values(
        ["_ord", pred_col], ascending=[True, False]
    ).drop(columns="_ord").reset_index(drop=True)
    return result


def total_points(lineup: pd.DataFrame, pred_col: str = "projected_points") -> float:
    return float(lineup[pred_col].sum())


if __name__ == "__main__":
    # Quick self-demo with a synthetic pool (real usage: predict.project_upcoming's
    # output filtered to one team-day).
    pool = pd.DataFrame([
        {"player_id": 1, "player_name": "Alpha Guard", "position": "G", "projected_points": 48.2},
        {"player_id": 2, "player_name": "Beta Wing", "position": "F", "projected_points": 51.0},
        {"player_id": 3, "player_name": "Gamma Big", "position": "C", "projected_points": 55.5},
        {"player_id": 4, "player_name": "Delta Forward", "position": "F", "projected_points": 33.1},
        {"player_id": 5, "player_name": "Epsilon Guard", "position": "G", "projected_points": 29.9},
        {"player_id": 6, "player_name": "Zeta Big", "position": "C", "projected_points": 21.4},
        {"player_id": 7, "player_name": "Eta Guard", "position": "G", "projected_points": 18.7},
    ])
    lineup = best_lineup(pool)
    print(lineup[["slot", "player_name", "position", "projected_points"]].to_string(index=False))
    print(f"Total: {total_points(lineup):.1f}")
