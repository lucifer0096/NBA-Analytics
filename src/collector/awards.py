"""Award-race + stat-leader computation from this repo's own collected data.

Pure stdlib (csv + json, no pandas) so the daily collector workflow stays
light. Reads data/raw/{season}/games/*.json (one parsed box score per final
game) and data/raw/{season}/schedule.csv (final scores -> team records).

HONEST FRAMING: these are homegrown, transparently-weighted metrics (each
formula is a one-liner documented on its function and repeated verbatim in
the dashboard caption) -- NOT official NBA voting, awards, or any vendor's
proprietary rating. The point is that a reader can check the math.

Races:
    MVP      production impact x team-success multiplier
    DPOY     stocks (STL+BLK) per game x team defensive multiplier
    6th Man  bench scoring/playmaking (players who mostly did NOT start)
    MIP      year-over-year impact improvement (needs the prior season)

Plus per-game stat leaders (PTS/REB/AST/STL/BLK) behind a games-played
qualifier, the same way rate titles work.

Season convention follows espn_api: a season label's END year is the calendar
year it finishes in (2025-26 games live under data/raw/2025-26/).
"""

import csv
import glob
import json
import os
import re
from collections import Counter

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
SEASON_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

# Impact weights, applied identically in MVP and MIP so the two races are
# comparable: one point ~ one point scored, a rebound worth 3/4 of a bucket,
# an assist worth a full one (roughly the traditional points-added intuition).
IMPACT_RPG_WEIGHT = 0.75
IMPACT_APG_WEIGHT = 1.00

# A rotation player is a "bench" player for the 6th-Man race when he started
# at most this fraction of the games he played in.
SIXTH_MAN_MAX_START_PCT = 0.40

# Team-success / team-defense multipliers are bounded so a great player on a
# mediocre team is discounted (0.6x) but never erased, while a great player on
# the league's best team tops out at 1.0x.
MULTIPLIER_FLOOR = 0.60

STAT_CATEGORIES = ("pts", "reb", "ast", "stl", "blk")
LEADERS_PER_CATEGORY = 25
RACE_SIZE = 10

# Display metadata shared with the dashboard (shared.py imports these so the
# formula text shown in a caption is the SAME string that defines the math --
# no drift between what's computed and what's claimed on screen).
STAT_LABELS = {"pts": "Points", "reb": "Rebounds", "ast": "Assists",
               "stl": "Steals", "blk": "Blocks"}
STAT_ABBR = {"pts": "PPG", "reb": "RPG", "ast": "APG", "stl": "SPG", "blk": "BPG"}
RACE_LABELS = {"mvp": "MVP race", "dpoy": "DPOY race",
               "sixth_man": "6th Man race", "mip": "MIP race"}
RACE_EMOJI = {"mvp": "🏆", "dpoy": "🛡️", "sixth_man": "🪑", "mip": "📈"}
RACE_FORMULAS = {
    "mvp": ("Impact per game (PTS + 0.75·REB + 1.0·AST) scaled by the "
            "team's win percentage, 0.6× floor to 1.0× at a perfect record."),
    "dpoy": ("Stocks per game (STL + BLK) scaled by the team's rank in "
             "points allowed, 0.6× floor to 1.0× for the best defense."),
    "sixth_man": ("Bench production per game (PTS + 0.5·AST + 0.25·REB), "
                  "players who started at most 40% of their games."),
    "mip": ("Year-over-year change in per-game impact vs the previous "
            "season; both seasons must clear the games-played qualifier."),
}


def previous_season(season: str) -> str:
    """'2025-26' -> '2024-25' (MIP needs the year before)."""
    start = int(season[:4])
    prev_start = start - 1
    return f"{prev_start}-{(prev_start + 1) % 100:02d}"


def min_games_for(max_team_games: int) -> int:
    """Rate-title qualifier: half the games any team played (so the bar grows
    naturally as the season progresses), floored at 8 so early-season runs
    still produce a race instead of an empty tab."""
    return max(8, int(0.5 * max_team_games))


def aggregate_players(season: str, raw_dir: str = None) -> list:
    """Per-player season aggregates from every stored box score.

    Games where the player was flagged did_not_play don't count toward games
    or starts (same semantics as refresh_leaderboards' `games`). A traded
    player's team is the one he appeared for in the most games; ties go to
    whichever comes first in sorted game-id order (deterministic).
    """
    raw = raw_dir or RAW_DIR
    files = sorted(glob.glob(os.path.join(raw, season, "games", "*.json")))
    if not files:
        return []

    players: dict = {}
    for path in files:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        for row in payload.get("players") or []:
            if row.get("did_not_play"):
                continue
            entry = players.get(row["player_id"])
            if entry is None:
                entry = players[row["player_id"]] = {
                    "player_id": row["player_id"],
                    "player_name": row.get("player_name"),
                    "team_abbrev": None,
                    "_team_counts": Counter(),
                    "gp": 0, "starts": 0, "minutes": 0,
                    "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "to": 0,
                }
            entry["gp"] += 1
            if row.get("starter"):
                entry["starts"] += 1
            entry["minutes"] += row.get("min") or 0
            for stat in ("pts", "reb", "ast", "stl", "blk", "to"):
                entry[stat] += row.get(stat) or 0
            if row.get("team_abbrev"):
                entry["_team_counts"][row["team_abbrev"]] += 1

    out = []
    for entry in players.values():
        counts = entry.pop("_team_counts")
        entry["team_abbrev"] = counts.most_common(1)[0][0] if counts else None
        out.append(entry)
    return out


def team_records(season: str, raw_dir: str = None) -> dict:
    """Team W-L and points allowed per game from the season's final games.

    Returns {abbrev: {"wins", "losses", "win_pct", "opp_pg", "games"}} and
    also lets callers derive max_team_games for the qualifier. Non-final or
    scoreless schedule rows (the normal pre-tip-off state) are skipped.
    """
    raw = raw_dir or RAW_DIR
    path = os.path.join(raw, season, "schedule.csv")
    stats: dict = {}
    if not os.path.exists(path):
        return stats

    def side(abbrev):
        return stats.setdefault(abbrev, {
            "wins": 0, "losses": 0, "points_for": 0, "points_allowed": 0,
            "games": 0,
        })

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "STATUS_FINAL":
                continue
            try:
                # schedule.csv scores can arrive as '125' or '125.0'
                # (column dtype rounding when the file was written).
                home_score = int(float(row["home_score"]))
                away_score = int(float(row["away_score"]))
            except (KeyError, TypeError, ValueError):
                continue
            home, away = side(row["home_abbrev"]), side(row["away_abbrev"])
            for team, scored, allowed in ((home, home_score, away_score),
                                           (away, away_score, home_score)):
                team["games"] += 1
                team["points_for"] += scored
                team["points_allowed"] += allowed
            if home_score > away_score:
                home["wins"] += 1
                away["losses"] += 1
            else:
                away["wins"] += 1
                home["losses"] += 1

    return {
        abbrev: {
            "wins": t["wins"],
            "losses": t["losses"],
            "win_pct": round(t["wins"] / t["games"], 3) if t["games"] else 0.0,
            "opp_pg": round(t["points_allowed"] / t["games"], 1) if t["games"] else None,
            "games": t["games"],
        }
        for abbrev, t in stats.items()
    }


def max_games_played(records: dict) -> int:
    return max((r["games"] for r in records.values()), default=0)


def _per_game(entry: dict, stat: str) -> float:
    return round(entry[stat] / entry["gp"], 1) if entry["gp"] else 0.0


def _impact(entry: dict) -> float:
    return (entry["pts"]
            + IMPACT_RPG_WEIGHT * entry["reb"]
            + IMPACT_APG_WEIGHT * entry["ast"]) / entry["gp"] if entry["gp"] else 0.0


def mvp_rows(players: list, records: dict, min_gp: int) -> list:
    """MVP ladder: (pts + 0.75*reb + 1.0*ast)/gp x (0.6 + 0.4 * team win%).

    A player's team win percentage scales his production between 0.6x (winless)
    and 1.0x (.500+ ... scaling tops out at 1.0 for a perfect team). Neutral
    0.5 win% is used when the player's team can't be matched to a record
    (traded to a team with no final games yet, abbreviation quirks)."""
    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        record = records.get(p["team_abbrev"] or "")
        win_pct = record["win_pct"] if record else 0.5
        score = _impact(p) * (MULTIPLIER_FLOOR + (1 - MULTIPLIER_FLOOR) * win_pct)
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "wins": record["wins"] if record else None,
            "losses": record["losses"] if record else None,
            "win_pct": round(win_pct, 3),
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["ppg"], r["player_name"] or ""))
    return _ranked(rows)


def dpoy_rows(players: list, records: dict, min_gp: int) -> list:
    """DPOY ladder: (stl + blk)/gp x (0.6 + 0.4 * team defense percentile).

    The team factor is each team's rank in points allowed per game mapped to
    (n - rank0) / n: best defense -> 1.0, worst -> ~0.03. Unknown teams get
    the neutral 0.5. Stock totals are the per-game headline; the team factor
    prevents a shot-happy roamer on a sieve defense from leading outright."""
    ranked_teams = sorted(
        (a for a, r in records.items() if r["opp_pg"] is not None),
        key=lambda a: records[a]["opp_pg"],
    )
    n_teams = len(ranked_teams)
    defense_pct = {
        a: (n_teams - i) / n_teams for i, a in enumerate(ranked_teams)
    }

    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        stocks = (p["stl"] + p["blk"]) / p["gp"] if p["gp"] else 0.0
        team_pct = defense_pct.get(p["team_abbrev"] or "", 0.5)
        score = stocks * (MULTIPLIER_FLOOR + (1 - MULTIPLIER_FLOOR) * team_pct)
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "spg": round(p["stl"] / p["gp"], 1) if p["gp"] else 0.0,
            "bpg": round(p["blk"] / p["gp"], 1) if p["gp"] else 0.0,
            "stocks_pg": round(stocks, 1),
            "opp_pg": (records.get(p["team_abbrev"] or "") or {}).get("opp_pg"),
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["stocks_pg"], r["player_name"] or ""))
    return _ranked(rows)


def sixth_man_rows(players: list, records: dict, min_gp: int) -> list:
    """6th-Man ladder: ppg + 0.5*apg + 0.25*rpg, bench players only.

    Eligible when the player started at most 40% of the games he played in --
    the classic "comes off the bench but carries the second unit" profile.
    Pure production, no team multiplier: bench minutes don't inherit a
    starter's team context, and the roster's bench quality IS the point."""
    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        start_pct = p["starts"] / p["gp"] if p["gp"] else 1.0
        if start_pct > SIXTH_MAN_MAX_START_PCT:
            continue
        score = (p["pts"]
                 + 0.5 * p["ast"]
                 + 0.25 * p["reb"]) / p["gp"] if p["gp"] else 0.0
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "starts": p["starts"],
            "start_pct": round(start_pct, 3),
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["ppg"], r["player_name"] or ""))
    return _ranked(rows)


def mip_rows(players: list, prev_players: list, min_gp: int,
             min_gp_prev: int) -> list:
    """MIP ladder: this season's impact/pg MINUS last season's impact/pg.

    Uses the same impact formula as MVP, so the delta reads as "+N points of
    production per game vs last year". Both seasons must clear their own
    games-played qualifier (a 12-game flash in either year is noise). Empty
    list when the prior season has no collected games -- a normal state, the
    dashboard says so instead of inventing a winner."""
    if not prev_players:
        return []
    prev_by_id = {p["player_id"]: p for p in prev_players if p["gp"] >= min_gp_prev}
    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        prev = prev_by_id.get(p["player_id"])
        if prev is None:
            continue
        delta = _impact(p) - _impact(prev)
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "gp_prev": prev["gp"],
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "ppg_prev": _per_game(prev, "pts"),
            "rpg_prev": _per_game(prev, "reb"),
            "apg_prev": _per_game(prev, "ast"),
            "delta_ppg": round(_per_game(p, "pts") - _per_game(prev, "pts"), 1),
            "score": round(delta, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["delta_ppg"], r["player_name"] or ""))
    return _ranked(rows)


def leader_rows(players: list, min_gp: int) -> dict:
    """Per-game stat leaders behind the same games-played qualifier.

    Returns {category: [top-N rows]} -- per-game rate (the fair cross-team,
    cross-pace comparison) with the season total alongside for context."""
    out = {}
    for stat in STAT_CATEGORIES:
        rows = [{
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "total": p[stat],
            "per_game": _per_game(p, stat),
            # Full per-game line so the dashboard's hover tooltip can show a
            # player's whole stat line, not just this category.
            "mpg": _per_game(p, "minutes"),
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "spg": _per_game(p, "stl"),
            "bpg": _per_game(p, "blk"),
        } for p in players if p["gp"] >= min_gp]
        rows.sort(key=lambda r: (-r["per_game"], -r["total"], r["player_name"] or ""))
        out[stat] = _ranked(rows[:LEADERS_PER_CATEGORY])
    return out


def _ranked(rows: list) -> list:
    for i, row in enumerate(rows[:RACE_SIZE], start=1):
        row["rank"] = i
    return rows[:RACE_SIZE]


def build_payload(season: str, raw_dir: str = None) -> dict:
    """Everything data/dashboard_awards.json carries, or {} when the season
    has no collected games (caller keeps the previous file untouched)."""
    raw = raw_dir or RAW_DIR
    players = aggregate_players(season, raw_dir=raw)
    if not players:
        return {}

    records = team_records(season, raw_dir=raw)
    min_gp = min_games_for(max_games_played(records))

    prev_label = previous_season(season)
    prev_players = aggregate_players(prev_label, raw_dir=raw) if SEASON_DIR_RE.fullmatch(prev_label) else []
    prev_records = team_records(prev_label, raw_dir=raw) if prev_players else {}
    min_gp_prev = min_games_for(max_games_played(prev_records))

    return {
        "season": season,
        "prev_season": prev_label if prev_players else None,
        "min_games": min_gp,
        "races": {
            "mvp": mvp_rows(players, records, min_gp),
            "dpoy": dpoy_rows(players, records, min_gp),
            "sixth_man": sixth_man_rows(players, records, min_gp),
            "mip": mip_rows(players, prev_players, min_gp, min_gp_prev),
        },
        "leaders": leader_rows(players, min_gp),
    }


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target is None:
        candidates = sorted(
            (name for name in os.listdir(RAW_DIR)
             if SEASON_DIR_RE.fullmatch(name)
             and glob.glob(os.path.join(RAW_DIR, name, "games", "*.json"))),
            reverse=True,
        )
        if not candidates:
            raise SystemExit("no collected seasons under data/raw/")
        target = candidates[0]
    payload = build_payload(target)
    print(json.dumps(payload, indent=1)[:4000])
